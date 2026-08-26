"""Did this candidate come back censored when you asked for the explicit cut?

"Prefer explicit versions" (``content_filter.prefer_explicit``) has existed
since #923, but only for Soulseek: ``matching_engine.detect_version_type`` reads
``(clean)`` / ``censored`` / ``edited version`` out of a FILENAME and reshapes
the ranking around it. Every structured source — Deezer, Tidal, Qobuz, HiFi,
Amazon — skips that entirely, and says so out loud in ``validation.py``:

    # Tidal/Qobuz/HiFi/Deezer have structured metadata; don't fall back to
    # filename matching

Which is right, and left the preference doing nothing for them. The irony is
that those sources give the AUTHORITATIVE answer and it was being thrown away:
Deezer returns ``explicit_lyrics`` on every track, parsed as far as
``deezer_client.py`` and then dropped before the candidate was built. So the app
held both facts — what you asked for, and what it was about to fetch — and never
compared them.

THE FLAG ONLY MEANS SOMETHING RELATIVE TO WHAT YOU ASKED FOR

That is the whole design, and getting it wrong would be far worse than the gap.
``explicit=False`` on a candidate almost always means "this song has no explicit
content", NOT "this is the censored cut of a song that has one". Penalising
every False would sink the correct result for the overwhelming majority of music
— a catastrophic misread of an honest flag.

So the question is never "is this explicit?" but "does this DISAGREE with the
track I asked for?", and it is only ever asked when the wanted track is itself
explicit. When it isn't — or when nobody said — the question does not arise and
nothing is adjusted.

RANKING, NEVER FILTERING

A censored cut still downloads when it is all that exists. The caller applies
:func:`adjust` to its SORT and leaves its acceptance threshold reading the true
match confidence, so a penalty can reorder candidates but can never drop one
below the gate and make it vanish. That mirrors what the Soulseek path already
promises: "a clean edit still matches (never skipped) when it's all that's on
offer".

Pure: no config, no I/O. The caller passes ``enabled``.
"""

from __future__ import annotations

import re
from typing import Any, Optional

MATCH = "match"          # agrees with the explicit track that was asked for
CENSORED = "censored"    # asked for explicit, this is the clean cut
UNKNOWN = "unknown"      # nothing to go on, or the question doesn't arise

# Mirrors the Soulseek reshaping in matching_engine (-0.05 boost / +0.10 sink)
# so the two paths rank the same way rather than each inventing a scale.
_BOOST = 0.05
_SINK = 0.10

# Title markers, for sources that report no flag at all (torrent/usenet release
# names, YouTube uploads). Deliberately bracket/dash-bound and phrase-anchored,
# the same discipline detect_version_type uses — a song called "Mr. Clean" or an
# album called "Clean Bandit" must never read as censored.
_CLEAN_RE = re.compile(
    r"\(clean\)|\[clean\]|[-–—]\s*clean\b|\bclean\s+version\b|\bcensored\b"
    r"|\bedited\s+version\b|\bradio\s+edit\b", re.IGNORECASE)
_EXPLICIT_RE = re.compile(r"\(explicit\)|\[explicit\]|[-–—]\s*explicit\b"
                          r"|\bexplicit\s+version\b", re.IGNORECASE)


def _flag(obj: Any, *names: str) -> Optional[bool]:
    """A tri-state flag off an object or its ``_source_metadata``.

    None is a real answer here — "this source didn't say" is different from
    "this source said no", and collapsing the two is exactly the misread this
    module exists to avoid."""
    for name in names:
        value = getattr(obj, name, None)
        if value is not None:
            return bool(value)
    meta = getattr(obj, "_source_metadata", None)
    if isinstance(meta, dict):
        for name in names:
            if meta.get(name) is not None:
                return bool(meta.get(name))
    if isinstance(obj, dict):
        for name in names:
            if obj.get(name) is not None:
                return bool(obj.get(name))
    return None


def wanted_is_explicit(wanted: Any) -> Optional[bool]:
    """Whether the track being searched for is itself explicit."""
    return _flag(wanted, "explicit", "explicit_lyrics")


def candidate_is_explicit(candidate: Any) -> Optional[bool]:
    """Whether a candidate is the explicit cut, per the source's own flag."""
    return _flag(candidate, "explicit", "explicit_lyrics")


def _marker_verdict(text: Any) -> str:
    """A last resort for sources with no flag — read the release name."""
    text = str(text or "")
    if not text:
        return UNKNOWN
    if _CLEAN_RE.search(text):
        return CENSORED
    if _EXPLICIT_RE.search(text):
        return MATCH
    return UNKNOWN


def verdict(wanted: Any, candidate: Any) -> str:
    """``MATCH`` | ``CENSORED`` | ``UNKNOWN`` for one candidate.

    UNKNOWN whenever the wanted track is not known to be explicit — including
    when nobody said. Preferring the explicit cut of a song that has no explicit
    cut is not a preference, it is noise."""
    if wanted_is_explicit(wanted) is not True:
        return UNKNOWN
    flag = candidate_is_explicit(candidate)
    if flag is True:
        return MATCH
    if flag is False:
        return CENSORED
    # No flag from this source: fall back to whatever the name says. This is
    # what brings torrent/usenet and YouTube into the preference at all — they
    # have a release title and nothing else.
    return _marker_verdict(getattr(candidate, "title", None)
                           or getattr(candidate, "filename", None))


def adjust(confidence: Any, decision: str, *, enabled: bool) -> float:
    """The confidence a SORT should use. Never call this on the value your
    acceptance threshold reads — see the module docstring."""
    # An unusable confidence gets no adjustment at all. `float(None or 0.0)`
    # would quietly succeed and then hand a nonexistent score a boost, which is
    # arithmetic on nothing dressed up as a preference.
    if confidence is None:
        return 0.0
    try:
        base = float(confidence)
    except (TypeError, ValueError):
        return 0.0
    if not enabled or decision == UNKNOWN:
        return base
    if decision == MATCH:
        base += _BOOST
    elif decision == CENSORED:
        base -= _SINK
    return max(0.0, min(1.0, base))


def summarize(decisions) -> str:
    """One line for the log when a search finishes.

    Worth saying out loud specifically when EVERY surviving candidate is the
    clean cut: the download will still happen, it will look completely normal,
    and the file will quietly be the censored one. That is the case the app
    could previously never report even though it held both facts."""
    decisions = list(decisions or [])
    if not decisions:
        return ""
    censored = decisions.count(CENSORED)
    matched = decisions.count(MATCH)
    if censored and not matched:
        return ("you asked for the explicit version and every candidate is the "
                "clean cut (%d of %d) — taking it anyway, there is nothing else"
                % (censored, len(decisions)))
    if censored:
        return "%d explicit, %d clean-cut candidate(s) — ranking explicit first" % (
            matched, censored)
    return ""


__all__ = ["MATCH", "CENSORED", "UNKNOWN", "wanted_is_explicit",
           "candidate_is_explicit", "verdict", "adjust", "summarize"]
