"""Discovery fixes that were quietly lost (old fork 2.3.9, the parts upstream still had)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from flask import Flask

from core.commissary import discovery_fixes as df
from core.discovery.manual_match import is_drifted_for_redo

_ROOT = Path(__file__).resolve().parents[2]


def _engine(monkeypatch, routes):
    """The matching engine the app injects at boot (configure())."""
    from core.matching_engine import MusicMatchingEngine
    engine = MusicMatchingEngine()
    monkeypatch.setattr(routes, "_matching_engine", lambda: engine, raising=False)


# ── a YouTube / mirrored Fix is cached where discovery looks ───────────────
def test_the_fix_key_is_the_playlist_tracks_own_name_and_artist():
    state = {"playlist": {"tracks": [{"name": "skip"}, {"name": "Idol", "artists": ["YOASOBI"]}]}}
    assert df.fix_source_key(state, 0, {}, {"index": 1}, {"name": "Picked"}) == ("Idol", "YOASOBI")


def test_no_artist_is_unknown_artist_as_discovery_has_it():
    state = {"playlist": {"tracks": [{"name": "Idol", "artists": []}]}}
    assert df.fix_source_key(state, 0, {}, {"index": 0}, {"name": "Picked"}) == ("Idol", "Unknown Artist")


def test_without_the_state_track_the_page_names_win():
    assert df.fix_source_key({}, 3, {"original_name": "Idol", "original_artist": "YOASOBI"},
                             {}, {"name": "Picked"}) == ("Idol", "YOASOBI")


@pytest.fixture
def saved(monkeypatch):
    import api.source_playlists as routes
    calls = []

    class _DB:
        def save_discovery_cache_match(self, *args, **kwargs):
            calls.append(args)
            return True

        def __getattr__(self, name):
            return lambda *a, **k: None

    monkeypatch.setattr(routes, "get_database", lambda: _DB())
    monkeypatch.setattr(routes, "_get_active_discovery_source", lambda: "itunes")
    _engine(monkeypatch, routes)
    return calls


@pytest.mark.parametrize("route, states", [("/api/youtube/discovery/update_match", "youtube_playlist_states"),
                                           ("/api/ytmusic/discovery/update_match", "ytmusic_discovery_states")])
def test_the_routes_save_the_fix_under_the_discovery_key(saved, monkeypatch, route, states):
    import api.source_playlists as routes
    monkeypatch.setitem(getattr(routes, states), "h1", {
        "playlist": {"tracks": [{"name": "Idol", "artists": ["YOASOBI"]}]},
        "discovery_results": [{"index": 0, "status": "Not Found", "yt_track": "Idol"}],
        "spotify_matches": 0})
    app = Flask(__name__)
    app.register_blueprint(routes.bp)
    picked = {"id": "1", "name": "アイドル", "artists": ["YOASOBI"], "album": "Idol", "duration_ms": 1}
    resp = app.test_client().post(route, json={"identifier": "h1", "track_index": 0, "spotify_track": picked})
    assert resp.status_code == 200, resp.get_json()
    engine = routes._matching_engine()
    assert saved and saved[0][:2] == (engine.clean_title("Idol"), engine.clean_artist("YOASOBI"))


# ── the mirrored write-back ─────────────────────────────────────────────────
@pytest.fixture
def mirror(tmp_path, monkeypatch):
    from database.music_database import MusicDatabase
    db = MusicDatabase(str(tmp_path / "music.db"))
    monkeypatch.setattr("api.source_playlists.get_database", lambda: db)
    with db._get_connection() as conn:
        cur = conn.cursor()
        cur.execute("INSERT INTO mirrored_playlists (source, source_playlist_id, name, profile_id) "
                    "VALUES ('qobuz', 'q1', 'Q', 1)")
        pl = cur.lastrowid
        for pos, (name, sid) in enumerate([("One", "q-101"), ("Two", "q-102"), ("Three", "q-103")], 1):
            cur.execute("INSERT INTO mirrored_playlist_tracks (playlist_id, position, track_name, artist_name, "
                        "source_track_id) VALUES (?, ?, ?, 'A', ?)", (pl, pos, name, sid))
        conn.commit()
    return db, pl


def _extras(db, pl):
    out = {}
    for t in db.get_mirrored_playlist_tracks(pl):
        extra = t.get("extra_data") or {}
        out[t["track_name"]] = json.loads(extra) if isinstance(extra, str) else extra
    return out


def _result(index, name, **source):
    return {"index": index, "status": "Found", "confidence": 0.9, **source,
            "match_data": {"id": f"m-{name}", "name": name, "artists": ["A"]}}


def test_a_qobuz_result_lands_on_its_own_track_by_id(mirror):
    from api.source_playlists import _sync_discovery_results_to_mirrored
    db, pl = mirror
    _sync_discovery_results_to_mirrored("qobuz", "q1", [
        _result(2, "Three", qobuz_track={"id": "q-103"}), _result(0, "One", qobuz_track={"id": "q-101"})],
        "itunes", profile_id=1)
    extras = _extras(db, pl)
    assert extras["Three"]["matched_data"]["id"] == "m-Three"
    assert extras["One"]["matched_data"]["id"] == "m-One"
    assert not extras["Two"].get("matched_data")


def test_without_an_id_a_result_lands_on_its_own_position_not_the_one_before(mirror):
    from api.source_playlists import _sync_discovery_results_to_mirrored
    db, pl = mirror
    _sync_discovery_results_to_mirrored("qobuz", "q1", [_result(1, "Two")], "itunes", profile_id=1)
    extras = _extras(db, pl)
    assert extras["Two"]["matched_data"]["id"] == "m-Two"
    assert not extras["One"].get("matched_data")


# ── a Wing It guess has no provider to drift from ──────────────────────────
GUESS = {"discovered": True, "wing_it_fallback": True, "provider": "wing_it_fallback",
         "matched_data": {"id": "wing_it_1", "name": "Idol"}}


@pytest.mark.parametrize("extra", [GUESS, {"unmatched_by_user": True, "provider": "spotify"},
                                   {"unavailable": True, "provider": "spotify"}])
def test_nothing_without_a_source_reopens_as_provider_changed(extra):
    assert is_drifted_for_redo(extra, "itunes") is False


def test_a_real_match_still_drifts_and_a_fixed_guess_is_no_guess():
    assert is_drifted_for_redo({"discovered": True, "provider": "spotify"}, "itunes") is True
    assert df.is_wing_it_guess(dict(GUESS, provider="itunes")) is False
    assert df.is_wing_it_guess(dict(GUESS, manual_match=True)) is False


def test_a_reopened_guess_is_a_wing_it_row():
    row = df.as_wing_it_row({"status": "Found", "matched_data": GUESS["matched_data"]},
                            {"name": "Idol", "artists": ["YOASOBI"]}, "itunes")
    assert (row["status"], row["status_class"], row["wing_it_fallback"]) == ("Wing It", "wing-it", True)
    assert row["matched_data"] == GUESS["matched_data"]
    src = (_ROOT / "api" / "mirrored_playlists.py").read_text(encoding="utf-8")
    assert "pre_discovered_results.append(as_wing_it_row(result, track, _current_provider))" in src


# ── ListenBrainz unmatch ────────────────────────────────────────────────────
def test_listenbrainz_unmatch_finds_its_profile_scoped_state(monkeypatch):
    import api.source_playlists as routes
    row = {"status": "Found", "status_class": "found", "spotify_data": {"id": "x"}, "matched_data": {"id": "x"}}
    monkeypatch.setitem(routes.listenbrainz_playlist_states, "1:mbid-1",
                        {"discovery_results": [row], "spotify_matches": 1})
    monkeypatch.setattr(routes, "get_current_profile_id", lambda: 1)
    app = Flask(__name__)
    app.register_blueprint(routes.bp)
    resp = app.test_client().post("/api/listenbrainz/discovery/unmatch",
                                  json={"identifier": "mbid-1", "track_index": 0})
    assert resp.status_code == 200
    assert row["status"] == "Not Found"


# ── Discovery Pool rematch ──────────────────────────────────────────────────
def test_a_rematch_replaces_the_entry_under_its_own_key(tmp_path, monkeypatch):
    from database.music_database import MusicDatabase
    import api.mirrored_playlists as routes
    db = MusicDatabase(str(tmp_path / "music.db"))
    monkeypatch.setattr(routes, "get_database", lambda: db)
    monkeypatch.setattr(routes, "_get_active_discovery_source", lambda: "itunes")
    _engine(monkeypatch, routes)
    db.save_discovery_cache_match("idol", "yoasobi", "deezer", 0.8, {"id": "old"}, "Idol", "YOASOBI")
    with db._get_connection() as conn:
        entry_id = conn.execute("SELECT id FROM discovery_match_cache").fetchone()[0]
    app = Flask(__name__)
    app.register_blueprint(routes.bp)
    resp = app.test_client().post("/api/discovery-pool/rematch", json={
        "cache_id": entry_id, "original_title": "Idol (Live)", "original_artist": "Yoasobi",
        "spotify_track": {"id": "new", "name": "アイドル", "artists": ["YOASOBI"], "album": "Idol"}})
    assert resp.status_code == 200, resp.get_json()
    match = db.get_discovery_cache_match("idol", "yoasobi", "deezer")
    assert match and match["id"] == "new", "read back exactly where the old match was"


# ── the pools' Fix Match ────────────────────────────────────────────────────
def test_the_pool_fix_searches_every_source():
    pool = (_ROOT / "webui" / "static" / "stats-automations.js").read_text(encoding="utf-8")
    assert "await window.commissaryPoolFixSearch(params)" in pool
    assert "/api/spotify/search_tracks?${params" not in pool
    index = (_ROOT / "webui" / "index.html").read_text(encoding="utf-8")
    assert "filename='commissary-pool-fix.js'" in index
