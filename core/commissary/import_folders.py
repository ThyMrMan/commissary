"""Import from any of your download folders, not only the Import folder.

Old fork 1.9.1. Upstream's Import page scans ``import.staging_path`` only, so a
release sitting where the torrent / usenet / Soulseek client left it had to be
moved by hand before it could be imported. Underneath, every Import endpoint
already takes its folder from ``ImportRouteRuntime.get_staging_path`` (the
scan cache is keyed on it), so scanning elsewhere is a matter of handing the
runtime another folder for that request.

The two surfaces are deliberately asymmetric. Browse lists folder names and
counts. The SCAN endpoints open every file they find and read its tags, and
the process endpoints MOVE files - so both are bounded to the folders
SoulSync already knows about: the Import folder, the Soulseek download and
transfer folders, the torrent / usenet completed folders. Containment is
checked over the resolved path on both sides, so a symlink or a
``downloads-elsewhere`` sibling can't step out, and "Up" stops at a root. The
music library is not one of them: an import moves files, and nothing here
should move a file out of the library.

Admin only, like the Import page's uploads.
"""

from __future__ import annotations

import os
from typing import Any, Dict, List, Optional, Tuple

from utils.logging_config import get_logger

logger = get_logger("commissary.import_folders")

AUDIO_EXTENSIONS = {".mp3", ".flac", ".ogg", ".opus", ".m4a", ".aac", ".wav", ".wma", ".aiff", ".aif",
                    ".alac", ".ape", ".wv", ".dsf"}
MAX_FOLDERS = 500
PARAM = "scan_path"

_LABELLED_KEYS = (
    ("Downloads", "soulseek.download_path"),
    ("Transfer", "soulseek.transfer_path"),
    ("Torrents", "download_source.torrent_download_path"),
    ("Usenet", "download_source.usenet_download_path"),
    ("Usenet", "usenet_client.completed_path"),
)


def _real(path: Any) -> Optional[str]:
    try:
        real = os.path.realpath(str(path))
    except (OSError, ValueError):
        return None
    return real if os.path.isdir(real) else None


def roots() -> List[Tuple[str, str]]:
    """``[(label, real path)]``: the Import folder, then every download folder."""
    from core.commissary.paths import split_paths
    from core.imports.paths import docker_resolve_path
    from core.imports.staging import get_staging_path
    from core.settings import config_manager

    found: List[Tuple[str, str]] = []
    seen = set()

    def add(label, path):
        real = _real(docker_resolve_path(path) if path else path) if path else None
        if real and os.path.normcase(real) not in seen:
            seen.add(os.path.normcase(real))
            found.append((label, real))

    try:
        add("Import folder", get_staging_path())
    except Exception:  # noqa: BLE001 - no Import folder is just one root fewer
        logger.debug("no Import folder to offer", exc_info=True)
    for label, key in _LABELLED_KEYS:
        for i, path in enumerate(split_paths(config_manager.get(key, "") or "")):
            add(label if i == 0 else f"{label} {i + 1}", path)
    return found


def extra_import_roots() -> List[str]:
    """The download folders upstream's import validation doesn't know about."""
    return [path for _label, path in roots()]


def within(path: Any) -> Optional[str]:
    """``path`` resolved, when it is inside one of the roots - else None."""
    if not path:
        return None
    real = _real(path)
    if real is None:
        return None
    for _label, root in roots():
        try:
            if os.path.commonpath([os.path.normcase(real), os.path.normcase(root)]) == os.path.normcase(root):
                return real
        except ValueError:  # different drives
            continue
    return None


def _is_admin() -> bool:
    try:
        from core.profile_context import is_admin_request
        return bool(is_admin_request())
    except Exception:  # noqa: BLE001
        return False


def runtime_overrides() -> Dict[str, Any]:
    """``ImportRouteRuntime`` fields for this request: the chosen folder, when
    an admin asked for one inside the roots. Nothing otherwise - the Import
    folder, exactly as upstream."""
    try:
        from flask import has_request_context, request
        if not has_request_context():
            return {}
        wanted = request.args.get(PARAM)
    except Exception:  # noqa: BLE001
        return {}
    if not wanted or not _is_admin():
        return {}
    folder = within(wanted)
    if folder is None:
        logger.warning("Import scan of %r refused: not inside a download or Import folder", wanted)
        return {}
    return {"get_staging_path": lambda: folder}


def _audio_count(folder: str, limit: int = 2000) -> int:
    count = 0
    try:
        for _root, _dirs, files in os.walk(folder):
            count += sum(1 for name in files if os.path.splitext(name)[1].lower() in AUDIO_EXTENSIONS)
            if count >= limit:
                break
    except OSError:
        pass
    return count


def browse(path: Optional[str]) -> Tuple[Dict[str, Any], int]:
    """One folder's subfolders, for the picker. No path: the roots themselves."""
    shortcuts = [{"label": label, "path": root} for label, root in roots()]
    if not path:
        return {"path": None, "parent": None, "folders": [], "shortcuts": shortcuts}, 200
    folder = within(path)
    if folder is None:
        exists = _real(path) is not None
        return {"error": "Not inside a download or Import folder" if exists else "Folder not found"}, \
            (403 if exists else 404)
    try:
        entries = sorted(os.scandir(folder), key=lambda e: e.name.casefold())
    except OSError as exc:
        return {"error": f"Could not read the folder: {exc.strerror or exc}"}, 403
    folders, direct_audio = [], 0
    for entry in entries:
        try:
            if entry.is_dir():
                if len(folders) < MAX_FOLDERS:
                    folders.append({"name": entry.name, "path": entry.path})
            elif os.path.splitext(entry.name)[1].lower() in AUDIO_EXTENSIONS:
                direct_audio += 1
        except OSError:
            continue
    at_root = any(os.path.normcase(folder) == os.path.normcase(root) for _label, root in roots())
    parent = None if at_root else os.path.dirname(folder)
    return {"path": folder, "parent": parent, "folders": folders, "audio_files": direct_audio,
            "total_audio": _audio_count(folder), "truncated": len(folders) >= MAX_FOLDERS,
            "shortcuts": shortcuts}, 200


def register(app) -> None:
    from flask import jsonify, request

    from core.profile_context import admin_only

    @app.route("/api/commissary/import/browse", methods=["GET"])
    @admin_only
    def commissary_import_browse():
        payload, status = browse(request.args.get("path"))
        return jsonify(payload), status
