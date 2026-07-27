"""Per-Library preferred tracker(s) — a SOFT ranking nudge over torrent/usenet
hits (multi-library torrent preferences: Anime library favors tracker A, TV
library favors tracker B), mirroring the existing prefer_codec/prefer_hdr/
Custom Formats shape in the shared ranker (_evaluate_hits). Never a hard
filter — Prowlarr still searches every allowed indexer; a preferred tracker's
hit just ranks higher.
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


# ---------------------------------------------------------------------------
# _evaluate_hits scoring (pure)
# ---------------------------------------------------------------------------

def test_preferred_indexer_reorders_otherwise_tied_hits(db, client):
    from api.video.downloads import _evaluate_hits
    from core.video.quality_profile import load as load_profile
    out = _evaluate_hits(_hits(), load_profile(db), "movie", None, None,
                         blocked=frozenset(), blocked_users=frozenset(),
                         want_title="Heat", want_year=1995, preferred_indexer_ids={2})
    assert [r["indexer_id"] for r in out] == [2, 1]


def test_no_preference_is_a_no_op(db, client):
    from api.video.downloads import _evaluate_hits
    from core.video.quality_profile import load as load_profile
    out_none = _evaluate_hits(_hits(), load_profile(db), "movie", None, None,
                              blocked=frozenset(), blocked_users=frozenset(),
                              want_title="Heat", want_year=1995)
    out_empty = _evaluate_hits(_hits(), load_profile(db), "movie", None, None,
                               blocked=frozenset(), blocked_users=frozenset(),
                               want_title="Heat", want_year=1995, preferred_indexer_ids=set())
    # identical to the untouched pre-feature order (whatever the ladder/availability decide)
    assert [r["indexer_id"] for r in out_none] == [r["indexer_id"] for r in out_empty]


def test_preferred_indexer_score_bump_is_exactly_25(db, client):
    from api.video.downloads import _evaluate_hits
    from core.video.quality_profile import load as load_profile
    baseline = _evaluate_hits(_hits(), load_profile(db), "movie", None, None,
                              blocked=frozenset(), blocked_users=frozenset(),
                              want_title="Heat", want_year=1995)
    by_indexer = {r["indexer_id"]: r["score"] for r in baseline}
    boosted = _evaluate_hits(_hits(), load_profile(db), "movie", None, None,
                             blocked=frozenset(), blocked_users=frozenset(),
                             want_title="Heat", want_year=1995, preferred_indexer_ids={1})
    boosted_by = {r["indexer_id"]: r["score"] for r in boosted}
    assert boosted_by[1] == by_indexer[1] + 25
    assert boosted_by[2] == by_indexer[2]   # the non-preferred hit is untouched


def test_a_hit_with_no_indexer_id_never_matches(db, client):
    """Soulseek hits carry no indexer_id — the preference must be a silent
    no-op for them, never an error."""
    from api.video.downloads import _evaluate_hits
    from core.video.quality_profile import load as load_profile
    hits = [{"title": "Heat 1995 1080p WEB x264-GRP", "filename": "Heat 1995 1080p WEB x264-GRP",
             "size_bytes": 4_000_000_000, "username": "someone"}]   # no indexer_id
    out = _evaluate_hits(hits, load_profile(db), "movie", None, None,
                         blocked=frozenset(), blocked_users=frozenset(),
                         want_title="Heat", want_year=1995, preferred_indexer_ids={1, 2})
    assert len(out) == 1   # no crash


# ---------------------------------------------------------------------------
# Resolving a Library's preference from a root_folder_id (the manual-search path)
# ---------------------------------------------------------------------------

def test_preferred_indexer_ids_for_root_folder(db):
    from api.video.downloads import _preferred_indexer_ids_for_root_folder
    lib = _add_library(db, path="/anime", preferred_indexer_ids="1,3")
    assert _preferred_indexer_ids_for_root_folder(db, lib) == {1, 3}
    assert _preferred_indexer_ids_for_root_folder(db, None) == set()
    assert _preferred_indexer_ids_for_root_folder(db, 999999) == set()   # unknown id
    assert _preferred_indexer_ids_for_root_folder(db, "not-an-id") == set()


def test_search_endpoints_accept_and_apply_root_folder_id(db, client, monkeypatch):
    """The manual-search endpoints (torrent/usenet branch) resolve
    root_folder_id -> preferred_indexer_ids and the ranked results reflect it."""
    import core.video.prowlarr_search as ps
    lib = _add_library(db, path="/anime", preferred_indexer_ids="2")
    monkeypatch.setattr(ps, "prowlarr_search", lambda *a, **kw: {"configured": True, "hits": _hits()})
    out = client.post("/api/video/downloads/search",
                      json={"scope": "movie", "title": "Heat", "year": 1995, "source": "torrent",
                            "root_folder_id": lib}).get_json()
    assert out["results"][0]["indexer_id"] == 2   # the preferred tracker's hit sorts first

    out_start = client.post("/api/video/downloads/search/start",
                            json={"scope": "movie", "title": "Heat", "year": 1995, "source": "torrent",
                                  "root_folder_id": lib}).get_json()
    assert out_start["results"][0]["indexer_id"] == 2


def test_search_without_root_folder_id_is_unaffected(db, client, monkeypatch):
    import core.video.prowlarr_search as ps
    monkeypatch.setattr(ps, "prowlarr_search", lambda *a, **kw: {"configured": True, "hits": _hits()})
    out = client.post("/api/video/downloads/search",
                      json={"scope": "movie", "title": "Heat", "year": 1995, "source": "torrent"}).get_json()
    assert len(out["results"]) == 2   # both present; order not asserted (no preference set)


# ---------------------------------------------------------------------------
# End-to-end: two Libraries, two different preferred trackers, resolve+score
# ---------------------------------------------------------------------------

def test_two_libraries_each_prefer_their_own_tracker(db):
    """The reported shape: an Anime library favors tracker A, a standard TV
    library favors tracker B — each Library's own preference wins its own
    ranking, composing _preferred_indexer_ids_for_item with _evaluate_hits."""
    from api.video.downloads import _evaluate_hits
    from core.automation.handlers.video_process_wishlist import _preferred_indexer_ids_for_item
    from core.video.quality_profile import load as load_profile
    anime = _add_library(db, path="/anime", preferred_indexer_ids="1")
    tv = _add_library(db, path="/tv", preferred_indexer_ids="2")

    import api.video as videoapi
    videoapi._video_db = db
    try:
        anime_item = {"root_folder_id": anime}
        tv_item = {"root_folder_id": tv}
        anime_pref = _preferred_indexer_ids_for_item(anime_item)
        tv_pref = _preferred_indexer_ids_for_item(tv_item)
        assert anime_pref == {1} and tv_pref == {2}

        anime_out = _evaluate_hits(_hits(), load_profile(db), "movie", None, None,
                                   blocked=frozenset(), blocked_users=frozenset(),
                                   want_title="Heat", want_year=1995, preferred_indexer_ids=anime_pref)
        tv_out = _evaluate_hits(_hits(), load_profile(db), "movie", None, None,
                                blocked=frozenset(), blocked_users=frozenset(),
                                want_title="Heat", want_year=1995, preferred_indexer_ids=tv_pref)
        assert anime_out[0]["indexer_id"] == 1
        assert tv_out[0]["indexer_id"] == 2
    finally:
        videoapi._video_db = None


# ---------------------------------------------------------------------------
# Frontend contracts — Library editor gains a preferred-trackers field
# ---------------------------------------------------------------------------

def test_library_row_wires_the_preferred_trackers_field():
    body = _SETTINGS_JS[_SETTINGS_JS.index("function libraryRow("):]
    body = body[:body.index("\n    function ", 10)]
    assert "data-lib-indexer-ids" in body
    assert "configured.preferred_indexer_ids" in body


def test_collect_libraries_reads_the_preferred_trackers_field():
    body = _SETTINGS_JS[_SETTINGS_JS.index("function collectLibraries("):]
    body = body[:body.index("\n    function ", 10)]
    assert "data-lib-indexer-ids" in body
    assert "preferred_indexer_ids:" in body


def test_search_into_sends_the_picked_library_before_grab_time():
    """The library picker's choice must reach the ranker at SEARCH time, not
    just at grab time — otherwise the preference can never affect ordering."""
    body = _VIEW_JS[_VIEW_JS.index("function searchInto("):]
    body = body[:body.index("\n    function ", 10)]
    assert "pickedRootFolderId(container)" in body
    assert "params.root_folder_id" in body
