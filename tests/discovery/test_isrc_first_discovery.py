"""Exact ISRC matches in playlist discovery.

A Qobuz track carries an ISRC -- the code every release of one recording shares
-- but the Qobuz client dropped it, no mirrored playlist stored it, and discovery
only ever searched by title and artist. iTunes, a common discovery source, has
no ISRC lookup; Deezer looks one up for free. So a track with an ISRC is now
matched on Deezer before any cache or search. The match carries its full album
(track count, release date) and position, so no later step has to look its
Deezer ids up with another source's client, and it is marked so re-discovery
and the provider-drift check leave it alone.
"""

from __future__ import annotations

import json
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from core.discovery import playlist as dp
from core.discovery import youtube as dy
from core.discovery.manual_match import is_drifted_for_redo, should_rediscover
from tests.discovery import test_discovery_playlist as playlist_tests
from tests.discovery import test_discovery_youtube as youtube_tests

CODE = "GBAYE0601498"

DEEZER_TRACK = {
    "id": 3135556, "title": "Harder, Better, Faster, Stronger", "isrc": CODE, "duration": 224,
    "track_position": 4, "disk_number": 1, "release_date": "2001-03-07",
    "artist": {"id": 27, "name": "Daft Punk"}, "contributors": [{"id": 27, "name": "Daft Punk"}],
    "album": {"id": 302127, "title": "Discovery", "cover_xl": "https://cdn/discovery-xl.jpg",
              "release_date": "2001-03-07"},
}
DEEZER_ALBUM = {"id": 302127, "title": "Discovery", "nb_tracks": 14, "release_date": "2001-03-07",
                "record_type": "album", "cover_xl": "https://cdn/discovery-xl.jpg",
                "artist": {"id": 27, "name": "Daft Punk"}, "label": "Parlophone"}
MATCH = {
    "id": "3135556", "name": "Harder, Better, Faster, Stronger", "artists": [{"name": "Daft Punk"}],
    "album": {"id": "302127", "name": "Discovery", "release_date": "2001-03-07", "total_tracks": 14,
              "album_type": "album", "images": [{"url": "https://cdn/discovery-xl.jpg"}],
              "image_url": "https://cdn/discovery-xl.jpg", "artists": [{"name": "Daft Punk"}]},
    "duration_ms": 224000, "image_url": "https://cdn/discovery-xl.jpg", "source": "deezer",
    "track_number": 4, "disc_number": 1, "isrc": CODE,
}


# ── reading an ISRC ─────────────────────────────────────────────────────────
@pytest.mark.parametrize("value, expected", [
    ("GBAYE0601498", CODE), ("gbaye0601498", CODE), ("GB-AYE-06-01498", CODE), (" GBAYE0601498 ", CODE),
    ({"value": "US-RC1-76-07839"}, "USRC17607839"), ({"id": "USRC17607839"}, "USRC17607839"),
    ("GBAYE060149", ""), ("1BAYE0601498", ""), ("GBAYE06014XY", ""), ("", ""), (None, ""), (12345, ""),
])
def test_an_isrc_is_read_in_one_shape(value, expected):
    from core.text.isrc import normalize_isrc
    assert normalize_isrc(value) == expected


def _qobuz_item(isrc):
    return {"id": 555, "title": "Forgotten Track", "duration": 240, "performer": {"name": "Some Artist"},
            "album": {"title": "Some Album", "image": {"large": "https://qobuz.example/art.jpg"}}, "isrc": isrc}


@pytest.mark.parametrize("raw, expected", [(CODE, CODE), ({"value": "us-rc1-76-07839"}, "USRC17607839"), (None, "")])
def test_a_qobuz_track_keeps_its_isrc(raw, expected):
    from core.qobuz_client import QobuzClient
    assert QobuzClient._normalize_qobuz_track(None, _qobuz_item(raw))["isrc"] == expected


# ── into the mirrored playlist ──────────────────────────────────────────────
def test_the_qobuz_refresh_carries_the_isrc_into_the_mirror():
    from core.playlists.sources import to_mirror_track_dict
    from core.playlists.sources.qobuz import QobuzPlaylistSource
    adapter = QobuzPlaylistSource.__new__(QobuzPlaylistSource)
    track = adapter._track_from_dict({"id": "555", "name": "Forgotten Track", "artists": ["Some Artist"],
                                      "album": "Some Album", "duration_ms": 240000, "isrc": CODE}, 1)
    assert to_mirror_track_dict(track)["isrc"] == CODE


def test_a_track_without_an_isrc_projects_as_before():
    from core.playlists.sources import NormalizedTrack, to_mirror_track_dict
    track = NormalizedTrack(position=0, track_name="Song", artist_name="Artist", source_track_id="abc",
                            extra={"isrc": "not a code"})
    assert "isrc" not in to_mirror_track_dict(track)


def _db(tmp_path):
    from database.music_database import MusicDatabase
    return MusicDatabase(str(tmp_path / "music.db"))


def _isrcs(db, playlist_id):
    return [t.get("isrc") for t in db.get_mirrored_playlist_tracks(playlist_id)]


def test_a_mirrored_track_stores_its_isrc(tmp_path):
    db = _db(tmp_path)
    playlist_id = db.mirror_playlist("qobuz", "p1", "P", [
        {"track_name": "A", "artist_name": "X", "source_track_id": "1", "isrc": "gb-aye-06-01498"},
        {"track_name": "B", "artist_name": "X", "source_track_id": "2", "isrc": "not an isrc"},
        {"track_name": "C", "artist_name": "X", "source_track_id": "3"},
    ], profile_id=1)
    assert _isrcs(db, playlist_id) == [CODE, None, None]


def test_re_mirroring_without_isrcs_keeps_the_stored_ones(tmp_path):
    db = _db(tmp_path)
    playlist_id = db.mirror_playlist("qobuz", "p1", "P", [
        {"track_name": "A", "artist_name": "X", "source_track_id": "1", "isrc": CODE}], profile_id=1)
    db.mirror_playlist("qobuz", "p1", "P", [
        {"track_name": "A", "artist_name": "X", "source_track_id": "1"}], profile_id=1)
    assert _isrcs(db, playlist_id) == [CODE]


def test_an_existing_database_gains_the_isrc_column(tmp_path):
    import sqlite3
    path = tmp_path / "old.db"
    conn = sqlite3.connect(path)
    conn.executescript("""
        CREATE TABLE mirrored_playlist_tracks (
            id INTEGER PRIMARY KEY AUTOINCREMENT, playlist_id INTEGER NOT NULL, position INTEGER NOT NULL,
            track_name TEXT NOT NULL, artist_name TEXT NOT NULL, album_name TEXT DEFAULT '',
            duration_ms INTEGER DEFAULT 0, image_url TEXT, source_track_id TEXT, extra_data TEXT,
            UNIQUE(playlist_id, position));
        INSERT INTO mirrored_playlist_tracks (playlist_id, position, track_name, artist_name) VALUES (1, 1, 'Old', 'X');
    """)
    conn.commit()
    conn.close()

    from database.music_database import MusicDatabase
    MusicDatabase(str(path))

    conn = sqlite3.connect(path)
    try:
        columns = [row[1] for row in conn.execute("PRAGMA table_info(mirrored_playlist_tracks)")]
        assert "isrc" in columns
        assert conn.execute("SELECT track_name, isrc FROM mirrored_playlist_tracks").fetchall() == [("Old", None)]
    finally:
        conn.close()


# ── looking an ISRC up on Deezer ────────────────────────────────────────────
class _Clock:
    def __init__(self, start=1000.0):
        self.now = start
        self.slept = []

    def time(self):
        return self.now

    def sleep(self, seconds):
        self.slept.append(seconds)
        self.now += seconds


class _Cache:
    def __init__(self):
        self.entities, self.searches = {}, {}

    def get_search_results(self, source, kind, query, limit):
        ids = self.searches.get((source, kind, query.strip().lower(), limit))
        return None if ids is None else [self.entities[(source, kind, i)] for i in ids]

    def store_entity(self, source, kind, entity_id, raw):
        self.entities[(source, kind, entity_id)] = raw

    def store_search_results(self, source, kind, query, limit, ids):
        self.searches[(source, kind, query.strip().lower(), limit)] = list(ids)


@pytest.fixture
def deezer(monkeypatch):
    import core.deezer_client as dc
    clock, cache = _Clock(), _Cache()
    monkeypatch.setattr(dc, "time", clock)
    monkeypatch.setattr(dc, "_last_api_call_time", clock.now)
    monkeypatch.setattr(dc, "get_metadata_cache", lambda: cache)
    client = dc.DeezerClient.__new__(dc.DeezerClient)
    calls = []

    def api_get(endpoint, params=None, timeout=15):
        calls.append(endpoint)
        return dict(DEEZER_TRACK)

    client._api_get = api_get
    return client, calls, clock, dc.MIN_API_INTERVAL


def test_deezer_looks_an_isrc_up_once_and_waits_only_to_ask(deezer):
    client, calls, clock, interval = deezer

    first = client.get_track_by_isrc("gb-aye-06-01498")
    waited = sum(clock.slept)
    second = client.get_track_by_isrc(CODE)

    assert first["id"] == second["id"] == 3135556
    assert calls == [f"track/isrc:{CODE}"]
    assert waited == pytest.approx(interval)
    assert sum(clock.slept) == waited


def test_an_invalid_or_unknown_isrc_is_no_track(deezer):
    client, calls, _clock, _interval = deezer
    assert client.get_track_by_isrc("not a code") is None
    assert calls == []
    client._api_get = lambda endpoint, params=None, timeout=15: calls.append(endpoint)
    assert client.get_track_by_isrc(CODE) is None
    assert calls == [f"track/isrc:{CODE}"]


class _FakeDeezer:
    def __init__(self, track=DEEZER_TRACK, album=DEEZER_ALBUM):
        self.track, self.album, self.lookups = track, album, []

    def get_track_by_isrc(self, isrc):
        self.lookups.append(isrc)
        return dict(self.track) if self.track else None

    def get_album_raw(self, album_id):
        return dict(self.album) if self.album else None


def test_an_isrc_resolves_to_a_complete_deezer_match():
    from core.discovery.isrc_match import resolve_isrc_track
    assert resolve_isrc_track(CODE, 224000, _FakeDeezer()) == MATCH


@pytest.mark.parametrize("duration_ms, trusted", [(224000, True), (228000, True), (231000, False), (0, True)])
def test_a_length_far_off_the_playlist_track_is_not_trusted(duration_ms, trusted):
    from core.discovery.isrc_match import resolve_isrc_track
    assert (resolve_isrc_track(CODE, duration_ms, _FakeDeezer()) is not None) is trusted


def test_an_answer_for_another_isrc_is_not_used():
    from core.discovery.isrc_match import resolve_isrc_track
    assert resolve_isrc_track(CODE, 224000, _FakeDeezer(track=dict(DEEZER_TRACK, isrc="USRC17607839"))) is None


def test_without_full_album_details_the_match_is_not_used():
    """A lean album is filled in at download time from the main source, with this
    album's id -- a Deezer id handed to iTunes. Better no ISRC match than that."""
    from core.discovery.isrc_match import resolve_isrc_track
    assert resolve_isrc_track(CODE, 224000, _FakeDeezer(album=None)) is None
    assert resolve_isrc_track(CODE, 224000, _FakeDeezer(album=dict(DEEZER_ALBUM, nb_tracks=0))) is None
    assert resolve_isrc_track(CODE, 224000, _FakeDeezer(
        track=dict(DEEZER_TRACK, release_date="", album=dict(DEEZER_TRACK["album"], release_date="")),
        album=dict(DEEZER_ALBUM, release_date=""))) is None


def test_without_its_place_on_the_album_the_match_is_not_used():
    """A download looks up a track that has no number again -- by this Deezer id, on
    the main source's client."""
    from core.discovery.isrc_match import resolve_isrc_track
    unplaced = {key: value for key, value in DEEZER_TRACK.items() if key != "track_position"}
    assert resolve_isrc_track(CODE, 224000, _FakeDeezer(track=unplaced)) is None


def test_no_code_no_client_or_a_failure_is_no_match():
    from core.discovery.isrc_match import resolve_isrc_track
    fake = _FakeDeezer()
    assert resolve_isrc_track("", 0, fake) is None
    assert fake.lookups == []
    assert resolve_isrc_track(CODE, 0, None) is None

    class _Broken(_FakeDeezer):
        def get_track_by_isrc(self, isrc):
            raise RuntimeError("deezer is down")

    assert resolve_isrc_track(CODE, 0, _Broken()) is None


def test_a_download_never_looks_an_isrc_match_up_again():
    from core.discovery.isrc_match import resolve_isrc_track
    from core.downloads.track_metadata_backfill import backfill_album_context_from_source, hydrate_download_metadata
    match = resolve_isrc_track(CODE, 224000, _FakeDeezer())
    album_context = dict(match["album"])
    lookups = []
    spotify = SimpleNamespace(get_track_details=lambda track_id: lookups.append(("track", track_id)))

    resolved = hydrate_download_metadata(SimpleNamespace(id=match["id"], track_number=None, disc_number=None),
                                         match, album_context, spotify)
    filled = backfill_album_context_from_source(album_context, "itunes",
                                                lambda source, album_id: lookups.append((source, album_id)))

    assert (resolved.track_number, resolved.disc_number) == (4, 1)
    assert filled is False
    assert lookups == []


# ── the discovery workers ───────────────────────────────────────────────────
def _yt_track(name, isrc=None, db_track_id=None):
    track = youtube_tests._track(name, "Daft Punk", 224000)
    if isrc:
        track["isrc"] = isrc
    if db_track_id:
        track["db_track_id"] = db_track_id
    return track


def _isrc_resolver(isrc, duration_ms):
    return json.loads(json.dumps(MATCH)) if isrc == CODE else None


def test_a_track_with_an_isrc_is_matched_exactly_without_searching():
    states = {}
    youtube_tests._seed_state("mirrored_isrc", states, tracks=[
        _yt_track("Harder, Better, Faster, Stronger", CODE, "db-isrc"), _yt_track("One More Time", None, "db-plain")])
    deps = youtube_tests._build_deps(states=states, discovery_source="itunes")
    deps.resolve_isrc_match = _isrc_resolver

    dy.run_youtube_discovery_worker("mirrored_isrc", deps)

    exact, _plain = states["mirrored_isrc"]["discovery_results"]
    assert (exact["status"], exact["discovery_source"], exact["confidence"], exact.get("isrc_match")) == (
        "Found", "deezer", 1.0, True)
    assert (exact["spotify_track"], exact["spotify_artist"]) == (MATCH["name"], "Daft Punk")
    assert deps._itunes.search_calls, "the track without an ISRC is still searched"
    assert not any("Harder" in query for query, _limit in deps._itunes.search_calls)
    written = dict(deps._db.mirrored_updates)
    assert (written["db-isrc"]["provider"], written["db-isrc"]["isrc_match"]) == ("deezer", True)
    assert written["db-plain"]["isrc_match"] is False


def test_the_pipeline_matches_a_track_by_isrc_without_searching():
    tracks = [dict(playlist_tests._track(track_id=1, name="Harder, Better, Faster, Stronger", artist="Daft Punk",
                                         duration_ms=224000), isrc=CODE),
              playlist_tests._track(track_id=2, name="One More Time", artist="Daft Punk")]
    deps = playlist_tests._build_deps(discovery_source="itunes", tracks_by_playlist={"p1": tracks})
    deps.resolve_isrc_match = _isrc_resolver

    dp.run_playlist_discovery_worker([playlist_tests._playlist("p1")], deps=deps)

    written = dict(deps._db.extra_data_writes)
    assert (written[1]["provider"], written[1]["confidence"], written[1]["isrc_match"]) == ("deezer", 1.0, True)
    assert written[1]["matched_data"]["id"] == MATCH["id"]
    assert not any("Harder" in query for query, _limit in deps._itunes.search_calls)
    assert written[2]["isrc_match"] is False


def test_an_isrc_match_is_never_provider_drift_or_rediscovered():
    exact = {"discovered": True, "provider": "deezer", "isrc_match": True, "matched_data": {"name": "X"}}
    assert is_drifted_for_redo(exact, "itunes") is False
    assert should_rediscover(exact) is False


def test_without_the_flag_a_deezer_match_still_drifts_under_itunes():
    plain = {"discovered": True, "provider": "deezer", "matched_data": {"name": "X"}}
    assert is_drifted_for_redo(plain, "itunes") is True
    assert should_rediscover(plain) is True


# ── the real app ────────────────────────────────────────────────────────────
SOURCE_PLAYLIST_ID = "zqisrc-1"
ARTIST = "Artist zqisrc"


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
    db = ws.get_database()
    playlist_id = db.mirror_playlist("qobuz", SOURCE_PLAYLIST_ID, "ISRC", [
        {"track_name": "Harder zqisrc", "artist_name": ARTIST, "duration_ms": 224000,
         "source_track_id": "zqisrc-a", "isrc": CODE},
        {"track_name": "One More Time zqisrc", "artist_name": ARTIST, "duration_ms": 320000,
         "source_track_id": "zqisrc-b"},
    ], profile_id=1)
    yield db, playlist_id, db.get_mirrored_playlist_tracks(playlist_id)
    ws.youtube_playlist_states.pop(f"mirrored_{playlist_id}", None)
    conn = db._get_connection()
    try:
        conn.execute("DELETE FROM mirrored_playlist_tracks WHERE playlist_id = ?", (playlist_id,))
        conn.execute("DELETE FROM mirrored_playlists WHERE id = ?", (playlist_id,))
        conn.execute("DELETE FROM discovery_match_cache WHERE original_title LIKE ? OR normalized_title LIKE ?",
                     ("%zqisrc%", "%zqisrc%"))
        conn.commit()
    finally:
        conn.close()


def _extras(db, playlist_id):
    return [json.loads(t["extra_data"]) if t.get("extra_data") else {}
            for t in db.get_mirrored_playlist_tracks(playlist_id)]


def _exact(db, track_id):
    db.update_mirrored_track_extra_data(track_id, {"discovered": True, "provider": "deezer", "confidence": 1.0,
                                                   "isrc_match": True, "matched_data": MATCH})


def _prepare(ws, playlist_id):
    response = ws.app.test_client().post(f"/api/mirrored-playlists/{playlist_id}/prepare-discovery")
    assert response.status_code == 200, response.get_json()
    return ws.youtube_playlist_states[f"mirrored_{playlist_id}"]


def test_reopening_hands_discovery_the_isrcs_and_keeps_an_isrc_match_found(ws, mirrored, monkeypatch):
    """Under iTunes a Deezer match is provider drift, unless it was an exact ISRC match."""
    db, playlist_id, tracks = mirrored
    monkeypatch.setattr(ws, "_get_active_discovery_source", lambda: "itunes")
    _exact(db, tracks[0]["id"])
    db.update_mirrored_track_extra_data(tracks[1]["id"], {"discovered": True, "provider": "deezer", "confidence": 0.9,
                                                          "matched_data": {"id": "m-2", "name": "One More Time"}})

    state = _prepare(ws, playlist_id)

    assert [t.get("isrc") for t in state["playlist"]["tracks"]] == [CODE, ""]
    assert [r["status"] for r in state["discovery_results"]] == ["Found", "Provider changed"]
    assert state["discovery_results"][0].get("isrc_match") is True


def test_a_fix_clears_the_isrc_flag(ws, mirrored):
    db, playlist_id, tracks = mirrored
    _exact(db, tracks[0]["id"])
    _prepare(ws, playlist_id)
    pick = {"id": "itunes-zqisrc", "name": "Harder", "artists": ["Daft Punk"], "album": "Discovery",
            "duration_ms": 224000, "source": "itunes"}

    response = ws.app.test_client().post("/api/youtube/discovery/update_match", json={
        "identifier": f"mirrored_{playlist_id}", "track_index": 0,
        "original_name": "Harder zqisrc", "original_artist": ARTIST, "spotify_track": pick})

    assert response.status_code == 200, response.get_json()
    assert ws.youtube_playlist_states[f"mirrored_{playlist_id}"]["discovery_results"][0]["isrc_match"] is False
    assert _extras(db, playlist_id)[0]["isrc_match"] is False


def test_a_wing_it_pool_fix_clears_the_isrc_flag(ws, mirrored):
    db, playlist_id, tracks = mirrored
    _exact(db, tracks[0]["id"])
    pick = {"id": "itunes-zqisrc", "name": "Harder", "artists": ["Daft Punk"], "album": "Discovery",
            "duration_ms": 224000, "source": "itunes"}

    response = ws.app.test_client().post("/api/discovery-pool/fix", json={"track_id": tracks[0]["id"], "spotify_track": pick})

    assert response.status_code == 200, response.get_json()
    assert _extras(db, playlist_id)[0]["isrc_match"] is False


def test_the_tab_write_back_records_an_isrc_match_as_one(ws, mirrored):
    db, playlist_id, tracks = mirrored
    results = [
        {"index": 0, "status": "Found", "confidence": 1.0, "qobuz_track": {"id": tracks[0]["source_track_id"]},
         "spotify_data": MATCH, "isrc_match": True},
        {"index": 1, "status": "Found", "confidence": 0.95, "qobuz_track": {"id": tracks[1]["source_track_id"]},
         "spotify_data": {"id": "m-2", "name": "One More Time"}},
    ]

    ws._sync_discovery_results_to_mirrored("qobuz", SOURCE_PLAYLIST_ID, results, "itunes", profile_id=1)

    exact, plain = _extras(db, playlist_id)
    assert (exact["provider"], exact["isrc_match"]) == ("deezer", True)
    assert (plain["provider"], plain["isrc_match"]) == ("itunes", False)
