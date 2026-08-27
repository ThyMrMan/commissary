"""Timestamps the app writes local and two readers were reading as UTC.

`video_downloads` accumulated three timestamp formats from three writers, and
two readers that each assumed UTC. On a UTC-4 host that made every app-written
stamp read four hours older than it was, and both readers act on the result:

  * ``stall`` compares ``progress_at`` against ``time.time()``. A local string
    read as UTC put idle at 14400s against a 1800s timeout, so the first tick
    without forward progress was instantly STALLED. Two films died four seconds
    after their last progress reading, one of them at 100.0% while it was still
    being finalised.

  * ``seeding`` compares ``completed_at`` to decide when a torrent may be
    released — and with ``seed_remove_data`` on, that deletes its data. Same
    misread, but this one is irreversible.

The storage stayed LOCAL rather than the writers moving to UTC, because the
Downloads history page renders these values verbatim with no conversion
(`fmtWhen`); it is right today precisely because they are local. See
core/video/timestamps.py.

A NOTE ON WHERE THESE TESTS BITE
On a host whose local zone IS UTC, local and UTC readings are identical and
nothing here can tell a correct reader from the broken one — that is a real
property of the situation, not a gap in the tests. They discriminate on every
host with a non-zero offset, which includes the machine this bug was found on.
The explicit-offset tests hold everywhere.
"""

from __future__ import annotations

import time
from datetime import datetime, timedelta, timezone

import pytest

from core.video import stall
from core.video.seeding import _completed_age_hours
from core.video.timestamps import LOCAL, UTC, now_str, to_epoch

FMT = "%Y-%m-%d %H:%M:%S"


# ── the parser ──────────────────────────────────────────────────────────────

class TestReadingAStoredStamp:
    def test_a_naive_value_means_local_by_default(self):
        """The default matters: every caller that forgets to say gets the
        convention the app actually writes, not the one that broke it."""
        s = "2026-08-27 12:00:00"
        assert to_epoch(s) == datetime.strptime(s, FMT).timestamp()

    def test_local_and_utc_are_two_different_readings_of_the_same_string(self):
        """Spelled out as two independent computations rather than one derived
        from the other, so a reader that quietly went back to UTC fails here on
        any host with an offset."""
        s = "2026-08-27 12:00:00"
        naive = datetime.strptime(s, FMT)
        assert to_epoch(s, naive_is=LOCAL) == naive.timestamp()
        assert to_epoch(s, naive_is=UTC) == naive.replace(tzinfo=timezone.utc).timestamp()

    def test_what_the_app_writes_reads_back_as_now(self):
        """The whole bug in one line: the writer and the reader have to agree."""
        assert to_epoch(now_str()) == pytest.approx(time.time(), abs=2)

    def test_an_explicit_offset_always_wins(self):
        """Old YouTube rows carry '+00:00'. Whatever the caller assumed about
        naive values, a value that states its offset is not a guess."""
        assert to_epoch("2026-08-27T16:00:00+00:00", naive_is=LOCAL) == 1787846400.0
        assert to_epoch("2026-08-27T16:00:00+00:00", naive_is=UTC) == 1787846400.0
        assert to_epoch("2026-08-27T16:00:00Z", naive_is=LOCAL) == 1787846400.0

    @pytest.mark.parametrize("value", [None, "", "   ", "not a date", "2026-13-45 99:99:99"])
    def test_an_unreadable_value_is_None_not_an_exception(self, value):
        """Callers turn None into 'no clock yet'. One malformed row must never
        mass-fail a queue, and must never raise inside the monitor loop."""
        assert to_epoch(value) is None

    def test_an_epoch_number_passes_straight_through(self):
        assert to_epoch(1787846400.0) == 1787846400.0
        assert to_epoch(1787846400) == 1787846400.0

    @pytest.mark.parametrize("fmt", ["%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S",
                                     "%Y-%m-%d %H:%M:%S.%f"])
    def test_every_shape_the_app_has_ever_written_parses(self, fmt):
        s = datetime(2026, 8, 27, 12, 0, 0, 500000).strftime(fmt)
        assert to_epoch(s) is not None


# ── the stall clock ─────────────────────────────────────────────────────────

def _written_now() -> str:
    """Exactly what download_monitor._now() puts in the row."""
    return now_str()


class TestAFreshDownloadIsNotStalled:
    def test_a_stamp_just_written_reports_no_idle_time(self):
        """THE regression. Before the fix this returned the host's UTC offset in
        seconds — 14400 on UTC-4 — so a download was over a 1800s timeout the
        instant its clock started."""
        verdict, idle = stall.classify(50, 50, _written_now(), time.time())
        assert idle == pytest.approx(0, abs=2)
        assert verdict == stall.WAITING

    def test_a_download_at_100_percent_is_not_killed_while_finalising(self):
        """Shang-Chi was at 100.0% — fully downloaded, still being finalised —
        when the skew killed it four seconds after its last reading."""
        verdict, _idle = stall.classify(100, 100, _written_now(), time.time())
        assert verdict != stall.STALLED

    def test_a_genuinely_old_stamp_still_stalls(self):
        """The fix must not make the timeout unreachable — that would trade a
        false positive for downloads that hang forever."""
        old = (datetime.now() - timedelta(seconds=3600)).strftime(FMT)
        verdict, idle = stall.classify(50, 50, old, time.time())
        assert verdict == stall.STALLED
        assert idle == pytest.approx(3600, abs=5)

    def test_a_youtube_row_with_an_explicit_offset_still_reads_right(self):
        """Rows written before the writers were unified carry '+00:00'."""
        stamp = datetime.now(timezone.utc).isoformat(timespec="seconds")
        verdict, idle = stall.classify(50, 50, stamp, time.time())
        assert idle == pytest.approx(0, abs=2) and verdict == stall.WAITING


# ── the seeding sweep, which deletes ────────────────────────────────────────

class TestSeedingAgeUsesTheRightBasePerColumn:
    def test_a_just_completed_download_is_zero_hours_old(self):
        """Read as UTC this was the host's offset in hours — four on UTC-4 —
        so a 4-hour seed goal was met the moment the download finished."""
        age = _completed_age_hours({"completed_at": now_str()})
        assert age == pytest.approx(0, abs=0.05)

    def test_updated_at_is_read_as_UTC_because_sqlite_wrote_it(self):
        """The fallback column is not app-written: it is SQLite's own
        datetime('now'), which is UTC. Reading both columns with one assumption
        is what made this wrong — whichever assumption you pick, one of them
        breaks.

        Measured against a stamp six hours old rather than a fresh one: the
        function floors its result at zero, so a fresh stamp misread in the
        future direction clamps to 0.0 and looks correct. An age that has room
        to be wrong in either direction is what makes this test bite."""
        six_ago = (datetime.now(timezone.utc) - timedelta(hours=6)).strftime(FMT)
        age = _completed_age_hours({"updated_at": six_ago})
        assert age == pytest.approx(6, abs=0.05)

    def test_completed_at_wins_over_updated_at(self):
        row = {"completed_at": (datetime.now() - timedelta(hours=2)).strftime(FMT),
               "updated_at": datetime.now(timezone.utc).strftime(FMT)}
        assert _completed_age_hours(row) == pytest.approx(2, abs=0.05)

    def test_a_real_age_is_still_measured(self):
        old = (datetime.now() - timedelta(hours=5)).strftime(FMT)
        assert _completed_age_hours({"completed_at": old}) == pytest.approx(5, abs=0.05)

    def test_an_explicit_offset_row_still_reads_right(self):
        stamp = datetime.now(timezone.utc).isoformat(timespec="seconds")
        assert _completed_age_hours({"completed_at": stamp}) == pytest.approx(0, abs=0.05)

    @pytest.mark.parametrize("row", [{}, {"completed_at": None},
                                     {"completed_at": "rubbish"}])
    def test_an_unusable_row_yields_None_rather_than_a_release(self, row):
        """None means 'cannot judge', and goals_met keeps seeding. A parse
        failure must never read as 'old enough to delete'."""
        assert _completed_age_hours(row) is None

    def test_a_future_stamp_clamps_to_zero_rather_than_going_negative(self):
        future = (datetime.now() + timedelta(hours=3)).strftime(FMT)
        assert _completed_age_hours({"completed_at": future}) == 0.0


# ── the writers agree with each other ───────────────────────────────────────

class TestOneStorageFormat:
    def test_every_writer_uses_the_shared_helper(self):
        """`completed_at` had three shapes from three writers, and the history
        page renders whichever one it finds verbatim — so YouTube grabs showed
        in UTC while every row beside them showed local."""
        import pathlib
        src = pathlib.Path(__file__).resolve().parents[1]
        for rel in ["core/video/download_monitor.py", "core/video/youtube_download.py",
                    "api/video/downloads.py"]:
            text = (src / rel).read_text(encoding="utf-8")
            assert "from core.video.timestamps import now_str" in text, rel

    def test_the_monitor_and_the_helper_produce_the_same_shape(self):
        from core.video.download_monitor import _now
        a, b = _now(), now_str()
        assert len(a) == len(b) == 19
        assert datetime.strptime(a, FMT) and datetime.strptime(b, FMT)
