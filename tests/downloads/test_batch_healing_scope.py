"""Batch healing must fire on stuck batches, not on working ones.

From a real app.log: 1,499 `[Batch Healing] Found N orphaned tasks in active
batch` warnings across just 31 batches — one batch "healed" 68 times in 41
minutes while downloading perfectly. The tell is the count itself, which climbed
1 → 2 → 3 → … → 32 in step with the batch's progress, once every 30 seconds:

    17:39:32  Found 1 orphaned tasks in active batch 98fce64d…
    17:40:02  Found 2 orphaned tasks in active batch 98fce64d…
    …
    18:18:32  Found 32 orphaned tasks in active batch 98fce64d…

Nothing was orphaned. "Orphaned" counted every task in a terminal state, so a
healthy batch with N finished tracks reported N orphans, triggered a completion
check, and did it again on the next tick for its whole life.

A finished task in a running batch is normal. The two things that are NOT: every
task done while the phase never advanced (a missed completion callback), and a
queued task whose record has vanished from the task table.

web_server.py can't be imported in a unit test (it builds the whole app at
module scope), so this reads the function's source — the same approach
tests/downloads/test_discovery_filter.py takes for sync-services.js.
"""

from __future__ import annotations

import re
from pathlib import Path

_SRC = (Path(__file__).resolve().parents[2] / "web_server.py").read_text(encoding="utf-8")


def _healer() -> str:
    body = _SRC.split("def validate_and_heal_batch_states(", 1)[1]
    return body.split("\ndef ", 1)[0]


def test_finished_tasks_are_no_longer_called_orphans():
    """The rename is the fix: the old name licensed treating a completed track
    as damage."""
    block = _healer()
    assert "orphaned_tasks" not in block
    assert "finished_tasks" in block
    assert "missing_tasks" in block


def test_a_terminal_status_counts_as_finished_not_missing():
    block = _healer()
    terminal = block.split("elif task_status in TERMINAL_TASK_STATUSES", 1)[1][:120]
    assert "finished_tasks.append" in terminal


def test_the_healer_shares_one_definition_of_active_with_the_validator():
    """The healer and core/downloads/monitor.py's _validate_worker_counts each
    recompute a batch's active count on their own 30-second tick. While the
    healer enumerated its own live states it omitted 'pending' -- the status
    every task is created with -- so it counted 3 where the validator counted
    21, and the two spent a whole run overwriting each other:

        [Batch Healing]     fixing active count 21 -> 3
        [Worker Validation] reported=3, actual=21 ... Fixed active count: 3 -> 21

    4,992 heals against 4,982 validations in one 21-hour log. task_is_active
    derives from the terminal set so a second copy cannot drift; the point of
    this test is that the healer keeps USING it rather than growing a list back.
    """
    block = _healer()
    assert "task_is_active(task_status)" in block
    assert "'searching', 'downloading', 'queued', 'post_processing'" not in block


def test_the_shared_helper_counts_a_pending_task_as_active():
    """The specific omission that caused the oscillation."""
    from core.runtime_state import task_is_active
    assert task_is_active('pending') is True
    assert task_is_active('completed') is False


def test_a_task_absent_from_the_task_table_is_the_real_fault():
    block = _healer()
    tail = block.split("# Task in queue but not in download_tasks dict", 1)[1][:120]
    assert "missing_tasks.append" in tail


def test_the_completion_check_needs_more_than_a_finished_task():
    """The whole bug: `if orphaned_tasks and phase == 'downloading'` was true for
    every healthy batch that had completed anything at all."""
    block = _healer()
    assert "if orphaned_tasks and phase == 'downloading'" not in block
    gate = re.search(r"if phase == 'downloading' and \((.*?)\):", block, re.S)
    assert gate, "the stuck-batch gate should be a single explicit condition"
    cond = gate.group(1)
    assert "missing_tasks" in cond
    assert "actually_active == 0" in cond
    assert "fully_dispatched" in cond


def test_a_batch_with_work_still_dispatching_is_not_stuck():
    """`fully_dispatched` is what stops a batch mid-queue — briefly at zero
    active tasks between workers — being declared stuck."""
    block = _healer()
    assert "fully_dispatched = queue_index >= len(queue)" in block


def test_the_warning_says_which_of_the_two_faults_it_found():
    block = _healer()
    assert "looks stuck" in block
    assert "missing from the task table" in block
    assert "never completed" in block
