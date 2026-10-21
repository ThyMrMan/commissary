"""Grabs are filed into the title's video Library, with that library's
download-client category.

On the live install upstream's movies_path / tv_path are empty, so without
this every unattended grab was skipped ("library folder not set") and a
manual grab answered "Set the library folder"."""

import sqlite3

import pytest
from flask import Flask

from core.commissary import video_libraries as vl
from database.video_database import VideoDatabase


@pytest.fixture()
def client(tmp_path, monkeypatch):
    import api.video as videoapi
    db = VideoDatabase(database_path=str(tmp_path / "video_library.db"))
    videoapi._video_db = db
    vl.save_libraries(db, "plex",
                      movies=[{"server_title": "Movies", "path": "/lib/movies", "category": "Movies"},
                              {"server_title": "Anime-Movies", "path": "/lib/anime-movies",
                               "category": "Anime-Movies", "default_quality_profile_id": 7}],
                      tv=[{"server_title": "TV Shows", "path": "/lib/tv", "category": "TV-Shows"},
                          {"server_title": "Anime", "path": "/lib/anime", "category": "Anime",
                           "default_quality_profile_id": 9}])
    ids = {r["server_title"]: r["id"] for k, v in vl.list_libraries(db, "plex").items() for r in v}
    conn = sqlite3.connect(str(db.database_path))
    conn.execute("INSERT INTO shows (title, tmdb_id, server_source, root_folder_id) "
                 "VALUES ('Bleach', 30984, 'plex', ?)", (ids["Anime"],))
    conn.execute("INSERT INTO movies (title, tmdb_id, server_source, root_folder_id) "
                 "VALUES ('Akira', 149, 'plex', ?)", (ids["Anime-Movies"],))
    conn.commit()
    conn.close()
    grabs = []

    def fake_grab(source, url, *, save_path=None, fallback_magnet=None, category=None):
        grabs.append({"source": source, "category": category})
        return {"ok": True, "ref": "abc123"}

    monkeypatch.setattr("core.video.client_grab.grab", fake_grab)
    monkeypatch.setattr("core.video.download_monitor.ensure_started", lambda *a, **k: None)
    monkeypatch.setattr("core.video.disk_guard.free_gb", lambda p: 10000.0)
    app = Flask(__name__)
    app.register_blueprint(videoapi.create_video_blueprint(), url_prefix="/api/video")
    try:
        yield app.test_client(), db, grabs, ids
    finally:
        videoapi._video_db = None


def _target_dirs(db):
    conn = sqlite3.connect(str(db.database_path))
    try:
        return [r[0] for r in conn.execute("SELECT target_dir FROM video_downloads ORDER BY id")]
    finally:
        conn.close()


def test_the_drain_files_an_anime_episode_into_anime_with_its_category(client):
    _c, db, grabs, _ids = client
    from core.automation.handlers import video_process_wishlist as vpw

    item = {"kind": "episode", "tmdb_id": 30984, "title": "Bleach", "season_number": 1,
            "episode_number": 1}
    best = {"source": "torrent", "download_url": "magnet:?xt=urn:btih:abc", "title": "Bleach - 01"}
    out = vpw._default_enqueue(item, best, [best], "episode", "/upstream/tv")
    assert vpw.enqueue_outcome(out)["ok"] is True, out
    assert _target_dirs(db) == ["/lib/anime"]
    assert grabs == [{"source": "torrent", "category": "Anime"}]


def test_the_drain_files_an_unknown_movie_into_the_primary_library(client):
    _c, db, grabs, _ids = client
    from core.automation.handlers import video_process_wishlist as vpw

    best = {"source": "usenet", "download_url": "http://nzb/1", "title": "New Film"}
    vpw._default_enqueue({"kind": "movie", "tmdb_id": 5, "title": "New Film"}, best, [best],
                         "movie", "")
    assert _target_dirs(db) == ["/lib/movies"]
    assert grabs[0]["category"] == "Movies"


def test_the_drain_has_a_folder_when_upstreams_paths_are_empty(client):
    from core.automation.handlers import video_process_wishlist as vpw

    assert vpw._default_target_dir("movie") == "/lib/movies"
    assert vpw._default_target_dir("episode") == "/lib/tv"


def test_a_manual_grab_goes_to_the_titles_library(client):
    c, db, grabs, _ids = client
    r = c.post("/api/video/downloads/grab", json={
        "kind": "movie", "title": "Akira", "source": "torrent", "media_id": 149,
        "media_source": "tmdb", "download_url": "magnet:?xt=urn:btih:def",
        "release_title": "Akira.1988.1080p"})
    assert r.status_code == 200, r.get_json()
    assert _target_dirs(db) == ["/lib/anime-movies"]
    assert grabs == [{"source": "torrent", "category": "Anime-Movies"}]


def test_a_manual_grab_can_pick_a_library(client):
    c, db, grabs, ids = client
    r = c.post("/api/video/downloads/grab", json={
        "kind": "movie", "title": "Akira", "source": "torrent", "media_id": 149,
        "root_folder_id": ids["Movies"], "download_url": "magnet:?xt=urn:btih:ghi"})
    assert r.status_code == 200, r.get_json()
    assert _target_dirs(db) == ["/lib/movies"]


def test_wishlist_rows_carry_their_library_and_its_default_profile(client):
    _c, db, _grabs, ids = client
    db.add_movie_to_wishlist(149, "Akira")
    db.add_episodes_to_wishlist(30984, "Bleach", [{"season_number": 1, "episode_number": 1,
                                                   "title": "E1"}])
    movie = db.movie_wishlist_to_download(due_only=False)[0]
    assert (movie["root_folder_id"], movie["quality_profile_id"]) == (ids["Anime-Movies"], 7)
    ep = db.episode_wishlist_to_download(due_only=False)[0]
    assert (ep["root_folder_id"], ep["quality_profile_id"]) == (ids["Anime"], 9)


def test_a_titles_own_profile_beats_the_library_default(client):
    _c, db, _grabs, _ids = client
    conn = sqlite3.connect(str(db.database_path))
    conn.execute("UPDATE movies SET quality_profile_id = 3 WHERE tmdb_id = 149")
    conn.commit()
    conn.close()
    db.add_movie_to_wishlist(149, "Akira")
    assert db.movie_wishlist_to_download(due_only=False)[0]["quality_profile_id"] == 3


def test_without_libraries_a_manual_grab_still_uses_upstreams_folder(tmp_path, monkeypatch):
    import api.video as videoapi
    db = VideoDatabase(database_path=str(tmp_path / "plain.db"))
    db.set_setting("movies_path", "/upstream/movies")
    videoapi._video_db = db
    monkeypatch.setattr("core.video.client_grab.grab",
                        lambda *a, **k: {"ok": True, "ref": "x", "category": k.get("category")})
    monkeypatch.setattr("core.video.download_monitor.ensure_started", lambda *a, **k: None)
    monkeypatch.setattr("core.video.disk_guard.free_gb", lambda p: 10000.0)
    app = Flask(__name__)
    app.register_blueprint(videoapi.create_video_blueprint(), url_prefix="/api/video")
    try:
        r = app.test_client().post("/api/video/downloads/grab", json={
            "kind": "movie", "title": "X", "source": "torrent", "media_id": 1,
            "download_url": "magnet:?xt=urn:btih:jkl"})
        assert r.status_code == 200, r.get_json()
        assert _target_dirs(db) == ["/upstream/movies"]
    finally:
        videoapi._video_db = None
