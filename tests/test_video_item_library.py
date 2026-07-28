"""File a movie (or show) under the right Library from Manage.

Reported for movies. The Libraries registry has always had a root_folder_id on
both movies and shows, and both kinds get a destination picker when downloading
— but nothing anywhere let you CORRECT the assignment afterwards, for either
kind. A title that landed in the wrong Library stayed there, and every future
grab and upgrade kept going to the wrong tree.

Metadata only: nothing on disk moves. It changes where FUTURE work goes, which
is what actually unsticks a mis-filed title.
"""

from __future__ import annotations

import pytest
from flask import Flask, g

from database.video_database import VideoDatabase


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


def _libs(db):
    """Two movie Libraries and one TV Library."""
    db.save_libraries(
        "plex",
        [{"server_title": "Movies", "label": "Movies", "path": "/media/movies"},
         {"server_title": "4K", "label": "4K Movies", "path": "/media/movies-4k"}],
        [{"server_title": "TV", "label": "TV Shows", "path": "/media/tv"}])
    movies = [r for r in db.all_library_rows() if r["content_kind"] == "movie"]
    shows = [r for r in db.all_library_rows() if r["content_kind"] == "show"]
    return movies, shows


def _movie(db, tmdb_id=603, title="The Matrix"):
    return db.upsert_movie("plex", {"server_id": "m1", "tmdb_id": tmdb_id,
                                    "title": title, "year": 1999})


# ── the DB layer ─────────────────────────────────────────────────────────────
def test_a_movie_can_be_refiled_and_cleared(app_db):
    _c, db = app_db
    movies, _ = _libs(db)
    mid = _movie(db)
    assert db.set_item_root_folder("movie", mid, movies[1]["id"]) is True
    assert db.movie_detail(mid)["root_folder_id"] == movies[1]["id"]
    # clearing is a real state — falls back to the primary Library for the kind
    assert db.set_item_root_folder("movie", mid, None) is True
    assert db.movie_detail(mid)["root_folder_id"] is None


def test_a_movie_cannot_be_filed_under_a_tv_library(app_db):
    """The failure this guard exists for is silent: every future grab for the
    movie would resolve into the TV tree."""
    _c, db = app_db
    movies, shows = _libs(db)
    mid = _movie(db)
    db.set_item_root_folder("movie", mid, movies[0]["id"])
    assert db.set_item_root_folder("movie", mid, shows[0]["id"]) is False
    assert db.movie_detail(mid)["root_folder_id"] == movies[0]["id"]   # unchanged


def test_an_unknown_library_is_refused(app_db):
    _c, db = app_db
    _libs(db)
    mid = _movie(db)
    assert db.set_item_root_folder("movie", mid, 99999) is False
    assert db.set_item_root_folder("movie", mid, "not-a-number") is False


def test_an_unknown_item_is_refused(app_db):
    _c, db = app_db
    movies, _ = _libs(db)
    assert db.set_item_root_folder("movie", 4242, movies[0]["id"]) is False


def test_a_bad_kind_is_refused(app_db):
    _c, db = app_db
    movies, _ = _libs(db)
    mid = _movie(db)
    assert db.set_item_root_folder("channel", mid, movies[0]["id"]) is False


def test_shows_work_the_same_way(app_db):
    """The panel is shared, so the field lands for both kinds — shows were
    missing it too."""
    _c, db = app_db
    movies, shows = _libs(db)
    sid = db.upsert_show_tree("plex", {"server_id": "s1", "tmdb_id": 1399,
                                       "title": "GoT", "seasons": []})
    assert db.set_item_root_folder("show", sid, shows[0]["id"]) is True
    assert db.set_item_root_folder("show", sid, movies[0]["id"]) is False


# ── the endpoint ─────────────────────────────────────────────────────────────
def test_the_endpoint_refiles_a_movie(app_db):
    c, db = app_db
    movies, _ = _libs(db)
    mid = _movie(db)
    r = c.put("/api/video/detail/movie/%d/library" % mid,
              json={"root_folder_id": movies[1]["id"]})
    assert r.status_code == 200 and r.get_json()["root_folder_id"] == movies[1]["id"]
    assert db.movie_detail(mid)["root_folder_id"] == movies[1]["id"]


def test_the_endpoint_clears_with_null(app_db):
    c, db = app_db
    movies, _ = _libs(db)
    mid = _movie(db)
    db.set_item_root_folder("movie", mid, movies[0]["id"])
    r = c.put("/api/video/detail/movie/%d/library" % mid, json={"root_folder_id": None})
    assert r.status_code == 200 and r.get_json()["root_folder_id"] is None


def test_the_endpoint_rejects_a_cross_kind_library(app_db):
    c, db = app_db
    _movies, shows = _libs(db)
    mid = _movie(db)
    r = c.put("/api/video/detail/movie/%d/library" % mid,
              json={"root_folder_id": shows[0]["id"]})
    assert r.status_code == 400 and "movie" in r.get_json()["error"]


def test_the_endpoint_rejects_a_bad_kind(app_db):
    c, _db = app_db
    assert c.put("/api/video/detail/channel/1/library", json={}).status_code == 400


def test_changing_the_library_is_admin_only(app_db, monkeypatch):
    """Same class as /metadata and /aka — it redirects where downloads land."""
    import api.video as videoapi
    c, db = app_db
    movies, _ = _libs(db)
    mid = _movie(db)
    app = c.application

    @app.before_request
    def _member():
        g.profile_id = 7; g.is_admin = False; g.can_download = True; g.allowed_sides = "both"

    r = c.put("/api/video/detail/movie/%d/library" % mid,
              json={"root_folder_id": movies[1]["id"]})
    assert r.status_code == 403
    assert db.movie_detail(mid)["root_folder_id"] != movies[1]["id"]


# ── the panel wiring ─────────────────────────────────────────────────────────
def test_the_panel_offers_the_field_for_both_kinds():
    from pathlib import Path
    js = (Path(__file__).resolve().parent.parent / "webui" / "static" / "video"
          / "video-manage-panel.js").read_text(encoding="utf-8")
    body = js.split("function bodyHtml", 1)[1].split("\n    function ", 1)[0]
    assert "data-vmg-library" in body
    # NOT inside the shows-only branch — that is the bug being fixed
    show_only = body.split("d.kind === 'show'", 1)[1].split(": ''", 1)[0]
    assert "data-vmg-library" not in show_only


def test_the_panel_reads_the_registry_not_server_discovery():
    """root_folder_id points at the registry (d.configured); the live server
    sections are admin-only discovery data with no ids on them."""
    from pathlib import Path
    js = (Path(__file__).resolve().parent.parent / "webui" / "static" / "video"
          / "video-manage-panel.js").read_text(encoding="utf-8")
    fn = js.split("function loadLibraries", 1)[1].split("\n    function ", 1)[0]
    assert "res.configured" in fn and "conf.movies" in fn and "conf.tv" in fn
