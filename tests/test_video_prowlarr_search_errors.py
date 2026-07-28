"""A search that never ran must not look like a search that found nothing.

Reported as: manual search returns no results; open Prowlarr's own UI, search
for the same show there, come back, and the results appear.

That is a timeout with a warm cache behind it. Prowlarr fans a cold search out
to every configured indexer and several authenticate on first use, so it
routinely ran past the client's 15s timeout — which was shared with metadata
calls. ``_api_get`` caught the timeout, returned None, ``_search_sync`` turned
that into ``[]``, and the picker rendered "No matching releases found". Once
Prowlarr had cached the query the same search answered instantly and worked.

Two changes: searches get their own much longer budget, and the video path asks
for errors to RAISE so the picker can say what actually happened. The second
matters more — a wrong answer delivered confidently is worse than a slow one.
"""

from __future__ import annotations

import pytest
import requests as http_requests

from core.prowlarr_client import ProwlarrClient, ProwlarrUnavailable


def _client(monkeypatch, behaviour):
    c = ProwlarrClient.__new__(ProwlarrClient)
    c._url, c._api_key = "http://prowlarr:9696", "k"
    monkeypatch.setattr(http_requests, "get", behaviour)
    return c


def _timeout(*a, **k):
    raise http_requests.exceptions.Timeout("timed out")


# ── the reported failure ─────────────────────────────────────────────────────
def test_a_timed_out_search_raises_instead_of_returning_nothing(monkeypatch):
    c = _client(monkeypatch, _timeout)
    with pytest.raises(ProwlarrUnavailable) as e:
        c._search_sync("Bleach", [5000], [], 100, strict=True)
    assert "timed out" in str(e.value)


def test_the_message_names_the_budget_and_the_setting(monkeypatch):
    """'It didn't work' is not actionable. The message has to say how long it
    waited and which knob changes that."""
    c = _client(monkeypatch, _timeout)
    with pytest.raises(ProwlarrUnavailable) as e:
        c._search_sync("Bleach", [5000], [], 100, strict=True, timeout=45)
    msg = str(e.value)
    assert "45" in msg and "prowlarr.search_timeout" in msg


def test_searches_get_a_longer_budget_than_metadata_calls():
    assert ProwlarrClient.DEFAULT_SEARCH_TIMEOUT > ProwlarrClient.DEFAULT_TIMEOUT
    assert ProwlarrClient.DEFAULT_SEARCH_TIMEOUT >= 60


def test_the_search_timeout_is_actually_applied(monkeypatch):
    seen = {}

    def _capture(url, **kw):
        seen["timeout"] = kw.get("timeout")
        raise http_requests.exceptions.Timeout("x")

    c = _client(monkeypatch, _capture)
    with pytest.raises(ProwlarrUnavailable):
        c._search_sync("q", [5000], [], 100, strict=True)
    assert seen["timeout"] == ProwlarrClient.DEFAULT_SEARCH_TIMEOUT


# ── the other failure modes ──────────────────────────────────────────────────
def test_a_transport_error_raises(monkeypatch):
    def _boom(*a, **k):
        raise http_requests.exceptions.ConnectionError("refused")

    with pytest.raises(ProwlarrUnavailable):
        _client(monkeypatch, _boom)._search_sync("q", [5000], [], 100, strict=True)


def test_a_bad_status_raises(monkeypatch):
    class _Resp:
        ok, status_code = False, 500

        @staticmethod
        def json():
            return {}

    with pytest.raises(ProwlarrUnavailable) as e:
        _client(monkeypatch, lambda *a, **k: _Resp())._search_sync(
            "q", [5000], [], 100, strict=True)
    assert "500" in str(e.value)


def test_an_unreadable_body_raises(monkeypatch):
    class _Resp:
        ok, status_code = True, 200

        @staticmethod
        def json():
            raise ValueError("not json")

    with pytest.raises(ProwlarrUnavailable):
        _client(monkeypatch, lambda *a, **k: _Resp())._search_sync(
            "q", [5000], [], 100, strict=True)


def test_an_unconfigured_client_raises_rather_than_looking_empty(monkeypatch):
    c = ProwlarrClient.__new__(ProwlarrClient)
    c._url, c._api_key = "", ""
    with pytest.raises(ProwlarrUnavailable):
        c._search_sync("q", [5000], [], 100, strict=True)


# ── a genuinely empty result is still empty ──────────────────────────────────
def test_no_releases_is_not_an_error(monkeypatch):
    """The distinction this whole change exists to draw."""
    class _Resp:
        ok, status_code = True, 200

        @staticmethod
        def json():
            return []

    assert _client(monkeypatch, lambda *a, **k: _Resp())._search_sync(
        "q", [5000], [], 100, strict=True) == []


# ── the music side must be untouched ─────────────────────────────────────────
def test_without_strict_a_failure_is_still_swallowed(monkeypatch):
    """Existing (music) callers pass no strict flag and must behave exactly as
    before — raising into them would turn a quiet degradation into a crash."""
    c = _client(monkeypatch, _timeout)
    assert c._search_sync("q", [3000], [], 100) == []
    assert c._api_get("indexer") is None


def test_metadata_calls_keep_the_short_timeout(monkeypatch):
    seen = {}

    def _capture(url, **kw):
        seen["timeout"] = kw.get("timeout")
        raise http_requests.exceptions.Timeout("x")

    _client(monkeypatch, _capture)._api_get("system/status")
    assert seen["timeout"] == ProwlarrClient.DEFAULT_TIMEOUT


# ── the video search surfaces it ─────────────────────────────────────────────
def test_the_video_search_reports_the_error_instead_of_an_empty_list(monkeypatch):
    from core.video import prowlarr_search as ps

    class _C:
        @staticmethod
        def is_configured():
            return True

        @staticmethod
        def _search_sync(*a, **k):
            raise ProwlarrUnavailable("the search timed out after 90s")

    monkeypatch.setattr(ps, "_client", lambda: _C())
    monkeypatch.setattr(ps, "_indexer_ids", lambda: [])
    res = ps.prowlarr_search("episode", "Bleach", season=1, episode=1)
    assert res["hits"] == []
    assert "timed out" in res["error"]        # the picker shows this


def test_the_video_search_still_returns_a_clean_empty_when_nothing_matched(monkeypatch):
    from core.video import prowlarr_search as ps

    class _C:
        @staticmethod
        def is_configured():
            return True

        @staticmethod
        def _search_sync(*a, **k):
            return []

    monkeypatch.setattr(ps, "_client", lambda: _C())
    monkeypatch.setattr(ps, "_indexer_ids", lambda: [])
    res = ps.prowlarr_search("episode", "Bleach", season=1, episode=1)
    assert res["hits"] == [] and not res.get("error")


def test_the_search_timeout_is_configurable_and_bounded(monkeypatch):
    from core.video import prowlarr_search as ps
    import config.settings as settings

    assert ps._search_timeout() == ps._DEFAULT_SEARCH_TIMEOUT

    class _Cfg:
        def __init__(self, v): self.v = v
        def get(self, key, default=None): return self.v

    monkeypatch.setattr(settings, "config_manager", _Cfg(150))
    assert ps._search_timeout() == 150
    monkeypatch.setattr(settings, "config_manager", _Cfg(1))
    assert ps._search_timeout() == 5.0        # nothing real finishes in 1s
    monkeypatch.setattr(settings, "config_manager", _Cfg(99999))
    assert ps._search_timeout() == 300.0
