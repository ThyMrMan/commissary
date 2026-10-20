"""Library tabs on the Library page and the Wishlist."""

from __future__ import annotations

import sqlite3

import pytest
from flask import Flask

from database.video_database import VideoDatabase


@pytest.fixture()
def db(tmp_path, monkeypatch):
    import api.video as videoapi
    d = VideoDatabase(database_path=str(tmp_path / "video_library.db"))
    videoapi._video_db = d
    monkeypatch.setattr("core.video.sources.resolve_video_server", lambda *a, **k: "plex")
    conn = sqlite3.connect(str(d.database_path))
    for i, (path, kind, label) in enumerate([("/m", "movie", "Movies"), ("/am", "movie", "Anime-Movies"),
                                             ("/t", "show", "TV"), ("/a", "show", "Anime")], 1):
        conn.execute("INSERT INTO root_folders (id, path, content_kind, server, label) VALUES (?,?,?,?,?)",
                     (i, path, kind, "plex", label))
    conn.commit()
    conn.close()
    for sid, title, tmdb, lib in (("m1", "Heat", 949, 1), ("m2", "Akira", 149, 2), ("m3", "Ran", 11645, 2)):
        d.upsert_movie("plex", {"server_id": sid, "title": title, "tmdb_id": tmdb})
        _set(d, "movies", tmdb, lib)
    d.upsert_show_tree("plex", {"server_id": "s1", "title": "Silo", "tmdb_id": 9, "seasons": []})
    _set(d, "shows", 9, 3)
    d.upsert_show_tree("plex", {"server_id": "s2", "title": "Nana", "tmdb_id": 31, "seasons": []})
    _set(d, "shows", 31, 4)
    yield d
    videoapi._video_db = None


def _set(db, table, tmdb, lib):
    conn = sqlite3.connect(str(db.database_path))
    conn.execute(f"UPDATE {table} SET root_folder_id=? WHERE tmdb_id=?", (lib, tmdb))
    conn.commit()
    conn.close()


@pytest.fixture()
def client(db):
    import api.video as videoapi
    app = Flask(__name__)
    app.register_blueprint(videoapi.create_video_blueprint(), url_prefix="/api/video")
    return app.test_client()


def _titles(d):
    return sorted(i["title"] for i in d.get("items") or [])


def test_the_library_page_narrows_to_one_library(client):
    assert _titles(client.get("/api/video/library?kind=movies").get_json()) == ["Akira", "Heat", "Ran"]
    assert _titles(client.get("/api/video/library?kind=movies&library=2").get_json()) == ["Akira", "Ran"]
    assert _titles(client.get("/api/video/library?kind=shows&library=4").get_json()) == ["Nana"]
    assert _titles(client.get("/api/video/library?kind=movies&library=junk").get_json()) == \
        ["Akira", "Heat", "Ran"], "junk is ignored, not an empty page"


def test_tabs_list_each_kinds_libraries_with_counts_and_no_paths(client):
    d = client.get("/api/video/commissary/library-tabs").get_json()
    assert d["movie"] == [{"id": 1, "label": "Movies", "count": 1},
                          {"id": 2, "label": "Anime-Movies", "count": 2}]
    assert [(t["label"], t["count"]) for t in d["show"]] == [("TV", 1), ("Anime", 1)]
    assert "path" not in str(d)


def test_one_library_has_nothing_to_split(client, db):
    conn = sqlite3.connect(str(db.database_path))
    conn.execute("DELETE FROM root_folders WHERE id=4")
    conn.commit()
    conn.close()
    assert client.get("/api/video/commissary/library-tabs").get_json()["show"] == []


def test_a_member_profile_can_read_the_tabs_but_not_the_registry(client):
    from flask import g

    @client.application.before_request
    def _member():
        g.is_admin = False

    assert client.get("/api/video/commissary/library-tabs").status_code == 200
    assert client.get("/api/video/libraries/registry").status_code == 403


def test_the_wishlist_narrows_by_each_rows_library(client, db):
    db.add_movie_to_wishlist(149, "Akira", year=1988)          # owned in Anime-Movies
    db.add_movie_to_wishlist(500, "New Film", year=2026)       # unowned, no library chosen
    db.add_episodes_to_wishlist(31, "Nana", [{"season_number": 1, "episode_number": e} for e in (1, 2)])
    db.add_episodes_to_wishlist(9, "Silo", [{"season_number": 2, "episode_number": 1}])
    wl = lambda q: _titles(client.get("/api/video/wishlist?" + q).get_json())  # noqa: E731
    assert wl("kind=movie&library=2") == ["Akira"]
    assert wl("kind=movie") == ["Akira", "New Film"]
    shows = client.get("/api/video/wishlist?kind=show&library=4").get_json()
    assert [i.get("title") or i.get("show_title") for i in shows["items"]] == ["Nana"]
    counts = client.get("/api/video/commissary/library-tabs?scope=wishlist").get_json()
    assert {t["label"]: t["count"] for t in counts["show"]} == {"TV": 1, "Anime": 1}, "shows, not episodes"
    assert {t["label"]: t["count"] for t in counts["movie"]} == {"Movies": 0, "Anime-Movies": 1}


def test_the_pages_hand_their_tab_to_the_script():
    from pathlib import Path
    root = Path(__file__).resolve().parents[2]
    read = lambda rel: (root / rel).read_text(encoding="utf-8")  # noqa: E731
    assert "CommissaryLibraryTabs.apply(params, apiKind)" in read("webui/static/video/video-library.js")
    assert "CommissaryLibraryTabs.applyWishlist(params, state.tab)" in read("webui/static/video/video-wishlist.js")
    assert "filename='commissary-library-tabs.js'" in read("webui/index.html")
