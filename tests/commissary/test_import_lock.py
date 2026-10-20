"""Lock automatic edits: no unattended import into a locked show, season or movie."""

from __future__ import annotations

import json
import sqlite3

import pytest
from flask import Flask

from core.commissary import import_lock as il
from database.video_database import VideoDatabase


@pytest.fixture()
def db(tmp_path):
    db = VideoDatabase(database_path=str(tmp_path / "video_library.db"))
    db.upsert_movie("plex", {"server_id": "m1", "title": "The Matrix", "year": 1999, "tmdb_id": 603})
    db.upsert_show_tree("plex", {"server_id": "s1", "title": "Silo", "tmdb_id": 9, "seasons": [
        {"season_number": s, "episodes": [{"episode_number": 1, "server_id": "e%d" % s}]}
        for s in (1, 2)]})
    lib = tmp_path / "lib"
    lib.mkdir()
    db.set_setting("organization", json.dumps({
        "transfer_mode": "copy", "verify_with_ffprobe": False, "save_artwork": False,
        "write_nfo": False, "download_subtitles": False, "carry_subtitles": False}))
    db.set_setting("movies_path", str(lib / "movies"))
    db.set_setting("tv_path", str(lib / "tv"))
    return db


def _id(db, table, tmdb):
    conn = sqlite3.connect(str(db.database_path))
    try:
        return conn.execute(f"SELECT id FROM {table} WHERE tmdb_id=?", (tmdb,)).fetchone()[0]
    finally:
        conn.close()


# ── the verdict ─────────────────────────────────────────────────────────────

def test_nothing_is_locked_by_default(db):
    assert il.reason(db, "movie", 603) is None
    assert il.reason(db, "episode", 9, 2) is None


def test_a_show_lock_covers_every_season_and_a_season_lock_only_its_own(db):
    sid = _id(db, "shows", 9)
    assert il.set_season_lock(db, sid, 2, True)
    assert "season 2 is locked" in il.reason(db, "episode", 9, 2)
    assert il.reason(db, "episode", 9, 1) is None
    il.set_lock(db, "show", sid, True)
    assert "Silo is locked" in il.reason(db, "episode", 9, 1)


def test_a_title_not_in_the_library_cannot_be_locked(db):
    assert il.reason(db, "movie", 999) is None and il.reason(db, "episode", 999, 1) is None


def test_the_old_forks_rows_carry_over(db):
    """Same columns: a season locked in the old fork is locked here."""
    conn = sqlite3.connect(str(db.database_path))
    conn.execute("UPDATE seasons SET import_locked=1 WHERE season_number=1")
    conn.commit()
    conn.close()
    assert il.reason(db, "episode", 9, 1)


def test_a_rescan_keeps_a_season_lock(db):
    il.set_season_lock(db, _id(db, "shows", 9), 2, True)
    db.upsert_show_tree("plex", {"server_id": "s1", "title": "Silo", "tmdb_id": 9, "seasons": [
        {"season_number": s, "episodes": [{"episode_number": 1, "server_id": "e%d" % s}]}
        for s in (1, 2)]})
    assert il.reason(db, "episode", 9, 2)


def test_a_failed_lookup_fails_open():
    class Broken:
        def _get_connection(self):
            raise RuntimeError("db down")
    assert il.reason(Broken(), "movie", 603) is None
    assert il.refusal(Broken(), {"kind": "movie", "media_id": 603, "media_source": "tmdb"}) is None


# ── the monitor ─────────────────────────────────────────────────────────────

def _movie_dl():
    return {"id": 7, "kind": "movie", "title": "The Matrix", "year": 1999, "source": "torrent",
            "media_id": 603, "media_source": "tmdb", "release_title": "The.Matrix.1999.1080p.BluRay",
            "size_bytes": 100, "search_ctx": json.dumps({"scope": "movie", "title": "The Matrix",
                                                         "year": 1999})}


def _episode_dl(season):
    return {"id": 8, "kind": "show", "title": "Silo", "source": "torrent", "media_id": 9,
            "media_source": "tmdb", "release_title": "Silo.S%02dE03.1080p.WEB-NTb" % season,
            "size_bytes": 100, "search_ctx": json.dumps({"scope": "episode", "title": "Silo",
                                                         "season": season, "episode": 3})}


def _organize(db, tmp_path, dl, name):
    from core.video.download_monitor import _make_organizer
    src = tmp_path / "dl" / name
    src.parent.mkdir(exist_ok=True)
    src.write_bytes(b"x" * 100)
    dl = dict(dl, target_dir=str(tmp_path / "lib" / ("movies" if dl["kind"] == "movie" else "tv")))
    return _make_organizer(db)(dl, str(src)), src


def test_a_locked_movie_refuses_the_import_and_leaves_the_file(db, tmp_path):
    il.set_lock(db, "movie", _id(db, "movies", 603), True)
    patch, src = _organize(db, tmp_path, _movie_dl(), "The.Matrix.1999.1080p.BluRay.mkv")
    assert patch["status"] == "import_failed"
    assert "The Matrix is locked" in patch["error"] and "Place it by hand" in patch["error"]
    assert not patch.get("_bad_release"), "not blocklisted: the release may be fine"
    assert patch["dest_path"] == str(src) and src.exists()
    assert not (tmp_path / "lib" / "movies").exists(), "no folder named inside it"


def test_an_unlocked_movie_imports_as_upstream_does(db, tmp_path):
    patch, _src = _organize(db, tmp_path, _movie_dl(), "The.Matrix.1999.1080p.BluRay.mkv")
    assert patch["status"] == "completed", patch


def test_a_season_lock_refuses_its_own_season_only(db, tmp_path):
    il.set_season_lock(db, _id(db, "shows", 9), 2, True)
    locked, _ = _organize(db, tmp_path, _episode_dl(2), "Silo.S02E03.1080p.WEB-NTb.mkv")
    assert locked["status"] == "import_failed" and "season 2 is locked" in locked["error"]
    open_, _ = _organize(db, tmp_path, _episode_dl(1), "Silo.S01E03.1080p.WEB-NTb.mkv")
    assert open_["status"] == "completed", open_


def test_manual_import_is_not_gated(db, tmp_path):
    """Placing it by hand is the check the lock asks for."""
    from core.video import importer
    il.set_lock(db, "movie", _id(db, "movies", 603), True)
    src = tmp_path / "m.mkv"
    src.write_bytes(b"x" * 100)
    patch = importer.run_import(_movie_dl(), str(src), fs=importer.real_fs(), force=True,
                                override={"scope": "movie", "title": "The Matrix", "year": 1999,
                                          "target_dir": str(tmp_path / "lib" / "movies")})
    assert patch["status"] == "completed", patch


# ── searching for it ────────────────────────────────────────────────────────

def test_the_automatic_search_skips_locked_targets(db, monkeypatch):
    import api.video as videoapi
    from core.automation.handlers import video_process_wishlist as vpw
    db.add_movie_to_wishlist(603, "The Matrix", year=1999)
    db.add_episodes_to_wishlist(9, "Silo", [{"season_number": s, "episode_number": 3,
                                             "air_date": "2025-01-01"} for s in (1, 2)])
    videoapi._video_db = db
    try:
        monkeypatch.setattr("core.video.numbering.annotate", lambda items, db=None: items)
        il.set_lock(db, "movie", _id(db, "movies", 603), True)
        il.set_season_lock(db, _id(db, "shows", 9), 2, True)
        movies = vpw._default_fetch_items("movie")
        eps = vpw._default_fetch_items("episode")
    finally:
        videoapi._video_db = None
    assert movies == []
    assert [(e["season_number"], e["episode_number"]) for e in eps] == [(1, 3)]


def test_rss_skips_them_too(db):
    from core.commissary.season_packs import rss_items
    il.set_season_lock(db, _id(db, "shows", 9), 2, True)
    items = [{"show_tmdb_id": 9, "show_title": "Silo", "season_number": s, "episode_number": 3}
             for s in (1, 2)]
    assert [i["season_number"] for i in rss_items(items, set(), "episode", db=db)] == [1]


# ── the endpoints ───────────────────────────────────────────────────────────

@pytest.fixture()
def client(db):
    import api.video as videoapi
    videoapi._video_db = db
    app = Flask(__name__)
    app.register_blueprint(videoapi.create_video_blueprint(), url_prefix="/api/video")
    try:
        yield app.test_client()
    finally:
        videoapi._video_db = None


def test_lock_and_read_back(client, db):
    sid = _id(db, "shows", 9)
    assert client.post("/api/video/libraries/show/%d/season/2/import-lock" % sid,
                       json={"locked": True}).status_code == 200
    assert client.get("/api/video/libraries/show/%d/import-lock" % sid).get_json() \
        == {"locked": False, "seasons": [2]}
    mid = _id(db, "movies", 603)
    assert client.post("/api/video/libraries/movie/%d/import-lock" % mid, json={"locked": True}).status_code == 200
    assert client.get("/api/video/libraries/movie/%d/import-lock" % mid).get_json()["locked"] is True
    assert client.post("/api/video/libraries/movie/%d/import-lock" % mid, json={"locked": "yes"}).status_code == 400
    assert client.post("/api/video/libraries/show/%d/season/9/import-lock" % sid,
                       json={"locked": True}).status_code == 404


def test_locks_are_admin_only(client, db):
    from flask import g

    @client.application.before_request
    def _member():
        g.is_admin = False

    sid = _id(db, "shows", 9)
    assert client.get("/api/video/libraries/show/%d/import-lock" % sid).status_code == 403
    assert client.post("/api/video/libraries/show/%d/import-lock" % sid, json={"locked": True}).status_code == 403


def test_the_page_loads_it_and_rereads_past_the_dedupe():
    """upstream's fetch-dedupe.js answers an identical /api GET within 2.5s with
    the earlier response, so a re-read straight after a save must use a new URL."""
    from pathlib import Path
    root = Path(__file__).resolve().parents[2]
    read = lambda rel: (root / rel).read_text(encoding="utf-8")  # noqa: E731
    assert "filename='commissary-import-lock.js'" in read("webui/index.html")
    assert "data-commissary-season=" in read("webui/static/video/video-detail.js")
    for rel in ("webui/static/commissary-import-lock.js", "webui/static/commissary-episode-numbering.js",
                "webui/static/commissary-title-setup.js", "webui/static/commissary-video-libraries.js",
                "webui/static/commissary-video-library-pickers.js"):
        assert "t=' + Date.now()" in read(rel), rel
