"""The video indexes the hot lookups depend on actually exist and get used.

Adapted from upstream 3.2.0's perf sweep (21276350). ``movies`` had a tmdb_id
index from day one; ``shows`` never did, so every library-id lookup, watchlist
state check and calendar hit was a full table scan — and a discover rail hits it
once per row. Measured here on a 3,400-show / 136,000-episode database with a
fresh connection per run:

    shows by tmdb_id   WITH 0.0034 ms (covering index)  WITHOUT 0.0676 ms (SCAN)

An index is invisible when it regresses: nothing fails, the page just gets
slower. So these assert the PLAN, not the timing — timing is machine-dependent
and would flake, while "did the planner choose a scan" is exact.

Upstream added a third, episodes(show_id, season_number, episode_number).
Deliberately not taken — see the note in _POST_INDEXES: our schema declares
UNIQUE on exactly those columns, so SQLite's implicit index already covers it.
Measured identical (1.0 ms) with and without on 120k episodes.
"""

from __future__ import annotations

import sqlite3

import pytest

from database.video_database import VideoDatabase


@pytest.fixture()
def db(tmp_path):
    d = VideoDatabase(database_path=str(tmp_path / "video_library.db"))
    conn = d._get_connection()
    conn.execute("INSERT INTO shows(server_source,server_id,title,tmdb_id) "
                 "VALUES('plex','s1','Silo',125988)")
    conn.execute("INSERT INTO seasons(show_id,season_number) VALUES(1,3)")
    conn.execute("INSERT INTO episodes(show_id,season_id,season_number,episode_number,"
                 "title,has_file) VALUES(1,1,3,6,'The Dive',1)")
    conn.commit()
    conn.close()
    return d


def _plan(db, sql, args=()):
    conn = db._get_connection()
    try:
        return " | ".join(str(r[-1]) for r in conn.execute("EXPLAIN QUERY PLAN " + sql, args))
    finally:
        conn.close()


def test_the_indexes_are_created(db):
    conn = db._get_connection()
    try:
        have = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='index'")}
    finally:
        conn.close()
    for name in ("idx_shows_tmdb", "idx_episodes_show_file"):
        assert name in have, name


def test_a_show_lookup_by_tmdb_id_is_not_a_full_scan(db):
    """The one that mattered: a discover rail hits this once per row."""
    plan = _plan(db, "SELECT id FROM shows WHERE tmdb_id=?", (125988,))
    assert "idx_shows_tmdb" in plan, plan
    assert "SCAN shows" not in plan, plan


def test_the_owned_episode_rollup_uses_the_covering_index(db):
    plan = _plan(db, "SELECT COUNT(*) FROM episodes WHERE show_id=? AND has_file=1", (1,))
    assert "idx_episodes_show_file" in plan, plan
    assert "SCAN episodes" not in plan, plan


def test_the_show_season_episode_join_is_already_covered(db):
    """Why upstream's third index was not taken. If this ever stops being true —
    someone drops the UNIQUE constraint — the join goes unindexed and the index
    we declined becomes worth adding after all."""
    plan = _plan(db,
                 "SELECT id FROM episodes WHERE show_id=? AND season_number=? AND episode_number=?",
                 (1, 3, 6))
    assert "SCAN episodes" not in plan, plan
    assert "autoindex" in plan.lower() or "idx_episodes" in plan, (
        "the UNIQUE (show_id, season_number, episode_number) constraint no longer "
        "provides an index for this join — reconsider upstream's third index: " + plan
    )


def test_indexes_survive_reopening_an_existing_database(tmp_path):
    """They are created in _POST_INDEXES, which runs after the migration ALTERs.
    A retrofit that only fired for fresh databases would leave every existing
    install on the slow path — which is the case this fix exists for."""
    import database.video_database as vdb

    path = str(tmp_path / "existing.db")
    VideoDatabase(database_path=path)          # first open creates the schema
    conn = sqlite3.connect(path)
    conn.execute("DROP INDEX IF EXISTS idx_shows_tmdb")     # simulate a pre-fix database
    conn.commit()
    conn.close()

    # _initialize_once caches per path for the life of the PROCESS, so a second
    # construction here would be a no-op and prove nothing. Clearing it is what
    # makes this a restart, which is when a real install gets the retrofit.
    vdb._initialized_paths.clear()
    VideoDatabase(database_path=path)          # reopening must retrofit it
    conn = sqlite3.connect(path)
    try:
        have = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='index'")}
    finally:
        conn.close()
    assert "idx_shows_tmdb" in have, "an existing install never gets the index"
