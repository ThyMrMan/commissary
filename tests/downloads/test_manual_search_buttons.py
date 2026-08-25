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


# ── the download-missing modal's footer ──────────────────────────────────────
# Requested: "next to Add to Wishlist, provide a button to start a manual
# download/search as well". Four separate files build that footer, so the real
# risk is not the button — it is the button existing in three of them.
_MODAL_SOURCES = {
    "downloads.js": _DOWNLOADS,
    "shared-helpers.js": (_ROOT / "webui" / "static" / "shared-helpers.js").read_text(encoding="utf-8"),
    "sync-services.js": (_ROOT / "webui" / "static" / "sync-services.js").read_text(encoding="utf-8"),
    "sync-spotify.js": (_ROOT / "webui" / "static" / "sync-spotify.js").read_text(encoding="utf-8"),
}
_WISHLIST_TOOLS = (_ROOT / "webui" / "static" / "wishlist-tools.js").read_text(encoding="utf-8")


def test_every_modal_offering_the_wishlist_also_offers_manual_search():
    """The invariant that matters. A fifth modal copied from one of these, or a
    future edit touching only some, would otherwise ship the pair unevenly."""
    for name, src in _MODAL_SOURCES.items():
        wishlist = src.count('onclick="addModalTracksToWishlist(')
        manual = src.count('onclick="openManualSearchForModalSelection(')
        assert wishlist > 0, f"{name} lost its Add to Wishlist button"
        assert manual == wishlist, (
            f"{name} builds {wishlist} wishlist button(s) but {manual} manual-search "
            f"button(s) — every footer must offer both"
        )


def test_the_manual_search_button_sits_next_to_the_wishlist_one():
    """Nothing UNRELATED may drift between the two footer actions.

    The whole-album release picker is the one permitted neighbour, and it is
    permitted because it is the manual search button's sibling: pick a file for
    one ticked track, or pick a release for the whole album. Pushing it to the
    far side of Add to Wishlist to satisfy a positional rule would separate the
    two choices that belong together — the bug this test exists to prevent,
    rather than the fix for it."""
    for name, src in _MODAL_SOURCES.items():
        manual_at = src.index('id="manual-search-btn-')
        wishlist_at = src.index('id="add-to-wishlist-btn-')
        between = src[min(manual_at, wishlist_at):max(manual_at, wishlist_at)]
        strangers = between.count("<button") - between.count('id="album-release-btn-')
        assert strangers <= 1, (
            f"{name}: something other than the album release picker was "
            f"inserted between the two buttons"
        )


def test_only_the_shared_album_modal_needs_the_release_picker():
    """Why exactly one of the four footers grew a button.

    Every album surface — artist pages, discover, beatport, label detail,
    library re-download, the search page — opens the SAME modal via
    ``openDownloadMissingModalForArtistAlbum`` in shared-helpers.js. The other
    three footers serve playlists and YouTube, which have no single album to
    choose a release for. If a second footer ever starts rendering album
    context, this is the test that should stop being true."""
    shared = _MODAL_SOURCES["shared-helpers.js"]
    assert 'id="album-release-btn-' in shared
    for name in ("downloads.js", "sync-services.js", "sync-spotify.js"):
        assert 'id="album-release-btn-' not in _MODAL_SOURCES[name], name


def test_the_modal_action_routes_through_the_one_opener():
    body = _WISHLIST_TOOLS.split("async function openManualSearchForModalSelection(", 1)[1]
    body = body[:body.index("\nwindow.openManualSearchForModalSelection")]
    assert "window.openManualSearchFor(" in body, "must not hand-roll the task dance"
    assert "/api/downloads/manual-search/task" not in body


def test_the_modal_action_refuses_a_multi_selection_rather_than_guessing():
    """The picker is a per-track choice, so 'manually search these nine' has no
    meaning. Searching the first of them silently would be the wrong track."""
    body = _WISHLIST_TOOLS.split("async function openManualSearchForModalSelection(", 1)[1]
    body = body[:body.index("\nwindow.openManualSearchForModalSelection")]
    assert "tracks.length > 1" in body
    assert "tracks.length === 0" in body, "an empty selection must say so too"
    assert body.index("tracks.length === 0") < body.index("window.openManualSearchFor("), \
        "the guards must run before the picker opens"


def test_both_footer_buttons_read_the_selection_the_same_way():
    """They act on the same ticked rows. Two copies of the checkbox-reading
    logic would drift into the buttons disagreeing about what is selected."""
    assert "function selectedModalTracks(" in _WISHLIST_TOOLS
    assert _WISHLIST_TOOLS.count(".track-select-cb:checked") == 1, \
        "the selection logic was duplicated instead of shared"
    for fn in ("addModalTracksToWishlist", "openManualSearchForModalSelection"):
        body = _WISHLIST_TOOLS.split(f"function {fn}(", 1)[1][:2000]
        assert "selectedModalTracks(" in body, f"{fn} does not use the shared helper"


# ── the picker's own search box ──────────────────────────────────────────────
def test_the_manual_search_box_is_prefilled_with_the_track():
    """Reported: the header names the song but the box below it is empty, so the
    user retypes what the dialog just told them — with the Search button greyed
    out saying 'type at least 2 characters'."""
    block = _DOWNLOADS.split("const manualSearchHtml = `", 1)[1]
    block = block[:block.index("`;")]
    assert 'id="candidates-manual-search-input"' in block
    assert "value=\"${escapeHtml(prefill)}\"" in block, "the box must carry a value, not just a placeholder"
    assert 'placeholder=' in block, "the placeholder still helps when the prefill is empty"


def test_the_prefill_uses_the_track_the_modal_is_about():
    setup = _DOWNLOADS.split("const prefill = ", 1)[1][:400]
    assert "trackArtist" in setup and "trackName" in setup
    assert "Unknown" in setup, "placeholder names must not be searched for literally"
    assert ".slice(0, 300)" in setup, "must respect the input's maxlength"


def test_the_button_state_is_recomputed_after_wiring():
    """The button ships disabled. With a prefilled box it has to enable itself,
    or the fix would put a query in front of the user they cannot run."""
    body = _DOWNLOADS.split("function _wireManualSearch(", 1)[1]
    body = body[:body.index("\nfunction ")]      # to the end of this function, not a fixed window
    last_call = body.rindex("updateButtonState();")
    listener = body.index("input.addEventListener('input', updateButtonState);")
    assert last_call > listener, "updateButtonState must run once after wiring, not only on input"
