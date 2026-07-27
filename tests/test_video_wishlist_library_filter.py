"""Wishlist page: filter by a specific configured Library (multi-library #1105
follow-up), not just the fixed Movies/TV/YouTube kind split.

``query_wishlist`` already carried ``library_id`` (the FK to the movie's/show's
own row) on every item for display, but nothing filtered ON it — the Movies/TV
tabs were the only scoping available, so a user with an Anime library separate
from their standard TV library had no way to see just its wishlist.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from flask import Flask

from database.video_database import VideoDatabase

_ROOT = Path(__file__).resolve().parent.parent
_WSH_JS = (_ROOT / "webui" / "static" / "video" / "video-wishlist.js").read_text(encoding="utf-8")
_INDEX = (_ROOT / "webui" / "index.html").read_text(encoding="utf-8")


@pytest.fixture()
def db(tmp_path):
    return VideoDatabase(database_path=str(tmp_path / "video_library.db"))


def _add_library(db, *, path, kind="movie", category=None, server="plex"):
    conn = db._get_connection()
    cur = conn.execute(
        "INSERT INTO root_folders (path, content_kind, server, category) VALUES (?,?,?,?)",
        (str(path), kind, server, category))
    rid = cur.lastrowid
    conn.commit(); conn.close()
    return rid


def test_movie_wishlist_scopes_to_one_library(db):
    anime = _add_library(db, path="/media/anime", category="anime")
    standard = _add_library(db, path="/media/movies")
    anime_movie_id = db.upsert_movie("plex", {"server_id": "m1", "tmdb_id": 1, "title": "Anime Film"})
    std_movie_id = db.upsert_movie("plex", {"server_id": "m2", "tmdb_id": 2, "title": "Regular Film"})
    conn = db._get_connection()
    conn.execute("UPDATE movies SET root_folder_id=? WHERE id=?", (anime, anime_movie_id))
    conn.execute("UPDATE movies SET root_folder_id=? WHERE id=?", (standard, std_movie_id))
    conn.commit(); conn.close()
    db.add_movie_to_wishlist(1, "Anime Film", year=2020, library_id=anime_movie_id)
    db.add_movie_to_wishlist(2, "Regular Film", year=2020, library_id=std_movie_id)

    anime_items = db.query_wishlist("movie", root_folder_id=anime)["items"]
    assert [i["title"] for i in anime_items] == ["Anime Film"]
    std_items = db.query_wishlist("movie", root_folder_id=standard)["items"]
    assert [i["title"] for i in std_items] == ["Regular Film"]


def test_show_wishlist_scopes_to_one_library(db):
    anime = _add_library(db, path="/media/anime-shows", kind="show", category="anime")
    show_id = db.upsert_show_tree("plex", {"server_id": "s1", "tmdb_id": 9, "title": "Anime Show"})
    other_show_id = db.upsert_show_tree("plex", {"server_id": "s2", "tmdb_id": 10, "title": "Normal Show"})
    conn = db._get_connection()
    conn.execute("UPDATE shows SET root_folder_id=? WHERE id=?", (anime, show_id))
    conn.commit(); conn.close()
    db.add_episodes_to_wishlist(9, "Anime Show",
                                [{"season_number": 1, "episode_number": 1}], library_id=show_id)
    db.add_episodes_to_wishlist(10, "Normal Show",
                                [{"season_number": 1, "episode_number": 1}], library_id=other_show_id)

    items = db.query_wishlist("show", root_folder_id=anime)["items"]
    assert [i["title"] for i in items] == ["Anime Show"]


def test_unlinked_items_dont_appear_under_a_specific_library_filter(db):
    anime = _add_library(db, path="/media/anime", category="anime")
    db.add_movie_to_wishlist(5, "Never Linked", year=2020)   # no library_id
    assert db.query_wishlist("movie", root_folder_id=anime)["items"] == []
    assert len(db.query_wishlist("movie", root_folder_id=None)["items"]) == 1   # unaffected


def test_library_filter_composes_with_search(db):
    anime = _add_library(db, path="/media/anime", category="anime")
    mid = db.upsert_movie("plex", {"server_id": "m1", "tmdb_id": 1, "title": "Anime Alpha"})
    mid2 = db.upsert_movie("plex", {"server_id": "m2", "tmdb_id": 2, "title": "Anime Beta"})
    conn = db._get_connection()
    conn.execute("UPDATE movies SET root_folder_id=? WHERE id IN (?,?)", (anime, mid, mid2))
    conn.commit(); conn.close()
    db.add_movie_to_wishlist(1, "Anime Alpha", year=2020, library_id=mid)
    db.add_movie_to_wishlist(2, "Anime Beta", year=2020, library_id=mid2)
    out = db.query_wishlist("movie", root_folder_id=anime, search="Beta")["items"]
    assert [i["title"] for i in out] == ["Anime Beta"]


def test_api_passes_root_folder_id_through(db):
    import api.video as videoapi
    videoapi._video_db = db
    try:
        anime = _add_library(db, path="/media/anime", category="anime")
        mid = db.upsert_movie("plex", {"server_id": "m1", "tmdb_id": 1, "title": "Anime Film"})
        conn = db._get_connection()
        conn.execute("UPDATE movies SET root_folder_id=? WHERE id=?", (anime, mid))
        conn.commit(); conn.close()
        db.add_movie_to_wishlist(1, "Anime Film", year=2020, library_id=mid)
        db.add_movie_to_wishlist(2, "Other Film", year=2020)   # unlinked

        app = Flask(__name__)
        app.register_blueprint(videoapi.create_video_blueprint(), url_prefix="/api/video")
        c = app.test_client()
        out = c.get("/api/video/wishlist?kind=movie&root_folder_id=%d" % anime).get_json()
        assert [i["title"] for i in out["items"]] == ["Anime Film"]
        out_all = c.get("/api/video/wishlist?kind=movie").get_json()
        assert len(out_all["items"]) == 2
    finally:
        videoapi._video_db = None


# ---------------------------------------------------------------------------
# Frontend contracts
# ---------------------------------------------------------------------------

def test_js_and_html_wire_the_library_filter():
    assert "data-vwsh-lib" in _INDEX
    assert "data-vwsh-lib" in _WSH_JS
    assert "root_folder_id" in _WSH_JS
    assert "/api/video/libraries" in _WSH_JS
