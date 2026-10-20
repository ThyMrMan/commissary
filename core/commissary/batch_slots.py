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
