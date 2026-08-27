"""One convention for the timestamps the video side stores, and one parser.

`video_downloads` grew three different timestamp formats, written by three
different places, and two readers that each guessed at what they were reading.
The guesses were wrong, and both readers do arithmetic that ends in an action:

    download_monitor._now()        "2026-08-27 12:00:00"        local, naive
    api.video.downloads (cancel)   "2026-08-27 12:00:00"        local, naive
    youtube_download._now()        "2026-08-27T16:00:00+00:00"  UTC, explicit
    SQLite datetime('now')         "2026-08-27 16:00:00"        UTC, naive

    stall._ts()                    naive → UTC   ✗ (writer is local)
    seeding._completed_age_hours() naive → UTC   ✗ for completed_at,
                                                 ✓ for its updated_at fallback

On a UTC-4 host that made every app-written timestamp read four hours older
than it was:

  * **Downloads died four seconds after their last progress reading.** `stall`
    compares `progress_at` against `time.time()` (epoch, UTC). Reading a local
    string as UTC put `idle` at 14400s against a 1800s timeout, so the first
    tick without forward progress was instantly STALLED. Shang-Chi was at
    **100.0%** — fully downloaded, still being finalised — when it was killed.

  * **The seeding sweep released torrents four hours early**, and with
    `seed_remove_data` on that deletes their data. Same misread, but this one
    ends in an irreversible action rather than a retry.

WHY NAIVE MEANS LOCAL, AND WHY THE WRITERS WERE NOT SIMPLY SWITCHED TO UTC

`completed_at` is rendered by the Downloads history page **verbatim** — the
frontend splits the stored string and prints the parts, with no timezone
conversion at all (`fmtWhen` in webui/static/video/video-download-history.js).
It reads correctly today precisely *because* the value is local. Writing UTC
instead would have shifted every "Finished" time on that page by the whole UTC
offset — trading a silent arithmetic bug for a loud display one.

So the storage convention is what the display already depends on: **naive means
local wall-clock**, and the readers were the half that was wrong. This also
needs no migration and has no transient — every row already on disk, written by
any of the three writers, parses correctly under these rules.

An EXPLICIT offset is always honoured. Old YouTube rows carry `+00:00` and stay
correct; new ones use the shared format so the history page stops showing
YouTube grabs in UTC while everything beside them is local.

SQLite's own `datetime('now')` columns (`created_at`, `updated_at`) are UTC and
are NOT app-written, so a caller reading one passes ``naive_is="utc"``. That is
the one place the two conventions genuinely meet, and naming it at the call site
is better than a parser that has to guess.
"""

from __future__ import annotations

import time
from datetime import datetime, timezone
from typing import Any, Optional

# The formats an app-written timestamp has ever used, most common first.
_FORMATS = ("%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S",
            "%Y-%m-%d %H:%M:%S.%f", "%Y-%m-%dT%H:%M:%S.%f")

# What a stored value means when it carries no offset.
LOCAL = "local"     # written by the app (download_monitor, youtube, cancel)
UTC = "utc"         # written by SQLite's datetime('now')


def now_str() -> str:
    """The app's storage format for a timestamp: local wall-clock, no offset.

    Local because the Downloads history page prints these verbatim. Use this
    for every column the app writes, so `completed_at` has one shape whichever
    code path filled it in."""
    return time.strftime("%Y-%m-%d %H:%M:%S")


def to_epoch(value: Any, *, naive_is: str = LOCAL) -> Optional[float]:
    """Epoch seconds for a stored timestamp, or None if it cannot be read.

    An explicit offset in the value always wins. A naive value is interpreted
    per ``naive_is`` — the caller knows which column it is holding, and that is
    not something a parser can work out for itself.

    None on anything unparseable, deliberately: callers turn this into "no clock
    yet" rather than "infinitely stalled", so one malformed row can never
    mass-fail a queue.
    """
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value)

    text = str(value).strip()
    if not text:
        return None

    # Explicit offset (including 'Z') — unambiguous, so honour it whatever the
    # caller assumed. This is what keeps pre-existing YouTube rows correct.
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
        if parsed.tzinfo is not None:
            return parsed.timestamp()
    except ValueError:
        parsed = None

    for fmt in _FORMATS:
        try:
            naive = datetime.strptime(text, fmt)
        except ValueError:
            continue
        if naive_is == UTC:
            return naive.replace(tzinfo=timezone.utc).timestamp()
        # A naive datetime's .timestamp() interprets it as local time, which is
        # what we want AND what gets DST right for the date in question — an
        # offset taken from "now" would be wrong for a timestamp written on the
        # other side of a clock change.
        return naive.timestamp()
    return None


__all__ = ["LOCAL", "UTC", "now_str", "to_epoch"]
