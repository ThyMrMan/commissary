"""TorrentDownloadPlugin — composes Prowlarr search + torrent client
adapter + archive_pipeline into a uniform download source.

Two flows:

**Per-track flow** (basic search, single-track wishlist) —
1. ``search(query)`` calls ``ProwlarrClient.search`` filtered to
   ``protocol='torrent'`` results, projects releases into
   ``TrackResult`` / ``AlbumResult`` shaped objects the existing
   search UI already understands. Encodes the indexer's
   ``downloadUrl`` (or magnet URI) into the filename so
   ``download()`` can recover it.
2. ``download(username, filename, ...)`` decodes the URL, asks the
   active torrent adapter (qBittorrent, Transmission, or Deluge per
   user's settings) to add it, spawns a background thread that
   polls the adapter for completion.
3. On completion the thread walks the adapter-reported save path
   via ``archive_pipeline.collect_audio_after_extraction`` and
   exposes the full audio-file list. Post-processing can then pick
   the requested track from a completed release instead of importing
   the first file blindly.

**Album-bundle flow** (album-context batch downloads — wired in
``core/downloads/master.py``) —
4. ``download_album_to_staging(album, artist, staging_dir)`` does
   ONE Prowlarr search for the whole release, picks the best
   torrent (prefers FLAC, decent seeders, reasonable size),
   downloads it, extracts archives if needed, copies every audio
   file into the staging directory. The existing per-track
   ``try_staging_match`` flow then finds + imports each track by
   fuzzy title match against the staged files. Per-track Prowlarr
   queries never fire — track titles like "Luther (with SZA)"
   would match album torrents like "GNX (2024) [FLAC]" at near-
   zero confidence and break the per-track dispatch.

Limitations:
- ``save_path`` is the torrent client's view of the disk. If
  Commissary runs on a different host than qBit / Trans / Deluge,
  the post-processing pipeline can't see those files. The plugin
  works fine for the all-on-one-box case (most users); remote
  setups will need a future sync step (rclone / SMB / Docker
  bind mount).
- Track-level metadata isn't available until after download.
  Search results carry only the release title + indexer metadata;
  individual track names are populated when the matching pipeline
  walks the extracted audio files.
"""

from __future__ import annotations

import asyncio
import re
import threading
import time
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from config.settings import config_manager
from core.archive_pipeline import collect_audio_after_extraction
from core.download_plugins.album_bundle import (
    TransientMissCounter,
    copy_audio_files_atomically,
    get_poll_interval,
    get_poll_timeout,
    pick_best_album_release,
    poll_album_download,
    resolve_reported_save_path,
)
from core.download_plugins.base import DownloadSourcePlugin
from core.download_plugins.candidate_store import get_candidate_store
from core.download_plugins.torrent_stall import (
    StallTracker,
    get_stall_action,
    get_stall_timeout,
)
from core.download_plugins.types import AlbumResult, DownloadStatus, TrackResult
from core.prowlarr_client import (
    DEFAULT_MUSIC_CATEGORIES,
    ProwlarrClient,
    ProwlarrSearchResult,
    safe_info_url,
)
from core.torrent_clients import get_active_adapter as get_active_torrent_adapter
from utils.async_helpers import run_async
from utils.logging_config import get_logger

logger = get_logger("download_plugins.torrent")


# Separator used to encode the download URL inside the filename
# field. Same convention Lidarr / YouTube use for embedding their
# own opaque identifiers — ``<download_url>||<display>``.
_FILENAME_SEP = '||'

# Adapter states that count as the download being on-disk and
# safe to walk. ``seeding`` and ``completed`` both mean the
# bits are there; the user can pause seeding manually if they
# don't want to keep sharing.
_COMPLETE_STATES = frozenset(['seeding', 'completed'])

# Poll cadence / timeout — both pull from config via the shared
# album_bundle helpers so users can extend the deadline for slow
# trackers without editing source. Kept as module aliases so the
# per-track flow at the bottom of this file can still import them
# under the legacy names without re-reading config every loop.
_POLL_TIMEOUT_SECONDS = get_poll_timeout()
_POLL_INTERVAL_SECONDS = get_poll_interval()


class TorrentDownloadPlugin(DownloadSourcePlugin):
    """Torrent download source backed by Prowlarr + an active
    torrent client adapter."""

    def __init__(self) -> None:
        self._prowlarr = ProwlarrClient()
        # Track every download we've kicked off. Keyed by our own
        # uuid — NOT the adapter's hash — because the orchestrator
        # owns the lifecycle and we need a stable id even before
        # the adapter has assigned one.
        self.active_downloads: Dict[str, Dict[str, Any]] = {}
        self._lock = threading.Lock()
        self.shutdown_check = None

    def set_shutdown_check(self, check_callable):
        self.shutdown_check = check_callable

    def reload_settings(self) -> None:
        self._prowlarr.reload_settings()

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def is_configured(self) -> bool:
        if not self._prowlarr.is_configured():
            return False
        adapter = get_active_torrent_adapter()
        return bool(adapter and adapter.is_configured())

    async def check_connection(self) -> bool:
        if not self._prowlarr.is_configured():
            return False
        adapter = get_active_torrent_adapter()
        if not adapter or not adapter.is_configured():
            return False
        # Probe both sides. A torrent download is useless if either
        # the indexer or the downloader is unreachable.
        prowlarr_ok = await self._prowlarr.check_connection()
        if not prowlarr_ok:
            return False
        return await adapter.check_connection()

    # ------------------------------------------------------------------
    # Search
    # ------------------------------------------------------------------

    async def search(
        self,
        query: str,
        timeout: Optional[int] = None,
        progress_callback=None,
    ) -> Tuple[List[TrackResult], List[AlbumResult]]:
        if not self._prowlarr.is_configured():
            return ([], [])
        try:
            indexer_ids = _parse_indexer_id_filter()
            results = await self._prowlarr.search(
                query,
                categories=DEFAULT_MUSIC_CATEGORIES,
                indexer_ids=indexer_ids,
            )
        except Exception as e:
            logger.error("Torrent plugin search failed: %s", e)
            return ([], [])
        return self._project_results(results)

    def _project_results(
        self, results: List[ProwlarrSearchResult]
    ) -> Tuple[List[TrackResult], List[AlbumResult]]:
        """Turn Prowlarr releases into TrackResult / AlbumResult
        shaped objects. One TrackResult + one AlbumResult per
        release — Prowlarr search hits are at the release level,
        not the track level, so we can't synthesise track listings
        without downloading the actual torrent."""
        tracks: List[TrackResult] = []
        albums: List[AlbumResult] = []
        for result in results:
            if result.protocol != 'torrent':
                continue
            # .torrent URL first, magnet second (#1139). The album flow below
            # already did this; the single-track grab did not, so one release
            # offering both would hand the client a bare info-hash.
            download_url = result.download_url or result.magnet_uri
            fallback_magnet = result.magnet_uri if result.download_url else None
            if not download_url:
                continue
            # The filename crosses to the browser in search responses and
            # comes back on grab. Indexer URLs can carry API keys / signed
            # params, so only an opaque server-side token travels (P0-03).
            token = get_candidate_store().put(download_url, magnet=fallback_magnet)
            filename = f"{token}{_FILENAME_SEP}{result.title}"
            quality = _guess_quality_from_title(result.title)
            parsed_artist, parsed_title = _parse_release_title(result.title)
            tr = TrackResult(
                username='torrent',
                filename=filename,
                size=result.size,
                bitrate=None,
                duration=None,
                quality=quality,
                # Torrent results don't have per-uploader slot / queue
                # data the way Soulseek does. Fill with neutral values
                # so the quality_score doesn't punish them artificially.
                free_upload_slots=max(1, result.seeders or 0),
                upload_speed=0,
                queue_length=0,
                # Pre-fill artist + title so TrackResult.__post_init__
                # doesn't auto-parse the filename — our filename starts
                # with the indexer download URL, which would otherwise
                # show up as "by download?apikey=..." in the UI.
                artist=parsed_artist or result.indexer_name or 'Torrent',
                title=parsed_title or result.title,
                album=parsed_title or None,
                track_number=None,
                _source_metadata={
                    'indexer': result.indexer_name,
                    'indexer_id': result.indexer_id,
                    'seeders': result.seeders,
                    'leechers': result.leechers,
                    'grabs': result.grabs,
                    'protocol': 'torrent',
                    # The indexer's own details page. Scheme-checked at this
                    # boundary because it comes from a third party and is
                    # rendered as a link the user clicks.
                    'info_url': safe_info_url(result.info_url),
                },
            )
            tracks.append(tr)
            albums.append(AlbumResult(
                username='torrent',
                album_path=f"torrent/{result.guid}",
                album_title=parsed_title or result.title,
                artist=parsed_artist or None,
                track_count=1,    # unknown until download finishes
                total_size=result.size,
                tracks=[tr],
                dominant_quality=quality,
                year=None,
            ))
        return tracks, albums

    # ------------------------------------------------------------------
    # Download
    # ------------------------------------------------------------------

    async def download(
        self,
        username: str,
        filename: str,
        file_size: int = 0,
    ) -> Optional[str]:
        if not self.is_configured():
            return None
        token, display_name = _decode_filename(filename)
        if not token:
            logger.error("Torrent download missing candidate token in filename: %r", filename)
            return None
        # Only a token from OUR candidate store is accepted — a raw URL from
        # the client is a trust-boundary violation, not a fallback (P0-03).
        download_url = get_candidate_store().resolve(token)
        if not download_url:
            logger.error("Torrent download: unknown or expired candidate for %r "
                         "— re-run the search", display_name)
            return None
        # The same release's magnet, when the indexer offered both. Carried so
        # the .torrent URL can be preferred without losing the magnet in an
        # install where this process cannot reach the indexer but the client can.
        fallback_magnet = get_candidate_store().resolve_magnet(token)

        download_id = str(uuid.uuid4())
        with self._lock:
            self.active_downloads[download_id] = {
                'id': download_id,
                'filename': filename,
                'username': 'torrent',
                'display_name': display_name,
                'state': 'Initializing',
                'progress': 0.0,
                'size': file_size,
                'transferred': 0,
                'speed': 0,
                'file_path': None,
                'audio_files': [],
                'torrent_hash': None,
                'error': None,
            }

        thread = threading.Thread(
            target=self._download_thread,
            args=(download_id, download_url, display_name, fallback_magnet),
            daemon=True,
            name=f'torrent-dl-{download_id[:8]}',
        )
        thread.start()
        return download_id

    def _download_thread(self, download_id: str, download_url: str, display_name: str,
                         fallback_magnet: Optional[str] = None) -> None:
        """Background worker: hand the URL to the active adapter,
        poll until done, then walk the resulting directory."""
        adapter = get_active_torrent_adapter()
        if adapter is None or not adapter.is_configured():
            self._mark_error(download_id, "No torrent client configured")
            return

        try:
            from core.torrent_clients.base import add_torrent_smart
            torrent_hash = run_async(add_torrent_smart(
                adapter, download_url, fallback_magnet=fallback_magnet))
        except Exception as e:
            self._mark_error(download_id, f"add_torrent failed: {e}")
            return
        if not torrent_hash:
            self._mark_error(download_id, "Torrent client refused the URL")
            return

        with self._lock:
            row = self.active_downloads.get(download_id)
            if row is not None:
                row['torrent_hash'] = torrent_hash
                row['state'] = 'InProgress, Downloading'

        deadline = time.monotonic() + _POLL_TIMEOUT_SECONDS
        last_save_path: Optional[str] = None
        # Tolerate transient None reads — covers network blips. Torrent
        # adapters don't have an SAB-style queue→history transition,
        # but the same tolerance keeps a one-off connection failure
        # from killing an otherwise-healthy download.
        misses = TransientMissCounter()
        # Stalled-torrent handling (noldevin): give up early on a torrent
        # making zero progress (dead magnet stuck on metadata, no seeders)
        # instead of holding this worker for the full album deadline. Read
        # per-download so a settings change applies to in-flight torrents.
        stall = StallTracker(get_stall_timeout())
        while time.monotonic() < deadline:
            if self.shutdown_check and self.shutdown_check():
                return
            try:
                status = run_async(adapter.get_status(torrent_hash))
            except Exception as e:
                logger.warning("Torrent poll error for %s: %s", torrent_hash, e)
                status = None

            if status is None:
                if misses.record_miss():
                    self._mark_error(
                        download_id,
                        f"Torrent disappeared from client (no status after {misses.threshold} polls)",
                    )
                    return
                time.sleep(_POLL_INTERVAL_SECONDS)
                continue

            misses.reset()

            with self._lock:
                row = self.active_downloads.get(download_id)
                if row is not None:
                    row['progress'] = status.progress * 100.0
                    row['transferred'] = status.downloaded
                    row['speed'] = status.download_speed
                    row['size'] = status.size or row.get('size', 0)
                    row['state'] = _adapter_state_to_display(status.state)
                    row['error'] = status.error
            if status.save_path:
                last_save_path = status.save_path

            if status.state in _COMPLETE_STATES:
                self._finalize_download(download_id, last_save_path,
                                        torrent_name=status.name)
                return
            if status.state == 'error':
                # Clean the dead torrent out of the client, or it's left orphaned
                # (active in qbit, untracked here) and re-grabbed as a duplicate.
                self._cleanup_torrent(torrent_hash, get_stall_action())
                self._mark_error(download_id, status.error or "Torrent client reported error")
                return

            if stall.is_stalled(status.downloaded, status.state, time.monotonic(),
                                size=status.size):
                self._handle_stalled(download_id, torrent_hash, get_stall_action())
                return

            time.sleep(_POLL_INTERVAL_SECONDS)

        # Deadline reached. One last status check closes the race where the
        # torrent completed during the final poll interval — finalize it instead
        # of deleting a just-finished download's files. Otherwise clean it out of
        # the client, or it sits orphaned in qbit (e.g. a metadata-stuck magnet
        # that escaped the stall timer) and gets re-grabbed as a duplicate.
        try:
            final = run_async(adapter.get_status(torrent_hash))
        except Exception:
            final = None
        if final is not None and final.state in _COMPLETE_STATES:
            self._finalize_download(download_id, final.save_path or last_save_path,
                                    torrent_name=final.name)
            return
        self._cleanup_torrent(torrent_hash, get_stall_action())
        self._mark_error(download_id, "Torrent download timed out")

    def _cleanup_torrent(self, torrent_hash: str, action: str) -> None:
        """Remove (abandon) or pause a dead/stalled/timed-out torrent in the
        client so it isn't left ORPHANED — active in qbit but no longer tracked
        here, which makes Commissary re-grab the same dead torrent as a duplicate
        on the next attempt (noldevin). Best-effort: a client error is logged,
        not raised, so the download still fails cleanly."""
        adapter = get_active_torrent_adapter()
        if adapter is None or not torrent_hash:
            return
        try:
            if action == "pause":
                run_async(adapter.pause(torrent_hash))
            else:
                # delete_files: a stalled/failed torrent's partial data is junk
                # (often just a metadata stub) — don't leave it on disk.
                run_async(adapter.remove(torrent_hash, delete_files=True))
        except Exception as e:
            logger.warning("Torrent cleanup (%s) on %s failed: %s",
                           action, torrent_hash[:8] if torrent_hash else "?", e)

    def _handle_stalled(self, download_id: str, torrent_hash: str, action: str) -> None:
        """A torrent made no progress past the stall timeout. Abandon it
        (remove from client + delete its partial data) or pause it for the
        user, then fail the download so the worker frees up."""
        timeout_min = round(get_stall_timeout() / 60, 1)
        self._cleanup_torrent(torrent_hash, action)
        verb = "paused" if action == "pause" else "removed"
        self._mark_error(
            download_id,
            f"Torrent stalled (no progress for {timeout_min} min) — {verb}",
        )

    def _finalize_download(self, download_id: str, save_path: Optional[str],
                           torrent_name: Optional[str] = None) -> None:
        """Adapter said complete. Walk the directory + pick the
        first audio file as the canonical ``file_path``."""
        if not save_path:
            self._mark_error(download_id, "Torrent completed but no save_path reported")
            return
        # Resolve the client-reported path to one this process can read
        # (the client may report its own container's mount). The torrent's
        # NAME is the content check: a same-named directory that doesn't
        # contain this torrent is the wrong mount, not a resolution
        # (TheHomeGuy's '/downloads'). See ``resolve_reported_save_path``.
        local_path = resolve_reported_save_path(save_path, expect_name=torrent_name)
        if local_path != save_path:
            logger.info("Torrent %s: resolved client path %r -> %r",
                        download_id[:8], save_path, local_path)
        # save_path is the torrent's save DIRECTORY; the content lives at
        # <save_path>/<name>. Walking just the release keeps a shared
        # download root from donating the "first audio file" of some OTHER
        # torrent that happens to live there.
        walk_root = Path(local_path)
        if torrent_name and (walk_root / torrent_name).is_dir():
            # is_dir, not exists: a single-FILE torrent's name points at the
            # file itself, and the audio walker only walks directories.
            walk_root = walk_root / torrent_name
        try:
            audio_files = collect_audio_after_extraction(walk_root)
        except Exception as e:
            self._mark_error(download_id, f"Post-extract walk failed: {e}")
            return
        if not audio_files:
            suffix = f" (resolved: {local_path})" if local_path != save_path else ""
            self._mark_error(download_id, f"No audio files found in {save_path}{suffix}")
            return
        primary = audio_files[0]
        completed_hash = None
        with self._lock:
            row = self.active_downloads.get(download_id)
            if row is not None:
                row['state'] = 'Completed, Succeeded'
                row['progress'] = 100.0
                row['file_path'] = str(primary)
                row['audio_files'] = [str(path) for path in audio_files]
                completed_hash = row.get('torrent_hash')
        logger.info("Torrent download complete: %s -> %s (%d audio files)",
                    download_id[:8], primary.name, len(audio_files))
        # Durably record this completed grab so the seeding sweep can manage the
        # tail (seed until ratio/time goals, then remove from the client). The
        # torrent_hash only lives in the in-memory row for the transfer, so this
        # is the one point it can be persisted. Best-effort: a DB hiccup here
        # must never affect the (already complete) download.
        if completed_hash:
            self._apply_seed_policy(completed_hash, torrent_name)

    def _mark_error(self, download_id: str, message: str) -> None:
        logger.error("Torrent download %s failed: %s", download_id[:8], message)
        with self._lock:
            row = self.active_downloads.get(download_id)
            if row is not None:
                row['state'] = 'Completed, Errored'
                row['error'] = message

    def _apply_seed_policy(self, torrent_hash: str, title: Optional[str]) -> None:
        """Route a completed grab per the seed-enforcement mode. 'client' writes
        the ratio/time limit into the torrent client so it enforces (arr-style);
        'soulsync' (default) records the grab for the seeding sweep. If a client
        push fails/isn't supported, fall back to recording so the goal still
        applies. Best-effort — never raises into the completion path."""
        try:
            from config.settings import config_manager
            mode = config_manager.get('torrent_client.seed_mode', 'soulsync')
            if mode == 'client':
                ratio_goal = config_manager.get('torrent_client.seed_ratio_goal', 0)
                time_goal = config_manager.get('torrent_client.seed_time_goal_hours', 0)
                if ratio_goal or time_goal:
                    from core.torrent_clients import get_active_adapter as _adapter
                    from core.torrent_clients.share_limits import push_seed_goal
                    if push_seed_goal(_adapter(), torrent_hash, ratio_goal, time_goal):
                        return  # the client now enforces the goal itself
                    # push failed/unsupported → fall through to the sweep
        except Exception as e:
            logger.warning("Seed policy check failed for %s (%s); recording for sweep",
                           torrent_hash[:8] if torrent_hash else "?", e)
        self._record_seed_grab(torrent_hash, title)

    def _record_seed_grab(self, torrent_hash: str, title: Optional[str]) -> None:
        """Persist a completed torrent grab for the seeding sweep. Best-effort:
        never raises into the completion path."""
        try:
            from database.music_database import get_database
            from config.settings import config_manager
            category = config_manager.get('torrent_client.category', 'soulsync') or 'soulsync'
            get_database().record_torrent_seed_grab(torrent_hash, title, category)
        except Exception as e:
            logger.warning("Could not record torrent seed grab %s: %s",
                           torrent_hash[:8] if torrent_hash else "?", e)

    # ------------------------------------------------------------------
    # Status / lifecycle
    # ------------------------------------------------------------------

    async def get_all_downloads(self) -> List[DownloadStatus]:
        with self._lock:
            rows = list(self.active_downloads.values())
        return [_row_to_status(r) for r in rows]

    async def get_download_status(self, download_id: str) -> Optional[DownloadStatus]:
        with self._lock:
            row = self.active_downloads.get(download_id)
            if row is None:
                return None
            return _row_to_status(row)

    async def cancel_download(
        self,
        download_id: str,
        username: Optional[str] = None,
        remove: bool = False,
    ) -> bool:
        adapter = get_active_torrent_adapter()
        with self._lock:
            row = self.active_downloads.get(download_id)
            torrent_hash = row.get('torrent_hash') if row else None
        if adapter and torrent_hash:
            try:
                await adapter.remove(torrent_hash, delete_files=remove)
            except Exception as e:
                logger.warning("Torrent cancel via adapter failed: %s", e)
        with self._lock:
            if remove:
                self.active_downloads.pop(download_id, None)
            else:
                row = self.active_downloads.get(download_id)
                if row is not None:
                    row['state'] = 'Cancelled'
        return True

    async def clear_all_completed_downloads(self) -> bool:
        with self._lock:
            for did in list(self.active_downloads.keys()):
                state = self.active_downloads[did].get('state', '')
                if state.startswith('Completed') or state == 'Cancelled':
                    self.active_downloads.pop(did, None)
        return True

    # ------------------------------------------------------------------
    # Album-bundle flow
    # ------------------------------------------------------------------

    def download_album_to_staging(
        self,
        album_name: str,
        artist_name: str,
        staging_dir: str,
        progress_callback=None,
        *,
        preferred_release: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """One-shot album download: search Prowlarr for the whole
        release, pick the best torrent, fetch it, extract if needed,
        copy every audio file into ``staging_dir`` so the existing
        ``try_staging_match`` flow can hand each track off to the
        post-processing pipeline.

        ``progress_callback`` is called with a dict on each state
        change so the batch UI can show download progress without
        waiting for the whole thing.

        ``preferred_release`` is a release the USER picked from the album
        source picker — ``{'token': <candidate-store token>, 'title': str}``.
        When present, the Prowlarr search and ``pick_best_album_release``
        heuristic are both skipped: the user has already answered the
        question they exist to guess at. Only a token from our own
        candidate store is accepted (same trust boundary as ``download()``
        — an indexer URL from the client is a P0-03 violation, not a
        convenience), so an expired or forged token fails the grab rather
        than silently falling back to a heuristic pick the user didn't ask
        for. Mirrors the ``preferred_source``/``preferred_tracks`` kwargs
        the Soulseek album flow already accepts from album pre-flight.

        Returns ``{'success': bool, 'files': [paths], 'error': str|None}``.
        """
        result: Dict[str, Any] = {'success': False, 'files': [], 'error': None}
        if not self.is_configured():
            result['error'] = 'Torrent source not configured'
            return result

        adapter = get_active_torrent_adapter()
        if adapter is None or not adapter.is_configured():
            result['error'] = 'No active torrent client'
            return result

        def _emit(state: str, **extra) -> None:
            if progress_callback:
                payload = {'state': state, **extra}
                try:
                    progress_callback(payload)
                except Exception as cb_exc:
                    logger.debug("[Torrent album] progress callback failed: %s", cb_exc)

        # Phase 1: decide WHICH release. A user pick skips straight past the
        # search + heuristic; otherwise search Prowlarr and score.
        if preferred_release:
            release_title = str(preferred_release.get('title') or '').strip() or album_name
            download_url = get_candidate_store().resolve(
                str(preferred_release.get('token') or ''))
            if not download_url:
                # Do NOT quietly fall back to a heuristic pick here: the user
                # named a release, and silently grabbing a different one is a
                # worse outcome than saying the choice went stale. No
                # `fallback` flag, so the batch fails visibly.
                result['error'] = ('That release is no longer available to grab '
                                   '— re-run the source search and pick again')
                return result
            logger.info("[Torrent album] Using user-picked release '%s'", release_title)
            _emit('queued', release=release_title)
        else:
            query = f"{artist_name} {album_name}".strip()
            _emit('searching', query=query)
            try:
                search_results = run_async(self._prowlarr.search(
                    query, categories=DEFAULT_MUSIC_CATEGORIES,
                    indexer_ids=_parse_indexer_id_filter(),
                ))
            except Exception as e:
                result['error'] = f'Prowlarr search failed: {e}'
                return result

            candidates = [r for r in search_results
                          if r.protocol == 'torrent' and (r.magnet_uri or r.download_url)]
            # #1139(3): nothing enforced a seeder floor. Sorting BY seeders
            # still returns a release when the whole field is on zero, and a
            # zero-seeder torrent is what sits at "downloading metadata" for
            # hours. Candidates that report NO seeder count are exempt — some
            # indexers omit the field, and this may only drop what we
            # positively know is dead.
            candidates = _drop_dead_releases(candidates, log_prefix='[Torrent album]')
            if not candidates:
                # Album isn't available on this source. Mark the failure as
                # fallback-eligible so the dispatch returns to the per-track flow
                # instead of hard-failing the batch — in hybrid mode that lets the
                # next configured source take over. Without this flag a torrent-first
                # hybrid would get stuck at "searching" forever when Prowlarr
                # returns nothing, never trying the other sources.
                result['error'] = f'No torrent results found for "{query}"'
                result['fallback'] = True
                return result

            picked = pick_best_album_release(
                candidates, _guess_quality_from_title, album_name=album_name,
            )
            if picked is None:
                # No candidate matched the requested album (or none passed filtering).
                # Fall back to the per-track flow rather than downloading a wrong
                # album (#730) — per-track searches each track individually.
                result['error'] = 'No torrent candidate matched the requested album'
                result['fallback'] = True
                return result

            release_title = picked.title
            # #1139(1): this preferred the MAGNET whenever the indexer offered
            # both. A magnet is an info-hash — the client has to find the swarm
            # itself, and one that cannot sits on "downloading metadata" (zero
            # size, zero peers) forever. add_torrent_smart already fetches the
            # .torrent server-side and hands over the file, the way Sonarr and
            # Radarr do, and never got the chance. Prefer the .torrent, keeping
            # the magnet as the fallback so a failed fetch uses the link we
            # already had rather than a URL this process just proved unreachable.
            download_url = picked.download_url or picked.magnet_uri
            fallback_magnet = picked.magnet_uri if picked.download_url else None
            logger.info("[Torrent album] Picked '%s' (size=%.1fMB seeders=%s indexer=%s)",
                        picked.title, picked.size / 1_048_576, picked.seeders, picked.indexer_name)
            _emit('queued', release=picked.title, size=picked.size, seeders=picked.seeders)

        # Phase 2: hand to adapter. Fetch the .torrent server-side first —
        # the client often can't reach Prowlarr itself (split containers).
        try:
            from core.torrent_clients.base import add_torrent_smart
            torrent_id = run_async(add_torrent_smart(adapter, download_url))
            if not torrent_id and fallback_magnet:
                logger.warning("[Torrent album] .torrent handoff failed for '%s' — "
                               "retrying with the magnet", release_title)
                torrent_id = run_async(add_torrent_smart(adapter, fallback_magnet))
        except Exception as e:
            result['error'] = f'Torrent client refused the release: {e}'
            result['fallback'] = True          # #1139(5) — try another source
            return result
        if not torrent_id:
            result['error'] = 'Torrent client refused the release'
            result['fallback'] = True
            return result

        # Phase 3: poll until complete. The lifted helper handles
        # transient missing windows (uncommon for torrents — adapters
        # don't have a queue→history transition like SAB — but the
        # same path also catches network blips that would otherwise
        # take down the whole download) and always emits a terminal
        # 'failed' state on failure paths so the UI doesn't freeze on
        # the last 'downloading' emit.
        _emit('downloading', release=release_title)
        save_path = poll_album_download(
            get_status=lambda: run_async(adapter.get_status(torrent_id)),
            title=release_title,
            emit=_emit,
            # Torrent adapters flip to 'seeding' on completion (files
            # on disk, share-ratio progress) — both states count as
            # terminal success.
            complete_states=frozenset(['seeding', 'completed']),
            # qBit / Transmission / Deluge surface a real 'error'
            # state when the torrent itself errors (tracker, missing
            # files, etc.). That's distinct from the unmapped-state
            # default-'error' fallback the helper treats as transient.
            failed_states=frozenset(['error']),
            is_shutdown=self.shutdown_check,
            # P2-21: remap the client-container path before the incomplete_path
            # stability check, not just on the final save_path — otherwise a
            # split-container mount never resolves to a readable local path and
            # the fallback can't stabilize. expect_name isn't known this early
            # (fetched below only after the poll returns), so this is the
            # weaker no-expect_name resolution; the final walk_root re-resolves
            # with expect_name for the stricter content-checked path.
            resolve_path=resolve_reported_save_path,
            log_prefix='[Torrent album]',
            # #1139(2)+(4): the album loop finally consults the configured
            # stall timeout, and cleans up after itself when it trips.
            stall_check=_album_stall_check(StallTracker(get_stall_timeout())),
            on_stall=lambda: _cleanup_stalled_album(adapter, torrent_id, release_title),
        )
        if save_path is None:
            # poll_album_download already emitted terminal 'failed'.
            # #1139(5): every album failure used to be terminal — none set
            # `fallback`, so one dead release ended the whole batch instead of
            # returning to the per-track flow (and, in hybrid mode, the next
            # source). A stall or timeout is exactly the case where another
            # source is likely to work.
            result['error'] = 'Torrent download failed or timed out'
            result['fallback'] = True
            return result

        # Phase 4: extract + walk + copy to staging.
        _emit('staging', release=release_title)
        # Resolve the client-reported path to one this process can read,
        # content-checked against the torrent's on-disk NAME (the release
        # folder) so an existing-but-wrong mount can't win, and walk just
        # that release so a shared root can't donate another torrent's files.
        torrent_name = None
        content_path = None
        try:
            _final_status = run_async(adapter.get_status(torrent_id))
            torrent_name = _final_status.name if _final_status else None
            # #1139(6): "No audio files found" after a torrent completed and
            # seeded. Staging walked save_path + the torrent's display NAME,
            # but the on-disk release folder routinely differs from that name
            # and save_path is shared with every concurrent grab. qBittorrent
            # answers directly with content_path — the exact release root, or
            # the file itself for a single-file torrent — which the video side
            # has used for a while and this flow never adopted. Transmission
            # and Deluge don't report it; they fall through to the old
            # resolution below.
            content_path = getattr(_final_status, 'content_path', None) if _final_status else None
        except Exception:   # noqa: BLE001 - the name is an assist, not a requirement
            torrent_name = None
        local_path = resolve_reported_save_path(save_path, expect_name=torrent_name)
        if local_path != save_path:
            logger.info("[Torrent album] Resolved client path %r -> %r", save_path, local_path)
        walk_root = Path(local_path)
        single_file: Optional[Path] = None
        if content_path:
            resolved_content = Path(resolve_reported_save_path(str(content_path),
                                                               expect_name=torrent_name))
            if resolved_content.is_dir():
                walk_root = resolved_content
            elif resolved_content.is_file():
                # A single-FILE torrent: stage that ONE file. Walking its parent
                # would be the shared download root and would donate every
                # neighbouring torrent's audio into this album's staging.
                single_file = resolved_content
        if single_file is None and torrent_name and (walk_root / torrent_name).is_dir():
            # is_dir, not exists: a single-FILE torrent's name points at the
            # file itself, and the audio walker only walks directories.
            walk_root = walk_root / torrent_name
        try:
            audio_files = [single_file] if single_file else collect_audio_after_extraction(walk_root)
        except Exception as e:
            result['error'] = f'Failed to walk audio files: {e}'
            result['fallback'] = True
            return result
        if not audio_files:
            # Separate "Commissary cannot read that path" — a remote-path-mapping
            # problem — from "readable, and it holds no audio". They need
            # completely different things from the user.
            if not walk_root.is_dir():
                result['error'] = (
                    f'Cannot read the completed download at {walk_root} — if the torrent '
                    f'client runs in a different container or host, add a '
                    f'download_source.path_mappings entry for it'
                )
            else:
                suffix = f' (resolved: {local_path})' if local_path != save_path else ''
                result['error'] = f'No audio files found in {save_path}{suffix}'
            result['fallback'] = True
            return result

        copied = copy_audio_files_atomically(audio_files, Path(staging_dir))
        if not copied:
            result['error'] = 'No audio files copied to staging'
            result['fallback'] = True
            return result
        logger.info("[Torrent album] Staged %d audio files for '%s'", len(copied), album_name)
        _emit('staged', count=len(copied))
        result['success'] = True
        result['files'] = copied
        return result



# ---------------------------------------------------------------------------
# Module-level helpers (pure functions — easy to unit-test)
# ---------------------------------------------------------------------------


# ── #1139 helpers (adapted from upstream 3.2.0, 6180aff4) ────────────────────
DEFAULT_MIN_SEEDERS = 1


def get_min_seeders() -> int:
    """The seeder floor for album grabs. 0 disables the check."""
    try:
        raw = int(config_manager.get("download_source.torrent_min_seeders",
                                     DEFAULT_MIN_SEEDERS))
    except (TypeError, ValueError):
        return DEFAULT_MIN_SEEDERS
    return max(0, raw)


def _drop_dead_releases(candidates: list, log_prefix: str = "[Torrent]") -> list:
    """Drop candidates we positively KNOW have no seeders.

    Sorting by seeders still returns a release when the entire field is on
    zero, and a zero-seeder torrent is precisely the one that sits on
    "downloading metadata" for hours. A candidate that reports NO seeder count
    is left alone — usenet has none, and some indexers omit the field, so this
    may only remove what is known dead, never what is merely unknown.
    """
    floor = get_min_seeders()
    if floor <= 0:
        return candidates
    kept, dropped = [], 0
    for c in candidates:
        seeders = getattr(c, "seeders", None)
        if seeders is None or int(seeders) >= floor:
            kept.append(c)
        else:
            dropped += 1
    if dropped:
        logger.info("%s dropped %d release(s) below the %d-seeder floor",
                    log_prefix, dropped, floor)
    return kept


def _album_stall_check(tracker: "StallTracker"):
    """Adapt a StallTracker to poll_album_download's (status, now) callback."""
    def _check(status, now: float) -> bool:
        return tracker.is_stalled(
            downloaded=getattr(status, "downloaded", 0) or 0,
            state=getattr(status, "state", "") or "",
            now=now,
            size=getattr(status, "size", None),
        )
    return _check


def _cleanup_stalled_album(adapter, torrent_id: str, title: str) -> None:
    """Remove or pause a stalled/timed-out ALBUM torrent per
    download_source.torrent_stall_action.

    The album path never did this: the torrent stayed active in the client,
    untracked here, and was re-grabbed as a duplicate on the next attempt.
    """
    if adapter is None or not torrent_id:
        return
    action = get_stall_action()
    try:
        if action == "pause":
            run_async(adapter.pause(torrent_id))
        else:
            run_async(adapter.remove(torrent_id, delete_files=True))
        logger.warning("[Torrent album] '%s' stalled — %s", title,
                       "paused" if action == "pause" else "removed")
    except Exception as e:      # noqa: BLE001 - cleanup must not mask the failure
        logger.warning("[Torrent album] cleanup (%s) failed for '%s': %s",
                       action, title, e)


def _decode_filename(filename: str) -> Tuple[Optional[str], str]:
    """Pull the encoded download URL out of the ``filename`` string.
    Returns ``(url, display_name)``. ``url`` is None when the string
    has no separator."""
    if not filename or _FILENAME_SEP not in filename:
        return (None, filename or '')
    url, display = filename.split(_FILENAME_SEP, 1)
    return (url, display)


def _parse_release_title(title: str) -> Tuple[str, str]:
    """Split a release title into ``(artist, title)`` using the
    ``Artist - Title`` / ``Artist - Album`` convention almost every
    indexer follows. Returns ``('', title)`` when no dash is found.

    Without this, ``TrackResult.__post_init__`` runs the bare
    filename through ``parse_filename_metadata`` — and our filename
    starts with the indexer's download URL, so the auto-parser
    extracts garbage like ``download?apikey=...`` as the artist
    and shows it in the search-result UI's "by" line. Pre-filling
    the artist field short-circuits the auto-parse.
    """
    if not title:
        return ('', '')
    # Strip common quality / format tags so the dash split doesn't
    # eat them — "Artist - Album [FLAC] (2020)" → "Artist", "Album".
    cleaned = re.sub(r'\s*[\[\(][^\]\)]*[\]\)]\s*$', '', title.strip())
    # Look for the FIRST " - " (or "-" surrounded by content). Some
    # release titles have multiple dashes (subtitle dashes); the
    # first split is the artist/work boundary.
    parts = re.split(r'\s+-\s+|\s+-(?=\S)|(?<=\S)-\s+', cleaned, maxsplit=1)
    if len(parts) == 2:
        artist = parts[0].strip()
        rest = parts[1].strip()
        # Reject obvious non-artist prefixes (URLs, hashes, single
        # punctuation) so we don't propagate garbage.
        if artist and not re.match(r'^https?:|^[a-f0-9]{32,}$', artist):
            return (artist, rest or cleaned)
    return ('', cleaned)


def _guess_quality_from_title(title: str) -> str:
    """Read the quality hint from a release title — most music
    torrents put the encoding right in the name (FLAC, MP3 320,
    etc.). Falls back to ``'mp3'`` so quality_score doesn't crash."""
    if not title:
        return 'mp3'
    lower = title.lower()
    if 'flac' in lower:
        return 'flac'
    if re.search(r'\b24[\s-]?bit\b', lower) or 'hi-?res' in lower:
        return 'flac'
    if 'aac' in lower:
        return 'aac'
    if 'ogg' in lower:
        return 'ogg'
    return 'mp3'


def _parse_indexer_id_filter() -> List[int]:
    """Read the comma-separated indexer-ID allowlist from config.
    Empty list = search every enabled indexer."""
    raw = (config_manager.get('prowlarr.indexer_ids', '') or '').strip()
    if not raw:
        return []
    out: List[int] = []
    for chunk in raw.split(','):
        chunk = chunk.strip()
        if not chunk:
            continue
        try:
            out.append(int(chunk))
        except ValueError:
            continue
    return out


def _adapter_state_to_display(state: str) -> str:
    """Translate the adapter-uniform state strings into the
    ``'InProgress, Downloading'`` / ``'Completed, Succeeded'``
    style the existing UI expects (matches Soulseek + Lidarr)."""
    mapping = {
        'queued':      'Queued',
        'downloading': 'InProgress, Downloading',
        'stalled':     'InProgress, Stalled',
        'seeding':     'Completed, Succeeded',
        'completed':   'Completed, Succeeded',
        'paused':      'Paused',
        'error':       'Completed, Errored',
    }
    return mapping.get(state, state.title())


def _row_to_status(row: Dict[str, Any]) -> DownloadStatus:
    return DownloadStatus(
        id=row['id'],
        filename=row['filename'],
        username=row['username'],
        state=row.get('state', 'Unknown'),
        progress=float(row.get('progress', 0.0)),
        size=int(row.get('size', 0)),
        transferred=int(row.get('transferred', 0)),
        speed=int(row.get('speed', 0)),
        time_remaining=None,
        file_path=row.get('file_path'),
        audio_files=row.get('audio_files') or None,
    )
