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
def test_track_rows_in_the_search_dropdown_offer_the_picker():
    """Clicking a track row starts an automatic download. The deliberate
    choice needs its own control on the same row."""
    # Anchor on the mapItem body, not the section id — 'enh-tracks-section'
    # also appears earlier in a list of element ids and splitting there reads
    # the wrong block.
    tracks_section = _SEARCH.split("onClick: () => handleEnhancedSearchTrackClick(track)", 1)[1][:600]
    assert "onSources:" in tracks_section
    assert "openManualSearchFor(" in tracks_section


def test_the_compact_row_helper_exposes_the_action_as_opt_in():
    """renderCompactSection is shared with artist-detail and library, which
    must not sprout a button they never asked for."""
    assert "config.onSources" in _HELPERS
    assert "enh-item-sources-btn" in _HELPERS


def test_the_sources_button_does_not_also_trigger_the_row_download():
    """The card's own click handler starts an auto-download. Without
    stopPropagation, one click would both open the picker AND download."""
    block = _HELPERS.split("if (config.onSources) {", 1)[1][:400]
    assert "e.stopPropagation()" in block


def test_the_new_ui_is_styled():
    for cls in ("candidates-source-group", "candidates-source-group-count-error",
                "candidates-release-note", "enh-item-sources-btn"):
        assert "." + cls in _CSS, cls
