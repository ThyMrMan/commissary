"""A batch's busy worker slots are its DISPATCHED, unfinished tasks (fork 2.3.7).

Upstream's worker-count validator (downloads/monitor.py) and batch healer
(web_server.py) each count statuses ('searching', 'downloading', 'queued',
'post_processing') and leave out 'pending' — the status a task still has when
a worker has just been handed it. That worker's slot read as free, the count
was "fixed" down, and another worker was started (fork 2.1.1 a).

Fork history says the fix is NOT "count every unfinished task" (2.1.1's
task_is_active): that also counts the tasks still waiting for a worker, and
once both counters shared it every batch bigger than its worker limit stalled
at 3/N (2.3.4 → 2.3.7). The rule is: dispatched (before queue_index) and not
finished, one definition for both counters. These drive the real validator,
healer, dispatcher and completion callback.
"""

from __future__ import annotations

import pytest

from core.commissary.batch_slots import count_active_workers
from core.downloads import lifecycle as lc
from core.downloads import monitor as monitor_mod
from core.runtime_state import download_batches, download_tasks
from tests.downloads.test_downloads_lifecycle import _build_deps

BATCH = "batch-dispatch-count-test"


@pytest.fixture(autouse=True)
def clean_tables(monkeypatch):
    monkeypatch.setattr(lc, '_scan_owed', False)
    saved_tasks, saved_batches = dict(download_tasks), dict(download_batches)
    download_tasks.clear()
    download_batches.clear()
    yield
    download_tasks.clear()
    download_batches.clear()
    download_tasks.update(saved_tasks)
    download_batches.update(saved_batches)


def _make_batch(size, *, dispatched=0, running="searching", max_concurrent=3):
    """``size`` tasks, the first ``dispatched`` handed to workers (status
    ``running``); the rest wait as 'pending'."""
    ids = ["task-%03d" % i for i in range(size)]
    for i, tid in enumerate(ids):
        download_tasks[tid] = {
            "status": running if i < dispatched else "pending",
            "track_info": {"name": "Track %d" % i, "artists": [{"name": "Artist"}]},
            "batch_id": BATCH, "track_index": i,
        }
    download_batches[BATCH] = {
        "queue": ids, "queue_index": dispatched, "active_count": dispatched,
        "max_concurrent": max_concurrent, "phase": "downloading",
        "_completed_task_ids": set(), "permanently_failed_tracks": [],
        "cancelled_tracks": set(),
    }
    return ids


# ── what holds a slot ──────────────────────────────────────────────────────

def test_a_task_still_waiting_for_a_worker_holds_none():
    _make_batch(98, dispatched=3)
    assert count_active_workers(download_batches[BATCH], download_tasks) == 3


@pytest.mark.parametrize("status", [
    "pending", "queued", "searching", "downloading", "post_processing", "a_new_status"])
def test_a_dispatched_unfinished_task_holds_one_whatever_its_status(status):
    _make_batch(5, dispatched=2, running=status)
    assert count_active_workers(download_batches[BATCH], download_tasks) == 2


def test_finished_called_back_or_vanished_tasks_hold_none():
    ids = _make_batch(10, dispatched=4)
    download_tasks[ids[0]]["status"] = "completed"
    download_batches[BATCH]["_completed_task_ids"].add(ids[1])  # status not caught up yet
    del download_tasks[ids[2]]
    assert count_active_workers(download_batches[BATCH], download_tasks) == 1


def test_the_boundary_is_the_queue_index():
    _make_batch(6, dispatched=3)
    batch = download_batches[BATCH]
    for queue_index, expected in ((0, 0), (1, 1), (3, 3), (6, 6), (9, 6)):
        batch["queue_index"] = queue_index
        assert count_active_workers(batch, download_tasks) == expected, queue_index


# ── the worker-count validator ─────────────────────────────────────────────

@pytest.fixture
def validator(monkeypatch):
    started = []
    monkeypatch.setattr(monitor_mod, "_start_next_batch_of_downloads",
                        lambda bid: started.append(bid))
    mon = monitor_mod.WebUIDownloadMonitor()
    mon.monitored_batches = {BATCH}
    return mon, started


def test_validator_keeps_a_just_dispatched_pending_worker_busy(validator):
    """The 2.1.1(a) bug upstream still had: three workers handed their tasks,
    which are still 'pending' — upstream counted 0 and started more."""
    mon, started = validator
    _make_batch(10, dispatched=3, running="pending")
    mon._validate_worker_counts()
    assert download_batches[BATCH]["active_count"] == 3
    assert started == []


def test_validator_does_not_count_tasks_waiting_for_a_worker(validator):
    """The 2.3.7 wedge: counting every unfinished task read 98 busy slots."""
    mon, started = validator
    _make_batch(98, dispatched=3)
    mon._validate_worker_counts()
    assert download_batches[BATCH]["active_count"] == 3
    assert started == []


def test_validator_repairs_an_inflated_count_and_starts_a_worker(validator):
    mon, started = validator
    ids = _make_batch(98, dispatched=3)
    download_tasks[ids[0]]["status"] = "completed"
    download_batches[BATCH]["_completed_task_ids"].add(ids[0])
    download_batches[BATCH]["active_count"] = 95
    mon._validate_worker_counts()
    assert download_batches[BATCH]["active_count"] == 2
    assert started == [BATCH]


# ── the batch healer (the real web_server function) ────────────────────────

@pytest.fixture
def healer(monkeypatch):
    import web_server
    started, checks = [], []
    monkeypatch.setattr(web_server, "_start_next_batch_of_downloads", lambda bid: started.append(bid))
    monkeypatch.setattr(web_server, "_check_batch_completion_v2", lambda bid: checks.append(bid))
    return web_server, started


def test_healer_keeps_a_just_dispatched_pending_worker_busy(healer):
    web_server, started = healer
    _make_batch(10, dispatched=3, running="pending")
    web_server.validate_and_heal_batch_states()
    assert download_batches[BATCH]["active_count"] == 3
    assert started == []


def test_healer_does_not_count_tasks_waiting_for_a_worker(healer):
    web_server, started = healer
    _make_batch(98, dispatched=3)
    web_server.validate_and_heal_batch_states()
    assert download_batches[BATCH]["active_count"] == 3
    assert started == []


def test_validator_and_healer_agree(validator, healer):
    """Both ticks on the same batch must settle on one number, not fight."""
    mon, _ = validator
    web_server, _ = healer
    ids = _make_batch(20, dispatched=3, running="pending")
    download_tasks[ids[1]]["status"] = "downloading"
    for _ in range(3):
        mon._validate_worker_counts()
        a = download_batches[BATCH]["active_count"]
        web_server.validate_and_heal_batch_states()
        b = download_batches[BATCH]["active_count"]
        assert a == b == 3


# ── end to end ─────────────────────────────────────────────────────────────

def test_every_track_of_a_big_batch_is_dispatched(monkeypatch):
    """Dispatch, the validator ticks, a worker finishes, repeat. Workers stay
    'pending' until the validator has seen them, as a freshly submitted worker
    does. The last track is left running so the batch never completes."""
    dispatched = []
    deps, _ = _build_deps(submit_dl_worker=lambda task_id, batch_id: dispatched.append(task_id))
    monkeypatch.setattr(monitor_mod, "_start_next_batch_of_downloads",
                        lambda bid: lc.start_next_batch_of_downloads(bid, deps))
    mon = monitor_mod.WebUIDownloadMonitor()
    mon.monitored_batches = {BATCH}
    ids = _make_batch(12)

    lc.start_next_batch_of_downloads(BATCH, deps)
    assert dispatched == ids[:3]

    for _ in range(len(ids) * 2):
        for t in dispatched:                      # just-submitted workers: still pending
            if download_tasks[t]["status"] == "searching":
                download_tasks[t]["status"] = "pending"
        mon._validate_worker_counts()
        assert len(dispatched) - sum(
            1 for t in dispatched if download_tasks[t]["status"] == "completed") <= 3, \
            "more workers running than max_concurrent"
        running = [t for t in dispatched if download_tasks[t]["status"] != "completed"]
        if len(running) <= 1 and len(dispatched) == len(ids):
            break
        download_tasks[running[0]]["status"] = "completed"
        lc.on_download_completed(BATCH, running[0], True, deps)

    assert dispatched == ids, "stalled at %d of %d tracks" % (len(dispatched), len(ids))
