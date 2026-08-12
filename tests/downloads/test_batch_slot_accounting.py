"""A post-processed task always releases its batch slot, exactly once.

Reported as "music download replacements seem to get stuck in a Downloading
state". From the user's log, batch d725b522 (a 9-track album from the
auto-wishlist): three tracks failed the integrity check on duration and went
back for a replacement, and sixteen seconds after the first retry one worker
slot leaked and never came back —

    15:36:24  [Retry:integrity] Re-queuing a332cd71 … (attempt 1/5)
    15:36:40  [Worker Validation] Batch d725b522: reported=3, actual=2
    …          the same line every pass for 80 seconds …
    15:38:36  [Batch Healing] … all 9 task(s) finished but the batch never completed

A batch cannot complete until ``active_count`` reaches zero, so a slot reserved
for a task that finished without telling its batch hangs the whole batch. It
took the 1.9.11 healer 36 seconds to rescue that one.

The cause is structural rather than one bad line: the verification wrapper has a
dozen exits, four of which deliberately do NOT notify because the task is going
around again. Any exit that forgets which kind it is costs a wedged batch. So
the contract is now enforced instead of remembered — a hand-off is declared, and
anything else notifies exactly once.

The second half of the report is that nothing caught it: ``not in live
transfers`` appears ZERO times in 41,902 lines of that log. The stuck-detector
bailed out for any task with no username/filename, which is precisely the state
the replacement path creates — see the monitor tests at the bottom.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from core.imports import pipeline
from core.runtime_state import TERMINAL_TASK_STATUSES, download_tasks, tasks_lock


TASK = "task-slot-test"
BATCH = "batch-slot-test"
CTXKEY = "user_file.flac"


@pytest.fixture()
def notified():
    """Records every batch notification the wrapper makes."""
    calls = []
    yield calls


@pytest.fixture()
def runtime(notified):
    return SimpleNamespace(
        on_download_completed=lambda b, t, success=True: notified.append((b, t, success)),
        automation_engine=None, web_scan_manager=None, repair_worker=None,
    )


@pytest.fixture(autouse=True)
def _clean_task():
    with tasks_lock:
        download_tasks[TASK] = {"status": "downloading", "track_info": {"name": "T"}}
    yield
    with tasks_lock:
        download_tasks.pop(TASK, None)


def _run(monkeypatch, runtime, *, inner, requeue=False, fallback=False):
    """Drive the wrapper with the inner pipeline stubbed to a given outcome."""
    monkeypatch.setattr(pipeline, "post_process_matched_download",
                        lambda *a, **k: inner(a[1]))
    monkeypatch.setattr(pipeline, "_requeue_quarantined_task_for_retry",
                        lambda *a, **k: requeue)
    monkeypatch.setattr(pipeline, "_attempt_version_mismatch_fallback",
                        lambda *a, **k: fallback)
    return pipeline.post_process_matched_download_with_verification(
        CTXKEY, {"task_id": TASK, "batch_id": BATCH}, "/src/file.flac",
        TASK, BATCH, runtime,
    )


# ── the safety net ──────────────────────────────────────────────────────────

def test_an_unflagged_outcome_still_reaches_a_decision(monkeypatch, runtime, notified):
    """No flag and no final path: the wrapper's "cannot verify, assuming
    success" fall-through owns this one. Pinned because it is what makes the
    ordinary path safe — if it ever stops notifying, the safety net catches it,
    but the batch would be relying on a net instead of an outcome."""
    _run(monkeypatch, runtime, inner=lambda ctx: ctx.clear())
    assert notified == [(BATCH, TASK, True)]


def test_a_hard_kill_mid_post_processing_still_frees_the_slot(monkeypatch, runtime, notified):
    """The net, exercised for real. ``except Exception`` does not catch
    BaseException, so an interpreter shutdown or a killed worker thread used to
    unwind straight out of this function still holding the batch slot — and a
    slot that is never released is a batch that can never complete."""
    def inner(ctx):
        raise KeyboardInterrupt("worker thread killed")

    with pytest.raises(KeyboardInterrupt):
        _run(monkeypatch, runtime, inner=inner)
    assert notified == [(BATCH, TASK, False)], (
        "the slot must be released even when the function does not return normally"
    )
    assert download_tasks[TASK]["status"] in TERMINAL_TASK_STATUSES


def test_the_safety_net_does_not_fire_when_a_retry_was_queued(monkeypatch, runtime, notified):
    """The four hand-offs are legitimate — the task is going around again and
    the retry owns its outcome. Notifying here would free a slot the retry is
    still using and let the batch 'complete' with work outstanding."""
    def inner(ctx):
        ctx["_integrity_failure_msg"] = "Duration mismatch: file is 215.3s, expected 198.6s"
    _run(monkeypatch, runtime, inner=inner, requeue=True)
    assert notified == [], "a queued retry must not release the slot"


def test_the_safety_net_fires_when_the_retry_was_refused(monkeypatch, runtime, notified):
    """Same integrity failure, but retries are exhausted — now it is terminal."""
    def inner(ctx):
        ctx["_integrity_failure_msg"] = "Duration mismatch"
    _run(monkeypatch, runtime, inner=inner, requeue=False)
    assert notified == [(BATCH, TASK, False)]


def test_the_version_mismatch_fallback_is_a_hand_off(monkeypatch, runtime, notified):
    def inner(ctx):
        ctx["_acoustid_quarantined"] = True
    _run(monkeypatch, runtime, inner=inner, requeue=False, fallback=True)
    assert notified == [], "the re-dispatched fallback owns the outcome"


def test_the_quality_guard_is_a_hand_off(monkeypatch, runtime, notified):
    """The inner pipeline fully owns this one — it quarantines and then either
    re-queues or fails and notifies. Notifying here would double-count."""
    def inner(ctx):
        ctx["_bitdepth_rejected"] = True
    _run(monkeypatch, runtime, inner=inner)
    assert notified == []


# ── exactly once ────────────────────────────────────────────────────────────

def test_a_successful_import_notifies_exactly_once(monkeypatch, runtime, notified, tmp_path):
    final = tmp_path / "done.flac"
    final.write_bytes(b"x")

    def inner(ctx):
        ctx["_final_processed_path"] = str(final)
    _run(monkeypatch, runtime, inner=inner)
    assert notified == [(BATCH, TASK, True)]


def test_an_exception_notifies_once_not_twice(monkeypatch, runtime, notified):
    """The handler notifies, then the safety net runs. Two decrements for one
    task would under-count active_count and over-start workers."""
    def inner(ctx):
        raise RuntimeError("permission denied creating album folder")
    _run(monkeypatch, runtime, inner=inner)
    assert notified == [(BATCH, TASK, False)], "exactly one notification"


def test_the_safety_net_never_overwrites_a_recorded_outcome(monkeypatch, runtime, notified):
    """If a path already marked the task completed, the net must not relabel it
    failed on the way out."""
    def inner(ctx):
        with tasks_lock:
            download_tasks[TASK]["status"] = "completed"
        ctx.clear()
    _run(monkeypatch, runtime, inner=inner)
    assert download_tasks[TASK]["status"] == "completed"


# ── the watchdog that never fired ───────────────────────────────────────────

def test_a_task_with_no_source_is_still_watched():
    """`not in live transfers` appeared ZERO times in the reported log. The
    detector returned early for any task without username/filename — the exact
    state a replacement passes through, since the quarantine requeue clears both
    and a Spotify track_info has no filename to fall back on."""
    import time
    from core.downloads.monitor import WebUIDownloadMonitor

    mon = WebUIDownloadMonitor()
    now = time.time()
    task = {
        "status": "downloading",          # claims to be downloading …
        "track_info": {"name": "Harujion"},   # … with no source to download from
        "status_change_time": now - 600,
        "batch_id": BATCH,
    }
    deferred: list = []
    mon._should_retry_task(TASK, task, {}, now, deferred)

    assert task["status"] == "searching", "a sourceless stuck task must be retried"
    assert task["stuck_retry_count"] == 1
    assert ("restart_worker", TASK, BATCH) in deferred


def test_a_sourceless_task_inside_the_grace_window_is_left_alone():
    """Only >90s counts as stuck — a replacement mid-restart must not be yanked."""
    import time
    from core.downloads.monitor import WebUIDownloadMonitor

    mon = WebUIDownloadMonitor()
    now = time.time()
    task = {"status": "downloading", "track_info": {"name": "Harujion"},
            "status_change_time": now - 5, "batch_id": BATCH}
    deferred: list = []
    assert mon._should_retry_task(TASK, task, {}, now, deferred) is False
    assert task["status"] == "downloading"
    assert deferred == []
