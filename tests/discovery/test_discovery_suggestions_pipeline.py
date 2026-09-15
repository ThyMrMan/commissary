"""Near misses kept as suggestions by the Playlist Pipeline's discovery.

The pipeline discovers mirrored playlists in the background
(core/discovery/playlist.py) and accepts a match from 0.7, so the suggestions
it keeps for a Wing It guess are the candidates below that.
"""

from __future__ import annotations

from core.discovery import playlist as dp
from tests.discovery.test_discovery_playlist import _FakeITunesClient, _FakeMatch, _build_deps, _playlist, _track

NEAR = {"nm-1": 0.66, "nm-2": 0.61, "nm-3": 0.58, "nm-4": 0.52}


def _match(mid, name, duration_ms=193000):
    return _FakeMatch(id=mid, name=name, artists=["Kenshi Yonezu"], album="KICK BACK",
                      duration_ms=duration_ms, image_url=f"https://img/{mid}")


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


def test_the_pipeline_saves_near_misses_on_a_guess_and_none_on_a_match():
    tracks = [_track(track_id=1, name="Kick Back", artist="Kenshi Yonezu", duration_ms=193000),
              _track(track_id=2, name="Lemon", artist="Kenshi Yonezu", duration_ms=255000)]
    deps = _build_deps(discovery_source="itunes", tracks_by_playlist={"p1": tracks},
                       itunes_results=_near_misses() + [_match("lemon", "Lemon", 255000)])
    deps.discovery_rank_candidates = _ranker(lambda title: {"lemon": 0.95} if title == "Lemon" else NEAR)

    dp.run_playlist_discovery_worker([_playlist("p1")], deps=deps)

    written = dict(deps._db.extra_data_writes)
    assert written[1]["wing_it_fallback"] is True
    assert [(s["id"], s["confidence"]) for s in written[1]["suggestions"]] == [
        ("nm-1", 0.66), ("nm-2", 0.61), ("nm-3", 0.58)]
    assert written[2]["matched_data"]["name"] == "Lemon"
    assert written[2]["suggestions"] == []
    assert (written[1]["isrc_match"], written[2]["isrc_match"]) == (False, False)


def test_near_misses_from_the_wider_search_count_too():
    """The first searches find nothing; only the 50-result search turns candidates up."""
    class _WideSearchOnly(_FakeITunesClient):
        def search_tracks(self, query, limit=10):
            self.search_calls.append((query, limit))
            return self._results if limit >= 50 else []

    deps = _build_deps(discovery_source="itunes", tracks_by_playlist={
        "p1": [_track(track_id=1, name="Kick Back", artist="Kenshi Yonezu", duration_ms=193000)]})
    wide = _WideSearchOnly(results=_near_misses())
    deps.get_metadata_fallback_client = lambda: wide
    deps.discovery_rank_candidates = _ranker(lambda title: NEAR)

    dp.run_playlist_discovery_worker([_playlist("p1")], deps=deps)

    assert [s["id"] for s in dict(deps._db.extra_data_writes)[1]["suggestions"]] == ["nm-1", "nm-2", "nm-3"]


def test_a_cached_match_saves_no_suggestions():
    cached = {"id": "c-1", "name": "Kick Back", "artists": ["Kenshi Yonezu"], "album": {"name": "KICK BACK"}}
    deps = _build_deps(discovery_source="itunes", cache_match=cached,
                       tracks_by_playlist={"p1": [_track(track_id=1, name="Kick Back", artist="Kenshi Yonezu")]})
    deps.discovery_rank_candidates = _ranker(lambda title: NEAR)

    dp.run_playlist_discovery_worker([_playlist("p1")], deps=deps)

    written = dict(deps._db.extra_data_writes)[1]
    assert (written["suggestions"], written["isrc_match"]) == ([], False)
