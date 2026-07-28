"""A season NUMBER is not a shared key between metadata providers.

Diagnosed on Bleach (tmdb 30984 / tvdb 74796). Its season 2 held 38 episodes:
the real 21 from 2005 (all owned, all from Plex), plus seventeen rows numbered
25, 26 and 39-53 with air dates from 2023 to 2026, none owned, none from the
server.

Those seventeen are Thousand-Year Blood War. TMDB files TYBW as season 17 — the
library HAS it there, 40 episodes, all owned. TVDB files TYBW as its season 2.
The TVDB gap-fill was handed TMDB's season numbers and allowed to INSERT, so
TVDB's season-2 episode list landed inside TMDB's season 2, the 2005 arc.

The damage isn't cosmetic: a wished episode filed under a season number no
release uses can never be matched, which is why the missing TYBW episodes were
never found.

TVDB stays useful — it is often first with titles and synopses for just-aired
episodes. It may now enrich an episode TMDB already listed. It may not decide
which episodes exist.
"""

from __future__ import annotations

import pytest

from database.video_database import VideoDatabase


@pytest.fixture()
def db(tmp_path):
    return VideoDatabase(database_path=str(tmp_path / "video_library.db"))


def _bleach(db):
    """The library as the diagnostic found it: 2005's season 2 owned from Plex,
    TYBW owned under season 17."""
    return db.upsert_show_tree("plex", {
        "server_id": "44632", "tmdb_id": 30984, "tvdb_id": 74796, "title": "Bleach",
        "seasons": [
            {"season_number": 2, "episodes": [
                {"season_number": 2, "episode_number": n, "title": "2005 ep %d" % n,
                 "air_date": "2005-03-%02d" % n, "server_id": "p%d" % n,
                 "files": [{"path": "/tv/Bleach/S02E%02d.mkv" % n}]} for n in range(1, 22)]},
            {"season_number": 17, "episodes": [
                {"season_number": 17, "episode_number": n, "title": "TYBW %d" % n,
                 "air_date": "2022-10-%02d" % ((n % 28) + 1), "server_id": "t%d" % n,
                 "files": [{"path": "/tv/Bleach/S17E%02d.mkv" % n}]} for n in range(1, 41)]},
        ]})


def _season(db, show_id, sn):
    conn = db._get_connection()
    try:
        return [(r["episode_number"], r["title"]) for r in conn.execute(
            "SELECT episode_number, title FROM episodes WHERE show_id=? AND season_number=? "
            "ORDER BY episode_number", (show_id, sn)).fetchall()]
    finally:
        conn.close()


# TVDB's season 2 = TYBW. Numbers and dates taken from the real diagnostic.
TVDB_SEASON_2 = [
    {"episode_number": 25, "title": "THE MASTER", "air_date": "2023-09-30"},
    {"episode_number": 26, "title": "BLACK", "air_date": "2023-09-30"},
    {"episode_number": 39, "title": "THE VISIBLE ANSWER", "air_date": "2024-12-28"},
    {"episode_number": 40, "title": "MY LAST WORDS", "air_date": "2024-12-29"},
    {"episode_number": 41, "title": "GOD OF THUNDER", "air_date": "2026-07-25"},
] + [{"episode_number": n, "title": "Episode %d" % n, "air_date": "2026-08-%02d" % (n - 41)}
     for n in range(42, 54)]


# ── the reported damage ──────────────────────────────────────────────────────
def test_a_secondary_provider_cannot_add_episodes_to_a_season(db):
    sid = _bleach(db)
    db.backfill_episodes(sid, 2, TVDB_SEASON_2, update_only=True)
    assert [n for n, _t in _season(db, sid, 2)] == list(range(1, 22))


def test_the_exact_seventeen_rows_from_the_report_are_not_created(db):
    sid = _bleach(db)
    db.backfill_episodes(sid, 2, TVDB_SEASON_2, update_only=True)
    got = {n for n, _t in _season(db, sid, 2)}
    assert got.isdisjoint({25, 26, 39, 40, 41} | set(range(42, 54)))
    assert len(TVDB_SEASON_2) == 17          # the report's count, pinned


def test_without_the_flag_it_still_creates(db):
    """The primary provider defines which episodes exist — unchanged."""
    sid = _bleach(db)
    db.backfill_episodes(sid, 2, [{"episode_number": 22, "air_date": "2005-08-02"}])
    assert 22 in {n for n, _t in _season(db, sid, 2)}


def test_a_tmdb_rescan_still_grows_the_season_the_episodes_belong_to(db):
    """The point of the whole exercise: the 10 TYBW episodes that were being
    hunted under season 2 must appear under season 17, where releases number
    them. Locking the TVDB path down must not also block the TMDB path — and
    1.8.2's duplicate-suppression guard must not eat the new rows either (their
    air dates are unique, so it has no reason to)."""
    import datetime
    sid = _bleach(db)                       # S17 owned, episodes 1-40
    start = datetime.date(2022, 10, 11)
    db.backfill_episodes(sid, 17, [
        {"episode_number": n, "title": "TYBW %d" % n,
         "air_date": str(start + datetime.timedelta(days=7 * (n - 1)))}
        for n in range(1, 51)])             # TMDB lists 50
    conn = db._get_connection()
    missing = [r["episode_number"] for r in conn.execute(
        "SELECT episode_number FROM episodes WHERE show_id=? AND season_number=17 "
        "AND server_id IS NULL ORDER BY episode_number", (sid,)).fetchall()]
    conn.close()
    assert missing == list(range(41, 51))
    assert len(_season(db, sid, 17)) == 50


# ── TVDB keeps its actual job ────────────────────────────────────────────────
def test_it_still_fills_metadata_tmdb_left_blank(db):
    """The reason the TVDB pass exists: TMDB is often slow with titles and
    synopses for just-aired episodes."""
    sid = db.upsert_show_tree("plex", {
        "server_id": "s1", "tmdb_id": 1, "tvdb_id": 2, "title": "Show",
        "seasons": [{"season_number": 1, "episodes": [
            {"season_number": 1, "episode_number": 1, "title": None,
             "server_id": "e1", "files": [{"path": "/tv/S01E01.mkv"}]}]}]})
    db.backfill_episodes(sid, 1, [{"episode_number": 1, "title": "The Real Title",
                                   "overview": "what happens"}], update_only=True)
    conn = db._get_connection()
    row = conn.execute("SELECT title, overview FROM episodes WHERE show_id=?", (sid,)).fetchone()
    conn.close()
    assert row["title"] == "The Real Title" and row["overview"] == "what happens"


def test_it_never_clobbers_what_tmdb_already_had(db):
    sid = db.upsert_show_tree("plex", {
        "server_id": "s1", "tmdb_id": 1, "tvdb_id": 2, "title": "Show",
        "seasons": [{"season_number": 1, "episodes": [
            {"season_number": 1, "episode_number": 1, "title": "TMDB title",
             "server_id": "e1", "files": [{"path": "/tv/S01E01.mkv"}]}]}]})
    db.backfill_episodes(sid, 1, [{"episode_number": 1, "title": "TVDB title"}],
                         update_only=True)
    conn = db._get_connection()
    row = conn.execute("SELECT title FROM episodes WHERE show_id=?", (sid,)).fetchone()
    conn.close()
    assert row["title"] == "TMDB title"


def test_a_missing_episode_tmdb_listed_is_still_enriched(db):
    """update_only means 'don't invent rows', not 'only touch owned ones' — a
    genuinely missing episode TMDB created still gets TVDB's metadata."""
    sid = db.upsert_show_tree("plex", {"server_id": "s1", "tmdb_id": 1, "tvdb_id": 2,
                                       "title": "Show", "seasons": []})
    db.backfill_episodes(sid, 1, [{"episode_number": 5, "air_date": "2025-01-01"}])
    db.backfill_episodes(sid, 1, [{"episode_number": 5, "title": "From TVDB"}],
                         update_only=True)
    assert (5, "From TVDB") in _season(db, sid, 1)


def test_it_does_not_create_a_season_row_it_will_never_fill(db):
    """TVDB knowing about a season TMDB doesn't must not conjure an empty one."""
    sid = _bleach(db)
    assert db.backfill_episodes(sid, 30, TVDB_SEASON_2, update_only=True) == 0
    assert _season(db, sid, 30) == []
    conn = db._get_connection()
    rows = conn.execute("SELECT id FROM seasons WHERE show_id=? AND season_number=30",
                        (sid,)).fetchall()
    conn.close()
    assert rows == []          # not even an empty season shell


# ── the wiring ───────────────────────────────────────────────────────────────
def test_the_tvdb_cascade_passes_update_only(db, monkeypatch):
    """The bug was entirely in the CALL, so pin the call."""
    from core.video.enrichment.engine import VideoEnrichmentEngine

    seen = {}

    class _Client:
        @staticmethod
        def season_episodes(_sid, sn):
            return [{"episode_number": 99, "title": "invented", "air_date": "2026-01-01"}]

    class _Worker:
        enabled = True
        client = _Client()

    eng = VideoEnrichmentEngine.__new__(VideoEnrichmentEngine)
    eng.workers = {"tvdb": _Worker()}
    eng.db = db

    real = db.backfill_episodes

    def _spy(show_id, sn, eps, *a, **kw):
        seen["update_only"] = kw.get("update_only", False)
        return real(show_id, sn, eps, *a, **kw)

    monkeypatch.setattr(db, "backfill_episodes", _spy)
    sid = _bleach(db)
    eng._cascade_tvdb_episodes(sid, 74796, [2])
    assert seen["update_only"] is True
    assert 99 not in {n for n, _t in _season(db, sid, 2)}
