"""RSS sync could not see a season pack, and could undercut one mid-flight.

Two gaps, both left over from season packs landing in the wishlist drain only:

1. Every wanted episode was ranked with ``scope='episode'``, so a
   ``Show.S01.1080p`` pack sitting in the very same feed matched nothing. The
   one acquisition path that reacts within minutes of a release being posted
   was structurally blind to the release shape that covers a whole season.

2. The pass checked only the per-episode active key. A season pack already
   downloading — from the drain, or from an earlier RSS pass — did not stop RSS
   grabbing episode 3 of that season on its own, leaving two claimants for one
   file.

Everything here is the drain's own seams (grouping, per-show mode, ranker,
enqueue), so the two paths cannot drift about what a pack is or which shows may
have one.
"""

from __future__ import annotations

import time

import pytest

import core.video.rss_sync as rss
from core.automation.handlers import video_process_wishlist as vpw
from database.video_database import VideoDatabase


@pytest.fixture()
def db(tmp_path, monkeypatch):
    import api.video as videoapi
    from core.video import download_events

    # Same hermetic guard as tests/test_video_rss_sync.py — a forwarder leaked
    # by an earlier file can spawn a concurrent pass that steals the run lock.
    download_events._reset_for_tests()
    deadline = time.monotonic() + 5.0
    while rss.is_running() and time.monotonic() < deadline:
        time.sleep(0.05)
    assert not rss.is_running(), "a leaked background rss_pass never finished"

    d = VideoDatabase(database_path=str(tmp_path / "video_library.db"))
    videoapi._video_db = d
    yield d
    videoapi._video_db = None


def _feed_hit(title, *, proto="torrent", size=4_000_000_000, seeders=50):
    return {"title": title, "size_bytes": size, "seeders": seeders, "peers": 5,
            "username": "indexer", "availability": seeders, "filename": title,
            "files": [], "file_count": 0, "folder_size_bytes": size,
            "download_url": "http://idx/dl/" + title.replace(" ", "."),
            "protocol": proto, "indexer_id": 1, "guid": "g-" + title}


@pytest.fixture()
def seams(monkeypatch):
    grabs = []
    monkeypatch.setattr(vpw, "_default_target_dir", lambda mt: "/media/" + mt)
    monkeypatch.setattr(vpw, "_default_active_keys", lambda mt: set())
    monkeypatch.setattr(vpw, "_default_enqueue",
                        lambda item, best, cands, mt, root: grabs.append(
                            {"item": item, "best": best, "cands": cands,
                             "media_type": mt, "root": root}) or True)
    return grabs


@pytest.fixture()
def packs_on(monkeypatch):
    """Season packs enabled globally, threshold 4, fall-back-to-episodes."""
    monkeypatch.setattr(vpw, "_season_pack_settings",
                        lambda: {"season_packs": True, "season_pack_min_episodes": 4,
                                 "season_pack_mode": "prefer"})
    monkeypatch.setattr(vpw, "_pack_mode_resolver", lambda s: (lambda it: s["season_pack_mode"]))


def _enable_torrent(db):
    from core.video.download_config import save
    save(db, {"download_mode": "torrent"})


def _wish_season(db, tmdb_id, title, episodes, season=1):
    db.add_episodes_to_wishlist(tmdb_id, title, [
        {"season_number": season, "episode_number": n, "air_date": "2026-07-0%d" % ((n % 9) + 1)}
        for n in episodes])


# ── the pack becomes visible ────────────────────────────────────────────────

def test_a_season_pack_in_the_feed_is_grabbed(db, seams, packs_on):
    """THE gap. The pack was in the feed the whole time and could not match,
    because every wanted episode was asked for at episode scope."""
    _enable_torrent(db)
    _wish_season(db, 500, "Severance", [1, 2, 3, 4, 5])
    out = rss.rss_pass(fetch=lambda: [_feed_hit("Severance S01 1080p WEB h264-NTb")])
    assert out["grabbed"] == 1
    assert len(seams) == 1
    assert seams[0]["item"]["_season_pack"] is True
    assert "S01" in seams[0]["best"]["title"]


def test_the_pack_claims_its_episodes_so_they_are_not_also_grabbed(db, seams, packs_on):
    """One grab for the season, not one plus five."""
    _enable_torrent(db)
    _wish_season(db, 500, "Severance", [1, 2, 3, 4, 5])
    feed = [_feed_hit("Severance S01 1080p WEB h264-NTb")] + [
        _feed_hit("Severance S01E0%d 1080p WEB h264-NTb" % n) for n in range(1, 6)]
    out = rss.rss_pass(fetch=lambda: feed)
    assert out["grabbed"] == 1, "the singles must not be grabbed alongside the pack"
    assert all(g["item"].get("_season_pack") for g in seams)


def test_below_the_threshold_no_pack_is_attempted(db, seams, packs_on):
    """Two missing episodes are not worth a whole season."""
    _enable_torrent(db)
    _wish_season(db, 500, "Severance", [1, 2])
    feed = [_feed_hit("Severance S01 1080p WEB h264-NTb"),
            _feed_hit("Severance S01E01 1080p WEB h264-NTb")]
    out = rss.rss_pass(fetch=lambda: feed)
    assert out["grabbed"] == 1
    assert not seams[0]["item"].get("_season_pack")


def test_with_packs_off_the_feed_behaves_exactly_as_before(db, seams, monkeypatch):
    """The default. Nothing about RSS changes for an install that has not opted
    in — the pack in the feed is ignored and the episodes are grabbed singly."""
    monkeypatch.setattr(vpw, "_season_pack_settings",
                        lambda: {"season_packs": False, "season_pack_min_episodes": 4,
                                 "season_pack_mode": "prefer"})
    monkeypatch.setattr(vpw, "_pack_mode_resolver", lambda s: (lambda it: "never"))
    _enable_torrent(db)
    _wish_season(db, 500, "Severance", [1, 2, 3, 4, 5])
    feed = [_feed_hit("Severance S01 1080p WEB h264-NTb")] + [
        _feed_hit("Severance S01E0%d 1080p WEB h264-NTb" % n) for n in range(1, 6)]
    out = rss.rss_pass(fetch=lambda: feed)
    assert out["grabbed"] == 5
    assert not any(g["item"].get("_season_pack") for g in seams)


def test_no_pack_in_the_feed_falls_through_to_episodes(db, seams, packs_on):
    """The feed is the last few hundred releases, not a search. A pack missing
    from it says nothing about whether one exists, so 'prefer' must not hold the
    episodes back the way the drain's own failed search does."""
    _enable_torrent(db)
    _wish_season(db, 500, "Severance", [1, 2, 3, 4, 5])
    feed = [_feed_hit("Severance S01E0%d 1080p WEB h264-NTb" % n) for n in range(1, 6)]
    out = rss.rss_pass(fetch=lambda: feed)
    assert out["grabbed"] == 5
    assert not any(g["item"].get("_season_pack") for g in seams)


# ── 'season packs only', and the airing guard that makes it safe ────────────

def test_packs_only_does_not_grab_singles_from_the_feed(db, seams, monkeypatch):
    _enable_torrent(db)
    monkeypatch.setattr(vpw, "_season_pack_settings",
                        lambda: {"season_packs": True, "season_pack_min_episodes": 4,
                                 "season_pack_mode": "only"})
    monkeypatch.setattr(vpw, "_pack_mode_resolver", lambda s: (lambda it: "only"))
    monkeypatch.setattr(vpw, "_season_has_finished_airing", lambda item: True)
    _wish_season(db, 500, "Severance", [1, 2, 3, 4, 5])
    feed = [_feed_hit("Severance S01E0%d 1080p WEB h264-NTb" % n) for n in range(1, 6)]
    out = rss.rss_pass(fetch=lambda: feed)
    assert out["grabbed"] == 0 and seams == []


def test_packs_only_still_grabs_singles_while_the_season_is_airing(db, seams, monkeypatch):
    """Same guard as the drain, so the two paths agree. A season going out
    weekly has no pack to wait for; refusing singles here would just make RSS
    stop working for currently-airing shows."""
    _enable_torrent(db)
    monkeypatch.setattr(vpw, "_season_pack_settings",
                        lambda: {"season_packs": True, "season_pack_min_episodes": 4,
                                 "season_pack_mode": "only"})
    monkeypatch.setattr(vpw, "_pack_mode_resolver", lambda s: (lambda it: "only"))
    monkeypatch.setattr(vpw, "_season_has_finished_airing", lambda item: False)
    _wish_season(db, 500, "Severance", [1, 2, 3, 4, 5])
    feed = [_feed_hit("Severance S01E0%d 1080p WEB h264-NTb" % n) for n in range(1, 6)]
    out = rss.rss_pass(fetch=lambda: feed)
    assert out["grabbed"] == 5


# ── not undercutting a pack that is already landing ────────────────────────

def test_an_episode_is_skipped_while_its_season_pack_downloads(db, seams, monkeypatch):
    """Only the per-item key was checked, so RSS would grab episode 3 on its own
    while the pack containing it was still landing — two claimants for one
    file."""
    monkeypatch.setattr(vpw, "_default_target_dir", lambda mt: "/media/" + mt)
    monkeypatch.setattr(vpw, "_default_active_keys",
                        lambda mt: {("season", "500", 1)})
    monkeypatch.setattr(vpw, "_default_enqueue",
                        lambda *a, **k: seams.append(a) or True)
    monkeypatch.setattr(vpw, "_season_pack_settings",
                        lambda: {"season_packs": False, "season_pack_min_episodes": 4,
                                 "season_pack_mode": "prefer"})
    monkeypatch.setattr(vpw, "_pack_mode_resolver", lambda s: (lambda it: "never"))
    _enable_torrent(db)
    _wish_season(db, 500, "Severance", [1, 2, 3])
    feed = [_feed_hit("Severance S01E0%d 1080p WEB h264-NTb" % n) for n in range(1, 4)]
    out = rss.rss_pass(fetch=lambda: feed)
    assert out["grabbed"] == 0 and seams == []


def test_a_different_season_is_unaffected_by_that_skip(db, seams, monkeypatch):
    monkeypatch.setattr(vpw, "_default_target_dir", lambda mt: "/media/" + mt)
    monkeypatch.setattr(vpw, "_default_active_keys",
                        lambda mt: {("season", "500", 1)})
    monkeypatch.setattr(vpw, "_season_pack_settings",
                        lambda: {"season_packs": False, "season_pack_min_episodes": 4,
                                 "season_pack_mode": "prefer"})
    monkeypatch.setattr(vpw, "_pack_mode_resolver", lambda s: (lambda it: "never"))
    _enable_torrent(db)
    _wish_season(db, 500, "Severance", [1], season=1)
    _wish_season(db, 500, "Severance", [1], season=2)
    feed = [_feed_hit("Severance S01E01 1080p WEB h264-NTb"),
            _feed_hit("Severance S02E01 1080p WEB h264-NTb")]
    out = rss.rss_pass(fetch=lambda: feed)
    assert out["grabbed"] == 1
    assert "S02E01" in seams[0]["best"]["title"]


def test_a_movie_is_untouched_by_the_season_logic(db, seams, packs_on):
    """The season pass is episode-only; a movie must not acquire a season key or
    be routed through the grouping."""
    _enable_torrent(db)
    db.add_movie_to_wishlist(1, "Heat", year=1995)
    out = rss.rss_pass(fetch=lambda: [_feed_hit("Heat 1995 1080p BluRay x264-GRP")])
    assert out["grabbed"] == 1 and seams[0]["media_type"] == "movie"
