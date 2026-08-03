"""Both tracker pickers have to say what they do, where it is read.

The root cause of "deselecting a tracker doesn't stop it being used" was not
only that the selection was a ranking nudge — it was that the one sentence
saying so was attached to an element the code then hid:

    indexerIdsInput.title = '… a soft nudge, not a search filter.'
    ...
    input.type = 'hidden';        # renderTrackerPicker, once the picker renders

A hidden input renders neither tooltip nor placeholder, so the explanation
disappeared exactly when the checkboxes it described appeared. What remained was
an unlabelled list of trackers with checkboxes, which reads as "search these".

So these pin the captions as much as the wiring — a control that lies about
itself is the bug, not a cosmetic complaint.
"""

from __future__ import annotations

import pathlib

_ROOT = pathlib.Path(__file__).resolve().parent.parent
_INDEX = (_ROOT / "webui" / "index.html").read_text(encoding="utf-8")
_SETTINGS_JS = (_ROOT / "webui" / "static" / "settings.js").read_text(encoding="utf-8")
_VIDEO_SETTINGS_JS = (_ROOT / "webui" / "static" / "video" / "video-settings.js").read_text(encoding="utf-8")
_VIDEO_CSS = (_ROOT / "webui" / "static" / "video" / "video-side.css").read_text(encoding="utf-8")
_CSS = (_ROOT / "webui" / "static" / "style.css").read_text(encoding="utf-8")


def _library_row():
    body = _VIDEO_SETTINGS_JS[_VIDEO_SETTINGS_JS.index("function libraryRow("):]
    return body[:body.index("\n    function ", 10)]


# ── the per-Library picker ───────────────────────────────────────────────────
def test_the_library_picker_has_a_visible_caption():
    """Not a title= on the input — that is the element which gets hidden."""
    row = _library_row()
    assert "library-tracker-label" in row
    assert "data-lib-trackers-label" in row


def test_the_caption_is_a_real_element_and_not_attached_to_the_hidden_input():
    """The caption must be appended to the row in its own right, so that
    input.type='hidden' cannot take it with it."""
    row = _library_row()
    label_pos = row.index("library-tracker-label")
    assert "fields.appendChild(trackerLabel)" in row[label_pos:], \
        "the caption is built but never added to the row"


def test_the_caption_says_only_ticked_trackers_are_searched():
    row = _library_row().lower()
    assert "only those are" in row or "only the ticked" in row
    assert "automatic" in row, "must be explicit that unattended searches obey it too"


def test_the_caption_explains_what_unticked_everything_means():
    """The 'blank = all' rule is the least guessable part: an empty selection
    is the opposite of 'search nothing'."""
    row = _library_row().lower()
    assert "unticked" in row and "all of them" in row


def test_the_stale_soft_nudge_wording_is_gone():
    """It described behaviour that no longer exists, and describing a filter as
    a nudge is how this was misread in the first place."""
    assert "soft nudge" not in _VIDEO_SETTINGS_JS
    assert "not a search filter" not in _VIDEO_SETTINGS_JS
    assert "no preference" not in _VIDEO_SETTINGS_JS


def test_the_caption_is_styled_and_the_reason_is_recorded():
    assert ".library-tracker-label" in _VIDEO_CSS
    assert "type=hidden" in _VIDEO_CSS or "hidden" in _VIDEO_CSS


# ── the global Restrict field ────────────────────────────────────────────────
def test_the_configured_indexers_are_selectable():
    """They used to be read-only cards sitting under a box asking for the very
    ids they displayed — visible but not clickable."""
    assert "data-prowlarr-indexer" in _SETTINGS_JS
    assert 'type="checkbox"' in _SETTINGS_JS.split("function loadProwlarrIndexers", 1)[1][:3000]


def test_ticking_a_card_writes_the_restrict_field():
    body = _SETTINGS_JS.split("function loadProwlarrIndexers", 1)[1][:3500]
    assert "getElementById('prowlarr-indexer-ids')" in body
    assert "picked.join(',')" in body


def test_the_cards_start_checked_from_the_saved_value():
    """Otherwise reopening Settings shows an empty selection over a non-empty
    field, and the first tick would wipe the saved restriction."""
    body = _SETTINGS_JS.split("function loadProwlarrIndexers", 1)[1][:3500]
    assert "_restrictIndexerIds()" in body
    assert "selected.has(" in body
    assert "function _restrictIndexerIds" in _SETTINGS_JS


def test_the_restrict_field_stays_visible():
    """The lesson from the per-Library bug: do not hide the element carrying
    the explanation. The text box remains, in sync with the checkboxes."""
    body = _SETTINGS_JS.split("function loadProwlarrIndexers", 1)[1][:3500]
    assert "type = 'hidden'" not in body
    assert 'type="hidden"' not in body
    field = _INDEX.split('id="prowlarr-indexer-ids"', 1)[0][-200:]
    assert 'type="text"' in field


def test_the_help_text_states_the_only_these_rule():
    block = _INDEX.split('id="prowlarr-indexer-ids"', 1)[1][:900].lower()
    assert "only thing searched" in block or "only those are searched" in block
    assert "automatic" in block


def test_the_help_text_states_how_a_library_interacts_with_it():
    """Two independent restrictions that AND together is the part users cannot
    infer — and getting it wrong means an empty search."""
    block = _INDEX.split('id="prowlarr-indexer-ids"', 1)[1][:900].lower()
    assert "narrow" in block and ("widen" in block or "wider" in block)


def test_the_configured_indexers_list_is_captioned():
    block = _INDEX.split("Configured Indexers:", 1)[1][:700].lower()
    assert "tick" in block
    assert "only" in block
    assert "unticked" in block


def test_the_selected_card_has_a_style():
    assert ".ind-indexer-card-on" in _CSS
    assert "cursor: pointer" in _CSS.split(".ind-indexer-card {", 1)[1][:600] or \
        "cursor: pointer" in _CSS.split(".ind-indexer-card:hover", 1)[1][:600]
