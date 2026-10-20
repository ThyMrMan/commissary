"""Hold a finished torrent/usenet video import until its files stop changing.

100% means the BYTES are in; it does not mean the client has finished putting
them where the importer is about to read them. qBittorrent's 'moving'
(relocating from the incomplete folder to the complete one) reports progress
1.0 and the adapter maps it to 'queued', which ``client_download`` normalises
to 'downloading' — and its ``state != completed and pct < 100`` guard lets
anything at 100% straight through, so the organizer read a file mid-copy.
Usenet par2 repair and unrar have the same shape.

State alone cannot fix it: the adapter deliberately lumps 'moving' and
'checkingDL' in with 'queuedUP' (done, merely queued to seed), and refusing to
import on 'queued' would strand every seed-queued torrent at 100% forever. So
the gate is the filesystem: the content must read identically twice running
(one extra ~3s monitor tick). mtime is what makes it work for torrents — a
pre-allocated file has its final size from the first byte written.

Ported from the legacy fork (1.8.16).
"""

from __future__ import annotations

# How many CONSECUTIVE identical readings mean "nothing is writing to this any
# more". One reading proves nothing; two costs one extra monitor tick.
SETTLE_READS = 2

# download id -> (path, snapshot, consecutive_matches). Module-level because the
# monitor calls process_* once per tick with no memory between calls.
_settle_state: dict = {}
SETTLE_STATE_CAP = 512


def snapshot(path: str):
    """Size/count/mtime fingerprint, reusing the music side's tested helper.
    Unreadable reads as None ("not settled"), never as done."""
    try:
        from core.download_plugins.album_bundle import snapshot_incomplete_path
        return snapshot_incomplete_path(path)
    except Exception:   # noqa: BLE001
        return None


def content_has_settled(dl: dict, path: str, *, snapshot=snapshot,
                        required: int = SETTLE_READS) -> bool:
    """True once ``path`` has read identical ``required`` times in a row.

    An unreadable path, an empty directory, or a path that changed since the
    last tick all reset the count — a client that has just created the
    destination folder and not yet copied into it must not look finished.
    Errs toward waiting: being wrong costs a corrupt file in the library,
    waiting costs one tick."""
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
    if len(_settle_state) > SETTLE_STATE_CAP:
        # Downloads that vanish mid-settle would otherwise leak an entry each.
        for stale in list(_settle_state)[:len(_settle_state) - SETTLE_STATE_CAP]:
            _settle_state.pop(stale, None)
    return False


def forget_settle_state(dl_id) -> None:
    """Drop a download's settle progress (cancelled, failed, retried elsewhere)."""
    _settle_state.pop(str(dl_id), None)
