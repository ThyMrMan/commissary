"""Discovery results written back to a mirrored playlist land on the right track,
and a Wing It guess reopens as one.

Two bugs sat in the code that discovery results -- and now their suggestions --
are saved and restored through:

- A Qobuz, Deezer or Beatport playlist discovered from its own tab wrote each
  result onto the previous mirrored track. Results count from 0 and mirrored
  positions from 1, and only Tidal's source-track ids were recognised, so the
  first result was dropped and every other one landed a row early.
- A mirrored Wing It guess (nothing matched, a stub stands in) is stored with
  provider 'wing_it_fallback'. Reopening the playlist compared that with the
  active metadata source and showed "Provider changed" with nothing in it, and a
  playlist of nothing but guesses reopened as never discovered.
"""

from __future__ import annotations

import json
from unittest.mock import patch

import pytest

from core.discovery.manual_match import is_drifted_for_redo

ARTIST = "Artist zqwb"
STUB = {"id": "wing_it_1", "name": "One", "artists": [{"name": ARTIST}],
        "album": {"name": ""}, "duration_ms": 200000, "source": "wing_it_fallback"}


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
def mirror(ws):
    """Mirror playlists into the test database, removed afterwards."""
    db = ws.get_database()
    created = []

    def make(source, source_playlist_id, names):
        tracks = [{"track_name": name, "artist_name": ARTIST, "album_name": "Album",
                   "duration_ms": 200000, "source_track_id": f"{source_playlist_id}-{i + 1}"}
                  for i, name in enumerate(names)]
        playlist_id = db.mirror_playlist(source, source_playlist_id, f"WB {source_playlist_id}",
                                         tracks, profile_id=1)
        created.append(playlist_id)
        return playlist_id, db.get_mirrored_playlist_tracks(playlist_id)

    yield db, make
    conn = db._get_connection()
    try:
        for playlist_id in created:
            conn.execute("DELETE FROM mirrored_playlist_tracks WHERE playlist_id = ?", (playlist_id,))
            conn.execute("DELETE FROM mirrored_playlists WHERE id = ?", (playlist_id,))
        conn.commit()
    finally:
        conn.close()


def _extras(db, playlist_id):
    return [json.loads(t["extra_data"]) if t.get("extra_data") else None
            for t in db.get_mirrored_playlist_tracks(playlist_id)]


def _found(index, name, **source_track):
    return dict({"index": index, "status": "Found", "confidence": 0.95,
                 "spotify_data": {"id": f"m-{name}", "name": name}}, **source_track)


def _matched_names(db, playlist_id):
    return [(extra or {}).get("matched_data", {}).get("name") for extra in _extras(db, playlist_id)]


# ── the tab write-back ──────────────────────────────────────────────────────
@pytest.mark.parametrize("source, track_key", [("qobuz", "qobuz_track"), ("deezer", "deezer_track")])
def test_results_carrying_source_track_ids_land_on_their_own_tracks(ws, mirror, source, track_key):
    db, make = mirror
    playlist_id, tracks = make(source, f"zqwb-{source}", ["One", "Two", "Three"])
    results = [_found(i, f"Match {t['track_name']}", **{track_key: {"id": t["source_track_id"]}})
               for i, t in enumerate(tracks)]

    ws._sync_discovery_results_to_mirrored(source, f"zqwb-{source}", results, "itunes", profile_id=1)

    assert _matched_names(db, playlist_id) == ["Match One", "Match Two", "Match Three"]


def test_results_without_a_source_track_id_land_by_their_index(ws, mirror):
    """Beatport chart results name their track but carry no id."""
    db, make = mirror
    playlist_id, tracks = make("beatport", "zqwb-beatport", ["One", "Two", "Three"])
    results = [_found(i, f"Match {t['track_name']}", beatport_track={"title": t["track_name"]})
               for i, t in enumerate(tracks)]

    ws._sync_discovery_results_to_mirrored("beatport", "zqwb-beatport", results, "itunes", profile_id=1)

    assert _matched_names(db, playlist_id) == ["Match One", "Match Two", "Match Three"]


def test_a_found_result_clears_the_suggestions_a_guess_had(ws, mirror):
    db, make = mirror
    playlist_id, tracks = make("qobuz", "zqwb-clear", ["One"])
    db.update_mirrored_track_extra_data(tracks[0]["id"], {
        "discovered": True, "provider": "wing_it_fallback", "wing_it_fallback": True,
        "matched_data": STUB, "suggestions": [{"id": "s-1", "name": "Maybe"}]})

    ws._sync_discovery_results_to_mirrored(
        "qobuz", "zqwb-clear", [_found(0, "Match One", qobuz_track={"id": tracks[0]["source_track_id"]})],
        "itunes", profile_id=1)

    (extra,) = _extras(db, playlist_id)
    assert extra["matched_data"]["name"] == "Match One"
    assert extra["suggestions"] == []


# ── reopening a mirrored playlist ───────────────────────────────────────────
def _guess(**more):
    return dict({"discovered": True, "provider": "wing_it_fallback", "confidence": 0,
                 "wing_it_fallback": True, "matched_data": STUB}, **more)


def _match(ws, name):
    return {"discovered": True, "provider": ws._get_active_discovery_source(), "confidence": 0.95,
            "matched_data": {"id": f"m-{name}", "name": name, "artists": [ARTIST],
                             "album": {"name": "Album", "id": "al-1"}, "track_number": 1}}


def _prepare(ws, playlist_id):
    response = ws.app.test_client().post(f"/api/mirrored-playlists/{playlist_id}/prepare-discovery")
    assert response.status_code == 200, response.get_json()
    return ws.youtube_playlist_states.pop(f"mirrored_{playlist_id}")


def test_a_wing_it_guess_reopens_as_wing_it(ws, mirror):
    db, make = mirror
    playlist_id, tracks = make("qobuz", "zqwb-reopen", ["One", "Two"])
    db.update_mirrored_track_extra_data(tracks[0]["id"], _guess())
    db.update_mirrored_track_extra_data(tracks[1]["id"], _match(ws, "Match Two"))

    state = _prepare(ws, playlist_id)

    guess, found = state["discovery_results"]
    assert (guess["status"], guess["status_class"], guess.get("wing_it_fallback")) == ("Wing It", "wing-it", True)
    assert guess["matched_data"] == STUB
    assert (found["status"], found["spotify_track"]) == ("Found", "Match Two")


def test_a_playlist_of_nothing_but_guesses_reopens_discovered(ws, mirror):
    db, make = mirror
    playlist_id, tracks = make("qobuz", "zqwb-guesses", ["One", "Two"])
    for track in tracks:
        db.update_mirrored_track_extra_data(track["id"], _guess())

    state = _prepare(ws, playlist_id)

    assert state["phase"] == "discovered"
    assert [r["status"] for r in state["discovery_results"]] == ["Wing It", "Wing It"]


def test_a_track_matched_after_being_a_guess_reopens_found(ws, mirror):
    """extra_data is merged on save, so a later match can leave the
    wing_it_fallback flag behind; the provider says what the row is now."""
    db, make = mirror
    playlist_id, tracks = make("qobuz", "zqwb-later", ["One"])
    db.update_mirrored_track_extra_data(tracks[0]["id"], _guess())
    db.update_mirrored_track_extra_data(tracks[0]["id"], _match(ws, "Match One"))

    (result,) = _prepare(ws, playlist_id)["discovery_results"]

    assert (result["status"], result["spotify_track"]) == ("Found", "Match One")


def test_a_wing_it_guess_is_never_provider_drift():
    guess = {"discovered": True, "provider": "wing_it_fallback", "wing_it_fallback": True}
    assert is_drifted_for_redo(guess, "itunes") is False


def test_a_real_match_still_drifts_when_the_source_changes():
    stale_flag = {"discovered": True, "provider": "deezer", "wing_it_fallback": True}
    assert is_drifted_for_redo(stale_flag, "itunes") is True


def test_a_manual_match_is_never_a_wing_it_guess():
    from core.discovery.manual_match import is_wing_it_guess
    fixed = {"discovered": True, "provider": "wing_it_fallback", "wing_it_fallback": True, "manual_match": True}
    assert is_wing_it_guess(fixed) is False
    assert is_wing_it_guess(dict(fixed, manual_match=False)) is True


@pytest.mark.parametrize("source, track_key", [("qobuz", "qobuz_track"), ("deezer", "deezer_track")])
def test_a_result_follows_its_track_id_when_the_playlist_order_moved(ws, mirror, source, track_key):
    """The tab discovered the playlist before a track was added at the top: the
    results' indexes no longer match mirrored positions, but their ids do."""
    db, make = mirror
    playlist_id, tracks = make(source, f"zqwb-moved-{source}", ["New", "One", "Two"])
    results = [_found(0, "Match One", **{track_key: {"id": tracks[1]["source_track_id"]}}),
               _found(1, "Match Two", **{track_key: {"id": tracks[2]["source_track_id"]}})]

    ws._sync_discovery_results_to_mirrored(source, f"zqwb-moved-{source}", results, "itunes", profile_id=1)

    assert _matched_names(db, playlist_id) == [None, "Match One", "Match Two"]
