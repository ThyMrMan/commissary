"""Per-user dashboard layout: drag to reorder, drag the right edge to resize.

Available to EVERY user — a personal view preference, not a permission — and
saved in localStorage like the other UI preferences here.

Two things in this feature are easy to get quietly wrong, and both are pinned
below:

  * Spans must be a data-span ATTRIBUTE, never an inline style.gridColumn. The
    grid drops to 2 columns at 1499px and 1 at 699px, where the CSS resets
    spans — and a media query cannot override an inline style. Get this wrong
    and anyone who widens a card silently breaks their own phone layout, on a
    viewport they may never test.

  * The admin's hide/show policy still wins. A card an admin has hidden must be
    invisible to the layout system: not shown, not draggable, not a drop
    target.

No JS test runner in this repo, so these are source guards over the module and
the stylesheet — the same approach as tests/test_dashboard_widgets.py.
"""

from __future__ import annotations

import os
import re

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _read(rel):
    with open(os.path.join(_ROOT, *rel.split('/')), encoding='utf-8') as fh:
        return fh.read()


_LAYOUT_JS = _read('webui/static/dashboard-layout.js')


def _strip_comments(js):
    """Code only. This file's own comments talk ABOUT the patterns some of
    these tests forbid, so a naive substring check would match the warning
    rather than a violation."""
    js = re.sub(r'/\*.*?\*/', '', js, flags=re.S)
    return re.sub(r'^\s*//.*$', '', js, flags=re.M)


_LAYOUT_CODE = _strip_comments(_LAYOUT_JS)
_WIDGETS_JS = _read('webui/static/dashboard-widgets.js')
_STYLE_CSS = _read('webui/static/style.css')
_INDEX = _read('webui/index.html')


def _media_block(max_width):
    """The body of `@media (max-width: Npx)` that contains the .dash-grid rules."""
    marker = f'@media (max-width: {max_width}px)'
    start = _STYLE_CSS.index(marker)
    while '.dash-grid' not in _STYLE_CSS[start:start + 4000]:
        start = _STYLE_CSS.index(marker, start + 1)
    return _STYLE_CSS[start:start + 4000]


# ── the responsive resets: the highest-value assertions here ────────────────

def test_spans_are_reset_on_a_single_column_viewport():
    """At <=699px the grid is one column and the shipped --wide/--full
    modifiers reset to auto. A user-set span must do the same or a card
    resized on a desktop overflows that user's phone."""
    block = _media_block(699)
    assert '.dash-grid .dash-card[data-span]' in block
    reset = block[block.index('.dash-grid .dash-card[data-span]'):]
    assert 'grid-column: auto' in reset[:200]


def test_spans_are_clamped_on_a_two_column_viewport():
    """At <=1499px there are only two columns, so a 3-wide card must clamp to
    the full row instead of spanning off the grid."""
    block = _media_block(1499)
    assert '.dash-grid .dash-card[data-span="3"]' in block


def test_span_rules_outrank_the_shipped_modifiers():
    """A card that ships --full must still honour a user who narrows it, so
    the data-span rules need both higher specificity and a later position."""
    full = _STYLE_CSS.index('.dash-card--full { grid-column: 1 / -1; }')
    span = _STYLE_CSS.index('.dash-grid .dash-card[data-span="1"]')
    assert span > full, "data-span rules must come after the shipped modifiers"
    # `.dash-grid .dash-card[data-span=…]` is 0,2,0 vs `.dash-card--full` 0,1,0.
    assert '.dash-grid .dash-card[data-span="3"]' in _STYLE_CSS


def test_width_is_never_written_as_an_inline_style():
    """An inline grid-column would defeat every media query above."""
    # Forbid WRITING an inline style, not the word itself (reading
    # gridTemplateColumns to count the grid's columns is legitimate).
    assert not re.search(r'\.style\.gridColumn', _LAYOUT_CODE)
    assert not re.search(r'style\s*\[\s*[\'"]gridColumn', _LAYOUT_CODE)
    assert not re.search(r'setProperty\(\s*[\'"]grid-column', _LAYOUT_CODE)
    assert "setAttribute('data-span'" in _LAYOUT_CODE


# ── the admin policy still wins ─────────────────────────────────────────────

def test_admin_hidden_cards_are_excluded_from_the_layout():
    start = _LAYOUT_JS.index('function _layoutCards')
    body = _LAYOUT_JS[start:_LAYOUT_JS.index('\nfunction ', start + 10)]
    assert "widgetHidden !== '1'" in body, (
        "a card the admin hid would be draggable and a drop target")


def test_layout_module_loads_after_the_widget_module():
    """It filters on the dataset flag applyWidgetPolicy() stamps."""
    widgets = _INDEX.index('dashboard-widgets.js')
    layout = _INDEX.index('dashboard-layout.js')
    assert widgets < layout


# ── storage ─────────────────────────────────────────────────────────────────

def test_storage_read_falls_back_instead_of_blanking_the_dashboard():
    start = _LAYOUT_JS.index('function _readDashLayout')
    body = _LAYOUT_JS[start:_LAYOUT_JS.index('\nfunction ', start + 10)]
    assert 'try {' in body and 'catch' in body
    assert 'return {}' in body


def test_storage_write_survives_a_blocked_quota():
    start = _LAYOUT_JS.index('function _writeDashLayout')
    body = _LAYOUT_JS[start:_LAYOUT_JS.index('\nfunction ', start + 10)]
    assert 'try {' in body and 'catch' in body


def test_only_deltas_are_stored():
    """A card left at its shipped width stores nothing, so a card added in a
    later release appears at its markup position instead of being dropped."""
    start = _LAYOUT_JS.index('function _persistSpan')
    body = _LAYOUT_JS[start:_LAYOUT_JS.index('\nfunction ', start + 10)]
    assert '_defaultSpanOf' in body and 'delete layout.spans' in body


def test_the_two_sides_are_stored_separately():
    assert "music: '#dashboard-page'" in _LAYOUT_JS
    assert "video: '[data-video-subpage=\"video-dashboard\"]'" in _LAYOUT_JS
    assert 'all[side] =' in _LAYOUT_JS


def test_storage_key_follows_the_established_prefix():
    assert "'soulsync-dashboard-layout'" in _LAYOUT_JS


# ── cards are moved, never rebuilt ──────────────────────────────────────────

def test_cards_are_reordered_not_recreated():
    """init.js's cursor-glow FX caches the card elements; moving nodes keeps
    those references valid, replacing them would not."""
    assert 'innerHTML' not in _LAYOUT_JS.split('_renderDashEditBar')[0], (
        "the apply path must not rebuild markup")
    assert 'appendChild' in _LAYOUT_JS and 'insertBefore' in _LAYOUT_JS


# ── the Customize control ───────────────────────────────────────────────────

def test_customize_button_is_outside_the_hideable_header_actions():
    """.header-actions is the header-enrich widget an admin can hide. Every
    user must be able to rearrange their own dashboard, so the button cannot
    live inside it."""
    for _ in range(2):     # one per dashboard
        pass
    assert _INDEX.count('data-dash-customize') == 2

    for match in re.finditer(r'data-dash-customize="(\w+)"', _INDEX):
        # Walk back to the nearest enclosing container start and prove it is
        # the quick-nav, not the header actions.
        before = _INDEX[:match.start()]
        last_quicknav = before.rfind('class="header-quick-nav"')
        last_actions = before.rfind('class="header-actions"')
        assert last_quicknav > last_actions, (
            f"the {match.group(1)} Customize button is inside .header-actions, "
            "which an admin can hide")


def test_customize_is_wired_without_an_inline_onclick():
    """The video side forbids inline handlers (script-split integrity
    contract), so both buttons are delegated."""
    start = _INDEX.index('data-dash-customize="video"')
    assert 'onclick' not in _INDEX[start:start + 400]
    assert "closest('[data-dash-customize]')" in _LAYOUT_JS


def test_edit_mode_offers_a_way_back():
    assert 'resetDashboardLayout' in _LAYOUT_CODE
    # The button reuses the shared .test-button styling; only the edit bar
    # itself needs rules of its own.
    assert 'dash-edit-bar__reset' in _LAYOUT_CODE
    assert '.dash-edit-bar' in _STYLE_CSS


def test_resize_is_reachable_without_a_mouse():
    assert 'ArrowLeft' in _LAYOUT_JS and 'ArrowRight' in _LAYOUT_JS
    assert 'aria-label' in _LAYOUT_JS


# ── registry agreement ──────────────────────────────────────────────────────

def test_every_layoutable_card_is_a_known_widget():
    """Layout keys are data-card values, the same ids the widget registry
    uses — so the two can't drift into disagreeing about what a card is."""
    registry = {i.split('.', 1)[1] for i in re.findall(r"id:\s*'([\w.-]+)'", _WIDGETS_JS)}
    cards = set(re.findall(r'data-card="([\w-]+)"', _INDEX))
    assert cards <= registry, f"cards unknown to the widget registry: {sorted(cards - registry)}"
