"""Re-read a show's episode list from TMDB, on demand.

Reported as "Bleach season 17 shows fewer than the 50 episodes TMDB lists", and
nothing in the app could fix it:

  • a show is cascaded ONCE, then episodes_synced=1, and the background pass
    only ever picks episodes_synced=0 — it never revisits.
  • the detail page's lazy refresh is gated on `needs` (not-yet-synced / no logo
    / missing season art / no IMDb rating). An established show fails every
    clause, so opening the page does nothing. This was advice I gave and it was
    wrong; the test below pins the real behaviour.
  • "Sync show now" reconciles against PLEX, so by definition it cannot add
    episodes the media server hasn't got.
  • the nightly schedule refresh sits behind the video-automations master switch
    (off by default) and only covers the latest seasons.

Deliberately not rematch_item: that exists to point a title at a DIFFERENT TMDB
entry and clears everything derived from the old match. This only re-reads the
episode list.
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


def _show(db, episodes=3):
    return db.upsert_show_tree("plex", {
        "server_id": "s1", "tmdb_id": 30984, "title": "Bleach",
        "seasons": [{"season_number": 17, "episodes": [
            {"season_number": 17, "episode_number": n, "title": "E%d" % n}
            for n in range(1, episodes + 1)]}]})


class _Engine:
    """Stands in for the enrichment engine: adds the episodes TMDB 'has'."""
    def __init__(self, db, show_id, total, ok=True, reason=None):
        self.db, self.show_id, self.total = db, show_id, total
        self.ok, self.reason, self.calls = ok, reason, []

    def refresh_show_art(self, show_id, with_ratings=True, recent_seasons_only=False):
        self.calls.append({"show_id": show_id, "with_ratings": with_ratings,
                           "recent_seasons_only": recent_seasons_only})
        if not self.ok:
            return {"ok": False, "reason": self.reason}
        self.db.backfill_episodes(self.show_id, 17, [
            {"episode_number": n, "title": "E%d" % n} for n in range(1, self.total + 1)])
        return {"ok": True}


def _patch_engine(monkeypatch, engine):
    import core.video.enrichment.engine as mod
    monkeypatch.setattr(mod, "get_video_enrichment_engine", lambda: engine)


# ── the endpoint ─────────────────────────────────────────────────────────────
def test_it_pulls_the_episodes_tmdb_has_and_reports_the_gain(app_db, monkeypatch):
    c, db = app_db
    sid = _show(db, episodes=3)
    _patch_engine(monkeypatch, _Engine(db, sid, total=50))
    r = c.post("/api/video/detail/show/%d/rescan-episodes" % sid)
    assert r.status_code == 200
    body = r.get_json()
    assert body["ok"] is True
    assert body["before"] == 3 and body["total"] == 50 and body["added"] == 47
    assert db.show_episode_count(sid) == 50


def test_an_already_current_show_reports_zero_added(app_db, monkeypatch):
    """Not a failure — 'already up to date' is a useful answer."""
    c, db = app_db
    sid = _show(db, episodes=50)
    _patch_engine(monkeypatch, _Engine(db, sid, total=50))
    body = c.post("/api/video/detail/show/%d/rescan-episodes" % sid).get_json()
    assert body["ok"] is True and body["added"] == 0 and body["total"] == 50


def test_it_does_not_burn_the_ratings_quota(app_db, monkeypatch):
    """One OMDb call per show would spend the daily quota for something this
    action has no opinion about."""
    c, db = app_db
    sid = _show(db)
    eng = _Engine(db, sid, total=10)
    _patch_engine(monkeypatch, eng)
    c.post("/api/video/detail/show/%d/rescan-episodes" % sid)
    assert eng.calls[0]["with_ratings"] is False


def test_it_reads_every_season_not_just_the_recent_ones(app_db, monkeypatch):
    """The nightly job scopes to the latest 2 seasons on purpose; an explicit
    'go and look' must not inherit that, or an older season stays wrong."""
    c, db = app_db
    sid = _show(db)
    eng = _Engine(db, sid, total=10)
    _patch_engine(monkeypatch, eng)
    c.post("/api/video/detail/show/%d/rescan-episodes" % sid)
    assert eng.calls[0]["recent_seasons_only"] is False


@pytest.mark.parametrize("reason,code", [("no_match", 400), ("not_found", 400),
                                         ("match_error", 400)])
def test_a_refusal_is_explained_not_swallowed(app_db, monkeypatch, reason, code):
    c, db = app_db
    sid = _show(db)
    _patch_engine(monkeypatch, _Engine(db, sid, total=0, ok=False, reason=reason))
    r = c.post("/api/video/detail/show/%d/rescan-episodes" % sid)
    assert r.status_code == code
    assert r.get_json()["ok"] is False and r.get_json()["error"]


def test_a_crash_is_a_502_not_a_500_traceback(app_db, monkeypatch):
    c, db = app_db
    sid = _show(db)

    class _Boom:
        def refresh_show_art(self, *a, **k):
            raise RuntimeError("tmdb down")
    _patch_engine(monkeypatch, _Boom())
    r = c.post("/api/video/detail/show/%d/rescan-episodes" % sid)
    assert r.status_code == 502 and r.get_json()["ok"] is False


def test_it_is_admin_only(app_db, monkeypatch):
    """It spends TMDB quota and rewrites the episode table."""
    c, db = app_db
    sid = _show(db)
    _patch_engine(monkeypatch, _Engine(db, sid, total=50))
    app = c.application

    @app.before_request
    def _member():
        g.profile_id = 7; g.is_admin = False; g.can_download = True; g.allowed_sides = "both"

    assert c.post("/api/video/detail/show/%d/rescan-episodes" % sid).status_code == 403
    assert db.show_episode_count(sid) == 3      # untouched


def test_owned_episodes_survive_the_rescan(app_db, monkeypatch):
    """backfill_episodes is a gap-fill upsert — a re-scan must never turn an
    episode you HAVE back into a missing one."""
    c, db = app_db
    sid = _show(db, episodes=3)
    conn = db._get_connection()
    conn.execute("UPDATE episodes SET has_file=1 WHERE show_id=? AND episode_number<=2", (sid,))
    conn.commit(); conn.close()
    _patch_engine(monkeypatch, _Engine(db, sid, total=50))
    c.post("/api/video/detail/show/%d/rescan-episodes" % sid)
    conn = db._get_connection()
    owned = conn.execute("SELECT COUNT(*) c FROM episodes WHERE show_id=? AND has_file=1",
                         (sid,)).fetchone()["c"]
    conn.close()
    assert owned == 2


# ── why the existing routes could not do this ────────────────────────────────
def test_the_pages_lazy_refresh_skips_an_established_show():
    """Pins the behaviour I initially mis-described: a show with art, a logo, a
    rating and episodes_synced set fails every clause of `needs`, so opening its
    page fires nothing at all."""
    from pathlib import Path
    js = (Path(__file__).resolve().parent.parent / "webui" / "static" / "video"
          / "video-detail.js").read_text(encoding="utf-8")
    fn = js.split("function maybeRefreshArt", 1)[1].split("\n    function ", 1)[0]
    assert "!data.episodes_synced" in fn and "if (!needs) return;" in fn


def test_sync_show_now_is_a_server_reconcile_not_a_tmdb_read():
    """It pulls the show tree from Plex/Jellyfin, so it cannot add episodes the
    media server does not have — which is exactly the reported case."""
    from pathlib import Path
    src = (Path(__file__).resolve().parent.parent / "core" / "video"
           / "show_sync.py").read_text(encoding="utf-8")
    assert "source.show_tree(" in src


def test_the_button_is_wired_for_shows_only():
    from pathlib import Path
    js = (Path(__file__).resolve().parent.parent / "webui" / "static" / "video"
          / "video-manage-panel.js").read_text(encoding="utf-8")
    body = js.split("function bodyHtml", 1)[1].split("\n    function ", 1)[0]
    # Positional rather than sliced: the shows-only ternary contains ": '')"
    # internally (the series-type option labels), so splitting on that terminator
    # cuts the branch short. The button must sit after the series-type select and
    # before "Also known as", which is rendered for both kinds — i.e. inside the
    # shows-only branch.
    ternary = body.index("(d.kind === 'show'")
    stype = body.index("data-vmg-series-type")
    rescan = body.index("data-vmg-rescan-eps")
    aka = body.index("data-vmg-aka")
    assert ternary < stype < rescan < aka
    assert "rescanEpisodes(rsc)" in js             # and actually handled
