"""The chosen numbering source is the one that supplies episodes.

Wiring test for core/video/episode_numbering, against the real Bleach shape:
Plex reports 17 seasons, TVDB has 17, TMDB has 3 (specials, the 366-episode
2004-2012 run, and Thousand-Year Blood War as its "season 2").

Two things had to become true:
  * TMDB must stop writing its season 2 (TYBW) over Plex's season 2 (2005), and
  * season 17 — which TMDB does not have at all — must finally be fillable,
    because that is where the library keeps TYBW and where the episodes that
    were never found actually live.

For an ordinary show, where both providers agree, nothing changes: TMDB stays
the source and TVDB stays a gap-fill.
"""

from __future__ import annotations

import pytest

from core.video.enrichment.engine import VideoEnrichmentEngine
from database.video_database import VideoDatabase


@pytest.fixture()
def db(tmp_path):
    return VideoDatabase(database_path=str(tmp_path / "video_library.db"))


def _bleach(db):
    """Plex's structure: 17 seasons. Only 2 and 17 populated, enough to score."""
    return db.upsert_show_tree("plex", {
        "server_id": "44632", "tmdb_id": 30984, "tvdb_id": 74796, "title": "Bleach",
        "seasons": [{"season_number": sn, "episodes": [
            {"season_number": sn, "episode_number": 1, "title": "S%d" % sn,
             "air_date": "2005-03-01", "server_id": "s%d" % sn,
             "files": [{"path": "/tv/B/S%02dE01.mkv" % sn}]}]}
            for sn in range(1, 18)]})


class _Client:
    def __init__(self, seasons, eps_by_season=None):
        self._seasons, self._eps = seasons, (eps_by_season or {})

    def season_numbers(self, _sid):
        return self._seasons

    def season_episodes(self, _sid, sn):
        return self._eps.get(sn, [])

    def match(self, _kind, _title, _year, known_id=None):
        return {"id": known_id or 1,
                "metadata": {"seasons": [{"season_number": n} for n in self._seasons]}}


class _Worker:
    def __init__(self, client):
        self.enabled, self.client = True, client
        self.cascaded = None

    def _cascade_episodes(self, show_id, tv_id, nums, mark_synced=True):
        self.cascaded = list(nums)


def _engine(db, tmdb_worker, tvdb_worker):
    e = VideoEnrichmentEngine.__new__(VideoEnrichmentEngine)
    e.workers, e.db = {"tmdb": tmdb_worker, "tvdb": tvdb_worker}, db
    return e


# ── the decision, on real data ───────────────────────────────────────────────
def test_bleach_resolves_to_tvdb(db):
    sid = _bleach(db)
    eng = _engine(db, _Worker(_Client([0, 1, 2])),
                  _Worker(_Client([0] + list(range(1, 18)))))
    info = db.show_match_info(sid)
    assert eng._episode_source(sid, info, [0, 1, 2]) == "tvdb"


def test_an_ordinary_show_stays_on_tmdb(db):
    sid = db.upsert_show_tree("plex", {
        "server_id": "s1", "tmdb_id": 1, "tvdb_id": 2, "title": "Normal",
        "seasons": [{"season_number": n, "episodes": [
            {"season_number": n, "episode_number": 1, "air_date": "2020-01-0%d" % n,
             "server_id": "e%d" % n, "files": [{"path": "/tv/N/S0%dE01.mkv" % n}]}]}
            for n in (1, 2, 3)]})
    eng = _engine(db, _Worker(_Client([1, 2, 3])), _Worker(_Client([0, 1, 2, 3])))
    assert eng._episode_source(sid, db.show_match_info(sid), [1, 2, 3]) == "tmdb"


def test_the_per_show_override_is_obeyed(db):
    sid = _bleach(db)
    assert db.set_show_episode_source(sid, "tmdb") is True
    eng = _engine(db, _Worker(_Client([0, 1, 2])),
                  _Worker(_Client([0] + list(range(1, 18)))))
    assert eng._episode_source(sid, db.show_match_info(sid), [0, 1, 2]) == "tmdb"
    assert db.set_show_episode_source(sid, "auto") is True
    assert eng._episode_source(sid, db.show_match_info(sid), [0, 1, 2]) == "tvdb"


def test_a_bad_override_is_rejected_rather_than_stored(db):
    sid = _bleach(db)
    assert db.set_show_episode_source(sid, "netflix") is False
    assert db.show_match_info(sid)["episode_source"] is None


# ── the cascade follows the decision ─────────────────────────────────────────
def test_tmdb_does_not_cascade_when_tvdb_owns_the_numbering(db):
    """The actual damage: TMDB writing its season 2 over Plex's."""
    sid = _bleach(db)
    tmdb = _Worker(_Client([0, 1, 2]))
    tvdb = _Worker(_Client([0] + list(range(1, 18))))
    eng = _engine(db, tmdb, tvdb)
    eng._backfill_ratings = lambda *a, **k: None
    db.enrichment_apply = lambda *a, **k: None
    eng.refresh_show_art(sid, with_ratings=False)
    assert tmdb.cascaded is None            # TMDB's season numbers never applied


def test_tmdb_still_cascades_for_an_ordinary_show(db):
    sid = db.upsert_show_tree("plex", {
        "server_id": "s1", "tmdb_id": 1, "tvdb_id": 2, "title": "Normal",
        "seasons": [{"season_number": n, "episodes": [
            {"season_number": n, "episode_number": 1, "air_date": "2020-01-0%d" % n,
             "server_id": "e%d" % n, "files": [{"path": "/tv/N/S0%dE01.mkv" % n}]}]}
            for n in (1, 2, 3)]})
    tmdb = _Worker(_Client([1, 2, 3]))
    eng = _engine(db, tmdb, _Worker(_Client([1, 2, 3])))
    eng._backfill_ratings = lambda *a, **k: None
    db.enrichment_apply = lambda *a, **k: None
    eng.refresh_show_art(sid, with_ratings=False)
    assert tmdb.cascaded == [1, 2, 3]


def test_season_17_can_finally_be_filled(db):
    """The whole point. TMDB has no season 17, so nothing could ever fill the
    season where the library actually keeps Thousand-Year Blood War."""
    sid = _bleach(db)
    tvdb_eps = {17: [{"episode_number": n, "title": "TYBW %d" % n,
                      "air_date": "2025-0%d-01" % (n % 9 + 1)} for n in range(1, 51)]}
    eng = _engine(db, _Worker(_Client([0, 1, 2])),
                  _Worker(_Client([0] + list(range(1, 18)), tvdb_eps)))
    eng._cascade_tvdb_episodes(sid, 74796, [0, 1, 2], authoritative=True)
    conn = db._get_connection()
    n = conn.execute("SELECT COUNT(*) c FROM episodes WHERE show_id=? AND season_number=17",
                     (sid,)).fetchone()["c"]
    conn.close()
    assert n == 50                           # 1 owned + 49 now listed as missing


def test_tvdb_creates_nothing_when_it_is_only_the_gap_fill(db):
    """An ordinary show must not gain rows from the enrichment pass."""
    sid = _bleach(db)
    tvdb_eps = {2: [{"episode_number": 99, "title": "invented", "air_date": "2026-01-01"}]}
    eng = _engine(db, _Worker(_Client([0, 1, 2])),
                  _Worker(_Client([0, 1, 2], tvdb_eps)))
    eng._cascade_tvdb_episodes(sid, 74796, [2], authoritative=False)
    conn = db._get_connection()
    n = conn.execute("SELECT COUNT(*) c FROM episodes WHERE show_id=? AND season_number=2 "
                     "AND episode_number=99", (sid,)).fetchone()["c"]
    conn.close()
    assert n == 0


# ── the structure probe ──────────────────────────────────────────────────────
def test_only_server_backed_seasons_count_as_the_structure(db):
    """Scoring a provider against seasons a mis-numbered backfill invented would
    let the mistake justify itself."""
    sid = _bleach(db)
    db.backfill_episodes(sid, 40, [{"episode_number": 1, "air_date": "2030-01-01"}])
    assert 40 in db.show_season_numbers(sid)
    assert 40 not in db.server_season_numbers(sid)


def test_a_tvdb_probe_failure_leaves_the_default_in_place(db):
    class _Boom:
        def season_numbers(self, _sid):
            raise RuntimeError("tvdb down")

    sid = _bleach(db)
    eng = _engine(db, _Worker(_Client([0, 1, 2])), _Worker(_Boom()))
    assert eng._episode_source(sid, db.show_match_info(sid), [0, 1, 2]) == "tmdb"
