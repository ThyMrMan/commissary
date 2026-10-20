"""The video side's own Plex/Jellyfin credentials are encrypted at rest.

Upstream stores video_plex_token / video_jellyfin_key in video.db as plaintext
while music's equivalents are Fernet-encrypted.
"""

import sqlite3

import pytest

from core.commissary import video_secrets
from database import video_database
from database.video_database import VideoDatabase


@pytest.fixture()
def db(tmp_path):
    return VideoDatabase(str(tmp_path / "video.db"))


def _raw(db, key):
    conn = sqlite3.connect(str(db.database_path))
    try:
        row = conn.execute("SELECT value FROM video_settings WHERE key = ?", (key,)).fetchone()
        return row[0] if row else None
    finally:
        conn.close()


@pytest.mark.parametrize("key", sorted(video_secrets.SECRET_KEYS))
def test_a_saved_secret_is_not_plaintext_on_disk(db, key):
    db.set_setting(key, "super-secret-token")
    stored = _raw(db, key)
    assert stored and "super-secret-token" not in stored
    assert stored.startswith("gAAAAA")


@pytest.mark.parametrize("key", sorted(video_secrets.SECRET_KEYS))
def test_readers_get_the_real_value_back(db, key):
    db.set_setting(key, "super-secret-token")
    assert db.get_setting(key) == "super-secret-token"


def test_other_settings_are_stored_as_is(db):
    db.set_setting("video_plex_url", "http://plex:32400")
    assert _raw(db, "video_plex_url") == "http://plex:32400"


def test_clearing_a_secret_still_clears_it(db):
    """Upstream's settings form saves '' to drop the override and inherit music's."""
    db.set_setting("video_plex_token", "tok")
    db.set_setting("video_plex_token", "")
    assert db.get_setting("video_plex_token") == ""


def test_an_existing_plaintext_row_is_sealed_by_the_migration(db):
    conn = sqlite3.connect(str(db.database_path))
    conn.execute("INSERT INTO video_settings(key, value) VALUES ('video_jellyfin_key', 'legacy-plain')")
    conn.commit()
    conn.close()
    assert video_secrets.migrate(db) == 1
    assert _raw(db, "video_jellyfin_key").startswith("gAAAAA")
    assert db.get_setting("video_jellyfin_key") == "legacy-plain"
    assert video_secrets.migrate(db) == 0, "idempotent"


def test_the_effective_video_plex_config_still_works(db):
    from core.video.sources import video_plex_config

    db.set_setting("video_plex_url", "http://plex:32400")
    db.set_setting("video_plex_token", "abc123")
    cfg = video_plex_config(db)
    assert cfg == {"base_url": "http://plex:32400", "token": "abc123", "source": "video"}


def test_secrets_never_go_into_a_config_export(db):
    db.set_setting("video_plex_token", "abc123")
    db.set_setting("video_plex_url", "http://plex:32400")
    exported = db.all_video_settings()
    assert "video_plex_token" not in exported
    assert exported.get("video_plex_url") == "http://plex:32400"


def test_the_database_and_module_agree_on_which_keys_are_secret():
    assert video_database._COMMISSARY_SECRET_KEYS == video_secrets.SECRET_KEYS

