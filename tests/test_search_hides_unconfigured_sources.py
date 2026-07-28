"""The search source picker shows only connections you've actually set up.

Reported as: on the Music Search page, hide Connections that are not active.

The picker used to render every known source and grey out the ones with no
credentials, with a "set up in Settings" tooltip — a row of buttons that cannot
answer a search. Now unconfigured sources are simply absent.

Two rails, because an empty picker is worse than a greyed-out one:

  * the ACTIVE source is always rendered, so the current selection can never
    become invisible;
  * if NOTHING is configured the full row comes back — that is the one case
    where the "set up in Settings" tooltips are the whole point, and a picker
    with zero buttons would leave no way to search or to learn why.

fetchSourceConfiguredMap already fails permissive (marks everything configured)
when /api/settings/config-status errors, so a network blip cannot empty the row.

This lives in the shared createSearchController, so it applies to both surfaces
that use it: the Search page and the global search widget.
"""

from __future__ import annotations

import pathlib

_ROOT = pathlib.Path(__file__).resolve().parents[1]


def _helpers():
    return (_ROOT / "webui" / "static" / "shared-helpers.js").read_text(encoding="utf-8")


def _render_fn():
    js = _helpers()
    return js.split("function renderSourceRow() {", 1)[1].split("\n    }", 1)[0]


def test_unconfigured_sources_are_filtered_out():
    body = _render_fn()
    assert "state.configuredSources[src] !== false" in body
    assert ".filter(" in body


def test_the_active_source_is_never_hidden():
    """Hiding the current selection would leave the picker with nothing marked
    active and the user unable to see what they are searching."""
    body = _render_fn()
    assert "src === state.activeSource" in body


def test_nothing_configured_falls_back_to_the_full_row():
    """With nothing set up, a one-button picker tells you nothing about what you
    could connect."""
    body = _render_fn()
    assert "const anyConfigured =" in body
    assert ": fullOrder" in body


def test_the_emptiness_check_ignores_the_active_source_rail():
    """The bug this pins: testing the COMBINED list for emptiness made the
    fallback unreachable — the active-source rail always keeps one entry, so the
    length was never 0 and 'nothing configured' rendered a single lone button
    instead of the full row. anyConfigured must be computed from fullOrder
    alone, before the rail is applied."""
    body = _render_fn()
    any_line = body.split("const anyConfigured =", 1)[1].split("\n", 1)[0]
    assert "fullOrder.some(" in any_line
    assert "activeSource" not in any_line


def test_the_row_is_built_from_the_filtered_order():
    """The filter has to reach the map() that emits the buttons — computing it
    and then rendering fullOrder anyway would be a no-op."""
    body = _render_fn()
    order_block = body.split("const order =", 1)[1].split(";", 1)[0]
    assert "configuredSources" in order_block
    assert "order.map(src =>" in body


def test_the_permissive_fallback_still_stands():
    """If /api/settings/config-status fails, every source is marked configured —
    so a failed request shows the full row rather than hiding everything."""
    js = _helpers()
    fn = js.split("async function fetchSourceConfiguredMap(", 1)[1].split("\n}", 1)[0]
    tail = fn.split("catch", 1)[1]
    assert "map[src] = true" in tail


def test_the_optimistic_default_is_unchanged():
    """Before config-status resolves, everything is assumed configured, so the
    first paint is the full row rather than an empty one."""
    js = _helpers()
    assert "for (const src of SOURCE_ORDER) state.configuredSources[src] = true;" in js


def test_both_surfaces_share_this_controller():
    """The Search page and the global search widget both build their picker from
    createSearchController, so neither can drift from this rule."""
    for rel in ("webui/static/search.js", "webui/static/downloads.js"):
        src = (_ROOT / rel).read_text(encoding="utf-8")
        assert "createSearchController({" in src, rel
