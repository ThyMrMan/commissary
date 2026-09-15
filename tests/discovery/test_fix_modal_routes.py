"""A manual Fix is found again by the discovery that would have needed it.

The Fix dialog's pick is written to the discovery cache, so the next discovery
of the same song -- in this playlist or any other -- takes it without a search.
For YouTube and mirrored playlists the route read the source track from a
``youtube_track`` field no discovery result has, so it saved the pick under the
PICKED song's title with no artist: a key no discovery looks up, so every fix
made there was forgotten.

And the Qobuz tab's Fix: the dialog sends the Qobuz playlist's id, which is
the key the server keeps that playlist's discovery state under.

Rows these tests write carry "zqfix" so they can be cleared from the shared
test database.
"""

from __future__ import annotations

from unittest.mock import patch

import pytest

from core.discovery import youtube as dy
from tests.discovery.test_discovery_youtube import _build_deps, _seed_state, _track

SOURCE_TITLE = "Kick Back zqfix"
SOURCE_ARTIST = "Kenshi Yonezu"
PICK = {"id": "itunes-zqfix-1", "name": "KICK BACK zqfix (Single Version)", "artists": ["Kenshi Yonezu"],
        "album": "KICK BACK", "duration_ms": 193000, "image_url": ""}


@pytest.fixture
def app_client():
    with patch("web_server.add_activity_item"):
        with patch("web_server.SpotifyClient"):
            with patch("core.tidal_client.TidalClient"):
                import web_server
                web_server.app.config["TESTING"] = True
                yield web_server, web_server.app.test_client()


@pytest.fixture
def ws(app_client):
    web_server, _client = app_client

    def purge():
        conn = web_server.get_database()._get_connection()
        try:
            conn.execute("DELETE FROM discovery_match_cache WHERE original_title LIKE ? OR normalized_title LIKE ?",
                         ("%zqfix%", "%zqfix%"))
            conn.commit()
        finally:
            conn.close()

    purge()
    yield app_client
    purge()


def _mirrored_playlist(web_server, url_hash, artists):
    web_server.youtube_playlist_states[url_hash] = {
        "playlist": {"name": "Fix Test", "tracks": [
            {"name": SOURCE_TITLE, "artists": artists, "duration_ms": 193000, "db_track_id": None}]},
        "phase": "discovered",
        "discovery_results": [{"index": 0, "yt_track": SOURCE_TITLE, "yt_artist": artists[0] if artists else "Unknown",
                               "status": "Wing It", "status_class": "wing-it", "wing_it_fallback": True}],
        "spotify_matches": 0,
        "spotify_total": 1,
    }


def _fix(web_server, client, url_hash, artists=(SOURCE_ARTIST,)):
    _mirrored_playlist(web_server, url_hash, list(artists))
    try:
        response = client.post("/api/youtube/discovery/update_match", json={
            "identifier": url_hash, "track_index": 0,
            "original_name": SOURCE_TITLE, "original_artist": artists[0] if artists else "Unknown",
            "spotify_track": PICK,
        })
    finally:
        web_server.youtube_playlist_states.pop(url_hash, None)
    assert response.status_code == 200, response.get_json()
    return response


def _cached(web_server, title, artist):
    key = web_server._get_discovery_cache_key(title, artist)
    return web_server.get_database().get_discovery_cache_match(
        key[0], key[1], web_server._get_active_discovery_source())


@pytest.mark.parametrize("identifier", ["mirrored_zqfix_1", "zqfix_youtube_hash"])
def test_a_fix_is_cached_under_the_playlist_track_it_fixed(ws, identifier):
    web_server, client = ws
    _fix(web_server, client, identifier)

    cached = _cached(web_server, SOURCE_TITLE, SOURCE_ARTIST)
    assert cached is not None
    assert cached["name"] == PICK["name"]
    assert _cached(web_server, PICK["name"], "") is None


def test_a_track_with_no_artist_is_keyed_as_discovery_keys_it(ws):
    """The worker searches a track with no artist as "Unknown Artist"."""
    web_server, client = ws
    _fix(web_server, client, "mirrored_zqfix_2", artists=())

    assert _cached(web_server, SOURCE_TITLE, "Unknown Artist") is not None


def test_the_next_discovery_takes_the_fix_without_searching(ws):
    web_server, client = ws
    _fix(web_server, client, "mirrored_zqfix_3")

    states = {}
    _seed_state("zqfix_rerun", states, tracks=[_track(SOURCE_TITLE, SOURCE_ARTIST, 193000)])
    deps = _build_deps(states=states, discovery_source=web_server._get_active_discovery_source())
    deps.get_discovery_cache_key = web_server._get_discovery_cache_key
    deps.validate_discovery_cache_artist = web_server._validate_discovery_cache_artist
    deps.extract_artist_name = web_server._extract_artist_name
    deps.get_database = web_server.get_database

    dy.run_youtube_discovery_worker("zqfix_rerun", deps)

    (result,) = states["zqfix_rerun"]["discovery_results"]
    assert (result["status"], result["spotify_track"]) == ("Found", PICK["name"])
    assert deps._spotify.search_calls == []
    assert deps._itunes.search_calls == []


def _qobuz_playlist(web_server, playlist_id):
    web_server.qobuz_discovery_states[playlist_id] = {
        "playlist": {"name": "Q", "tracks": []},
        "phase": "discovered",
        "discovery_results": [{"qobuz_track": {"name": SOURCE_TITLE, "artists": [SOURCE_ARTIST]},
                               "status": "Not Found", "status_class": "not-found",
                               "spotify_track": "", "spotify_artist": "", "spotify_album": ""}],
        "spotify_matches": 0,
    }


@pytest.mark.parametrize("sent, updates_the_playlist", [
    ("zqfix-qobuz-123", True),         # the Qobuz playlist id, as the dialog now sends
    ("qobuz_zqfix-qobuz-123", False),  # the modal's own key, which the server never uses
])
def test_a_qobuz_fix_reaches_the_playlist_it_names(ws, sent, updates_the_playlist):
    web_server, client = ws
    _qobuz_playlist(web_server, "zqfix-qobuz-123")
    try:
        response = client.post("/api/qobuz/discovery/update_match", json={
            "identifier": sent, "track_index": 0,
            "original_name": SOURCE_TITLE, "original_artist": SOURCE_ARTIST, "spotify_track": PICK,
        })
        result = web_server.qobuz_discovery_states["zqfix-qobuz-123"]["discovery_results"][0]
    finally:
        web_server.qobuz_discovery_states.pop("zqfix-qobuz-123", None)

    assert response.status_code == 200
    assert (result["status_class"] == "found") is updates_the_playlist
