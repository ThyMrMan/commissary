"""TMDB identity by certainty: the server's ids through /find, then a title
search that has to prove itself - never the first result on trust."""

from __future__ import annotations

import sqlite3

import pytest

from core.commissary import tmdb_match as tm
from core.video.enrichment.clients import TMDBClient
from database.video_database import VideoDatabase


class _R:
    def __init__(self, data, code=200):
        self._d, self.status_code = data, code

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError("HTTP %s" % self.status_code)

    def json(self):
        return self._d


def _requests(monkeypatch, routes):
    calls = []

    def get(url, params=None, timeout=None):
        path = url.split("/3", 1)[-1]
        calls.append((path, {k: v for k, v in (params or {}).items() if k != "api_key"}))
        for key, resp in routes.items():
            if path.startswith(key[0]) and all(params.get(k) == v for k, v in key[1]):
                return resp() if callable(resp) else resp
        return _R({"results": []})

    monkeypatch.setattr("requests.get", get)
    return calls


SILO_NAMESAKE = {"id": 1, "name": "Silo Stories"}
SILO = {"id": 125988, "name": "Silo", "overview": "Mysteries"}


def test_the_first_result_is_never_taken_on_trust(monkeypatch):
    _requests(monkeypatch, {("/search/tv", ()): _R({"results": [SILO_NAMESAKE]})})
    assert tm.search_verified(TMDBClient("k"), "show", "Silo", 2023) == (None, None)


def test_the_year_is_tried_then_dropped(monkeypatch):
    calls = _requests(monkeypatch, {
        ("/search/tv", (("first_air_date_year", 2024),)): _R({"results": [SILO_NAMESAKE]}),
        ("/search/tv", ()): _R({"results": [SILO_NAMESAKE, SILO]})})
    assert tm.search_verified(TMDBClient("k"), "show", "Silo", 2024) == (125988, "Mysteries")
    assert [c[1].get("first_air_date_year") for c in calls] == [2024, None]


def test_find_resolves_the_servers_tvdb_id_exactly(monkeypatch):
    calls = _requests(monkeypatch, {("/find/403245", ()): _R({"tv_results": [{"id": 125988}]})})
    assert tm.resolve(TMDBClient("k"), "show", "Silo", 2023, {"tvdb_id": 403245})[0] == 125988
    assert not any(c[0].startswith("/search") for c in calls), "no title search needed"


def test_a_404_tries_the_next_id_and_other_failures_raise(monkeypatch):
    _requests(monkeypatch, {("/find/1", ()): _R({}, 404),
                            ("/find/tt9", ()): _R({"tv_results": [{"id": 7}]})})
    assert tm.find_by_external_id(TMDBClient("k"), "show", {"tvdb_id": 1, "imdb_id": "tt9"})[0] == 7
    _requests(monkeypatch, {("/find/1", ()): _R({}, 429)})
    with pytest.raises(RuntimeError):
        tm.find_by_external_id(TMDBClient("k"), "show", {"tvdb_id": 1})


def test_upstreams_match_uses_the_ladder(monkeypatch):
    _requests(monkeypatch, {("/search/tv", ()): _R({"results": [SILO_NAMESAKE]})})
    assert TMDBClient("k").match("show", "Silo", 2023) is None, "refused, so it's retried later"


@pytest.fixture()
def db(tmp_path):
    return VideoDatabase(database_path=str(tmp_path / "video_library.db"))


def test_the_worker_hands_over_the_rows_ids(db):
    from core.video.enrichment.worker import VideoEnrichmentWorker
    db.upsert_show_tree("plex", {"server_id": "s", "title": "Silo", "year": 2023, "seasons": []})
    conn = sqlite3.connect(str(db.database_path))
    conn.execute("UPDATE shows SET tvdb_id=403245, imdb_id='tt14688458'")
    conn.commit()
    conn.close()
    seen = {}

    class Client:
        enabled = True

        def match(self, kind, title, year, known_id=None, external_ids=None):
            seen.update(external_ids or {})
            return None

    VideoEnrichmentWorker(db, "tmdb", Client()).process_one()
    assert seen == {"tvdb_id": 403245, "imdb_id": "tt14688458"}
