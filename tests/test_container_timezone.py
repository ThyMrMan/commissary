"""The container has to be able to honour TZ.

Reported: the video calendar highlighted 9/5 while the user's local clock read
20:16 on 9/4. api/video/calendar.py sends ``date.today()`` to the browser as the
"today" marker, so the highlight is the SERVER's date — and the server was on
UTC, which rolls over four hours early in America/New_York.

The same offset is visible in their app.log without needing the calendar at all:
entries stamped ``2026-09-05 00:16:30`` inside a file whose mtime is Sep 4 20:16.

docker-compose.yml already ships ``TZ=America/New_York``. The missing half is
that python:3.11-slim carries no timezone database, and TZ against a missing
/usr/share/zoneinfo leaves glibc on UTC without complaining — so the setting
looked applied and did nothing.

Source reads rather than a container build: these are cheap, and the thing worth
pinning is that the two halves stay together. Either alone is silently useless.
"""

from __future__ import annotations

from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
_DOCKERFILE = (_ROOT / "Dockerfile").read_text(encoding="utf-8")
_COMPOSE = (_ROOT / "docker-compose.yml").read_text(encoding="utf-8")


def _runtime_stage() -> str:
    """Everything after the LAST FROM — the image that actually ships.

    A builder stage installing tzdata would not help: its /usr/share/zoneinfo
    is discarded unless it is explicitly copied forward.
    """
    return _DOCKERFILE.rsplit("FROM ", 1)[1]


def test_the_runtime_image_carries_a_timezone_database():
    """Without tzdata, TZ is accepted and ignored — the failure mode is a
    correct-looking setting that silently leaves every date on UTC."""
    assert "tzdata" in _runtime_stage(), (
        "tzdata is missing from the RUNTIME stage; TZ cannot take effect and "
        "every server-rendered date falls back to UTC")


def test_the_compose_file_still_sets_a_timezone():
    """The other half. tzdata alone changes nothing if nothing selects a zone."""
    active = [ln for ln in _COMPOSE.splitlines()
              if "TZ=" in ln and not ln.lstrip().startswith("#")]
    assert active, "docker-compose.yml no longer sets TZ for the service"


def test_the_calendar_marks_today_from_a_date_the_timezone_reaches():
    """Pins WHY this matters. The highlight is the server's date, so it is only
    right when the server's timezone is. If this ever moves to a client-supplied
    date the timezone dependency goes away and this test should go with it."""
    src = (_ROOT / "api" / "video" / "calendar.py").read_text(encoding="utf-8")
    assert "date.today()" in src
    assert '"today": today.isoformat()' in src
