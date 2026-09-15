"""Exact matches by ISRC for playlist discovery.

A playlist track that carries an ISRC -- Qobuz sends one with every track --
names one recording exactly. Deezer looks tracks up by ISRC for free, so a hit
there is the match, whichever metadata source discovery otherwise searches
(iTunes has no ISRC lookup at all).

The match is built with its full album -- track count and release date -- and
its position on it. Anything less gets filled in at download time from the main
metadata source using this album's id, which would hand a Deezer id to another
source's client; so without them the ISRC isn't used, and discovery searches as
it always did.
"""

from __future__ import annotations

import logging
from typing import Any, Optional

from core.text.isrc import normalize_isrc

logger = logging.getLogger(__name__)

ISRC_MATCH_SOURCE = 'deezer'
# Both Qobuz and Deezer give lengths in whole seconds. A bigger gap means the
# code points at another cut of the song, or the source's code is wrong.
ISRC_DURATION_TOLERANCE_MS = 5000
_ALBUM_TYPES = {'compile': 'compilation'}


def resolve_isrc_track(isrc: Any, duration_ms: Any, deezer_client: Any) -> Optional[dict]:
    """Discovery ``matched_data`` for the Deezer recording with this ISRC, or None.

    None when the code isn't a valid ISRC, Deezer doesn't know it, the answer
    carries a different ISRC, its length is more than 5 s off the playlist
    track's, or its album's track count, its release date or its position on the
    album can't be had. Never raises.
    """
    code = normalize_isrc(isrc)
    if not code or deezer_client is None:
        return None
    try:
        track = deezer_client.get_track_by_isrc(code)
        if not isinstance(track, dict) or not track.get('id'):
            return None
        if normalize_isrc(track.get('isrc')) not in ('', code):
            return None
        track_ms = int(track.get('duration') or 0) * 1000
        source_ms = int(duration_ms or 0)
        if track_ms and source_ms and abs(track_ms - source_ms) > ISRC_DURATION_TOLERANCE_MS:
            logger.info("[ISRC] %s runs %d ms on Deezer but %d ms in the playlist -- not used",
                        code, track_ms, source_ms)
            return None
        album = track.get('album') if isinstance(track.get('album'), dict) else {}
        full_album = deezer_client.get_album_raw(album['id']) if album.get('id') else None
        return _matched_data(code, track, album, full_album if isinstance(full_album, dict) else {})
    except Exception as exc:
        logger.debug("[ISRC] lookup for %s failed: %s", code, exc)
        return None


def _matched_data(code: str, track: dict, album: dict, full_album: dict) -> Optional[dict]:
    total_tracks = int(full_album.get('nb_tracks') or 0)
    release_date = full_album.get('release_date') or album.get('release_date') or track.get('release_date') or ''
    # Without its position a download looks the track up again, handing this Deezer id
    # to the main source's client -- the lookup the full album is there to avoid.
    track_number = int(track.get('track_position') or 0)
    if not total_tracks or not release_date or track_number <= 0:
        return None
    artist = track.get('artist') if isinstance(track.get('artist'), dict) else {}
    names = [c.get('name') for c in (track.get('contributors') or []) if isinstance(c, dict) and c.get('name')]
    if not names and artist.get('name'):
        names = [artist['name']]
    album_artist = full_album.get('artist') if isinstance(full_album.get('artist'), dict) else {}
    cover = album.get('cover_xl') or full_album.get('cover_xl') or album.get('cover_big') or ''
    record_type = full_album.get('record_type') or 'album'
    return {
        'id': str(track['id']),
        'name': track.get('title') or '',
        'artists': [{'name': name} for name in names],
        'album': {
            'id': str(album.get('id') or full_album.get('id') or ''),
            'name': album.get('title') or full_album.get('title') or '',
            'release_date': release_date,
            'total_tracks': total_tracks,
            'album_type': _ALBUM_TYPES.get(record_type, record_type),
            'images': [{'url': cover}] if cover else [],
            'image_url': cover,
            'artists': [{'name': album_artist.get('name') or (names[0] if names else '')}],
        },
        'duration_ms': int(track.get('duration') or 0) * 1000,
        'image_url': cover,
        'source': ISRC_MATCH_SOURCE,
        'track_number': track_number,
        'disc_number': track.get('disk_number') or None,
        'isrc': code,
    }
