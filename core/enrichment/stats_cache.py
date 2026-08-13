"""Idle enrichment workers stop re-scanning the database every 2 seconds.

Adapted from upstream 3.2.0 (ade94dbf), the single largest steady-state cost
their audit found. The socket loop calls ``get_stats()`` on all ~18 workers
every 2 seconds for as long as any browser tab is open. Each call is not a
property read: it counts pending items and builds a progress breakdown — whole
table aggregates, on a fresh connection. That is roughly a hundred aggregate
scans per tick, forever, with every worker idle and nothing to do.

The UI heartbeat is not the problem and is left alone: the socket still emits
every 2 seconds. What changes is where the payload comes from.

  · a RUNNING worker is re-read every tick — its numbers are moving, and this
    is the case the display exists for;
  · an IDLE one is re-read at most every ``idle_ttl`` seconds and served from
    cache in between;
  · a transition can never show stale, not even for one tick: ``running`` and
    ``paused`` are plain attributes, so comparing them against the cached
    payload costs nothing and forces a refresh the instant they disagree.

Pure except for the ``get_stats`` call it is handed, so the policy is testable
without workers, sockets or a database.
"""

from __future__ import annotations

import time
from typing import Any, Callable, Dict, Optional

DEFAULT_IDLE_TTL = 30.0


class WorkerStatsCache:
    """Per-worker cache of the last ``get_stats()`` payload."""

    def __init__(self, idle_ttl: float = DEFAULT_IDLE_TTL):
        self.idle_ttl = float(idle_ttl)
        self._entries: Dict[str, Dict[str, Any]] = {}

    # ── the decision ────────────────────────────────────────────────────────
    def _is_stale(self, name: str, worker: Any, now: float) -> bool:
        entry = self._entries.get(name)
        if entry is None:
            return True                                  # never read
        payload = entry["payload"]

        # A worker that is doing something is re-read every tick.
        if bool(getattr(worker, "running", False)) and not bool(getattr(worker, "paused", False)):
            return True

        # Cheap in-memory attributes vs what the cached payload claims. Any
        # disagreement means the worker changed state since the last read, and
        # the display must not lag it — not even one tick.
        for attr in ("running", "paused"):
            if attr in payload and bool(payload[attr]) != bool(getattr(worker, attr, False)):
                return True

        return (now - entry["at"]) >= self.idle_ttl

    def get(self, name: str, worker: Any, get_stats: Optional[Callable[[], Any]] = None,
            now: Optional[float] = None) -> Any:
        """The worker's stats, fresh or cached per the policy above."""
        now = time.monotonic() if now is None else now
        if self._is_stale(name, worker, now):
            payload = (get_stats or worker.get_stats)()
            self._entries[name] = {"payload": payload, "at": now}
            return payload
        return self._entries[name]["payload"]

    def invalidate(self, name: Optional[str] = None) -> None:
        """Drop a worker's cached payload (or all of them). Call after anything
        that changes a worker out of band — a pause, a resume, a reset — so the
        next tick reads rather than waiting out the TTL."""
        if name is None:
            self._entries.clear()
        else:
            self._entries.pop(name, None)
