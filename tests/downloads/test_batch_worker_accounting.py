"""A batch that miscounts its own workers and can never finish.

Reported again as "hit download on some songs that needed to be replaced and
manually grabbed from Deezer, but it has gotten stuck on downloading" — the same
SYMPTOM as ``test_batch_slot_accounting`` and a completely different cause. That
one was a wrapper exit that forgot to release its slot; this one is the batch
miscounting slots that were never leaked.

From the user's log, five batches out of five, identical arc every time:

    03:10:39  [Worker Validation] Batch 2b17224d: reported=3, actual=2
    03:10:39  [Worker Validation] Fixed active count: 3 → 2
    03:10:39  [Worker Validation] Starting replacement workers for 2b17224d
    …the same three lines every 2-4 seconds for two minutes…
    03:12:40  [Worker Validation] Fixed active count: 1 → 0
    03:12:51  [Batch Healing] Batch 2b17224d looks stuck — all 18 task(s)
              finished but the batch never completed

Three defects, stacked:

1. TWO lists of "still working" statuses existed and disagreed. The authoritative
   one in downloads/master.py included 'pending'; the worker-count validator's
   copy did not. 'pending' is the status every download task is CREATED with, so
   it is the status of every worker the validator had just started. Such a task
   matched neither the active branch nor the orphaned branch — it was invisible.

   So the validator "corrected" a count that was right, freeing a slot still in
   use, then saw a free slot and started a replacement whose task was also
   pending. Round and round, consuming a queue entry every pass.

2. ``active_count -= 1`` was unclamped, so once the count had been driven below
   the number of live workers, their completion decrements took it NEGATIVE.

3. The completion gate tested ``active_count == 0``. A negative number never
   equals zero, so the batch could never be allowed to finish.

Any one of the three breaks the deadlock. All three together are why it
reproduced on every batch.

There was also nothing to see it with: downloads/lifecycle.py and
downloads/master.py used ``logging.getLogger(__name__)``, outside the
``soulsync`` namespace the file handler is attached to. 2.7 MB of app.log
contained ZERO lines from the module that decides whether a batch completes.
"""

from __future__ import annotations

import pathlib

import pytest

from core.downloads import monitor as monitor_mod
from core.runtime_state import (
    TERMINAL_TASK_STATUSES,
    download_batches,
    download_tasks,
    task_is_active,
)

_SRC = pathlib.Path(__file__).resolve().parents[2]
BATCH = "batch-accounting-test"


# ── the predicate ───────────────────────────────────────────────────────────

class TestWhatCountsAsStillWorking:
    @pytest.mark.parametrize("status", [
        "pending", "queued", "searching", "downloading", "post_processing"])
    def test_live_statuses_hold_a_slot(self, status):
        assert task_is_active(status) is True

    def test_pending_above_all(self):
        """THE bug. A freshly started worker's task is 'pending', so a validator
        blind to it is blind to exactly the work it just created."""
        assert task_is_active("pending") is True

    @pytest.mark.parametrize("status", sorted(TERMINAL_TASK_STATUSES))
    def test_terminal_statuses_release_it(self, status):
        assert task_is_active(status) is False

    def test_an_unknown_status_reads_as_still_working(self):
        """Fails in the safe direction: a batch waits rather than declaring a
        slot free and spawning a phantom worker for it."""
        assert task_is_active("some_status_nobody_has_written_yet") is True

    def test_but_no_status_at_all_is_not_working(self):
        """A task absent from download_tasks holds no slot — and
        _wait_for_batch_drain would otherwise block on something that does not
        exist, up to its one-hour cap."""
        assert task_is_active(None) is False
        assert task_is_active("") is False


# ── the validator, behaviourally ────────────────────────────────────────────

@pytest.fixture()
def batch(monkeypatch):
    """A batch with three live workers: two searching, one still pending."""
    started = []
    monkeypatch.setattr(monitor_mod, "_start_next_batch_of_downloads",
                        lambda bid: started.append(bid))
    # Exactly the live scenario: three slots reserved, three workers running,
    # and the most recently started one still in 'pending'. queue_index == 3
    # means all three have been dispatched and nothing is left to start.
    ids = ["t-search-1", "t-search-2", "t-pending"]
    for tid, status in zip(ids, ["searching", "searching", "pending"]):
        download_tasks[tid] = {"status": status}
    download_batches[BATCH] = {
        "active_count": 3, "max_concurrent": 3,
        "queue": ids, "queue_index": 3, "_completed_task_ids": set(),
    }
    mon = monitor_mod.WebUIDownloadMonitor()
    mon.monitored_batches = {BATCH}
    try:
        yield mon, started
    finally:
        download_batches.pop(BATCH, None)
        for tid in ids:
            download_tasks.pop(tid, None)


class TestTheValidatorSeesPendingWork:
    def test_a_correct_count_is_left_alone(self, batch):
        """Three live workers, one of them pending. Before, this read as 2 and
        the count was 'fixed' downward — freeing a slot that was in use."""
        mon, started = batch
        mon._validate_worker_counts()
        assert download_batches[BATCH]["active_count"] == 3

    def test_and_no_phantom_worker_is_started(self, batch):
        """The second half of the loop: having freed a slot it should not have,
        the validator then filled it, creating another pending task and another
        round. Every pass consumed a queue entry."""
        mon, started = batch
        mon._validate_worker_counts()
        assert started == [], "started a replacement worker for a slot in use"

    def test_a_genuinely_wrong_count_is_still_corrected(self, batch):
        """The validator must keep doing its job — this is not a licence to stop
        repairing real drift."""
        mon, started = batch
        download_tasks["t-search-1"]["status"] = "completed"
        download_tasks["t-search-2"]["status"] = "failed"
        mon._validate_worker_counts()
        assert download_batches[BATCH]["active_count"] == 1

    @pytest.mark.parametrize("status", ["skipped", "already_owned"])
    def test_the_orphan_branch_covers_every_terminal_status(self, batch, status, caplog):
        """It listed four of the six. A task that finished as 'skipped' or
        'already_owned' sat in the live queue region unnoticed."""
        import logging
        mon, started = batch
        download_batches[BATCH]["queue_index"] = 0   # all three still "live" region
        download_tasks["t-search-1"]["status"] = status
        caplog.set_level(logging.WARNING, logger="soulsync.downloads.monitor")
        mon._validate_worker_counts()
        msgs = [r.getMessage() for r in caplog.records
                if r.name == "soulsync.downloads.monitor"]
        assert any("orphaned=1" in m for m in msgs), msgs


# ── the count cannot go negative, and the gate does not need exact zero ─────

class TestTheCountAndTheGate:
    def test_the_decrement_is_floored(self):
        src = (_SRC / "core" / "downloads" / "lifecycle.py").read_text(encoding="utf-8")
        assert "['active_count'] = max(0, old_active - 1)" in src
        assert "['active_count'] -= 1" not in src, \
            "an unclamped decrement can drive the count negative"

    def test_both_completion_gates_accept_zero_or_less(self):
        """`== 0` is one arithmetic slip away from a batch that can never
        finish; the gate should not be the thing that depends on the clamp."""
        src = (_SRC / "core" / "downloads" / "lifecycle.py").read_text(encoding="utf-8")
        assert src.count("no_active_workers = batch['active_count'] <= 0") == 2
        assert "no_active_workers = batch['active_count'] == 0" not in src


# ── one implementation, and lines that reach the log ───────────────────────

class TestThereIsOnlyOneAnswer:
    def test_the_validator_asks_runtime_state(self):
        src = (_SRC / "core" / "downloads" / "monitor.py").read_text(encoding="utf-8")
        assert "if task_is_active(task_status):" in src
        assert "['searching', 'downloading', 'queued', 'post_processing']" not in src, \
            "a second hand-maintained list is how this drifted in the first place"

    def test_and_so_does_the_drain_wait(self):
        src = (_SRC / "core" / "downloads" / "master.py").read_text(encoding="utf-8")
        assert "task_is_active(download_tasks.get(t, {}).get('status'))" in src
        assert "_NON_TERMINAL_TASK_STATUSES = (" not in src
        # ...and the name is genuinely imported there, not just written down.
        # The two assertions above passed while master.py referenced
        # task_is_active without importing it — a NameError that only fired when
        # the drain actually ran, caught by test_album_serialize_drain and not
        # by this file.
        from core.downloads import master as master_mod
        assert callable(getattr(master_mod, "task_is_active", None))


class TestTheBatchLifecycleReachesAppLog:
    @pytest.mark.parametrize("module", ["lifecycle", "master"])
    def test_it_uses_the_project_factory(self, module):
        """The file handler is attached to the `soulsync` logger, so
        logging.getLogger(__name__) reaches the console and never app.log. These
        two modules decide whether a batch completes, and their absence is why
        this took a source read rather than a log read to find."""
        src = (_SRC / "core" / "downloads" / f"{module}.py").read_text(encoding="utf-8")
        assert f'get_logger("downloads.{module}")' in src
        assert "logger = logging.getLogger(__name__)" not in src

    @pytest.mark.parametrize("module", ["lifecycle", "master", "monitor"])
    def test_and_lands_in_the_captured_namespace(self, module):
        mod = __import__(f"core.downloads.{module}", fromlist=["logger"])
        assert mod.logger.name.startswith("soulsync."), mod.logger.name
