""""To Be Purchased" tag: flags a downloaded track so it can be told apart
from music the user has actually bought, with a dedicated shopping-list view
and a manual toggle to clear it.

Auto-flag-on-download behavior (record_soulsync_library_entry) is covered in
tests/imports/test_import_side_effects.py; this file covers the schema
migration, the DB-level list/pagination/search method, and the HTTP
list + toggle endpoints.
"""

from __future__ import annotations

import os
import tempfile
from pathlib import Path

import pytest

from database.music_database import MusicDatabase


@pytest.fixture
def db(tmp_path: Path) -> MusicDatabase:
    return MusicDatabase(database_path=str(tmp_path / "tbp.db"))


def _insert_track(db: MusicDatabase, *, track_id: str, title: str, to_be_purchased: int,
                  album_id: str = "a1", artist_id: str = "ar1", artist_name: str = "Test Artist",
                  album_title: str = "Test Album") -> None:
    conn = db._get_connection()
    cur = conn.cursor()
    cur.execute("INSERT OR IGNORE INTO artists (id, name) VALUES (?, ?)", (artist_id, artist_name))
    cur.execute("INSERT OR IGNORE INTO albums (id, artist_id, title) VALUES (?, ?, ?)",
               (album_id, artist_id, album_title))
    cur.execute(
        "INSERT INTO tracks (id, album_id, artist_id, title, to_be_purchased) VALUES (?, ?, ?, ?, ?)",
        (track_id, album_id, artist_id, title, to_be_purchased),
    )
    conn.commit()
    conn.close()


# ── Schema migration ────────────────────────────────────────────────────────

def test_to_be_purchased_column_exists_after_init(db: MusicDatabase):
    conn = db._get_connection()
    cols = {row[1] for row in conn.execute("PRAGMA table_info(tracks)")}
    conn.close()
    assert "to_be_purchased" in cols


def test_migration_is_idempotent(tmp_path: Path):
    path = str(tmp_path / "idempotent.db")
    MusicDatabase(database_path=path)
    MusicDatabase(database_path=path)   # re-init against the same file must not raise
    db2 = MusicDatabase(database_path=path)
    conn = db2._get_connection()
    cols = [row[1] for row in conn.execute("PRAGMA table_info(tracks)")]
    conn.close()
    assert cols.count("to_be_purchased") == 1


def test_default_is_zero_for_a_row_that_omits_it(db: MusicDatabase):
    """A row inserted without specifying the column (e.g. some other, older
    insert path) defaults to NOT flagged."""
    conn = db._get_connection()
    conn.execute("INSERT OR IGNORE INTO artists (id, name) VALUES ('ar1', 'A')")
    conn.execute("INSERT OR IGNORE INTO albums (id, artist_id, title) VALUES ('a1', 'ar1', 'Al')")
    conn.execute("INSERT INTO tracks (id, album_id, artist_id, title) VALUES ('t1', 'a1', 'ar1', 'T')")
    conn.commit()
    row = conn.execute("SELECT to_be_purchased FROM tracks WHERE id = 't1'").fetchone()
    conn.close()
    assert row["to_be_purchased"] == 0


def test_to_be_purchased_in_track_editable_fields_whitelist(db: MusicDatabase):
    assert "to_be_purchased" in db.TRACK_EDITABLE_FIELDS


# ── get_to_be_purchased_tracks() ────────────────────────────────────────────

def test_get_to_be_purchased_tracks_empty(db: MusicDatabase):
    result = db.get_to_be_purchased_tracks()
    assert result["tracks"] == []
    assert result["pagination"]["total_count"] == 0


def test_get_to_be_purchased_tracks_only_flagged(db: MusicDatabase):
    _insert_track(db, track_id="t1", title="Flagged Song", to_be_purchased=1)
    _insert_track(db, track_id="t2", title="Owned Song", to_be_purchased=0)

    result = db.get_to_be_purchased_tracks()
    assert len(result["tracks"]) == 1
    assert result["tracks"][0]["title"] == "Flagged Song"
    assert result["tracks"][0]["artist_name"] == "Test Artist"
    assert result["tracks"][0]["album_title"] == "Test Album"
    assert result["pagination"]["total_count"] == 1


def test_get_to_be_purchased_tracks_search_matches_title_artist_or_album(db: MusicDatabase):
    _insert_track(db, track_id="t1", title="Alpha Song", to_be_purchased=1,
                  artist_id="ar1", artist_name="Zeta Artist", album_id="a1", album_title="Gamma Album")
    _insert_track(db, track_id="t2", title="Beta Song", to_be_purchased=1,
                  artist_id="ar2", artist_name="Other Artist", album_id="a2", album_title="Other Album")

    by_title = db.get_to_be_purchased_tracks(search="alpha")
    assert [t["title"] for t in by_title["tracks"]] == ["Alpha Song"]

    by_artist = db.get_to_be_purchased_tracks(search="zeta")
    assert [t["title"] for t in by_artist["tracks"]] == ["Alpha Song"]

    by_album = db.get_to_be_purchased_tracks(search="gamma")
    assert [t["title"] for t in by_album["tracks"]] == ["Alpha Song"]

    no_match = db.get_to_be_purchased_tracks(search="nonexistent")
    assert no_match["tracks"] == []


def test_get_to_be_purchased_tracks_pagination(db: MusicDatabase):
    for i in range(5):
        _insert_track(db, track_id=f"t{i}", title=f"Song {i}", to_be_purchased=1)

    page1 = db.get_to_be_purchased_tracks(page=1, limit=2)
    assert len(page1["tracks"]) == 2
    assert page1["pagination"] == {
        "page": 1, "limit": 2, "total_count": 5, "total_pages": 3,
        "has_prev": False, "has_next": True,
    }

    page3 = db.get_to_be_purchased_tracks(page=3, limit=2)
    assert len(page3["tracks"]) == 1
    assert page3["pagination"]["has_next"] is False
    assert page3["pagination"]["has_prev"] is True


# ── HTTP endpoints ───────────────────────────────────────────────────────────

_TMP = tempfile.mkdtemp(prefix="soulsync-testdb-tbp-")
os.environ["DATABASE_PATH"] = os.path.join(_TMP, "http.db")
os.environ["SOULSYNC_TEST_DB_READY"] = "1"
web_server = pytest.importorskip("web_server")


@pytest.fixture
def client():
    return web_server.app.test_client()


def test_list_endpoint_returns_flagged_tracks(client):
    wdb = web_server.get_database()
    _insert_track(wdb, track_id="wt1", title="Shopping List Song", to_be_purchased=1,
                  artist_id="war1", album_id="wa1")

    r = client.get("/api/library/to-be-purchased")
    body = r.get_json()
    assert r.status_code == 200 and body["success"] is True
    assert any(t["title"] == "Shopping List Song" for t in body["tracks"])


def test_list_endpoint_search_param(client):
    wdb = web_server.get_database()
    _insert_track(wdb, track_id="wt2", title="Uniquely Named Track", to_be_purchased=1,
                  artist_id="war2", album_id="wa2")

    r = client.get("/api/library/to-be-purchased?search=Uniquely+Named")
    body = r.get_json()
    assert any(t["title"] == "Uniquely Named Track" for t in body["tracks"])

    r2 = client.get("/api/library/to-be-purchased?search=DefinitelyNotPresent")
    assert r2.get_json()["tracks"] == []


def test_toggle_via_put_track_endpoint_clears_and_sets_flag(client):
    wdb = web_server.get_database()
    _insert_track(wdb, track_id="wt3", title="Toggle Me", to_be_purchased=1,
                  artist_id="war3", album_id="wa3")

    # Clear it
    r = client.put("/api/library/track/wt3", json={"to_be_purchased": 0})
    assert r.status_code == 200 and r.get_json()["success"] is True
    assert not any(t["id"] == "wt3" for t in
                   client.get("/api/library/to-be-purchased").get_json()["tracks"])

    # Set it again — the manual "flag this too" direction, not just clearing
    r2 = client.put("/api/library/track/wt3", json={"to_be_purchased": 1})
    assert r2.status_code == 200 and r2.get_json()["success"] is True
    assert any(t["id"] == "wt3" for t in
              client.get("/api/library/to-be-purchased").get_json()["tracks"])
