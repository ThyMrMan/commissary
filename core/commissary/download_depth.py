"""Find a finished release that its client filed under category folders.

When a torrent/usenet client reports a path this process can't read (the usual
arr-stack remote-path mismatch), upstream's ``resolve_reported_save_path`` looks
for the same name under its download roots: ``<root>/<release>`` and
``<root>/<parent>/<release>``. Clients sort finished downloads into CATEGORY
folders - ``<root>/complete/Music/<release>`` - two levels below the root, so
the release never resolved and the download sat finished but never imported
(old fork 1.8.18 / 2.x).

This descends ``download_source.import_search_depth`` levels (default 3,
clamped 1..6), BREADTH-FIRST with depth as the outer loop across every root:
a release directly under one root always beats a same-named folder buried
under another, so a stale copy in an old category folder can't shadow the real
download. Within one level the most recently written match wins, as upstream
does across roots. These roots can point at whole download disks, so the scan
is bounded per root and unreadable folders are skipped.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Callable, Iterable, List, Optional, Tuple

DEFAULT_SEARCH_DEPTH = 3
MAX_SEARCH_DEPTH = 6
# folders examined per root, at most - a root mis-pointed at '/' stays a bounded scan
MAX_DIRS_PER_ROOT = 400


def search_depth(config_get: Callable[..., Any]) -> int:
    """Levels below a download root to search, clamped to something sane."""
    try:
        depth = int(config_get("download_source.import_search_depth", DEFAULT_SEARCH_DEPTH))
    except (TypeError, ValueError):
        return DEFAULT_SEARCH_DEPTH
    return max(1, min(MAX_SEARCH_DEPTH, depth))


def descendant_dirs(root: Any, max_depth: int, budget: int = MAX_DIRS_PER_ROOT) -> List[Tuple[int, Path]]:
    """``[(depth, dir), ...]`` below ``root``, shallowest first, at most ``budget``."""
    out: List[Tuple[int, Path]] = []
    frontier = [Path(root)]
    for depth in range(1, max(1, max_depth) + 1):
        if not frontier:
            break
        nxt = []
        for parent in frontier:
            try:
                entries = sorted(parent.iterdir())
            except OSError:  # missing / permission-denied - skip this branch
                continue
            for entry in entries:
                try:
                    if not entry.is_dir():
                        continue
                except OSError:  # broken symlink
                    continue
                out.append((depth, entry))
                nxt.append(entry)
                if len(out) >= budget:
                    return out
        frontier = nxt
    return out


def _mtime(path: Path) -> float:
    try:
        return path.stat().st_mtime
    except OSError:
        return -1.0


def _by_level(roots: Iterable[Any], config_get: Callable[..., Any]):
    depth = search_depth(config_get)
    per_root = [descendant_dirs(root, depth) for root in roots]
    for level in range(1, depth + 1):
        yield [d for descendants in per_root for (lvl, d) in descendants if lvl == level]


def find_release(roots: Iterable[Any], name: Optional[str], config_get: Callable[..., Any],
                 accept: Callable[[Any], bool]) -> Optional[str]:
    """``<root>/<category...>/<name>`` - a folder or file ``accept`` takes -
    the shallowest level first, the newest within a level."""
    if not name:
        return None
    for dirs in _by_level(roots, config_get):
        hits = [d / name for d in dirs if accept(d / name)]
        if hits:
            return str(max(hits, key=_mtime))
    return None


def find_holder(roots: Iterable[Any], expect_name: Optional[str], config_get: Callable[..., Any],
                contains: Callable[[Any], bool]) -> Optional[str]:
    """The client reported its save folder and the release sits in a category
    folder under one of our roots: that category folder - or the release file
    itself when it is a single file, never the folder around it (upstream's
    own rule for a root)."""
    if not expect_name:
        return None
    wanted = Path(expect_name).name
    for dirs in _by_level(roots, config_get):
        hits = [d for d in dirs if contains(d)]
        if hits:
            holder = max(hits, key=_mtime)
            target = holder / wanted
            try:
                return str(target) if target.is_file() else str(holder)
            except OSError:
                return str(holder)
    return None
