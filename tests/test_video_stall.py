"""A stall clock a restart could reset — so the longer something was stuck, the
less likely it was ever caught.

Ported from upstream SoulSync 3.2.1. Six torrents on a live install sat at the
same percentage for 199 minutes against a 30-minute timeout and were never
failed. They all flipped to 'searching' about 30 minutes after a restart: the
clock was a module dict keyed off ``time.monotonic()``, so every restart wiped
it and handed each stuck download a fresh half hour. The perverse consequence is
that the more restarts a dead download survived, the safer it was.

The clock now lives on the row (``progress_at``) in wall-clock time. It survives
restarts, and it is the same measure a person uses looking at the Downloads
page: "this hasn't moved since 4pm".

Two more things fall out of the same read:

  · "Finished, but the file never appeared" was not tracked AT ALL. That patch
    carries progress and NO status, and the old branch matched only
    queued/downloading, so the row fell through every guard and spun at 100%
    forever. On a library spread across many mount roots that is exactly what an
    unresolvable save path looks like, so it gets its own message.

  · A BACKWARDS percentage is no longer read as movement. A torrent re-checking
    or resuming briefly reports less, and counting that as progress renewed the
    grace period every time it happened.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from core.video import stall


def _stamp(seconds_ago: float) -> str:
    """A stored ``progress_at`` that many seconds in the past.

    Built the way the PRODUCTION WRITER builds it — ``download_monitor._now()``
    is ``time.strftime(...)``, i.e. local wall-clock with no offset. This helper
    used to build a UTC-naive string instead, which matched the reader's
    (wrong) assumption rather than the writer, and is why the whole file passed
    while every real download on a non-UTC host was killed four seconds after
    its last progress reading. A fixture that models the reader instead of the
    writer cannot catch a reader/writer disagreement."""
    return (datetime.now() - timedelta(seconds=seconds_ago)).strftime(
        "%Y-%m-%d %H:%M:%S")


def _now() -> float:
    return datetime.now(timezone.utc).timestamp()


class TestTheClockSurvivesARestart:
    def test_a_download_stuck_past_the_timeout_is_stalled(self):
        """THE regression. The stored stamp is wall-clock, so process uptime is
        irrelevant — this is the case the old module dict could never see after
        a restart."""
        verdict, idle = stall.classify(50.0, 50.0, _stamp(3600), _now(),
                                       timeout_seconds=1800)
        assert verdict == stall.STALLED
        assert idle == pytest.approx(3600, abs=5)

    def test_inside_the_grace_period_it_just_waits(self):
        verdict, idle = stall.classify(50.0, 50.0, _stamp(600), _now(),
                                       timeout_seconds=1800)
        assert verdict == stall.WAITING and idle == pytest.approx(600, abs=5)

    def test_progress_resets_the_clock(self):
        verdict, idle = stall.classify(50.0, 51.0, _stamp(3600), _now(),
                                       timeout_seconds=1800)
        assert verdict == stall.MOVED and idle == 0.0

    def test_a_row_with_no_clock_yet_starts_one(self):
        """A new row, or one migrated before the column existed. It must not read
        as infinitely stalled — that would mass-fail an existing queue on the
        first poll after an upgrade."""
        assert stall.classify(0, 0, None, _now())[0] == stall.SEEDED
        assert stall.classify(0, 0, "", _now())[0] == stall.SEEDED

    def test_an_unparseable_stamp_is_treated_as_no_clock(self):
        """Never as infinitely stalled — a bad timestamp must not fail a queue."""
        assert stall.classify(0, 0, "not-a-date", _now())[0] == stall.SEEDED


class TestBackwardsIsNotProgress:
    def test_a_recheck_reporting_less_does_not_renew_the_grace(self):
        """A torrent re-verifying briefly reports a lower percentage. Counting
        that as movement handed a dead download a fresh half hour every time it
        happened."""
        verdict, _ = stall.classify(80.0, 40.0, _stamp(3600), _now(),
                                    timeout_seconds=1800)
        assert verdict == stall.STALLED

    def test_identical_progress_is_not_movement_either(self):
        assert stall.classify(80.0, 80.0, _stamp(3600), _now(),
                              timeout_seconds=1800)[0] == stall.STALLED

    @pytest.mark.parametrize("prev,new", [(None, 1.0), (0, 0.5), ("", 2)])
    def test_going_forwards_from_nothing_still_counts(self, prev, new):
        assert stall.classify(prev, new, _stamp(3600), _now())[0] == stall.MOVED

    @pytest.mark.parametrize("prev,new", [("junk", "junk"), (None, None)])
    def test_junk_percentages_do_not_raise(self, prev, new):
        assert stall.classify(prev, new, _stamp(10), _now())[0] in (
            stall.WAITING, stall.STALLED)


class TestWhichStatesAreWatched:
    @pytest.mark.parametrize("status", ["queued", "downloading", "", None])
    def test_a_live_download_is_watched(self, status):
        assert stall.tracks_stall(status) is True

    @pytest.mark.parametrize("status", ["completed", "failed", "cancelled",
                                        "import_failed"])
    def test_a_finished_one_is_not(self, status):
        assert stall.tracks_stall(status) is False
        assert stall.is_terminal(status) is True

    @pytest.mark.parametrize("status", ["importing", "searching"])
    def test_busy_elsewhere_is_not_watched(self, status):
        """An import sits at 100% for as long as the copy takes, and a multi-GB
        file over SMB can exceed the timeout easily — failing it for "no
        progress" would destroy a download that was working perfectly.
        'searching' belongs to the requery thread, which owns its own lifetime."""
        assert stall.tracks_stall(status) is False
        assert stall.is_terminal(status) is False

    def test_a_statusless_patch_IS_watched(self):
        """The "finished but unplaceable" shape: progress and no status. The old
        branch matched only queued/downloading, so this fell through every guard
        and spun at 100% forever."""
        assert stall.tracks_stall(None) is True


class TestTheMessage:
    def test_an_ordinary_stall_says_how_long(self):
        assert stall.reason(stall.STALLED, 3600) == "Stalled — no progress for 60 min"

    def test_a_completed_but_unplaceable_download_says_something_else(self):
        """Telling someone "no progress" when the bytes are already on disk
        sends them hunting seeders that were never the problem."""
        msg = stall.reason(stall.STALLED, 1860, at_completion=True)
        assert "never appeared" in msg
        assert "save path" in msg
        assert "no progress" not in msg

    @pytest.mark.parametrize("bad", [None, "", "junk", object()])
    def test_junk_input_never_raises(self, bad):
        """A message is not worth raising over — this one was a real bug caught
        upstream by the junk-input test."""
        assert isinstance(stall.reason(stall.STALLED, bad), str)


def test_the_monitor_no_longer_keeps_the_clock_in_memory():
    """Source guard. The module dict is the defect; a revert would restore it."""
    from pathlib import Path
    src = (Path(__file__).resolve().parent.parent / "core" / "video"
           / "download_monitor.py").read_text(encoding="utf-8")
    # NB: match the assignment, not the bare name — 'tracks_stall' contains
    # '_stall' as a substring, which is the trap upstream's own test hit.
    assert "_stall: dict" not in src
    assert "_stall.pop(" not in src
    assert "stall.tracks_stall(" in src
    assert 'upd["progress_at"]' in src


def test_the_row_can_store_the_clock(tmp_path):
    import database.video_database as mod
    if hasattr(mod, "_initialized_paths"):
        mod._initialized_paths.clear()
    db = mod.VideoDatabase(str(tmp_path / "v.db"))
    conn = db._get_connection()
    cols = [r["name"] for r in conn.execute("PRAGMA table_info(video_downloads)")]
    conn.close()
    assert "progress_at" in cols
