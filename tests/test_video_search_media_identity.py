"""A manual search has to say WHICH title it is searching for.

The payload carries a pair — ``media_id`` plus ``media_source`` — and the two
have to agree: 'tmdb' means media_id IS a TMDB id, anything else means it is a
shows/movies ROW id. Everything a search needs beyond the bare title hangs off
that pair: the alias set, the user's own AKA titles, the air-date and absolute
hints, the series type, the Library's tracker list and its quality profile.

Reported for Re:ZERO S4: every release rejected as "Wrong title", and adding the
name the message printed as an AKA changed nothing. It could not: the show
search sent its TMDB id labelled 'library', so the lookup resolved a row id that
does not exist and answered nothing, and the manual-search overlay sent no pair
at all. The AKA was being read from an identity that never arrived.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from flask import Flask, g

from database.video_database import VideoDatabase

_ROOT = Path(__file__).resolve().parent.parent
_VIEW_JS = (_ROOT / "webui" / "static" / "video" / "video-download-view.js").read_text(encoding="utf-8")
_WISHLIST_JS = (_ROOT / "webui" / "static" / "video" / "video-wishlist.js").read_text(encoding="utf-8")
_DETAIL_JS = (_ROOT / "webui" / "static" / "video" / "video-detail.js").read_text(encoding="utf-8")

TMDB_ID = 65942
# Season 4 episode 16 of a show whose first three seasons run 25 + 25 + 16 = 66,
# so its absolute number is 82 — which is the number the tracker's SxxExx carries.
ANIMEBYTES = ("[SubsPlease] Re:ZERO -Starting Life in Another World- Season 4 S04E82 - 82 "
              "[Web][MKV][h264][1080p][AAC 2.0][Softsubs (SubsPlease)][Episode 82]")


@pytest.fixture()
def app_db(tmp_path, monkeypatch):
    import api.video as videoapi
    import core.video.sources as sources
    monkeypatch.setattr(sources, "resolve_video_server", lambda *a, **k: "plex")
    db = VideoDatabase(database_path=str(tmp_path / "video_library.db"))
    videoapi._video_db = db
    app = Flask(__name__)
    app.register_blueprint(videoapi.create_video_blueprint(), url_prefix="/api/video")

    @app.before_request
    def _p():
        g.profile_id = 1; g.is_admin = True; g.can_download = True; g.allowed_sides = "both"

    try:
        yield app.test_client(), db
    finally:
        videoapi._video_db = None


def _seed(db):
    """Re:ZERO as the library holds it: three finished seasons and a fourth airing."""
    seasons = []
    for sn, count in ((1, 25), (2, 25), (3, 16), (4, 19)):
        seasons.append({"season_number": sn, "episodes": [
            {"season_number": sn, "episode_number": en, "title": "E%d" % en,
             "air_date": "2026-09-09"} for en in range(1, count + 1)]})
    row_id = db.upsert_show_tree("plex", {
        "server_id": "s1", "tmdb_id": TMDB_ID,
        "title": "Re:ZERO -Starting Life in Another World-", "seasons": seasons})
    db.set_show_series_type(row_id, "anime")
    return row_id


def _no_aliases(monkeypatch):
    """TMDB's own alias list, stubbed away — these tests are about the user's
    AKAs and the hints, and a live alias lookup would answer over the network."""
    import core.video.enrichment.engine as engine
    monkeypatch.setattr(engine, "get_video_enrichment_engine",
                        lambda: type("E", (), {"alt_titles_for": staticmethod(lambda k, t: [])})())


# ── the pair has to agree ────────────────────────────────────────────────────
def test_a_tmdb_id_labelled_library_resolves_to_nothing(app_db):
    """The exact shape the show search sent. Not an error anywhere — it simply
    answers None, and every lookup downstream quietly gets nothing."""
    from api.video.downloads import _tmdb_id_from
    _, db = app_db
    row_id = _seed(db)
    assert _tmdb_id_from(db, {"kind": "show", "media_id": TMDB_ID, "media_source": "library"}) is None
    assert _tmdb_id_from(db, {"kind": "show", "media_id": TMDB_ID, "media_source": "tmdb"}) == TMDB_ID
    assert _tmdb_id_from(db, {"kind": "show", "media_id": row_id, "media_source": "library"}) == TMDB_ID


def test_the_users_own_aka_only_arrives_with_the_identity(app_db, monkeypatch):
    from api.video.downloads import _want_titles
    _, db = app_db
    row_id = _seed(db)
    db.set_aka_titles("show", TMDB_ID, ["Re:ZERO"])
    _no_aliases(monkeypatch)
    body = {"kind": "show", "scope": "episode",
            "title": "Re:ZERO -Starting Life in Another World-"}
    assert _want_titles(db, body) == ["Re:ZERO -Starting Life in Another World-"]
    assert "Re:ZERO" in _want_titles(db, {**body, "media_id": row_id, "media_source": "library"})
    assert "Re:ZERO" in _want_titles(db, {**body, "media_id": TMDB_ID, "media_source": "tmdb"})


def test_the_hints_and_the_series_type_come_from_the_same_pair(app_db):
    from api.video.downloads import _episode_hints, _series_type_for
    _, db = app_db
    row_id = _seed(db)
    ident = {"kind": "show", "scope": "episode", "media_id": row_id, "media_source": "library"}
    assert _episode_hints(db, ident, 4, 16) == ("2026-09-09", 82)
    assert _series_type_for(db, ident) == "anime"
    # the mismatched pair: no hints, and the show's type unknown
    broken = {**ident, "media_id": TMDB_ID}
    assert _episode_hints(db, broken, 4, 16) == (None, None)
    assert _series_type_for(db, broken) != "anime"


def test_the_search_turns_the_identity_into_an_accepted_release(app_db, monkeypatch):
    """End to end through the real route: the same hit, judged with and without
    the pair. Nothing about the release changes — only what the search knows."""
    from core.video import prowlarr_search
    client, db = app_db
    row_id = _seed(db)
    _no_aliases(monkeypatch)
    monkeypatch.setattr(prowlarr_search, "prowlarr_search", lambda *a, **k: {
        "configured": True, "hits": [{"title": ANIMEBYTES, "filename": ANIMEBYTES,
                                      "size_bytes": 1_400_000_000, "seeders": 40,
                                      "username": "AnimeBytes"}]})
    body = {"scope": "episode", "kind": "show", "source": "torrent", "season": 4, "episode": 16,
            "title": "Re:ZERO -Starting Life in Another World-"}

    blind = client.post("/api/video/downloads/search/start", json=body).get_json()
    assert [r["accepted"] for r in blind["results"]] == [False]
    assert blind["results"][0]["rejected"] == "Wrong episode"

    told = client.post("/api/video/downloads/search/start",
                       json={**body, "media_id": row_id, "media_source": "library"}).get_json()
    assert [r["accepted"] for r in told["results"]] == [True]


# ── what the grab hands the importer ─────────────────────────────────────────
def test_the_grab_records_the_hints_it_was_not_given(app_db, monkeypatch):
    """A picker grab's search_ctx carried {scope,title,year,season,episode} and
    nothing else, so the importer had no air date and no absolute number to
    judge the file by — and turned away what the search had just accepted."""
    from core.video import client_grab, disk_guard, download_monitor
    client, db = app_db
    row_id = _seed(db)
    monkeypatch.setattr(disk_guard, "has_room", lambda *a, **k: (True, 999))
    monkeypatch.setattr(client_grab, "grab", lambda *a, **k: {"ok": True, "ref": "abc"})
    monkeypatch.setattr(download_monitor, "ensure_started", lambda *a, **k: None)
    db.set_setting("tv_path", "/lib/anime")

    res = client.post("/api/video/downloads/grab", json={
        "kind": "show", "source": "torrent", "title": "Re:ZERO -Starting Life in Another World-",
        "release_title": ANIMEBYTES, "download_url": "http://x/1.torrent",
        "media_id": row_id, "media_source": "library", "size_bytes": 1_400_000_000,
        "search_ctx": {"scope": "episode", "title": "Re:ZERO -Starting Life in Another World-",
                       "season": 4, "episode": 16}}).get_json()
    assert res.get("ok") is True, res
    ctx = json.loads(db.get_video_download(res["id"])["search_ctx"])
    assert ctx["absolute"] == 82
    assert ctx["air_date"] == "2026-09-09"
    assert (ctx["season"], ctx["episode"]) == (4, 16)   # what was asked for is untouched


# ── the UI sends a pair that agrees ──────────────────────────────────────────
def test_the_search_reads_the_id_and_its_source_together():
    """A row id and a TMDB id are both just integers — only the source says
    which, so whichever field the id came from has to set the source too."""
    fn = _VIEW_JS.split("function mediaRef", 1)[1].split("\n    }", 1)[0]
    assert "ident.mediaSource" in fn and "'tmdb'" in fn
    # ...and the payload is built FROM it, not from a second reading of the same
    # fields somewhere else.
    into = _VIEW_JS.split("function searchInto", 1)[1].split("triggerRows =", 1)[0]
    assert "mediaRef(" in into
    assert "params.media_id = ref.id" in into and "params.media_source = ref.source" in into


def test_the_manual_overlay_searches_with_the_element_it_identified():
    """The overlay sets _opts on its BODY element (what buildGrabPayload reads)
    and used to hand searchInto the overlay itself — an element with no identity
    on it at all."""
    holder = _VIEW_JS.split("._opts = { id: opts.mediaId", 1)[0].rsplit("var ", 1)[1].split(" ", 1)[0]
    assert ("searchInto(%s," % holder) in _VIEW_JS
    assert "searchInto(ov," not in _VIEW_JS


def test_every_manual_search_entry_point_passes_its_identity():
    assert "mediaId: data.id, mediaSource: data.source" in _DETAIL_JS   # detail page
    assert _WISHLIST_JS.count("mediaId: tmdb, mediaSource: 'tmdb'") == 2  # wishlist movie + show
