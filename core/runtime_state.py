"""Shared runtime state and tiny helpers for the app."""

from __future__ import annotations

import logging
import threading
import time
from functools import wraps
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)

matched_context_lock = threading.Lock()
matched_downloads_context: Dict[str, Dict[str, Any]] = {}
tasks_lock = threading.Lock()
download_tasks: Dict[str, Dict[str, Any]] = {}
download_batches: Dict[str, Dict[str, Any]] = {}
batch_locks: Dict[str, threading.Lock] = {}
processed_download_ids = set()
post_process_locks: Dict[str, threading.Lock] = {}
post_process_locks_lock = threading.Lock()

# A task in one of these is finished — its batch slot has been accounted for and
# nothing more will happen to it. Lives here rather than in any one consumer so
# the download tracker, the cleanup sweep and the post-processing safety net all
# agree on what "done" means; two copies of this set would drift into a task
# that one of them thinks is still running.
TERMINAL_TASK_STATUSES = frozenset({
    'completed', 'failed', 'not_found', 'cancelled', 'skipped', 'already_owned',
})


def task_is_active(status) -> bool:
    """Whether a download task still holds a batch worker slot.

    DERIVED from the terminal set rather than listing the live states, because
    the enumerated version drifted and cost a wedged batch every time. The
    worker-count validator listed ('searching', 'downloading', 'queued',
    'post_processing') and omitted 'pending' — the status a task is created
    with, and therefore the status of every worker the validator had just
    started. Such a task matched neither the active branch nor the orphaned
    branch: it was invisible, so the validator "corrected" a count that was
    right, freed a slot still in use, and launched a replacement — whose task
    was also pending, so the next pass did it again. Observed on five batches
    out of five, oscillating 3->2 every few seconds for two minutes.

    Deriving it also fails in the safe direction: a status nobody thought about
    reads as STILL WORKING, so a batch waits rather than declaring itself free
    and spawning phantom workers.

    A missing/blank status is NOT active: a task that isn't in ``download_tasks``
    has no slot to hold, and ``_wait_for_batch_drain`` would otherwise block on
    something that does not exist."""
    if not status:
        return False
    return str(status) not in TERMINAL_TASK_STATUSES

activity_feed = []
activity_feed_lock = threading.Lock()
_activity_toast_emitter = None


def caller_must_hold_tasks_lock(func):
    """Best-effort guard for helpers that mutate download_tasks in place."""
    @wraps(func)
    def wrapper(*args, **kwargs):
        if not tasks_lock.locked():
            raise RuntimeError(f"{func.__name__}() requires tasks_lock to be held by the caller")
        return func(*args, **kwargs)

    return wrapper


def set_activity_toast_emitter(emitter) -> None:
    """Set the WebSocket-style emitter used by add_activity_item."""
    global _activity_toast_emitter
    _activity_toast_emitter = emitter


def add_activity_item(icon, title, subtitle, time_ago="Now", show_toast=True):
    """Append an activity item and emit a toast if an emitter is configured."""
    activity_item = {
        "icon": icon,
        "title": title,
        "subtitle": subtitle,
        "time": time_ago,
        "timestamp": time.time(),
        "show_toast": show_toast,
    }
    with activity_feed_lock:
        activity_feed.append(activity_item)
        if len(activity_feed) > 20:
            activity_feed.pop(0)

    if show_toast and _activity_toast_emitter is not None:
        try:
            _activity_toast_emitter("dashboard:toast", activity_item)
        except Exception as e:
            logger.debug("emit activity toast failed: %s", e)

    return activity_item


def claim_for_post_processing(task_id: str) -> bool:
    """Atomically claim a download task for post-processing.

    The browser-poll status endpoint AND the background download monitor both
    watch the same slskd/streaming transfers and each tries to post-process a
    completed file. Without a single claim, BOTH run the verification pipeline
    on the same download — double imports, and a nasty race where one path
    quarantines + requeues the next-best candidate (clearing the source identity
    and resetting status to ``searching``) while the other, mid-flight, then
    reports a bogus "missing file or source information" failure that clobbers
    the in-flight retry.

    The claim is the ``downloading``/``queued`` -> ``post_processing`` status
    transition, done under ``tasks_lock``. Exactly one caller wins:

    - Returns ``True`` (and flips the status) for the caller that claimed it.
    - Returns ``False`` if the task is gone, already ``post_processing`` (owned
      by the other path), requeued (``searching``), or terminal — the caller
      must then NOT process the file.

    Acquires ``tasks_lock`` itself, so callers must NOT already hold it.
    """
    with tasks_lock:
        task = download_tasks.get(task_id)
        if not task:
            return False
        if task.get("status") in ("downloading", "queued"):
            task["status"] = "post_processing"
            task["status_change_time"] = time.time()
            return True
        return False


@caller_must_hold_tasks_lock
def mark_task_completed(task_id: str, track_info: Optional[Dict[str, Any]] = None) -> bool:
    """Mark a download task as completed.

    Callers must already hold `tasks_lock`.
    """
    task = download_tasks.get(task_id)
    if not task:
        return False

    task["status"] = "completed"
    task["stream_processed"] = True
    task["status_change_time"] = time.time()
    if track_info is not None:
        task["track_info"] = track_info
    return True
