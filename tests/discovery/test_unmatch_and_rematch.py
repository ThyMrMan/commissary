"""Removing a discovery match, re-matching a cached one, and finding the playlist either names.

- Unmatching a mirrored playlist's track never reached its database: the route read
  the playlist's tracks from ``state['tracks']``, which no discovery state has, so the
  track came back matched the next time the playlist opened. Saved as it was written,
  it would have reopened as "Provider changed" with its old match and Wing It flag
  still stored.
- Unmatch and "Not available" looked a ListenBrainz playlist up by its bare id, though
  its state is kept per profile, and unmatch knew no Qobuz playlists at all.
- Re-matching a cached match in the Discovery Pool saved the new match under a freshly
  normalized title and 'spotify', where discovery -- which looks a cached match up by
  its own key and the active source -- never found it.
"""

from __future__ import annotations

import json
from unittest.mock import patch

import pytest

from core.discovery import manual_match

ARTIST = "Artist zqum"
STUB = {"id": "wing_it_zqum", "name": "Guess zqum", "artists": [{"name": ARTIST}],
        "album": {"name": ""}, "duration_ms": 200000, "source": "wing_it_fallback"}
MATCHED = {"id": "m-zqum", "name": "Found match zqum", "artists": [ARTIST], "track_number": 1,
           "album": {"name": "Album", "id": "al-1", "release_date": "2020-01-01", "total_tracks": 9},
           "duration_ms": 200000}


def test_a_track_the_user_unmatched_is_never_provider_drift():
    unmatched = {"discovered": False, "discovery_attempted": True, "unmatched_by_user": True, "provider": "deezer"}
    assert manual_match.is_drifted_for_redo(unmatched, "itunes") is False
    assert manual_match.is_drifted_for_redo(dict(unmatched, unmatched_by_user=False), "itunes") is True


@pytest.fixture
def ws(monkeypatch):
    with patch("web_server.add_activity_item"):
        with patch("web_server.SpotifyClient"):
            with patch("core.tidal_client.TidalClient"):
                import web_server
                web_server.app.config["TESTING"] = True
                monkeypatch.setattr(web_server, "mirrored_playlist_visible", lambda playlist: bool(playlist))
                yield web_server
                conn = web_server.get_database()._get_connection()
                try:
                    for marker in ("%zqrm%", "%zqum%"):
                        conn.execute("DELETE FROM discovery_match_cache WHERE original_title LIKE ? "
                                     "OR normalized_title LIKE ?", (marker, marker))
                    conn.commit()
                finally:
                    conn.close()


@pytest.fixture
def mirrored(ws):
    """A mirrored playlist of a real match and a Wing It guess."""
    db = ws.get_database()
    playlist_id = db.mirror_playlist("qobuz", "zqum-1", "Unmatch", [
        {"track_name": name, "artist_name": ARTIST, "album_name": "Album", "duration_ms": 200000,
         "source_track_id": f"zqum-{i}"}
        for i, name in enumerate(["Found zqum", "Guess zqum"])
    ], profile_id=1)
    tracks = db.get_mirrored_playlist_tracks(playlist_id)
    db.update_mirrored_track_extra_data(tracks[0]["id"], {
        "discovered": True, "provider": ws._get_active_discovery_source(), "confidence": 1.0,
        "matched_data": MATCHED, "manual_match": True})
    db.update_mirrored_track_extra_data(tracks[1]["id"], {
        "discovered": True, "provider": "wing_it_fallback", "wing_it_fallback": True, "confidence": 0,
        "matched_data": STUB})
    yield db, playlist_id, tracks
    ws.youtube_playlist_states.pop(f"mirrored_{playlist_id}", None)
    conn = db._get_connection()
    try:
        conn.execute("DELETE FROM mirrored_playlist_tracks WHERE playlist_id = ?", (playlist_id,))
        conn.execute("DELETE FROM mirrored_playlists WHERE id = ?", (playlist_id,))
        conn.commit()
    finally:
        conn.close()


def _prepare(ws, playlist_id):
    response = ws.app.test_client().post(f"/api/mirrored-playlists/{playlist_id}/prepare-discovery")
    assert response.status_code == 200, response.get_json()
    return ws.youtube_playlist_states[f"mirrored_{playlist_id}"]


def _unmatch(ws, identifier, index, route="youtube"):
    return ws.app.test_client().post(f"/api/{route}/discovery/unmatch",
                                     json={"identifier": identifier, "track_index": index})


def _extras(db, playlist_id):
    return [json.loads(t["extra_data"]) if t.get("extra_data") else {}
            for t in db.get_mirrored_playlist_tracks(playlist_id)]


# ── unmatching a mirrored playlist's track ──────────────────────────────────
def test_unmatching_a_mirrored_track_is_saved_with_its_match_cleared(ws, mirrored):
    db, playlist_id, tracks = mirrored
    state = _prepare(ws, playlist_id)
    matches = state["spotify_matches"]

    response = _unmatch(ws, f"mirrored_{playlist_id}", 0)

    assert response.status_code == 200, response.get_json()
    extra = _extras(db, playlist_id)[0]
    assert (extra["discovered"], extra["discovery_attempted"], extra["unmatched_by_user"]) == (False, True, True)
    assert (extra["matched_data"], extra["manual_match"], extra["wing_it_fallback"]) == (None, False, False)
    assert extra["provider"] == ws._get_active_discovery_source()
    assert state["playlist"]["tracks"][0]["extra_data"]["unmatched_by_user"] is True
    assert state["discovery_results"][0]["status_class"] == "not-found"
    assert state["spotify_matches"] == matches - 1


def test_an_unmatched_track_reopens_not_found_even_after_a_source_change(ws, mirrored, monkeypatch):
    db, playlist_id, tracks = mirrored
    _prepare(ws, playlist_id)
    for index in (0, 1):
        assert _unmatch(ws, f"mirrored_{playlist_id}", index).status_code == 200
    ws.youtube_playlist_states.pop(f"mirrored_{playlist_id}")
    monkeypatch.setattr(ws, "_get_active_discovery_source", lambda: "zqum-another-source")

    reopened = _prepare(ws, playlist_id)

    assert reopened["phase"] == "discovered"
    assert [r["status"] for r in reopened["discovery_results"]] == ["Not Found", "Not Found"]
    assert manual_match.should_rediscover(_extras(db, playlist_id)[0]) is False


def test_an_unmatched_guess_leaves_the_wing_it_pool_for_the_discovery_pool(ws, mirrored):
    db, playlist_id, tracks = mirrored
    state = _prepare(ws, playlist_id)
    assert [t["id"] for t in db.get_wing_it_pool(playlist_id=playlist_id)] == [tracks[1]["id"]]
    matches = state["spotify_matches"]

    assert _unmatch(ws, f"mirrored_{playlist_id}", 1).status_code == 200

    assert db.get_wing_it_pool(playlist_id=playlist_id) == []
    assert [t["id"] for t in db.get_discovery_pool_failed(playlist_id=playlist_id)] == [tracks[1]["id"]]
    # A Wing It guess never counted as a match.
    assert state["spotify_matches"] == matches


def test_a_removal_that_did_not_save_changes_nothing(ws, mirrored, monkeypatch):
    from database.music_database import MusicDatabase
    db, playlist_id, tracks = mirrored
    state = _prepare(ws, playlist_id)
    monkeypatch.setattr(MusicDatabase, "update_mirrored_track_extra_data", lambda self, *args, **kwargs: False)

    response = _unmatch(ws, f"mirrored_{playlist_id}", 0)

    assert response.status_code == 500, response.get_json()
    assert state["discovery_results"][0]["status_class"] == "found"


# ── the playlists unmatch and "Not available" can find ──────────────────────
def test_a_qobuz_tab_playlist_track_is_unmatched_through_its_own_route(ws):
    ws.qobuz_discovery_states["zqum-qobuz"] = {
        "phase": "discovered", "spotify_matches": 1,
        "discovery_results": [{"index": 0, "status": "Found", "status_class": "found", "spotify_track": "X",
                               "spotify_data": {"id": "x", "name": "X"}}],
    }
    try:
        response = _unmatch(ws, "zqum-qobuz", 0, route="qobuz")

        assert response.status_code == 200, response.get_json()
        state = ws.qobuz_discovery_states["zqum-qobuz"]
        assert (state["discovery_results"][0]["status_class"], state["spotify_matches"]) == ("not-found", 0)
    finally:
        ws.qobuz_discovery_states.pop("zqum-qobuz", None)


def test_a_listenbrainz_playlist_track_can_be_unmatched_and_marked_not_available(ws):
    with ws.app.test_request_context():
        key = ws._lb_state_key("zqum-mbid")
    ws.listenbrainz_playlist_states[key] = {
        "phase": "discovered", "spotify_matches": 1,
        "discovery_results": [
            {"index": 0, "status": "Found", "status_class": "found", "lb_track": "A", "spotify_track": "A",
             "spotify_data": {"id": "a", "name": "A"}},
            {"index": 1, "status": "Not Found", "status_class": "not-found", "lb_track": "B"},
        ],
    }
    try:
        client = ws.app.test_client()
        unmatched = _unmatch(ws, "zqum-mbid", 0, route="listenbrainz")
        marked = client.post("/api/discovery/unavailable", json={"identifier": "zqum-mbid", "track_index": 1})

        assert (unmatched.status_code, marked.status_code) == (200, 200), (unmatched.get_json(), marked.get_json())
        rows = ws.listenbrainz_playlist_states[key]["discovery_results"]
        assert [r["status_class"] for r in rows] == ["not-found", "unavailable"]
    finally:
        ws.listenbrainz_playlist_states.pop(key, None)


# ── re-matching a cached match ──────────────────────────────────────────────
ITUNES_PICK = {"id": "1440857786", "name": "HBFS", "artists": ["Daft Punk"], "album": "Discovery",
               "duration_ms": 224000, "image_url": "https://img/hbfs", "source": "itunes"}
ITUNES_DETAILS = {"id": "1440857786", "name": "HBFS", "track_number": 4, "disc_number": 1,
                  "album": {"id": "1440857781", "name": "Discovery", "total_tracks": 14,
                            "release_date": "2001-03-07", "album_type": "album"}}


class _ITunes:
    def get_track_details(self, track_id):
        return ITUNES_DETAILS


# Discovery keys these as "hbfs zqrm" by "daft punk"; normalized afresh they are
# "hbfs zqrm featured guest" by "daft punk featured guest", which no lookup asks for.
TITLE, ARTIST_FEAT = "HBFS zqrm (feat. Guest)", "Daft Punk feat. Guest"


def _cached_entry(ws, provider="deezer"):
    """A cached match for TITLE as discovery would save it; returns its id and key."""
    key = ws._get_discovery_cache_key(TITLE, ARTIST_FEAT)
    db = ws.get_database()
    assert db.save_discovery_cache_match(key[0], key[1], provider, 0.9, {"id": "old", "name": "Wrong cut"},
                                         TITLE, ARTIST_FEAT)
    conn = db._get_connection()
    try:
        row = conn.execute("SELECT id FROM discovery_match_cache WHERE normalized_title = ? AND "
                           "normalized_artist = ? AND provider = ?", (key[0], key[1], provider)).fetchone()
    finally:
        conn.close()
    return row["id"], key


def _rows_for(ws, title):
    conn = ws.get_database()._get_connection()
    try:
        return conn.execute("SELECT provider FROM discovery_match_cache WHERE original_title = ?",
                            (title,)).fetchall()
    finally:
        conn.close()


def test_a_rematch_replaces_the_cached_match_where_discovery_looks(ws, monkeypatch):
    import core.metadata.registry as registry
    monkeypatch.setattr(registry, "get_client_for_source",
                        lambda source, **kwargs: _ITunes() if source == "itunes" else None)
    entry_id, key = _cached_entry(ws)

    response = ws.app.test_client().post("/api/discovery-pool/rematch", json={
        "cache_id": entry_id, "original_title": TITLE, "original_artist": ARTIST_FEAT,
        "spotify_track": dict(ITUNES_PICK)})

    assert response.status_code == 200, response.get_json()
    found = ws.get_database().get_discovery_cache_match(key[0], key[1], "deezer")
    assert (found or {}).get("id") == ITUNES_PICK["id"]
    assert (found["source"], found["track_number"], found["album"]["total_tracks"]) == ("itunes", 4, 14)
    # Replaced in place, not saved again under a key of its own.
    assert [row["provider"] for row in _rows_for(ws, TITLE)] == ["deezer"]


def test_a_rematch_of_an_entry_that_is_gone_changes_nothing(ws):
    entry_id, key = _cached_entry(ws)

    response = ws.app.test_client().post("/api/discovery-pool/rematch", json={
        "cache_id": entry_id + 987654321, "original_title": TITLE, "original_artist": ARTIST_FEAT,
        "spotify_track": dict(ITUNES_PICK)})

    assert response.status_code == 404, response.get_json()
    # Not even the entry that is there, under the very names sent, takes the pick.
    assert ws.get_database().get_discovery_cache_match(key[0], key[1], "deezer")["id"] == "old"
    assert [row["provider"] for row in _rows_for(ws, TITLE)] == ["deezer"]


def test_a_rematch_without_a_pick_clears_the_entry(ws):
    entry_id, key = _cached_entry(ws)

    response = ws.app.test_client().post("/api/discovery-pool/rematch", json={"cache_id": entry_id})

    assert response.status_code == 200, response.get_json()
    assert ws.get_database().get_discovery_cache_match(key[0], key[1], "deezer") is None


# ── match counts follow each platform's rule for guesses ────────────────────
# Tidal, Deezer, Qobuz, link-playlist and Beatport discovery count a Wing It guess as a
# match (core/discovery/*.py); YouTube, mirrored and ListenBrainz discovery don't.
SPOTIFY_PICK = dict(ITUNES_PICK, source="spotify")


def _guess(**row):
    return dict({"index": 0, "status": "Wing It", "status_class": "wing-it", "wing_it_fallback": True,
                 "spotify_data": {"id": "wing_it_g", "name": "Guess"}}, **row)


def test_a_beatport_guess_unmatched_or_marked_leaves_the_match_count(ws):
    ws.beatport_chart_states["zqum-bp"] = {
        "phase": "discovered", "spotify_matches": 2, "wing_it_count": 2,
        "discovery_results": [_guess(index=0, status="found"), _guess(index=1, status="found")],
    }
    try:
        client = ws.app.test_client()
        assert _unmatch(ws, "zqum-bp", 0, route="beatport").status_code == 200
        assert client.post("/api/discovery/unavailable", json={"identifier": "zqum-bp", "track_index": 1}).status_code == 200

        state = ws.beatport_chart_states["zqum-bp"]
        assert (state["spotify_matches"], state["wing_it_count"]) == (0, 0)
    finally:
        ws.beatport_chart_states.pop("zqum-bp", None)


def test_a_fix_on_a_qobuz_tab_playlist_guess_counts_it_once(ws):
    ws.qobuz_discovery_states["zqum-qg"] = {
        "phase": "discovered", "spotify_matches": 1,
        "discovery_results": [_guess(qobuz_track={"name": "Guess zqum", "artists": [ARTIST]})],
    }
    try:
        response = ws.app.test_client().post("/api/qobuz/discovery/update_match", json={
            "identifier": "zqum-qg", "track_index": 0, "spotify_track": dict(SPOTIFY_PICK)})

        assert response.status_code == 200, response.get_json()
        assert ws.qobuz_discovery_states["zqum-qg"]["spotify_matches"] == 1
    finally:
        ws.qobuz_discovery_states.pop("zqum-qg", None)


def test_a_fix_on_an_apple_music_link_guess_counts_it_once(ws):
    ws.itunes_link_discovery_states["zqum-link"] = {
        "phase": "discovered", "spotify_matches": 1,
        "discovery_results": [_guess(itunes_link_track={"name": "Link guess zqum", "artists": [ARTIST]})],
    }
    try:
        response = ws.app.test_client().post("/api/itunes-link/discovery/update_match", json={
            "identifier": "zqum-link", "track_index": 0, "spotify_track": dict(SPOTIFY_PICK)})

        assert response.status_code == 200, response.get_json()
        assert ws.itunes_link_discovery_states["zqum-link"]["spotify_matches"] == 1
    finally:
        ws.itunes_link_discovery_states.pop("zqum-link", None)


def test_a_fix_on_a_listenbrainz_guess_counts_it(ws):
    with ws.app.test_request_context():
        key = ws._lb_state_key("zqum-lbg")
    ws.listenbrainz_playlist_states[key] = {
        "phase": "discovered", "spotify_matches": 0,
        "discovery_results": [_guess(lb_track="LB guess zqum", lb_artist=ARTIST)],
    }
    try:
        response = ws.app.test_client().post("/api/listenbrainz/discovery/update_match", json={
            "identifier": "zqum-lbg", "track_index": 0, "spotify_track": dict(SPOTIFY_PICK)})

        assert response.status_code == 200, response.get_json()
        assert ws.listenbrainz_playlist_states[key]["spotify_matches"] == 1
    finally:
        ws.listenbrainz_playlist_states.pop(key, None)
