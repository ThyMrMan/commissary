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
    assert "filename='commissary-library-tabs.js'" in read("webui/index.html")


# ── Download History ─────────────────────────────────────────────────────────

def _grab(db, n, kind, title, *, dest=None, media_id=None, media_source="tmdb", status="completed",
          source="torrent"):
    db.record_download_history({
        "id": n, "kind": kind, "title": title, "dest_path": dest, "media_id": media_id,
        "media_source": media_source, "status": status, "source": source,
        "completed_at": f"2026-10-{n:02d} 12:00:00"})


@pytest.fixture()
def history(db):
    # filed: by the folder it landed in, the longest folder first
    _grab(db, 1, "movie", "Heat", dest="/m/Heat (1995)/Heat.mkv", media_id=949)
    _grab(db, 2, "movie", "Akira", dest=r"\am\Akira (1988)\Akira.mkv", media_id=1)  # path wins over the id
    _grab(db, 3, "episode", "Silo", dest="/t/Silo/Season 01/Silo - S01E01.mkv", media_id=9)
    _grab(db, 4, "episode", "Nana", dest="/a/Nana/Season 01/Nana - S01E01.mkv", media_id=31)
    _grab(db, 5, "episode", "Nana", dest="/ab/Not A Library/x.mkv", media_id=31)  # /a is not /ab - the title says Anime
    # no file: the title's library (owned, by tmdb or library row id), then a wish / follow
    _grab(db, 6, "movie", "Ran", status="failed", media_id=11645)
    _grab(db, 7, "movie", "Heat", status="failed", media_source="library",
          media_id=db_id(db, "movies", 949))
    conn = sqlite3.connect(str(db.database_path))
    conn.execute("INSERT INTO video_wishlist (kind, tmdb_id, title, profile_id, root_folder_id) "
                 "VALUES ('movie', 500, 'Paprika', 1, 2)")
    conn.execute("INSERT INTO video_watchlist (kind, tmdb_id, title, root_folder_id) "
                 "VALUES ('show', 600, 'Mushishi', 4)")
    conn.commit()
    conn.close()
    _grab(db, 8, "movie", "Paprika", status="failed", media_id=500)
    _grab(db, 9, "episode", "Mushishi", status="failed", media_id=600)
    _grab(db, 10, "movie", "Unknown", status="failed", media_id=777)  # no library at all
    _grab(db, 11, "youtube", "A video", dest="/m/yt/v.mkv", source="youtube")  # YouTube never has one
    return db


def db_id(db, table, tmdb):
    conn = sqlite3.connect(str(db.database_path))
    try:
        return conn.execute(f"SELECT id FROM {table} WHERE tmdb_id=?", (tmdb,)).fetchone()[0]
    finally:
        conn.close()


def _history(client, query):
    d = client.get("/api/video/downloads/history?limit=200&" + query).get_json()
    assert d["success"]
    return sorted((i["id"] and i["title"], i["outcome"]) for i in d["items"])


def test_the_history_narrows_to_one_library(client, history):
    assert _history(client, "kind=movie&library=1") == [("Heat", "completed"), ("Heat", "failed")]
    assert _history(client, "kind=movie&library=2") == [
        ("Akira", "completed"), ("Paprika", "failed"), ("Ran", "failed")]
    assert _history(client, "kind=show&library=3") == [("Silo", "completed")]
    assert _history(client, "kind=show&library=4") == [
        ("Mushishi", "failed"), ("Nana", "completed"), ("Nana", "completed")]
    everything = _history(client, "")
    assert len(everything) == 11, "no library chosen: upstream's list, unassigned rows included"
    assert _history(client, "kind=movie&library=junk") == _history(client, "kind=movie")


def test_history_tabs_count_completed_grabs_per_library(client, history):
    d = client.get("/api/video/commissary/library-tabs?scope=history").get_json()
    assert [(t["label"], t["count"]) for t in d["movie"]] == [("Movies", 1), ("Anime-Movies", 1)]
    assert [(t["label"], t["count"]) for t in d["show"]] == [("TV", 1), ("Anime", 2)]
    assert "path" not in str(d)
    # the owned-title counts are untouched without the scope
    plain = client.get("/api/video/commissary/library-tabs").get_json()
    assert [t["count"] for t in plain["movie"]] == [1, 2]
