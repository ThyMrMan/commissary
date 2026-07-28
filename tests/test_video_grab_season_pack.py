"""Grab season fetches ONE pack, not N episodes.

It used to run the per-episode auto-grab once per missing episode: N searches and
N grabs for one season. That hammers the indexers, is slow, and routinely
assembles a season out of a dozen unrelated releases at different qualities and
from different groups.

A season pack is one release covering the whole season; the import already fans
it out per episode (run_season_import, shipped 1.8.0) exactly as the automation
does, so everything downstream — naming, upgrades, subtitles, seeding — is
unchanged.

Packs ONLY, deliberately. When no pack exists the button says so rather than
quietly reverting to per-episode grabbing: Auto on each row still does that, and
a control that does something other than what it says is worse than one that
declines. These are source guards — the repo has no JS runner.
"""

from __future__ import annotations

import pathlib

import pytest

_ROOT = pathlib.Path(__file__).resolve().parents[1]


def _js(rel):
    return (_ROOT / rel).read_text(encoding="utf-8")


GRAB = "webui/static/video/video-grab.js"
VIEW = "webui/static/video/video-download-view.js"
DETAIL = "webui/static/video/video-detail.js"


def _func(src: str, name: str) -> str:
    """The body of a top-level function, to the next one."""
    start = src.index("function %s(" % name)
    nxt = src.find("\n    function ", start + 1)
    return src[start:nxt if nxt > 0 else len(src)]


# ── both Grab-season paths search for a pack ─────────────────────────────────
@pytest.mark.parametrize("rel,name", [(GRAB, "season"), (VIEW, "grabSeason")])
def test_the_search_scope_is_season(rel, name):
    body = _func(_js(rel), name)
    assert "scope: 'season'" in body


@pytest.mark.parametrize("rel,name", [(GRAB, "season"), (VIEW, "grabSeason")])
def test_it_no_longer_loops_over_episodes(rel, name):
    """The whole point: one search and one grab, not one per episode."""
    body = _func(_js(rel), name)
    assert "scope: 'episode'" not in body
    for looped in ("MAX = 3", "pump()", "autoGrabEpisode("):
        assert looped not in body, looped


def test_the_per_episode_helper_is_gone_rather_than_left_dangling():
    """Removing the last caller without removing the helper leaves a second,
    silently-diverging implementation for someone to wire back up."""
    assert "autoGrabEpisode" not in _js(VIEW)


# ── it must not silently do something else ───────────────────────────────────
@pytest.mark.parametrize("rel,name", [(GRAB, "season"), (VIEW, "grabSeason")])
def test_no_pack_is_reported_not_worked_around(rel, name):
    body = _func(_js(rel), name)
    assert "no season pack found" in body.lower()


def test_the_detail_page_message_matches_what_happened():
    """'Grabbing 12 of 12 episodes' would describe twelve downloads that are not
    happening — it is one pack."""
    body = _func(_js(DETAIL), "grabSeasonInline")
    assert "res.pack" in body
    assert "of ' + res.total" not in body


# ── the two pack shapes ──────────────────────────────────────────────────────
@pytest.mark.parametrize("rel,name", [(GRAB, "season"), (VIEW, "grabSeason")])
def test_a_soulseek_folder_goes_through_grab_pack(rel, name):
    """slskd packs are a FOLDER of files and are fanned out server-side; a
    torrent pack is a single release the monitor unpacks on completion."""
    body = _func(_js(rel), name)
    assert "files" in body
    assert ("grab-pack" in body) or ("_grabPack" in body)


def test_the_torrent_grab_carries_season_scope():
    """scope 'season' in search_ctx is what makes the download monitor treat the
    finished download as a pack (_is_pack → run_season_import). Without it the
    pack lands as one unrecognised file."""
    body = _func(_js(GRAB), "season")
    assert "scope: 'season'" in body
    assert "search_ctx" in body


def test_the_button_tooltip_describes_the_new_behaviour():
    view = _js(VIEW)
    assert "one at a time" not in view
    assert "season pack" in view.lower()


# ── the backend already understands a season-scoped grab ─────────────────────
def test_the_monitor_treats_a_season_scope_as_a_pack():
    from core.video.client_download import _is_pack
    assert _is_pack({"search_ctx": '{"scope": "season"}'}) is True
    assert _is_pack({"search_ctx": '{"scope": "series"}'}) is True
    assert _is_pack({"search_ctx": '{"scope": "episode"}'}) is False
    assert _is_pack({}) is False


def test_a_season_search_is_a_supported_scope():
    """prowlarr_search must build a real season query, not fall through to the
    movie branch."""
    from core.video.prowlarr_search import build_strategies
    strat = build_strategies("season", "Bleach", season=2, tvdb_id=74796)
    assert strat
    kinds = {s[0] for s in strat}
    assert "tvsearch" in kinds
    assert any(("season", 2) in s[2] for s in strat)
