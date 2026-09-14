"""The batch healer counts only dispatched tasks as busy worker slots.

``validate_and_heal_batch_states`` recomputes every batch's active count on a
30-second tick. It shared the worker-count validator's count of every unfinished
task in the queue, so it re-asserted the same wrong number -- ``fixing active
count 3 → 64``, ``3 → 93``, ``3 → 102`` in a live log -- and a batch bigger than
its worker limit never started a fourth worker (see
test_batch_dispatched_worker_count). This drives the real function in web_server.
"""

from __future__ import annotations

import os
import tempfile

import pytest

# Redirect the DB before importing web_server so it never touches a real library.
_TMP = tempfile.mkdtemp(prefix='soulsync-testdb-healer-')
os.environ['DATABASE_PATH'] = os.path.join(_TMP, 'healer_count.db')
os.environ['SOULSYNC_TEST_DB_READY'] = '1'

web_server = pytest.importorskip('web_server')

from core.runtime_state import download_batches, download_tasks  # noqa: E402
from tests.downloads.test_batch_dispatched_worker_count import BATCH, _make_batch  # noqa: E402


@pytest.fixture
def healer(monkeypatch):
    started, completion_checks = [], []
    monkeypatch.setattr(web_server, "_start_next_batch_of_downloads",
                        lambda bid: started.append(bid))
    monkeypatch.setattr(web_server, "_check_batch_completion_v2",
                        lambda bid: completion_checks.append(bid))
    saved_tasks, saved_batches = dict(download_tasks), dict(download_batches)
    download_tasks.clear()
    download_batches.clear()
    try:
        yield started, completion_checks
    finally:
        download_tasks.clear()
        download_batches.clear()
        download_tasks.update(saved_tasks)
        download_batches.update(saved_batches)


def test_a_big_batch_keeps_its_true_count(healer):
    started, completion_checks = healer
    _make_batch(98, dispatched=3)

    web_server.validate_and_heal_batch_states()

    assert download_batches[BATCH]["active_count"] == 3
    assert started == [] and completion_checks == []


def test_a_batch_the_old_count_wedged_is_healed_and_moving_again(healer):
    """A live batch after the old count had run: 95 'busy' slots, two workers
    really running, the third finished."""
    started, _ = healer
    ids = _make_batch(98, dispatched=3)
    download_tasks[ids[0]]["status"] = "completed"
    download_batches[BATCH]["_completed_task_ids"].add(ids[0])
    download_batches[BATCH]["active_count"] = 95

    web_server.validate_and_heal_batch_states()

    assert download_batches[BATCH]["active_count"] == 2
    assert started == [BATCH], "a free slot and 95 tracks waiting, and no worker started"
