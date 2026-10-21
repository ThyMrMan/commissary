"""Discovery fixes that were quietly lost (old fork 2.3.9, the parts upstream
3.5.7 still has).

Each function is the body of one small hook in upstream's discovery routes;
the docstring says what was lost and why.
"""

from __future__ import annotations

from typing import Any, Dict, Optional, Tuple


def _first_artist(artists: Any) -> str:
    if not isinstance(artists, list) or not artists:
        return ""
    first = artists[0]
    return str(first.get("name") or "") if isinstance(first, dict) else str(first or "")


def fix_source_key(state: Dict[str, Any], track_index: Any, data: Dict[str, Any],
                   result: Dict[str, Any], picked: Dict[str, Any]) -> Tuple[str, str]:
    """``(name, artist)`` a YouTube / YouTube Music / mirrored Fix is cached under.

    The routes read the source track from a ``youtube_track`` field no discovery
    result has, so every such fix was cached under the PICKED song's title with
    no artist - a key no discovery ever looks up, and the fix was lost on the
    next discovery. Discovery (core/discovery/youtube.py) looks a track up by
    the playlist track's own name and first artist, "Unknown Artist" when it has
    none; this returns exactly that. The playlist track is found by the result's
    own ``index`` (the worker skips tracks, so the result's position isn't the
    track's); failing that, the names the page sent; failing that, upstream's
    old fallback.
    """
    tracks = (state.get("playlist") or {}).get("tracks") or state.get("tracks") or []
    index = result.get("index", track_index) if isinstance(result, dict) else track_index
    source: Optional[Dict[str, Any]] = None
    if isinstance(index, int) and 0 <= index < len(tracks) and isinstance(tracks[index], dict):
        source = tracks[index]
    if source and source.get("name"):
        return str(source["name"]), _first_artist(source.get("artists")) or "Unknown Artist"
    sent_name = str((data or {}).get("original_name") or "").strip()
    if sent_name:
        return sent_name, str((data or {}).get("original_artist") or "").strip() or "Unknown Artist"
    return str(picked.get("name") or ""), ""


_SOURCE_TRACK_KEYS = ("tidal_track", "source_track", "deezer_track", "qobuz_track", "beatport_track")


def result_source_track(result: Dict[str, Any]) -> Dict[str, Any]:
    """A tab discovery result's own playlist track, whichever key its worker used.

    The mirrored write-back read only 'tidal_track' / 'source_track', so a Deezer
    or Qobuz tab's results never matched their mirrored track by id and fell to
    the position fallback - which was off by one."""
    for key in _SOURCE_TRACK_KEYS:
        value = result.get(key)
        if isinstance(value, dict) and value:
            return value
    return {}


def is_wing_it_guess(extra_data: Any) -> bool:
    """A Wing It stub still standing in for a match: no source matched it, so its
    provider is 'wing_it_fallback'. extra_data is merged on save, so a track
    matched later can still carry the flag; its provider or manual_match says so."""
    return (isinstance(extra_data, dict) and bool(extra_data.get("wing_it_fallback"))
            and extra_data.get("provider") == "wing_it_fallback"
            and not extra_data.get("manual_match"))


def never_drifts(extra_data: Any) -> bool:
    """A mirrored track with no metadata source to drift from.

    Upstream's ``is_drifted_for_redo`` compared every entry's provider with the
    active one, so a Wing It guess (provider 'wing_it_fallback') reopened as
    "Provider changed" with nothing in it, and a playlist of only guesses
    reopened as never discovered. Nor can a track the user unmatched, or marked
    "Not available", drift: it waits for the user, not for a source."""
    if not isinstance(extra_data, dict):
        return False
    if is_wing_it_guess(extra_data):
        return True
    if not extra_data.get("discovered") and (
            extra_data.get("unmatched_by_user") or extra_data.get("unavailable")):
        return True
    return False


def as_wing_it_row(row: Dict[str, Any], track: Dict[str, Any], provider: str) -> Dict[str, Any]:
    """A reopened mirrored Wing It guess as the guess it is - not as "Found",
    and not as an empty "Provider changed" row (its stub stays with it)."""
    artists = track.get("artists") or []
    first = _first_artist(artists)
    row.update({
        "status": "Wing It", "status_class": "wing-it",
        "spotify_track": track.get("name", ""), "spotify_artist": first, "spotify_album": "",
        "discovery_source": provider, "confidence": 0, "wing_it_fallback": True,
    })
    return row


def cache_entry(database: Any, entry_id: Any) -> Optional[Dict[str, Any]]:
    """One discovery-cache row - its key, provider and original names - or None."""
    try:
        conn = database._get_connection()
        cursor = conn.cursor()
        cursor.execute(
            "SELECT normalized_title, normalized_artist, provider, original_title, original_artist "
            "FROM discovery_match_cache WHERE id = ?", (entry_id,))
        row = cursor.fetchone()
    except Exception:  # noqa: BLE001 - no entry is the caller's old path
        return None
    if not row:
        return None
    keys = ("normalized_title", "normalized_artist", "provider", "original_title", "original_artist")
    return {key: row[i] for i, key in enumerate(keys)}


def rematch_key(entry: Optional[Dict[str, Any]], engine: Any, original_title: str,
                original_artist: str, active_provider: str) -> Dict[str, str]:
    """Where a Discovery Pool Rematch saves the new match.

    Upstream saved it under ``normalize_string`` of the names, but discovery
    looks the cache up by ``clean_title`` / ``clean_artist``
    (``_get_discovery_cache_key``), so a rematch was never read back. The
    replaced entry's own key and provider are exactly where the discovery that
    found the old match looks; without one, the lookup's own normalization."""
    if entry and entry.get("normalized_title") is not None:
        return {"normalized_title": entry["normalized_title"],
                "normalized_artist": entry["normalized_artist"] or "",
                "provider": entry["provider"] or active_provider,
                "original_title": entry.get("original_title") or original_title,
                "original_artist": entry.get("original_artist") or original_artist}
    return {"normalized_title": engine.clean_title(original_title) if original_title else "",
            "normalized_artist": engine.clean_artist(original_artist) if original_artist else "",
            "provider": active_provider,
            "original_title": original_title, "original_artist": original_artist}


# The tabs whose discovery workers count a Wing It guess as a match
# (state['spotify_matches'] = successful_discoveries, guesses included). YouTube,
# YouTube Music, mirrored playlists and ListenBrainz count only real matches.
GUESSES_COUNT_AS_MATCHES = frozenset({"tidal", "deezer", "qobuz", "spotify_public", "spotify-public",
                                      "itunes_link", "itunes-link", "beatport"})
_FOUND = ("found", "Found")
_GUESS = ("wing-it", "Wing It")


def counts_guesses(platform: str) -> bool:
    return str(platform or "").lower() in GUESSES_COUNT_AS_MATCHES


def fix_adds_a_match(old_status: Any, platform: str) -> bool:
    """Whether fixing a row raises the match count. A guess the tab already
    counted is not a new match - the tab Fix routes counted it twice."""
    if old_status in _FOUND:
        return False
    return not (old_status in _GUESS and counts_guesses(platform))


def unmatch_removes_a_match(old_status: Any, platform: str) -> bool:
    """Whether unmatching a row lowers the match count: a real match always, a
    guess only where the tab counted it (unmatch lowered it everywhere)."""
    if old_status in _FOUND:
        return True
    return old_status in _GUESS and counts_guesses(platform)


def platform_of_path(path: str) -> str:
    """'deezer' for /api/deezer/discovery/unmatch, and so on."""
    parts = [p for p in str(path or "").split("/") if p]
    return parts[1] if len(parts) > 1 and parts[0] == "api" else ""
