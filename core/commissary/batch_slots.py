"""Post-processing always frees its batch slot (fork 1.9.16).

``post_process_matched_download_with_verification`` has a dozen exits. Most
notify the batch that the task is over; a few deliberately don't, because a
retry was queued and something else owns the outcome. An exit that forgets
which kind it is leaks the worker slot: ``active_count`` never reaches zero
and the whole album hangs on "downloading" until the healer notices.

So a hand-off has to be declared, and the wrapper's ``finally`` calls
:func:`release_unclaimed_slot` for every other way out, including ones that
never return normally (a BaseException unwinding the worker thread).
"""

from __future__ import annotations

from core.downloads.cancel import _TERMINAL_STATUSES
from core.runtime_state import download_tasks, tasks_lock
from utils.logging_config import get_logger

logger = get_logger("commissary.batch_slots")


def release_unclaimed_slot(state: dict, task_id, batch_id, context_key, notify) -> bool:
    """Free the slot if post-processing ended un-notified and un-handed-off.

    ``state`` is the wrapper's ``{"notified": bool, "handed_off": reason}``;
    ``notify(batch_id, task_id, success=False)`` is its notifier.
    Returns True when it had to step in.
    """
    if state.get('notified') or state.get('handed_off') or not (task_id and batch_id):
        return False
    logger.error(
        "[Batch Accounting] Post-processing for task %s (batch %s, %s) ended without "
        "completing it or queueing a retry — freeing its worker slot and marking it "
        "failed so the batch can finish. This is a bug.",
        task_id, batch_id, context_key,
    )
    with tasks_lock:
        task = download_tasks.get(task_id)
        if isinstance(task, dict) and task.get('status') not in _TERMINAL_STATUSES:
            task['status'] = 'failed'
            task.setdefault('error_message', 'Post-processing ended without recording an outcome')
    notify(batch_id, task_id, success=False)
    return True


def count_active_workers(batch: dict, tasks: dict) -> int:
    """Busy worker slots in ``batch``: tasks DISPATCHED to a worker and not finished.

    ONE definition, shared by the worker-count validator (downloads/monitor.py)
    and the batch healer (web_server.py) — fork 2.3.7. Two enumerated status
    lists once disagreed and fought (2.3.4: "21 <-> 3" every 30s); counting
    every unfinished task instead made them agree on a wrong number and wedged
    every batch bigger than its worker limit (2.3.7: "3 -> 98").

    Dispatched = before ``queue_index``: start_next_batch_of_downloads advances
    it as it hands each task over, and batches that start their own workers
    (pinned, redownload) are created already past them. Everything from
    ``queue_index`` on is waiting and holds no slot.

    A dispatched task counts whatever its status — including 'pending', which
    upstream's lists left out, so a just-started worker read as a free slot and
    the validator launched another. An unknown status counts too (safe
    direction: the batch waits rather than over-starting). A terminal task, one
    already through the completion callback, or one missing from ``tasks``
    holds none.
    """
    queue = batch.get('queue') or []
    dispatched = queue[:batch.get('queue_index') or 0]
    completed = batch.get('_completed_task_ids') or ()
    busy = 0
    for task_id in dispatched:
        if task_id in completed:
            continue
        status = (tasks.get(task_id) or {}).get('status')
        if status and status not in _TERMINAL_STATUSES:
            busy += 1
    return busy
