"""Marking a discovery track "Not available".

A track no metadata source has keeps coming back: Review offers it again, Retry
Failed searches it again, the Playlist Pipeline re-discovers it, and a mirrored
playlist's next discovery searches it once more. Marking it "Not available" (the
review dialog's button, POST /api/discovery/unavailable) clears its match and any
Wing It guess, so nothing syncs or downloads it, and every discovery pass leaves
it alone until someone fixes it by hand or marks it available again.
"""

from __future__ import annotations

import json
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from core.discovery import manual_match
from core.discovery import playlist as dp
from core.discovery import youtube as dy
from core.discovery.endpoints import convert_results_to_spotify_tracks
from tests.discovery import test_discovery_playlist as playlist_tests
from tests.discovery import test_discovery_youtube as youtube_tests

MARK = {"discovered": False, "discovery_attempted": True, "unavailable": True, "provider": "deezer"}


# ── the gates ───────────────────────────────────────────────────────────────
def test_a_marked_track_is_never_rediscovered_or_drifted():
    assert manual_match.is_marked_unavailable(MARK) is True
    assert manual_match.should_rediscover(MARK) is False
    assert manual_match.is_drifted_for_redo(MARK, "itunes") is False


def test_without_the_mark_the_same_track_is_retried_and_drifts():
    attempted = dict(MARK, unavailable=False)
    assert manual_match.is_marked_unavailable(attempted) is False
    assert manual_match.should_rediscover(attempted) is True
    assert manual_match.is_drifted_for_redo(attempted, "itunes") is True


def test_a_match_saved_later_outranks_a_mark_left_behind():
    """extra_data merges on save, so a fix can leave the old flag in place."""
    fixed = {"discovered": True, "unavailable": True, "manual_match": True, "provider": "itunes"}
    assert manual_match.is_marked_unavailable(fixed) is False
    assert manual_match.is_marked_unavailable(None) is False


def test_the_playlist_pipeline_leaves_a_marked_track_alone():
    tracks = [playlist_tests._track(track_id=1, name="Gone", artist="Nobody", extra_data=MARK),
              playlist_tests._track(track_id=2, name="Here", artist="Somebody")]
    deps = playlist_tests._build_deps(discovery_source="itunes", tracks_by_playlist={"p1": tracks})

    dp.run_playlist_discovery_worker([playlist_tests._playlist("p1")], deps=deps)

    assert [track_id for track_id, _extra in deps._db.extra_data_writes] == [2]
    assert not any("Gone" in query for query, _limit in deps._itunes.search_calls)


def test_discovery_keeps_a_marked_track_out_of_the_search_and_the_database():
    states = {}
    marked = youtube_tests._track(name="Gone", artist="Nobody")
    marked.update(db_track_id="db-gone", extra_data=dict(MARK))
    plain = youtube_tests._track(name="Here", artist="Somebody")
    plain["db_track_id"] = "db-here"
    youtube_tests._seed_state("mirrored_zqna", states, tracks=[marked, plain])
    deps = youtube_tests._build_deps(states=states, discovery_source="itunes")

    dy.run_youtube_discovery_worker("mirrored_zqna", deps)

    results = states["mirrored_zqna"]["discovery_results"]
    assert [(r["index"], r["status_class"]) for r in results] == [(0, "unavailable"), (1, "wing-it")]
    assert (results[0]["status"], results[0]["unavailable"]) == ("Not available", True)
    assert not any("Gone" in query for query, _limit in deps._itunes.search_calls)
    assert any("Here" in query for query, _limit in deps._itunes.search_calls)
    assert [db_id for db_id, _extra in deps._db.mirrored_updates] == ["db-here"]


def test_a_track_marked_on_an_open_youtube_playlist_is_skipped_too():
    states = {}
    marked = youtube_tests._track(name="Gone", artist="Nobody")
    marked["unavailable"] = True
    youtube_tests._seed_state("zqna_youtube", states, tracks=[marked])
    deps = youtube_tests._build_deps(states=states, discovery_source="itunes")

    dy.run_youtube_discovery_worker("zqna_youtube", deps)

    assert [r["status_class"] for r in states["zqna_youtube"]["discovery_results"]] == ["unavailable"]
    assert deps._itunes.search_calls == []


# ── the real app ────────────────────────────────────────────────────────────
ARTIST = "Artist zqna"
STUB = {"id": "wing_it_zqna", "name": "Guess zqna", "artists": [{"name": ARTIST}],
        "album": {"name": ""}, "duration_ms": 200000, "source": "wing_it_fallback"}
SUGGESTION = {"id": "itunes-zqna", "name": "Guess", "artists": ["Somebody"], "album": "Album",
              "duration_ms": 200000, "source": "itunes", "confidence": 0.8}
MATCHED = {"id": "m-found", "name": "Found match zqna", "artists": [ARTIST], "track_number": 1,
           "album": {"name": "Album", "id": "al-1", "release_date": "2020-01-01"}, "duration_ms": 200000}
PICK = {"id": "itunes-gone", "name": "Gone", "artists": ["Nobody"], "album": "Album",
        "duration_ms": 200000, "source": "itunes"}


@pytest.fixture
def ws(monkeypatch):
    with patch("web_server.add_activity_item"):
        with patch("web_server.SpotifyClient"):
            with patch("core.tidal_client.TidalClient"):
                import web_server
                web_server.app.config["TESTING"] = True
                monkeypatch.setattr(web_server, "mirrored_playlist_visible", lambda playlist: bool(playlist))
                yield web_server


@pytest.fixture
def mirrored(ws):
    """A mirrored playlist of a track nothing found, a Wing It guess and a match."""
    db = ws.get_database()
    playlist_id = db.mirror_playlist("qobuz", "zqna-1", "Not available", [
        {"track_name": name, "artist_name": ARTIST, "album_name": "Album", "duration_ms": 200000,
         "source_track_id": f"zqna-{i}"}
        for i, name in enumerate(["Gone zqna", "Guess zqna", "Found zqna"])
    ], profile_id=1)
    tracks = db.get_mirrored_playlist_tracks(playlist_id)
    active = ws._get_active_discovery_source()
    db.update_mirrored_track_extra_data(tracks[0]["id"], {"discovered": False, "discovery_attempted": True,
                                                          "provider": active})
    db.update_mirrored_track_extra_data(tracks[1]["id"], {"discovered": True, "provider": "wing_it_fallback",
                                                          "wing_it_fallback": True, "confidence": 0,
                                                          "matched_data": STUB, "suggestions": [SUGGESTION]})
    db.update_mirrored_track_extra_data(tracks[2]["id"], {"discovered": True, "provider": active,
                                                          "confidence": 0.95, "matched_data": MATCHED})
    yield db, playlist_id, tracks
    ws.youtube_playlist_states.pop(f"mirrored_{playlist_id}", None)
    conn = db._get_connection()
    try:
        conn.execute("DELETE FROM mirrored_playlist_tracks WHERE playlist_id = ?", (playlist_id,))
        conn.execute("DELETE FROM mirrored_playlists WHERE id = ?", (playlist_id,))
        conn.execute("DELETE FROM discovery_match_cache WHERE original_title LIKE ? OR normalized_title LIKE ?",
                     ("%zqna%", "%zqna%"))
        conn.commit()
    finally:
        conn.close()


def _prepare(ws, playlist_id):
    response = ws.app.test_client().post(f"/api/mirrored-playlists/{playlist_id}/prepare-discovery")
    assert response.status_code == 200, response.get_json()
    return ws.youtube_playlist_states[f"mirrored_{playlist_id}"]


def _mark(ws, identifier, index, unavailable=True):
    return ws.app.test_client().post("/api/discovery/unavailable", json={
        "identifier": identifier, "track_index": index, "unavailable": unavailable})


def _extras(db, playlist_id):
    return [json.loads(t["extra_data"]) if t.get("extra_data") else {}
            for t in db.get_mirrored_playlist_tracks(playlist_id)]


def test_a_marked_guess_loses_its_match_and_reopens_not_available(ws, mirrored):
    db, playlist_id, tracks = mirrored
    state = _prepare(ws, playlist_id)
    assert state["discovery_results"][1]["status"] == "Wing It"

    response = _mark(ws, f"mirrored_{playlist_id}", 1)

    assert response.status_code == 200, response.get_json()
    row = state["discovery_results"][1]
    assert (row["status"], row["status_class"], row["unavailable"]) == ("Not available", "unavailable", True)
    assert (row["spotify_data"], row["matched_data"], row["wing_it_fallback"], row["suggestions"]) == (
        None, None, False, [])
    extra = _extras(db, playlist_id)[1]
    assert (extra["unavailable"], extra["discovered"], extra["wing_it_fallback"]) == (True, False, False)
    assert (extra["matched_data"], extra["suggestions"]) == (None, [])

    ws.youtube_playlist_states.pop(f"mirrored_{playlist_id}")
    reopened = _prepare(ws, playlist_id)

    assert reopened["phase"] == "discovered"
    assert [r["status_class"] for r in reopened["discovery_results"]] == ["not-found", "unavailable", "found"]


def test_nothing_syncs_a_track_marked_not_available(ws, mirrored):
    db, playlist_id, tracks = mirrored
    state = _prepare(ws, playlist_id)
    synced = [t["name"] for t in convert_results_to_spotify_tracks(state["discovery_results"], "test")]
    assert synced == ["Guess zqna", "Found match zqna"]
    matches = state["spotify_matches"]

    for index in (1, 2):
        assert _mark(ws, f"mirrored_{playlist_id}", index).status_code == 200

    assert convert_results_to_spotify_tracks(state["discovery_results"], "test") == []
    # The guess never counted as a match; the real match did.
    assert state["spotify_matches"] == matches - 1


def test_marking_it_available_again_puts_it_back_in_line(ws, mirrored):
    db, playlist_id, tracks = mirrored
    state = _prepare(ws, playlist_id)
    assert _mark(ws, f"mirrored_{playlist_id}", 0).status_code == 200

    response = _mark(ws, f"mirrored_{playlist_id}", 0, unavailable=False)

    assert response.status_code == 200, response.get_json()
    row = state["discovery_results"][0]
    assert (row["status_class"], row["unavailable"]) == ("not-found", False)
    assert state["playlist"]["tracks"][0]["unavailable"] is False
    extra = _extras(db, playlist_id)[0]
    assert (extra["unavailable"], extra["discovered"]) == (False, False)
    assert manual_match.should_rediscover(extra) is True


def test_marking_a_matched_track_available_changes_nothing(ws, mirrored):
    db, playlist_id, tracks = mirrored
    state = _prepare(ws, playlist_id)

    response = _mark(ws, f"mirrored_{playlist_id}", 2, unavailable=False)

    assert response.status_code == 200, response.get_json()
    assert state["discovery_results"][2]["status_class"] == "found"
    assert _extras(db, playlist_id)[2]["discovered"] is True


def test_retry_failed_leaves_a_marked_track_alone(ws, mirrored, monkeypatch):
    db, playlist_id, tracks = mirrored
    state = _prepare(ws, playlist_id)
    assert _mark(ws, f"mirrored_{playlist_id}", 0).status_code == 200
    submitted = []
    monkeypatch.setattr(ws, "youtube_discovery_executor",
                        SimpleNamespace(submit=lambda fn, *args: submitted.append(args)))

    response = ws.app.test_client().post(f"/api/mirrored-playlists/{playlist_id}/retry-failed-discovery")

    assert response.status_code == 200, response.get_json()
    assert (response.get_json()["retry_count"], response.get_json()["already_found"]) == (1, 1)
    assert submitted == [(f"mirrored_{playlist_id}",)]
    assert [r["status_class"] for r in state["discovery_results"]] == ["unavailable", "found"]
    assert [t.get("skip_discovery") for t in state["playlist"]["tracks"]] == [True, False, True]
    extra = _extras(db, playlist_id)[0]
    assert (extra["unavailable"], extra["discovery_attempted"]) == (True, True)


def test_a_tab_discovery_writes_nothing_over_a_marked_track(ws, mirrored):
    db, playlist_id, tracks = mirrored
    db.update_mirrored_track_extra_data(tracks[0]["id"], dict(MARK))
    results = [
        {"index": 0, "status": "Found", "confidence": 0.95, "qobuz_track": {"id": tracks[0]["source_track_id"]},
         "spotify_data": {"id": "m-0", "name": "Match Gone"}},
        {"index": 1, "status": "Found", "confidence": 0.95, "qobuz_track": {"id": tracks[1]["source_track_id"]},
         "spotify_data": {"id": "m-1", "name": "Match Guess"}},
    ]

    ws._sync_discovery_results_to_mirrored("qobuz", "zqna-1", results, "itunes", profile_id=1)

    gone, guess = _extras(db, playlist_id)[:2]
    assert (gone["unavailable"], gone["discovered"], gone.get("matched_data")) == (True, False, None)
    assert guess["matched_data"]["name"] == "Match Guess"


def test_a_fix_clears_the_mark(ws, mirrored):
    db, playlist_id, tracks = mirrored
    state = _prepare(ws, playlist_id)
    assert _mark(ws, f"mirrored_{playlist_id}", 0).status_code == 200

    response = ws.app.test_client().post("/api/youtube/discovery/update_match", json={
        "identifier": f"mirrored_{playlist_id}", "track_index": 0,
        "original_name": "Gone zqna", "original_artist": ARTIST, "spotify_track": PICK})

    assert response.status_code == 200, response.get_json()
    extra = _extras(db, playlist_id)[0]
    assert (extra["unavailable"], extra["discovered"]) == (False, True)
    track = state["playlist"]["tracks"][0]
    assert track["unavailable"] is False
    assert manual_match.is_marked_unavailable(track["extra_data"]) is False


def test_a_wing_it_pool_fix_clears_the_mark(ws, mirrored):
    db, playlist_id, tracks = mirrored
    db.update_mirrored_track_extra_data(tracks[0]["id"], dict(MARK))

    response = ws.app.test_client().post("/api/discovery-pool/fix",
                                         json={"track_id": tracks[0]["id"], "spotify_track": PICK})

    assert response.status_code == 200, response.get_json()
    assert _extras(db, playlist_id)[0]["unavailable"] is False


def test_a_marked_track_leaves_the_discovery_pool_and_the_wing_it_pool(ws, mirrored):
    db, playlist_id, tracks = mirrored
    _prepare(ws, playlist_id)
    assert [t["id"] for t in db.get_discovery_pool_failed(playlist_id=playlist_id)] == [tracks[0]["id"]]
    assert [t["id"] for t in db.get_wing_it_pool(playlist_id=playlist_id)] == [tracks[1]["id"]]
    failed = db.get_discovery_pool_stats(profile_id=1)["failed"]

    for index in (0, 1):
        assert _mark(ws, f"mirrored_{playlist_id}", index).status_code == 200

    assert db.get_discovery_pool_failed(playlist_id=playlist_id) == []
    assert db.get_wing_it_pool(playlist_id=playlist_id) == []
    assert db.get_discovery_pool_stats(profile_id=1)["failed"] == failed - 1


def test_marking_needs_a_known_playlist_and_track(ws, mirrored):
    db, playlist_id, tracks = mirrored
    _prepare(ws, playlist_id)
    client = ws.app.test_client()

    assert _mark(ws, "mirrored_zqna_nonesuch", 0).status_code == 404
    assert _mark(ws, f"mirrored_{playlist_id}", 9).status_code == 400
    assert client.post("/api/discovery/unavailable", json={"identifier": f"mirrored_{playlist_id}"}).status_code == 400
    assert _extras(db, playlist_id)[0].get("unavailable") is None


def test_marking_waits_for_discovery_to_finish(ws, mirrored):
    db, playlist_id, tracks = mirrored
    state = _prepare(ws, playlist_id)
    state["phase"] = "discovering"

    response = _mark(ws, f"mirrored_{playlist_id}", 0)

    assert response.status_code == 409, response.get_json()
    assert _extras(db, playlist_id)[0].get("unavailable") is None
    assert state["discovery_results"][0]["status_class"] == "not-found"


def test_an_open_youtube_playlist_keeps_the_mark_on_its_track(ws):
    url_hash = "zqna_youtube_state"
    ws.youtube_playlist_states[url_hash] = {
        "phase": "discovered", "spotify_matches": 2, "wing_it_count": 1,
        "playlist": {"name": "YT", "tracks": [{"name": "Wrong", "artists": ["Nobody"]},
                                              {"name": "Guess", "artists": ["Nobody"]},
                                              {"name": "Right", "artists": ["Nobody"]}]},
        "discovery_results": [
            {"index": 0, "status": "Found", "status_class": "found", "spotify_track": "Wrong match",
             "spotify_data": {"id": "x", "name": "Wrong match"}, "confidence": 0.8},
            {"index": 1, "status": "Wing It", "status_class": "wing-it", "wing_it_fallback": True,
             "spotify_data": {"id": "wing_it_1", "name": "Guess"}},
            {"index": 2, "status": "Found", "status_class": "found", "spotify_track": "Right",
             "spotify_data": {"id": "y", "name": "Right"}, "confidence": 0.95},
        ],
    }
    try:
        for index in (0, 1):
            response = _mark(ws, url_hash, index)
            assert response.status_code == 200, response.get_json()

        state = ws.youtube_playlist_states[url_hash]
        assert [t.get("unavailable") for t in state["playlist"]["tracks"]] == [True, True, None]
        # The wrong match leaves the count and the guess never counted on YouTube; the
        # right match still counts, so an extra drop can't hide at zero.
        assert (state["spotify_matches"], state["wing_it_count"]) == (1, 0)
    finally:
        ws.youtube_playlist_states.pop(url_hash, None)


def test_a_playlist_discovered_from_the_qobuz_tab_can_be_marked(ws):
    ws.qobuz_discovery_states["zqna-qobuz"] = {
        "phase": "discovered", "spotify_matches": 0,
        "discovery_results": [{"index": 0, "status": "Not Found", "status_class": "not-found"}],
    }
    try:
        response = _mark(ws, "zqna-qobuz", 0)

        assert response.status_code == 200, response.get_json()
        assert ws.qobuz_discovery_states["zqna-qobuz"]["discovery_results"][0]["status_class"] == "unavailable"
    finally:
        ws.qobuz_discovery_states.pop("zqna-qobuz", None)
