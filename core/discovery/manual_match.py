"""Helpers for Fix-popup manual match persistence.

When the user manually fixes a mirrored-playlist discovery via the Fix
popup, two questions land at the web_server route layer that are easier
to test in isolation:

1. *Which metadata source did the manual match come from?* — the popup
   cascade queries the user's primary source first, then Spotify /
   Deezer / iTunes / MusicBrainz as fallbacks; each search endpoint
   stamps `source` on its rows but the MBID-paste lookup uses a lean
   flat shape that doesn't carry it. `derive_manual_match_provider`
   collapses the fallback chain into a single string.

2. *Should the discovery layer re-run for this track when the current
   active provider differs from the cached one?* — re-running silently
   overwrites the user's deliberate pick with whatever the auto-search
   ranks first, so manual matches are exempt regardless of provider
   drift. `is_drifted_for_redo` encapsulates the decision.

3. *Should the Playlist Pipeline pre-scan (re)discover this track at all?*
   — `should_rediscover` encapsulates that gate, with the manual match
   checked FIRST so a leftover Wing It flag can't override the user's pick.

4. *Did the user mark the track "Not available"?* — `is_marked_unavailable`.
   No source has it, so every discovery pass leaves it alone until a match
   is saved for it.
"""

from __future__ import annotations

from typing import Any, Dict, Optional


def derive_manual_match_provider(
    payload_track: Dict[str, Any],
    active_provider: Optional[str],
) -> str:
    """Return the provider string to stamp on a manually-fixed match.

    Resolution order:
        1. ``payload_track['source']`` — every *_search_tracks endpoint
           sets this; the MBID-paste path doesn't.
        2. ``active_provider`` — what the user has configured as their
           primary discovery source.
        3. ``'spotify'`` — last-ditch default matching the historic
           hardcode (so behaviour is identical when both upstream
           signals are absent).
    """
    if not isinstance(payload_track, dict):
        payload_track = {}
    source = payload_track.get('source')
    if source:
        return source
    if active_provider:
        return active_provider
    return 'spotify'


def is_wing_it_guess(extra_data: Optional[Dict[str, Any]]) -> bool:
    """Return True for a Wing It stub still standing in for a match.

    No metadata source matched the track, so its provider is
    ``'wing_it_fallback'`` -- it belongs to no source. ``extra_data`` is merged on
    save, so a track matched or fixed later can still carry the old
    ``wing_it_fallback`` flag; its provider (or ``manual_match``) says otherwise.
    """
    if not isinstance(extra_data, dict):
        return False
    return (bool(extra_data.get('wing_it_fallback'))
            and extra_data.get('provider') == 'wing_it_fallback'
            and not extra_data.get('manual_match'))


def is_marked_unavailable(extra_data: Optional[Dict[str, Any]]) -> bool:
    """Return True for a track the user marked "Not available".

    No metadata source has it, so discovery leaves it alone: it is never
    re-discovered, never provider drift, and a discovery pass keeps its row
    without searching. ``extra_data`` is merged on save, so a match saved later
    can still carry the flag; ``discovered`` says the track has a match now.
    """
    if not isinstance(extra_data, dict):
        return False
    return bool(extra_data.get('unavailable')) and not extra_data.get('discovered')


def unavailable_result_row(index: int, track_name: str, artist_name: str,
                           duration_ms: Any = 0) -> Dict[str, Any]:
    """The discovery row for a track marked "Not available": no match, so
    nothing syncs or downloads it."""
    try:
        dur = int(duration_ms or 0)
    except (TypeError, ValueError):
        dur = 0
    return {
        'index': index,
        'yt_track': track_name,
        'yt_artist': artist_name,
        'status': 'Not available',
        'status_class': 'unavailable',
        'unavailable': True,
        'spotify_track': '',
        'spotify_artist': '',
        'spotify_album': '',
        'duration': f"{dur // 60000}:{(dur % 60000) // 1000:02d}" if dur else '0:00',
        'confidence': 0,
    }


def is_drifted_for_redo(
    extra_data: Optional[Dict[str, Any]],
    active_provider: Optional[str],
) -> bool:
    """Return True when a cached discovery entry should be treated as
    stale because the user's active provider has changed since it was
    cached AND the entry isn't a manual match.

    Manual matches are *always* considered fresh: re-running discovery
    against the current source would overwrite the user's deliberate
    pick with whatever auto-search ranks first. The first Playlist
    Pipeline run after a manual fix used to clobber it for exactly
    this reason — the check lives here now so it's pinned by tests.
    """
    if not isinstance(extra_data, dict):
        return False
    if extra_data.get('manual_match'):
        return False
    # An exact ISRC match is the recording itself, looked up on Deezer whatever the
    # discovery source; no source switch can improve on it.
    if extra_data.get('isrc_match'):
        return False
    # A Wing It guess matched no source, so there is no source for it to drift from.
    if is_wing_it_guess(extra_data):
        return False
    # Nor did a track the user marked "Not available".
    if is_marked_unavailable(extra_data):
        return False
    # A track the user unmatched waits for them, not for a source.
    if extra_data.get('unmatched_by_user') and not extra_data.get('discovered'):
        return False
    cached_provider = extra_data.get('provider', 'spotify')
    return cached_provider != active_provider


def should_rediscover(extra_data: Optional[Dict[str, Any]]) -> bool:
    """Return True when a mirrored track needs (re)discovery, False to skip it.

    This is the gate the Playlist Pipeline pre-scan runs over every mirrored
    track before discovering. The **ordering is the fix**: a manual match is
    authoritative and is checked FIRST.

    ``extra_data`` is *merged* on save (see ``update_mirrored_track_extra_data``),
    so a track that was a Wing It stub and is then manually fixed still carries
    ``wing_it_fallback: True`` alongside the new ``manual_match: True``. The old
    pre-scan tested ``wing_it_fallback`` before ``manual_match``, so the stale
    flag won and the pipeline re-discovered the track — silently reverting the
    user's pick to Wing It. Checking ``manual_match`` first makes the fix stick.

    Decision order:
      * manual_match            -> skip   (authoritative; never re-discover)
      * isrc_match              -> skip   (the exact recording; nothing to improve)
      * wing_it_fallback        -> redo   (stub — keep trying for a real match)
      * discovered + complete   -> skip   (full metadata already stored)
      * discovered + incomplete -> redo   (backfill track_number / album fields)
      * unavailable             -> skip   (the user marked it "Not available")
      * unmatched_by_user       -> skip   (user deliberately removed the match)
      * never discovered        -> redo   (first-time discovery)
    """
    extra = extra_data if isinstance(extra_data, dict) else {}

    if extra.get('discovered'):
        if extra.get('manual_match'):
            return False
        if extra.get('isrc_match'):
            return False
        if extra.get('wing_it_fallback'):
            return True
        # Otherwise re-discover only when the stored match is missing the
        # enriched fields (track_number + release_date/album.id) that older
        # discoveries dropped via the Track dataclass.
        matched = extra.get('matched_data')
        matched = matched if isinstance(matched, dict) else {}
        album = matched.get('album')
        album = album if isinstance(album, dict) else {}
        has_track_num = matched.get('track_number')
        has_release = album.get('release_date')
        has_album_id = album.get('id')
        return not (has_track_num and (has_release or has_album_id))

    if extra.get('unavailable'):
        return False
    if extra.get('unmatched_by_user'):
        return False
    return True
