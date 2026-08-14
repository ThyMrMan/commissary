"""The Discover "block this artist" button — wiring the last mile.

``blockDiscoveryArtist()`` (webui/static/discover.js), its
``POST /api/discover/artist-blacklist`` endpoint, its DB table, and even its CSS
(``.track-compact-block``, hidden until the row is hovered) all shipped. The
markup that would let anyone press it never did, so the function sat with **zero
call sites** and the only route to blocking an artist was the blocked-artists
modal's search box: you had to leave the track you objected to, then type the
artist's name from memory.

That is a specific failure mode worth pinning — a feature can be complete at
every layer and still be unreachable, and nothing in the test suite noticed
because every individual piece was fine.

The behavioural assertions (escaping, the button's attributes, the row removal)
live in ``tests/js/discover_block_button_harness.mjs`` and run under Node; this
module shells out to that and additionally pins the couplings a JS harness can't
see — that BOTH live renderers emit the button, and that the CSS grid reserves a
column for it.
"""

from __future__ import annotations

import re
import shutil
import subprocess
from pathlib import Path

import pytest


_REPO_ROOT = Path(__file__).resolve().parents[1]
_HARNESS = _REPO_ROOT / "tests" / "js" / "discover_block_button_harness.mjs"
_DISCOVER_JS = _REPO_ROOT / "webui" / "static" / "discover.js"
_STYLE_CSS = _REPO_ROOT / "webui" / "static" / "style.css"


def _node_available() -> bool:
    if not shutil.which("node"):
        return False
    try:
        out = subprocess.run(["node", "--version"], capture_output=True, text=True, timeout=15)
        return int(out.stdout.strip().lstrip("v").split(".")[0]) >= 18
    except Exception:
        return False


@pytest.mark.skipif(not _node_available(), reason="node >= 18 not available")
def test_block_button_behaviour_harness():
    """Escaping, the button's attributes, and the row-removal logic."""
    result = subprocess.run(
        ["node", str(_HARNESS)], capture_output=True, text=True, timeout=60,
        cwd=str(_REPO_ROOT),
    )
    assert result.returncode == 0, (
        f"discover block-button harness failed:\n{result.stdout}\n{result.stderr}"
    )


def _fn_body(src: str, name: str) -> str:
    start = src.index(f"function {name}(")
    end = src.index("\n}", start)
    return src[start:end]


# ── the actual fix: the button reaches the user ─────────────────────────────

@pytest.mark.parametrize("renderer", ["renderCompactPlaylist", "_renderTabbedTrackList"])
def test_every_live_track_renderer_emits_the_button(renderer):
    """Both renderers that are actually on screen must emit it.

    ``renderCompactPlaylist`` draws the mix modals (Hidden Gems, Discovery
    Shuffle, Popular Picks, Fresh Tape, The Archives, decades, Last.fm,
    ListenBrainz); ``_renderTabbedTrackList`` draws the decade and genre
    browsers. Wiring only one leaves half the Discover page without the
    affordance — indistinguishable from the bug, on those surfaces.
    """
    body = _fn_body(_DISCOVER_JS.read_text(encoding="utf-8"), renderer)
    assert "_blockArtistBtn(" in body, f"{renderer} does not render the block button"


def test_the_handler_is_no_longer_orphaned():
    """The regression this file exists for. Before the fix, the ONLY occurrence
    of ``blockDiscoveryArtist`` outside its own definition was... none."""
    src = _DISCOVER_JS.read_text(encoding="utf-8")
    call_sites = [
        m for m in re.finditer(r"blockDiscoveryArtist\s*\(", src)
        if not src[:m.start()].rstrip().endswith("async function")
    ]
    assert call_sites, "blockDiscoveryArtist() has no call sites — it is unreachable again"


def test_the_button_never_interpolates_the_name_into_javascript():
    """Artist names come from a scraped third-party source. The whole reason the
    value rides in ``data-artist`` is that it never becomes part of a JS string
    literal — an inline ``blockDiscoveryArtist('${name}')`` would put a quote in
    a name one character away from executing."""
    body = _fn_body(_DISCOVER_JS.read_text(encoding="utf-8"), "_blockArtistBtn")
    assert "blockDiscoveryArtist(this.dataset.artist)" in body
    assert not re.search(r"blockDiscoveryArtist\(\s*['\"`]", body), \
        "the artist name is being interpolated into an inline JS string"


# ── the CSS coupling a JS test cannot see ───────────────────────────────────

def test_the_grid_reserves_a_column_for_the_button():
    """The row is a CSS grid with a fixed column list. Adding a sixth cell
    without a sixth column makes it spill into an implicit row — the button
    lands UNDER the track instead of beside it, which looks like a broken
    layout rather than a new feature."""
    css = _STYLE_CSS.read_text(encoding="utf-8")
    block = css[css.index(".discover-playlist-track-compact {"):]
    block = block[:block.index("}")]
    cols = re.search(r"grid-template-columns:\s*([^;]+);", block)
    assert cols, "the compact row lost its grid-template-columns"
    assert len(cols.group(1).split()) == 6, (
        f"row renders 6 cells but the grid declares {len(cols.group(1).split())} columns: "
        f"{cols.group(1).strip()}"
    )


def test_the_button_css_it_depends_on_still_exists():
    """The class and its hover reveal predate this wiring — if either is renamed
    the button renders as an always-visible unstyled control on every row."""
    css = _STYLE_CSS.read_text(encoding="utf-8")
    assert ".track-compact-block {" in css
    assert ".discover-playlist-track-compact:hover .track-compact-block {" in css, \
        "the hover reveal is gone — the button would show on every row at all times"


def test_an_empty_artist_still_occupies_its_grid_cell():
    """Rows with no usable artist get no button, but must still emit a cell —
    otherwise that row's columns shift left against every other row."""
    body = _fn_body(_DISCOVER_JS.read_text(encoding="utf-8"), "_blockArtistBtn")
    assert "<span></span>" in body, "the no-button path emits nothing and breaks row alignment"
