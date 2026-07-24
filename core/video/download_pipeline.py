"""Pure helpers for the video download pipeline: locate a finished file in the
download dir and work out where it should move to.

slskd writes a completed download somewhere under the shared download folder (it
mirrors the remote folder structure), so we locate the file by basename. Kept pure
(filesystem access is injected) so it's unit-tested without touching disk.

Isolated: stdlib only; no music imports.
"""

from __future__ import annotations

import os
from typing import Any, Callable


def basename_of(path: Any) -> str:
    """Final path segment, handling both / and \\ separators (slskd uses Windows-style)."""
    return str(path or "").replace("\\", "/").rstrip("/").rsplit("/", 1)[-1]


def find_completed_file(download_dir: str, filename: str, lister: Callable) -> str | None:
    """Find the downloaded file on disk by basename. ``lister(dir)`` yields candidate
    full paths (injected: os.walk-based in the monitor, a list in tests). Returns the
    largest match (the real video, not a stray same-named bit), or None."""
    base = basename_of(filename)
    if not base or not download_dir:
        return None
    matches = [p for p in lister(download_dir) if basename_of(p) == base]
    if not matches:
        return None
    return matches[0] if len(matches) == 1 else max(matches, key=len)


def dest_path_for(target_dir: str, src_path: str) -> str:
    """Where a finished file moves to inside its library folder (flat, by basename)."""
    return os.path.join(str(target_dir or ""), basename_of(src_path))


def resolve_download_root(kind: str, *, root_folder: dict | None = None,
                          primary_root_folder: dict | None = None,
                          paths: dict | None = None) -> str:
    """Where a finished movie/show download should land (multi-library, P.4).
    Kept pure like the rest of this module — callers fetch the DB-backed
    ingredients and pass them in. Order:
      1. ``root_folder`` — an explicitly-picked Library (the interactive "Get"
         flow's dropdown, or a season-pack/manual-import override).
      2. ``primary_root_folder`` — the lowest-``sort_order`` configured
         Library for this kind (the default for unattended grabs: wishlist
         drain, RSS instant-grab, repair upgrades — nobody's there to pick).
      3. ``paths`` — the legacy scalar {movies_path, tv_path[, transfer_path]}
         config, unchanged fallback for installs with zero Libraries configured.
    Each dict wins only when it actually carries a non-blank ``path``/*_path,
    so a Library row that exists but has no destination set yet doesn't
    shadow a good fallback."""
    k = "movie" if str(kind or "").lower() in ("movie", "movies", "film", "films") else "show"
    if root_folder and root_folder.get("path"):
        return root_folder["path"]
    if primary_root_folder and primary_root_folder.get("path"):
        return primary_root_folder["path"]
    paths = paths or {}
    if k == "movie":
        return paths.get("movies_path") or paths.get("transfer_path") or ""
    return paths.get("tv_path") or ""


__all__ = ["basename_of", "find_completed_file", "dest_path_for", "resolve_download_root"]
