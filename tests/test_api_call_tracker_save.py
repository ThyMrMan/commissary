"""Two overlapping saves must not fight over one temp file.

From the user's app.log:

    [ApiCallTracker] Failed to save history:
    [Errno 13] Permission denied: 'database/api_call_history.json.tmp'

Shutdown saves TWICE — ``_shutdown_runtime_components()`` calls ``save()``, then
``sys.exit(0)`` fires the atexit handler which calls it again — and both used a
single fixed ``.tmp`` name. On Windows the second open fails while the first
still holds the handle.

The failure path made it worse: it deleted that shared name, so a save that
failed could remove a concurrent save's file out from under its ``os.replace``
and take the healthy one down too.

Same shape, and the same fix, as the atomic audio save in 2.3.2.
"""

from __future__ import annotations

import os

import core.api_call_tracker as tracker_mod


def _capture_tmp_paths(monkeypatch, tmp_path, calls):
    """Run save() against a scratch file, recording the temp path it builds."""
    monkeypatch.setattr(tracker_mod, "_PERSIST_PATH",
                        str(tmp_path / "api_call_history.json"))
    real_replace = os.replace

    def _spy(src, dst):
        calls.append(src)
        real_replace(src, dst)

    monkeypatch.setattr(tracker_mod.os, "replace", _spy)


def test_each_save_writes_its_own_temp_file(monkeypatch, tmp_path):
    """The fixed name is what let two shutdown saves collide."""
    seen = []
    _capture_tmp_paths(monkeypatch, tmp_path, seen)
    t = tracker_mod.ApiCallTracker()

    t.save()
    t.save()

    assert len(seen) == 2
    assert seen[0] != seen[1], (
        "both saves used the same temp path (%s) — a second save while the "
        "first still holds the handle is the reported Errno 13" % seen[0])


def test_the_temp_file_is_cleaned_up_on_success(monkeypatch, tmp_path):
    """Unique names must not mean an accumulating pile of .tmp files."""
    seen = []
    _capture_tmp_paths(monkeypatch, tmp_path, seen)
    tracker_mod.ApiCallTracker().save()

    leftovers = list(tmp_path.glob("*.tmp"))
    assert not leftovers, "temp files left behind: %s" % leftovers
    assert (tmp_path / "api_call_history.json").exists()


def test_a_failed_save_only_removes_its_own_temp_file(monkeypatch, tmp_path):
    """The old cleanup deleted the shared fixed name, so a losing writer could
    destroy a concurrent writer's file. A save that fails must leave every temp
    file except the one it created itself."""
    monkeypatch.setattr(tracker_mod, "_PERSIST_PATH",
                        str(tmp_path / "api_call_history.json"))
    # Stand in for a concurrent save's in-flight temp file.
    other = tmp_path / "api_call_history.json.99.99.deadbeef.tmp"
    other.write_text("someone else's write in progress", encoding="utf-8")

    def _boom(src, dst):
        raise OSError("disk went away mid-replace")

    monkeypatch.setattr(tracker_mod.os, "replace", _boom)
    tracker_mod.ApiCallTracker().save()      # must not raise

    assert other.exists(), "a failed save deleted another save's temp file"
