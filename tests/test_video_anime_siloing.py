"""Keeping Anime and TV apart — the first grab of a new show decides forever.

Diagnosed against a real 66MB video_library.db. That install has four Libraries
(Movies/Anime-Movies, TV-Shows/Anime), 148 shows filed under TV-Shows and 571
under Anime. Nothing was mis-filed at rest — every pending wishlist episode
resolved correctly — which is what made this hard to see. The bug is not a wrong
row; it is a MISSING one, and it only bites once per show:

    a show you don't own yet has no movies/shows row
        -> the wishlist row's library_id and root_folder_id are both NULL
        -> resolution finds nothing and falls back to the PRIMARY show Library
        -> it downloads into TV-Shows and imports there
        -> the library scan reads it back from the TV Shows section and stamps
           shows.root_folder_id = <TV-Shows> onto it
        -> every later episode now resolves "correctly" to the wrong shelf

The mistake becomes the answer. The live database contained the proof: `Star
Wars: Visions Presents` had no shows row and root_folder_id set EXPLICITLY —
someone had corrected it by hand, which was the only available cure.

Three things had to change: somewhere to record the intent before you own the
show (the watchlist had no such column), something that actually reads it (eight
of the nine wishlist-creating paths passed no Library at all), and a way to see
it happen (the fallback was silent).
"""

from __future__ import annotations

import pytest

from database.video_database import VideoDatabase


@pytest.fixture
def db(tmp_path):
    import database.video_database as mod
    # Process-level cache: a second VideoDatabase(path) is a no-op without this,
    # so each test would silently share the first test's schema state.
    if hasattr(mod, "_initialized_paths"):
        mod._initialized_paths.clear()
    d = VideoDatabase(str(tmp_path / "v.db"))
    conn = d._get_connection()
    conn.execute("INSERT INTO root_folders (id, path, content_kind, server, server_title, label, sort_order) "
                 "VALUES (2, '/tv', 'show', 'plex', 'TV Shows', 'TV-Shows', 0)")
    conn.execute("INSERT INTO root_folders (id, path, content_kind, server, server_title, label, sort_order, category) "
                 "VALUES (5, '/anime', 'show', 'plex', 'Anime', 'Anime', 1, 'Anime')")
    conn.execute("INSERT INTO root_folders (id, path, content_kind, server, server_title, label, sort_order) "
                 "VALUES (3, '/movies', 'movie', 'plex', 'Movies', 'Movies', 0)")
    conn.commit()
    conn.close()
    return d


def _follow(db, tmdb_id, title, state="follow"):
    c = db._get_connection()
    c.execute("INSERT INTO video_watchlist (kind, tmdb_id, title, state) VALUES ('show', ?, ?, ?)",
              (tmdb_id, title, state))
    c.commit(); c.close()


def _own(db, tmdb_id, title, rfid):
    c = db._get_connection()
    c.execute("INSERT INTO shows (tmdb_id, title, root_folder_id, server_source, server_id) "
              "VALUES (?, ?, ?, 'plex', ?)", (tmdb_id, title, rfid, f"s{tmdb_id}"))
    c.commit(); c.close()


# ── B1: somewhere to record the intent ──────────────────────────────────────

class TestWatchlistLibrary:
    def test_the_column_exists(self, db):
        c = db._get_connection()
        cols = [r["name"] for r in c.execute("PRAGMA table_info(video_watchlist)")]
        c.close()
        assert "root_folder_id" in cols

    def test_following_a_show_can_name_its_library(self, db):
        _follow(db, 1, "Brand New Anime")
        assert db.set_watchlist_root_folder(1, 5) == 1
        assert db.resolve_destination_library("show", 1) == 5

    def test_a_movie_library_is_refused_for_a_show(self, db):
        """A show filed under a movie Library would send every grab into the
        wrong tree, silently."""
        _follow(db, 1, "X")
        assert db.set_watchlist_root_folder(1, 3) == 0
        assert db.resolve_destination_library("show", 1) is None

    def test_an_unknown_library_is_refused(self, db):
        _follow(db, 1, "X")
        assert db.set_watchlist_root_folder(1, 99999) == 0

    def test_choosing_a_library_also_moves_queued_episodes_and_the_show_row(self, db):
        """Otherwise picking a Library would split a title in two — new episodes
        on one shelf, the ones already queued heading for another."""
        _follow(db, 1, "X"); _own(db, 1, "X", 2)
        db.add_episodes_to_wishlist(1, "X", [{"season_number": 1, "episode_number": 1}])
        db.set_watchlist_root_folder(1, 5)
        c = db._get_connection()
        assert c.execute("SELECT root_folder_id FROM video_wishlist WHERE tmdb_id=1").fetchone()[0] == 5
        assert c.execute("SELECT root_folder_id FROM shows WHERE tmdb_id=1").fetchone()[0] == 5
        c.close()

    def test_clearing_does_not_cascade(self, db):
        """'Default' means 'inherit from the library row'. Wiping that row too
        would make choosing Default unassign a title nobody asked to touch."""
        _follow(db, 1, "X"); _own(db, 1, "X", 5)
        db.set_watchlist_root_folder(1, None)
        c = db._get_connection()
        assert c.execute("SELECT root_folder_id FROM shows WHERE tmdb_id=1").fetchone()[0] == 5
        c.close()

    def test_the_owned_row_outranks_the_watchlist_choice(self, db):
        """Once a show is on disk, sending new episodes elsewhere splits it."""
        _follow(db, 1, "X")
        db.set_watchlist_root_folder(1, 5)
        _own(db, 1, "X", 2)
        assert db.resolve_destination_library("show", 1) == 2

    def test_nothing_known_still_means_nothing(self, db):
        """The pre-existing behaviour for a title no one has an opinion about:
        None, which callers read as 'use the primary'."""
        assert db.resolve_destination_library("show", 4242) is None
        assert db.resolve_destination_library("movie", 4242) is None


# ── B2: something that reads it, for every caller ───────────────────────────

class TestAddersResolveTheLibrary:
    def test_an_episode_add_that_passes_nothing_still_gets_the_library(self, db):
        """THE fix. The daily airing automation is how episodes of a followed
        show reach the wishlist and it passed no Library, so a new anime show's
        first episode always went to the primary TV Library."""
        _follow(db, 1, "New Anime")
        db.set_watchlist_root_folder(1, 5)
        db.add_episodes_to_wishlist(1, "New Anime", [{"season_number": 1, "episode_number": 1}])
        c = db._get_connection()
        assert c.execute("SELECT root_folder_id FROM video_wishlist WHERE tmdb_id=1").fetchone()[0] == 5
        c.close()

    def test_an_owned_show_needs_no_watchlist_choice_at_all(self, db):
        _own(db, 7, "Owned Anime", 5)
        db.add_episodes_to_wishlist(7, "Owned Anime", [{"season_number": 1, "episode_number": 1}])
        c = db._get_connection()
        assert c.execute("SELECT root_folder_id FROM video_wishlist WHERE tmdb_id=7").fetchone()[0] == 5
        c.close()

    def test_an_explicit_library_still_wins(self, db):
        _own(db, 7, "X", 2)
        db.add_episodes_to_wishlist(7, "X", [{"season_number": 1, "episode_number": 1}],
                                    root_folder_id=5)
        c = db._get_connection()
        assert c.execute("SELECT root_folder_id FROM video_wishlist WHERE tmdb_id=7").fetchone()[0] == 5
        c.close()

    def test_a_title_with_no_opinion_writes_NULL_exactly_as_before(self, db):
        db.add_episodes_to_wishlist(9, "Unknown", [{"season_number": 1, "episode_number": 1}])
        c = db._get_connection()
        assert c.execute("SELECT root_folder_id FROM video_wishlist WHERE tmdb_id=9").fetchone()[0] is None
        c.close()

    def test_movies_resolve_too(self, db):
        c = db._get_connection()
        c.execute("INSERT INTO movies (tmdb_id, title, root_folder_id, server_source, server_id) "
                  "VALUES (550, 'Fight Club', 3, 'plex', 'm550')")
        c.commit(); c.close()
        db.add_movie_to_wishlist(550, "Fight Club")
        c = db._get_connection()
        assert c.execute("SELECT root_folder_id FROM video_wishlist WHERE tmdb_id=550 "
                         "AND kind='movie'").fetchone()[0] == 3
        c.close()


# ── D: a Library that holds anime already knows ─────────────────────────────

class TestLibraryDefaultSeriesType:
    def _set_default(self, db, rfid, value):
        c = db._get_connection()
        c.execute("UPDATE root_folders SET default_series_type=? WHERE id=?", (value, rfid))
        c.commit(); c.close()

    def test_it_types_shows_that_have_none(self, db):
        _own(db, 1, "A", 5); _own(db, 2, "B", 5)
        self._set_default(db, 5, "anime")
        assert db.apply_library_default_series_type() == 2
        c = db._get_connection()
        assert {r[0] for r in c.execute("SELECT series_type FROM shows")} == {"anime"}
        c.close()

    def test_a_per_show_type_is_never_overwritten(self, db):
        """A type set by hand is a deliberate statement and outranks a
        Library-wide default."""
        _own(db, 1, "A", 5)
        c = db._get_connection()
        c.execute("UPDATE shows SET series_type='daily' WHERE tmdb_id=1"); c.commit(); c.close()
        self._set_default(db, 5, "anime")
        db.apply_library_default_series_type()
        c = db._get_connection()
        assert c.execute("SELECT series_type FROM shows WHERE tmdb_id=1").fetchone()[0] == "daily"
        c.close()

    def test_other_libraries_are_untouched(self, db):
        _own(db, 1, "Anime show", 5); _own(db, 2, "TV show", 2)
        self._set_default(db, 5, "anime")
        db.apply_library_default_series_type()
        c = db._get_connection()
        assert c.execute("SELECT series_type FROM shows WHERE tmdb_id=2").fetchone()[0] in (None, "")
        c.close()

    def test_it_is_idempotent(self, db):
        _own(db, 1, "A", 5)
        self._set_default(db, 5, "anime")
        assert db.apply_library_default_series_type() == 1
        assert db.apply_library_default_series_type() == 0

    def test_a_library_with_no_default_changes_nothing(self, db):
        _own(db, 1, "A", 5)
        assert db.apply_library_default_series_type() == 0

    def test_it_can_be_scoped_to_one_library(self, db):
        _own(db, 1, "A", 5); _own(db, 2, "B", 2)
        self._set_default(db, 5, "anime"); self._set_default(db, 2, "standard")
        assert db.apply_library_default_series_type(root_folder_id=5) == 1
        c = db._get_connection()
        assert c.execute("SELECT series_type FROM shows WHERE tmdb_id=2").fetchone()[0] in (None, "")
        c.close()


# ── A: the fallback says so ─────────────────────────────────────────────────

class TestFallbackIsVisible:
    def test_falling_back_to_the_primary_is_logged(self, caplog):
        """It was silent, and silence is why one wrong first grab went unnoticed
        long enough for the scan to make it permanent."""
        import logging
        from core.automation.handlers import video_process_wishlist as vpw
        caplog.set_level(logging.INFO, logger="soulsync.automation.video_process_wishlist")
        out = vpw._item_target_dir({"show_title": "Some New Anime"}, "/tv")
        assert out == "/tv"
        msgs = [r.getMessage() for r in caplog.records
                if r.name == "soulsync.automation.video_process_wishlist"]
        assert any("Some New Anime" in m and "primary" in m for m in msgs), msgs

    def test_an_item_with_its_own_library_logs_nothing(self, caplog, monkeypatch):
        import logging
        from core.automation.handlers import video_process_wishlist as vpw
        monkeypatch.setattr(vpw, "_root_folder_for_item", lambda item: {"path": "/anime"})
        caplog.set_level(logging.INFO, logger="soulsync.automation.video_process_wishlist")
        assert vpw._item_target_dir({"show_title": "X"}, "/tv") == "/anime"
        assert not [r for r in caplog.records
                    if r.name == "soulsync.automation.video_process_wishlist"]

    @pytest.mark.parametrize("item,expected", [
        ({"show_title": "Show"}, "Show"),
        ({"title": "Movie"}, "Movie"),
        ({}, "?"),
    ])
    def test_the_log_can_name_any_item_shape(self, item, expected):
        from core.automation.handlers.video_process_wishlist import _item_label
        assert _item_label(item) == expected
