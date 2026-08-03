"""Run a manual import placement off the request thread.

Placement copies the file into the library, and the importer's own comment puts
that at "minutes over SMB" for a multi-GB release. Doing it inside the HTTP
request means any proxy timeout, dropped connection or browser abort surfaces as
a failure — while the server, which does not stop when the client goes away,
finishes the copy successfully. Reported as "couldn't place the file, but the
import goes fine".

So the work runs here instead, keyed by download id, and the endpoint polls.
Deliberately small: one placement per download at a time, no queue, no
cancellation. A placement is user-initiated and short-lived; anything more would
be a job system, and there is already one of those for the library-wide jobs.

State is in-process, exactly like the sibling ``mass_rename`` preview. A restart
loses it, which is fine: the DB row is the durable record of what happened, and
the endpoint reads that first.
"""

from __future__ import annotations

import threading
import time
from typing import Any, Callable, Dict, Optional

from utils.logging_config import get_logger

logger = get_logger("video.manual_place")

_lock = threading.Lock()
_jobs: Dict[int, dict] = {}

# Placements finished within this long are answered in the original request, so
# the common case (a small file, a same-filesystem move) keeps the synchronous
# response every existing caller expects and never sees a poll.
DEFAULT_GRACE_SECONDS = 8.0

# A finished job is kept this long so a client that reconnects after a dropped
# request can still collect the result rather than being told "no such job".
_RESULT_TTL = 900.0
_MAX_FINISHED = 64


def _prune(now: float) -> None:
    """Drop finished jobs that nobody came back for. Caller holds the lock."""
    done = [(j.get("finished_at") or 0.0, k) for k, j in _jobs.items() if not j.get("running")]
    for finished_at, key in done:
        if now - finished_at > _RESULT_TTL:
            _jobs.pop(key, None)
    if len(_jobs) > _MAX_FINISHED:
        for _ts, key in sorted(done)[:len(_jobs) - _MAX_FINISHED]:
            _jobs.pop(key, None)


def state(dl_id: Any) -> Optional[dict]:
    """Snapshot for a download's placement, or None if nothing is known."""
    try:
        key = int(dl_id)
    except (TypeError, ValueError):
        return None
    with _lock:
        job = _jobs.get(key)
        return dict(job) if job else None


def is_running(dl_id: Any) -> bool:
    job = state(dl_id)
    return bool(job and job.get("running"))


def forget(dl_id: Any) -> None:
    try:
        key = int(dl_id)
    except (TypeError, ValueError):
        return
    with _lock:
        _jobs.pop(key, None)


def start(dl_id: Any, work: Callable[[], dict]) -> dict:
    """Run ``work()`` on a worker thread for this download.

    Re-starting one already in flight is a no-op that returns the running job —
    a double-click, or a client retrying after its request timed out, must never
    start a second copy of the same file on top of the first.
    """
    key = int(dl_id)
    now = time.time()
    with _lock:
        _prune(now)
        existing = _jobs.get(key)
        if existing and existing.get("running"):
            return dict(existing)
        job = {"running": True, "result": None, "error": None,
               "started_at": now, "finished_at": None}
        _jobs[key] = job

    def _run() -> None:
        result, error = None, None
        try:
            result = work()
        except Exception as exc:      # noqa: BLE001 - recorded as the job's outcome
            logger.exception("manual placement %s failed", key)
            error = str(exc) or exc.__class__.__name__
        with _lock:
            entry = _jobs.get(key)
            if entry is not None:
                entry.update(running=False, result=result, error=error,
                             finished_at=time.time())

    threading.Thread(target=_run, name="video-place-%s" % key, daemon=True).start()
    return dict(job)


def wait(dl_id: Any, timeout: float) -> dict:
    """Block up to ``timeout`` for a placement to finish, then snapshot it.

    Polls rather than using an Event because the caller only needs "is it done
    yet" at human granularity, and this keeps the job record a plain dict that
    ``state`` can hand out without copying synchronisation primitives.
    """
    deadline = time.monotonic() + max(0.0, float(timeout))
    while True:
        snap = state(dl_id) or {}
        if not snap.get("running"):
            return snap
        if time.monotonic() >= deadline:
            return snap
        time.sleep(0.1)
