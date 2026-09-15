"""The Fix dialog's search across every metadata source, and what a pick saves.

The dialog used to search the main metadata source and fall back to the next only
when it found nothing. It now asks every source at once, one request each
(GET /api/discovery/fix-search), and shows all their answers.

That makes a pick from a source other than the main one ordinary. A download fills
in what a saved match lacks by looking its id up -- a number-only id on the main
source's client (core/downloads/track_metadata_backfill.py) -- so an iTunes or
Deezer pick is completed from its own source before it's saved
(core.discovery.fix_pick), and every Fix route keeps what it was completed with.
"""

from __future__ import annotations

import json
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from core.metadata.types import Track

ARTIST = "Daft Punk"
ITUNES_PICK = {"id": "1440857786", "name": "HBFS zqfp", "artists": [ARTIST], "album": "Discovery",
               "duration_ms": 224000, "image_url": "https://img/hbfs", "source": "itunes"}
ITUNES_DETAILS = {"id": "1440857786", "name": "HBFS zqfp", "track_number": 4, "disc_number": 1,
                  "album": {"id": "1440857781", "name": "Discovery", "total_tracks": 14,
                            "release_date": "2001-03-07T08:00:00Z", "album_type": "album"}}
DEEZER_PICK = dict(ITUNES_PICK, id="3135556", source="deezer")
DEEZER_DETAILS = {"id": "3135556", "name": "HBFS zqfp", "track_number": 4, "disc_number": 1,
                  "album": {"id": "302127", "name": "Discovery", "total_tracks": 0, "release_date": "2001-03-07"}}
DEEZER_ALBUM = {"id": 302127, "nb_tracks": 14, "release_date": "2001-03-07"}
DETAILS = ("track_number", "disc_number", "album_id", "release_date", "total_tracks")
COMPLETE = (4, "2001-03-07", 14)


class _LookupClient:
    """A metadata client that answers track and album lookups."""

    def __init__(self, details=None, album=None, fail=False):
        self.details, self.album, self.fail = details, album, fail
        self.lookups = []

    def get_track_details(self, track_id):
        self.lookups.append(("track", track_id))
        if self.fail:
            raise RuntimeError("the source is down")
        return self.details

    def get_album_raw(self, album_id):
        self.lookups.append(("album", album_id))
        return self.album


def _complete(pick, client):
    from core.discovery.fix_pick import complete_fix_pick
    asked = []
    return complete_fix_pick(pick, lambda source: asked.append(source) or client), asked


def _completed(pick=ITUNES_PICK, details=ITUNES_DETAILS):
    return _complete(dict(pick), _LookupClient(details=details, album=DEEZER_ALBUM))[0]


def _saved_details(saved):
    return saved.get("track_number"), saved["album"].get("release_date"), saved["album"].get("total_tracks")


# ── completing a pick ───────────────────────────────────────────────────────
def test_an_itunes_pick_is_completed_from_itunes():
    pick = dict(ITUNES_PICK)
    completed, asked = _complete(pick, _LookupClient(details=ITUNES_DETAILS))

    assert asked == ["itunes"]
    assert {key: completed[key] for key in DETAILS} == {
        "track_number": 4, "disc_number": 1, "album_id": "1440857781", "release_date": "2001-03-07",
        "total_tracks": 14}
    assert pick == ITUNES_PICK


def test_a_deezer_pick_takes_its_track_count_from_its_album():
    client = _LookupClient(details=DEEZER_DETAILS, album=DEEZER_ALBUM)
    completed, asked = _complete(dict(DEEZER_PICK), client)

    assert asked == ["deezer"]
    assert client.lookups == [("track", "3135556"), ("album", "302127")]
    assert {key: completed[key] for key in DETAILS} == {
        "track_number": 4, "disc_number": 1, "album_id": "302127", "release_date": "2001-03-07", "total_tracks": 14}


def test_a_lookup_fills_in_only_what_the_pick_lacks():
    completed, _ = _complete(dict(ITUNES_PICK, track_number=7, release_date="2001-03-12"),
                             _LookupClient(details=ITUNES_DETAILS))

    assert (completed["track_number"], completed["release_date"], completed["total_tracks"]) == (7, "2001-03-12", 14)


def test_an_answer_for_another_track_is_not_used():
    completed, _ = _complete(dict(ITUNES_PICK), _LookupClient(details=dict(ITUNES_DETAILS, id="999")))
    assert completed == ITUNES_PICK


def test_a_failed_lookup_or_a_source_that_isnt_connected_saves_the_pick_as_it_came():
    assert _complete(dict(ITUNES_PICK), _LookupClient(fail=True))[0] == ITUNES_PICK
    assert _complete(dict(ITUNES_PICK), None)[0] == ITUNES_PICK


def test_a_complete_pick_and_a_spotify_or_musicbrainz_pick_look_nothing_up():
    client = _LookupClient(details=ITUNES_DETAILS)
    complete = dict(ITUNES_PICK, track_number=4, release_date="2001-03-07", total_tracks=14)
    for pick in (complete, dict(ITUNES_PICK, source="spotify"), dict(ITUNES_PICK, source="musicbrainz"),
                 dict(ITUNES_PICK, source="")):
        assert _complete(pick, client) == (pick, [])
    assert client.lookups == []


# ── what a pick saves ───────────────────────────────────────────────────────
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
                    conn.execute("DELETE FROM discovery_match_cache WHERE original_title LIKE ? "
                                 "OR normalized_title LIKE ?", ("%zqfp%", "%zqfp%"))
                    conn.commit()
                finally:
                    conn.close()


def test_a_saved_match_keeps_the_details_and_the_source(ws):
    saved = ws._build_fix_modal_spotify_data(_completed())

    assert (saved["track_number"], saved["disc_number"], saved["source"]) == (4, 1, "itunes")
    assert saved["album"] == {"name": "Discovery", "id": "1440857781", "release_date": "2001-03-07",
                              "total_tracks": 14, "album_type": "album", "image_url": "https://img/hbfs",
                              "images": [{"url": "https://img/hbfs"}]}


def test_a_lean_album_is_saved_without_its_id(ws):
    """A lean album is looked up by id on the main source at download time, and
    this id belongs to the pick's own source."""
    saved = ws._build_fix_modal_spotify_data(dict(ITUNES_PICK, album_id="1440857781", release_date="2001-03-07"))

    assert saved["album"]["release_date"] == "2001-03-07"
    assert "id" not in saved["album"]


def test_a_download_never_looks_a_completed_deezer_pick_up_on_another_source(ws):
    from core.downloads.track_metadata_backfill import backfill_album_context_from_source, hydrate_download_metadata

    def download(match):
        lookups = []
        album_context = dict(match["album"])
        client = SimpleNamespace(get_track_details=lambda track_id: lookups.append(("track", track_id)))
        resolved = hydrate_download_metadata(SimpleNamespace(id=match["id"], track_number=None, disc_number=None),
                                             match, album_context, client)
        backfill_album_context_from_source(album_context, "itunes",
                                           lambda source, album_id: lookups.append((source, album_id)))
        return resolved, lookups

    resolved, lookups = download(ws._build_fix_modal_spotify_data(_completed(DEEZER_PICK, DEEZER_DETAILS)))

    assert (resolved.track_number, resolved.disc_number, lookups) == (4, 1, [])
    # Saved as it came, the Deezer id would go to the download's client.
    assert download(ws._build_fix_modal_spotify_data(dict(DEEZER_PICK)))[1] == [("track", "3135556")]


# ── the search ──────────────────────────────────────────────────────────────
def _track(name, artist, track_id, **fields):
    fields.setdefault("album", "Head Games")
    fields.setdefault("duration_ms", 238000)
    fields.setdefault("album_type", "album")
    return Track(id=track_id, name=name, artists=[artist], **fields)


class _SearchClient:
    def __init__(self, tracks=(), fail=False):
        self.tracks, self.fail, self.calls = list(tracks), fail, []

    def search_tracks(self, query, limit=10, **kwargs):
        self.calls.append((query, limit, kwargs))
        if self.fail:
            raise RuntimeError("the source is down")
        return list(self.tracks)


def _search(ws, monkeypatch, clients, **params):
    import core.metadata.registry as registry
    monkeypatch.setattr(registry, "get_client_for_source", lambda source, **kwargs: clients.get(source))
    return ws.app.test_client().get("/api/discovery/fix-search", query_string=params)


def test_a_source_answers_with_its_results_ranked_and_ready_to_save(ws, monkeypatch):
    deezer = _SearchClient([
        _track("Dirty White Boy (Karaoke Version)", "Pop Music Workshop", "karaoke-1",
               album="Backing Tracks", album_type="compilation"),
        _track("Dirty White Boy", "Foreigner", "real-1", image_url="https://img/hg", release_date="1979-09-01",
               total_tracks=10, track_number=1, disc_number=1),
    ])

    response = _search(ws, monkeypatch, {"deezer": deezer}, source="deezer", track="Dirty White Boy",
                       artist="Foreigner", limit=30)

    assert response.status_code == 200, response.get_json()
    body = response.get_json()
    assert (body["source"], body["status"]) == ("deezer", "ok")
    assert [t["id"] for t in body["tracks"]] == ["real-1", "karaoke-1"]
    real, karaoke = body["tracks"]
    fields = ("name", "artists", "album", "duration_ms", "image_url", "source", "track_number", "disc_number",
              "release_date", "total_tracks", "album_type")
    assert {key: real[key] for key in fields} == {
        "name": "Dirty White Boy", "artists": ["Foreigner"], "album": "Head Games", "duration_ms": 238000,
        "image_url": "https://img/hg", "source": "deezer", "track_number": 1, "disc_number": 1,
        "release_date": "1979-09-01", "total_tracks": 10, "album_type": "album"}
    assert real["relevance"] > karaoke["relevance"]
    assert deezer.calls == [("Dirty White Boy Foreigner", 30, {})]


def test_spotify_answers_from_spotify_alone(ws, monkeypatch):
    """Spotify's client falls back to another source when it finds nothing, and
    those results would go out under Spotify's name."""
    spotify = _SearchClient([_track("Dirty White Boy", "Foreigner", "4uLU6hMCjMI75M1A2tKUQC")])

    response = _search(ws, monkeypatch, {"spotify": spotify}, source="spotify", track="Dirty White Boy")

    assert response.get_json()["status"] == "ok"
    assert spotify.calls == [("Dirty White Boy", 20, {"allow_fallback": False})]


def test_a_source_that_isnt_connected_says_so(ws, monkeypatch):
    response = _search(ws, monkeypatch, {}, source="spotify", track="Dirty White Boy")

    assert response.status_code == 200
    assert response.get_json() == {"source": "spotify", "status": "not_connected", "tracks": []}


def test_a_failing_source_reports_its_error(ws, monkeypatch):
    response = _search(ws, monkeypatch, {"itunes": _SearchClient(fail=True)}, source="itunes",
                       track="Dirty White Boy")

    assert response.status_code == 502
    body = response.get_json()
    assert (body["source"], body["status"], body["tracks"]) == ("itunes", "error", [])


def test_an_unknown_source_or_an_empty_search_is_refused(ws, monkeypatch):
    deezer = _SearchClient()

    assert _search(ws, monkeypatch, {"deezer": deezer}, source="napster", track="X").status_code == 400
    assert _search(ws, monkeypatch, {"deezer": deezer}, source="deezer").status_code == 400
    assert deezer.calls == []


def test_musicbrainz_searches_by_track_and_artist(ws, monkeypatch):
    calls = []

    class _MusicBrainz:
        def search_tracks_with_artist(self, track, artist, limit=10):
            calls.append((track, artist, limit))
            return [_track("Coffee Break", "Zeds Dead", "mbid-1", release_date="2019-01-01", total_tracks=1)]

    monkeypatch.setattr("core.musicbrainz_search.MusicBrainzSearchClient", _MusicBrainz)
    response = _search(ws, monkeypatch, {}, source="musicbrainz", track="Coffee Break", artist="Zeds Dead")

    assert [(t["id"], t["source"]) for t in response.get_json()["tracks"]] == [("mbid-1", "musicbrainz")]
    assert calls == [("Coffee Break", "Zeds Dead", 20)]


# ── every Fix route saves the completed pick ────────────────────────────────
@pytest.fixture
def itunes_answers(monkeypatch):
    """Every lookup a Fix route makes goes to an iTunes client that knows the pick."""
    import core.metadata.registry as registry
    client = _LookupClient(details=ITUNES_DETAILS)
    monkeypatch.setattr(registry, "get_client_for_source", lambda source, **kwargs: client if source == "itunes" else None)
    return client


@pytest.fixture
def mirrored(ws):
    db = ws.get_database()
    playlist_id = db.mirror_playlist("qobuz", "zqfp-1", "Fix pick", [
        {"track_name": "HBFS zqfp", "artist_name": ARTIST, "album_name": "Discovery", "duration_ms": 224000,
         "source_track_id": "zqfp-0"}], profile_id=1)
    tracks = db.get_mirrored_playlist_tracks(playlist_id)
    db.update_mirrored_track_extra_data(tracks[0]["id"], {"discovered": False, "discovery_attempted": True,
                                                          "provider": ws._get_active_discovery_source()})
    yield db, playlist_id, tracks
    ws.youtube_playlist_states.pop(f"mirrored_{playlist_id}", None)
    conn = db._get_connection()
    try:
        conn.execute("DELETE FROM mirrored_playlist_tracks WHERE playlist_id = ?", (playlist_id,))
        conn.execute("DELETE FROM mirrored_playlists WHERE id = ?", (playlist_id,))
        conn.commit()
    finally:
        conn.close()


def _matched_data(db, playlist_id):
    return json.loads(db.get_mirrored_playlist_tracks(playlist_id)[0]["extra_data"])["matched_data"]


def test_a_mirrored_playlist_fix_saves_the_completed_pick(ws, mirrored, itunes_answers):
    db, playlist_id, tracks = mirrored
    client = ws.app.test_client()
    assert client.post(f"/api/mirrored-playlists/{playlist_id}/prepare-discovery").status_code == 200

    response = client.post("/api/youtube/discovery/update_match", json={
        "identifier": f"mirrored_{playlist_id}", "track_index": 0, "original_name": "HBFS zqfp",
        "original_artist": ARTIST, "spotify_track": dict(ITUNES_PICK)})

    assert response.status_code == 200, response.get_json()
    row = ws.youtube_playlist_states[f"mirrored_{playlist_id}"]["discovery_results"][0]
    assert _saved_details(row["spotify_data"]) == COMPLETE
    matched = _matched_data(db, playlist_id)
    assert (_saved_details(matched), matched["source"]) == (COMPLETE, "itunes")


def test_a_wing_it_pool_fix_saves_the_completed_pick(ws, mirrored, itunes_answers):
    db, playlist_id, tracks = mirrored

    response = ws.app.test_client().post("/api/discovery-pool/fix",
                                         json={"track_id": tracks[0]["id"], "spotify_track": dict(ITUNES_PICK)})

    assert response.status_code == 200, response.get_json()
    assert _saved_details(_matched_data(db, playlist_id)) == COMPLETE


def test_a_tab_playlist_fix_saves_the_completed_pick_and_its_cache_row(ws, itunes_answers):
    ws.qobuz_discovery_states["zqfp-qobuz"] = {"phase": "discovered", "spotify_matches": 0, "discovery_results": [
        {"index": 0, "status": "Not Found", "status_class": "not-found",
         "qobuz_track": {"name": "HBFS zqfp", "artists": [ARTIST]}}]}
    try:
        response = ws.app.test_client().post("/api/qobuz/discovery/update_match", json={
            "identifier": "zqfp-qobuz", "track_index": 0, "spotify_track": dict(ITUNES_PICK)})

        assert response.status_code == 200, response.get_json()
        row = ws.qobuz_discovery_states["zqfp-qobuz"]["discovery_results"][0]
        assert _saved_details(row["spotify_data"]) == COMPLETE
        key = ws._get_discovery_cache_key("HBFS zqfp", ARTIST)
        cached = ws.get_database().get_discovery_cache_match(key[0], key[1], ws._get_active_discovery_source())
        assert (_saved_details(cached), cached["source"]) == (COMPLETE, "itunes")
    finally:
        ws.qobuz_discovery_states.pop("zqfp-qobuz", None)


def test_a_beatport_fix_saves_the_completed_pick(ws, itunes_answers):
    ws.beatport_chart_states["zqfp-beatport"] = {"phase": "discovered", "spotify_matches": 0, "discovery_results": [
        {"index": 0, "status": "not_found", "status_class": "not-found",
         "beatport_track": {"title": "HBFS zqfp", "artist": ARTIST}}]}
    try:
        response = ws.app.test_client().post("/api/beatport/discovery/update_match", json={
            "identifier": "zqfp-beatport", "track_index": 0, "spotify_track": dict(ITUNES_PICK)})

        assert response.status_code == 200, response.get_json()
        row = ws.beatport_chart_states["zqfp-beatport"]["discovery_results"][0]
        assert _saved_details(row["spotify_data"]) == COMPLETE
    finally:
        ws.beatport_chart_states.pop("zqfp-beatport", None)


def test_an_apple_music_link_fix_saves_the_completed_pick(ws, itunes_answers):
    ws.itunes_link_discovery_states["zqfp-link"] = {"phase": "discovered", "spotify_matches": 0, "discovery_results": [
        {"index": 0, "status": "Not Found", "status_class": "not-found",
         "itunes_link_track": {"name": "HBFS zqfp", "artists": [ARTIST]}}]}
    try:
        response = ws.app.test_client().post("/api/itunes-link/discovery/update_match", json={
            "identifier": "zqfp-link", "track_index": 0, "spotify_track": dict(ITUNES_PICK)})

        assert response.status_code == 200, response.get_json()
        row = ws.itunes_link_discovery_states["zqfp-link"]["discovery_results"][0]
        assert _saved_details(row["spotify_data"]) == COMPLETE
    finally:
        ws.itunes_link_discovery_states.pop("zqfp-link", None)


def test_a_listenbrainz_fix_saves_the_completed_pick(ws, itunes_answers):
    with ws.app.test_request_context():
        key = ws._lb_state_key("zqfp-lb")
    ws.listenbrainz_playlist_states[key] = {"phase": "discovered", "spotify_matches": 0, "discovery_results": [
        {"index": 0, "status": "Not Found", "status_class": "not-found", "lb_track": "HBFS zqfp",
         "lb_artist": ARTIST}]}
    try:
        response = ws.app.test_client().post("/api/listenbrainz/discovery/update_match", json={
            "identifier": "zqfp-lb", "track_index": 0, "spotify_track": dict(ITUNES_PICK)})

        assert response.status_code == 200, response.get_json()
        row = ws.listenbrainz_playlist_states[key]["discovery_results"][0]
        assert _saved_details(row["spotify_data"]) == COMPLETE
    finally:
        ws.listenbrainz_playlist_states.pop(key, None)
