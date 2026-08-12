"""The bottom-right floating chrome yields to anything full-height or modal.

Server Activity, the notification bell, the Interactive Help button and the
global search bar are all ``position: fixed`` in the bottom-right at z-index
999999 — deliberately above everything, so they stay reachable. That is the
right default and the wrong one whenever a surface owns that corner:

  * the Now Playing modal's transport controls,
  * the download-missing modal's footer actions,
  * the video Rename Files slide-over, which is pinned to the right edge for
    the full height at z-index 9101 — measured at 680px wide against a 1280px
    viewport, so it spans x 600-1280 while the bell and help button sit at
    x 1156-1256. They land squarely on its Preview/Apply controls.

Reported for the third case. Lowering their z-index would not help: the panel
is opaque, so "behind it" and "hidden" look identical — which is why the two
existing cases suppress them, and why this one does too.

These pin that the FOUR elements stay in step. The bug this file exists to
prevent is a fifth surface being added with three of the four listed, or a new
floating button joining the cluster and being left out of all three rules —
which is exactly what had happened to Server Activity: it was missing from both
pre-existing rules and would have kept covering the modals it was never listed
against.
"""

from __future__ import annotations

import pathlib
import re

import pytest

_ROOT = pathlib.Path(__file__).resolve().parents[1]
_CSS = (_ROOT / "webui" / "static" / "style.css").read_text(encoding="utf-8")
_RENAME_JS = (_ROOT / "webui" / "static" / "video" / "video-rename-panel.js").read_text(encoding="utf-8")

# Every floating element that owns the bottom-right corner. A new one added to
# the cluster has to be added here, which is the point.
_FLOATING = ("#gsearch-bar", "#gsearch-aura", "#activity-float-btn",
             "#notif-bell-btn", "#helper-float-btn")

# The surfaces that must win the corner, by the selector each rule keys off.
_SURFACES = {
    "Now Playing modal": "body.np-modal-open",
    "download-missing modal": 'body:has(.download-missing-modal[style*="display: flex"])',
    "video Rename Files panel": "body:has(.vrn-panel.vrn-open)",
}


@pytest.mark.parametrize("surface,key", sorted(_SURFACES.items()))
def test_every_surface_suppresses_every_floating_element(surface, key):
    for sel in _FLOATING:
        assert f"{key} {sel}" in _CSS, (
            f"the {surface} does not suppress {sel} — it will cover that corner"
        )


@pytest.mark.parametrize("surface,key", sorted(_SURFACES.items()))
def test_each_rule_actually_hides(surface, key):
    """A rule that lands in the file but sets something else is worse than none."""
    start = _CSS.index(f"{key} {_FLOATING[0]}")
    block = _CSS[start:_CSS.index("}", start) + 1]
    assert "display: none !important" in block, f"{surface}: the rule does not hide"


def test_the_rename_panel_rule_matches_the_class_the_panel_uses():
    """The CSS keys off .vrn-open. If the panel ever signalled its open state a
    different way, the rule would silently stop matching and the overlap would
    come back with nothing failing."""
    assert "'.vrn-panel{" in _RENAME_JS or '".vrn-panel{' in _RENAME_JS
    assert "classList.add('vrn-open')" in _RENAME_JS
    assert "classList.remove('vrn-open')" in _RENAME_JS


def test_the_panel_still_outranks_the_page_but_not_the_chrome():
    """Context for the fix: the panel's z-index is far below the FABs', which is
    why suppression rather than re-stacking is the answer here."""
    panel_z = int(re.search(r"\.vrn-panel\{[^}]*z-index:(\d+)", _RENAME_JS).group(1))
    fab_z = int(re.search(r"\.activity-float-btn\s*\{[^}]*z-index:\s*(\d+)", _CSS).group(1))
    assert panel_z < fab_z, (
        "the FABs no longer outrank the panel — if that changed deliberately, "
        "these suppression rules may no longer be needed"
    )


def test_the_suppression_is_scoped_and_not_a_blanket_hide():
    """The FABs must come back when the surface closes. A rule without its
    surface prefix would hide them permanently."""
    for sel in _FLOATING:
        for line in _CSS.splitlines():
            stripped = line.strip().rstrip(",")
            assert stripped != sel, (
                f"{sel} is hidden by a bare selector somewhere — it would never return"
            )
