"""A batch bigger than its worker limit must keep dispatching.

From a live app.log, every wishlist album batch larger than three tracks stalled
after its first three:

    [Batch Lock] Starting workers for 14f49943…: active=0, max=3, queue_pos=0/98
    [Worker Validation] Batch 14f49943…: reported=3, actual=98, orphaned=0
    [Worker Validation] Fixed active count: 3 → 98
    [Batch Lock] Starting workers for 14f49943…: active=95, max=3, queue_pos=3/98

A worker is only started while ``active_count < max_concurrent``. The
worker-count validator, and the batch healer that shares its count, treated
every unfinished task in the queue as a busy slot -- and the 95 tasks still
waiting for a worker are 'pending', which is not a finished status. The count
said 98 slots were busy, nothing more was started, and the batch sat at 3/98
until a restart threw it away.

A slot is held by a task that has been DISPATCHED (``queue_index`` has moved
past it) and has not finished. These drive the real dispatcher, completion
callback and validator over the real task tables.
"""

from __future__ import annotations

import pytest

from core.downloads import lifecycle as lc
from core.downloads import monitor as monitor_mod
from core.runtime_state import download_batches, download_tasks
from tests.downloads.test_downloads_lifecycle import _build_deps

BATCH = "batch-dispatch-count-test"


@pytest.fixture(autouse=True)
def clean_tables():
    download_tasks.clear()
    download_batches.clear()
    yield
    download_tasks.clear()
    download_batches.clear()


def _make_batch(size, *, dispatched=0, running="searching", max_concurrent=3):
    """A batch of ``size`` tasks whose first ``dispatched`` have been handed to
    workers (in status ``running``); the rest wait as 'pending', the status
    every task is created with."""
    ids = ["task-%03d" % i for i in range(size)]
    for i, tid in enumerate(ids):
        download_tasks[tid] = {
            "status": running if i < dispatched else "pending",
            "track_info": {"name": "Track %d" % i, "artists": [{"name": "Artist"}]},
            "batch_id": BATCH,
            "track_index": i,
        }
    download_batches[BATCH] = {
        "queue": ids,
        "queue_index": dispatched,
        "active_count": dispatched,
        "max_concurrent": max_concurrent,
        "phase": "downloading",
        "_completed_task_ids": set(),
        "permanently_failed_tracks": [],
        "cancelled_tracks": set(),
    }
    return ids


# ── what holds a slot ──────────────────────────────────────────────────────
class TestWhatHoldsASlot:
    def test_a_task_still_waiting_for_a_worker_holds_none(self):
        from core.runtime_state import count_active_workers
        _make_batch(98, dispatched=3)
        assert count_active_workers(download_batches[BATCH], download_tasks) == 3

    @pytest.mark.parametrize("status", [
        "pending", "queued", "searching", "downloading", "post_processing",
        "a_status_nobody_has_written_yet"])
    def test_a_dispatched_unfinished_task_holds_one_whatever_its_status(self, status):
        """Once dispatched, the status never frees the slot -- task_is_active's
        safe direction still applies there."""
        from core.runtime_state import count_active_workers
        _make_batch(5, dispatched=2, running=status)
        assert count_active_workers(download_batches[BATCH], download_tasks) == 2

    def test_finished_called_back_or_vanished_tasks_hold_none(self):
        from core.runtime_state import count_active_workers
        ids = _make_batch(10, dispatched=4)
        download_tasks[ids[0]]["status"] = "completed"
        # Through the completion callback; its status has not caught up yet.
        download_batches[BATCH]["_completed_task_ids"].add(ids[1])
        del download_tasks[ids[2]]
        assert count_active_workers(download_batches[BATCH], download_tasks) == 1

    def test_the_boundary_is_the_queue_index(self):
        from core.runtime_state import count_active_workers
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


class TestTheValidator:
    def test_a_big_batch_keeps_its_true_count(self, validator):
        mon, started = validator
        _make_batch(98, dispatched=3)

        mon._validate_worker_counts()

        assert download_batches[BATCH]["active_count"] == 3
        assert started == []

    def test_a_batch_the_old_count_wedged_is_repaired(self, validator):
        """A live batch after the old count had run: 95 'busy' slots, two
        workers really running, the third finished."""
        mon, started = validator
        ids = _make_batch(98, dispatched=3)
        download_tasks[ids[0]]["status"] = "completed"
        download_batches[BATCH]["_completed_task_ids"].add(ids[0])
        download_batches[BATCH]["active_count"] = 95

        mon._validate_worker_counts()

        assert download_batches[BATCH]["active_count"] == 2
        assert started == [BATCH], "a free slot and 95 tracks waiting, and no worker started"


# ── end to end ─────────────────────────────────────────────────────────────
def test_every_track_of_a_big_batch_is_dispatched(monkeypatch):
    """The live arc: dispatch, the validator ticks, a worker finishes, repeat.
    The last track is left running so the batch never reaches completion."""
    dispatched = []
    deps, _recorder = _build_deps(
        submit_dl_worker=lambda task_id, batch_id: dispatched.append(task_id))
    monkeypatch.setattr(monitor_mod, "_start_next_batch_of_downloads",
                        lambda bid: lc.start_next_batch_of_downloads(bid, deps))
    mon = monitor_mod.WebUIDownloadMonitor()
    mon.monitored_batches = {BATCH}
    ids = _make_batch(12)

    lc.start_next_batch_of_downloads(BATCH, deps)
    assert dispatched == ids[:3]

    for _ in range(len(ids) - 1):
        mon._validate_worker_counts()
        running = [t for t in dispatched if download_tasks[t]["status"] == "searching"]
        if not running:
            break
        download_tasks[running[0]]["status"] = "completed"
        lc.on_download_completed(BATCH, running[0], True, deps)

    assert dispatched == ids, "stalled at %d of %d tracks" % (len(dispatched), len(ids))
