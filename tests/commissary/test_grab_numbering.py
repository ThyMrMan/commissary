"""A manual episode grab records the air date and absolute number its search
judged by, so the importer accepts what the search accepted (fork 2.4.2).

Upstream 3.6.9's manual search works these out itself (_episode_numbering_facts)
but its grab writes only what the browser sent; the wishlist's grabs carry them.
"""

from __future__ import annotations

from types import SimpleNamespace

import api.video.downloads as dl

FACTS = {"air_date": "2024-04-07", "absolute": 82, "series_type": "anime", "alt_season": 4}


def _patch(monkeypatch, calls):
    def facts(db, tmdb, season, episode, tvdb_id=None):
        calls.append((tmdb, season, episode))
        return dict(FACTS)
    monkeypatch.setattr(dl, "_episode_numbering_facts", facts)


def test_an_episode_grab_records_the_numbering(monkeypatch):
    calls = []
    _patch(monkeypatch, calls)
    ctx = {"scope": "episode", "season": 4, "episode": 16, "tmdb_id": 94605}
    assert dl._grab_numbering(None, {}, ctx) == {"air_date": "2024-04-07", "absolute": 82}
    assert calls == [(94605, 4, 16)]


def test_an_owned_show_is_found_by_its_library_row(monkeypatch):
    calls = []
    _patch(monkeypatch, calls)
    db = SimpleNamespace(search_ids_for=lambda kind, library_id=None, tmdb_id=None: {"tmdb_id": 1399})
    body = {"media_source": "library", "media_id": "12"}
    assert dl._grab_numbering(db, body, {"season": 1, "episode": 2})["absolute"] == 82
    assert calls == [(1399, 1, 2)]


def test_a_movie_or_pack_grab_records_nothing(monkeypatch):
    calls = []
    _patch(monkeypatch, calls)
    assert dl._grab_numbering(None, {}, {"scope": "movie", "tmdb_id": 603}) == {}
    assert dl._grab_numbering(None, {}, {"scope": "season", "season": 1, "tmdb_id": 1}) == {}
    assert calls == []
