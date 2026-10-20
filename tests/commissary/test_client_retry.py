"""After a torrent fails: don't grab it again, grab the next one now. And TMDB paging."""

from __future__ import annotations

import json

import pytest

from core.commissary import client_retry as cr
from core.commissary import tmdb_search
from database.video_database import VideoDatabase


@pytest.fixture()
def db(tmp_path):
    import api.video as videoapi
    d = VideoDatabase(database_path=str(tmp_path / "video_library.db"))
    videoapi._video_db = d
    yield d
    videoapi._video_db = None


def _dl(**kw):
    row = {"id": 5, "kind": "show", "source": "torrent", "media_id": 9, "media_source": "tmdb",
           "title": "Silo", "release_title": "Silo.S02E03.1080p.WEB-NTb",
           "search_ctx": json.dumps({"scope": "episode", "season": 2, "episode": 3})}
    row.update(kw)
    return row


# ── remembering ─────────────────────────────────────────────────────────────

def test_a_failed_release_is_remembered_for_its_episode_only(db):
    assert cr.record(db, _dl()) == ("episode", 9, 2, 3)
    assert cr.recent(db, "episode", 9, 2, 3) == {"silo.s02e03.1080p.web-ntb"}
    assert cr.recent(db, "episode", 9, 2, 4) == set()


def test_searches_mark_it_rejected_with_the_reason(db):
    cr.record(db, _dl())
    item = {"show_tmdb_id": 9, "season_number": 2, "episode_number": 3}
    cands = [{"title": "Silo.S02E03.1080p.WEB-NTb", "accepted": True},
             {"title": "Silo.S02E03.720p.WEB-NTb", "accepted": True}]
    out = cr.mark_failed(item, "episode", cands)
    assert [c["accepted"] for c in out] == [False, True]
    assert out[0]["rejected"] == cr.REJECT


def test_it_is_forgotten_after_a_week(db):
    cr.record(db, _dl())
    conn = db._get_connection()
    conn.execute("UPDATE commissary_failed_releases SET failed_at = datetime('now', '-8 days')")
    conn.commit()
    conn.close()
    assert cr.recent(db, "episode", 9, 2, 3) == set()


# ── searching again ─────────────────────────────────────────────────────────

def test_a_torrent_failure_searches_again_until_the_daily_cap(db, monkeypatch):
    kicked = []
    monkeypatch.setattr(cr, "search_again", lambda *a, **k: kicked.append(a[1:]) or 1)
    for i in range(cr.MAX_RETRIES + 2):
        cr.after_failure(db, _dl(release_title="Silo.S02E03.v%d" % i))
    assert len(kicked) == cr.MAX_RETRIES and kicked[0] == ("episode", 9, 2, 3)


def test_soulseek_rows_are_upstreams_business(db, monkeypatch):
    monkeypatch.setattr(cr, "search_again", lambda *a, **k: pytest.fail("not for soulseek"))
    assert cr.after_failure(db, _dl(source="soulseek")) is False


def test_the_monitor_hands_a_final_torrent_failure_over(db, monkeypatch):
    from core.video import download_monitor as dm
    kicked = []
    monkeypatch.setattr(cr, "search_again", lambda *a, **k: kicked.append(a[1:]) or 1)
    dl_id = db.add_video_download({"kind": "show", "title": "Silo", "source": "torrent",
                                   "release_title": "Silo.S02E03.1080p.WEB-NTb", "media_id": 9,
                                   "media_source": "tmdb", "status": "downloading",
                                   "search_ctx": json.dumps({"scope": "episode", "season": 2,
                                                             "episode": 3})})
    dm._fail_or_retry(db, db.get_video_download(dl_id), "Stalled: no progress for 30 minutes")
    assert db.get_video_download(dl_id)["status"] == "failed"
    assert kicked == [("episode", 9, 2, 3)]
    assert cr.recent(db, "episode", 9, 2, 3) == {"silo.s02e03.1080p.web-ntb"}


def test_a_failure_that_found_the_file_is_not_retried(db, monkeypatch):
    """allow_retry=False: the download worked, the file couldn't be located."""
    from core.video import download_monitor as dm
    monkeypatch.setattr(cr, "search_again", lambda *a, **k: pytest.fail("no retry"))
    dl_id = db.add_video_download({"kind": "movie", "title": "Heat", "source": "torrent",
                                   "media_id": 949, "media_source": "tmdb", "status": "downloading",
                                   "release_title": "Heat.1995", "search_ctx": "{}"})
    dm._fail_or_retry(db, db.get_video_download(dl_id), "not found", allow_retry=False)
    assert cr.recent(db, "movie", 949) == set()


def test_the_retry_searches_as_the_background_drain_not_as_a_click(db, monkeypatch):
    """A click allows an equal-quality replacement; an automatic retry must not."""
    from core.automation.handlers import video_process_wishlist as vpw
    db.add_movie_to_wishlist(949, "Heat", year=1995)
    seen, grabbed = [], []
    monkeypatch.setattr(vpw, "_default_target_dir", lambda mt: "/movies")
    monkeypatch.setattr(vpw, "_default_active_keys", lambda mt: set())
    monkeypatch.setattr(vpw, "_default_search", lambda it, mt: seen.append(it) or (
        [{"title": "Heat.1995.1080p", "accepted": True, "source": "torrent"}], None))
    monkeypatch.setattr(vpw, "_default_enqueue", lambda it, best, c, mt, t: grabbed.append(best) or True)
    assert cr.search_again(db, "movie", 949, background=False) == 1
    assert seen and "_user_initiated" not in seen[0]
    assert grabbed[0]["title"] == "Heat.1995.1080p"


def test_the_drains_search_marks_the_failed_release(db, monkeypatch):
    from core.automation.handlers import video_process_wishlist as vpw
    cr.record(db, _dl())
    hit = {"title": "Silo.S02E03.1080p.WEB-NTb", "size_bytes": 1_500_000_000, "protocol": "torrent",
           "seeders": 50, "download_url": "magnet:?x", "indexer_id": 1}
    monkeypatch.setattr("core.video.prowlarr_search.prowlarr_search",
                        lambda *a, **k: {"configured": True, "hits": [hit]})
    monkeypatch.setattr(vpw, "_extto_hits_for_context", lambda ctx: ([], None))
    item = {"show_tmdb_id": 9, "show_title": "Silo", "season_number": 2, "episode_number": 3,
            "air_date": "2025-01-01"}
    cands, _err = vpw._search_one_source("torrent", item, "episode")
    mine = [c for c in cands if c.get("title") == hit["title"]]
    assert mine and mine[0]["accepted"] is False and mine[0]["rejected"] == cr.REJECT


def test_rss_marks_it_too(db, monkeypatch):
    import core.video.rss_sync as rss
    from core.automation.handlers import video_process_wishlist as vpw
    from core.video.download_config import save
    save(db, {"download_mode": "torrent"})
    db.add_episodes_to_wishlist(9, "Silo", [{"season_number": 2, "episode_number": 3,
                                             "air_date": "2025-01-01"}])
    cr.record(db, _dl())
    grabs = []
    monkeypatch.setattr(vpw, "_default_target_dir", lambda mt: "/tv")
    monkeypatch.setattr(vpw, "_default_active_keys", lambda mt: set())
    monkeypatch.setattr(vpw, "_default_enqueue", lambda it, best, c, mt, root: grabs.append(best) or True)
    feed = [{"title": t, "size_bytes": 1_500_000_000, "seeders": 50, "peers": 5, "username": "idx",
             "availability": 50, "filename": t, "files": [], "file_count": 0,
             "folder_size_bytes": 1_500_000_000, "download_url": "http://x/" + t,
             "protocol": "torrent", "indexer_id": 1, "guid": "g" + t}
            for t in ("Silo S02E03 1080p WEB-DL-NTb", "Silo.S02E03.1080p.WEB-NTb")]
    feed[0]["title"] = feed[0]["filename"] = "Silo.S02E03.1080p.WEB-NTb"
    feed[1]["title"] = feed[1]["filename"] = "Silo.S02E03.720p.WEB-NTb"
    rss.rss_pass(fetch=lambda: feed, extto=False)
    assert [g["title"] for g in grabs] == ["Silo.S02E03.720p.WEB-NTb"]


# ── TMDB paging ─────────────────────────────────────────────────────────────

class _R:
    def __init__(self, data, fail=False):
        self._d, self._f = data, fail

    def raise_for_status(self):
        if self._f:
            raise RuntimeError("boom")

    def json(self):
        return self._d


def _tmdb(monkeypatch, answers):
    calls = []

    def get(url, params=None, timeout=None):
        calls.append((params["query"], params["page"]))
        fn = answers.get((params["query"], params["page"]))
        return fn() if callable(fn) else _R(fn or {"results": [], "total_pages": 1})

    monkeypatch.setattr("requests.get", get)
    return calls


def _hits(*ids, year="2020"):
    return [{"media_type": "tv", "id": i, "name": "Another World", "first_air_date": year + "-01-01"}
            for i in ids]


def test_three_pages_deduped_stopping_at_the_last(monkeypatch):
    from core.video.enrichment.clients import TMDBClient
    calls = _tmdb(monkeypatch, {
        ("Another World", 1): {"results": _hits(1, 2), "total_pages": 2},
        ("Another World", 2): {"results": _hits(2, 3, year="2025"), "total_pages": 2}})
    got = TMDBClient("k").search("Another World")
    assert [g["tmdb_id"] for g in got] == [1, 2, 3]
    assert calls == [("Another World", 1), ("Another World", 2)]


def test_a_later_page_failing_keeps_the_first(monkeypatch):
    calls = _tmdb(monkeypatch, {
        ("X", 1): {"results": _hits(1), "total_pages": 3},
        ("X", 2): lambda: _R({}, fail=True)})
    assert [r["id"] for r in tmdb_search.raw_results(type("C", (), {"BASE": "b", "api_key": "k"})(), "X")] == [1]
    assert len(calls) == 2


def test_a_trailing_year_is_retried_only_when_the_exact_query_is_empty(monkeypatch):
    client = type("C", (), {"BASE": "b", "api_key": "k"})()
    _tmdb(monkeypatch, {("Another World", 1): {"results": _hits(1) + _hits(2, year="2025"),
                                               "total_pages": 1}})
    got = tmdb_search.raw_results(client, "Another World 2025")
    assert [r["id"] for r in got] == [2, 1], "that year first, the rest kept"
    _tmdb(monkeypatch, {("Blade Runner 2049", 1): {"results": _hits(7), "total_pages": 1}})
    assert [r["id"] for r in tmdb_search.raw_results(client, "Blade Runner 2049")] == [7]


def test_the_failure_path_survives_anything_the_retry_hits():
    from unittest.mock import Mock
    assert cr.after_failure(Mock(), _dl()) is False
