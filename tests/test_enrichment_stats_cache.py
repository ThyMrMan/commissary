"""Idle enrichment workers stop re-scanning the database every 2 seconds.

Adapted from upstream 3.2.0 (ade94dbf). The socket loop called get_stats() on
~18 workers every 2 seconds for as long as a tab was open, and get_stats() is
not a property read — it counts pending items and builds a progress breakdown,
whole-table aggregates on a fresh connection. Roughly a hundred scans per tick,
forever, with everything idle.

The risk in fixing it is showing the user stale numbers, so these pin the
freshness rules harder than the saving: a running worker is never cached, and a
state change refreshes immediately rather than waiting out the TTL.
"""

from __future__ import annotations

from core.enrichment.stats_cache import WorkerStatsCache


class FakeWorker:
    """A worker whose get_stats() is expensive — counted, so 'expensive' is
    something the tests can assert about."""

    def __init__(self, running=False, paused=False):
        self.running = running
        self.paused = paused
        self.calls = 0

    def get_stats(self):
        self.calls += 1
        return {"running": self.running, "paused": self.paused, "calls": self.calls}


def test_an_idle_worker_is_read_once_then_served_from_cache():
    w = FakeWorker(running=False)
    c = WorkerStatsCache(idle_ttl=30)
    for tick in range(15):                       # 15 ticks = 30s of a 2s loop
        c.get("mb", w, now=tick * 2.0)
    assert w.calls == 1, f"idle worker scanned {w.calls} times in 30s"


def test_the_idle_worker_is_re_read_once_the_ttl_elapses():
    w = FakeWorker(running=False)
    c = WorkerStatsCache(idle_ttl=30)
    c.get("mb", w, now=0.0)
    c.get("mb", w, now=29.9)
    assert w.calls == 1
    c.get("mb", w, now=30.0)
    assert w.calls == 2, "an idle worker must still refresh eventually"


def test_a_running_worker_is_never_cached():
    """Its numbers are moving — this is the case the display exists for."""
    w = FakeWorker(running=True)
    c = WorkerStatsCache(idle_ttl=30)
    for tick in range(10):
        c.get("mb", w, now=tick * 2.0)
    assert w.calls == 10


def test_starting_a_worker_refreshes_on_the_very_next_tick():
    """Not one tick of lag: 'running' is a plain attribute, so the cache can
    see the disagreement with its stored payload for free."""
    w = FakeWorker(running=False)
    c = WorkerStatsCache(idle_ttl=30)
    c.get("mb", w, now=0.0)
    assert w.calls == 1
    w.running = True                              # user hits Start
    out = c.get("mb", w, now=2.0)                 # the very next tick
    assert w.calls == 2
    assert out["running"] is True


def test_pausing_a_worker_refreshes_on_the_very_next_tick():
    w = FakeWorker(running=True)
    c = WorkerStatsCache(idle_ttl=30)
    c.get("mb", w, now=0.0)
    w.running, w.paused = False, True             # paused: idle for caching…
    before = w.calls
    out = c.get("mb", w, now=2.0)
    assert w.calls == before + 1, "the pause must be visible immediately"
    assert out["paused"] is True


def test_a_paused_worker_is_then_cached_like_any_other_idle_one():
    w = FakeWorker(running=False, paused=True)
    c = WorkerStatsCache(idle_ttl=30)
    for tick in range(10):
        c.get("mb", w, now=tick * 2.0)
    assert w.calls == 1


def test_workers_do_not_share_a_cache_entry():
    a, b = FakeWorker(), FakeWorker()
    c = WorkerStatsCache(idle_ttl=30)
    c.get("mb", a, now=0.0)
    c.get("discogs", b, now=0.0)
    assert a.calls == 1 and b.calls == 1


def test_invalidate_forces_the_next_read():
    """For anything that changes a worker out of band — a reset, a config
    change — so the UI does not wait out the TTL."""
    w = FakeWorker()
    c = WorkerStatsCache(idle_ttl=30)
    c.get("mb", w, now=0.0)
    c.invalidate("mb")
    c.get("mb", w, now=1.0)
    assert w.calls == 2
    c.get("mb", w, now=2.0)
    c.invalidate()                                 # all of them
    c.get("mb", w, now=3.0)
    assert w.calls == 3


def test_the_saving_is_what_it_claims():
    """18 workers over a 10-minute idle session on a 2s loop."""
    workers = [FakeWorker() for _ in range(18)]
    c = WorkerStatsCache(idle_ttl=30)
    for tick in range(300):                        # 300 ticks * 2s = 600s
        for i, w in enumerate(workers):
            c.get(f"w{i}", w, now=tick * 2.0)
    before = 18 * 300
    after = sum(w.calls for w in workers)
    assert after == 18 * 20, after                 # 600s / 30s TTL = 20 reads each
    assert after < before / 10, f"{before} -> {after} is not the promised cut"
