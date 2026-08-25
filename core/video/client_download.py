"""Monitor a torrent/usenet video download to completion + hand the file to the importer.

The Soulseek path polls slskd transfers; this is the parallel path for torrent/usenet grabs.
``process_client_download`` is PURE (all I/O injected) and returns the SAME patch shape as
``download_monitor.process_download`` — so the monitor's existing progress/failure/completion/
history handling works unchanged. Production wiring reuses the SHARED torrent/usenet client
adapters + ``resolve_reported_save_path`` (music-safe: imported + called, never modified).
"""

from __future__ import annotations

import asyncio
import os
from typing import Any, Callable, Optional

from core.video import packed_release
from core.video.slskd_search import _is_video
from utils.logging_config import get_logger

logger = get_logger("video.client_download")

_FAILED_STATES = {"error", "failed"}
_COMPLETE_STATES = {"seeding", "completed", "complete", "succeeded", "finished"}


def _norm_state(status: Any) -> str:
    st = str(getattr(status, "state", "") or "").lower()
    if st in _FAILED_STATES:
        return "failed"
    if st in _COMPLETE_STATES:
        return "completed"
    return "downloading"


def _is_pack(dl: dict) -> bool:
    """Whether this grab is a season/complete-series pack rather than one file."""
    try:
        from core.video.importer import _scope_of
        return _scope_of(dl or {}) in ("season", "series")
    except Exception:      # noqa: BLE001 - unknown scope behaves like today (single file)
        return False


def _packed_patch(dl: dict, names) -> dict:
    """The refusal patch for a release that can never yield a video file.

    ``_bad_release`` is what tells the monitor to blocklist this exact release
    before retrying — being packed is a permanent property of it, not a bad
    night on the swarm, and without the flag the retry would pick it straight
    back off its own candidate list."""
    err = packed_release.reason(names, before_finishing=False)
    logger.info("video download %s (%s): %s", dl.get("id"),
                dl.get("release_title") or dl.get("title") or "?", err)
    return {"status": "failed", "_bad_release": True, "error": err}


def _packed_by_client(dl: dict, ref, list_files) -> Optional[dict]:
    """Refuse an in-flight TORRENT whose file list is archives and nothing else.

    Torrents only, and that is not a limitation to be lifted later. A usenet
    release is ALWAYS rars — that is what usenet is — and SABnzbd/NZBGet unpack
    them server-side before Commissary ever sees the folder. Judging a usenet job
    by its file list would refuse every usenet download there is."""
    if list_files is None or str(dl.get("source") or "") != "torrent":
        return None
    try:
        names = list_files(str(dl.get("source") or ""), str(ref))
    except Exception:   # noqa: BLE001 - an unanswerable client reads as unknown
        logger.debug("file list unavailable for %s", ref, exc_info=True)
        return None
    if packed_release.classify(names or []) != packed_release.PACKED:
        return None
    err = packed_release.reason(names or [], before_finishing=True)
    logger.info("video download %s (%s): %s", dl.get("id"),
                dl.get("release_title") or dl.get("title") or "?", err)
    return {"status": "failed", "_bad_release": True, "error": err}


def process_client_download(dl: dict, *, get_status: Callable[[str, str], Any],
                            resolve_path: Callable[[Any], Any],
                            find_video: Callable[[Any, Any], Any],
                            organizer: Optional[Callable] = None,
                            find_pack: Optional[Callable[[Any, Any], Any]] = None,
                            settled: Optional[Callable[[dict, str], bool]] = None,
                            list_files: Optional[Callable[[str, str], Any]] = None,
                            listing: Optional[Callable[[Any], Any]] = None) -> dict:
    """Next-state patch for a torrent/usenet download. ``get_status(source, ref)`` returns the
    client's status object (or None if it forgot the job), ``resolve_path`` maps its reported
    save_path to a locally-readable one, ``find_video(root, name)`` returns the main video file
    for THIS job — scoped to its own content (``name`` = the torrent/nzb job name), never the
    largest file in a shared download folder.

    ``settled(dl, path)`` is the guard against importing a file something is still
    writing. 100% means the BYTES are in; it does not mean the client has finished
    putting them where we are about to read them. Passing None keeps the old
    behaviour (import as soon as a file is visible).

    ``list_files(source, ref)`` asks the download CLIENT what is inside the job,
    and ``listing(path)`` asks the FILESYSTEM what actually arrived. Both feed the
    same classifier for the same question — "is this a video, or a pile of RAR
    parts nothing here can unpack?" — at the only two moments it can be answered:
    while there is still bandwidth to save, and after the fact when there is at
    least an honest explanation to give. Both default to None, which keeps every
    existing caller's behaviour exactly."""
    ref = dl.get("client_ref")
    if not ref:
        return {"_missing": True}
    status = get_status(str(dl.get("source") or ""), str(ref))
    if status is None:
        # Client no longer knows the job — could be done+cleared. If we already placed the
        # file, finish; otherwise treat as missing (the monitor decides when to give up).
        if dl.get("dest_path"):
            return {"status": "completed", "progress": 100.0, "dest_path": dl.get("dest_path")}
        return {"_missing": True}
    state = _norm_state(status)
    if state == "failed":
        return {"status": "failed", "error": getattr(status, "error", None) or "Download client reported an error"}
    pct = max(0.0, min(100.0, float(getattr(status, "progress", 0) or 0) * 100.0))
    # Ready to import once the DOWNLOAD is 100% — the byte progress, NOT the seed/upload state.
    # 'seeding'/'stalledUP'/'uploading'/'pausedUP'/'queuedUP' all mean the download is finished;
    # the adapter lumps queuedUP (done, just queued to seed) in with checking/moving under
    # 'queued', so state alone would leave a completed-but-seed-queued torrent stuck on
    # "Downloading 100%" forever. A file that isn't settled on disk yet is handled below
    # (find_video returns nothing → we keep polling), so treating 100% as done is safe.
    if state != "completed" and pct < 100.0:
        speed = int(getattr(status, "download_speed", 0) or 0)
        eta = getattr(status, "eta", None)
        # clients report a huge sentinel (qBittorrent: 8640000s) for "unknown";
        # fall back to computing from speed, else no estimate
        if eta is None or eta >= 604800:
            size = int(getattr(status, "size", 0) or 0)
            done = int(getattr(status, "downloaded", 0) or 0)
            eta = int((size - done) / speed) if (speed > 0 and size > done) else None
        # WHAT is this, before hours of bandwidth go into it? Nothing on the video
        # side unpacks archives, so a release that is a set of RAR parts is already
        # lost — the only question is whether that is discovered now or at 100%,
        # thirty minutes later, by a stall clock whose message is about save paths.
        # The client is the ONLY place to ask: Prowlarr results carry no file list
        # at all (prowlarr_search._project hardcodes files=[]), so at grab time
        # there is quite literally nothing to judge.
        refusal = _packed_by_client(dl, ref, list_files)
        if refusal is not None:
            return refusal
        return {"status": "downloading", "progress": pct,
                "speed_bps": speed, "eta_seconds": eta}
    # Completed → locate THIS job's finished video file, then import. Prefer the client's
    # exact content_path (this torrent's own file/folder) — the reliable anti-cross-attribution
    # signal: the shared save_path DIR holds every concurrent grab, and the torrent NAME often
    # differs from the real on-disk filename (e.g. name 'Love Island S13E42 1080p WEB H264-SKYFiRE'
    # vs file 'love.island.s13e42.1080p.web.h264-skyfire[EZTVx.to].mkv'), so save_path/name misses.
    # content_path points straight at the content. Fall back to save_path + name scoping for
    # clients that don't report it (never the largest file in the shared folder).
    # A season/series pack must hand the importer its FOLDER, not one member: the
    # single-file locator returns the largest episode, which would import that one
    # and quietly abandon the rest of the season. find_pack is optional so callers
    # (and every existing test) that don't supply it keep today's behaviour exactly.
    locate = find_pack if (find_pack and _is_pack(dl)) else find_video
    content = getattr(status, "content_path", None)
    name = None
    if content:
        save = resolve_path(content)
        src = locate(save, None) if save else None            # already this job's own content
    else:
        reported = getattr(status, "save_path", None) or getattr(status, "incomplete_path", None)
        save = resolve_path(reported)
        name = getattr(status, "name", None)
        src = locate(save, name) if save else None
    if not src:
        if dl.get("dest_path"):
            return {"status": "completed", "progress": 100.0, "dest_path": dl.get("dest_path")}
        # "Not yet" and "never" look identical from here, and the difference is
        # thirty minutes of polling followed by a message telling you to go and
        # check your save paths. So look at what DID arrive.
        own = scoped_content_path(save, name)
        names = listing(own) if (listing and own) else []
        if packed_release.classify(names) == packed_release.PACKED:
            # Settle FIRST. A usenet job at 100% is very often still being unrar'd
            # by its own client, with the rars sitting right there while it works;
            # failing on sight would kill the exact case that was about to succeed.
            if settled is None or settled(dl, own):
                return _packed_patch(dl, names)
        return {"progress": 100.0}   # complete but the file isn't visible yet — keep polling
    # Visible is not the same as finished. qBittorrent's 'moving' (relocating from
    # the incomplete folder to the complete one) reports progress 1.0, and the
    # adapter maps it to 'queued' — which normalises to 'downloading' here, so the
    # `state != completed AND pct < 100` guard above lets it straight through and
    # the import reads a file mid-copy. The same is true of usenet par2 repair and
    # unrar, which write into the folder well after the download hits 100%.
    # State alone cannot fix this: the adapter deliberately lumps 'moving' and
    # 'checkingDL' in with 'queuedUP' (done, merely queued to seed), and refusing
    # to import on 'queued' would strand every seed-queued torrent at 100% forever
    # — the exact bug the comment above was written to prevent. So the gate is the
    # filesystem itself: hold until the bytes stop changing.
    if settled is not None and not settled(dl, src):
        return {"progress": 100.0}   # still being written — keep polling
    # find_pack_dir hands back its content FOLDER whatever is in it, so a season
    # delivered as RAR parts gets this far with src set and would fail deep in the
    # importer, per file, with a message about the wrong thing entirely. Settled
    # already, so there is no unrar left to wait for.
    settled_names = listing(src) if listing else None
    if settled_names is not None and \
            packed_release.classify(settled_names) == packed_release.PACKED:
        return _packed_patch(dl, settled_names)
    if organizer is not None:
        return organizer(dl, src)
    return {"status": "completed", "progress": 100.0, "dest_path": src}


# ── production seams ──────────────────────────────────────────────────────────
def _run(coro):
    return asyncio.run(coro)


def _get_status(source: str, ref: str):
    """Poll the SHARED torrent/usenet client for a job's status (or None)."""
    try:
        if source == "torrent":
            from core.torrent_clients import get_active_adapter
        else:
            from core.usenet_clients import get_active_adapter
        adapter = get_active_adapter()
        if adapter is None:
            return None
        return _run(adapter.get_status(ref))
    except Exception:   # noqa: BLE001 - a poll hiccup = 'unknown this tick', not a failure
        logger.debug("client status poll failed for %s %s", source, ref, exc_info=True)
        return None


def _list_files(source: str, ref: str):
    """The torrent client's file list for one job, memoized per client ref.

    Memoized because the monitor polls every few seconds and this answer does not
    change once the metadata is in — asking every tick would be a request per
    torrent per tick to settle a question asked once. Only a NON-EMPTY answer is
    cached: an empty list means the magnet has not resolved yet, and caching that
    as final would mean never looking again at precisely the torrents that had
    not told us anything.

    Torrent adapters only — the usenet ones have no such call, and would answer
    'rars' for every job even when SABnzbd is about to unpack them perfectly."""
    if source != "torrent":
        return None
    cached = _files_cache.get(ref)
    if cached is not None:
        return cached
    try:
        from core.torrent_clients import get_active_adapter
        adapter = get_active_adapter()
        if adapter is None or not hasattr(adapter, "list_files"):
            return None
        names = _run(adapter.list_files(ref))
    except Exception:   # noqa: BLE001 - unanswerable reads as unknown, never as bad
        logger.debug("file list unavailable for %s", ref, exc_info=True)
        return None
    if names:
        if len(_files_cache) > _FILES_CACHE_CAP:
            for stale in list(_files_cache)[:len(_files_cache) - _FILES_CACHE_CAP]:
                _files_cache.pop(stale, None)
        _files_cache[ref] = list(names)
    return names


# client_ref -> the file list, once the client has actually given us one. Same
# shape and the same reason as _settle_state below: the monitor calls process_*
# once per tick with no memory in between.
_files_cache: dict = {}
_FILES_CACHE_CAP = 512


def _resolve_path(reported):
    try:
        from core.download_plugins.album_bundle import resolve_reported_save_path
        return resolve_reported_save_path(reported)
    except Exception:   # noqa: BLE001 - fall back to the raw path if the resolver isn't usable
        return reported


def _largest_video(path) -> Optional[str]:
    """The largest non-sample video file under ``path`` — accepts a single file or a
    directory to walk. This is the 'main movie/episode' pick WITHIN an already-scoped root."""
    if os.path.isfile(path):
        return str(path) if _is_video(str(path)) else None
    if not os.path.isdir(path):
        return None
    best, best_size = None, -1
    for dirpath, _dirs, files in os.walk(path):
        for f in files:
            if not _is_video(f) or "sample" in f.lower():
                continue
            p = os.path.join(dirpath, f)
            try:
                sz = os.path.getsize(p)
            except OSError:
                sz = 0
            if sz > best_size:
                best, best_size = p, sz
    return best


def _scoped_content(root, name) -> Optional[str]:
    """The on-disk path of a job's OWN content inside ``root`` — ``root/name`` (a single-file
    torrent's file, or a multi-file torrent's / nzb's folder). Tolerates a case/layout mismatch
    by matching a top-level entry. Returns None when the job's content isn't there — so we never
    fall back to scanning ``root`` itself and picking up a different job's file."""
    direct = os.path.join(root, str(name))
    if os.path.exists(direct):
        return direct
    try:
        for entry in os.listdir(root):
            if entry.lower() == str(name).lower():
                return os.path.join(root, entry)
    except OSError:
        return None
    return None


def find_pack_dir(root, name=None) -> Optional[str]:
    """The FOLDER holding a season/series pack's episodes, scoped to this job.

    The single-file path collapses a pack to its largest member, which would
    import one episode and silently abandon the rest — so a pack asks for its
    content root instead and the importer fans it out. Same scoping rule as
    find_video_file: with a job name we only ever look inside that job's own
    content, never the shared download folder.

    A pack that turns out to be ONE file (a whole season in a single container,
    or a mislabelled release) is handed back as that file, so the caller's normal
    single-file import still gets a chance at it."""
    if not root:
        return None
    target = _scoped_content(root, name) if name else root
    if not target:
        return None
    if os.path.isdir(target):
        return str(target)
    return str(target) if _is_video(str(target)) else None


def scoped_content_path(root, name=None) -> Optional[str]:
    """This job's OWN content inside ``root``, or None when it isn't there.

    Public because the archive check and the settle gate that guards it both
    have to look at exactly what ``find_video_file`` looked at. A shared download
    folder holds every concurrent grab, so a neighbour's .rar files must never be
    what condemns this release."""
    if not root:
        return None
    return _scoped_content(root, name) if name else str(root)


def content_listing(path) -> list:
    """Every file under ``path`` (or the file itself), as full paths.

    The mirror image of ``_largest_video``: the same walk, asking what IS here
    rather than which video is biggest. Handed to the same classifier the torrent
    client's file list goes through, so both sources get one answer."""
    if not path:
        return []
    if os.path.isfile(path):
        return [str(path)]
    if not os.path.isdir(path):
        return []
    out = []
    for dirpath, _dirs, files in os.walk(path):
        for f in files:
            out.append(os.path.join(dirpath, f))
    return out


def find_video_file(root, name=None) -> Optional[str]:
    """The main video file for a download. When ``name`` (the torrent/nzb job name) is given the
    search is SCOPED to that job's own content (``root/name``), so a shared download folder
    holding several concurrent jobs can never leak a neighbour's (often larger) file into this
    import — the cross-attribution bug. With no name we fall back to the largest video in
    ``root`` (single-job folders, e.g. per-job usenet output)."""
    if not root:
        return None
    if name:
        scoped = _scoped_content(root, name)
        return _largest_video(scoped) if scoped else None
    return _largest_video(root)


# ── the settle gate ──────────────────────────────────────────────────────────
# How many CONSECUTIVE identical readings of the content mean "nothing is writing
# to this any more". Two is the minimum that can mean anything (one reading proves
# nothing), and at the monitor's 3s poll that costs one extra tick.
_SETTLE_READS = 2

# download id -> (path, snapshot, consecutive_matches). Module-level because the
# monitor calls process_* once per tick with no memory between calls — the same
# shape as this module's sibling `_stall` map in download_monitor.
_settle_state: dict = {}
_SETTLE_STATE_CAP = 512


def _snapshot(path: str):
    """Size/count/mtime fingerprint, reusing the music side's tested helper.

    mtime is what makes this work for torrents specifically: a pre-allocated or
    sparse file already has its FINAL size on the first byte written, so size
    alone cannot tell a finished file from one still being filled in.
    """
    try:
        from core.download_plugins.album_bundle import snapshot_incomplete_path
        return snapshot_incomplete_path(path)
    except Exception:   # noqa: BLE001 - unreadable reads as "not settled", never as done
        return None


def content_has_settled(dl: dict, path: str, *, snapshot=_snapshot,
                        required: int = _SETTLE_READS) -> bool:
    """True once ``path`` has read identical ``required`` times in a row.

    An unreadable path, an empty directory, or a path that changed since the last
    tick all reset the count — a client that has just created the destination
    folder and not yet copied into it must not look the same as one that has
    finished. Errs toward waiting: the cost of being wrong here is a corrupt
    file in the library, the cost of waiting is one 3-second tick.
    """
    key = str(dl.get("id") or dl.get("client_ref") or path)
    current = snapshot(path)
    prior_path, prior_snap, matches = _settle_state.get(key, (None, None, 0))

    if current is None or current[1] <= 0:
        _settle_state[key] = (path, None, 0)
        return False
    if path == prior_path and current == prior_snap:
        matches += 1
    else:
        matches = 1
    _settle_state[key] = (path, current, matches)

    if matches >= required:
        _settle_state.pop(key, None)     # done with this download
        return True
    if len(_settle_state) > _SETTLE_STATE_CAP:
        # Downloads that vanish mid-settle would otherwise leak an entry each.
        for stale in list(_settle_state)[:len(_settle_state) - _SETTLE_STATE_CAP]:
            _settle_state.pop(stale, None)
    return False


def forget_settle_state(dl_id) -> None:
    """Drop a download's settle progress (cancelled, failed, retried elsewhere)."""
    _settle_state.pop(str(dl_id), None)


def forget_file_list(client_ref) -> None:
    """Drop a job's cached file list. Its own function rather than a line in
    forget_settle_state because the two are keyed differently — settle by
    download id, this by the CLIENT's ref, which is what the adapter answers to."""
    _files_cache.pop(str(client_ref), None)


def process_active_client_download(dl: dict, organizer=None) -> dict:
    """Production entry: poll the real client + resolve + find the video for one torrent/usenet row."""
    return process_client_download(dl, get_status=_get_status, resolve_path=_resolve_path,
                                   find_video=find_video_file, organizer=organizer,
                                   find_pack=find_pack_dir, settled=content_has_settled,
                                   list_files=_list_files, listing=content_listing)
