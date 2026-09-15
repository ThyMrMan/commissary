"""A track picked in the Fix dialog, saved with what a download needs.

The Fix dialog searches every metadata source at once, so a pick can come from a
source other than the main one. A download fills in what a match lacks
(core/downloads/track_metadata_backfill.py): its track number through
``spotify_client.get_track_details(id)``, which hands a number-only id to the main
source's client, and a lean album (no release date or track count) from the main
source by album id. Deezer and iTunes ids are plain numbers, so a Deezer pick
under an iTunes main source would be tagged from whatever iTunes files under that
number.

So an iTunes or Deezer pick is completed from its own source before it is saved:
its track number, disc number and album id, release date and track count. iTunes
answers from the search result it cached; Deezer from the track and its album. A
Spotify id still reaches Spotify at download time, and a MusicBrainz id reaches no
other source, so those picks are saved as they come.
"""

from __future__ import annotations

import logging
from typing import Any, Callable, Dict

logger = logging.getLogger(__name__)

COMPLETED_SOURCES = ('itunes', 'deezer')


def _positive_int(value: Any) -> int:
    try:
        number = int(value)
    except (TypeError, ValueError):
        return 0
    return number if number > 0 else 0


def pick_has_details(pick: Any) -> bool:
    """True when a pick carries its track number and its album's release date and
    track count -- everything a download would otherwise look up."""
    if not isinstance(pick, dict):
        return False
    album = pick.get('album') if isinstance(pick.get('album'), dict) else {}
    return bool(_positive_int(pick.get('track_number'))
                and (pick.get('release_date') or album.get('release_date'))
                and _positive_int(pick.get('total_tracks') or album.get('total_tracks')))


def complete_fix_pick(pick: Any, client_for_source: Callable[[str], Any]) -> Any:
    """``pick`` completed from its own source, as a copy (see the module docstring).

    Unchanged when it already has its details, when its source isn't iTunes or
    Deezer, or when the lookup fails or answers for another track. Never raises.
    """
    if not isinstance(pick, dict) or pick_has_details(pick):
        return pick
    source = str(pick.get('source') or '').lower()
    pick_id = str(pick.get('id') or '')
    if source not in COMPLETED_SOURCES or not pick_id:
        return pick
    try:
        client = client_for_source(source)
        details = client.get_track_details(pick_id) if client is not None else None
        if not isinstance(details, dict) or str(details.get('id') or '') != pick_id:
            return pick
        album = details.get('album') if isinstance(details.get('album'), dict) else {}
        album_id = str(album.get('id') or '')
        release_date = str(album.get('release_date') or '').split('T', 1)[0]
        total_tracks = _positive_int(album.get('total_tracks'))
        if source == 'deezer' and album_id and not (release_date and total_tracks):
            # Deezer's track answer names its album but not the album's track count.
            full_album = client.get_album_raw(album_id)
            if isinstance(full_album, dict):
                release_date = release_date or str(full_album.get('release_date') or '')
                total_tracks = total_tracks or _positive_int(full_album.get('nb_tracks'))
        found = {
            'track_number': _positive_int(details.get('track_number')),
            'disc_number': _positive_int(details.get('disc_number')),
            'album_id': album_id,
            'release_date': release_date,
            'total_tracks': total_tracks,
            'album_type': album.get('album_type') or '',
        }
    except Exception as exc:
        logger.debug("[Fix pick] completing %s %s failed: %s", source, pick_id, exc)
        return pick
    completed = dict(pick)
    for key, value in found.items():
        if value and not pick.get(key):
            completed[key] = value
    return completed


def fix_pick_album(pick: Dict[str, Any]) -> Dict[str, Any]:
    """The album a saved pick carries: its name and artwork and, when the pick
    knows them, its release date, track count and type.

    The album id goes with them only once the release date and the track count are
    both there: a lean album is looked up by id on the main source at download
    time, and this id belongs to the pick's own source.
    """
    image_url = pick.get('image_url') or ''
    album_raw = pick.get('album', '')
    album = dict(album_raw) if isinstance(album_raw, dict) else {'name': album_raw or ''}
    for key in ('release_date', 'total_tracks', 'album_type'):
        if pick.get(key) and not album.get(key):
            album[key] = pick[key]
    if (pick.get('album_id') and not album.get('id')
            and album.get('release_date') and _positive_int(album.get('total_tracks'))):
        album['id'] = str(pick['album_id'])
    if image_url and not album.get('image_url'):
        album['image_url'] = image_url
    if image_url and not album.get('images'):
        album['images'] = [{'url': image_url}]
    return album


def fix_pick_numbers(pick: Dict[str, Any]) -> Dict[str, int]:
    """The pick's track and disc number, when it knows them."""
    numbers = {}
    for key in ('track_number', 'disc_number'):
        value = _positive_int(pick.get(key))
        if value:
            numbers[key] = value
    return numbers
