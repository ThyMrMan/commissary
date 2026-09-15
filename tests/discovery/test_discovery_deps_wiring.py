"""The discovery workers are handed the ranker and the ISRC lookup.

Both are optional deps: without them a worker scores exactly as before, keeps no
suggestions and never looks an ISRC up. A deps builder that forgot one would
pass every worker test and quietly switch the feature off in the app.
"""

from __future__ import annotations

from unittest.mock import patch


def _web_server():
    with patch("web_server.add_activity_item"), patch("web_server.SpotifyClient"), \
            patch("core.tidal_client.TidalClient"):
        import web_server
    return web_server


def test_the_youtube_and_mirrored_worker_gets_the_ranker_and_the_isrc_lookup():
    ws = _web_server()
    deps = ws._build_youtube_discovery_deps()
    assert deps.discovery_rank_candidates is ws._discovery_rank_candidates
    assert deps.resolve_isrc_match is ws._resolve_isrc_match


def test_the_playlist_pipeline_worker_gets_the_ranker_and_the_isrc_lookup():
    ws = _web_server()
    deps = ws._build_playlist_discovery_deps()
    assert deps.discovery_rank_candidates is ws._discovery_rank_candidates
    assert deps.resolve_isrc_match is ws._resolve_isrc_match


def test_the_isrc_lookup_asks_deezer(monkeypatch):
    import core.discovery.isrc_match as isrc_match
    import core.metadata.registry as registry
    ws = _web_server()
    deezer = object()
    asked = []
    monkeypatch.setattr(registry, "get_client_for_source", lambda source: asked.append(source) or deezer)
    monkeypatch.setattr(isrc_match, "resolve_isrc_track",
                        lambda isrc, duration_ms, client: {"client": client, "isrc": isrc, "ms": duration_ms})

    assert ws._resolve_isrc_match("GBAYE0601498", 224000) == {"client": deezer, "isrc": "GBAYE0601498", "ms": 224000}
    assert asked == ["deezer"]
