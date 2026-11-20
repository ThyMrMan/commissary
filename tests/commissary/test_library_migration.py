"""The fork's video Libraries and import locks, moved onto upstream's own
libraries and padlock once (core/commissary/library_migration.py).

Each test builds a real upstream database, then adds the columns and rows an
old fork database carries, and runs the move the way the next start does.
"""

from __future__ import annotations

import json

import pytest

from core.commissary import library_migration as lm
from database.video_database import VideoDatabase

FORK_COLS = ("label TEXT", "server_title TEXT", "server TEXT", "sort_order INTEGER",
             "category TEXT", "preferred_indexer_ids TEXT", "default_series_type TEXT",
             "default_quality_profile_id INTEGER")


@pytest.fixture()
def db(tmp_path):
    return VideoDatabase(database_path=str(tmp_path / "video_library.db"))


def _fork_db(db, libraries, *, settings=None):
    """an old fork database: the Library columns, its rows, and no markers yet"""
    conn = db._get_connection()
    have = {r[1] for r in conn.execute("PRAGMA table_info(root_folders)")}
    for col in FORK_COLS:
        if col.split()[0] not in have:
            conn.execute(f"ALTER TABLE root_folders ADD COLUMN {col}")
    for table in ("video_wishlist", "video_watchlist"):
        cols = {r[1] for r in conn.execute(f"PRAGMA table_info({table})")}
        if "root_folder_id" not in cols:
            conn.execute(f"ALTER TABLE {table} ADD COLUMN root_folder_id INTEGER")
    for table in ("movies", "shows"):
        cols = {r[1] for r in conn.execute(f"PRAGMA table_info({table})")}
        if "import_locked" not in cols:
            conn.execute(f"ALTER TABLE {table} ADD COLUMN import_locked INTEGER DEFAULT 0")
    ids = []
    for lib in libraries:
        cur = conn.execute(
            "INSERT INTO root_folders (path, content_kind, label, server_title, server, sort_order, "
            "category, preferred_indexer_ids, default_series_type, default_quality_profile_id) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (lib["path"], lib["kind"], lib.get("label"), lib.get("section"), lib.get("server", "plex"),
             lib.get("order", len(ids)), lib.get("category"), lib.get("indexers"),
             lib.get("series_type"), lib.get("profile")))
        ids.append(cur.lastrowid)
    conn.execute("DELETE FROM video_settings WHERE key IN (?, ?)", (lm.DONE_KEY, lm.LOCKS_KEY))
    for key, value in (settings or {}).items():
        conn.execute("INSERT OR REPLACE INTO video_settings (key, value) VALUES (?, ?)", (key, value))
    conn.commit()
    conn.close()
    return ids


def _run(db):
    conn = db._get_connection()
    try:
        out = lm.run(conn)
        moved = lm.move_locks(conn)
        conn.commit()
        return out, moved
    finally:
        conn.close()


def _rows(db, sql, args=()):
    conn = db._get_connection()
    try:
        return [tuple(r) for r in conn.execute(sql, args).fetchall()]
    finally:
        conn.close()


def _libraries(db, kind):
    return _rows(db, "SELECT name, path, builtin, options FROM video_libraries WHERE kind=? "
                     "ORDER BY position, id", (kind,))


LIBS = [
    {"kind": "movie", "path": "/media/Movies", "label": "Films", "section": "Movies",
     "category": "radarr", "indexers": "3,7", "profile": 2},
    {"kind": "movie", "path": "/media/Kids", "label": "Kids", "section": "Kids Movies",
     "category": "kids"},
    {"kind": "show", "path": "/media/TV", "label": "TV", "section": "TV Shows"},
    {"kind": "show", "path": "/media/Anime", "label": "Anime", "section": "Anime",
     "series_type": "anime"},
    {"kind": "show", "path": "/media/Docs", "label": "Docs", "section": "Documentaries"},
]


def test_the_first_library_becomes_the_main_one(db):
    _fork_db(db, LIBS)
    out, _ = _run(db)
    assert out == {"libraries": 5, "created": 3, "titles": 0}   # Kids, Anime, Docs
    name, path, builtin, options = _libraries(db, "movie")[0]
    assert (name, builtin) == ("Films", "main")
    opts = json.loads(options)
    assert opts["torrent_category"] == "radarr" and opts["indexer_ids"] == [3, 7]
    assert opts["quality_profile_id"] == 2
    assert db.get_setting("movies_path") == "/media/Movies", "an empty folder setting is filled"


def test_a_folder_already_set_is_kept(db):
    _fork_db(db, LIBS, settings={"movies_path": "/data/films"})
    _run(db)
    assert db.get_setting("movies_path") == "/data/films"


def test_the_others_become_custom_libraries(db):
    _fork_db(db, LIBS)
    _run(db)
    custom = [(n, p) for n, p, b, _o in _libraries(db, "movie") + _libraries(db, "show") if b is None]
    assert ("Kids", "/media/Kids") in custom and ("Docs", "/media/Docs") in custom
    kids = next(o for n, _p, _b, o in _libraries(db, "movie") if n == "Kids")
    assert json.loads(kids)["torrent_category"] == "kids"


def test_the_anime_folder_is_no_library_but_its_section_is_anime(db):
    _fork_db(db, LIBS, settings={"anime_path": "/media/Anime"})
    _run(db)
    assert all(p != "/media/Anime" for _n, p, _b, _o in _libraries(db, "show"))
    assert json.loads(db.get_setting("plex.anime_libraries")) == ["Anime"]


def test_an_anime_library_elsewhere_still_marks_its_section(db):
    _fork_db(db, LIBS)
    _run(db)
    assert any(p == "/media/Anime" for _n, p, _b, _o in _libraries(db, "show"))
    assert json.loads(db.get_setting("plex.anime_libraries")) == ["Anime"]


def test_every_section_is_scanned(db):
    _fork_db(db, LIBS)
    _run(db)
    assert db.get_setting("plex.movies_library") == "Movies"
    assert json.loads(db.get_setting("plex" + lm.MOVIES_MORE)) == ["Kids Movies"]
    assert db.get_setting("plex.tv_library") == "TV Shows"
    assert json.loads(db.get_setting("plex.tv_libraries_more")) == ["Anime", "Documentaries"]
    sel = lm.movie_sections(db, "plex", {"movies": "Movies", "tv": "TV Shows"})
    assert sel["movies"] == ["Movies", "Kids Movies"]


def test_a_section_picked_already_stays_the_main_one(db):
    _fork_db(db, LIBS, settings={"plex.tv_library": "Documentaries"})
    _run(db)
    assert db.get_setting("plex.tv_library") == "Documentaries"
    assert json.loads(db.get_setting("plex.tv_libraries_more")) == ["TV Shows", "Anime"]


def test_titles_keep_their_library(db):
    ids = _fork_db(db, LIBS)
    kids, tv, docs = ids[1], ids[2], ids[4]
    conn = db._get_connection()
    conn.execute("INSERT INTO movies (server_source, server_id, title, tmdb_id, root_folder_id) "
                 "VALUES ('plex', 'm1', 'Paddington', 116149, ?)", (kids,))
    conn.execute("INSERT INTO shows (server_source, server_id, title, tmdb_id, root_folder_id) "
                 "VALUES ('plex', 's1', 'Silo', 125988, ?)", (tv,))       # the main: nothing to pin
    conn.execute("INSERT INTO video_wishlist (kind, tmdb_id, title, root_folder_id) "
                 "VALUES ('episode', 87108, 'Chernobyl', ?)", (docs,))
    conn.execute("INSERT INTO video_watchlist (kind, tmdb_id, title, root_folder_id) "
                 "VALUES ('show', 1399, 'Planet Earth', ?)", (docs,))
    conn.commit()
    conn.close()
    out, _ = _run(db)
    assert out["titles"] == 3
    pinned = _rows(db, "SELECT t.kind, t.tmdb_id, l.name FROM video_title_libraries t "
                       "JOIN video_libraries l ON l.id = t.library_id ORDER BY t.tmdb_id")
    assert pinned == [("show", 1399, "Docs"), ("show", 87108, "Docs"), ("movie", 116149, "Kids")]


def test_it_runs_once(db):
    _fork_db(db, LIBS)
    _run(db)
    before = _libraries(db, "show")
    assert _run(db) == (None, 0)
    assert _libraries(db, "show") == before


def test_a_database_without_fork_libraries_is_left_alone(db):
    before = _libraries(db, "movie")
    conn = db._get_connection()
    try:
        assert lm.run(conn) is None
    finally:
        conn.close()
    assert _libraries(db, "movie") == before


def test_a_title_lock_becomes_the_padlock(db):
    _fork_db(db, [])
    conn = db._get_connection()
    conn.execute("INSERT INTO movies (server_source, server_id, title, tmdb_id, import_locked) "
                 "VALUES ('plex', 'm1', 'Heat', 949, 1)")
    conn.execute("INSERT INTO movies (server_source, server_id, title, tmdb_id) "
                 "VALUES ('plex', 'm2', 'Ronin', 8195)")
    conn.commit()
    conn.close()
    _out, moved = _run(db)
    assert moved == 1
    assert _rows(db, "SELECT title, hands_off FROM movies ORDER BY title") == [("Heat", 1), ("Ronin", 0)]


def test_a_new_database_runs_the_move_on_start(tmp_path):
    """the hook in VideoDatabase's init: a fresh install just records it done"""
    db = VideoDatabase(database_path=str(tmp_path / "fresh.db"))
    assert db.get_setting(lm.DONE_KEY) == "1"
    assert db.get_setting(lm.LOCKS_KEY) == "1"
