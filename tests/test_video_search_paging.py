"""In-app search reads several TMDB pages, and understands a typed year.

Reported: "Another World (2025)" (tmdb 1470329) could not be found at all, and
adding the year made it worse — zero results instead of the wrong ones.

Two separate causes:

1. /search/multi was read ONE page deep. TMDB returns 20 results a page ordered
   by popularity, so any title sharing its name with something more popular was
   simply unreachable — a 2025 indie called "Another World" sits far below a
   long-running soap of the same name. (The old `[:32]` slice was dead code: a
   page never holds more than 20.)

2. /search/multi has NO year parameter. A typed year went to TMDB as part of the
   TITLE, so "Another World 2025" matched nothing — the query that should narrow
   the search was the one guaranteed to fail.
"""

from __future__ import annotations

import pytest

from core.video.enrichment.clients import TMDBClient


# ── the year in the query ────────────────────────────────────────────────────
@pytest.mark.parametrize("query,expected", [
    ("Another World 2025", ("Another World", 2025)),
    ("Another World (2025)", ("Another World", 2025)),
    ("Dune [2021]", ("Dune", 2021)),
    ("  The Matrix   1999  ", ("The Matrix", 1999)),
])
def test_a_trailing_year_is_split_off(query, expected):
    assert TMDBClient.split_search_year(query) == expected


@pytest.mark.parametrize("query", [
    "1917",             # the film — a bare number IS the title
    "2012",
    "Another World",
    "",
])
def test_titles_that_are_just_a_year_are_left_alone(query):
    term, year = TMDBClient.split_search_year(query)
    assert year is None and term == query.strip()


def test_a_number_outside_the_plausible_range_is_not_a_year():
    assert TMDBClient.split_search_year("Space 1889")[1] is None
    assert TMDBClient.split_search_year("Rush 2112")[1] is None


def test_a_title_that_genuinely_ends_in_a_year_is_never_damaged():
    """'Blade Runner 2049' DOES parse as title+year — the split alone cannot tell
    it apart from 'Another World 2025'. What protects it is that the split is only
    consulted when the query as typed returned nothing, which for a real title it
    never does. Pinned in the paging tests below."""
    assert TMDBClient.split_search_year("Blade Runner 2049") == ("Blade Runner", 2049)


# ── paging ───────────────────────────────────────────────────────────────────
class _Resp:
    def __init__(self, payload):
        self._p = payload

    def raise_for_status(self):
        pass

    def json(self):
        return self._p


def _client(monkeypatch, pages, by_term=None):
    """A TMDB client whose multi-search returns the given {page: [results]}.

    ``by_term`` instead maps {query: {page: [results]}} so a test can make the
    query-as-typed come back empty and the year-stripped retry succeed.

    requests is imported INSIDE the method, so the module attribute doesn't exist
    to patch — patch requests.get itself.
    """
    import requests
    c = TMDBClient.__new__(TMDBClient)
    c.api_key = "k"
    calls = []

    def fake_get(url, params=None, timeout=None, **kw):
        params = params or {}
        page = int(params.get("page") or 1)
        term = params.get("query")
        calls.append({"query": term, "page": page})
        table = (by_term or {}).get(term, {}) if by_term is not None else pages
        return _Resp({"page": page, "total_pages": max(1, len(table)),
                      "results": table.get(page, [])})
    monkeypatch.setattr(requests, "get", fake_get)
    return c, calls


def _movie(tid, title, year):
    return {"media_type": "movie", "id": tid, "title": title,
            "release_date": "%d-01-01" % year}


def test_results_beyond_the_first_page_are_reachable(monkeypatch):
    """The whole bug: the wanted title sat on page 3."""
    pages = {
        1: [_movie(1, "Another World", 1999)],
        2: [_movie(2, "Another World", 2011)],
        3: [_movie(1470329, "Another World", 2025)],
    }
    c, calls = _client(monkeypatch, pages)
    got = c.search("Another World")
    assert [r["tmdb_id"] for r in got] == [1, 2, 1470329]
    assert [x["page"] for x in calls] == [1, 2, 3]


def test_a_year_query_that_returns_nothing_retries_without_it(monkeypatch):
    """The reported case: "Another World 2025" returned ZERO, because
    /search/multi has no year param and got the year as part of the title."""
    c, calls = _client(monkeypatch, None, by_term={
        "Another World 2025": {},                                   # as typed → nothing
        "Another World": {1: [_movie(1, "Another World", 1999)],
                          2: [_movie(1470329, "Another World", 2025)]},
    })
    got = c.search("Another World 2025")
    assert [x["query"] for x in calls][0] == "Another World 2025"    # tried as typed first
    assert "Another World" in [x["query"] for x in calls]            # then retried
    assert got[0]["tmdb_id"] == 1470329                              # 2025 floated to the top


def test_a_real_title_ending_in_a_year_is_left_exactly_as_typed(monkeypatch):
    """'Blade Runner 2049' parses as title+year, so a naive strip would search
    'Blade Runner' and bury the film under the 1982 original. Because the strip
    only happens when the typed query found NOTHING, it never runs here."""
    c, calls = _client(monkeypatch, None, by_term={
        "Blade Runner 2049": {1: [_movie(335984, "Blade Runner 2049", 2017)]},
        "Blade Runner": {1: [_movie(78, "Blade Runner", 1982)]},
    })
    got = c.search("Blade Runner 2049")
    assert [x["query"] for x in calls] == ["Blade Runner 2049"]      # no retry at all
    assert got[0]["tmdb_id"] == 335984


def test_the_retry_ranks_but_never_filters(monkeypatch):
    """A wrong year must not empty the results — it is a hint about which of
    several same-named titles was meant, not a filter."""
    c, _calls = _client(monkeypatch, None, by_term={
        "Another World 2077": {},
        "Another World": {1: [_movie(1, "Another World", 1999)]},
    })
    assert [r["tmdb_id"] for r in c.search("Another World 2077")] == [1]


def test_paging_stops_at_the_last_page(monkeypatch):
    """No pointless requests past total_pages."""
    c, calls = _client(monkeypatch, {1: [_movie(1, "Solo", 2018)]})
    c.search("Solo")
    assert [x["page"] for x in calls] == [1]


def test_duplicates_across_pages_are_dropped(monkeypatch):
    c, _calls = _client(monkeypatch, {1: [_movie(7, "Dupe", 2020)],
                                      2: [_movie(7, "Dupe", 2020)]})
    assert len(c.search("Dupe")) == 1


def test_a_failing_later_page_keeps_the_earlier_results(monkeypatch):
    """Page 1 succeeding then page 2 erroring must degrade, not lose everything."""
    c = TMDBClient.__new__(TMDBClient)
    c.api_key = "k"

    def flaky(url, params=None, timeout=None, **kw):
        if int((params or {}).get("page") or 1) == 1:
            return _Resp({"page": 1, "total_pages": 3, "results": [_movie(1, "A", 2020)]})
        raise OSError("network")
    import requests
    monkeypatch.setattr(requests, "get", flaky)
    assert [r["tmdb_id"] for r in c.search("A")] == [1]


def test_no_api_key_or_empty_query_short_circuits(monkeypatch):
    c, _calls = _client(monkeypatch, {1: [_movie(1, "X", 2020)]})
    assert c.search("") == []
    c.api_key = ""
    assert c.search("X") == []
