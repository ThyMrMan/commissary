"""A video Library's trackers limit the Prowlarr searches for its titles.

The live Anime library searches indexers 1,7,9 and Anime-Movies only 1; the
general libraries use their own set. A title that belongs to no library is
not limited (it does not inherit the primary library's trackers)."""

import sqlite3

import pytest

from core.commissary import video_libraries as vl
from database.video_database import VideoDatabase


@pytest.fixture()
def db(tmp_path, monkeypatch):
    import api.video as videoapi
    db = VideoDatabase(database_path=str(tmp_path / "video_library.db"))
    videoapi._video_db = db
    vl.save_libraries(db, "plex",
                      movies=[{"server_title": "Movies", "path": "/m", "preferred_indexer_ids": "3,7"}],
                      tv=[{"server_title": "TV Shows", "path": "/t", "preferred_indexer_ids": "3,7,12"},
                          {"server_title": "Anime", "path": "/a", "preferred_indexer_ids": "1,7,9"}])
    ids = {r["server_title"]: r["id"] for k, v in vl.list_libraries(db, "plex").items() for r in v}
    conn = sqlite3.connect(str(db.database_path))
    conn.execute("INSERT INTO shows (title, tmdb_id, server_source, root_folder_id) "
                 "VALUES ('Bleach', 30984, 'plex', ?)", (ids["Anime"],))
    conn.commit()
    conn.close()
    yield db
    videoapi._video_db = None


class _Client:
    def __init__(self):
        self.ids = []

    def is_configured(self):
        return True

    def _search_sync(self, q, cats, ids, limit, **kw):
        self.ids.append(ids)
        return []


@pytest.fixture()
def client(monkeypatch):
    import core.video.prowlarr_search as ps
    c = _Client()
    monkeypatch.setattr(ps, "_client", lambda: c)
    monkeypatch.setattr(ps, "_indexer_ids", lambda: [])
    return c


def test_a_library_limits_the_search_to_its_trackers(client):
    from core.video.prowlarr_search import prowlarr_search
    prowlarr_search("episode", "Bleach", season=1, episode=1, indexer_ids=[1, 7, 9])
    assert client.ids and all(ids == [1, 7, 9] for ids in client.ids)


def test_the_global_allowlist_still_applies(client, monkeypatch):
    import core.video.prowlarr_search as ps
    from core.video.prowlarr_search import prowlarr_search
    monkeypatch.setattr(ps, "_indexer_ids", lambda: [7, 9, 12])
    prowlarr_search("episode", "Bleach", season=1, episode=1, indexer_ids=[1, 7, 9])
    assert all(ids == [7, 9] for ids in client.ids)


def test_no_overlap_with_the_allowlist_says_so(client, monkeypatch):
    import core.video.prowlarr_search as ps
    from core.video.prowlarr_search import prowlarr_search
    monkeypatch.setattr(ps, "_indexer_ids", lambda: [12])
    res = prowlarr_search("episode", "Bleach", season=1, episode=1, indexer_ids=[1, 7])
    assert res["hits"] == [] and "trackers" in res["error"]
    assert client.ids == []


def test_the_drain_searches_an_anime_show_on_animes_trackers(db, monkeypatch):
    from core.automation.handlers import video_process_wishlist as vpw
    seen = {}

    def fake(*a, **kw):
        seen.update(kw)
        return {"configured": True, "hits": []}

    monkeypatch.setattr("core.video.prowlarr_search.prowlarr_search", fake)
    monkeypatch.setattr(vpw, "_extto_hits_for_context", lambda ctx: ([], None), raising=False)
    item = {"kind": "episode", "tmdb_id": 30984, "show_tmdb_id": 30984, "show_title": "Bleach",
            "season_number": 1, "episode_number": 1}
    vpw._search_one_source("usenet", item, "episode")
    assert seen.get("indexer_ids") == [1, 7, 9]


def test_an_unassigned_title_is_not_limited_to_the_primarys_trackers(db, monkeypatch):
    from core.automation.handlers import video_process_wishlist as vpw
    seen = {}
    monkeypatch.setattr("core.video.prowlarr_search.prowlarr_search",
                        lambda *a, **kw: seen.update(kw) or {"configured": True, "hits": []})
    vpw._search_one_source("usenet", {"kind": "movie", "tmdb_id": 999, "title": "New"}, "movie")
    assert "indexer_ids" not in seen


def test_a_manual_search_uses_the_titles_library(db, monkeypatch):
    from api.video import downloads
    seen = {}
    monkeypatch.setattr("core.video.prowlarr_search.prowlarr_search",
                        lambda *a, **kw: seen.update(kw) or {"configured": True, "hits": []})
    downloads._torrent_lane_hits({"media_id": 30984, "media_source": "tmdb"}, "episode",
                                 "Bleach", 1, 1, "usenet")
    assert seen.get("indexer_ids") == [1, 7, 9]


def test_the_rss_pool_keeps_only_the_librarys_trackers(db):
    pool = [{"indexer_id": 1, "title": "a"}, {"indexer_id": 3, "title": "b"},
            {"indexer_id": 9, "title": "c"}]
    ids = vl.search_indexer_ids(db, "episode", item={"tmdb_id": 30984})
    assert [h["title"] for h in vl.filter_hits(pool, ids)] == ["a", "c"]
    assert vl.filter_hits(pool, None) == pool


def test_the_rss_ranker_filters_its_pool_by_library(db, monkeypatch):
    from core.video import rss_sync
    seen = {}
    monkeypatch.setattr("api.video.downloads._evaluate_hits",
                        lambda pool, *a, **k: seen.setdefault("pool", pool) and [])
    item = {"kind": "episode", "tmdb_id": 30984, "show_tmdb_id": 30984, "show_title": "Bleach",
            "season_number": 1, "episode_number": 1}
    rss_sync._rank([{"indexer_id": 1, "title": "a"}, {"indexer_id": 3, "title": "b"}], item, "episode")
    assert [h["title"] for h in seen["pool"]] == ["a"]
