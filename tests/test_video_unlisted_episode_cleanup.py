"""Remove episodes a secondary provider invented under the wrong season number.

The prevention (``update_only`` on the TVDB cascade) stops new ones. Libraries
already carrying them need the rows gone: the diagnostic on Bleach found 17 in
season 2, 16 in season 1 and 7 in season 0, none owned, none from the server,
and the scan's prune will never touch them because it only inspects rows that
HAVE a server_id.

The authority is TMDB's own episode list for the season. That matters more than
the match: if the list can't be read, every missing episode in the season would
look unlisted, so a failed lookup must delete NOTHING rather than everything.
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


def _bleach(db):
    """Plex's structure: 17 seasons. Season 2 is the 2005 arc, 21 episodes.

    The other sixteen are present because the numbering detection scores a
    provider on how much of the SERVER's structure it can serve — with only
    season 2 there is no evidence TMDB's three-season split is wrong, and it
    correctly declines to switch."""
    seasons = [{"season_number": 2, "episodes": [
        {"season_number": 2, "episode_number": n, "title": "2005 ep %d" % n,
         "air_date": "2005-03-%02d" % n, "server_id": "p%d" % n,
         "files": [{"path": "/tv/Bleach/S02E%02d.mkv" % n}]} for n in range(1, 22)]}]
    seasons += [{"season_number": sn, "episodes": [
        {"season_number": sn, "episode_number": 1, "title": "S%d" % sn,
         "air_date": "2006-01-01", "server_id": "q%d" % sn,
         "files": [{"path": "/tv/Bleach/S%02dE01.mkv" % sn}]}]}
        for sn in list(range(1, 2)) + list(range(3, 18))]
    return db.upsert_show_tree("plex", {
        "server_id": "44632", "tmdb_id": 30984, "tvdb_id": 74796, "title": "Bleach",
        "seasons": seasons})


def _tvdb_junk(db, show_id):
    """What the TVDB cascade left behind, as the real diagnostic reported it."""
    db.backfill_episodes(show_id, 2, [
        {"episode_number": 25, "title": "THE MASTER", "air_date": "2023-09-30"},
        {"episode_number": 26, "title": "BLACK", "air_date": "2023-09-30"},
        {"episode_number": 39, "title": "THE VISIBLE ANSWER", "air_date": "2024-12-28"},
        {"episode_number": 40, "title": "MY LAST WORDS", "air_date": "2024-12-29"},
    ] + [{"episode_number": n, "title": "Episode %d" % n, "air_date": "2026-08-01"}
         for n in range(41, 54)])


def _eps(db, show_id, sn=2):
    conn = db._get_connection()
    try:
        return sorted(r["episode_number"] for r in conn.execute(
            "SELECT episode_number FROM episodes WHERE show_id=? AND season_number=?",
            (show_id, sn)).fetchall())
    finally:
        conn.close()


TMDB_S2 = set(range(1, 22))      # TMDB's real season 2: 21 episodes


# ── it finds and removes exactly the invented rows ───────────────────────────
def test_the_invented_rows_are_reported(app_db):
    _c, db = app_db
    sid = _bleach(db); _tvdb_junk(db, sid)
    rows = db.unlisted_episode_rows(sid, 2, TMDB_S2)
    assert {r["episode_number"] for r in rows} == {25, 26, 39, 40} | set(range(41, 54))


def test_removing_leaves_the_real_season(app_db):
    _c, db = app_db
    sid = _bleach(db); _tvdb_junk(db, sid)
    assert _eps(db, sid) != list(range(1, 22))          # damaged first
    assert db.delete_unlisted_episode_rows(sid, 2, TMDB_S2) == 17
    assert _eps(db, sid) == list(range(1, 22))


# ── what it must refuse ──────────────────────────────────────────────────────
def test_an_owned_episode_is_never_removed(app_db):
    """Even one TMDB doesn't list — your file is the ground truth, not TMDB."""
    _c, db = app_db
    sid = _bleach(db)
    assert db.delete_unlisted_episode_rows(sid, 2, {1, 2, 3}) == 0
    assert _eps(db, sid) == list(range(1, 22))


def test_a_genuinely_missing_episode_tmdb_lists_is_kept(app_db):
    """The whole point of the backfill — an episode you don't own yet but which
    really belongs to the season must survive."""
    _c, db = app_db
    sid = _bleach(db)
    db.backfill_episodes(sid, 2, [{"episode_number": 22, "air_date": "2005-08-02"}])
    assert db.delete_unlisted_episode_rows(sid, 2, set(range(1, 23))) == 0
    assert 22 in _eps(db, sid)


def test_an_empty_authority_list_deletes_nothing(app_db):
    """A failed TMDB read makes every missing episode look unlisted. Refusing is
    the only safe reading of 'I could not check'."""
    _c, db = app_db
    sid = _bleach(db); _tvdb_junk(db, sid)
    before = _eps(db, sid)
    assert db.unlisted_episode_rows(sid, 2, set()) == []
    assert db.delete_unlisted_episode_rows(sid, 2, None) == 0
    assert _eps(db, sid) == before


def test_a_row_with_a_file_survives_even_without_a_server_id(app_db):
    _c, db = app_db
    sid = _bleach(db)
    conn = db._get_connection()
    sid_row = conn.execute("SELECT id FROM seasons WHERE show_id=? AND season_number=2",
                           (sid,)).fetchone()["id"]
    conn.execute("INSERT INTO episodes (show_id, season_id, season_number, episode_number, "
                 "title, has_file) VALUES (?,?,2,90,'hand-added',1)", (sid, sid_row))
    conn.commit(); conn.close()
    assert db.delete_unlisted_episode_rows(sid, 2, TMDB_S2) == 0
    assert 90 in _eps(db, sid)


def test_the_delete_re_derives_rather_than_trusting_a_preview(app_db):
    """A scan landing between preview and confirm must not let a stale list
    remove an episode that has since become real."""
    _c, db = app_db
    sid = _bleach(db); _tvdb_junk(db, sid)
    ids = [r["id"] for r in db.unlisted_episode_rows(sid, 2, TMDB_S2)]
    conn = db._get_connection()
    conn.execute("UPDATE episodes SET server_id='now-real', has_file=1 WHERE id=?", (ids[0],))
    conn.commit(); conn.close()
    assert db.delete_unlisted_episode_rows(sid, 2, TMDB_S2) == len(ids) - 1
    assert 25 in _eps(db, sid)


# ── the endpoint ─────────────────────────────────────────────────────────────
class _FakeTmdb:
    """TMDB as the authority: season 2 is the 2005 arc, 21 episodes."""
    enabled = True

    class client:
        @staticmethod
        def match(_kind, _title, _year, known_id=None):
            return {"id": 30984, "metadata": {"seasons": [{"season_number": 2}]}}

        @staticmethod
        def season_episodes(_id, sn):
            return {"episodes": [{"episode_number": n} for n in range(1, 22)]} if sn == 2 else {}


def _patch_tmdb(monkeypatch, worker, db=None, tvdb=None):
    """A REAL engine instance — the endpoint now asks it which provider owns the
    show's numbering, so a stub with only `workers` no longer stands in."""
    import core.video.enrichment.engine as engmod
    e = engmod.VideoEnrichmentEngine.__new__(engmod.VideoEnrichmentEngine)
    e.workers = {"tmdb": worker, "tvdb": tvdb}
    e.db = db
    monkeypatch.setattr(engmod, "get_video_enrichment_engine", lambda *a, **k: e)


def test_preview_then_remove_through_the_api(app_db, monkeypatch):
    c, db = app_db
    _patch_tmdb(monkeypatch, _FakeTmdb(), db)
    sid = _bleach(db); _tvdb_junk(db, sid)
    prev = c.get("/api/video/repair/unlisted-episodes?show_id=%d" % sid).get_json()
    assert prev["count"] == 17
    done = c.post("/api/video/repair/unlisted-episodes", json={"show_id": sid}).get_json()
    assert done["removed"] == 17
    assert _eps(db, sid) == list(range(1, 22))


def test_the_api_refuses_when_tmdb_is_unavailable(app_db, monkeypatch):
    c, db = app_db
    _patch_tmdb(monkeypatch, type("W", (), {"enabled": False, "client": None})(), db)
    sid = _bleach(db); _tvdb_junk(db, sid)
    before = _eps(db, sid)
    r = c.post("/api/video/repair/unlisted-episodes", json={"show_id": sid})
    assert r.status_code == 400
    assert _eps(db, sid) == before


def test_it_needs_a_show(app_db):
    c, _db = app_db
    assert c.get("/api/video/repair/unlisted-episodes").status_code == 400


def test_the_cleanup_is_admin_only(app_db, monkeypatch):
    c, db = app_db
    _patch_tmdb(monkeypatch, _FakeTmdb(), db)
    sid = _bleach(db); _tvdb_junk(db, sid)

    @c.application.before_request
    def _member():
        g.profile_id = 7; g.is_admin = False; g.can_download = True; g.allowed_sides = "both"

    assert c.post("/api/video/repair/unlisted-episodes",
                  json={"show_id": sid}).status_code == 403
    assert 25 in _eps(db, sid)


# ── the real Bleach: the authority must be TVDB, not TMDB ────────────────────
class _BleachTmdb:
    """TMDB's Bleach: 3 seasons. Season 2 is Thousand-Year Blood War."""
    enabled = True

    class client:
        @staticmethod
        def match(_k, _t, _y, known_id=None):
            return {"id": 30984, "metadata": {"seasons": [{"season_number": n} for n in (0, 1, 2)]}}

        @staticmethod
        def season_episodes(_id, sn):
            return {"episodes": [{"episode_number": n} for n in range(1, 51)]} if sn == 2 else {}


class _BleachTvdb:
    """TVDB's Bleach: 17 seasons, matching Plex. Season 2 is the 2005 arc."""
    enabled = True

    class client:
        @staticmethod
        def season_numbers(_id):
            return [0] + list(range(1, 18))

        @staticmethod
        def season_episodes(_id, sn):
            return [{"episode_number": n} for n in range(1, 22)] if sn == 2 else []


def test_the_check_uses_tvdb_when_tvdb_owns_the_numbering(app_db, monkeypatch):
    """Checking against TMDB would call the library's 2005 season out of place
    and the injected 2023-2026 rows legitimate — exactly backwards. This is the
    scenario that made the 1.8.3 clean-up report 'no out-of-place episodes'."""
    c, db = app_db
    sid = _bleach(db)              # Plex season 2, 21 owned 2005 episodes
    _tvdb_junk(db, sid)            # 25, 26, 39, 40, 41-53 — TMDB's TYBW numbering
    _patch_tmdb(monkeypatch, _BleachTmdb(), db, _BleachTvdb())

    prev = c.get("/api/video/repair/unlisted-episodes?show_id=%d" % sid).get_json()
    assert prev["source"] == "tvdb"
    assert prev["count"] == 17     # every injected row, not just 51-53
    assert c.post("/api/video/repair/unlisted-episodes",
                  json={"show_id": sid}).get_json()["removed"] == 17
    assert _eps(db, sid) == list(range(1, 22))
