"""Profile <-> Plex account linkage: the _add_profile_plex_link migration
(idempotent, ALTER TABLE-based) and the plex_account_id/plex_username/
plex_thumb CRUD round-trip on database/music_database.py."""

from __future__ import annotations

import pytest

from database.music_database import MusicDatabase


@pytest.fixture
def db(tmp_path):
    return MusicDatabase(database_path=str(tmp_path / "music.db"))


def test_migration_is_idempotent(tmp_path):
    path = str(tmp_path / "idempotent.db")
    MusicDatabase(database_path=path)
    # Re-initializing against the SAME file re-runs every migration function —
    # must not raise (duplicate ALTER TABLE) or duplicate the metadata row.
    db2 = MusicDatabase(database_path=path)
    with db2._get_connection() as conn:
        rows = conn.execute(
            "SELECT COUNT(*) FROM metadata WHERE key = 'profiles_plex_link_v1'").fetchone()
        assert rows[0] == 1


def test_migration_adds_columns_to_existing_db(tmp_path):
    path = str(tmp_path / "cols.db")
    db1 = MusicDatabase(database_path=path)
    with db1._get_connection() as conn:
        cols = [c[1] for c in conn.execute("PRAGMA table_info(profiles)")]
    assert {'plex_account_id', 'plex_username', 'plex_thumb'} <= set(cols)


def test_create_profile_with_plex_link_roundtrips(db):
    pid = db.create_profile(
        'Alice', plex_account_id=555, plex_username='alice_plex', plex_thumb='http://thumb')
    assert pid is not None

    profile = db.get_profile(pid)
    assert profile['plex_account_id'] == 555
    assert profile['plex_username'] == 'alice_plex'

    listed = {p['id']: p for p in db.get_all_profiles()}
    assert listed[pid]['plex_account_id'] == 555
    assert listed[pid]['plex_username'] == 'alice_plex'


def test_create_profile_without_plex_link_is_none(db):
    pid = db.create_profile('Bob')
    assert db.get_profile(pid)['plex_account_id'] is None


def test_get_profile_by_plex_id(db):
    pid = db.create_profile('Carol', plex_account_id=777)
    found = db.get_profile_by_plex_id(777)
    assert found == {'id': pid, 'name': 'Carol', 'is_admin': False}
    assert db.get_profile_by_plex_id(999) is None


def test_update_profile_can_relink_and_unlink_plex(db):
    pid = db.create_profile('Dave', plex_account_id=1)
    assert db.update_profile(pid, plex_account_id=2, plex_username='dave2') is True
    assert db.get_profile(pid)['plex_account_id'] == 2
    assert db.get_profile_by_plex_id(1) is None
    assert db.get_profile_by_plex_id(2)['id'] == pid

    assert db.update_profile(pid, plex_account_id=None) is True
    assert db.get_profile(pid)['plex_account_id'] is None
    assert db.get_profile_by_plex_id(2) is None


def test_no_db_level_uniqueness_enforced_in_code_by_caller(db):
    """There's no DB-level UNIQUE on plex_account_id (SQLite ALTER TABLE ADD
    COLUMN can't add one) — two profiles COULD carry the same id if a caller
    doesn't check get_profile_by_plex_id() first. This pins that the DB layer
    itself doesn't guard it, so callers (the import/sign-in endpoints) MUST."""
    db.create_profile('First', plex_account_id=1)
    second_id = db.create_profile('Second', plex_account_id=1)
    assert second_id is not None   # DB layer allows it — not its job to prevent
