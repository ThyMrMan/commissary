"""Season packs you can actually switch on, and a per-show say in it.

The grouping, the grab and the per-episode import fan-out have existed since
season-pack support landed. What never existed was a way to turn any of it on:
``video.season_packs`` and ``video.season_pack_min_episodes`` were the only two
``video.*`` config keys in the tree, read by the drain and written by nothing —
no settings UI, no default, no API route. The feature shipped switched off with
no switch.

Three things here:

1. The settings have ONE reader (``core.video.download_config``), which is also
   what the settings page loads and saves. The drain used to look the keys up
   itself; a setting whose reader and writer disagree about where it lives is
   exactly how the download-source order came to save and then revert (2.1.2).

2. A "season packs only" mode, for when a season assembled from a dozen
   unrelated releases at different qualities is worse than waiting. It binds
   ONLY on a season that has finished airing — see the airing tests below,
   which are the whole reason that mode is safe to offer.

3. A per-show override that beats the global in BOTH directions, so "always get
   this one as packs" is expressible while packs are off globally.
"""

from __future__ import annotations

import os
import tempfile

import pytest

from core.automation.handlers.video_process_wishlist import (
    _pack_mode_resolver,
    _season_pack_settings,
    _try_season_packs,
    season_pack_groups,
)
from core.video.download_config import (
    normalize_min_episodes,
    normalize_season_pack_mode,
    season_pack_settings,
)


def cfg(**values):
    return lambda key, default=None: values.get(key, default)


def _ep(show="500", season=1, episode=1, **over):
    it = {"show_tmdb_id": show, "show_title": "Frieren", "season_number": season,
          "episode_number": episode}
    it.update(over)
    return it


# ── the settings, normalized in one place ───────────────────────────────────

class TestTheSettings:
    def test_the_shipped_defaults(self):
        """Off, four episodes, fall back to singles. An install that updates
        must not start spending disk on packs it never asked for."""
        assert season_pack_settings(cfg()) == {
            "season_packs": False, "season_pack_min_episodes": 4,
            "season_pack_mode": "prefer"}

    @pytest.mark.parametrize("value", ["only", "ONLY", " only "])
    def test_only_is_recognised(self, value):
        assert normalize_season_pack_mode(value) == "only"

    @pytest.mark.parametrize("value", ["", None, "banana", 7, "never"])
    def test_an_unknown_mode_falls_back_to_the_one_that_still_downloads(self, value):
        """'never' is a PER-SHOW value, not a global one — a global that stopped
        acquiring every show would be a footgun with no visible cause."""
        assert normalize_season_pack_mode(value) == "prefer"

    @pytest.mark.parametrize("value,expect", [(0, 2), (1, 2), (2, 2), (5, 5), (999, 200)])
    def test_the_threshold_is_floored_at_two(self, value, expect):
        """A 'pack' covering one episode is an episode; the grouping refuses it
        anyway, so letting the number go below 2 only misleads."""
        assert normalize_min_episodes(value) == expect

    @pytest.mark.parametrize("value", [None, "", "lots", [4]])
    def test_a_junk_threshold_reads_as_the_default(self, value):
        assert normalize_min_episodes(value) == 4

    def test_the_drain_sees_what_the_settings_page_saved(self):
        """Not two lookups of the same key — one implementation.

        Asserting the KEY NAMES was not enough: a drain that had gone back to
        reading `video.season_packs` itself and hardcoding the other two still
        returned all three names. So write through the settings-page path and
        require the drain to report the values back."""
        from config.settings import config_manager
        keys = ["video.season_packs", "video.season_pack_min_episodes",
                "video.season_pack_mode"]
        saved = {k: config_manager.get(k) for k in keys}
        try:
            config_manager.set("video.season_packs", True)
            config_manager.set("video.season_pack_min_episodes", 7)
            config_manager.set("video.season_pack_mode", "only")
            assert _season_pack_settings() == {
                "season_packs": True, "season_pack_min_episodes": 7,
                "season_pack_mode": "only"}
        finally:
            for k, v in saved.items():
                config_manager.set(k, v)


# ── the per-show resolver ───────────────────────────────────────────────────

def _resolver(overrides, **settings):
    base = {"season_packs": False, "season_pack_min_episodes": 4,
            "season_pack_mode": "prefer"}
    base.update(settings)

    class _DB:
        def all_season_pack_overrides(self):
            return overrides

    import core.automation.handlers.video_process_wishlist as mod
    real = mod._pack_mode_resolver

    # The resolver reads its overrides through api.video.get_video_db; hand it a
    # double by patching that import target for the duration.
    import api.video as apivideo
    prev = apivideo.get_video_db
    apivideo.get_video_db = lambda: _DB()
    try:
        return real(base)
    finally:
        apivideo.get_video_db = prev


class TestWhoDecidesPerShow:
    def test_with_packs_off_and_no_override_nothing_packs(self):
        assert _resolver({})(_ep()) == "never"

    def test_with_packs_on_a_show_follows_the_global_mode(self):
        r = _resolver({}, season_packs=True, season_pack_mode="only")
        assert r(_ep()) == "only"

    def test_a_show_can_opt_IN_while_packs_are_off_globally(self):
        """The case someone opens the panel for. An override that could only
        subtract would make 'always get this one as packs' unexpressible."""
        assert _resolver({500: "only"})(_ep(show=500)) == "only"

    def test_a_show_can_opt_OUT_while_packs_are_on_globally(self):
        r = _resolver({500: "never"}, season_packs=True)
        assert r(_ep(show=500)) == "never"

    def test_only_the_named_show_is_affected(self):
        r = _resolver({500: "only"})
        assert r(_ep(show=500)) == "only"
        assert r(_ep(show=999)) == "never"

    def test_a_junk_stored_override_falls_back_to_the_global(self):
        r = _resolver({500: "sometimes"}, season_packs=True, season_pack_mode="prefer")
        assert r(_ep(show=500)) == "prefer"

    def test_a_show_with_no_tmdb_id_still_resolves(self):
        assert _resolver({}, season_packs=True)(_ep(show=None)) == "prefer"


# ── grouping honours the per-show decision ──────────────────────────────────

class TestGrouping:
    def test_a_never_show_is_not_grouped_at_all(self):
        items = [_ep(episode=n) for n in range(1, 6)]
        assert season_pack_groups(items, min_episodes=4,
                                  mode_for=lambda it: "never") == []

    def test_the_pack_carries_the_mode_it_was_grouped_under(self):
        items = [_ep(episode=n) for n in range(1, 6)]
        pack = season_pack_groups(items, min_episodes=4,
                                  mode_for=lambda it: "only")[0]
        assert pack["_pack_mode"] == "only"

    def test_without_a_resolver_grouping_behaves_exactly_as_before(self):
        """The existing callers and tests pass no mode_for; they must keep the
        old behaviour rather than acquiring an implicit new one."""
        items = [_ep(episode=n) for n in range(1, 6)]
        pack = season_pack_groups(items, min_episodes=4)[0]
        assert pack["_pack_size"] == 5 and pack["_pack_mode"] == "prefer"

    def test_one_show_never_does_not_suppress_another(self):
        items = ([_ep(show="a", episode=n) for n in range(1, 6)]
                 + [_ep(show="b", episode=n) for n in range(1, 6)])
        groups = season_pack_groups(
            items, min_episodes=4,
            mode_for=lambda it: "never" if it["show_tmdb_id"] == "a" else "prefer")
        assert [g["show_tmdb_id"] for g in groups] == ["b"]


# ── what happens when no pack is found ──────────────────────────────────────

class _Deps:
    def __init__(self):
        self.lines = []

    def update_progress(self, _aid, **kw):
        if kw.get("log_line"):
            self.lines.append(kw["log_line"])


def _run(todo, *, mode, found=None, aired=True):
    """Drive _try_season_packs with a search that finds `found` and a season
    whose airing state is `aired`."""
    import core.automation.handlers.video_process_wishlist as mod
    deps = _Deps()
    prev_air = mod._season_has_finished_airing
    mod._season_has_finished_airing = lambda item: aired
    try:
        return _try_season_packs(
            todo, root=None,
            search=lambda item, kind: (found or [], None),
            enqueue=lambda *a, **k: True,
            deps=deps, automation_id=1,
            settings={"season_packs": True, "season_pack_min_episodes": 4,
                      "season_pack_mode": mode},
            mode_for=lambda it: mode), deps
    finally:
        mod._season_has_finished_airing = prev_air


class TestWhenThereIsNoPack:
    def test_prefer_falls_through_to_per_episode(self):
        todo = [_ep(episode=n) for n in range(1, 6)]
        (remaining, grabs), _deps = _run(todo, mode="prefer", found=[])
        assert grabs == 0
        assert len(remaining) == 5, "prefer must not hold episodes back"

    def test_only_holds_a_finished_season_back(self):
        todo = [_ep(episode=n) for n in range(1, 6)]
        (remaining, grabs), deps = _run(todo, mode="only", found=[], aired=True)
        assert grabs == 0 and remaining == []
        assert any("holding 5 episode(s)" in l for l in deps.lines), deps.lines

    def test_only_does_NOT_hold_a_season_that_is_still_airing(self):
        """The trap this mode would otherwise be. A season still going out
        weekly has no complete pack to find, so pack-or-nothing would skip its
        episodes every tick and the show would silently stop arriving."""
        todo = [_ep(episode=n) for n in range(1, 6)]
        (remaining, grabs), deps = _run(todo, mode="only", found=[], aired=False)
        assert grabs == 0
        assert len(remaining) == 5
        assert not any("holding" in l for l in deps.lines), deps.lines

    def test_a_season_below_the_threshold_is_never_held(self):
        """It was never a pack candidate, so 'packs only' has no opinion on it —
        holding it back would strand episodes no pack was ever sought for."""
        todo = [_ep(episode=n) for n in range(1, 3)]
        (remaining, grabs), _deps = _run(todo, mode="only", found=[], aired=True)
        assert len(remaining) == 2 and grabs == 0

    def test_a_found_pack_claims_its_episodes_in_either_mode(self):
        for mode in ("prefer", "only"):
            todo = [_ep(episode=n) for n in range(1, 6)]
            (remaining, grabs), deps = _run(
                todo, mode=mode,
                # `accepted` is what pick_best filters on — a candidate the
                # quality gate rejected is not a pick.
                found=[{"title": "Frieren.S01.1080p", "accepted": True}])
            assert grabs == 1, mode
            assert remaining == [], mode
            assert any("Grabbed the" in l for l in deps.lines), (mode, deps.lines)

    def test_episodes_of_an_untouched_show_survive_a_hold(self):
        """Holding one season must not drop another show's work from the tick."""
        todo = ([_ep(show="a", episode=n) for n in range(1, 6)]
                + [_ep(show="b", episode=1)])
        import core.automation.handlers.video_process_wishlist as mod
        deps = _Deps()
        prev = mod._season_has_finished_airing
        mod._season_has_finished_airing = lambda item: True
        try:
            remaining, _ = _try_season_packs(
                todo, root=None, search=lambda i, k: ([], None),
                enqueue=lambda *a, **k: True, deps=deps, automation_id=1,
                settings={"season_packs": True, "season_pack_min_episodes": 4,
                          "season_pack_mode": "only"},
                mode_for=lambda it: "only")
        finally:
            mod._season_has_finished_airing = prev
        assert [it["show_tmdb_id"] for it in remaining] == ["b"]


# ── the airing check, against a real database ───────────────────────────────

@pytest.fixture(scope="module")
def vdb():
    tmp = tempfile.mkdtemp(prefix="soulsync-testdb-sp-")
    os.environ.setdefault("DATABASE_PATH", os.path.join(tmp, "x.db"))
    from database.video_database import VideoDatabase
    return VideoDatabase(os.path.join(tmp, "video.db"))


def _seed_show(db, tmdb_id, air_dates):
    conn = db._get_connection()
    try:
        cur = conn.execute("INSERT INTO shows (tmdb_id, title) VALUES (?,?)",
                           (tmdb_id, "Test %s" % tmdb_id))
        show_id = cur.lastrowid
        cur = conn.execute(
            "INSERT INTO seasons (show_id, season_number) VALUES (?,1)", (show_id,))
        season_id = cur.lastrowid
        for n, air in enumerate(air_dates, start=1):
            conn.execute(
                "INSERT INTO episodes (show_id, season_id, season_number, episode_number, air_date) "
                "VALUES (?,?,?,?,?)", (show_id, season_id, 1, n, air))
        conn.commit()
    finally:
        conn.close()


class TestHasTheSeasonFinishedAiring:
    def test_a_season_entirely_in_the_past_has_finished(self, vdb):
        _seed_show(vdb, 9001, ["2020-01-01", "2020-01-08", "2020-01-15"])
        assert vdb.season_fully_aired(9001, 1) is True

    def test_a_season_with_an_unaired_episode_has_not(self, vdb):
        _seed_show(vdb, 9002, ["2020-01-01", "2999-01-01"])
        assert vdb.season_fully_aired(9002, 1) is False

    def test_a_season_we_have_no_episodes_for_is_unknown(self, vdb):
        assert vdb.season_fully_aired(9003, 1) is None

    def test_an_undated_episode_makes_it_unknown_not_finished(self, vdb):
        """Not-provably-finished must not read as finished: the caller turns
        'finished' into 'hold these episodes back', and holding on a guess is
        how a show stops arriving for reasons nobody can see."""
        _seed_show(vdb, 9004, ["2020-01-01", None])
        assert vdb.season_fully_aired(9004, 1) is None

    def test_a_garbage_id_is_unknown_rather_than_an_error(self, vdb):
        assert vdb.season_fully_aired("nope", 1) is None
        assert vdb.season_fully_aired(9001, None) is None


# ── the per-show override, stored ───────────────────────────────────────────

class TestTheStoredOverride:
    @pytest.mark.parametrize("mode", ["prefer", "only", "never"])
    def test_it_round_trips(self, vdb, mode):
        assert vdb.set_season_pack_mode_override(7100, mode) == mode
        assert vdb.season_pack_mode_for_tmdb(7100) == mode

    def test_an_empty_value_clears_it(self, vdb):
        vdb.set_season_pack_mode_override(7101, "only")
        assert vdb.set_season_pack_mode_override(7101, "") == ""
        assert vdb.season_pack_mode_for_tmdb(7101) is None

    def test_an_unknown_mode_is_refused_rather_than_stored(self, vdb):
        vdb.set_season_pack_mode_override(7102, "only")
        assert vdb.set_season_pack_mode_override(7102, "occasionally") is None
        assert vdb.season_pack_mode_for_tmdb(7102) == "only", "a bad write must not clobber"

    def test_setting_it_does_not_disturb_the_series_type_beside_it(self, vdb):
        """Both live on the same overrides row; an upsert that named only its
        own column would blank the other."""
        vdb.set_series_type_override(7103, "anime")
        vdb.set_season_pack_mode_override(7103, "only")
        assert vdb.series_type_for_tmdb(7103) == "anime"
        vdb.set_series_type_override(7103, "daily")
        assert vdb.season_pack_mode_for_tmdb(7103) == "only"

    def test_the_bulk_read_returns_only_shows_that_set_one(self, vdb):
        vdb.set_season_pack_mode_override(7200, "only")
        vdb.set_season_pack_mode_override(7201, "")        # cleared
        all_modes = vdb.all_season_pack_overrides()
        assert all_modes.get(7200) == "only"
        assert 7201 not in all_modes, "a cleared override must not read as a preference"
