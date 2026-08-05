"""Filtering discovery results by match quality.

Discovery answers "which library track is this?" for every row of an imported
playlist, and the answers are not equally trustworthy. On a 200-track import
the handful needing a human decision are buried among the ones that matched
cleanly, so the buckets are filterable rather than only colour-coded.

The PERFECT/LOW line is 0.9 because that is the strictest bar any source
applies before it will call something a match (Beatport and ListenBrainz both
use 0.9; `core/discovery/playlist.py` accepts down to 0.7). "Low" therefore
means "accepted, but by a looser rule than the strictest source would have
used" — which is exactly the set worth reviewing.
"""

from __future__ import annotations

import re
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[2]
_SYNC = (_ROOT / "webui" / "static" / "sync-services.js").read_text(encoding="utf-8")
_CSS = (_ROOT / "webui" / "static" / "style.css").read_text(encoding="utf-8")


def _classifier() -> str:
    return _SYNC.split("function discoveryBucketFor(", 1)[1][:1200]


# ── the threshold is tied to the backend, not invented ───────────────────────
def test_the_perfect_bar_matches_the_strictest_source_threshold():
    assert "DISCOVERY_PERFECT_CONFIDENCE = 0.9" in _SYNC
    for src in ("beatport.py", "listenbrainz.py"):
        text = (_ROOT / "core" / "discovery" / src).read_text(encoding="utf-8")
        assert "min_confidence = 0.9" in text, src


# ── classification rules ─────────────────────────────────────────────────────
def test_a_wing_it_stub_is_never_called_a_match():
    """Wing It is a fabricated stub with confidence 0 — the single most
    important row to be able to isolate."""
    block = _classifier()
    assert "result.wing_it_fallback || cls === 'wing-it'" in block
    # ...and it is decided BEFORE the found/confidence branch, or a stub
    # carrying spotify_data would be scored as a real match.
    assert block.index("wing_it_fallback") < block.index("DISCOVERY_PERFECT_CONFIDENCE")


def test_a_missing_confidence_is_not_reported_as_low():
    """Some transformed result shapes drop `confidence` entirely. Calling those
    'low confidence' would invent a doubt the data cannot support."""
    block = _classifier()
    assert "conf === undefined || conf === null" in block
    assert re.search(r"conf === undefined \|\| conf === null\)\s*return 'perfect'", block)


def test_a_manual_match_outranks_its_score():
    """A human already decided; a stale numeric score must not demote it."""
    assert "if (result.manual_match) return 'perfect'" in _classifier()


def test_errors_are_their_own_bucket():
    """An error is not 'not found' — nothing was concluded, so it needs a
    different action from the user."""
    assert "cls === 'error'" in _classifier()


# ── the filter is view-only ──────────────────────────────────────────────────
def test_filtering_narrows_display_only():
    """Hiding a bucket must never change what a later download acts on."""
    block = _SYNC.split("const _bucket = _discoveryFilters[urlHash]", 1)[1][:700]
    assert "_visible" in block
    # the filter is applied to the RENDER list, not to state
    assert "state.discoveryResults =" not in block
    assert "discovery_results =" not in block


def test_an_empty_bucket_says_so_rather_than_rendering_nothing():
    """A blank table reads as "discovery broke"."""
    assert "No tracks in this group" in _SYNC


def test_empty_buckets_are_not_offered():
    """A run with no errors should not advertise an Error filter that shows
    nothing when clicked."""
    bar = _SYNC.split("function buildDiscoveryFilterBarHtml", 1)[1][:900]
    assert "b.id === 'all' || counts[b.id] > 0" in bar


def test_the_bar_is_hidden_before_discovery_has_answers():
    bar = _SYNC.split("function buildDiscoveryFilterBarHtml", 1)[1][:900]
    assert "if (!results.length) return ''" in bar


# ── wiring ───────────────────────────────────────────────────────────────────
def test_the_selection_survives_a_poller_refresh():
    """Discovery streams in and re-renders the table repeatedly; keeping the
    active bucket on module state rather than on `state` stops a refresh from
    silently resetting the user's view mid-triage."""
    assert "const _discoveryFilters = {}" in _SYNC
    refresh = _SYNC.split("function refreshYouTubeDiscoveryModalTable", 1)[1][:1400]
    assert "buildDiscoveryFilterBarHtml(state, urlHash)" in refresh


def test_the_handler_is_exposed_for_the_inline_onclick():
    assert "window.setDiscoveryFilter = setDiscoveryFilter" in _SYNC


def test_the_bar_is_styled():
    for cls in ("discovery-filter-bar", "discovery-filter-chip",
                "discovery-filter-count", "discovery-filter-empty"):
        assert f".{cls}" in _CSS, cls
