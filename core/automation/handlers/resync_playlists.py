"""Automation handler: ``resync_incomplete_playlists`` action.

Closes the last link of the post-download chain::

    batch_complete            -> scan_library              (nudge the media server)
    library_scan_completed    -> start_database_update     (read the server into our DB)
    database_update_completed -> resync_incomplete_playlists   <- THIS

Up to the database update everything already worked: a wishlisted track is
downloaded, imported, scanned by the media server, and read into Commissary's
library. But the server playlist it was downloaded FOR was written minutes
earlier, back when the track was still missing — ``sync_playlist`` matches
against what you own AT THAT MOMENT and only then hands the leftovers to the
wishlist. Nothing ever went back and added the track, so it stayed missing
until the user matched it by hand in the compare view ("Find & add").

A schedule can't cover the gap either: there are ~14 minutes between the sync
that queues the downloads and the moment those downloads become matchable (the
scan debounce, the assumed-scan-complete wait, then the database update), so a
periodic re-sync mostly lands inside the window and re-confirms "still
missing". ``database_update_completed`` fires at exactly the right instant, and
until now nothing listened to it.

Scope is deliberately narrow: only playlists whose LAST sync came up short are
re-synced. A playlist that already matched in full has nothing to gain and is
left alone. Re-running is safe by construction — the matching is
``auto_sync_playlist``'s, and a sync that resolves to the playlist already on
the server no longer rewrites it (``PlexClient.update_playlist``).
"""

from __future__ import annotations

import time
from typing import Any, Dict, List

from core.automation.deps import AutomationDeps
from core.automation.handlers.sync_playlist import auto_sync_playlist

# Per-playlist cap on waiting for the background sync thread. Matches the
# pipeline's own per-playlist budget (_pipeline_shared).
SYNC_TIMEOUT_SECONDS = 600
_TERMINAL_STATUSES = ('finished', 'complete', 'error', 'failed')


def playlist_came_up_short(status: Any) -> bool:
    """True when a playlist's recorded last sync left source tracks unmatched.

    ``matched_tracks`` / ``total_tracks`` are stamped onto the sync-status file
    by every sync (``core.discovery.sync``). A playlist with NO recorded sync is
    not a candidate: it has no server playlist to repair, and first-sync belongs
    to the pipeline, not to us.
    """
    if not isinstance(status, dict):
        return False
    try:
        total = int(status.get('total_tracks') or 0)
        matched = int(status.get('matched_tracks') or 0)
    except (TypeError, ValueError):
        return False
    return total > 0 and matched < total


def select_incomplete_playlists(playlists: Any, sync_statuses: Any) -> List[Dict[str, Any]]:
    """The mirrored playlists worth re-syncing, in the order given.

    Pure — the caller supplies both the playlist rows and the parsed sync-status
    map, so the selection rule is testable without a DB or a status file.
    """
    statuses = sync_statuses if isinstance(sync_statuses, dict) else {}
    out: List[Dict[str, Any]] = []
    for pl in (playlists or []):
        if not isinstance(pl, dict):
            continue
        pid = pl.get('id')
        if not pid:
            continue
        if playlist_came_up_short(statuses.get(f'auto_mirror_{pid}')):
            out.append(pl)
    return out


def _wait_for_sync(deps: AutomationDeps, sync_id: str, automation_id: Any, name: str) -> str:
    """Block until the background sync thread reaches a terminal state.

    Returns the final status string, or ``'timeout'``. Progress is emitted while
    waiting so the automation card doesn't look wedged on a long playlist.
    """
    sync_states = deps.get_sync_states()
    started = time.time()
    while time.time() - started < SYNC_TIMEOUT_SECONDS:
        state = sync_states.get(sync_id)
        if isinstance(state, dict) and state.get('status') in _TERMINAL_STATUSES:
            return str(state.get('status'))
        time.sleep(2)
        deps.update_progress(
            automation_id,
            phase=f'Re-syncing "{name}" ({int(time.time() - started)}s)',
        )
    return 'timeout'


def auto_resync_incomplete_playlists(config: Dict[str, Any], deps: AutomationDeps) -> Dict[str, Any]:
    """Re-sync every mirrored playlist whose last sync left tracks unmatched.

    Returns ``{'status', 'resynced', 'skipped', 'errors', 'candidates'}``.
    """
    automation_id = config.get('_automation_id')

    # Hold the shared playlist-pipeline flag for the whole run so a scheduled
    # pipeline can't start syncing the same playlists underneath us. (The
    # registered guard covers the other direction — this handler is refused
    # while a pipeline is already running.)
    if hasattr(deps.state, 'try_start_pipeline'):
        if not deps.state.try_start_pipeline():
            return {'status': 'skipped', 'reason': 'playlist_pipeline is already running'}
    else:
        deps.state.set_pipeline_running(True)

    try:
        try:
            sync_statuses = deps.load_sync_status_file() or {}
        except Exception as e:  # noqa: BLE001 — an unreadable status file must not break the chain
            deps.logger.debug("[Playlist Re-sync] sync status read failed: %s", e)
            sync_statuses = {}

        db = deps.get_database()
        playlists = select_incomplete_playlists(db.get_mirrored_playlists(), sync_statuses)

        if not playlists:
            deps.update_progress(
                automation_id,
                progress=100,
                log_line='No playlist is short any tracks — nothing to re-sync',
                log_type='skip',
            )
            return {
                'status': 'skipped',
                'reason': 'every synced playlist already matched in full',
                'candidates': '0',
            }

        deps.update_progress(
            automation_id,
            progress=5,
            phase=f'Re-syncing {len(playlists)} playlist(s)',
            log_line=(
                'Newly imported tracks are now in the library — re-syncing '
                f"{len(playlists)} playlist(s) that were missing some"
            ),
            log_type='info',
        )

        sync_states = deps.get_sync_states()
        resynced = skipped = errors = 0

        for idx, pl in enumerate(playlists):
            name = pl.get('name') or 'Playlist'
            sync_id = f"auto_mirror_{pl['id']}"

            # Drop the previous run's terminal state, or the wait below reads it
            # immediately and reports success before this sync has even started.
            try:
                sync_states.pop(sync_id, None)
            except Exception as e:  # noqa: BLE001 — shared dict, never fatal
                deps.logger.debug("[Playlist Re-sync] could not clear %s: %s", sync_id, e)

            result = auto_sync_playlist(
                {'playlist_id': str(pl['id']), '_automation_id': None}, deps,
            )
            status = result.get('status', '')

            if status == 'started':
                final = _wait_for_sync(deps, sync_id, automation_id, name)
                if final in ('finished', 'complete'):
                    resynced += 1
                    deps.update_progress(
                        automation_id,
                        log_line=f'Re-synced "{name}"',
                        log_type='success',
                    )
                else:
                    errors += 1
                    deps.update_progress(
                        automation_id,
                        log_line=f'Re-sync of "{name}" ended as {final}',
                        log_type='error',
                    )
            elif status == 'skipped':
                skipped += 1
                deps.update_progress(
                    automation_id,
                    log_line=f'Skipped "{name}": {result.get("reason", "unchanged")}',
                    log_type='skip',
                )
            else:
                errors += 1
                deps.update_progress(
                    automation_id,
                    log_line=f'Re-sync error for "{name}": {result.get("reason", "unknown")}',
                    log_type='error',
                )

            deps.update_progress(
                automation_id,
                progress=min(99, 5 + int((idx + 1) / len(playlists) * 94)),
            )

        deps.update_progress(
            automation_id,
            progress=100,
            phase='Re-sync complete',
            log_line=f'{resynced} re-synced, {skipped} skipped, {errors} errors',
            log_type='success' if errors == 0 else 'warning',
        )
        return {
            'status': 'completed',
            'candidates': str(len(playlists)),
            'resynced': str(resynced),
            'skipped': str(skipped),
            'errors': str(errors),
        }

    except Exception as e:  # noqa: BLE001 — callers expect a status dict
        deps.logger.error("[Playlist Re-sync] failed: %s", e)
        return {'status': 'error', 'error': str(e)}

    finally:
        deps.state.set_pipeline_running(False)


__all__ = [
    'auto_resync_incomplete_playlists',
    'playlist_came_up_short',
    'select_incomplete_playlists',
    'SYNC_TIMEOUT_SECONDS',
]
