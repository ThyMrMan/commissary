"""The multi-source picker is reachable from search, album rows and the wishlist.

The picker and its per-source search already existed; what was missing was any
way in that didn't require a download to have already failed. So the placements
matter as much as the endpoint — this pins that all three exist, that each hands
over enough metadata to search with, and that every one of them routes through
the SAME opener so a future change can't leave one behind on a private path.
"""

from __future__ import annotations

import pathlib

_ROOT = pathlib.Path(__file__).resolve().parents[2]
_DOWNLOADS = (_ROOT / "webui" / "static" / "downloads.js").read_text(encoding="utf-8")
_LIBRARY = (_ROOT / "webui" / "static" / "library.js").read_text(encoding="utf-8")
_SEARCH = (_ROOT / "webui" / "static" / "search.js").read_text(encoding="utf-8")
_CSS = (_ROOT / "webui" / "static" / "style.css").read_text(encoding="utf-8")


# ── the shared opener ────────────────────────────────────────────────────────
def test_there_is_one_opener_and_it_is_global():
    """search.js and library.js both call it, so it has to be on window."""
    assert "async function openManualSearchFor(" in _DOWNLOADS
    assert "window.openManualSearchFor = openManualSearchFor;" in _DOWNLOADS


def test_the_opener_creates_a_task_then_reuses_the_existing_modal():
    body = _DOWNLOADS.split("async function openManualSearchFor(", 1)[1]
    body = body[:body.index("\nwindow.openManualSearchFor")]
    assert "'/api/downloads/manual-search/task'" in body
    assert "showCandidatesModal(data.task_id)" in body


def test_the_opener_reports_a_failure_rather_than_silently_doing_nothing():
    body = _DOWNLOADS.split("async function openManualSearchFor(", 1)[1]
    body = body[:body.index("\nwindow.openManualSearchFor")]
    assert "showToast" in body
    assert "finally" in body, "the button must be re-enabled even when it fails"


# ── the three placements ─────────────────────────────────────────────────────
def test_the_search_page_offers_it():
    assert "search-sources-button" in _SEARCH
    assert "openManualSearchFor(" in _SEARCH


def test_the_search_button_passes_the_result_it_belongs_to():
    row = _SEARCH.split("search-sources-button", 1)[1][:600]
    for attr in ("data-ms-name", "data-ms-artist", "data-ms-album"):
        assert attr in row, attr


def test_the_search_button_does_not_also_trigger_the_row_click():
    """The row itself has an onclick; without stopPropagation the picker would
    open and the row would select at the same time."""
    row = _SEARCH.split("search-sources-button", 1)[1][:400]
    assert "event.stopPropagation()" in row


def test_album_track_rows_offer_it_whether_owned_or_missing():
    """Missing: the only route used to be the wishlist and a wait. Owned: swap
    in a better copy."""
    assert _LIBRARY.count("enhanced-manual-search-btn") >= 3   # two renders + handler
    # Anchor on the actions cell — `track._missingExpected` also appears earlier
    # for the row's own CSS class, and splitting there reads the wrong block.
    actions = _LIBRARY.split("actionsTd.className = 'col-track-actions'", 1)[1]
    missing_branch, owned_branch = actions.split("} else {", 1)
    assert "enhanced-manual-search-btn" in missing_branch, "missing tracks have no way in"
    assert "enhanced-manual-search-btn" in owned_branch[:900], "owned tracks have no way in"


def test_the_album_row_handler_passes_the_track_and_its_album():
    body = _LIBRARY.split("const msBtn = target.closest('.enhanced-manual-search-btn')", 1)[1][:600]
    assert "openManualSearchFor(" in body
    assert "track.title" in body
    assert "album" in body


def test_wishlist_rows_offer_it():
    assert "wishlist-manual-search-btn" in _DOWNLOADS
    assert "data-manual-search-track" in _DOWNLOADS


def test_the_wishlist_button_carries_the_track_it_names():
    row = _DOWNLOADS.split("wishlist-manual-search-btn", 1)[1][:500]
    for attr in ("data-ms-name", "data-ms-artist", "data-ms-album"):
        assert attr in row, attr


def test_the_wishlist_search_is_matched_before_the_delete_button():
    """Both live in the same row. If the delete handler matched first, clicking
    search would remove the track instead — the worst possible mix-up here."""
    handler = _DOWNLOADS.split("container.addEventListener('click'", 1)[1]
    search_at = handler.index("data-manual-search-track")
    delete_at = handler.index(".wishlist-delete-btn'")
    assert search_at < delete_at, "the delete handler would swallow the search click"


# ── every entry point goes through the one opener ────────────────────────────
def test_no_placement_calls_the_endpoint_directly():
    """They must not hand-roll the create-then-open dance; one opener means one
    place to fix when the flow changes."""
    for name, src in (("library.js", _LIBRARY), ("search.js", _SEARCH)):
        assert "/api/downloads/manual-search/task" not in src, \
            f"{name} bypasses openManualSearchFor"


def test_the_buttons_are_styled():
    for cls in ("enhanced-manual-search-btn", "wishlist-manual-search-btn",
                "search-sources-button"):
        assert "." + cls in _CSS, cls
        assert "." + cls + "[disabled]" in _CSS, f"{cls} has no disabled state"
