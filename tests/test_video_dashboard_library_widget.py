"""Video dashboard Library widget: shows more than just Movies/Shows totals.

The fixed tiles summed EVERY configured Library of a kind into one number, so
a second Library of the same kind (e.g. a separate Anime library) had no way
to show its own count. dashboard_stats() now carries a `by_library` breakdown
(tested in tests/test_video_database.py); this file pins the frontend wiring
that renders it as extra tiles in the existing widget.
"""

from __future__ import annotations

from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
_JS = (_ROOT / "webui" / "static" / "video" / "video-dashboard.js").read_text(encoding="utf-8")
_INDEX = (_ROOT / "webui" / "index.html").read_text(encoding="utf-8")


def _func(name: str) -> str:
    i = _JS.index("function " + name + "(")
    nxt = _JS.find("\n    function ", i + 1)
    return _JS[i:nxt if nxt != -1 else len(_JS)]


def test_library_widget_has_a_container_for_extra_tiles():
    assert "data-video-lib-stats" in _INDEX


def test_render_library_tiles_reads_the_backend_breakdown():
    assert "function renderLibraryTiles(" in _JS
    body = _func("renderLibraryTiles")
    assert "data-video-lib-stats" in body
    assert "lib.count" in body and "lib.label" in body


def test_render_library_tiles_is_called_with_the_breakdown_from_load_stats():
    body = _func("loadStats")
    assert "renderLibraryTiles(d.library && d.library.by_library)" in body


def test_extra_tiles_are_cleared_before_re_render():
    """Re-fetching stats must not pile up duplicate tiles on every poll."""
    body = _func("renderLibraryTiles")
    assert "data-video-lib-tile" in body
    assert ".remove()" in body


def test_extra_tiles_reuse_the_shared_stat_tile_markup():
    """Must match .library-status-stat / .library-status-stat-value / -label so
    they inherit the same styling as the fixed Movies/Shows/Episodes tiles —
    no separate CSS needed."""
    body = _func("renderLibraryTiles")
    assert "library-status-stat" in body
    assert "library-status-stat-value" in body
    assert "library-status-stat-label" in body
