"""Video path resolver — from the SERVER's view of a file path to a real one.

The scanner stores what the media server reports (Plex ``part.file`` /
Jellyfin ``Path``) — the path as seen from INSIDE the server's own container/
host. From SoulSync's filesystem view that path often doesn't exist (different
Docker mounts, drive letters, NAS exports). This is the video twin of the
music side's ``core.library.path_resolver`` (the issue-#476 class of bugs):
try the stored path as-is, then re-root its tail segments against the folders
SoulSync actually knows about until a file exists.

Upgrades depend on this: replacing an owned copy means finding the REAL file,
not the template location SoulSync would have chosen for a fresh import.
"""

from __future__ import annotations

import os
from typing import Callable, Optional

# How many trailing path segments to re-root (file, its folder, a parent…).
_PROBE_DEPTH = 4

# The flat per-kind destination settings, in the order they have always been
# read. These predate the Libraries registry (root_folders) and remain the
# fallback for installs with no libraries configured — resolve_download_root
# in download_pipeline.py treats them the same way.
_LEGACY_PATH_KEYS = {"movie": "movies_path", "show": "tv_path", "youtube": "youtube_path"}


def _setting(db, key) -> str:
    try:
        return str(db.get_setting(key) or "").strip()
    except Exception:   # noqa: BLE001 - a settings hiccup must not break a scan
        return ""


def library_roots(db, kind: str | None = None) -> list:
    """Every folder SoulSync treats as a video library root.

    The Libraries registry (Settings → Connections) is the source of truth; the
    flat movies_path/tv_path/youtube_path settings (Settings → Downloads) are
    unioned in behind it as the documented fallback. Both are live: a library
    with no Destination Folder of its own falls back to the scalar, so a folder
    named by either can genuinely receive files and both deserve to be
    health-checked, recycled from, and searched when re-rooting a moved file.

    Registry paths come FIRST and the list is de-duplicated by absolute path,
    so a scalar that merely repeats a library path doesn't appear twice.
    """
    roots, seen = [], set()

    def _add(path):
        p = str(path or "").strip()
        if not p:
            return
        key = os.path.abspath(p)
        if key in seen:
            return
        seen.add(key)
        roots.append(p)

    try:
        for path in db.all_library_paths(kind):
            _add(path)
    except Exception:   # noqa: BLE001 - an old DB without the registry still works
        pass

    keys = ([_LEGACY_PATH_KEYS[kind]] if kind in _LEGACY_PATH_KEYS
            else list(_LEGACY_PATH_KEYS.values()))
    for key in keys:
        _add(_setting(db, key))
    return roots


def video_base_dirs(db) -> list:
    """The local folders video files can live under: every library root, plus
    the transfer folder (a staging area, NOT a library — which is why it isn't
    part of library_roots)."""
    dirs = library_roots(db)
    transfer = _setting(db, "transfer_path")
    if transfer and os.path.abspath(transfer) not in {os.path.abspath(d) for d in dirs}:
        dirs.append(transfer)
    return dirs


def resolve_video_file_path(stored_path, base_dirs, *, size_bytes=None,
                            exists: Callable[[str], bool] = os.path.exists,
                            getsize: Callable[[str], int] = os.path.getsize) -> Optional[str]:
    """Resolve a DB-stored (server-view) file path to a file that exists HERE.

    Tries the raw path first, then joins the path's last N..1 segments onto
    each base dir — DEEPEST FIRST, so the most specific match wins (a bare
    basename like 'movie.mkv' must never shadow the real
    'The Matrix (1999)/movie.mkv' when both exist).

    This resolver's callers REPLACE the file they resolve to (upgrades), so a
    re-rooted candidate must prove identity: when ``size_bytes`` is known, a
    candidate whose on-disk size differs is rejected. The raw stored path is
    exempt (it IS the recorded location). Returns the first hit or None —
    never raises."""
    if not isinstance(stored_path, str) or not stored_path:
        return None
    if exists(stored_path):
        return stored_path
    parts = [p for p in stored_path.replace("\\", "/").split("/") if p]
    if not parts:
        return None

    def _same_file(cand: str) -> bool:
        if not size_bytes:
            return True
        try:
            return int(getsize(cand)) == int(size_bytes)
        except Exception:   # noqa: BLE001 - unreadable size → can't prove identity
            return False

    for base in (base_dirs or []):
        base = str(base or "").rstrip("/\\")
        if not base:
            continue
        for k in range(min(_PROBE_DEPTH, len(parts)), 0, -1):
            cand = os.path.join(base, *parts[-k:])
            if exists(cand) and _same_file(cand):
                return cand
    return None
