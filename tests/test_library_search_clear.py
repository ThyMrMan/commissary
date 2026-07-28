"""A clear button on the library-style search fields.

Three fields share the same markup — the video Library, the music Library and
Purchased — so this is ONE delegated implementation rather than three copies of
a five-line behaviour that would drift apart.

The load-bearing detail: the button does NOT filter anything itself. It clears
the input and dispatches the same ``input`` event typing produces, so each page's
existing debounce/reload runs unchanged. A second, page-aware code path could
filter differently from typing, and that difference would only show up in the
cases nobody tests.
"""

from __future__ import annotations

import pathlib
import re

import pytest

_ROOT = pathlib.Path(__file__).resolve().parents[1]
_HTML = (_ROOT / "webui" / "index.html").read_text(encoding="utf-8")
_JS = (_ROOT / "webui" / "static" / "search-clear.js").read_text(encoding="utf-8")
_CSS = (_ROOT / "webui" / "static" / "style.css").read_text(encoding="utf-8")


# ── it's on every field that has this shape ──────────────────────────────────
def test_all_three_search_fields_get_a_clear_button():
    """video Library, music Library, Purchased. Missing one is the bug this
    count exists to catch."""
    assert _HTML.count("data-search-clear") == 3
    assert _HTML.count('class="library-search-container"') == 3


def test_each_button_is_paired_with_its_own_input():
    """Delegation resolves the input via closest('.library-search-container'), so
    each button must live in the same container as an input.

    Sliced container-to-container rather than to the next `</div>`: the icon
    closes a div BEFORE the button, so matching the first one truncates the
    block and the button falls outside it."""
    parts = _HTML.split('class="library-search-container"')[1:]
    assert len(parts) == 3
    for block in parts:
        assert "library-search-input" in block
        # Both must appear before the NEXT container starts, which the split
        # already guarantees — check ordering so the button can't precede the
        # input's container.
        assert "data-search-clear" in block
        assert block.index("library-search-input") < block.index("data-search-clear")


def test_the_script_is_actually_loaded():
    assert "search-clear.js" in _HTML


# ── it must not become a second filtering path ───────────────────────────────
def test_clearing_replays_the_event_typing_produces():
    """The whole design: the page's own handler does the filtering."""
    assert "new Event('input', { bubbles: true })" in _JS


def test_it_does_not_reach_into_any_page_module():
    """Naming a page's internals here would need updating every time one
    changes, and would silently stop working when one did."""
    for leak in ("video-lib", "purchased-search-input", "library-search-input\"",
                 "reload(", "VideoLibrary", "loadLibrary"):
        assert leak not in _JS, leak


def test_it_is_delegated_rather_than_bound_per_element():
    """The pages re-render around these fields; a bound listener would be
    thrown away."""
    assert _JS.count("document.addEventListener") >= 3


# ── the states ───────────────────────────────────────────────────────────────
def test_the_button_is_hidden_while_the_field_is_empty():
    assert "hidden>" in _HTML or "hidden >" in _HTML
    assert "btn.hidden = !String(input.value || '').length" in _JS
    assert ".library-search-clear[hidden] { display: none; }" in _CSS


def test_a_field_that_already_holds_text_shows_the_button():
    """A revisited page can arrive with text already in the field."""
    assert "function syncAll" in _JS
    assert "soulsync:video-page-shown" in _JS
    assert "DOMContentLoaded" in _JS


def test_focus_returns_to_the_field():
    """Clearing to type something else shouldn't cost a click."""
    assert "input.focus()" in _JS


def test_escape_clears_but_only_when_there_is_something_to_clear():
    """Swallowing Escape on an empty field would stop it closing a modal."""
    m = re.search(r"if \(e\.key !== 'Escape'.*?\n(.*?)\n\s*\}\);", _JS, re.S)
    assert m, "Escape handler not found"
    body = m.group(0)
    assert "if (!e.target.value) return;" in body
    assert "e.stopPropagation();" in body
    assert body.index("if (!e.target.value) return;") < body.index("e.stopPropagation();")


# ── it must not damage the field it sits in ──────────────────────────────────
def test_the_input_reserves_room_for_the_button():
    """Without the padding a long query slides underneath the ×."""
    assert ".library-search-container .library-search-input { padding-right: 42px; }" in _CSS


def test_the_button_is_reachable_and_labelled():
    assert 'aria-label="Clear search"' in _HTML
    assert 'type="button"' in _HTML.split("data-search-clear")[0][-120:]
    assert ".library-search-clear:focus-visible" in _CSS


def test_it_is_a_button_not_a_bare_element():
    """A div would be unreachable by keyboard."""
    for chunk in _HTML.split("data-search-clear")[1:]:
        pass
    assert _HTML.count("<button type=\"button\" class=\"library-search-clear\"") == 3


# ── the shared JS guard covers this file too ─────────────────────────────────
def test_no_undeclared_interpolated_identifiers():
    """Same check as the other static JS — a name used but declared nowhere is
    a runtime ReferenceError that a syntax check happily passes."""
    from tests.test_video_js_no_undeclared_locals import (GLOBALS, _INTERP,
                                                          _declared_names, _strip_comments)
    text = _strip_comments(_JS)
    declared = _declared_names(text) | GLOBALS
    missing = sorted({n for n in _INTERP.findall(text) if n not in declared})
    assert not missing, missing
