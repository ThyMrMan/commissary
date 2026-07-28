"""TVDB's episode endpoint is paginated, and a truncated read looks like success.

/series/{id}/episodes/default returns the WHOLE series in aired order, and the
`season` parameter is not reliably applied server-side. Reading only page 0 means
that on a long-running show the later seasons come back empty — and an empty
season list is indistinguishable from "this season has no episodes", which is the
input that makes every caller quietly do nothing:

  * the episode cascade writes no rows,
  * the out-of-place clean-up skips the season entirely (a season it has no list
    for cannot be judged), so it reports nothing to do,

and both look like success. Bleach is 366 episodes plus a 50-episode revival, so
season 17 sits well past the first page.
"""

from __future__ import annotations

import pytest

from core.video.enrichment.clients import TVDBClient


class _Paged:
    """A TVDB that pages at 100 and ignores the `season` filter, the shape that
    truncation actually looks like."""

    def __init__(self, total=416, page_size=100, honour_season=False):
        self.page_size, self.honour_season = page_size, honour_season
        self.calls = []
        self.episodes = ([{"seasonNumber": 2, "number": n, "name": "2005 %d" % n}
                          for n in range(1, 22)] +
                         [{"seasonNumber": s, "number": n, "name": "mid"}
                          for s in range(3, 17) for n in range(1, 26)] +
                         [{"seasonNumber": 17, "number": n, "name": "TYBW %d" % n}
                          for n in range(1, 51)])

    def __call__(self, path, params):
        self.calls.append(params.get("page"))
        eps = self.episodes
        if self.honour_season:
            eps = [e for e in eps if e["seasonNumber"] == params.get("season")]
        start = (params.get("page") or 0) * self.page_size
        return {"data": {"episodes": eps[start:start + self.page_size]}}


def _client(fake):
    c = TVDBClient.__new__(TVDBClient)
    c.api_key = "k"
    c._authed_get = fake
    return c


def test_a_late_season_is_not_lost_to_paging():
    """Season 17 lives past page 0. Reading one page returned nothing for it."""
    fake = _Paged()
    got = _client(fake).season_episodes(74796, 17)
    assert [e["episode_number"] for e in got] == list(range(1, 51))
    assert len(fake.calls) > 1          # it actually paged


def test_an_early_season_still_works():
    got = _client(_Paged()).season_episodes(74796, 2)
    assert [e["episode_number"] for e in got] == list(range(1, 22))


def test_a_server_side_season_filter_is_still_honoured():
    """When TVDB DOES apply `season`, the walk must not double-count or spin."""
    fake = _Paged(honour_season=True)
    got = _client(fake).season_episodes(74796, 17)
    assert [e["episode_number"] for e in got] == list(range(1, 51))


def test_it_stops_on_a_short_page_rather_than_asking_forever():
    fake = _Paged()
    _client(fake).season_episodes(74796, 17)
    assert len(fake.calls) <= 6         # 416 episodes / 100 per page, then stop


def test_it_stops_on_an_empty_page():
    calls = []

    def fake(_path, params):
        calls.append(params.get("page"))
        return {"data": {"episodes": []}}

    assert _client(fake).season_episodes(1, 1) == []
    assert calls == [0]


def test_a_page_error_keeps_what_was_already_read():
    """A mid-walk failure must not throw away the episodes already collected —
    partial beats empty, because empty is what makes callers do nothing."""
    state = {"n": 0}

    def fake(_path, params):
        state["n"] += 1
        if state["n"] > 1:
            raise RuntimeError("tvdb blew up")
        return {"data": {"episodes": [{"seasonNumber": 1, "number": n} for n in range(1, 101)]}}

    got = _client(fake).season_episodes(1, 1)
    assert [e["episode_number"] for e in got] == list(range(1, 101))


def test_duplicates_across_pages_are_not_repeated():
    def fake(_path, params):
        if (params.get("page") or 0) > 1:
            return {"data": {"episodes": []}}
        return {"data": {"episodes": [{"seasonNumber": 1, "number": n} for n in range(1, 101)]}}

    got = _client(fake).season_episodes(1, 1)
    assert len(got) == 100


def test_a_missing_api_key_short_circuits():
    c = TVDBClient.__new__(TVDBClient)
    c.api_key = None
    assert c.season_episodes(1, 1) == []
