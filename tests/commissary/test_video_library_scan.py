"""Scanning reads every video Library's section and records where each title is.

Upstream scans one section per kind ("Movies", "TV Shows"). On an install with
an Anime-Movies library, a deep scan of "Movies" alone would prune every anime
movie as missing: 195 of 896 rows is under upstream's 50% prune guard.
"""

import sqlite3
from types import SimpleNamespace

import pytest

from core.commissary import video_libraries as vl
from core.video.scanner import VideoLibraryScanner
from database.video_database import VideoDatabase


@pytest.fixture()
def db(tmp_path):
    db = VideoDatabase(database_path=str(tmp_path / "video_library.db"))
    vl.save_libraries(db, "plex",
                      movies=[{"server_title": "Movies", "path": "/m/movies"},
                              {"server_title": "Anime-Movies", "path": "/m/anime-movies"}],
                      tv=[{"server_title": "TV Shows", "path": "/m/tv"},
                          {"server_title": "Anime", "path": "/m/anime",
                           "default_series_type": "anime"}])
    return db


def _ids(db):
    libs = vl.list_libraries(db, "plex")
    return {r["server_title"]: r["id"] for k in libs for r in libs[k]}


def _q(db, sql):
    conn = sqlite3.connect(str(db.database_path))
    try:
        return conn.execute(sql).fetchall()
    finally:
        conn.close()


def _movie(sid, title, section):
    return vl.tag({"server_id": sid, "title": title,
                   "file": {"relative_path": f"{title}.mkv", "size_bytes": 5}}, section)


def _show(sid, title, section):
    return vl.tag({"server_id": sid, "title": title, "seasons": [
        {"season_number": 1, "episodes": [
            {"server_id": sid + "e1", "episode_number": 1, "title": "E1",
             "file": {"relative_path": "e1.mkv"}}]}]}, section)


class Source:
    """What a Plex source yields once it reads every library's section."""
    server_name = "plex"

    def __init__(self, movies, shows):
        self._m, self._s = movies, shows

    def iter_movies(self, incremental=False, since=None):
        return iter(self._m)

    def iter_shows(self, incremental=False, since=None):
        return iter(self._s)


def test_a_deep_scan_keeps_every_library_and_records_each_titles_library(db):
    movies = [_movie(f"m{i}", f"Film {i}", "Movies") for i in range(8)] + \
             [_movie(f"a{i}", f"Anime Film {i}", "Anime-Movies") for i in range(2)]
    shows = [_show("s1", "Drama", "TV Shows"), _show("s2", "Bleach", "Anime")]
    scanner = VideoLibraryScanner(db)
    scanner.scan_sync(lambda: Source(movies, shows), mode="deep")
    st = scanner.scan_sync(lambda: Source(movies, shows), mode="deep")
    assert st["state"] == "done"
    ids = _ids(db)
    by_lib = dict(_q(db, "SELECT root_folder_id, COUNT(*) FROM movies GROUP BY 1"))
    assert by_lib == {ids["Movies"]: 8, ids["Anime-Movies"]: 2}
    shows_lib = dict(_q(db, "SELECT title, root_folder_id FROM shows"))
    assert shows_lib == {"Drama": ids["TV Shows"], "Bleach": ids["Anime"]}
    assert dict(_q(db, "SELECT title, series_type FROM shows"))["Bleach"] == "anime"


def test_a_title_moved_between_libraries_on_the_server_follows(db):
    scanner = VideoLibraryScanner(db)
    scanner.scan_sync(lambda: Source([_movie("m1", "Akira", "Movies")], []), mode="full")
    scanner.scan_sync(lambda: Source([_movie("m1", "Akira", "Anime-Movies")], []), mode="full")
    assert _q(db, "SELECT root_folder_id FROM movies") == [(_ids(db)["Anime-Movies"],)]


def test_untagged_items_keep_whatever_library_they_had(db):
    """show_tree / a single-title refresh yields no section; that must not
    unassign anything."""
    scanner = VideoLibraryScanner(db)
    scanner.scan_sync(lambda: Source([_movie("m1", "Akira", "Anime-Movies")], []), mode="full")
    scanner.scan_sync(lambda: Source([{"server_id": "m1", "title": "Akira"}], []), mode="full")
    assert _q(db, "SELECT root_folder_id FROM movies") == [(_ids(db)["Anime-Movies"],)]


def test_the_scan_selection_is_every_librarys_section(db):
    sel = vl.scan_selection(db, "plex", {"movies": "Movies", "tv": "TV Shows"})
    assert sel == {"movies": ["Movies", "Anime-Movies"], "tv": ["TV Shows", "Anime"]}


def test_without_libraries_the_selection_is_upstreams(tmp_path):
    empty = VideoDatabase(database_path=str(tmp_path / "empty.db"))
    sel = {"movies": "Movies", "tv": "TV Shows"}
    assert vl.scan_selection(empty, "plex", sel) == sel


def test_sources_load_the_registry_selection(db, monkeypatch):
    import core.video.sources as sources
    import database.video_database as vdb
    monkeypatch.setattr(sources, "resolve_video_server", lambda *a, **k: "plex")
    monkeypatch.setattr(vdb, "VideoDatabase", lambda *a, **k: db)
    assert sources._load_selection()["movies"] == ["Movies", "Anime-Movies"]


def _plex(sections):
    server = SimpleNamespace(library=SimpleNamespace(sections=lambda: sections))
    from core.video.sources import PlexVideoSource
    return PlexVideoSource(server, movies_lib=["Movies", "Anime-Movies"], tv_lib=["Anime"])


def test_plex_scans_every_listed_section_and_only_those():
    secs = [SimpleNamespace(type="movie", title=t) for t in ("Movies", "Anime-Movies", "4K")] + \
           [SimpleNamespace(type="show", title=t) for t in ("TV Shows", "Anime")]
    src = _plex(secs)
    assert [s.title for s in src._scan_sections("movie", src._movies_lib)] == ["Movies", "Anime-Movies"]
    assert [s.title for s in src._scan_sections("show", src._tv_lib)] == ["Anime"]
    assert [s.title for s in src._sections("movie", "4K")] == ["4K"], "a single name still works"


def test_plex_tags_scanned_movies_with_their_section(monkeypatch):
    m = SimpleNamespace(title="Akira")
    sec = SimpleNamespace(type="movie", title="Anime-Movies", all=lambda: [m])
    src = _plex([sec])
    monkeypatch.setattr(src, "_movie", lambda obj: {"server_id": "1", "title": obj.title})
    items = list(src.iter_movies())
    assert items[0][vl.SECTION_KEY] == "Anime-Movies"


def test_jellyfin_views_accept_a_list():
    from core.video.sources import JellyfinVideoSource
    src = JellyfinVideoSource.__new__(JellyfinVideoSource)
    src.uid = "u"
    src._req = lambda path: {"Items": [{"Name": "Movies", "CollectionType": "movies"},
                                       {"Name": "Anime-Movies", "CollectionType": "movies"},
                                       {"Name": "Other", "CollectionType": "movies"}]}
    assert [v["Name"] for v in src._views("movies", ["Movies", "Anime-Movies"])] == \
        ["Movies", "Anime-Movies"]
