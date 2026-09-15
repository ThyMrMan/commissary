"""Near misses kept as suggestions for a track discovery couldn't match.

Every discovery worker scored its candidates and kept only the best. Below the
auto-match bar the track became a Wing It guess and the other candidates were
thrown away, so fixing it meant searching again from scratch. The best few are
now kept on the result, saved with a mirrored track, restored when the playlist
reopens, and cleared once a match is picked.
"""

from __future__ import annotations

import json
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from core.discovery import scoring
from core.discovery import youtube as dy
from core.matching_engine import MusicMatchingEngine
from tests.discovery.test_discovery_youtube import _FakeMatch, _build_deps, _seed_state, _track


# ── ranking candidates ──────────────────────────────────────────────────────
@pytest.fixture
def engine(monkeypatch):
    monkeypatch.setattr(scoring, "matching_engine", MusicMatchingEngine())


def _cand(cid, name, artist="Kenshi Yonezu", duration_ms=193000):
    return SimpleNamespace(id=cid, name=name, artists=[artist], album="KICK BACK",
                           duration_ms=duration_ms, image_url="")


def test_ranking_keeps_every_candidate_that_clears_both_floors_best_first(engine):
    results = [_cand("tv", "Kick Back (TV Size)", duration_ms=90000),
               _cand("other-artist", "Kick Back", artist="Somebody Else"),
               _cand("exact", "Kick Back"),
               _cand("other-title", "Lemon")]

    ranked = scoring._discovery_rank_candidates("Kick Back", "Kenshi Yonezu", 193000, results)

    assert [track.id for _, _, track in ranked] == ["exact", "tv"]
    (top, top_index, _), (near, near_index, _) = ranked
    assert top >= 0.9 > near
    assert (top_index, near_index) == (2, 0)


def test_the_best_candidate_is_the_top_of_the_ranking(engine):
    results = [_cand("tv", "Kick Back (TV Size)", duration_ms=90000), _cand("exact", "Kick Back")]
    match, confidence, index = scoring._discovery_score_candidates("Kick Back", "Kenshi Yonezu", 193000, results)
    assert (match.id, index) == ("exact", 1)
    assert confidence >= 0.9


def test_equal_candidates_keep_search_order(engine):
    results = [_cand("first", "Kick Back"), _cand("second", "Kick Back")]
    ranked = scoring._discovery_rank_candidates("Kick Back", "Kenshi Yonezu", 193000, results)
    assert [track.id for _, _, track in ranked] == ["first", "second"]
    match, _, index = scoring._discovery_score_candidates("Kick Back", "Kenshi Yonezu", 193000, results)
    assert (match.id, index) == ("first", 0)


def test_nothing_clearing_the_floors_ranks_nothing(engine):
    results = [_cand("lemon", "Lemon")]
    assert scoring._discovery_rank_candidates("Kick Back", "Kenshi Yonezu", 193000, results) == []
    assert scoring._discovery_score_candidates("Kick Back", "Kenshi Yonezu", 193000, results) == (None, 0.0, -1)


# ── building suggestions ────────────────────────────────────────────────────
def _match(mid, name, **kw):
    return _FakeMatch(id=mid, name=name, artists=kw.get("artists", ["Kenshi Yonezu"]),
                      album=kw.get("album", "KICK BACK"), duration_ms=kw.get("duration_ms", 193000),
                      image_url=kw.get("image_url", f"https://img/{mid}"))


def test_suggestions_are_the_best_near_misses_one_per_track():
    from core.discovery.suggestions import build_discovery_suggestions
    ranked = [(0.62, 0, _match("c", "C")), (0.95, 1, _match("hit", "Hit")), (0.84, 2, _match("a", "A")),
              (0.71, 3, _match("b", "B")), (0.80, 4, _match("a", "A again")), (0.55, 5, _match("d", "D"))]

    suggestions = build_discovery_suggestions(ranked, "itunes", below=0.9)

    assert [s["id"] for s in suggestions] == ["a", "b", "c"]
    assert suggestions[0] == {"id": "a", "name": "A", "artists": ["Kenshi Yonezu"], "album": "KICK BACK",
                              "duration_ms": 193000, "image_url": "https://img/a", "source": "itunes",
                              "confidence": 0.84}


def test_suggestions_read_artist_and_album_objects():
    from core.discovery.suggestions import build_discovery_suggestions
    candidate = SimpleNamespace(id=5, name="X", artists=[{"name": "A1"}, "A2"], album={"name": "Alb"},
                                duration_ms=None, image_url=None)
    (suggestion,) = build_discovery_suggestions([(0.7, 0, candidate)], "deezer", below=0.9)
    assert (suggestion["id"], suggestion["artists"], suggestion["album"],
            suggestion["duration_ms"], suggestion["image_url"]) == ("5", ["A1", "A2"], "Alb", 0, "")


def test_a_candidate_without_an_id_is_never_suggested():
    from core.discovery.suggestions import build_discovery_suggestions
    ranked = [(0.8, 0, _match("", "No id")), (0.7, 1, _match("ok", "Ok"))]
    assert [s["id"] for s in build_discovery_suggestions(ranked, "itunes", below=0.9)] == ["ok"]


# ── the YouTube / mirrored discovery worker ─────────────────────────────────
NEAR = {"nm-1": 0.84, "nm-2": 0.71, "nm-3": 0.62, "nm-4": 0.55}


def _near_misses():
    return [_match("nm-2", "Kick Back (Short)"), _match("nm-1", "Kick Back (TV Size)"),
            _match("nm-4", "Kick Back Remix"), _match("nm-3", "Kick Back (Demo)")]


def _ranker(scores_for):
    """A ranker scoring each candidate by id; ``scores_for(title)`` picks the scores."""
    def rank(title, artist, duration_ms, results):
        scores = scores_for(title)
        ranked = [(scores[r.id], i, r) for i, r in enumerate(results) if r.id in scores]
        return sorted(ranked, key=lambda entry: entry[0], reverse=True)
    return rank


def test_a_track_nothing_matched_keeps_its_best_near_misses():
    states = {}
    _seed_state("zq-yt", states, tracks=[_track("Kick Back", "Kenshi Yonezu", 193000)])
    deps = _build_deps(states=states, discovery_source="itunes", itunes_results=_near_misses())
    deps.discovery_rank_candidates = _ranker(lambda title: NEAR)

    dy.run_youtube_discovery_worker("zq-yt", deps)

    (result,) = states["zq-yt"]["discovery_results"]
    assert result["status"] == "Wing It"
    assert [(s["id"], s["confidence"]) for s in result["suggestions"]] == [
        ("nm-1", 0.84), ("nm-2", 0.71), ("nm-3", 0.62)]
    assert {s["source"] for s in result["suggestions"]} == {"itunes"}


def test_a_matched_track_carries_no_suggestions():
    states = {}
    _seed_state("zq-yt-hit", states, tracks=[_track("Kick Back", "Kenshi Yonezu", 193000)])
    deps = _build_deps(states=states, discovery_source="itunes",
                       itunes_results=[_match("hit", "Kick Back")] + _near_misses())
    deps.discovery_rank_candidates = _ranker(lambda title: dict(NEAR, hit=0.97))

    dy.run_youtube_discovery_worker("zq-yt-hit", deps)

    (result,) = states["zq-yt-hit"]["discovery_results"]
    assert (result["status"], result["spotify_track"]) == ("Found", "Kick Back")
    assert not result.get("suggestions")


def test_a_mirrored_guess_saves_its_suggestions_and_a_match_saves_none():
    states = {}
    guess, hit = _track("Kick Back", "Kenshi Yonezu", 193000), _track("Lemon", "Kenshi Yonezu", 255000)
    guess["db_track_id"], hit["db_track_id"] = "db-guess", "db-hit"
    _seed_state("mirrored_zq", states, tracks=[guess, hit])
    deps = _build_deps(states=states, discovery_source="itunes",
                       itunes_results=_near_misses() + [_match("lemon", "Lemon", duration_ms=255000)])
    deps.discovery_rank_candidates = _ranker(lambda title: {"lemon": 0.97} if title == "Lemon" else NEAR)

    dy.run_youtube_discovery_worker("mirrored_zq", deps)

    written = dict(deps._db.mirrored_updates)
    assert [s["id"] for s in written["db-guess"]["suggestions"]] == ["nm-1", "nm-2", "nm-3"]
    assert written["db-hit"]["suggestions"] == []


# ── the real app: restore, pick, accept ─────────────────────────────────────
ARTIST = "Artist zqsugg"
SUGGESTIONS = [{"id": "itunes-zqsugg-1", "name": "KICK BACK", "artists": ["Kenshi Yonezu"],
                "album": "KICK BACK", "duration_ms": 193000, "image_url": "", "source": "itunes",
                "confidence": 0.84}]
STUB = {"id": "wing_it_zq", "name": "Kick Back zqsugg", "artists": [{"name": ARTIST}],
        "album": {"name": ""}, "duration_ms": 193000, "source": "wing_it_fallback"}


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
def guessed(ws):
    """A mirrored playlist whose one track is a Wing It guess with suggestions."""
    db = ws.get_database()
    playlist_id = db.mirror_playlist("qobuz", "zqsugg-1", "Suggestions", [{
        "track_name": "Kick Back zqsugg", "artist_name": ARTIST, "album_name": "Album",
        "duration_ms": 193000, "source_track_id": "zqsugg-track"}], profile_id=1)
    (track,) = db.get_mirrored_playlist_tracks(playlist_id)
    db.update_mirrored_track_extra_data(track["id"], {
        "discovered": True, "provider": "wing_it_fallback", "confidence": 0,
        "wing_it_fallback": True, "matched_data": STUB, "suggestions": SUGGESTIONS})
    yield db, playlist_id, track["id"]
    ws.youtube_playlist_states.pop(f"mirrored_{playlist_id}", None)
    conn = db._get_connection()
    try:
        conn.execute("DELETE FROM mirrored_playlist_tracks WHERE playlist_id = ?", (playlist_id,))
        conn.execute("DELETE FROM mirrored_playlists WHERE id = ?", (playlist_id,))
        conn.execute("DELETE FROM discovery_match_cache WHERE original_title LIKE ? OR normalized_title LIKE ?",
                     ("%zqsugg%", "%zqsugg%"))
        conn.commit()
    finally:
        conn.close()


def _extra(db, playlist_id):
    (track,) = db.get_mirrored_playlist_tracks(playlist_id)
    return json.loads(track["extra_data"])


def _prepare(ws, playlist_id):
    response = ws.app.test_client().post(f"/api/mirrored-playlists/{playlist_id}/prepare-discovery")
    assert response.status_code == 200, response.get_json()
    return ws.youtube_playlist_states[f"mirrored_{playlist_id}"]


def test_reopening_a_playlist_restores_a_guess_and_its_suggestions(ws, guessed):
    _db, playlist_id, _track_id = guessed

    (result,) = _prepare(ws, playlist_id)["discovery_results"]

    assert result["status"] == "Wing It"
    assert result["suggestions"] == SUGGESTIONS


def test_picking_a_match_clears_the_suggestions(ws, guessed):
    db, playlist_id, _track_id = guessed
    _prepare(ws, playlist_id)

    response = ws.app.test_client().post("/api/youtube/discovery/update_match", json={
        "identifier": f"mirrored_{playlist_id}", "track_index": 0,
        "original_name": "Kick Back zqsugg", "original_artist": ARTIST, "spotify_track": SUGGESTIONS[0]})

    assert response.status_code == 200, response.get_json()
    result = ws.youtube_playlist_states[f"mirrored_{playlist_id}"]["discovery_results"][0]
    assert result.get("suggestions") == []
    extra = _extra(db, playlist_id)
    assert extra["manual_match"] is True
    assert extra["suggestions"] == []


def test_accepting_a_suggestion_in_the_wing_it_pool_records_where_it_came_from(ws, guessed):
    db, playlist_id, track_id = guessed

    response = ws.app.test_client().post("/api/discovery-pool/fix",
                                         json={"track_id": track_id, "spotify_track": SUGGESTIONS[0]})

    assert response.status_code == 200, response.get_json()
    extra = _extra(db, playlist_id)
    assert (extra["provider"], extra["matched_data"]["source"]) == ("itunes", "itunes")
    assert extra["manual_match"] is True
    assert extra["suggestions"] == []
