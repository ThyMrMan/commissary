"""Files an album's titles can't pair, paired by their place in it and their length.

A title in another language scores ~0 against the tracklist, so a Japanese-
titled file paired only through its track number - and not at all when a
multi-disc release is numbered straight through (file 41 is disc 2, track 1).
Position alone is too weak to trust; a file and a track at the same place in
the album whose lengths agree within a few seconds are the same song, once
other places confirm the numbering. A language version is a different
recording whatever else agrees (language_versions.py). Old fork 2.x, from a
real import of a 40 + 44 track soundtrack numbered 01-84 and titled in Japanese.

Two users:

- album import (``pair_by_position_and_duration``): after the title scorer, what
  it left over pairs by place, with the whole tracklist to count disc offsets;
- staging (``staged_file_by_place``): a batch's missing track nothing claimed by
  name. The batch holds only the missing tracks, not the whole tracklist, so a
  straight-through offset is read off the staged files and accepted only when
  other tracks of the batch agree on it by length.
"""

from __future__ import annotations

import os
import re
from typing import Any, Callable, Dict, List, Optional, Tuple

from core.commissary.language_versions import file_language, first_language
from utils.logging_config import get_logger

logger = get_logger("commissary.position_pairing")

POSITION_DURATION_TOLERANCE_MS = 3000
# Recorded for an album-import pair: above album_matching.MATCH_THRESHOLD, below
# the 0.8 at which a single auto-import match carries a whole album on its own.
POSITION_DURATION_CONFIDENCE = 0.7

_SPACED_NUMBER_RE = re.compile(r'^\s*(\d{1,3})\s+\S')
_DISC_FOLDER_RE = re.compile(r'^(?:disc|cd|disk)\s*(\d+)$', re.IGNORECASE)


def _positive_int(value: Any) -> int:
    try:
        number = int(str(value).split('/')[0].strip())
    except (TypeError, ValueError):
        return 0
    return number if number > 0 else 0


def _positive_ms(value: Any) -> int:
    try:
        ms = int(float(value or 0))
    except (TypeError, ValueError):
        return 0
    return ms if ms > 0 else 0


def durations_agree(file_duration_ms: Any, track_duration_ms: Any,
                    tolerance_ms: int = POSITION_DURATION_TOLERANCE_MS) -> bool:
    """True when both lengths are known and within ``tolerance_ms`` of each other."""
    a, b = _positive_ms(file_duration_ms), _positive_ms(track_duration_ms)
    return a > 0 and b > 0 and abs(a - b) <= tolerance_ms


def file_track_number(file_path: str, tag_number: Any = 0) -> int:
    """A file's track number: its tag, else the number its filename starts with -
    "01 - Title" and "1-03 Title", and also the bare "41 常夏の島" that
    ``extract_explicit_track_number`` leaves alone."""
    number = _positive_int(tag_number)
    if number:
        return number
    from core.imports.filename import extract_explicit_track_number
    number = extract_explicit_track_number(file_path or '')
    if number:
        return number
    match = _SPACED_NUMBER_RE.match(os.path.splitext(os.path.basename(file_path or ''))[0])
    return int(match.group(1)) if match else 0


def file_disc_number(file_path: str, tags: Dict[str, Any]) -> int:
    """The file's disc: its tag, else the disc folder it sits in ("CD2"), else 1."""
    disc = _positive_int(tags.get('disc_number')) or 1
    if disc <= 1:
        folder = _DISC_FOLDER_RE.match(os.path.basename(os.path.dirname(file_path or '')))
        if folder:
            return max(int(folder.group(1)), 1)
    return disc


# ── album import ────────────────────────────────────────────────────────────

def pair_by_position_and_duration(
    audio_files: List[str],
    file_tags: Dict[str, Dict[str, Any]],
    tracks: List[Dict[str, Any]],
    *,
    target_album: str,
    matches: List[Dict[str, Any]],
) -> List[Dict[str, Any]]:
    """Pair the files ``matches`` left over with the tracks it left over, by
    their place in the album, where their lengths agree.

    A track's place is its position counted through the discs: disc 2, track 1
    of a 40 + 44 track album is 41. A file's place is its number - the tag, or
    one its filename starts with - read per disc when the file names a disc (a
    tag, or a "CD2" folder) and the number fits that disc, and straight through
    otherwise.

    Position is trusted only once the numbering is confirmed: at least two pairs
    in all sit at the same place (the matches already made count), and most of
    the leftover places agree on length. A place pairs only with a single file
    that agrees, and different language versions never pair. Returns the new
    matches, each with ``'match_type': 'position_duration'``."""
    from core.imports.album_matching import _extract_track_disc, _track_duration_ms

    disc_lengths: Dict[int, int] = {}
    for track in tracks:
        number = _positive_int(track.get('track_number'))
        if number:
            disc = _extract_track_disc(track)
            disc_lengths[disc] = max(disc_lengths.get(disc, 0), number)
    if not disc_lengths:
        return []
    offsets: Dict[int, int] = {}
    total = 0
    for disc in sorted(disc_lengths):
        offsets[disc] = total
        total += disc_lengths[disc]

    def track_place(track: Dict[str, Any]) -> int:
        number = _positive_int(track.get('track_number'))
        disc = _extract_track_disc(track)
        return offsets[disc] + number if number and disc in offsets else 0

    def file_place(file_path: str) -> int:
        tags = file_tags.get(file_path, {})
        number = file_track_number(file_path, tags.get('track_number'))
        if not number:
            return 0
        disc = file_disc_number(file_path, tags)
        if disc > 1:
            if disc not in offsets:
                return 0
            if number <= disc_lengths[disc]:
                return offsets[disc] + number
        return number if number <= total else 0

    matched_files = {m['file'] for m in matches}
    matched_tracks = {id(m['track']) for m in matches}
    confirmed = sum(1 for m in matches
                    if track_place(m['track']) and file_place(m['file']) == track_place(m['track']))

    files_at: Dict[int, List[str]] = {}
    for file_path in audio_files:
        if file_path not in matched_files:
            place = file_place(file_path)
            if place:
                files_at.setdefault(place, []).append(file_path)

    placed = 0
    agreeing: List[Tuple[Dict[str, Any], str]] = []
    taken = set()
    for track in tracks:
        if id(track) in matched_tracks:
            continue
        candidates = files_at.get(track_place(track)) or []
        if not candidates:
            continue
        placed += 1
        track_ms = _track_duration_ms(track)
        language = first_language(track.get('name'), target_album)
        fits = [f for f in candidates
                if f not in taken
                and durations_agree(file_tags.get(f, {}).get('duration_ms'), track_ms)
                and file_language(f, file_tags.get(f, {}).get('title'),
                                  file_tags.get(f, {}).get('album')) == language]
        if len(fits) == 1:
            agreeing.append((track, fits[0]))
            taken.add(fits[0])

    if len(agreeing) + confirmed < 2 or len(agreeing) * 2 < placed:
        return []
    return [{'track': track, 'file': file_path,
             'confidence': POSITION_DURATION_CONFIDENCE, 'match_type': 'position_duration'}
            for track, file_path in agreeing]


def add_position_pairs(audio_files, file_tags, tracks, *, target_album, matches, used_files) -> None:
    """``pair_by_position_and_duration`` folded into the matcher's own lists."""
    paired = pair_by_position_and_duration(
        audio_files, file_tags, tracks, target_album=target_album, matches=matches)
    if paired:
        matches.extend(paired)
        used_files.update(m['file'] for m in paired)
        logger.info("[Album Matching] '%s': paired %d file(s) by their place in the album and length",
                    target_album, len(paired))


# ── staging ─────────────────────────────────────────────────────────────────

def _track_place(track_info: Any, fallback_duration_ms: Any = 0) -> Tuple[int, int, int]:
    """``(disc, track number, length in ms)`` of a task's track: 0 where unknown,
    and disc 1 when it isn't said."""
    info = track_info if isinstance(track_info, dict) else {}
    disc = _positive_int(info.get('disc_number')) or _positive_int(info.get('discNumber')) or 1
    number = _positive_int(info.get('track_number')) or _positive_int(info.get('trackNumber'))
    duration = _positive_ms(info.get('duration_ms')) or _positive_ms(fallback_duration_ms)
    return disc, number, duration


def _batch_track_places(batch_id, get_batch_field) -> List[Tuple[int, int, int]]:
    """The places of the batch's queued tracks whose number and length are known."""
    if not batch_id or get_batch_field is None:
        return []
    try:
        queue = list(get_batch_field(batch_id, 'queue') or [])
    except Exception as exc:  # noqa: BLE001 - a missing batch is no pairing
        logger.debug("[Staging] batch queue unavailable for %s: %s", batch_id, exc)
        return []
    from core.runtime_state import download_tasks, tasks_lock
    places = []
    with tasks_lock:
        for queued_id in queue:
            place = _track_place((download_tasks.get(queued_id) or {}).get('track_info'))
            if place[1] and place[2]:
                places.append(place)
    return places


def _corroborated_file(files_at: Callable[[int, int], list], target, others, agree):
    """The one file at ``target``'s place whose length agrees - once the
    numbering is confirmed: another track's place agrees as well, and most of
    the places that have a file do. Returns ``(file, places agreeing)``, or
    ``(None, 0)``."""
    disc, number, duration = target
    fits = [sf for sf in files_at(disc, number) if agree(sf, duration)]
    if len(fits) != 1:
        return None, 0
    checked = agreeing = 1
    for other_disc, other_number, other_duration in others:
        if (other_disc, other_number) == (disc, number):
            continue
        candidates = files_at(other_disc, other_number)
        if not candidates:
            continue
        checked += 1
        if any(agree(sf, other_duration) for sf in candidates):
            agreeing += 1
    if agreeing >= 2 and agreeing * 2 >= checked:
        return fits[0], agreeing
    return None, 0


def staged_file_by_place(task_id, batch_id, track, staging_files, get_batch_field) -> Optional[dict]:
    """The staged file at this track's place in the album whose length agrees,
    for a track nothing claimed by name - a title in another language, say.

    A release is numbered per disc (a disc tag, 1..N on each disc) or straight
    through the discs, so disc 2, track 1 of a 40 + 44 album is file 41. Never
    claims a different language version. Never raises: a failure here is just
    no pairing."""
    try:
        return _staged_file_by_place(task_id, batch_id, track, staging_files, get_batch_field)
    except Exception:  # noqa: BLE001
        logger.exception("[Staging] place-and-length pairing failed for task %s", task_id)
        return None


def _staged_file_by_place(task_id, batch_id, track, staging_files, get_batch_field):
    from core.runtime_state import download_tasks, tasks_lock

    with tasks_lock:
        track_info = (download_tasks.get(task_id) or {}).get('track_info')
    target = _track_place(track_info, getattr(track, 'duration_ms', 0))
    disc, number, duration = target
    if not number or not duration:
        return None
    track_language = first_language(getattr(track, 'name', None), getattr(track, 'album', None))

    def agree(staged_file, duration_ms):
        return durations_agree(staged_file.get('duration_ms'), duration_ms)

    numbered = []
    for sf in staging_files:
        file_number = file_track_number(sf.get('full_path') or '', sf.get('track_number'))
        if (file_number and _positive_ms(sf.get('duration_ms'))
                and file_language(sf.get('full_path'), sf.get('title'), sf.get('album')) == track_language):
            numbered.append((sf, file_number, _positive_int(sf.get('disc_number')) or 1))
    if not numbered:
        return None
    places = _batch_track_places(batch_id, get_batch_field)

    per_disc: Dict[Tuple[int, int], list] = {}
    for sf, file_number, file_disc in numbered:
        per_disc.setdefault((file_disc, file_number), []).append(sf)
    found, _agreeing = _corroborated_file(
        lambda d, n: per_disc.get((d, n), []), target, places, agree)
    if found is not None or disc <= 1:
        return found

    straight: Dict[int, list] = {}
    for sf, file_number, file_disc in numbered:
        if file_disc == 1:
            straight.setdefault(file_number, []).append(sf)
    same_disc = [place for place in places if place[0] == disc]
    offsets = sorted({file_number - number for file_number, files in straight.items()
                      if file_number > number and any(agree(sf, duration) for sf in files)})
    best, best_agreeing = None, 0
    for offset in offsets:
        found, agreeing = _corroborated_file(
            lambda _d, n, offset=offset: straight.get(offset + n, []), target, same_disc, agree)
        if found is not None and agreeing > best_agreeing:
            best, best_agreeing = found, agreeing
    return best
