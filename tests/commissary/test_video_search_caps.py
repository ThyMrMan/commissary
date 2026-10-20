"""Manual-search caps are settings, defaulting to upstream's numbers (fork 1.8.6).

Upstream cut the ranked list to 40 accepted + 15 rejected rows and asked
Prowlarr for 100 results per call, all hard-coded — the ranker had more and the
response threw them away.
"""

from __future__ import annotations

import pytest

from core.commissary import search_caps
from database.video_database import VideoDatabase


class _Config:
    def __init__(self, values):
        self.values = values

    def get(self, key, default=None):
        return self.values.get(key, default)


@pytest.fixture
def config(monkeypatch):
    import core.settings as settings
    cfg = _Config({})
    monkeypatch.setattr(settings, "config_manager", cfg)
    return cfg


@pytest.fixture
def profile(tmp_path):
    from core.video.quality_profile import load as load_profile
    return load_profile(VideoDatabase(database_path=str(tmp_path / "v.db")))


def _hits(n, title):
    return [{"title": title, "username": "peer%d" % i, "filename": "f%d.mkv" % i,
             "size_bytes": 2 * 1024 ** 3, "availability": 5} for i in range(n)]


def _evaluate(profile, accepted=0, rejected=0):
    from api.video.downloads import _evaluate_hits
    raw = _hits(accepted, "Heat.1995.1080p.BluRay.x264") + \
        _hits(rejected, "Other.Film.2001.1080p.BluRay.x264")
    for i, h in enumerate(raw):
        h["filename"] = "f%d.mkv" % i
        h["username"] = "peer%d" % i
    out = _evaluate_hits(raw, profile, "movie", None, None, blocked=set(), blocked_users=set(),
                         want_title="Heat", want_year=1995)
    return [r for r in out if r["accepted"]], [r for r in out if not r["accepted"]]


def test_defaults_match_upstream(config):
    assert (search_caps.max_accepted(), search_caps.max_rejected(),
            search_caps.prowlarr_limit()) == (40, 15, 100)


def test_the_ranker_honours_the_defaults(config, profile):
    acc, rej = _evaluate(profile, accepted=60, rejected=30)
    assert (len(acc), len(rej)) == (40, 15)


def test_the_ranker_honours_configured_caps(config, profile):
    config.values.update({"video.search.max_results": 55, "video.search.max_rejected": 3})
    acc, rej = _evaluate(profile, accepted=60, rejected=30)
    assert (len(acc), len(rej)) == (55, 3)


@pytest.mark.parametrize("key,value,fn,want", [
    ("video.search.max_results", 0, "max_accepted", 40),        # 0 = unset -> default
    ("video.search.max_results", -5, "max_accepted", 1),
    ("video.search.max_results", 99999, "max_accepted", 500),
    ("video.search.max_rejected", 99999, "max_rejected", 200),
    ("video.search.max_results", "junk", "max_accepted", 40),
    ("prowlarr.search_limit", 5, "prowlarr_limit", 20),
    ("prowlarr.search_limit", 250, "prowlarr_limit", 250),
])
def test_values_are_clamped(config, key, value, fn, want):
    config.values[key] = value
    assert getattr(search_caps, fn)() == want


def test_prowlarr_is_asked_for_the_configured_limit(config, monkeypatch):
    import core.video.prowlarr_search as ps
    config.values["prowlarr.search_limit"] = 300
    limits = []

    class _Client:
        def is_configured(self):
            return True

        def _search_sync(self, q, cats, ids, limit, search_type="search", extra_params=None,
                         max_wait_seconds=None):
            limits.append(limit)
            return []

    monkeypatch.setattr(ps, "_client", lambda: _Client())
    monkeypatch.setattr(ps, "_indexer_ids", lambda: [])
    ps.prowlarr_search("movie", "X", year=2020, source="torrent")
    assert limits and set(limits) == {300}
