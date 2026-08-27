"""A failed torrent grab that went looking on Soulseek.

The video retry path was written for Soulseek and never taught about the two
indexer sources, in three separate places — each of which on its own was enough
to make a torrent row unretryable:

1. ``build_download_record`` stored ``candidates: []`` for torrent/usenet. The
   ranked runners-up existed; they were thrown away at the moment of the grab.
   So there was never a next release to try.

2. ``merge_candidates`` projected a candidate down to five fields and dropped
   ``download_url`` / ``magnet_uri`` / ``guid`` / ``indexer_id``. A torrent
   candidate could be ranked and chosen and then not grabbed, because by then
   nothing knew where to fetch it from.

3. ``_apply_candidate`` called slskd's ``start_download`` unconditionally, and
   the requery worker called the slskd search unconditionally.

Together they meant a failed torrent asked SOULSEEK for a release only an
indexer had — almost always nothing, so the row failed with "No working release
found after retries" having never once asked the indexers that served it. And in
the rare case a same-named file did exist on Soulseek, the row went on saying
``source=torrent`` while a Soulseek transfer ran underneath it.
"""

from __future__ import annotations

import json
import os
import tempfile

import pytest

from core.video.retry import merge_candidates


def _hit(title="Show.S01E01.1080p", **over):
    h = {"title": title, "filename": title, "username": "SomeIndexer",
         "size_bytes": 1_000_000, "quality_label": "1080p",
         "download_url": "https://idx/dl/abc.torrent",
         "magnet_uri": "magnet:?xt=urn:btih:abc", "protocol": "torrent",
         "indexer_id": 7, "guid": "abc", "source": "torrent"}
    h.update(over)
    return h


# ── 1. the retry pool keeps what a grab needs ───────────────────────────────

class TestTheCandidateSurvivesTheMerge:
    def test_the_grab_carriers_are_kept(self):
        """Without these a torrent candidate is un-grabbable — it can be ranked
        and picked, and then there is nothing to hand the download client."""
        [c] = merge_candidates([_hit()], [])
        for key in ("download_url", "magnet_uri", "protocol", "indexer_id", "guid", "source"):
            assert c.get(key) == _hit()[key], key

    def test_a_soulseek_candidate_is_unchanged(self):
        """Soulseek hits carry none of those keys; the projection must not start
        inventing them."""
        [c] = merge_candidates([{"title": "T", "filename": "a.mkv", "username": "bob",
                                 "size_bytes": 5, "quality_label": "720p"}], [])
        assert c == {"username": "bob", "filename": "a.mkv", "size_bytes": 5,
                     "quality_label": "720p", "release_title": "T"}

    def test_a_null_carrier_is_not_copied_as_None(self):
        """An indexer that supplies no magnet must not leave `magnet_uri: None`
        sitting in the row where a real value is expected."""
        [c] = merge_candidates([_hit(magnet_uri=None)], [])
        assert "magnet_uri" not in c
        assert c["download_url"] == "https://idx/dl/abc.torrent"

    def test_already_tried_releases_are_still_dropped(self):
        assert merge_candidates([_hit("A"), _hit("B")], ["A"]) == [
            c for c in merge_candidates([_hit("B")], [])]

    def test_the_blocklist_still_applies(self):
        assert merge_candidates([_hit("A")], [], blocked={("SomeIndexer", "A")}) == []
        assert merge_candidates([_hit("A")], [], blocked_users={"SomeIndexer"}) == []


# ── 2. the grab stores a pool at all ────────────────────────────────────────

class TestTheGrabKeepsTheRunnersUp:
    def _record(self, source, cands, best):
        from core.automation.handlers.video_process_wishlist import build_download_record
        item = {"tmdb_id": 1, "show_tmdb_id": 1, "title": "T", "show_title": "T",
                "season_number": 1, "episode_number": 1}
        return build_download_record(item, best, cands, media_type="movie",
                                     target_dir="/media/movies", query="T 2026")

    def test_a_torrent_grab_keeps_the_other_accepted_releases(self):
        """THE gap. These were discarded, so the retry planner had nothing to
        plan with and every failure went straight to a requery."""
        best = _hit("Best")
        rec = self._record("torrent", [best, _hit("Second"), _hit("Third")], best)
        stored = json.loads(rec["candidates"])
        assert [c["title"] for c in stored] == ["Second", "Third"]

    def test_the_chosen_release_is_not_in_its_own_retry_pool(self):
        best = _hit("Best")
        rec = self._record("torrent", [best, _hit("Second")], best)
        assert "Best" not in [c["title"] for c in json.loads(rec["candidates"])]

    def test_the_chosen_release_is_recorded_as_tried(self):
        """Otherwise a requery can hand back the release that just failed."""
        best = _hit("Best")
        rec = self._record("torrent", [best], best)
        assert json.loads(rec["tried_files"]) == ["Best"]

    def test_the_query_is_recorded_so_the_next_one_differs(self):
        best = _hit("Best")
        rec = self._record("torrent", [best], best)
        assert json.loads(rec["tried_queries"]) == ["T 2026"]

    def test_a_soulseek_grab_is_unchanged(self):
        best = {"filename": "a.mkv", "username": "bob", "title": "A", "source": "soulseek"}
        other = {"filename": "b.mkv", "username": "amy", "title": "B", "source": "soulseek"}
        rec = self._record("soulseek", [best, other], best)
        assert rec["source"] == "soulseek"
        assert [c["filename"] for c in json.loads(rec["candidates"])] == ["b.mkv"]


# ── 3. the retry uses the row's OWN client ──────────────────────────────────

class _DB:
    def __init__(self):
        self.updates = {}
        self.rf = None

    def update_video_download(self, dl_id, **fields):
        self.updates = fields

    def root_folder_for_path(self, path):
        return self.rf


@pytest.fixture()
def monitor(monkeypatch):
    from core.video import download_monitor as mod
    calls = {"slskd": [], "grab": []}

    def _fake_start(username, filename, size):
        calls["slskd"].append((username, filename, size))
        return {"ok": True}

    monkeypatch.setattr(mod, "start_download", _fake_start)

    import core.video.client_grab as cg

    def _fake_grab(source, url, *, category=None, save_path=None, fallback_magnet=None):
        calls["grab"].append({"source": source, "url": url, "category": category,
                              "fallback_magnet": fallback_magnet})
        return {"ok": True, "ref": "HASH123"}

    monkeypatch.setattr(cg, "grab", _fake_grab)
    return mod, calls


class TestStartingARetry:
    def test_a_torrent_row_goes_to_the_torrent_client(self, monitor):
        mod, calls = monitor
        row = {"id": 1, "source": "torrent", "target_dir": "/media/tv", "title": "T"}
        res = mod._start_candidate(_DB(), row, _hit())
        assert res["ok"] and res["ref"] == "HASH123"
        assert calls["slskd"] == [], "a torrent retry must not touch slskd"
        assert calls["grab"][0]["source"] == "torrent"
        assert calls["grab"][0]["url"] == "https://idx/dl/abc.torrent"

    def test_the_magnet_rides_along_as_the_fallback(self, monitor):
        mod, calls = monitor
        row = {"id": 1, "source": "torrent", "target_dir": "/media/tv"}
        mod._start_candidate(_DB(), row, _hit())
        assert calls["grab"][0]["fallback_magnet"] == "magnet:?xt=urn:btih:abc"

    def test_a_usenet_row_goes_to_the_usenet_client(self, monitor):
        mod, calls = monitor
        row = {"id": 1, "source": "usenet", "target_dir": "/media/tv"}
        mod._start_candidate(_DB(), row, _hit(protocol="usenet"))
        assert calls["grab"][0]["source"] == "usenet" and calls["slskd"] == []

    def test_a_soulseek_row_still_goes_to_slskd(self, monitor):
        mod, calls = monitor
        row = {"id": 1, "source": "soulseek"}
        mod._start_candidate(_DB(), row, {"username": "bob", "filename": "a.mkv",
                                          "size_bytes": 9})
        assert calls["grab"] == [] and calls["slskd"] == [("bob", "a.mkv", 9)]

    def test_a_row_with_no_source_is_treated_as_soulseek(self, monitor):
        mod, calls = monitor
        mod._start_candidate(_DB(), {"id": 1}, {"username": "bob", "filename": "a.mkv"})
        assert calls["slskd"], "the historical default must not change"

    def test_a_carrier_less_candidate_fails_loudly_instead_of_falling_to_slskd(self, monitor):
        """An old row stored before the carriers were kept. Refusing is right;
        quietly starting a Soulseek transfer for a torrent row is not."""
        mod, calls = monitor
        row = {"id": 1, "source": "torrent"}
        res = mod._start_candidate(_DB(), row, {"filename": "X", "username": "idx"})
        assert res["ok"] is False and "download URL" in res["error"]
        assert calls["slskd"] == [] and calls["grab"] == []


class TestTheRowIsRepointedAtTheNewJob:
    def test_the_client_ref_is_updated_on_a_torrent_retry(self, monitor):
        """The ref is how the monitor finds the job. Leaving the old one would
        read progress from the download that just failed."""
        mod, _calls = monitor
        db = _DB()
        assert mod._apply_candidate(db, 1, {"id": 1, "source": "torrent",
                                            "target_dir": "/media/tv"}, _hit(), [])
        assert db.updates["client_ref"] == "HASH123"
        assert db.updates["status"] == "downloading"

    def test_a_soulseek_retry_does_not_invent_a_client_ref(self, monitor):
        mod, _calls = monitor
        db = _DB()
        mod._apply_candidate(db, 1, {"id": 1, "source": "soulseek"},
                             {"username": "bob", "filename": "a.mkv"}, [])
        assert "client_ref" not in db.updates

    def test_a_refused_grab_leaves_the_row_alone(self, monitor):
        mod, _calls = monitor
        import core.video.client_grab as cg
        cg.grab = lambda *a, **k: {"ok": False, "error": "client offline"}
        db = _DB()
        assert mod._apply_candidate(db, 1, {"id": 1, "source": "torrent"}, _hit(), []) is False
        assert db.updates == {}, "a refused retry must not mark the row downloading"


# ── 4. the requery asks the right network ───────────────────────────────────

class TestTheRequerySearchesTheRightPlace:
    def _ctx(self):
        return {"scope": "episode", "title": "Show", "season": 1, "episode": 2}

    def test_a_torrent_row_searches_prowlarr(self, monkeypatch):
        from core.video import download_monitor as mod
        seen = {}
        monkeypatch.setattr(mod, "_search_for_retry",
                            lambda q, **k: pytest.fail("slskd must not be searched"))
        import core.video.prowlarr_search as ps

        def _fake(scope, title, **kw):
            seen.update({"scope": scope, "title": title, **kw})
            return {"configured": True, "hits": [_hit()]}

        monkeypatch.setattr(ps, "prowlarr_search", _fake)
        out = mod._requery_hits(_DB(), {"source": "torrent", "target_dir": "/media/tv"},
                                "ignored free text", self._ctx())
        assert out["started"] is True and len(out["hits"]) == 1
        assert seen["scope"] == "episode" and seen["source"] == "torrent"
        assert seen["season"] == 1 and seen["episode"] == 2

    def test_a_soulseek_row_still_searches_slskd(self, monkeypatch):
        from core.video import download_monitor as mod
        monkeypatch.setattr(mod, "_search_for_retry",
                            lambda q, **k: {"hits": [{"filename": "a"}], "started": True})
        out = mod._requery_hits(_DB(), {"source": "soulseek"}, "Show S01E02", self._ctx())
        assert out["hits"] == [{"filename": "a"}]

    def test_the_librarys_tracker_selection_is_honoured(self, monkeypatch):
        """A retry that queried every indexer would quietly undo a deselection —
        the exact complaint the per-Library selection exists to answer."""
        from core.video import download_monitor as mod
        seen = {}
        import core.video.prowlarr_search as ps
        monkeypatch.setattr(ps, "prowlarr_search",
                            lambda scope, title, **kw: seen.update(kw) or
                            {"configured": True, "hits": []})
        db = _DB()
        db.rf = {"preferred_indexer_ids": "[3, 9]"}
        mod._requery_hits(db, {"source": "torrent", "target_dir": "/media/anime"},
                          "q", self._ctx())
        assert seen["indexer_ids"] == {3, 9}

    def test_no_selection_means_no_restriction(self, monkeypatch):
        from core.video import download_monitor as mod
        seen = {}
        import core.video.prowlarr_search as ps
        monkeypatch.setattr(ps, "prowlarr_search",
                            lambda scope, title, **kw: seen.update(kw) or
                            {"configured": True, "hits": []})
        db = _DB()
        db.rf = {"preferred_indexer_ids": None}
        mod._requery_hits(db, {"source": "torrent", "target_dir": "/x"}, "q", self._ctx())
        assert seen["indexer_ids"] == set()

    def test_prowlarr_not_configured_reads_as_not_started(self, monkeypatch):
        """'started: False' is how the caller tells "the search could not run"
        from "the search found nothing" — they deserve different messages."""
        from core.video import download_monitor as mod
        import core.video.prowlarr_search as ps
        monkeypatch.setattr(ps, "prowlarr_search",
                            lambda *a, **k: {"configured": False})
        out = mod._requery_hits(_DB(), {"source": "torrent"}, "q", self._ctx())
        assert out["started"] is False and "Prowlarr" in out["error"]

    def test_a_prowlarr_error_is_surfaced_not_swallowed(self, monkeypatch):
        from core.video import download_monitor as mod
        import core.video.prowlarr_search as ps
        monkeypatch.setattr(ps, "prowlarr_search",
                            lambda *a, **k: {"configured": True, "error": "boom"})
        out = mod._requery_hits(_DB(), {"source": "torrent"}, "q", self._ctx())
        assert out["started"] is False and out["error"] == "boom"


# ── 5. recovering the Library from where the grab was headed ────────────────

@pytest.fixture(scope="module")
def vdb():
    tmp = tempfile.mkdtemp(prefix="soulsync-testdb-retry-")
    os.environ.setdefault("DATABASE_PATH", os.path.join(tmp, "x.db"))
    from database.video_database import VideoDatabase
    db = VideoDatabase(os.path.join(tmp, "video.db"))
    conn = db._get_connection()
    try:
        conn.execute("INSERT INTO root_folders (server, content_kind, path, label, category) "
                     "VALUES ('plex','show','/media/tv','TV','tv')")
        conn.execute("INSERT INTO root_folders (server, content_kind, path, label, category) "
                     "VALUES ('plex','show','/media/tv/anime','Anime','anime')")
        conn.commit()
    finally:
        conn.close()
    return db


class TestFindingTheLibraryFromTheDestination:
    def test_an_exact_path_matches(self, vdb):
        assert vdb.root_folder_for_path("/media/tv")["category"] == "tv"

    def test_the_deepest_library_wins(self, vdb):
        """A nested Library must not resolve to its parent — that is exactly the
        Anime-inside-TV layout, and the wrong answer sends an anime grab into
        the TV category."""
        assert vdb.root_folder_for_path("/media/tv/anime")["category"] == "anime"

    def test_a_path_inside_a_library_resolves_to_it(self, vdb):
        assert vdb.root_folder_for_path("/media/tv/anime/Show Name")["category"] == "anime"

    def test_a_trailing_slash_does_not_matter(self, vdb):
        assert vdb.root_folder_for_path("/media/tv/anime/")["category"] == "anime"

    def test_an_unrelated_path_matches_nothing(self, vdb):
        assert vdb.root_folder_for_path("/downloads/complete") is None

    def test_a_partial_name_is_not_a_match(self, vdb):
        """'/media/tvshows' starts with '/media/tv' as a STRING but is not
        inside it."""
        assert vdb.root_folder_for_path("/media/tvshows") is None

    @pytest.mark.parametrize("value", [None, "", "   "])
    def test_nothing_in_nothing_out(self, vdb, value):
        assert vdb.root_folder_for_path(value) is None
