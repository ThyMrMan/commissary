"""Clean up episodes listed twice under two season-numbering schemes.

The prevention (backfill declining to create the duplicate) only helps from now
on. Libraries already carrying phantoms — Bleach's newer run listed under Plex's
S2 AND TMDB's S17 — need them removed, and the scan's prune will never do it: it
only inspects rows with a server_id, which a backfilled row does not have.

This DELETES rows, so almost every test below is about what it must REFUSE to
touch. A cleanup that removes a real episode is far worse than the duplicates it
was cleaning.
"""

from __future__ import annotations

import pytest
from flask import Flask, g

from database.video_database import VideoDatabase


@pytest.fixture()
def app_db(tmp_path, monkeypatch):
    import api.video as videoapi
    import core.video.sources as sources
    monkeypatch.setattr(sources, "resolve_video_server", lambda *a, **k: "plex")
    db = VideoDatabase(database_path=str(tmp_path / "video_library.db"))
    videoapi._video_db = db
    app = Flask(__name__)
    app.register_blueprint(videoapi.create_video_blueprint(), url_prefix="/api/video")

    @app.before_request
    def _p():
        g.profile_id = 1; g.is_admin = True; g.can_download = True; g.allowed_sides = "both"

    try:
        yield app.test_client(), db
    finally:
        videoapi._video_db = None


def _owned(db, seasons):
    """Episodes as the SERVER reports them — server_id set, file present."""
    return db.upsert_show_tree("plex", {
        "server_id": "s1", "tmdb_id": 30984, "title": "Bleach",
        "seasons": [{"season_number": sn, "episodes": [
            {"season_number": sn, "episode_number": en, "title": "E%d" % en,
             "air_date": ad, "server_id": "ep-%d-%d" % (sn, en),
             "files": [{"path": "/tv/Bleach/S%02dE%02d.mkv" % (sn, en)}]}
            for en, ad in eps]} for sn, eps in seasons.items()]})


def _phantom(db, show_id, season, episode, air_date):
    """A row as the TMDB backfill would have left it before the fix: no
    server_id, no file. Written directly — the backfill now refuses to."""
    conn = db._get_connection()
    conn.execute("INSERT OR IGNORE INTO seasons (show_id, season_number) VALUES (?,?)",
                 (show_id, season))
    sid = conn.execute("SELECT id FROM seasons WHERE show_id=? AND season_number=?",
                       (show_id, season)).fetchone()["id"]
    conn.execute("INSERT INTO episodes (show_id, season_id, season_number, episode_number, "
                 "title, air_date, has_file) VALUES (?,?,?,?,?,?,0)",
                 (show_id, sid, season, episode, "TMDB E%d" % episode, air_date))
    conn.commit(); conn.close()


def _eps(db, show_id):
    conn = db._get_connection()
    try:
        return sorted((r["season_number"], r["episode_number"]) for r in conn.execute(
            "SELECT season_number, episode_number FROM episodes WHERE show_id=?",
            (show_id,)).fetchall())
    finally:
        conn.close()


# ── it finds the real thing ──────────────────────────────────────────────────
def test_it_finds_the_phantom_and_names_what_it_duplicates(app_db):
    _c, db = app_db
    sid = _owned(db, {2: [(1, "2022-10-11")]})
    _phantom(db, sid, 17, 1, "2022-10-11")
    rows = db.duplicate_episode_rows()
    assert len(rows) == 1
    r = rows[0]
    assert (r["season_number"], r["episode_number"]) == (17, 1)
    assert (r["owned_season"], r["owned_episode"]) == (2, 1)
    assert r["show_title"] == "Bleach"


def test_deleting_removes_only_the_phantom(app_db):
    _c, db = app_db
    sid = _owned(db, {2: [(1, "2022-10-11"), (2, "2022-10-18")]})
    _phantom(db, sid, 17, 1, "2022-10-11")
    _phantom(db, sid, 17, 2, "2022-10-18")
    assert db.delete_episode_rows([r["id"] for r in db.duplicate_episode_rows()]) == 2
    assert _eps(db, sid) == [(2, 1), (2, 2)]


# ── what it must refuse to delete ────────────────────────────────────────────
def test_an_episode_you_own_is_never_touched(app_db):
    _c, db = app_db
    sid = _owned(db, {2: [(1, "2022-10-11")]})
    owned_ids = [r["id"] for r in _rows(db, sid)]
    assert db.delete_episode_rows(owned_ids) == 0
    assert _eps(db, sid) == [(2, 1)]


def test_a_genuinely_missing_episode_is_never_touched(app_db):
    """A missing episode in a season the server DOES number the same way is the
    normal 'you don't have this yet' row — not a duplicate."""
    _c, db = app_db
    sid = _owned(db, {2: [(1, "2022-10-11")]})
    _phantom(db, sid, 2, 5, "2022-11-15")       # same season, unmatched date
    assert db.duplicate_episode_rows() == []


def test_a_phantom_in_the_same_season_is_not_a_cross_season_duplicate(app_db):
    _c, db = app_db
    sid = _owned(db, {2: [(1, "2022-10-11")]})
    _phantom(db, sid, 2, 9, "2022-10-11")       # same date, SAME season
    assert db.duplicate_episode_rows() == []


def test_an_ambiguous_air_date_is_never_cleaned(app_db):
    """A streaming season shares one date across every episode — the pairing is
    unprovable, so nothing is removed."""
    _c, db = app_db
    sid = _owned(db, {1: [(1, "2024-05-01"), (2, "2024-05-01")]})
    _phantom(db, sid, 2, 1, "2024-05-01")
    assert db.duplicate_episode_rows() == []


def test_two_server_less_rows_on_one_date_are_left_alone(app_db):
    """Which of the two is the duplicate? Unanswerable — so neither goes."""
    _c, db = app_db
    sid = _owned(db, {2: [(1, "2022-10-11")]})
    _phantom(db, sid, 17, 1, "2022-10-11")
    _phantom(db, sid, 18, 1, "2022-10-11")
    assert db.duplicate_episode_rows() == []


def test_a_row_with_no_air_date_is_never_cleaned(app_db):
    _c, db = app_db
    sid = _owned(db, {2: [(1, "2022-10-11")]})
    _phantom(db, sid, 17, 1, None)
    assert db.duplicate_episode_rows() == []


def test_a_stale_preview_cannot_delete_something_that_became_real(app_db):
    """Ids come from a preview; a scan may have landed since. The rule is
    re-checked at delete time, so a row that is now owned survives."""
    _c, db = app_db
    sid = _owned(db, {2: [(1, "2022-10-11")]})
    _phantom(db, sid, 17, 1, "2022-10-11")
    ids = [r["id"] for r in db.duplicate_episode_rows()]
    conn = db._get_connection()          # the episode arrives for real
    conn.execute("UPDATE episodes SET server_id='ep-new', has_file=1 WHERE id=?", (ids[0],))
    conn.commit(); conn.close()
    assert db.delete_episode_rows(ids) == 0
    assert (17, 1) in _eps(db, sid)


def test_junk_ids_are_ignored(app_db):
    _c, db = app_db
    _owned(db, {2: [(1, "2022-10-11")]})
    assert db.delete_episode_rows([]) == 0
    assert db.delete_episode_rows(["x", None, 999999]) == 0


# ── the endpoints ────────────────────────────────────────────────────────────
def test_preview_then_clean_one_show(app_db):
    c, db = app_db
    sid = _owned(db, {2: [(1, "2022-10-11")]})
    _phantom(db, sid, 17, 1, "2022-10-11")
    prev = c.get("/api/video/repair/duplicate-episodes?show_id=%d" % sid).get_json()
    assert prev["count"] == 1
    done = c.post("/api/video/repair/duplicate-episodes", json={"show_id": sid}).get_json()
    assert done["removed"] == 1
    assert c.get("/api/video/repair/duplicate-episodes").get_json()["count"] == 0


def test_the_post_needs_a_target(app_db):
    c, _db = app_db
    assert c.post("/api/video/repair/duplicate-episodes", json={}).status_code == 400


def test_the_cleanup_is_admin_only(app_db):
    c, db = app_db
    sid = _owned(db, {2: [(1, "2022-10-11")]})
    _phantom(db, sid, 17, 1, "2022-10-11")
    app = c.application

    @app.before_request
    def _member():
        g.profile_id = 7; g.is_admin = False; g.can_download = True; g.allowed_sides = "both"

    assert c.post("/api/video/repair/duplicate-episodes",
                  json={"show_id": sid}).status_code == 403
    assert c.get("/api/video/repair/duplicate-episodes").status_code == 403
    assert (17, 1) in _eps(db, sid)


def _rows(db, show_id):
    conn = db._get_connection()
    try:
        return [dict(r) for r in conn.execute(
            "SELECT id FROM episodes WHERE show_id=?", (show_id,)).fetchall()]
    finally:
        conn.close()
