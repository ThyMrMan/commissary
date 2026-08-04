"""The default Music Library re-aligns itself with Music Library Folder at startup.

1.9.2 let the two disagree: editing Settings → Music Library Folder did not move
the first row of Music Libraries, and the import pipeline reads the ROW. 1.9.3
fixed the write path, but only healed on the next settings save — so an install
already in the broken state kept importing to the stale path forever, with the
field on screen showing the path the user expected.

These pin the startup repair, and just as importantly the cases where it must
keep its hands off.
"""

from __future__ import annotations

import sqlite3

import pytest

from database.music_database import MusicDatabase


@pytest.fixture
def cursor():
    conn = sqlite3.connect(":memory:")
    conn.execute("""
        CREATE TABLE music_root_folders (
            id INTEGER PRIMARY KEY,
            path TEXT NOT NULL UNIQUE,
            label TEXT,
            sort_order INTEGER NOT NULL DEFAULT 0,
            naming_template TEXT,
            quality_profile_id INTEGER
        )
    """)
    cur = conn.cursor()
    yield cur
    conn.close()


@pytest.fixture
def db():
    return MusicDatabase.__new__(MusicDatabase)


def _configured(monkeypatch, path):
    from config.settings import config_manager
    original = config_manager.get
    monkeypatch.setattr(config_manager, "get",
                        lambda k, d=None: (path if k == "soulseek.transfer_path"
                                           else original(k, d)))


def _rows(cursor):
    cursor.execute("SELECT path, sort_order FROM music_root_folders ORDER BY sort_order, id")
    return cursor.fetchall()


def test_stale_default_is_repaired(db, cursor, monkeypatch):
    """The reported bug: the field says one thing, the row says another, and
    the files follow the row."""
    cursor.execute("INSERT INTO music_root_folders (path, label, sort_order) "
                   "VALUES ('/old/transfer', 'Music Library', 0)")
    _configured(monkeypatch, "/media/completed/listening/music")

    db._reconcile_default_music_library(cursor)

    assert _rows(cursor) == [("/media/completed/listening/music", 0)]


def test_the_users_label_survives_the_repair(db, cursor, monkeypatch):
    """Only the path is wrong; renaming a library they labelled would be a
    second unasked-for change."""
    cursor.execute("INSERT INTO music_root_folders (path, label, sort_order) "
                   "VALUES ('/old', 'Main Collection', 0)")
    _configured(monkeypatch, "/new")

    db._reconcile_default_music_library(cursor)

    cursor.execute("SELECT label FROM music_root_folders")
    assert cursor.fetchone()[0] == "Main Collection"


def test_agreeing_install_is_untouched(db, cursor, monkeypatch):
    """The overwhelmingly common case must be a no-op."""
    cursor.execute("INSERT INTO music_root_folders (path, label, sort_order) "
                   "VALUES ('/music', 'Music Library', 0)")
    cursor.execute("INSERT INTO music_root_folders (path, label, sort_order) "
                   "VALUES ('/archive', 'Archive', 1)")
    _configured(monkeypatch, "/music")

    db._reconcile_default_music_library(cursor)

    assert _rows(cursor) == [("/music", 0), ("/archive", 1)]


def test_existing_library_at_that_path_is_promoted_not_duplicated(db, cursor, monkeypatch):
    """Inserting the path again would violate UNIQUE and lose the repair; the
    library that is already there becomes the default instead."""
    cursor.execute("INSERT INTO music_root_folders (path, label, sort_order) "
                   "VALUES ('/old', 'Old', 0)")
    cursor.execute("INSERT INTO music_root_folders (path, label, sort_order) "
                   "VALUES ('/media/music', 'Real', 1)")
    _configured(monkeypatch, "/media/music")

    db._reconcile_default_music_library(cursor)

    assert _rows(cursor)[0] == ("/media/music", 0)
    cursor.execute("SELECT COUNT(*) FROM music_root_folders WHERE path='/media/music'")
    assert cursor.fetchone()[0] == 1


def test_empty_table_is_left_to_the_seeder(db, cursor, monkeypatch):
    """Seeding deliberately refuses to resurrect a destination the user
    deleted; the repair must not become a back door that does it anyway."""
    _configured(monkeypatch, "/music")

    db._reconcile_default_music_library(cursor)

    assert _rows(cursor) == []


def test_unset_transfer_path_changes_nothing(db, cursor, monkeypatch):
    """A blank config key is not an instruction to blank the library."""
    cursor.execute("INSERT INTO music_root_folders (path, label, sort_order) "
                   "VALUES ('/music', 'Music Library', 0)")
    _configured(monkeypatch, "")

    db._reconcile_default_music_library(cursor)

    assert _rows(cursor) == [("/music", 0)]


def test_a_broken_config_read_does_not_break_startup(db, cursor, monkeypatch):
    """This runs inside schema init — raising here would take the whole app
    down over a settings problem."""
    cursor.execute("INSERT INTO music_root_folders (path, label, sort_order) "
                   "VALUES ('/music', 'Music Library', 0)")
    from config.settings import config_manager

    def _boom(*_a, **_kw):
        raise RuntimeError("config unavailable")

    monkeypatch.setattr(config_manager, "get", _boom)

    db._reconcile_default_music_library(cursor)     # must not raise

    assert _rows(cursor) == [("/music", 0)]


def test_reordering_the_library_list_is_respected(db, cursor, monkeypatch):
    """Saving the library list mirrors the new first path back to the config
    key, so after a reorder the two already agree and the repair is a no-op.
    Pinned because a repair that fought the reorder would silently undo it."""
    cursor.execute("INSERT INTO music_root_folders (path, label, sort_order) "
                   "VALUES ('/b', 'B', 0)")
    cursor.execute("INSERT INTO music_root_folders (path, label, sort_order) "
                   "VALUES ('/a', 'A', 1)")
    _configured(monkeypatch, "/b")      # what the mirror wrote

    db._reconcile_default_music_library(cursor)

    assert _rows(cursor) == [("/b", 0), ("/a", 1)]
