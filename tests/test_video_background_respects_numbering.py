"""The unattended passes must not undo an out-of-place clean-up.

Two background paths cascade episodes: the show-match cascade and the
episode-sync queue. Both took their season numbers from the matching provider
unconditionally — so for a show whose numbering belongs to the OTHER provider,
they re-create exactly the rows the clean-up removed.

That is worse than the original bug: it runs unattended, so the repair appears
to work and then quietly reverts, with nothing in the UI to suggest why.

Failing OPEN is deliberate. If the check can't be resolved, the cascade still
runs — an episode list that shouldn't have been written is a visible, fixable
mess; refusing to write episodes at all is a silent hole in the library.
"""

from __future__ import annotations

import pytest

from database.video_database import VideoDatabase


@pytest.fixture()
def db(tmp_path):
    return VideoDatabase(database_path=str(tmp_path / "video_library.db"))


def _bleach(db):
    """17 seasons from Plex — the structure TMDB cannot serve."""
    return db.upsert_show_tree("plex", {
        "server_id": "44632", "tmdb_id": 30984, "tvdb_id": 74796, "title": "Bleach",
        "seasons": [{"season_number": sn, "episodes": [
            {"season_number": sn, "episode_number": 1, "air_date": "2005-03-01",
             "server_id": "s%d" % sn, "files": [{"path": "/tv/B/S%02dE01.mkv" % sn}]}]}
            for sn in range(1, 18)]})


class _Worker:
    """Just enough of the worker to exercise _owns_numbering."""

    def __init__(self, db, service):
        from core.video.enrichment.worker import VideoEnrichmentWorker
        self.db, self.service = db, service
        self._owns_numbering = VideoEnrichmentWorker._owns_numbering.__get__(self)


def _engine(db, tmdb_seasons, tvdb_seasons, monkeypatch):
    import core.video.enrichment.engine as engmod

    class _C:
        def __init__(self, seasons):
            self._s = seasons

        def season_numbers(self, _i):
            return self._s

    e = engmod.VideoEnrichmentEngine.__new__(engmod.VideoEnrichmentEngine)
    e.db = db
    e.workers = {"tmdb": type("W", (), {"enabled": True, "client": _C(tmdb_seasons)})(),
                 "tvdb": type("W", (), {"enabled": True, "client": _C(tvdb_seasons)})()}
    monkeypatch.setattr(engmod, "get_video_enrichment_engine", lambda *a, **k: e)
    return e


# ── the guard ────────────────────────────────────────────────────────────────
def test_tmdb_does_not_own_a_show_tvdb_matches(db, monkeypatch):
    sid = _bleach(db)
    _engine(db, [0, 1, 2], [0] + list(range(1, 18)), monkeypatch)
    assert _Worker(db, "tmdb")._owns_numbering(sid, [0, 1, 2]) is False


def test_tvdb_owns_it(db, monkeypatch):
    sid = _bleach(db)
    _engine(db, [0, 1, 2], [0] + list(range(1, 18)), monkeypatch)
    assert _Worker(db, "tvdb")._owns_numbering(sid, [0, 1, 2]) is True


def test_an_ordinary_show_still_belongs_to_tmdb(db, monkeypatch):
    sid = db.upsert_show_tree("plex", {
        "server_id": "s1", "tmdb_id": 1, "tvdb_id": 2, "title": "Normal",
        "seasons": [{"season_number": n, "episodes": [
            {"season_number": n, "episode_number": 1, "air_date": "2020-01-01",
             "server_id": "e%d" % n, "files": [{"path": "/tv/N/S0%dE01.mkv" % n}]}]}
            for n in (1, 2, 3)]})
    _engine(db, [1, 2, 3], [0, 1, 2, 3], monkeypatch)
    assert _Worker(db, "tmdb")._owns_numbering(sid, [1, 2, 3]) is True


def test_a_pinned_override_is_respected(db, monkeypatch):
    sid = db.upsert_show_tree("plex", {
        "server_id": "s1", "tmdb_id": 1, "tvdb_id": 2, "title": "Normal",
        "seasons": [{"season_number": n, "episodes": [
            {"season_number": n, "episode_number": 1, "air_date": "2020-01-01",
             "server_id": "e%d" % n, "files": [{"path": "/tv/N/S0%dE01.mkv" % n}]}]}
            for n in (1, 2, 3)]})
    db.set_show_episode_source(sid, "tvdb")
    _engine(db, [1, 2, 3], [1, 2, 3], monkeypatch)
    assert _Worker(db, "tmdb")._owns_numbering(sid, [1, 2, 3]) is False
    assert _Worker(db, "tvdb")._owns_numbering(sid, [1, 2, 3]) is True


# ── it fails open ────────────────────────────────────────────────────────────
def test_a_broken_resolution_still_cascades(db, monkeypatch):
    """Refusing to write episodes is the more damaging way to be wrong."""
    import core.video.enrichment.engine as engmod

    def _boom(*a, **k):
        raise RuntimeError("engine unavailable")

    monkeypatch.setattr(engmod, "get_video_enrichment_engine", _boom)
    assert _Worker(db, "tmdb")._owns_numbering(_bleach(db), [1, 2]) is True


def test_an_unknown_show_still_cascades(db, monkeypatch):
    _engine(db, [1, 2], [1, 2], monkeypatch)
    assert _Worker(db, "tmdb")._owns_numbering(999999, [1, 2]) is True


# ── the queue must not loop on a skipped show ────────────────────────────────
def test_a_skipped_show_is_marked_synced_rather_than_re_picked(db, monkeypatch):
    """episode_sync_next() picks shows with episodes_synced=0. Skipping the
    cascade without marking it would hand back the same show forever."""
    import pathlib
    src = pathlib.Path("core/video/enrichment/worker.py").read_text(encoding="utf-8")
    skip = src.split("if self._owns_numbering(show[\"id\"], nums):")[1]
    assert "mark_episodes_synced(show[\"id\"])" in skip.split("return True")[0]
