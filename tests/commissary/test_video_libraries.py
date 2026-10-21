"""The video Libraries registry: several libraries per kind, each a server
section plus its folder, category, trackers and defaults."""

import sqlite3

import pytest

from core.commissary import video_libraries as vl
from database.video_database import VideoDatabase


@pytest.fixture()
def db(tmp_path):
    return VideoDatabase(str(tmp_path / "video.db"))


def _exec(db, sql, params=()):
    conn = sqlite3.connect(str(db.database_path))
    try:
        cur = conn.execute(sql, params)
        conn.commit()
        return cur.lastrowid
    finally:
        conn.close()


def _seed(db):
    """The shape of the real install: two movie and two show libraries."""
    vl.save_libraries(db, "plex", movies=[
        {"server_title": "Movies", "label": "Movies", "path": "/m/movies", "category": "Movies",
         "preferred_indexer_ids": "3,7"},
        {"server_title": "Anime-Movies", "path": "/m/anime-movies", "category": "Anime-Movies",
         "preferred_indexer_ids": "1"},
    ], tv=[
        {"server_title": "TV Shows", "path": "/m/tv", "category": "TV-Shows",
         "default_series_type": "standard"},
        {"server_title": "Anime", "path": "/m/anime", "category": "Anime",
         "preferred_indexer_ids": "1, 7,x,9", "default_series_type": "anime",
         "default_quality_profile_id": 1},
    ])
    libs = vl.list_libraries(db, "plex")
    return {r["server_title"]: r["id"] for k in libs for r in libs[k]}


def test_the_columns_exist_on_a_fresh_install(db):
    conn = sqlite3.connect(str(db.database_path))
    try:
        for table, col, _ in vl.COLUMNS:
            assert col in {r[1] for r in conn.execute(f"PRAGMA table_info({table})")}, (table, col)
    finally:
        conn.close()


def test_an_empty_registry_has_no_opinion(db):
    """Without libraries every answer is 'nothing', so upstream's own
    movies_path / tv_path behaviour is untouched."""
    assert not vl.has_libraries(db)
    assert vl.library_for_item(db, "movie", tmdb_id=1) is None
    assert vl.target_dir(db, "episode", tmdb_id=1) == ""
    assert vl.category(db, "movie") is None
    assert vl.indexer_ids(db, "movie") is None
    assert vl.section_titles(db, "plex", "movies") == []


def test_libraries_list_scan_in_order_and_the_first_is_primary(db):
    _seed(db)
    assert vl.section_titles(db, "plex", "movies") == ["Movies", "Anime-Movies"]
    assert vl.section_titles(db, "plex", "tv") == ["TV Shows", "Anime"]
    assert vl.primary(db, "movie")["server_title"] == "Movies"
    assert vl.primary(db, "episode")["server_title"] == "TV Shows"


def test_an_owned_title_goes_back_to_its_own_library(db):
    ids = _seed(db)
    sid = _exec(db, "INSERT INTO shows (title, tmdb_id, server_source, root_folder_id) "
                    "VALUES ('Bleach', 30984, 'plex', ?)", (ids["Anime"],))
    assert sid
    assert vl.target_dir(db, "episode", tmdb_id=30984) == "/m/anime"
    assert vl.category(db, "episode", tmdb_id=30984) == "Anime"
    assert vl.indexer_ids(db, "episode", tmdb_id=30984) == [1, 7, 9]


def test_an_unknown_title_goes_to_the_primary_library(db):
    _seed(db)
    assert vl.target_dir(db, "movie", tmdb_id=424242) == "/m/movies"
    assert vl.category(db, "movie", tmdb_id=424242) == "Movies"


def test_an_explicit_choice_and_a_wishlist_row_win_over_primary(db):
    ids = _seed(db)
    assert vl.target_dir(db, "movie", root_folder_id=ids["Anime-Movies"]) == "/m/anime-movies"
    item = {"tmdb_id": 5, "root_folder_id": ids["Anime-Movies"]}
    assert vl.target_dir(db, "movie", item=item) == "/m/anime-movies"


def test_a_choice_of_the_wrong_kind_or_a_deleted_library_falls_through(db):
    ids = _seed(db)
    assert vl.target_dir(db, "movie", root_folder_id=ids["Anime"]) == "/m/movies"
    assert vl.target_dir(db, "movie", root_folder_id=9999) == "/m/movies"


def test_a_followed_show_goes_where_it_was_followed_to(db):
    ids = _seed(db)
    _exec(db, "INSERT INTO video_watchlist (kind, tmdb_id, title) VALUES ('show', 77, 'X')")
    assert vl.set_watchlist_library(db, 77, ids["Anime"]) == 1
    assert vl.target_dir(db, "episode", tmdb_id=77) == "/m/anime"


def test_setting_a_wishlist_library_rejects_the_wrong_kind(db):
    ids = _seed(db)
    assert vl.set_wishlist_library(db, "movie", 5, ids["Anime"]) == 0


def test_saving_keeps_ids_and_removing_a_library_unassigns_its_titles(db):
    ids = _seed(db)
    _exec(db, "INSERT INTO movies (title, tmdb_id, server_source, root_folder_id) "
              "VALUES ('Akira', 149, 'plex', ?)", (ids["Anime-Movies"],))
    libs = vl.list_libraries(db, "plex")
    vl.save_libraries(db, "plex", movies=[libs["movies"][0]])   # drop Anime-Movies
    assert vl.section_titles(db, "plex", "movies") == ["Movies"]
    assert vl.list_libraries(db, "plex")["movies"][0]["id"] == ids["Movies"]
    assert vl.owned_root_folder_id(db, "movie", 149) is None
    assert vl.section_titles(db, "plex", "tv") == ["TV Shows", "Anime"], "tv untouched"


def test_a_kind_left_out_of_a_save_is_left_alone(db):
    _seed(db)
    vl.save_libraries(db, "plex", youtube=[{"server_title": "YT", "path": "/m/yt"}])
    vl.save_libraries(db, "plex", movies=vl.list_libraries(db, "plex")["movies"])
    assert [r["server_title"] for r in vl.list_libraries(db, "plex")["youtube"]] == ["YT"]


def test_library_series_type_applies_only_to_unset_shows(db):
    ids = _seed(db)
    _exec(db, "INSERT INTO shows (title, tmdb_id, server_source, root_folder_id) "
              "VALUES ('A', 1, 'plex', ?)", (ids["Anime"],))
    _exec(db, "INSERT INTO shows (title, tmdb_id, server_source, root_folder_id, series_type) "
              "VALUES ('B', 2, 'plex', ?, 'daily')", (ids["Anime"],))
    vl.apply_default_series_type(db)
    conn = sqlite3.connect(str(db.database_path))
    try:
        types = dict(conn.execute("SELECT title, series_type FROM shows").fetchall())
    finally:
        conn.close()
    assert types == {"A": "anime", "B": "daily"}


def test_for_path_picks_the_innermost_library(db):
    _seed(db)
    vl.save_libraries(db, "plex", youtube=[{"server_title": "Inner", "path": "/m/movies/inner"}])
    assert vl.for_path(db, "/m/movies/inner/X (2000)")["server_title"] == "Inner"
    assert vl.for_path(db, "/m/movies/Y (2001)")["server_title"] == "Movies"
    assert vl.for_path(db, "/elsewhere") is None


def test_the_watchlist_column_survives_upstreams_v49_rebuild(tmp_path):
    """v49 rebuilds video_watchlist with a fixed column list; the registry
    column must still be there after a legacy-shaped DB is opened."""
    path = tmp_path / "legacy.db"
    VideoDatabase(str(path))
    conn = sqlite3.connect(str(path))
    try:
        conn.execute("DROP TABLE video_watchlist")
        conn.execute("CREATE TABLE video_watchlist (id INTEGER PRIMARY KEY, kind TEXT NOT NULL, "
                     "tmdb_id INTEGER NOT NULL, title TEXT NOT NULL, poster_url TEXT, "
                     "library_id INTEGER, source TEXT NOT NULL DEFAULT 'tmdb', source_id TEXT, "
                     "state TEXT NOT NULL DEFAULT 'follow', date_added TEXT, lookback_years INTEGER, "
                     "root_folder_id INTEGER, UNIQUE(kind, tmdb_id))")
        conn.execute("PRAGMA user_version = 47")
        conn.commit()
    finally:
        conn.close()
    import database.video_database as vdb
    for key in [k for k in vdb._initialized_paths if "legacy.db" in str(k)]:
        vdb._initialized_paths.discard(key)     # let this process run init again
    VideoDatabase(str(path))
    conn = sqlite3.connect(str(path))
    try:
        cols = [r[1] for r in conn.execute("PRAGMA table_info(video_watchlist)")]
    finally:
        conn.close()
    assert "root_folder_id" in cols and "profile_id" in cols
