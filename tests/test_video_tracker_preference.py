"""Per-Library tracker selection — a search RESTRICTION, not a ranking nudge.

Reported as: "if I deselect a tracker from the list it will still try to grab
from that tracker during automatic searches."

It did, and by design. The selection only ever reached ``_evaluate_hits`` as a
+25 score bonus while Prowlarr was still asked to search every indexer. So
unticking a tracker removed a bonus and changed nothing about which trackers
were used. The tooltip did say "a soft nudge, not a search filter" — but
``renderTrackerPicker`` sets ``input.type = 'hidden'`` the moment the checkbox
list renders, and a hidden input shows neither tooltip nor placeholder. The one
sentence explaining the behaviour disappeared exactly when the checkboxes it
described appeared.

Ticked trackers are now the ONLY ones searched for grabs into that Library,
enforced at every unattended acquisition path:

  * the wishlist drain  — prowlarr_search(indexer_ids=…)
  * the RSS pass        — the pool is fetched with the global allowlist, then
                          filtered per item by its Library's selection
  * manual search       — resolved BEFORE the search, not after

The old +25 bump is gone rather than kept: once only permitted trackers are
searched, every hit comes from one, so the bonus applies uniformly and can no
longer discriminate between candidates.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from flask import Flask

from database.video_database import VideoDatabase

_ROOT = Path(__file__).resolve().parent.parent
_SETTINGS_JS = (_ROOT / "webui" / "static" / "video" / "video-settings.js").read_text(encoding="utf-8")
_VIEW_JS = (_ROOT / "webui" / "static" / "video" / "video-download-view.js").read_text(encoding="utf-8")


@pytest.fixture()
def db(tmp_path):
    return VideoDatabase(database_path=str(tmp_path / "video_library.db"))


@pytest.fixture()
def client(db, monkeypatch):
    import api.video as videoapi
    import core.video.sources as sources
    monkeypatch.setattr(sources, "resolve_video_server", lambda: "plex")
    videoapi._video_db = db
    app = Flask(__name__)
    app.register_blueprint(videoapi.create_video_blueprint(), url_prefix="/api/video")
    try:
        yield app.test_client()
    finally:
        videoapi._video_db = None


def _add_library(db, *, path, kind="show", preferred_indexer_ids=None):
    conn = db._get_connection()
    cur = conn.execute(
        "INSERT INTO root_folders (path, content_kind, server, preferred_indexer_ids) VALUES (?,?,?,?)",
        (str(path), kind, "plex", preferred_indexer_ids))
    rid = cur.lastrowid
    conn.commit(); conn.close()
    return rid


def _hits():
    return [
        {"title": "Heat 1995 1080p WEB x264-A", "filename": "Heat 1995 1080p WEB x264-A",
         "size_bytes": 4_000_000_000, "username": "indexerA", "indexer_id": 1},
        {"title": "Heat 1995 1080p WEB x264-B", "filename": "Heat 1995 1080p WEB x264-B",
         "size_bytes": 4_000_000_000, "username": "indexerB", "indexer_id": 2},
    ]


# ── which indexers a search may use ──────────────────────────────────────────
def _effective(global_ids, restrict, monkeypatch):
    import core.video.prowlarr_search as ps
    monkeypatch.setattr(ps, "_indexer_ids", lambda: global_ids)
    return ps.effective_indexer_ids(restrict)


def test_no_restriction_anywhere_searches_everything(monkeypatch):
    """[] has always meant 'no filter' to Prowlarr — unchanged for anyone who
    has never touched either setting."""
    assert _effective([], None, monkeypatch) == []
    assert _effective([], [], monkeypatch) == []


def test_a_library_selection_alone_restricts(monkeypatch):
    assert _effective([], [5, 7], monkeypatch) == [5, 7]


def test_the_global_allowlist_alone_still_restricts(monkeypatch):
    assert _effective([1, 2, 3], None, monkeypatch) == [1, 2, 3]


def test_both_set_intersects(monkeypatch):
    """The Library narrows the global allowlist; it can never widen it."""
    assert _effective([1, 2, 3], [2, 3, 9], monkeypatch) == [2, 3]


def test_a_library_cannot_reach_a_globally_excluded_tracker(monkeypatch):
    import core.video.prowlarr_search as ps
    assert _effective([1, 2], [9], monkeypatch) is ps._NO_INDEXERS


def test_a_contradiction_never_degrades_to_searching_everything(monkeypatch):
    """The dangerous failure mode. An empty intersection returned as [] would
    read as 'no restriction' and search EVERY indexer — the exact opposite of
    what both settings asked for."""
    import core.video.prowlarr_search as ps
    got = _effective([1, 2], [7, 8], monkeypatch)
    assert got is ps._NO_INDEXERS
    assert got != []


def test_junk_ids_are_dropped_not_crashed(monkeypatch):
    assert _effective([], ["4", "x", None, 6], monkeypatch) == [4, 6]


def test_the_search_reports_a_contradiction_instead_of_running(monkeypatch):
    import core.video.prowlarr_search as ps

    class _C:
        @staticmethod
        def is_configured():
            return True

    monkeypatch.setattr(ps, "_client", lambda: _C())
    monkeypatch.setattr(ps, "_indexer_ids", lambda: [1, 2])
    out = ps.prowlarr_search("movie", "Heat", indexer_ids=[9])
    assert out["hits"] == []
    assert "excluded" in out["error"].lower()


# ── the reported bug: the unattended drain ───────────────────────────────────
def test_the_drain_restricts_the_search_to_the_library_s_trackers(db, monkeypatch):
    """THE regression test. The wishlist drain is the 'automatic searches' in
    the report — it must ask Prowlarr for the chosen trackers only."""
    import api.video as videoapi
    import core.automation.handlers.video_process_wishlist as vpw
    import core.video.prowlarr_search as ps

    lib = _add_library(db, path="/anime", preferred_indexer_ids="2")
    videoapi._video_db = db
    seen = {}

    def _fake_search(*a, **kw):
        seen["indexer_ids"] = kw.get("indexer_ids")
        return {"configured": True, "hits": _hits()}

    monkeypatch.setattr(ps, "prowlarr_search", _fake_search)
    try:
        vpw._search_one_source("torrent", {"root_folder_id": lib, "title": "Heat",
                                           "year": 1995}, "movie")
    finally:
        videoapi._video_db = None
    assert seen["indexer_ids"] == {2}, "the drain searched without the Library's restriction"


def test_the_drain_passes_no_restriction_when_the_library_has_none(db, monkeypatch):
    import api.video as videoapi
    import core.automation.handlers.video_process_wishlist as vpw
    import core.video.prowlarr_search as ps

    lib = _add_library(db, path="/tv", preferred_indexer_ids=None)
    videoapi._video_db = db
    seen = {}
    monkeypatch.setattr(ps, "prowlarr_search",
                        lambda *a, **kw: seen.update(indexer_ids=kw.get("indexer_ids"))
                        or {"configured": True, "hits": _hits()})
    try:
        vpw._search_one_source("torrent", {"root_folder_id": lib, "title": "Heat",
                                           "year": 1995}, "movie")
    finally:
        videoapi._video_db = None
    assert not seen["indexer_ids"], "an unset Library must not restrict anything"


def test_an_item_with_no_library_is_not_restricted(db):
    """Inheriting a RESTRICTION from an unrelated 'primary' Library would
    silently narrow searches to trackers the user never chose for this item,
    and an over-narrow search is indistinguishable from 'no releases exist'."""
    import api.video as videoapi
    from core.automation.handlers.video_process_wishlist import _preferred_indexer_ids_for_item
    _add_library(db, path="/anime", preferred_indexer_ids="1")
    videoapi._video_db = db
    try:
        assert _preferred_indexer_ids_for_item({}) == set()
    finally:
        videoapi._video_db = None


# ── the RSS pass ─────────────────────────────────────────────────────────────
def test_the_rss_pool_honours_the_global_allowlist(monkeypatch):
    """It passed a hardcoded [], so the RSS pass polled every indexer even when
    the global setting named a few — releases from an excluded tracker could be
    matched and grabbed unattended."""
    import core.video.prowlarr_search as ps
    import core.video.rss_sync as rss
    seen = {}

    class _C:
        @staticmethod
        def is_configured():
            return True

        @staticmethod
        def _search_sync(q, cats, ids, limit, **kw):
            seen["ids"] = ids
            return []

    monkeypatch.setattr(ps, "_client", lambda: _C())
    monkeypatch.setattr(ps, "_indexer_ids", lambda: [3, 4])
    rss.fetch_recent_releases()
    assert seen["ids"] == [3, 4]


def test_the_rss_rank_drops_releases_from_deselected_trackers(db):
    """The pool is already fetched here, so the Library's restriction has to be
    enforced by filtering it."""
    import api.video as videoapi
    import core.video.rss_sync as rss

    lib = _add_library(db, path="/anime", preferred_indexer_ids="2")
    videoapi._video_db = db
    try:
        out = rss._rank(_hits(), {"root_folder_id": lib, "title": "Heat", "year": 1995}, "movie")
    finally:
        videoapi._video_db = None
    assert all(c.get("indexer_id") == 2 for c in out), "a deselected tracker survived the RSS pass"


def test_the_rss_rank_is_unfiltered_without_a_restriction(db):
    import api.video as videoapi
    import core.video.rss_sync as rss

    lib = _add_library(db, path="/tv", preferred_indexer_ids=None)
    videoapi._video_db = db
    try:
        out = rss._rank(_hits(), {"root_folder_id": lib, "title": "Heat", "year": 1995}, "movie")
    finally:
        videoapi._video_db = None
    assert {c.get("indexer_id") for c in out} == {1, 2}


# ── manual search ────────────────────────────────────────────────────────────
def test_preferred_indexer_ids_for_root_folder(db):
    from api.video.downloads import _preferred_indexer_ids_for_root_folder
    lib = _add_library(db, path="/anime", preferred_indexer_ids="1,3")
    assert _preferred_indexer_ids_for_root_folder(db, lib) == {1, 3}
    assert _preferred_indexer_ids_for_root_folder(db, None) == set()
    assert _preferred_indexer_ids_for_root_folder(db, 999999) == set()   # unknown id
    assert _preferred_indexer_ids_for_root_folder(db, "not-an-id") == set()


def test_manual_search_restricts_before_searching_not_after(db, client, monkeypatch):
    """Resolving the Library after the search could only ever re-order results
    that had already come back from every tracker."""
    import core.video.prowlarr_search as ps
    lib = _add_library(db, path="/anime", preferred_indexer_ids="2")
    seen = {}
    monkeypatch.setattr(ps, "prowlarr_search",
                        lambda *a, **kw: seen.update(indexer_ids=kw.get("indexer_ids"))
                        or {"configured": True, "hits": _hits()})

    client.post("/api/video/downloads/search",
                json={"scope": "movie", "title": "Heat", "year": 1995, "source": "torrent",
                      "root_folder_id": lib})
    assert seen["indexer_ids"] == {2}

    seen.clear()
    client.post("/api/video/downloads/search/start",
                json={"scope": "movie", "title": "Heat", "year": 1995, "source": "torrent",
                      "root_folder_id": lib})
    assert seen["indexer_ids"] == {2}


def test_search_without_root_folder_id_is_unaffected(db, client, monkeypatch):
    import core.video.prowlarr_search as ps
    monkeypatch.setattr(ps, "prowlarr_search", lambda *a, **kw: {"configured": True, "hits": _hits()})
    out = client.post("/api/video/downloads/search",
                      json={"scope": "movie", "title": "Heat", "year": 1995,
                            "source": "torrent"}).get_json()
    assert len(out["results"]) == 2


# ── the ranker no longer scores trackers ─────────────────────────────────────
def test_the_ranker_takes_no_tracker_argument_any_more():
    """Leaving a scoring signal that can never discriminate is dead weight, and
    keeping the parameter would let a caller believe it still filters."""
    import inspect
    from api.video.downloads import _evaluate_hits
    assert "preferred_indexer_ids" not in inspect.signature(_evaluate_hits).parameters


def test_hits_are_ranked_without_reference_to_their_tracker(db, client):
    from api.video.downloads import _evaluate_hits
    from core.video.quality_profile import load as load_profile
    out = _evaluate_hits(_hits(), load_profile(db), "movie", None, None,
                         blocked=frozenset(), blocked_users=frozenset(),
                         want_title="Heat", want_year=1995)
    scores = {r["indexer_id"]: r["score"] for r in out}
    assert scores[1] == scores[2], "two otherwise-identical hits must score the same"


def test_a_hit_with_no_indexer_id_is_still_fine(db, client):
    """Soulseek hits carry no indexer_id and never go near Prowlarr."""
    from api.video.downloads import _evaluate_hits
    from core.video.quality_profile import load as load_profile
    hits = [{"title": "Heat 1995 1080p WEB x264-GRP", "filename": "Heat 1995 1080p WEB x264-GRP",
             "size_bytes": 4_000_000_000, "username": "someone"}]
    out = _evaluate_hits(hits, load_profile(db), "movie", None, None,
                         blocked=frozenset(), blocked_users=frozenset(),
                         want_title="Heat", want_year=1995)
    assert len(out) == 1


# ── the UI has to say what it does ───────────────────────────────────────────
def test_library_row_wires_the_trackers_field():
    body = _SETTINGS_JS[_SETTINGS_JS.index("function libraryRow("):]
    body = body[:body.index("\n    function ", 10)]
    assert "data-lib-indexer-ids" in body
    assert "configured.preferred_indexer_ids" in body


def test_collect_libraries_reads_the_trackers_field():
    body = _SETTINGS_JS[_SETTINGS_JS.index("function collectLibraries("):]
    body = body[:body.index("\n    function ", 10)]
    assert "data-lib-indexer-ids" in body
    assert "preferred_indexer_ids:" in body


def test_search_into_sends_the_picked_library_before_grab_time():
    body = _VIEW_JS[_VIEW_JS.index("function searchInto("):]
    body = body[:body.index("\n    function ", 10)]
    assert "pickedRootFolderId(container)" in body
    assert "params.root_folder_id" in body
