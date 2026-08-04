"""The picker presents every source's answer, grouped and honestly labelled.

Un-gating the source list (see ``tests/test_manual_search_endpoint.py``) means
a search can now hit a dozen sources at once. A flat, undifferentiated result
table can't answer the question the user actually has — "who has this, and in
what form?" — so the modal groups results per source and corrals the
release-level ones (torrent/usenet, which index whole albums) behind a note
saying what picking one does.

These are source guards in this repo's established no-JS-runner style: they
pin the wiring, not the rendering. The rendering is verified in the browser.
"""

from __future__ import annotations

import pathlib

_ROOT = pathlib.Path(__file__).resolve().parents[2]
_DOWNLOADS = (_ROOT / "webui" / "static" / "downloads.js").read_text(encoding="utf-8")
_SEARCH = (_ROOT / "webui" / "static" / "search.js").read_text(encoding="utf-8")
_HELPERS = (_ROOT / "webui" / "static" / "shared-helpers.js").read_text(encoding="utf-8")
_CSS = (_ROOT / "webui" / "static" / "style.css").read_text(encoding="utf-8")


def _wire_manual_search_body() -> str:
    body = _DOWNLOADS.split("function _wireManualSearch(", 1)[1]
    return body[:body.index("\nasync function downloadCandidate(")]


# ── the source picker is no longer keyed on hybrid mode ──────────────────────
def test_the_dropdown_is_driven_by_source_count_not_by_mode_name():
    """It used to render only when download_mode === 'hybrid'. A single-source
    user can now have several searchable sources, so keying off the mode hid
    the picker from exactly the people who most needed it."""
    assert "const multiSource = availableSources.length > 1;" in _DOWNLOADS
    assert "const isHybrid = downloadMode === 'hybrid';" not in _DOWNLOADS


def test_the_modal_hands_the_source_list_to_the_search_wiring():
    """The per-source metadata (label, release_level) has to reach the code
    that builds the result groups, or every group falls back to a bare id."""
    assert "_wireManualSearch(overlay, data.task_id, trackName, multiSource, availableSources);" in _DOWNLOADS
    assert "function _wireManualSearch(overlay, taskId, trackName, multiSource, availableSources)" in _DOWNLOADS


# ── grouped results ──────────────────────────────────────────────────────────
def test_results_are_grouped_per_source():
    body = _wire_manual_search_body()
    assert "const _groupFor = (sourceId)" in body
    assert "candidates-source-group" in body
    # Rows are appended into their own source's group, not one flat table.
    assert "const _appendRows = (sourceId, newCandidates)" in body


def test_the_stream_routes_each_source_event_to_its_own_group():
    body = _wire_manual_search_body()
    assert "_appendRows(msg.source, msg.candidates)" in body


def test_a_source_that_found_nothing_still_reports_back():
    """Silence from a source is indistinguishable from a hang. "Qobuz: no
    results" is a real answer to "who has this?"."""
    body = _wire_manual_search_body()
    assert "_noteEmptySource" in body
    assert "no results" in body


def test_a_failed_source_is_marked_on_its_own_group():
    body = _wire_manual_search_body()
    assert "_noteSourceError" in body
    assert "candidates-source-group-count-error" in body


def test_an_empty_search_keeps_the_per_source_breakdown():
    """The old code replaced the whole container with one "no results" line,
    throwing away the per-source detail — which is the most useful part of a
    search that found nothing."""
    body = _wire_manual_search_body()
    done_branch = body.split("msg.type === 'done'", 1)[1][:700]
    assert "resultsContainer.innerHTML" not in done_branch, \
        "the done handler still wipes the per-source groups"
    assert "_setStatus(" in done_branch


# ── release-level sources ────────────────────────────────────────────────────
def test_release_level_sources_are_separated_and_explained():
    body = _wire_manual_search_body()
    assert "candidates-manual-releases" in body
    assert "meta.release_level" in body, "release-level sources aren't routed anywhere different"
    # The note has to say what picking one actually does, not just name them.
    # Anchored inside _wireManualSearch — the album picker reuses the same CSS
    # class for its own (differently-worded) note earlier in the file.
    note = body.split("candidates-release-note", 1)[1][:600]
    assert "whole albums" in note
    assert "fails" in note, "the note must admit it can fail rather than importing the wrong file"


def test_release_level_is_carried_by_the_source_list_not_hardcoded_names():
    """Hardcoding 'torrent'/'usenet' in the frontend is a fourth place to
    forget when a source is added. It rides on the source entry instead."""
    body = _wire_manual_search_body()
    assert "'torrent'" not in body and '"torrent"' not in body
    assert "'usenet'" not in body and '"usenet"' not in body


# ── the search page's primary action ─────────────────────────────────────────
def test_clicking_a_track_row_opens_the_picker():
    """The picker IS the default action now.

    This test used to assert the opposite — that clicking a row started an
    automatic download and the picker needed its own control. That was the
    shipped behaviour, and it was wrong: the decision was that the picker
    replaces one-click download, and leaving the cascade on the click meant
    the picker existed but nobody ever met it. Searching every source and
    choosing is the point of the search page.
    """
    tracks_section = _SEARCH.split("onClick: () => openManualSearchFor(", 1)[1][:600]
    # ...and the cascade is still reachable, just not by default.
    assert "onAuto:" in tracks_section
    assert "handleEnhancedSearchTrackClick(track)" in tracks_section


def test_the_cascade_survives_as_an_explicit_choice():
    """Removing it outright would strip the only interactive way to exercise
    the fallback chain, which is what wishlist automation runs."""
    assert "config.onAuto" in _HELPERS
    assert "enh-item-auto-btn" in _HELPERS


def test_the_basic_result_row_leads_with_sources():
    """The other search renderer has to agree with the dropdown, or the same
    page teaches two different default actions."""
    actions = _SEARCH.split('<div class="result-actions">', 1)[1][:1600]
    assert actions.index("search-sources-button") < actions.index("download-button"), \
        "Sources must come before the auto-download button"
    assert "⬇ Auto" in actions and "⬇ Download" not in actions


def test_the_compact_row_helper_exposes_the_action_as_opt_in():
    """renderCompactSection is shared with artist-detail and library, which
    must not sprout a button they never asked for."""
    assert "config.onSources" in _HELPERS
    assert "enh-item-sources-btn" in _HELPERS


def test_the_row_buttons_do_not_also_trigger_the_card_action():
    """The card itself is clickable, so without stopPropagation a click on
    either button would ALSO fire the card's own action — previously that
    meant downloading while opening the picker; now it would mean opening
    the picker while starting the cascade. Wrong in both directions."""
    for marker in ("if (config.onSources) {", "if (config.onAuto) {"):
        block = _HELPERS.split(marker, 1)[1][:400]
        assert "e.stopPropagation()" in block, marker


# ── recovery inside a batch download ─────────────────────────────────────────
def test_a_failed_batch_row_offers_the_picker():
    """Begin Analysis runs the unattended cascade over a whole playlist, so a
    track that ends up failed is exactly where a human wants to choose the
    copy. The picker could always reach these rows — the status cell has been
    clickable all along — but nothing said so, leaving the wishlist as the only
    discoverable route."""
    # Anchor on the actionsEl branch specifically — the same three status
    # names appear earlier inside the "not a terminal state" guard.
    block = _DOWNLOADS.split(
        "actionsEl && ['failed', 'cancelled', 'not_found']", 1)[1][:900]
    assert "track-sources-btn" in block
    assert "showCandidatesModal(task.task_id)" in block


def test_a_completed_row_offers_nothing():
    """The button is recovery, not decoration — a finished track has nothing
    to re-pick, and offering it would invite re-downloading over a good file."""
    tail = _DOWNLOADS.split("'completed', 'post_processing'", 1)[1][:300]
    assert "actionsEl.innerHTML = '-'" in tail


def test_the_new_ui_is_styled():
    for cls in ("candidates-source-group", "candidates-source-group-count-error",
                "candidates-release-note", "enh-item-sources-btn",
                "enh-item-auto-btn", "track-sources-btn", "download-button--auto"):
        assert "." + cls in _CSS, cls
