"""Endpoints behind the video Libraries editor and the library pickers."""

import sqlite3

import pytest
from flask import Flask, g

from core.commissary import video_libraries as vl
from database.video_database import VideoDatabase


@pytest.fixture()
def app(tmp_path, monkeypatch):
    import api.video as videoapi
    db = VideoDatabase(database_path=str(tmp_path / "video_library.db"))
    videoapi._video_db = db
    monkeypatch.setattr("core.video.sources.list_video_libraries", lambda: {
        "server": "plex", "movies": [{"title": "Movies"}, {"title": "Anime-Movies"}],
        "tv": [{"title": "TV Shows"}, {"title": "Anime"}]})
    monkeypatch.setattr("core.video.sources.resolve_video_server", lambda *a, **k: "plex")
    flask_app = Flask(__name__)
    flask_app.register_blueprint(videoapi.create_video_blueprint(), url_prefix="/api/video")
    admin = {"on": True}

    @flask_app.before_request
    def _who():
        g.profile_id = 1 if admin["on"] else 2
        g.is_admin = admin["on"]

    try:
        yield flask_app.test_client(), db, admin
    finally:
        videoapi._video_db = None


def test_the_editor_loads_sections_and_libraries(app):
    c, db, _ = app
    vl.save_libraries(db, "plex", movies=[{"server_title": "Movies", "path": "/m"}])
    d = c.get("/api/video/libraries/registry").get_json()
    assert d["server"] == "plex"
    assert d["available"] == {"movies": ["Movies", "Anime-Movies"], "tv": ["TV Shows", "Anime"]}
    assert [r["server_title"] for r in d["libraries"]["movies"]] == ["Movies"]
    assert {"id": 0, "name": "Default"} in d["profiles"]


def test_saving_from_the_editor(app):
    c, db, _ = app
    r = c.post("/api/video/libraries/registry", json={
        "movies": [{"server_title": "Movies", "path": "/m", "category": "Movies"},
                   {"server_title": "Anime-Movies", "path": "/am", "preferred_indexer_ids": "1"}],
        "tv": [{"server_title": "Anime", "path": "/a", "default_series_type": "anime"}]})
    assert r.status_code == 200, r.get_json()
    assert vl.section_titles(db, "plex", "movies") == ["Movies", "Anime-Movies"]
    assert vl.section_titles(db, "plex", "tv") == ["Anime"]


def test_a_bad_payload_is_refused(app):
    c, _db, _ = app
    assert c.post("/api/video/libraries/registry", json={"movies": "Movies"}).status_code == 400


def test_sending_a_wished_and_a_followed_title_to_a_library(app):
    c, db, _ = app
    vl.save_libraries(db, "plex", movies=[{"server_title": "Movies", "path": "/m"},
                                          {"server_title": "Anime-Movies", "path": "/am"}],
                      tv=[{"server_title": "Anime", "path": "/a"}])
    ids = {r["server_title"]: r["id"] for k, v in vl.list_libraries(db, "plex").items() for r in v}
    db.add_movie_to_wishlist(149, "Akira")
    conn = sqlite3.connect(str(db.database_path))
    conn.execute("INSERT INTO video_watchlist (kind, tmdb_id, title) VALUES ('show', 77, 'X')")
    conn.commit()
    conn.close()
    r = c.post("/api/video/libraries/assign/wishlist",
               json={"kind": "movie", "tmdb_id": 149, "root_folder_id": ids["Anime-Movies"]})
    assert r.get_json()["updated"] == 1
    assert vl.target_dir(db, "movie", item=db.movie_wishlist_to_download(due_only=False)[0]) == "/am"
    r = c.post("/api/video/libraries/assign/watchlist", json={"tmdb_id": 77, "root_folder_id": ids["Anime"]})
    assert r.get_json()["updated"] == 1
    assert vl.target_dir(db, "episode", tmdb_id=77) == "/a"


def test_only_an_admin_can_use_them(app):
    c, _db, admin = app
    admin["on"] = False
    assert c.get("/api/video/libraries/registry").status_code == 403
    assert c.post("/api/video/libraries/registry", json={"movies": []}).status_code == 403
    assert c.post("/api/video/libraries/assign/watchlist", json={"tmdb_id": 1}).status_code == 403
