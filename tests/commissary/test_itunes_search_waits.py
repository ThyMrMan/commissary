"""An iTunes search waits for the rate limit once, and only before a real request.

iTunes allows about 20 requests a minute, so ``rate_limited`` spaces requests
3 s apart. ``search_tracks`` and ``search_albums`` carried the decorator and
called ``_search``, which carries it too: every uncached search waited twice,
about 6 s, and a search the metadata cache could answer still waited up to 3 s
before looking. Playlist discovery runs one search after another, so a
first-time track cost roughly 7 s.
"""

from __future__ import annotations

import pytest

import core.itunes_client as ic


class _Clock:
    """Stands in for the module's ``time``: sleeping advances it, instantly."""

    def __init__(self, start=1000.0):
        self.now = start
        self.slept = []

    def time(self):
        return self.now

    def sleep(self, seconds):
        self.slept.append(seconds)
        self.now += seconds


class _Response:
    status_code = 200

    def __init__(self, results):
        self._results = results

    def json(self):
        return {"results": self._results}


class _Cache:
    """The metadata cache, holding the answer to every search -- or to none."""

    def __init__(self, cached=None):
        self.cached = cached

    def get_search_results(self, source, kind, query, limit):
        return self.cached

    def get_entity(self, *args, **kwargs):
        return None

    def store_entity(self, *args, **kwargs):
        pass

    def store_entities_bulk(self, *args, **kwargs):
        pass

    def store_search_results(self, *args, **kwargs):
        pass


RAW = {
    "search_tracks": {"wrapperType": "track", "kind": "song", "trackId": 11, "trackName": "Song",
                      "artistId": 22, "artistName": "Artist", "collectionName": "Album",
                      "trackTimeMillis": 180000},
    "search_albums": {"wrapperType": "collection", "collectionId": 33, "collectionName": "Album",
                      "artistId": 22, "artistName": "Artist", "trackCount": 10},
    "search_artists": {"wrapperType": "artist", "artistId": 22, "artistName": "Artist"},
}


@pytest.fixture
def clock(monkeypatch):
    clock = _Clock()
    monkeypatch.setattr(ic, "time", clock)
    # A request went out this instant, so the next one owes the whole interval.
    monkeypatch.setattr(ic, "_last_api_call_time", clock.now)
    return clock


def _client(monkeypatch, clock, cache, search):
    """A client whose requests are answered in memory and logged with the time
    each went out. Lookups (artist names) answer empty."""
    monkeypatch.setattr(ic, "get_metadata_cache", lambda: cache)
    client = ic.iTunesClient()
    sent = []

    class _Session:
        def get(self, url, params=None, timeout=None):
            sent.append((url, clock.now))
            return _Response([RAW[search]] if url == client.SEARCH_URL else [])

    client.session = _Session()
    return client, sent


@pytest.mark.parametrize("search", sorted(RAW))
def test_a_search_the_cache_answers_does_not_wait(monkeypatch, clock, search):
    client, sent = _client(monkeypatch, clock, _Cache(cached=[RAW[search]]), search)

    found = getattr(client, search)("query")

    assert len(found) == 1
    assert sent == []
    assert clock.slept == []


@pytest.mark.parametrize("search", sorted(RAW))
def test_a_new_search_waits_out_the_interval_once(monkeypatch, clock, search):
    client, sent = _client(monkeypatch, clock, _Cache(), search)

    found = getattr(client, search)("query")

    assert len(found) == 1
    assert [url for url, _ in sent if url == client.SEARCH_URL] == [client.SEARCH_URL]
    assert sum(clock.slept) == pytest.approx(ic.MIN_API_INTERVAL)


@pytest.mark.parametrize("search", sorted(RAW))
def test_back_to_back_searches_still_keep_the_interval(monkeypatch, clock, search):
    monkeypatch.setattr(ic, "_last_api_call_time", 0.0)
    client, sent = _client(monkeypatch, clock, _Cache(), search)

    getattr(client, search)("first query")
    getattr(client, search)("second query")

    first, second = [at for url, at in sent if url == client.SEARCH_URL]
    assert second - first >= ic.MIN_API_INTERVAL
