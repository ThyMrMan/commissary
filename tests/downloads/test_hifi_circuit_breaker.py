"""HiFi must stop re-dialling a pool it already knows is dead.

From a real 12-hour app.log: all seven public instances were down, and every
search walked the whole pool paying the full timeout on each — 4,094 "All HiFi
API instances exhausted" errors and roughly 23,500 warnings, which between them
were 47% of that log's errors and 80% of its warnings. One line shows sixteen
seconds burned on a single host AFTER the pool had already been reported
exhausted, because the exhaustion was per-call and nothing remembered it.

These instances are volunteer-run; whole-pool outages are normal and can last
hours. The breaker turns that from "pay the timeout, every search, forever" into
"pay it once, then skip".
"""

from __future__ import annotations

import threading
import time
import types

import pytest
import requests as http_requests

from core.hifi_client import HiFiClient

POOL = ["https://a", "https://b", "https://c", "https://d",
        "https://e", "https://f", "https://g"]


class _Session:
    """Counts attempts; every host fails unless named in ``healthy``."""

    def __init__(self, healthy=()):
        self.healthy = set(healthy)
        self.attempts = []

    def get(self, url, **kw):
        self.attempts.append(url)
        if any(url.startswith(h) for h in self.healthy):
            return types.SimpleNamespace(raise_for_status=lambda: None,
                                         json=lambda: {"ok": True})
        raise http_requests.exceptions.ConnectTimeout()


def _client(healthy=(), instances=None):
    c = HiFiClient.__new__(HiFiClient)
    c._instances = list(instances or POOL)
    c._instance_lock = threading.Lock()
    c._current_instance = c._instances[0]
    c._last_api_call = 0
    c._api_lock = threading.Lock()
    c._min_interval = 0
    c._breaker = {}
    c._breaker_lock = threading.Lock()
    c._pool_down_logged_until = 0.0
    c.session = _Session(healthy)
    return c


@pytest.fixture(autouse=True)
def _fast_timeout(monkeypatch):
    from config.settings import config_manager
    monkeypatch.setattr(config_manager, "get_source_search_timeout", lambda: 1,
                        raising=False)


# ── the behaviour that was costing the time ──────────────────────────────────
def test_a_dead_pool_is_walked_once_then_skipped():
    """THE regression. Before: every search re-tried all seven."""
    c = _client()
    assert c._api_get("/search") is None
    assert len(c.session.attempts) == len(POOL)      # learned it the hard way

    c.session.attempts.clear()
    for _ in range(10):
        assert c._api_get("/search") is None
    assert c.session.attempts == []                  # ...and never again


def test_one_dead_host_does_not_take_the_pool_down():
    """A single failure must cost that host, not the source."""
    c = _client(healthy=["https://b"])
    assert c._api_get("/search") == {"ok": True}
    assert c.breaker_status().keys() == {"https://a"}
    # the working host answers immediately from here on
    c.session.attempts.clear()
    assert c._api_get("/search") == {"ok": True}
    assert c.session.attempts == ["https://b/search"]


def test_a_success_clears_the_record_completely():
    """A host that works is not 'less broken' — it is working."""
    c = _client()
    c._api_get("/search")
    assert "https://a" in c.breaker_status()
    c.session.healthy = {"https://a"}
    c._note_success("https://a")
    assert "https://a" not in c.breaker_status()


def test_the_cooldown_grows_with_consecutive_failures():
    """A flapping instance backs off on its own instead of being re-dialled at
    the same rate forever."""
    c = _client()
    c._note_failure("https://a")
    first = c.breaker_status()["https://a"]
    c._note_failure("https://a")
    second = c.breaker_status()["https://a"]
    assert second > first
    for _ in range(20):
        c._note_failure("https://a")
    assert c.breaker_status()["https://a"] <= HiFiClient._BREAKER_MAX_COOLDOWN


def test_a_cooled_down_host_gets_one_probe_and_can_recover():
    """Half-open: the pool must not be locked out once it comes back."""
    c = _client()
    c._api_get("/search")
    with c._breaker_lock:                       # jump to the end of the cooldown
        for entry in c._breaker.values():
            entry["until"] = 0
    c.session = _Session(healthy=["https://a"])
    assert c._api_get("/search") == {"ok": True}
    assert c.breaker_status() == {}             # fully recovered, no residue


def test_the_pool_outage_is_logged_once_not_once_per_call(caplog):
    """4,094 identical lines is what buried the one that explained it."""
    import logging
    c = _client()
    c._api_get("/search")                       # opens every breaker
    caplog.set_level(logging.WARNING, logger="soulsync.hifi_client")
    caplog.clear()
    for _ in range(25):
        c._api_get("/search")
    pool_lines = [r for r in caplog.records
                  if "All HiFi instances are failing" in r.getMessage()]
    assert len(pool_lines) == 1


def test_editing_the_instance_list_clears_the_cooldowns(monkeypatch):
    """Adding a host or hitting Restore Defaults is a deliberate intervention;
    holding old cooldowns would make it look like it did nothing."""
    c = _client()
    c._api_get("/search")
    assert c.breaker_status()
    monkeypatch.setattr(c, "_load_instances_from_db",
                        lambda: setattr(c, "_instances", list(POOL)))
    c.reload_instances()
    assert c.breaker_status() == {}


# ── it must not change what happens when things work ─────────────────────────
def test_a_healthy_pool_is_untouched():
    c = _client(healthy=POOL)
    for _ in range(5):
        assert c._api_get("/search") == {"ok": True}
    assert len(c.session.attempts) == 5          # one call each, no rotation
    assert c.breaker_status() == {}


def test_a_request_level_error_still_fails_fast_without_burning_the_pool():
    """400/404 describe the REQUEST — no instance answers differently, and that
    predates the breaker. It must not now be recorded as an instance fault."""
    c = _client()

    def _bad_request(url, **kw):
        c.session.attempts.append(url)
        resp = types.SimpleNamespace(status_code=404)
        raise http_requests.exceptions.HTTPError(response=resp)

    c.session.get = _bad_request
    assert c._api_get("/search") is None
    assert len(c.session.attempts) == 1          # asked once, gave up
    assert c.breaker_status() == {}              # ...and blamed no instance


def test_an_empty_instance_list_is_not_a_cooling_pool():
    """No instances configured is a different state from all of them failing —
    it must not report a cooldown that will never elapse."""
    c = _client(instances=[])
    c._current_instance = None
    assert c._pool_cooling() is False
    assert c._api_get("/search") is None
