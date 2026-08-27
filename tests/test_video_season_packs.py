"""Season packs: match, grab, and actually import.

The matching half already worked — parse_release flags is_season_pack,
evaluate_release has a 'season' scope, the search endpoints accept it and the UI
issues it. What was missing was everything after the download finished:
plan_import refused packs outright ("Season/complete packs need manual import"),
so a pack you grabbed downloaded and then stopped.

A pack is a FOLDER of episodes while the pipeline is built around one download →
one file. Rather than teach plan_import about packs — forking its naming,
upgrade, subtitle, recycle and seeding rules — the pack is fanned out: each
member is parsed on its OWN name and handed to the same single-file importer as
a synthetic per-episode download.
"""

from __future__ import annotations

import json
import os

import pytest

from core.automation.handlers.video_process_wishlist import (
    active_download_keys,
    item_key,
    search_context,
    season_key,
    season_pack_groups,
)
from core.video.importer import pack_members, run_season_import

PACK = "/dl/Frieren.S01.1080p.WEB-DL-VARYG"


def _files(*names):
    return [PACK + "/" + n for n in names]


def _pack_dl(**over):
    dl = {"kind": "show", "source": "torrent", "title": "Frieren", "target_dir": "/tv",
          "release_title": "Frieren.S01.1080p.WEB-DL-VARYG",
          "search_ctx": json.dumps({"scope": "season", "title": "Frieren", "season": 1})}
    dl.update(over)
    return dl


class _FS:
    def __init__(self):
        self.copied, self.removed = [], []

    def list_dir(self, p):
        return []

    def makedirs(self, p):
        pass

    def copy(self, s, d):
        self.copied.append(d)

    def move(self, s, d):
        self.copied.append(d)

    def remove(self, p):
        self.removed.append(p)


# ── what counts as an episode in a pack ──────────────────────────────────────
def test_only_real_episodes_are_members():
    files = _files(
        "Frieren.S01E01.1080p.WEB-DL.mkv",
        "Frieren.S01E02.1080p.WEB-DL.mkv",
        "Sample/Frieren.S01E01.sample.mkv",     # sample
        "Extras/Behind.The.Scenes.mkv",         # no episode number
        "Frieren.S01E01.1080p.WEB-DL.srt",      # not video
        "RARBG.txt",
    )
    got = pack_members(PACK, lambda d: files, size_of=lambda p: 1000)
    assert [(m["season"], m["episode"]) for m in got] == [(1, 1), (1, 2)]


def test_members_come_back_in_episode_order():
    files = _files("Show.S01E10.mkv", "Show.S01E02.mkv", "Show.S01E01.mkv")
    got = pack_members(PACK, lambda d: files)
    assert [m["episode"] for m in got] == [1, 2, 10]


def test_specials_are_kept():
    """S00 episodes are real files with a real destination — dropping them would
    silently lose them from a pack that ships them."""
    files = _files("Show.S00E01.Special.mkv", "Show.S01E01.mkv")
    assert [(m["season"], m["episode"]) for m in pack_members(PACK, lambda d: files)] == [(0, 1), (1, 1)]


# ── the fan-out ──────────────────────────────────────────────────────────────
def test_every_episode_is_imported_under_its_own_name():
    fs = _FS()
    files = _files("Frieren.S01E01.1080p.WEB-DL.mkv", "Frieren.S01E02.1080p.WEB-DL.mkv",
                   "Frieren.S01E03.1080p.WEB-DL.mkv")
    r = run_season_import(_pack_dl(), PACK, fs=fs, lister=lambda d: files)
    assert r["status"] == "completed"
    assert r["_pack_imported"] == 3 and r["_pack_total"] == 3
    names = [os.path.basename(p) for p in fs.copied]
    assert names == ["Frieren - S01E01 WEBDL-1080p.mkv",
                     "Frieren - S01E02 WEBDL-1080p.mkv",
                     "Frieren - S01E03 WEBDL-1080p.mkv"]


def test_a_member_is_identified_by_its_own_filename_not_the_packs():
    """The pack's name has no episode number in it. Parsing THAT for each member
    would give every episode the same (wrong) identity — they would all collide
    on one destination path."""
    fs = _FS()
    files = _files("Frieren.S01E01.1080p.WEB-DL.mkv", "Frieren.S01E02.1080p.WEB-DL.mkv")
    run_season_import(_pack_dl(), PACK, fs=fs, lister=lambda d: files)
    assert len({os.path.basename(p) for p in fs.copied}) == 2


def test_a_torrent_pack_is_never_deleted_from_the_download_dir():
    """Seeding: the per-member imports must inherit the single-file rule."""
    fs = _FS()
    files = _files("Frieren.S01E01.mkv", "Frieren.S01E02.mkv")
    run_season_import(_pack_dl(source="torrent"), PACK, fs=fs, lister=lambda d: files)
    assert fs.removed == []


# ── partial success is success ───────────────────────────────────────────────
def test_a_short_pack_still_imports_what_it_has():
    """A release advertised as S01 that ships 2 episodes has still done its job."""
    fs = _FS()
    files = _files("Frieren.S01E01.mkv", "Frieren.S01E02.mkv", "readme.nfo")
    r = run_season_import(_pack_dl(), PACK, fs=fs, lister=lambda d: files)
    assert r["status"] == "completed" and r["_pack_imported"] == 2


def test_one_bad_member_does_not_abort_the_pack():
    fs = _FS()
    calls = {"n": 0}
    real_copy = fs.copy

    def flaky(s, d):
        calls["n"] += 1
        if calls["n"] == 1:
            raise OSError("disk hiccup")
        real_copy(s, d)
    fs.copy = flaky
    files = _files("Frieren.S01E01.mkv", "Frieren.S01E02.mkv", "Frieren.S01E03.mkv")
    r = run_season_import(_pack_dl(), PACK, fs=fs, lister=lambda d: files)
    assert r["status"] == "completed"
    assert r["_pack_imported"] == 2 and len(r["_pack_failed"]) == 1


def test_a_pack_with_no_episodes_fails_and_keeps_the_source():
    """Nothing usable → import_failed pointing at the pack, so the Import page
    can pick it up manually like any other failed import."""
    fs = _FS()
    r = run_season_import(_pack_dl(), PACK, fs=fs, lister=lambda d: _files("readme.nfo", "cover.jpg"))
    assert r["status"] == "import_failed" and r["dest_path"] == PACK


# ── the automation ───────────────────────────────────────────────────────────
def _ep(show="500", season=1, episode=1, **over):
    it = {"show_tmdb_id": show, "show_title": "Frieren", "season_number": season,
          "episode_number": episode}
    it.update(over)
    return it


def test_a_season_with_enough_holes_becomes_one_pack():
    items = [_ep(episode=n) for n in range(1, 6)]
    groups = season_pack_groups(items, min_episodes=4)
    assert len(groups) == 1
    assert groups[0]["_season_pack"] is True and groups[0]["_pack_size"] == 5


def test_a_season_with_too_few_holes_is_left_to_per_episode():
    assert season_pack_groups([_ep(episode=1), _ep(episode=2)], min_episodes=4) == []


def test_upgrades_never_trigger_a_pack():
    """Wanting a better copy of some episodes must not pull the whole season —
    a pack would mostly re-download what you already have."""
    items = [_ep(episode=n, _min_rank=5) for n in range(1, 8)]
    assert season_pack_groups(items, min_episodes=4) == []


def test_specials_never_pack():
    assert season_pack_groups([_ep(season=0, episode=n) for n in range(1, 9)],
                              min_episodes=4) == []


def test_seasons_are_grouped_separately_per_show():
    items = ([_ep(show="500", episode=n) for n in range(1, 6)] +
             [_ep(show="600", episode=n) for n in range(1, 6)] +
             [_ep(show="500", season=2, episode=n) for n in range(1, 6)])
    assert len(season_pack_groups(items, min_episodes=4)) == 3


def test_the_pack_search_context_is_season_scoped():
    """No episode/air_date/absolute — those identify ONE episode and would make
    the pack fail its own scope gate."""
    pack = season_pack_groups([_ep(episode=n, air_date="2024-01-05") for n in range(1, 6)],
                              min_episodes=4)[0]
    ctx = search_context(pack, "episode")
    assert ctx["scope"] == "season" and ctx["season"] == 1
    assert "episode" not in ctx and "air_date" not in ctx and "absolute" not in ctx


def test_the_pack_inherits_the_seasons_routing():
    """Built from a representative member, so Library/profile/type come along —
    otherwise a pack for an Anime library would land in the standard TV folder."""
    items = [_ep(episode=n, root_folder_id=7, quality_profile_id=3, series_type="anime")
             for n in range(1, 6)]
    pack = season_pack_groups(items, min_episodes=4)[0]
    assert pack["root_folder_id"] == 7 and pack["quality_profile_id"] == 3
    assert search_context(pack, "episode")["series_type"] == "anime"


def test_a_pack_in_flight_claims_its_whole_season():
    """Without this the next tick grabs every episode individually while the pack
    that contains them is still downloading."""
    active = active_download_keys([{
        "kind": "episode", "media_id": "500",
        "search_ctx": json.dumps({"scope": "season", "season": 1})}])
    assert ("season", "500", 1) in active
    assert season_key(_ep(episode=4), "episode") in active
    # a different season is untouched
    assert season_key(_ep(season=2, episode=4), "episode") not in active


def test_a_single_episode_in_flight_still_claims_only_itself():
    active = active_download_keys([{
        "kind": "episode", "media_id": "500",
        "search_ctx": json.dumps({"scope": "episode", "season": 1, "episode": 3})}])
    assert item_key(_ep(episode=3), "episode") in active
    assert item_key(_ep(episode=4), "episode") not in active
    assert season_key(_ep(episode=4), "episode") not in active


def test_season_packs_are_off_by_default():
    """One pack can be tens of GB and the drain is unattended — an existing
    install must not start spending disk because it updated.

    Now read through core.video.download_config, which is also what the settings
    page reads and writes: the drain used to look the key up itself, and a
    setting whose reader and writer disagree is exactly how the download-source
    order came to save and then revert (2.1.2)."""
    from core.automation.handlers.video_process_wishlist import _season_pack_settings
    assert _season_pack_settings()["season_packs"] is False


def test_a_pack_grabbed_from_the_ui_also_claims_its_season():
    """The interactive modal stores kind='season' while the drain's own packs are
    kind='episode'. Keying off kind alone would miss one of them and let the
    drain re-grab every episode of a season already downloading."""
    active = active_download_keys([{
        "kind": "season", "media_id": "500",
        "search_ctx": json.dumps({"scope": "season", "season": 1})}])
    assert ("season", "500", 1) in active
    assert season_key(_ep(episode=7), "episode") in active


def test_a_series_pack_claims_the_season_it_names():
    active = active_download_keys([{
        "kind": "show", "media_id": "500",
        "search_ctx": json.dumps({"scope": "series", "season": 2})}])
    assert ("season", "500", 2) in active
