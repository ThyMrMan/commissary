"""Video dashboard Library widget: per-Library tiles REPLACE the totals.

The fixed Movies/Shows/Disk Size tiles summed EVERY configured Library of a
kind into one number, which is meaningless once an Anime library sits beside
a standard TV one. dashboard_stats() carries a `by_library` breakdown with
each Library's own count AND its own disk size (tested in
tests/test_video_database.py); this file pins the frontend wiring that renders
it — including hiding the aggregates it supersedes.
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


def test_each_tile_shows_that_librarys_own_disk_size():
    """The single 'Disk Size' aggregate summed every Library; each tile now
    carries its own bytes instead."""
    body = _func("renderLibraryTiles")
    assert "lib.size_bytes" in body
    assert "formatBytes(lib.size_bytes)" in body


def test_aggregate_tiles_are_hidden_once_libraries_are_configured():
    """Movies / Shows / Disk Size sum across Libraries — they must give way to
    the per-Library tiles, and stay only as the no-Library-configured fallback
    (so a fresh install still sees something)."""
    assert "data-video-lib-agg" in _INDEX
    # exactly the three aggregate tiles, not Episodes (a true library-wide total)
    assert _INDEX.count("data-video-lib-agg") == 3
    body = _func("renderLibraryTiles")
    assert "data-video-lib-agg" in body
    assert "hidden = libs.length > 0" in body
