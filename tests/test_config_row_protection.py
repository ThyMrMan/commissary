"""An unreadable config row must never be mistaken for an absent one.

Adapted from upstream 3.2.0 (#1137), all four links confirmed present here
before adapting:

1. ``_load_from_database`` returned None for BOTH "no row" and "read failed",
   and the no-row path regenerates defaults and writes them over the real row.
   One locked database at boot — an enrichment batch insert, a library scan
   commit — and every setting was gone.
2. A corrupt blob was replaced rather than kept, destroying the only copy.
3. The config.json fallback used ``open(path, 'w')``: truncate-on-open, so a
   crash mid-dump left zero bytes in the file the user falls back to precisely
   when things are already wrong.
4. One Save click called set() per leaf key, each encrypting and committing the
   whole config — hundreds of write cycles, which is the lock contention that
   pushed saves onto that fallback to begin with.
"""

from __future__ import annotations

import json
import sqlite3

import pytest

from config.settings import ConfigManager


@pytest.fixture()
def cfg(tmp_path, monkeypatch):
    monkeypatch.setenv("DATABASE_PATH", str(tmp_path / "music_library.db"))
    monkeypatch.setenv("SOULSYNC_CONFIG_PATH", str(tmp_path / "config.json"))
    c = ConfigManager(str(tmp_path / "config.json"))
    c.set("spotify.client_id", "the-real-one")
    return c


def _stored(c):
    conn = sqlite3.connect(str(c.database_path))
    try:
        row = conn.execute("SELECT value FROM metadata WHERE key='app_config'").fetchone()
        return row[0] if row else None
    finally:
        conn.close()


# ── link 1: absence must be positively observed ─────────────────────────────

def test_a_read_failure_is_reported_separately_from_an_absent_row(cfg, monkeypatch):
    monkeypatch.setattr(cfg, "_connect_db",
                        lambda: (_ for _ in ()).throw(sqlite3.OperationalError("database is locked")))
    data, load_error = cfg._load_from_database()
    assert data is None and load_error is True, "a locked database is not an empty one"


def test_an_absent_row_is_reported_as_absent_not_an_error(cfg):
    conn = sqlite3.connect(str(cfg.database_path))
    conn.execute("DELETE FROM metadata WHERE key='app_config'")
    conn.commit()
    conn.close()
    data, load_error = cfg._load_from_database()
    assert data is None and load_error is False, "a fresh install must still get defaults"


def test_an_unreadable_row_is_never_overwritten_with_defaults(cfg, tmp_path, monkeypatch):
    """The whole point. Boot with the row unreadable, save a setting, and the
    stored row must still hold the real configuration."""
    before = _stored(cfg)
    assert "gAAAAA" in before or "spotify" in before

    calls = {"n": 0}
    real_connect = ConfigManager._connect_db

    def flaky(self):
        calls["n"] += 1
        raise sqlite3.OperationalError("database is locked")

    monkeypatch.setattr(ConfigManager, "_connect_db", flaky)
    monkeypatch.setattr("config.settings.time.sleep", lambda *_: None)   # no 7.7s wait
    degraded = ConfigManager(str(tmp_path / "config.json"))
    assert degraded._db_row_protected is True
    assert calls["n"] > 1, "an unreadable row must be retried before giving up"

    monkeypatch.setattr(ConfigManager, "_connect_db", real_connect)
    degraded.set("spotify.client_id", "written-while-degraded")
    assert _stored(degraded) == before, "the protected row was overwritten"


def test_the_degraded_session_still_persists_to_the_json_file(cfg, tmp_path, monkeypatch):
    monkeypatch.setattr(ConfigManager, "_connect_db",
                        lambda self: (_ for _ in ()).throw(sqlite3.OperationalError("locked")))
    monkeypatch.setattr("config.settings.time.sleep", lambda *_: None)
    degraded = ConfigManager(str(tmp_path / "config.json"))
    degraded.set("soulseek.download_path", "/somewhere/new")
    saved = json.loads((tmp_path / "config.json").read_text(encoding="utf-8"))
    assert saved["soulseek"]["download_path"] == "/somewhere/new"


# ── link 2: a corrupt blob is kept ──────────────────────────────────────────

def test_a_corrupt_blob_is_quarantined_before_anything_replaces_it(cfg, tmp_path):
    conn = sqlite3.connect(str(cfg.database_path))
    conn.execute("UPDATE metadata SET value='{not json at all' WHERE key='app_config'")
    conn.commit()
    conn.close()

    data, load_error = cfg._load_from_database()
    assert data is None and load_error is True
    quarantined = list(tmp_path.glob("config.corrupt-*.json"))
    assert quarantined, "the only copy of the settings was discarded"
    assert quarantined[0].read_text(encoding="utf-8") == "{not json at all"


# ── link 3: the fallback file is written atomically ─────────────────────────

def test_the_json_fallback_is_written_atomically(cfg, tmp_path, monkeypatch):
    """A crash mid-dump must not leave a truncated file. Simulate the dump
    failing and assert the previous contents survived."""
    cfg._save_to_config_file()
    good = (tmp_path / "config.json").read_text(encoding="utf-8")
    assert len(good) > 10

    def explode(*a, **k):
        raise OSError("no space left on device")

    monkeypatch.setattr("config.settings.json.dump", explode)
    assert cfg._save_to_config_file() is False
    assert (tmp_path / "config.json").read_text(encoding="utf-8") == good, \
        "a failed write truncated the file it was supposed to protect"
    assert not list(tmp_path.glob("*.tmp")), "the temp file was left behind"


# ── link 4: one write per form, not per key ─────────────────────────────────

def test_batch_coalesces_many_sets_into_one_write(cfg, monkeypatch):
    writes = {"n": 0}
    real = cfg._save_to_database
    monkeypatch.setattr(cfg, "_save_to_database", lambda d: (writes.__setitem__("n", writes["n"] + 1), real(d))[1])

    with cfg.batch():
        for i in range(50):
            cfg.set(f"settings.k{i}", i)
    assert writes["n"] == 1, f"a 50-key form did {writes['n']} database writes"
    assert cfg.get("settings.k49") == 49, "the values must still be saved"


def test_without_batch_each_set_still_writes(cfg, monkeypatch):
    """The coalescing is opt-in — an ordinary single set() must still persist
    immediately, or a one-off write would sit in memory unsaved."""
    writes = {"n": 0}
    real = cfg._save_to_database
    monkeypatch.setattr(cfg, "_save_to_database", lambda d: (writes.__setitem__("n", writes["n"] + 1), real(d))[1])
    cfg.set("settings.solo", 1)
    assert writes["n"] == 1


def test_a_failed_batch_body_still_does_not_lose_the_earlier_sets(cfg):
    with pytest.raises(RuntimeError):
        with cfg.batch():
            cfg.set("settings.before_error", "kept")
            raise RuntimeError("boom")
    fresh = ConfigManager(str(cfg.config_path))
    assert fresh.get("settings.before_error") == "kept"
