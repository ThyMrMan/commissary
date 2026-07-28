"""A show numbered differently by the media server and TMDB is listed once.

Reported on Bleach: the newest episodes appeared under Season 2 AND duplicated
under Season 17, and the episodes being hunted were never found.

Plex files Bleach's newer run as S2; TMDB calls it S17. Both are "right", but
episodes are keyed UNIQUE(show_id, season_number, episode_number), so the same
episode under two season numbers is two rows — one owned (from the server), one
missing (from the TMDB backfill). And the scan's prune only inspects rows with a
server_id, so the backfilled phantom survives every rescan forever. The search
then hunts the TMDB numbering while the file sits on disk under the server's, so
it can never match.

The backfill now declines to create a row when the server already holds that
episode under a different season number. The guards matter more than the match:
a careless version of this HIDES episodes, which is worse than duplicating them.
"""

from __future__ import annotations

import pytest

from database.video_database import VideoDatabase


@pytest.fixture()
def db(tmp_path):
    return VideoDatabase(database_path=str(tmp_path / "video_library.db"))


def _show(db, seasons):
    """seasons: {season_number: [(episode_number, air_date), ...]} — as the SERVER
    reports them, so these rows carry a server_id and count as owned."""
    return db.upsert_show_tree("plex", {
        "server_id": "s1", "tmdb_id": 30984, "title": "Bleach",
        "seasons": [{"season_number": sn, "episodes": [
            {"season_number": sn, "episode_number": en, "title": "E%d" % en,
             "air_date": ad, "server_id": "ep-%d-%d" % (sn, en),
             "files": [{"path": "/tv/Bleach/S%02dE%02d.mkv" % (sn, en)}]}
            for en, ad in eps]} for sn, eps in seasons.items()]})


def _eps(db, show_id):
    conn = db._get_connection()
    try:
        return [(r["season_number"], r["episode_number"])
                for r in conn.execute(
                    "SELECT season_number, episode_number FROM episodes WHERE show_id=? "
                    "ORDER BY season_number, episode_number", (show_id,)).fetchall()]
    finally:
        conn.close()


# ── the reported bug ─────────────────────────────────────────────────────────
def test_an_episode_the_server_has_under_another_season_is_not_duplicated(db):
    """Plex says S2E01 (owned); TMDB calls the same episode S17E01."""
    sid = _show(db, {2: [(1, "2022-10-11"), (2, "2022-10-18")]})
    db.backfill_episodes(sid, 17, [
        {"episode_number": 1, "air_date": "2022-10-11", "title": "TYBW 1"},
        {"episode_number": 2, "air_date": "2022-10-18", "title": "TYBW 2"}])
    assert _eps(db, sid) == [(2, 1), (2, 2)]      # no S17 phantoms


def test_the_owned_copy_is_left_alone(db):
    sid = _show(db, {2: [(1, "2022-10-11")]})
    db.backfill_episodes(sid, 17, [{"episode_number": 1, "air_date": "2022-10-11"}])
    conn = db._get_connection()
    row = conn.execute("SELECT has_file, server_id FROM episodes WHERE show_id=?",
                       (sid,)).fetchone()
    conn.close()
    assert row["has_file"] == 1 and row["server_id"] is not None


# ── the guards: it must not HIDE anything ────────────────────────────────────
def test_a_genuinely_missing_episode_is_still_created(db):
    """The whole point of the backfill — episodes you do NOT own must appear."""
    sid = _show(db, {2: [(1, "2022-10-11")]})
    db.backfill_episodes(sid, 2, [
        {"episode_number": 1, "air_date": "2022-10-11"},
        {"episode_number": 2, "air_date": "2022-10-18"},      # not owned
    ])
    assert (2, 2) in _eps(db, sid)


def test_a_whole_season_sharing_one_air_date_is_never_suppressed(db):
    """A streaming season drops in one go, so every episode carries the same
    date. Matching on that alone would suppress all but one — the failure mode
    this guard exists to prevent."""
    sid = _show(db, {1: [(1, "2024-05-01")]})
    db.backfill_episodes(sid, 2, [
        {"episode_number": n, "air_date": "2024-05-01"} for n in range(1, 9)])
    got = [e for e in _eps(db, sid) if e[0] == 2]
    assert len(got) == 8            # ambiguous date → no judgement, all created


def test_an_episode_with_no_air_date_is_created(db):
    """No date means no evidence — never guess."""
    sid = _show(db, {2: [(1, "2022-10-11")]})
    db.backfill_episodes(sid, 17, [{"episode_number": 5, "air_date": None}])
    assert (17, 5) in _eps(db, sid)


def test_a_previously_backfilled_row_does_not_block_a_new_one(db):
    """Only a SERVER-BACKED row proves the episode is really held elsewhere. A
    row created by an earlier backfill proves nothing, or one mis-numbered
    backfill would permanently suppress the correct entry."""
    sid = _show(db, {1: [(1, "2020-01-01")]})
    db.backfill_episodes(sid, 5, [{"episode_number": 9, "air_date": "2022-10-11"}])
    assert (5, 9) in _eps(db, sid)
    db.backfill_episodes(sid, 17, [{"episode_number": 1, "air_date": "2022-10-11"}])
    assert (17, 1) in _eps(db, sid)      # the earlier backfill did not block it


def test_the_same_season_still_updates_rather_than_skipping(db):
    """Same season+episode is the ordinary gap-fill path and must be untouched."""
    sid = _show(db, {2: [(1, "2022-10-11")]})
    db.backfill_episodes(sid, 2, [
        {"episode_number": 1, "air_date": "2022-10-11", "overview": "filled in"}])
    conn = db._get_connection()
    row = conn.execute("SELECT overview FROM episodes WHERE show_id=? AND season_number=2",
                       (sid,)).fetchone()
    conn.close()
    assert row["overview"] == "filled in"


def test_specials_do_not_collide_with_a_regular_episode(db):
    """S00 shares dates with regular episodes often enough to matter; the count
    guard covers it, but pin the behaviour."""
    sid = _show(db, {0: [(1, "2022-10-11")], 2: [(1, "2022-10-18")]})
    db.backfill_episodes(sid, 17, [{"episode_number": 1, "air_date": "2022-10-18"}])
    assert (17, 1) not in _eps(db, sid)     # matched the owned S2E01, not the special


def test_the_guard_is_read_only_and_never_deletes(db):
    """It declines to CREATE. Existing rows — including phantoms already in a
    library — are not touched; cleaning those up is a separate, explicit job."""
    sid = _show(db, {2: [(1, "2022-10-11")]})
    db.backfill_episodes(sid, 17, [{"episode_number": 1, "air_date": "2022-10-11"}])
    db.backfill_episodes(sid, 17, [{"episode_number": 1, "air_date": "2022-10-11"}])
    assert _eps(db, sid) == [(2, 1)]
