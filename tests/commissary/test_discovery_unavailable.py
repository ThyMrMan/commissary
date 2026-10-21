"""Discovery counts that treat a Wing It guess per tab, and "Not available"."""

from __future__ import annotations

import json
from unittest.mock import MagicMock

import pytest
from flask import Flask

from core.commissary import discovery_fixes as df
from core.commissary import discovery_unavailable as du
from core.discovery.manual_match import should_rediscover


def _engine(monkeypatch, *modules):
    from core.matching_engine import MusicMatchingEngine
    engine = MusicMatchingEngine()
    for module in modules:
        monkeypatch.setattr(module, "_matching_engine", lambda: engine, raising=False)


def _app(*blueprints):
    app = Flask(__name__)
    for bp in blueprints:
        app.register_blueprint(bp)
    du.register(app)
    return app.test_client()


# ── counts ──────────────────────────────────────────────────────────────────
@pytest.mark.parametrize("platform, counted", [("deezer", True), ("qobuz", True), ("tidal", True),
                                               ("spotify_public", True), ("itunes_link", True),
                                               ("beatport", True), ("youtube", False),
                                               ("ytmusic", False), ("listenbrainz", False)])
def test_a_guess_counts_as_a_match_only_where_the_tab_counted_it(platform, counted):
    assert df.fix_adds_a_match("Wing It", platform) is (not counted)
    assert df.unmatch_removes_a_match("wing-it", platform) is counted
    assert df.fix_adds_a_match("Not Found", platform) is True
    assert df.unmatch_removes_a_match("found", platform) is True
    assert df.fix_adds_a_match("Found", platform) is False


@pytest.fixture
def sp(monkeypatch):
    import api.source_playlists as routes
    _engine(monkeypatch, routes)
    monkeypatch.setattr(routes, "_get_active_discovery_source", lambda: "itunes")
    db = MagicMock()
    db.save_discovery_cache_match.return_value = True
    monkeypatch.setattr(routes, "get_database", lambda: db)
    return routes


PICK = {"id": "1", "name": "Song", "artists": ["A"], "album": "Al", "duration_ms": 1}


def test_fixing_a_counted_guess_on_a_tab_is_not_a_second_match(sp, monkeypatch):
    monkeypatch.setitem(sp.deezer_discovery_states, "d1", {
        "discovery_results": [{"index": 0, "status": "Wing It", "wing_it_fallback": True,
                               "deezer_track": {"name": "Song", "artists": ["A"]}}],
        "spotify_matches": 1})
    resp = _app(sp.bp).post("/api/deezer/discovery/update_match",
                            json={"identifier": "d1", "track_index": 0, "spotify_track": PICK})
    assert resp.status_code == 200, resp.get_json()
    assert sp.deezer_discovery_states["d1"]["spotify_matches"] == 1


def test_unmatching_a_youtube_guess_leaves_the_count_alone(sp, monkeypatch):
    monkeypatch.setitem(sp.youtube_playlist_states, "y1", {
        "playlist": {"tracks": [{"name": "Song", "artists": ["A"]}]},
        "discovery_results": [{"index": 0, "status": "Wing It", "status_class": "wing-it"}],
        "spotify_matches": 3, "wing_it_count": 1})
    resp = _app(sp.bp).post("/api/youtube/discovery/unmatch", json={"identifier": "y1", "track_index": 0})
    assert resp.status_code == 200
    state = sp.youtube_playlist_states["y1"]
    assert (state["spotify_matches"], state["wing_it_count"]) == (3, 0)


# ── "Not available" ─────────────────────────────────────────────────────────
def _youtube_state(**over):
    state = {"phase": "discovered",
             "playlist": {"tracks": [{"name": "Rare B-side", "artists": ["A"], "duration_ms": 61000}]},
             "discovery_results": [{"index": 0, "status": "Found", "status_class": "found",
                                    "spotify_data": {"id": "x"}, "matched_data": {"id": "x"}}],
             "spotify_matches": 1}
    state.update(over)
    return state


def test_marking_clears_the_match_and_the_count(sp, monkeypatch):
    monkeypatch.setitem(sp.youtube_playlist_states, "y2", _youtube_state())
    data = _app(sp.bp).post("/api/commissary/discovery/unavailable",
                            json={"identifier": "y2", "track_index": 0, "platform": "youtube"}).get_json()
    state = sp.youtube_playlist_states["y2"]
    row = state["discovery_results"][0]
    assert (row["status"], row["status_class"], row["spotify_data"]) == ("Not available", "unavailable", None)
    assert state["spotify_matches"] == 0 and data["spotify_matches"] == 0
    assert state["playlist"]["tracks"][0]["unavailable"] is True


def test_available_again_and_a_fix_both_undo_it(sp, monkeypatch):
    monkeypatch.setitem(sp.youtube_playlist_states, "y3", _youtube_state())
    client = _app(sp.bp)
    client.post("/api/commissary/discovery/unavailable", json={"identifier": "y3", "track_index": 0, "platform": "youtube"})
    client.post("/api/commissary/discovery/unavailable",
                json={"identifier": "y3", "track_index": 0, "platform": "youtube", "unavailable": False})
    assert sp.youtube_playlist_states["y3"]["discovery_results"][0]["status_class"] == "not-found"
    client.post("/api/commissary/discovery/unavailable", json={"identifier": "y3", "track_index": 0, "platform": "youtube"})
    client.post("/api/youtube/discovery/update_match",
                json={"identifier": "y3", "track_index": 0, "spotify_track": PICK})
    assert sp.youtube_playlist_states["y3"]["playlist"]["tracks"][0]["unavailable"] is False


def test_not_while_discovery_runs_and_not_a_missing_state(sp, monkeypatch):
    monkeypatch.setitem(sp.youtube_playlist_states, "y4", _youtube_state(phase="discovering"))
    client = _app(sp.bp)
    assert client.post("/api/commissary/discovery/unavailable",
                       json={"identifier": "y4", "track_index": 0, "platform": "youtube"}).status_code == 409
    assert client.post("/api/commissary/discovery/unavailable",
                       json={"identifier": "nope", "track_index": 0, "platform": "youtube"}).status_code == 404


def test_beatport_rows_are_found_by_their_own_index(monkeypatch):
    import api.beatport_charts as bc
    import api.source_playlists as routes
    rows = [{"index": 5, "status": "Not Found", "status_class": "not-found"},
            {"index": 2, "status": "Not Found", "status_class": "not-found"}]
    monkeypatch.setitem(bc.beatport_chart_states if hasattr(bc, "beatport_chart_states")
                        else routes.beatport_chart_states, "b1",
                        {"phase": "discovered", "discovery_results": rows, "spotify_matches": 0})
    resp = _app(routes.bp).post("/api/commissary/discovery/unavailable",
                                json={"identifier": "b1", "track_index": 2, "platform": "beatport"})
    assert resp.status_code == 200, resp.get_json()
    assert (rows[0]["status_class"], rows[1]["status_class"]) == ("not-found", "unavailable")


def test_a_mirrored_mark_is_saved_and_reopens_as_not_available(sp, monkeypatch, tmp_path):
    from database.music_database import MusicDatabase
    import api.mirrored_playlists as mp
    db = MusicDatabase(str(tmp_path / "music.db"))
    monkeypatch.setattr("database.music_database.get_database", lambda *a, **k: db)
    with db._get_connection() as conn:
        cur = conn.cursor()
        cur.execute("INSERT INTO mirrored_playlists (source, source_playlist_id, name, profile_id) "
                    "VALUES ('youtube', 'm1', 'M', 1)")
        pl = cur.lastrowid
        cur.execute("INSERT INTO mirrored_playlist_tracks (playlist_id, position, track_name, artist_name) "
                    "VALUES (?, 1, 'Rare B-side', 'A')", (pl,))
        track_id = cur.lastrowid
        conn.commit()
    monkeypatch.setitem(sp.youtube_playlist_states, f"mirrored_{pl}", _youtube_state(playlist={
        "tracks": [{"name": "Rare B-side", "artists": ["A"], "db_track_id": track_id}]}))
    resp = _app(sp.bp).post("/api/commissary/discovery/unavailable",
                            json={"identifier": f"mirrored_{pl}", "track_index": 0, "platform": "mirrored"})
    assert resp.status_code == 200, resp.get_json()
    extra = db.get_mirrored_playlist_tracks(pl)[0]["extra_data"]
    extra = json.loads(extra) if isinstance(extra, str) else extra
    assert du.is_marked(extra)
    assert should_rediscover(extra) is False, "the Playlist Pipeline leaves it alone"

    sp.youtube_playlist_states.pop(f"mirrored_{pl}", None)
    monkeypatch.setattr(mp, "get_database", lambda: db)
    monkeypatch.setattr(mp, "_get_active_discovery_source", lambda: "itunes")
    monkeypatch.setattr(mp, "_spotify_client", lambda: None, raising=False)
    monkeypatch.setattr(mp, "mirrored_playlist_visible", lambda *_a, **_k: True, raising=False)
    prep = _app(mp.bp).post(f"/api/mirrored-playlists/{pl}/prepare-discovery").get_json()
    rows = (prep.get("results") or prep.get("discovery_results")
            or sp.youtube_playlist_states.get(f"mirrored_{pl}", {}).get("discovery_results"))
    assert rows and rows[0]["status_class"] == "unavailable", prep


def test_the_youtube_worker_never_searches_a_marked_track():
    from core.discovery.youtube import YoutubeDiscoveryDeps, run_youtube_discovery_worker
    states = {"y5": {"phase": "discovering", "discovery_results": [], "spotify_matches": 0,
                     "playlist": {"name": "P", "tracks": [{"id": "v1", "name": "Rare", "artists": ["A"],
                                                           "unavailable": True, "duration_ms": 0}]}}}
    fallback = MagicMock()
    deps = YoutubeDiscoveryDeps(
        youtube_playlist_states=states, spotify_client=None, matching_engine=MagicMock(),
        pause_enrichment_workers=lambda *_a: {}, resume_enrichment_workers=lambda *_a: None,
        get_active_discovery_source=lambda: "itunes", get_metadata_fallback_client=lambda: fallback,
        get_discovery_cache_key=MagicMock(), validate_discovery_cache_artist=MagicMock(),
        extract_artist_name=lambda a: a, spotify_rate_limited=lambda: False,
        discovery_score_candidates=MagicMock(), get_metadata_cache=MagicMock(),
        build_discovery_wing_it_stub=MagicMock(), get_database=MagicMock(), add_activity_item=MagicMock())
    run_youtube_discovery_worker("y5", deps)
    assert states["y5"]["discovery_results"][0]["status_class"] == "unavailable"
    fallback.search_tracks.assert_not_called()
    deps.get_discovery_cache_key.assert_not_called()


def test_fixing_a_counted_beatport_guess_is_not_a_second_match(monkeypatch):
    import api.beatport_charts as bc
    from core.matching_engine import MusicMatchingEngine
    engine = MusicMatchingEngine()
    for name, value in (("_matching_engine", lambda: engine), ("_get_active_discovery_source", lambda: "itunes"),
                        ("_join_artist_names", lambda a: ", ".join(a)), ("_extract_artist_name", lambda a: a),
                        ("get_database", lambda: MagicMock())):
        monkeypatch.setattr(bc, name, value, raising=False)
    monkeypatch.setitem(bc.beatport_chart_states, "b2", {
        "discovery_results": [{"index": 0, "status": "Wing It", "wing_it_fallback": True}],
        "spotify_matches": 4})
    app = Flask(__name__)
    app.register_blueprint(bc.bp)
    resp = app.test_client().post("/api/beatport/discovery/update_match",
                                  json={"identifier": "b2", "track_index": 0, "spotify_track": PICK})
    assert resp.status_code == 200, resp.get_json()
    assert bc.beatport_chart_states["b2"]["spotify_matches"] == 4


def test_the_app_serves_the_route():
    web_server = pytest.importorskip("web_server")
    assert "/api/commissary/discovery/unavailable" in {r.rule for r in web_server.app.url_map.iter_rules()}


def test_the_sync_page_wires_the_button_to_the_route():
    from pathlib import Path
    root = Path(__file__).resolve().parents[2] / "webui" / "src" / "routes" / "sync"
    modals = (root / "-ui" / "source-modals.tsx").read_text(encoding="utf-8")
    assert "onAvailabilityTrack={(row, unavailable) => void onAvailabilityTrack(row, unavailable)}" in modals
    assert "postAvailability(config, openId, row.index, unavailable)" in modals
