"""The video download monitor must never put a real thread in the air.

web_server.py calls ensure_started(_get_video_db) at module scope, and
tests/blocklist/test_blocklist_api.py imports web_server at module scope via
importorskip -- so without the conftest neutraliser the real daemon launches
while pytest is still importing test modules, then lives for the rest of the
run. Its loop calls the live db_provider, which resolves to whichever per-test
database happens to be installed, and it pumps youtube workers there. Because
it ticks on its own schedule, the test it lands in is decided by timing alone;
that is the video suite's order-dependence in one sentence.
"""
import threading

import core.video.download_monitor as mon


def _live_monitor_threads():
    return [t.name for t in threading.enumerate()
            if "video-download-monitor" in t.name and t.is_alive()]


def test_no_monitor_thread_survived_collection():
    """The launch this suite actually suffered from came from an import, not a
    call, so assert on the process rather than on any one code path."""
    assert not _live_monitor_threads()


def test_the_launcher_is_inert_even_when_the_flag_is_clear(monkeypatch):
    """test_video_health clears _started to assert the "monitor is not running"
    health warning. While only the flag was pre-set, any grab-shaped call in
    that window started the daemon for the remainder of the session."""
    monkeypatch.setattr(mon, "_started", False)

    mon.ensure_started(lambda: None)

    # The real ensure_started sets _started True as it launches; the inert one
    # leaves it alone. This distinguishes them without inspecting the thread.
    assert mon._started is False, \
        "the real ensure_started ran -- it sets _started=True when it launches"
    assert not _live_monitor_threads()
