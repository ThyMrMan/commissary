"""A batch's worker slots are counted so the batch can always finish.

Ported from the fork (1.9.16, 2.1.1 b/c). Not ported: 2.1.1's task_is_active
on its own — it read 'pending' as busy and, once the healer shared it (2.3.4),
wedged every batch larger than its worker limit (2.3.7). Upstream 3.5.7's
validator and healer already agree on one status list.

1. ``active_count -= 1`` was unclamped, and the completion gates tested
   ``== 0``: once a count had been driven below zero the batch could never
   finish.
2. Post-processing has a dozen exits; one that neither notified the batch nor
   queued a retry leaked its slot and hung the whole album.
3. The stuck-download watchdog skipped any task with no username/filename —
   exactly the state a replacement passes through.
"""

from __future__ import annotations

import threading
import time
from types import SimpleNamespace

import pytest

from core.downloads import lifecycle as lc
from core.downloads.monitor import WebUIDownloadMonitor
from core.imports import pipeline
from core.runtime_state import download_batches, download_tasks, tasks_lock

TASK = "task-slot-test"
BATCH = "batch-slot-test"
CTXKEY = "user_file.flac"


@pytest.fixture(autouse=True)
def _clean_state(monkeypatch):
    monkeypatch.setattr(lc, '_scan_owed', False)
    download_tasks.clear()
    download_batches.clear()
    yield
    download_tasks.clear()
    download_batches.clear()


# ── lifecycle: clamp and <= 0 gates ─────────────────────────────────────────

class _Cfg:
    def get(self, key, default=None):
        return default


class _Monitor:
    def stop_monitoring(self, batch_id):
        pass


def _deps():
    noop = lambda *a, **k: None  # noqa: E731
    return lc.LifecycleDeps(
        config_manager=_Cfg(), automation_engine=None, download_monitor=_Monitor(),
        repair_worker=None, mb_worker=None, is_shutting_down=lambda: False,
        get_batch_lock=lambda bid: threading.Lock(),
        submit_download_track_worker=noop, submit_failed_to_wishlist=noop,
        submit_failed_to_wishlist_with_auto_completion=noop,
        process_failed_to_wishlist=noop,
        process_failed_to_wishlist_with_auto_completion=noop,
        ensure_wishlist_track_format=lambda t: t, get_track_artist_name=lambda t: 'A',
        check_and_remove_from_wishlist=noop, regenerate_batch_m3u=noop,
        youtube_playlist_states={}, tidal_discovery_states={},
        deezer_discovery_states={}, spotify_public_discovery_states={},
        get_global_max_concurrent=None,
    )


def _batch(active_count, n=2):
    ids = [f't{i}' for i in range(n)]
    for tid in ids:
        download_tasks[tid] = {'status': 'completed', 'track_info': {'name': tid}}
    download_batches['b1'] = {
        'queue': ids, 'queue_index': n, 'active_count': active_count,
        'max_concurrent': 3, 'permanently_failed_tracks': [], 'cancelled_tracks': set(),
        'playlist_name': 'PL',
    }
    return ids


def test_late_completion_after_a_corrected_count_does_not_go_negative():
    """The validator already corrected the count to 0; the last worker's
    completion arrives afterwards. Unclamped, the count went to -1 and the
    ``== 0`` gate never let the batch finish."""
    ids = _batch(active_count=0)
    lc.on_download_completed('b1', ids[-1], True, _deps())
    assert download_batches['b1']['active_count'] == 0
    assert download_batches['b1']['phase'] == 'complete'


def test_completion_gate_accepts_an_already_negative_count():
    """A count that drifted negative some other way must still let the batch
    finish — a duplicate completion skips the decrement and re-checks."""
    ids = _batch(active_count=-1)
    download_batches['b1']['_completed_task_ids'] = {ids[-1]}
    lc.on_download_completed('b1', ids[-1], True, _deps())
    assert download_batches['b1'].get('phase') == 'complete'


def test_v2_completion_check_accepts_an_already_negative_count():
    _batch(active_count=-1)
    assert lc.check_batch_completion_v2('b1', _deps()) is True
    assert download_batches['b1']['phase'] == 'complete'


# ── post-processing: exactly-once notify, declared hand-offs, safety net ────

@pytest.fixture()
def notified():
    return []


@pytest.fixture()
def runtime(notified):
    return SimpleNamespace(
        on_download_completed=lambda b, t, success=True: notified.append((b, t, success)),
        automation_engine=None, web_scan_manager=None, repair_worker=None,
    )


@pytest.fixture()
def task():
    with tasks_lock:
        download_tasks[TASK] = {"status": "post_processing", "track_info": {"name": "T"}}
    return download_tasks[TASK]


def _run(monkeypatch, runtime, *, inner, requeue=False, fallback=False):
    monkeypatch.setattr(pipeline, "post_process_matched_download",
                        lambda *a, **k: inner(a[1]))
    monkeypatch.setattr(pipeline, "_requeue_quarantined_task_for_retry",
                        lambda *a, **k: requeue)
    monkeypatch.setattr(pipeline, "_attempt_version_mismatch_fallback",
                        lambda *a, **k: fallback)
    monkeypatch.setattr(pipeline, "_record_failed_download_blocklist", lambda *a, **k: None)
    monkeypatch.setattr(pipeline, "import_profile_id", lambda *a, **k: None)
    return pipeline.post_process_matched_download_with_verification(
        CTXKEY, {"task_id": TASK, "batch_id": BATCH}, "/src/file.flac",
        TASK, BATCH, runtime,
    )


def test_a_hard_kill_mid_post_processing_still_frees_the_slot(monkeypatch, runtime, notified, task):
    """``except Exception`` does not catch BaseException: a killed worker
    thread unwound out of the wrapper still holding the batch slot."""
    def inner(ctx):
        raise KeyboardInterrupt("worker thread killed")

    with pytest.raises(KeyboardInterrupt):
        _run(monkeypatch, runtime, inner=inner)
    assert notified == [(BATCH, TASK, False)]
    assert task["status"] == "failed"


def test_an_exception_notifies_once_not_twice(monkeypatch, runtime, notified, task):
    def inner(ctx):
        raise RuntimeError("permission denied creating album folder")
    _run(monkeypatch, runtime, inner=inner)
    assert notified == [(BATCH, TASK, False)]


@pytest.mark.parametrize("flags,fallback", [
    ({"_integrity_failure_msg": "Duration mismatch"}, False),
    ({"_acoustid_quarantined": True}, False),
    ({"_bitdepth_rejected": True}, False),
])
def test_a_queued_retry_is_a_hand_off(monkeypatch, runtime, notified, task, flags, fallback):
    """The retry owns the outcome; notifying would free a slot still in use."""
    _run(monkeypatch, runtime, inner=lambda ctx: ctx.update(flags), requeue=True,
         fallback=fallback)
    assert notified == []
    assert task["status"] == "post_processing"


def test_the_version_mismatch_fallback_is_a_hand_off(monkeypatch, runtime, notified, task):
    _run(monkeypatch, runtime, inner=lambda ctx: ctx.update({"_acoustid_quarantined": True}),
         requeue=False, fallback=True)
    assert notified == []


def test_a_refused_retry_is_terminal_and_notifies_once(monkeypatch, runtime, notified, task):
    _run(monkeypatch, runtime, inner=lambda ctx: ctx.update({"_integrity_failure_msg": "x"}),
         requeue=False)
    assert notified == [(BATCH, TASK, False)]


def test_a_successful_import_notifies_exactly_once(monkeypatch, runtime, notified, task, tmp_path):
    final = tmp_path / "done.flac"
    final.write_bytes(b"x")
    _run(monkeypatch, runtime, inner=lambda ctx: ctx.update({"_final_processed_path": str(final)}))
    assert notified == [(BATCH, TASK, True)]


def test_the_safety_net_never_overwrites_a_recorded_outcome(monkeypatch, runtime, notified, task):
    def inner(ctx):
        task["status"] = "completed"
        raise KeyboardInterrupt
    with pytest.raises(KeyboardInterrupt):
        _run(monkeypatch, runtime, inner=inner)
    assert task["status"] == "completed"
    assert notified == [(BATCH, TASK, False)]


# ── the watchdog that never fired ───────────────────────────────────────────

def test_a_task_with_no_source_is_still_watched():
    """A replacement has its username/filename cleared by the requeue; the
    watchdog used to return early for it, so it sat on 'downloading' forever."""
    mon = WebUIDownloadMonitor()
    now = time.time()
    task = {"status": "downloading", "track_info": {"name": "Harujion"},
            "status_change_time": now - 600, "batch_id": BATCH}
    deferred: list = []
    mon._should_retry_task(TASK, task, {}, now, deferred)
    assert task["status"] == "searching"
    assert task["stuck_retry_count"] == 1
    assert ("restart_worker", TASK, BATCH) in deferred
    assert not any(op[0] in ("cancel_download", "cleanup_orphan") for op in deferred)


def test_a_sourceless_task_that_exhausted_its_retries_fails():
    mon = WebUIDownloadMonitor()
    now = time.time()
    task = {"status": "downloading", "track_info": {"name": "Harujion"},
            "status_change_time": now - 600, "batch_id": BATCH,
            "stuck_retry_count": 3, "last_retry_time": now - 60}
    assert mon._should_retry_task(TASK, task, {}, now, []) is True
    assert task["status"] == "failed"


def test_a_sourceless_task_inside_the_grace_window_is_left_alone():
    mon = WebUIDownloadMonitor()
    now = time.time()
    task = {"status": "downloading", "track_info": {"name": "Harujion"},
            "status_change_time": now - 5, "batch_id": BATCH}
    deferred: list = []
    assert mon._should_retry_task(TASK, task, {}, now, deferred) is False
    assert task["status"] == "downloading"
    assert deferred == []
