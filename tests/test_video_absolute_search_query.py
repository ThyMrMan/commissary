"""Manual search ASKS the tracker by absolute number, not just SxxExx.

Reported: a show the tracker lists as 'EP81' never matched, because the search
went out as 'S04E15'.

The matching half of this was already fixed (tests/test_video_manual_search_
episode_hints.py): the interactive endpoints compute the air-date and absolute
hints and hand them to the ranker. But they handed them ONLY to the ranker --
the query itself was still built as ``build_query(..., season=, episode=)`` with
no ``absolute`` and no ``series_type``. So the tracker was asked for 'Show
S04E15', returned nothing for it, and the absolute-aware ranking had no
candidate to accept. The capability was present and unreachable.

The second half is that ``build_query`` emitted the absolute form only for
``series_type == 'anime'``. Nothing derives that tag automatically -- it is set
by hand per show, or by a Library's ``default_series_type`` -- so 'untyped' is
the ordinary state of a show rather than evidence that it is not anime, and
requiring the tag made the query depend on something most shows do not carry.

The unattended drain is deliberately NOT changed here: ``_absolute_hint`` still
withholds a later season's absolute number from an untyped show, because an
unattended grab that guesses wrong downloads the wrong episode. See
tests/test_video_anime_absolute_search.py, which pins that boundary.
"""

from __future__ import annotations

import pytest
from flask import Flask, g

from core.video.slskd_search import build_query
from database.video_database import VideoDatabase


# ── the query form itself ────────────────────────────────────────────────────
def test_an_untyped_show_is_asked_for_by_absolute_number():
    """The reported case. Requiring series_type=='anime' here meant the absolute
    query depended on a tag nothing sets automatically."""
    assert build_query("episode", "Show", season=4, episode=15,
                       absolute=81) == "Show 81"


def test_an_anime_show_is_unchanged():
    assert build_query("episode", "One Piece", season=20, episode=45,
                       absolute=1071, series_type="anime") == "One Piece 1071"


def test_a_show_explicitly_marked_standard_keeps_sxxexx():
    """The safety boundary, and the reason this is not simply ungated: wanting
    S04E15 with absolute 81, a release named 'Show - 81' is a real risk for a
    show somebody has actually told us is standard. An explicit tag is believed."""
    assert build_query("episode", "Show", season=4, episode=15,
                       absolute=81, series_type="standard") == "Show S04E15"


def test_a_daily_show_still_prefers_its_air_date():
    assert build_query("episode", "The Daily Show", season=30, episode=88,
                       air_date="2026-07-08", absolute=600,
                       series_type="daily") == "The Daily Show 2026.07.08"


def test_a_daily_show_without_an_air_date_does_not_fall_back_to_absolute():
    """Dailies are matched on date. Offering the absolute number instead would
    be the same wrong-episode risk as the standard case above."""
    assert build_query("episode", "The Daily Show", season=30, episode=88,
                       absolute=600, series_type="daily") == "The Daily Show S30E88"


def test_no_absolute_number_means_no_change():
    assert build_query("episode", "Breaking Bad", season=1, episode=2) == "Breaking Bad S01E02"


# ── the absolute query is ADDITIVE, never a replacement ──────────────────────
def test_prowlarr_runs_both_the_absolute_and_the_sxxexx_query():
    """An indexer that numbers this show by season must still be reachable, so
    the absolute query is an extra strategy rather than a substitution."""
    from core.video.prowlarr_search import build_strategies
    strat = build_strategies("episode", "Show", season=4, episode=15, absolute=81)
    queries = [q for (t, q, _x) in strat if t == "search"]
    assert "Show 81" in queries
    assert "Show S04E15" in queries


# ── the endpoints actually hand it to the query ──────────────────────────────
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


def _seed_four_seasons(db, tmdb_id=700):
    """66 episodes across seasons 1-3, so S04E15 is absolute 81."""
    seasons = []
    for s, n in ((1, 25), (2, 25), (3, 16), (4, 20)):
        seasons.append({"season_number": s, "episodes": [
            {"season_number": s, "episode_number": e, "title": "E%d" % e}
            for e in range(1, n + 1)]})
    return db.upsert_show_tree("plex", {"server_id": "s1", "tmdb_id": tmdb_id,
                                        "title": "Show", "seasons": seasons})


def _captured_prowlarr_query(client, db, monkeypatch, path, **body_extra):
    import core.video.prowlarr_search as ps
    seen = {}

    def _fake(scope, title, **kw):
        seen.update(kw); seen["scope"], seen["title"] = scope, title
        return {"configured": True, "hits": []}

    monkeypatch.setattr(ps, "prowlarr_search", _fake)
    r = client.post(path, json={"scope": "episode", "title": "Show", "source": "torrent",
                                "media_id": 700, "media_source": "tmdb",
                                "season": 4, "episode": 15, **body_extra})
    assert r.status_code == 200, r.get_data(as_text=True)
    return seen


def test_the_absolute_number_reaches_the_search_endpoint_query(app_db, monkeypatch):
    """The bug the user hit: the endpoint knew absolute 81 and asked for S04E15."""
    client, db = app_db
    _seed_four_seasons(db)
    seen = _captured_prowlarr_query(client, db, monkeypatch, "/api/video/downloads/search")
    assert seen["absolute"] == 81
    assert build_query("episode", "Show", season=4, episode=15,
                       absolute=seen["absolute"],
                       series_type=seen["series_type"]) == "Show 81"


def test_the_absolute_number_reaches_the_start_endpoint_query(app_db, monkeypatch):
    client, db = app_db
    _seed_four_seasons(db)
    seen = _captured_prowlarr_query(client, db, monkeypatch,
                                    "/api/video/downloads/search/start")
    assert seen["absolute"] == 81


def test_an_explicitly_standard_show_is_still_asked_for_by_sxxexx(app_db, monkeypatch):
    """End to end, the boundary the query form test pins in isolation."""
    client, db = app_db
    _seed_four_seasons(db)
    db.set_series_type_override(700, "standard")
    seen = _captured_prowlarr_query(client, db, monkeypatch, "/api/video/downloads/search")
    assert seen["series_type"] == "standard"
    assert build_query("episode", "Show", season=4, episode=15,
                       absolute=seen["absolute"],
                       series_type=seen["series_type"]) == "Show S04E15"


def test_a_library_row_type_reaches_the_query_without_an_override(app_db, monkeypatch):
    """A Library's default_series_type, and the per-show Series Type control,
    write the shows ROW; only the tmdb override table records a choice made
    before you own the show. Reading the override alone reports a daily show as
    untyped, and it would then be asked for by absolute number instead of by
    air date -- the wrong-episode risk this whole boundary exists to avoid."""
    client, db = app_db
    sid = _seed_four_seasons(db)
    db.set_show_series_type(sid, "daily")            # no override written
    seen = _captured_prowlarr_query(client, db, monkeypatch,
                                    "/api/video/downloads/search")
    assert seen["series_type"] == "daily"


# ── resolving the type ───────────────────────────────────────────────────────
def test_effective_series_type_prefers_the_override_then_the_library_row(app_db):
    """An explicit 'standard' only ever lands in the override table -- the shows
    column stores standard as NULL -- so reading the row alone cannot tell
    'standard' from 'nobody has typed this'."""
    _, db = app_db
    sid = _seed_four_seasons(db)
    assert db.effective_series_type(700) is None          # untyped
    db.set_show_series_type(sid, "anime")                 # library row only
    assert db.effective_series_type(700) == "anime"
    db.set_series_type_override(700, "standard")          # override wins
    assert db.effective_series_type(700) == "standard"


def test_effective_series_type_is_defensive(app_db):
    """A type lookup must never be the thing that breaks a search."""
    _, db = app_db
    assert db.effective_series_type(None) is None
    assert db.effective_series_type("nonsense") is None
    assert db.effective_series_type(999999) is None       # no such show
