"""A title's search settings can be set before the title is owned.

Upstream keeps "Also known as", the season-pack preference and the series type
on the library row, so a show not yet on disk - the one whose first grab they
decide - had nowhere to put them.
"""

import sqlite3

import pytest
from flask import Flask

from core.commissary import title_overrides as to
from database.video_database import VideoDatabase

NEW_SHOW = 250008       # wished, not owned
OWNED = 220542


@pytest.fixture()
def db(tmp_path):
    db = VideoDatabase(database_path=str(tmp_path / "video_library.db"))
    db.upsert_show_tree("plex", {"server_id": "s1", "title": "The Apothecary Diaries",
                                 "tmdb_id": OWNED, "seasons": [{"season_number": 1, "episodes": [
                                     {"episode_number": 1, "title": "E1", "air_date": "2026-01-01"}]}]})
    db.add_episodes_to_wishlist(NEW_SHOW, "Tenkou-saki", [
        {"season_number": 1, "episode_number": 2, "title": "E2", "air_date": "2026-01-08"}])
    return db


def _row_id(db, tmdb_id=OWNED):
    conn = sqlite3.connect(str(db.database_path))
    try:
        return conn.execute("SELECT id FROM shows WHERE tmdb_id=?", (tmdb_id,)).fetchone()[0]
    finally:
        conn.close()


def _wished(db):
    return {r["show_tmdb_id"]: r for r in db.wishlist_manual_search_items("show", NEW_SHOW)}


# ── the store ────────────────────────────────────────────────────────────────

def test_the_old_forks_rows_read_back(db):
    """Same table and columns as the old fork, so the live rows carry over."""
    conn = sqlite3.connect(str(db.database_path))
    conn.execute("INSERT INTO video_title_overrides (kind, tmdb_id, aka_titles, series_type, "
                 "season_pack_mode) VALUES ('show', ?, 'Tenkou-saki\nOsananajimi', 'anime', 'only')",
                 (NEW_SHOW,))
    conn.commit()
    conn.close()
    got = to.get(db, "show", NEW_SHOW)
    assert got == {"manual_aliases": ["Tenkou-saki", "Osananajimi"], "series_type": "anime",
                   "pack_preference": "prefer"}


def test_clearing_everything_removes_the_row(db):
    to.set_values(db, "show", NEW_SHOW, manual_aliases=["A"], series_type="anime")
    to.set_values(db, "show", NEW_SHOW, manual_aliases=[], series_type="", pack_preference="auto")
    conn = sqlite3.connect(str(db.database_path))
    assert conn.execute("SELECT COUNT(*) FROM video_title_overrides").fetchone()[0] == 0
    conn.close()


# ── an unowned title ────────────────────────────────────────────────────────

def test_an_unowned_shows_aka_and_pack_mode_reach_the_drain(db):
    to.set_values(db, "show", NEW_SHOW, manual_aliases=["Tenkou-saki no Seiso"],
                  pack_preference="never")
    got = db.title_overrides_for("show", tmdb_id=NEW_SHOW)
    assert got["manual_aliases"] == ["Tenkou-saki no Seiso"]
    assert got["pack_preference"] == "never"


def test_an_unowned_shows_series_type_reaches_the_wishlist(db):
    assert _wished(db)[NEW_SHOW]["series_type"] is None
    to.set_values(db, "show", NEW_SHOW, series_type="anime")
    assert _wished(db)[NEW_SHOW]["series_type"] == "anime"


def test_the_unattended_drain_query_reads_it_too(db):
    to.set_values(db, "show", NEW_SHOW, series_type="anime")
    rows = [r for r in db.episode_wishlist_to_download(due_only=False)
            if r["show_tmdb_id"] == NEW_SHOW]
    assert rows and rows[0]["series_type"] == "anime"


def test_a_movie_aka_works_unowned(db):
    to.set_values(db, "movie", 603, manual_aliases=["The Matrix (Remastered)"])
    assert db.title_overrides_for("movie", tmdb_id=603)["manual_aliases"] == ["The Matrix (Remastered)"]


# ── once owned ──────────────────────────────────────────────────────────────

def test_once_owned_the_stored_values_fill_the_rows_blanks(db):
    to.set_values(db, "show", OWNED, manual_aliases=["Kusuriya no Hitorigoto"],
                  series_type="anime", pack_preference="prefer")
    rid = _row_id(db)
    got = db.title_overrides("show", rid)
    assert got["manual_aliases"] == ["Kusuriya no Hitorigoto"]
    assert got["pack_preference"] == "prefer"
    assert db.show_detail(rid)["series_type"] == "anime"


def test_the_row_wins_where_it_has_a_value(db):
    to.set_values(db, "show", OWNED, manual_aliases=["Old"])
    conn = sqlite3.connect(str(db.database_path))
    conn.execute("UPDATE shows SET manual_aliases='[\"Row\"]', series_type='daily' WHERE tmdb_id=?",
                 (OWNED,))
    conn.commit()
    conn.close()
    assert db.title_overrides("show", _row_id(db))["manual_aliases"] == ["Row"]
    to.set_values(db, "show", OWNED, series_type="anime")
    assert db.show_detail(_row_id(db))["series_type"] == "daily"


def test_clearing_an_owned_shows_aka_does_not_bring_the_old_one_back(db):
    to.set_values(db, "show", OWNED, manual_aliases=["Kusuriya no Hitorigoto"])
    rid = _row_id(db)
    db.set_title_overrides("show", rid, manual_aliases=[])
    assert db.title_overrides("show", rid)["manual_aliases"] == []


def test_standard_set_on_an_owned_show_beats_a_stored_anime(db):
    """upstream stores 'standard' as NULL on the row; the mirror keeps it."""
    to.set_values(db, "show", OWNED, series_type="anime")
    rid = _row_id(db)
    db.set_show_series_type(rid, "standard")
    assert db.show_detail(rid)["series_type"] == "standard"


def test_a_scanned_in_show_adopts_its_type_before_a_library_default(db):
    from core.commissary import video_libraries as vl
    to.set_values(db, "show", OWNED, series_type="daily")
    conn = sqlite3.connect(str(db.database_path))
    conn.execute("INSERT INTO root_folders (path, content_kind, default_series_type) "
                 "VALUES ('/anime', 'show', 'anime')")
    rf = conn.execute("SELECT id FROM root_folders").fetchone()[0]
    conn.execute("UPDATE shows SET root_folder_id=? WHERE tmdb_id=?", (rf, OWNED))
    conn.commit()
    conn.close()
    vl.apply_default_series_type(db)
    conn = sqlite3.connect(str(db.database_path))
    assert conn.execute("SELECT series_type FROM shows WHERE tmdb_id=?", (OWNED,)).fetchone()[0] \
        == "daily"
    conn.close()


def test_the_manual_search_identity_knows_an_unowned_type(db):
    from core.commissary import search_identity as si
    to.set_values(db, "show", NEW_SHOW, series_type="anime")
    assert si.series_type(db, NEW_SHOW) == "anime"


# ── the endpoint ────────────────────────────────────────────────────────────

@pytest.fixture()
def client(db):
    import api.video as videoapi
    videoapi._video_db = db
    app = Flask(__name__)
    app.register_blueprint(videoapi.create_video_blueprint(), url_prefix="/api/video")
    try:
        yield app.test_client()
    finally:
        videoapi._video_db = None


def test_put_and_get_for_an_unowned_show(client):
    url = "/api/video/commissary/title/show/%d/overrides" % NEW_SHOW
    r = client.put(url, json={"manual_aliases": ["Tenkou-saki"], "series_type": "anime",
                              "pack_preference": "prefer"})
    assert r.status_code == 200, r.get_json()
    got = client.get(url).get_json()
    assert (got["library_id"], got["manual_aliases"], got["series_type"], got["pack_preference"]) \
        == (None, ["Tenkou-saki"], "anime", "prefer")


def test_put_for_an_owned_show_writes_the_row(client, db):
    r = client.put("/api/video/commissary/title/show/%d/overrides" % OWNED,
                   json={"manual_aliases": ["Kusuriya"], "series_type": "anime"})
    assert r.status_code == 200 and r.get_json()["library_id"] == _row_id(db)
    conn = sqlite3.connect(str(db.database_path))
    row = conn.execute("SELECT manual_aliases, series_type FROM shows WHERE tmdb_id=?",
                       (OWNED,)).fetchone()
    conn.close()
    assert row == ('["Kusuriya"]', "anime")


def test_bad_values_are_refused(client):
    url = "/api/video/commissary/title/show/%d/overrides" % NEW_SHOW
    assert client.put(url, json={"series_type": "weekly"}).status_code == 400
    assert client.put(url, json={"manual_aliases": "a, b"}).status_code == 400
    assert client.put(url, json={}).status_code == 400
    assert client.put("/api/video/commissary/title/album/1/overrides",
                      json={"manual_aliases": []}).status_code == 400


def test_a_non_admin_cannot_write(client):
    from flask import g
    app = client.application

    @app.before_request
    def _as_member():
        g.is_admin = False

    url = "/api/video/commissary/title/show/%d/overrides" % NEW_SHOW
    assert client.put(url, json={"series_type": "anime"}).status_code == 403
    assert client.get(url).status_code == 200
