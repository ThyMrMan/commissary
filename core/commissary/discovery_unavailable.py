"""Mark a discovery track "Not available" (old fork 2.3.9).

A track no metadata source has keeps coming back: Retry Failed and the
Playlist Pipeline search it again, and so does the playlist's next discovery.
The mark clears its match and any Wing It guess, so nothing syncs or
downloads it, and discovery leaves it alone:

- a mirrored playlist keeps the mark in its database (``extra_data``), so the
  YouTube worker (which also runs mirrored playlists and YouTube Music), the
  Pipeline's rediscover gate and reopening the playlist all respect it;
- a YouTube / YouTube Music playlist keeps it on the track while its state lives;
- on the other tabs (Tidal, Deezer, Qobuz, Spotify / iTunes links, Beatport,
  ListenBrainz) it marks the row and lowers the count until that playlist is
  discovered again - their workers are separate copies upstream, and hooking
  each one would be a lot of fork to keep in step.

Fixing the track by hand clears the mark, and so does ``unavailable: false``.
"""

from __future__ import annotations

from typing import Any, Dict, Optional, Tuple

from utils.logging_config import get_logger

logger = get_logger("commissary.discovery_unavailable")

SAVED_MARK = {"unavailable": True, "discovered": False, "discovery_attempted": True,
              "wing_it_fallback": False, "manual_match": False, "matched_data": None, "confidence": 0}
SAVED_CLEAR = {"unavailable": False, "discovered": False, "discovery_attempted": True}

# platform -> (states dict name in api.source_playlists / api.beatport_charts)
_STATES = {
    "youtube": "youtube_playlist_states", "mirrored": "youtube_playlist_states",
    "ytmusic": "ytmusic_discovery_states", "tidal": "tidal_discovery_states",
    "deezer": "deezer_discovery_states", "qobuz": "qobuz_discovery_states",
    "spotify_public": "spotify_public_discovery_states", "spotify-public": "spotify_public_discovery_states",
    "itunes_link": "itunes_link_discovery_states", "itunes-link": "itunes_link_discovery_states",
    "beatport": "beatport_chart_states", "listenbrainz": "listenbrainz_playlist_states",
}


def is_marked(extra_data: Any) -> bool:
    """A saved mark: extra_data is merged on save, so a match saved later can
    still carry the flag - ``discovered`` says the track has a match now."""
    return isinstance(extra_data, dict) and bool(extra_data.get("unavailable")) \
        and not extra_data.get("discovered")


def is_track_unavailable(track: Any) -> bool:
    """A playlist track the user marked, in memory or in its saved extra_data."""
    if not isinstance(track, dict):
        return False
    return bool(track.get("unavailable")) or is_marked(track.get("extra_data"))


def unavailable_row(index: int, track: Dict[str, Any]) -> Dict[str, Any]:
    """The discovery row for a marked track: no match, so nothing syncs or downloads it."""
    artists = track.get("artists") or []
    first = artists[0] if artists else ""
    first = first.get("name", "") if isinstance(first, dict) else str(first or "")
    try:
        dur = int(track.get("duration_ms") or 0)
    except (TypeError, ValueError):
        dur = 0
    return {
        "index": index, "yt_track": track.get("name", ""), "yt_artist": first or "Unknown",
        "status": "Not available", "status_class": "unavailable", "unavailable": True,
        "spotify_track": "", "spotify_artist": "", "spotify_album": "",
        "duration": f"{dur // 60000}:{(dur % 60000) // 1000:02d}" if dur else "0:00",
        "confidence": 0,
    }


def clear_on_fix(state: Dict[str, Any], result: Dict[str, Any]) -> None:
    """A track fixed by hand is available again."""
    tracks = (state.get("playlist") or {}).get("tracks") or []
    index = result.get("index") if isinstance(result, dict) else None
    if isinstance(index, int) and 0 <= index < len(tracks) and isinstance(tracks[index], dict):
        tracks[index]["unavailable"] = False
        extra = tracks[index].get("extra_data")
        if isinstance(extra, dict):
            extra["unavailable"] = False
    if isinstance(result, dict):
        result["unavailable"] = False


def find_state(platform: str, identifier: str) -> Tuple[Optional[Dict[str, Any]], str]:
    """``(state, platform)`` for the page's platform and identifier."""
    platform = str(platform or "").lower()
    if identifier.startswith("mirrored_"):
        platform = "mirrored"
    name = _STATES.get(platform)
    if not name:
        return None, platform
    import api.source_playlists as sp
    states = getattr(sp, name, None)
    if states is None and platform == "beatport":
        import api.beatport_charts as bc
        states = getattr(bc, name, None)
    if not isinstance(states, dict):
        return None, platform
    state = states.get(identifier)
    if state is None and platform == "listenbrainz":
        from core.profile_context import get_current_profile_id
        state = states.get(f"{get_current_profile_id()}:{identifier}")
    return state, platform


def mark(state: Dict[str, Any], platform: str, identifier: str, track_index: int,
         unavailable: bool, database: Any) -> Tuple[Dict[str, Any], int]:
    from core.commissary.discovery_fixes import unmatch_removes_a_match

    if state.get("phase") == "discovering":
        return {"success": False, "error": "Discovery is still running"}, 409
    results = state.get("discovery_results") or []
    # the page's row index: the result's POSITION, except on Beatport and
    # ListenBrainz where it is the result's own index - as the Fix routes read it
    if platform in ("beatport", "listenbrainz"):
        position = next((i for i, r in enumerate(results)
                         if isinstance(r, dict) and r.get("index") == track_index), None)
    else:
        position = track_index if 0 <= track_index < len(results) else None
    if position is None:
        return {"success": False, "error": "Invalid track index"}, 400
    result = results[position]
    if not unavailable and result.get("status_class") != "unavailable":
        return {"success": True, "result": result}, 200  # only a mark is undone

    tracks = (state.get("playlist") or {}).get("tracks") or []
    index = result.get("index", track_index)
    track = tracks[index] if isinstance(index, int) and 0 <= index < len(tracks) \
        and isinstance(tracks[index], dict) else None
    saved = SAVED_MARK if unavailable else SAVED_CLEAR
    # the database first: a mark that didn't save must not look saved
    if platform == "mirrored" and track and track.get("db_track_id"):
        if not database.update_mirrored_track_extra_data(track["db_track_id"], dict(saved)):
            return {"success": False, "error": "Could not save the track"}, 500
    if track is not None:
        track["unavailable"] = unavailable
        if isinstance(track.get("extra_data"), dict):
            track["extra_data"].update(saved)

    if unavailable:
        old = result.get("status_class") or result.get("status")
        counted_as = "youtube" if platform in ("youtube", "mirrored", "ytmusic") else platform
        if unmatch_removes_a_match(old, counted_as):
            state["spotify_matches"] = max(0, int(state.get("spotify_matches") or 0) - 1)
        if old in ("wing-it", "Wing It"):
            state["wing_it_count"] = max(0, int(state.get("wing_it_count") or 0) - 1)
        result.update(unavailable_row(index if isinstance(index, int) else track_index, track or {
            "name": result.get("yt_track", ""), "artists": [result.get("yt_artist", "")]}))
        for key in ("spotify_id", "spotify_data", "matched_data", "match_data"):
            result[key] = None if key != "spotify_id" else ""
        result.update({"wing_it_fallback": False, "manual_match": False})
    else:
        result.update({"status": "Not Found", "status_class": "not-found", "unavailable": False})
    logger.info("Discovery track %s of %s marked %s", track_index, identifier,
                "not available" if unavailable else "available again")
    return {"success": True, "result": result,
            "spotify_matches": int(state.get("spotify_matches") or 0)}, 200


def register(app) -> None:
    from flask import jsonify, request

    @app.route("/api/commissary/discovery/unavailable", methods=["POST"])
    def commissary_discovery_unavailable():
        data = request.get_json(silent=True) or {}
        identifier = str(data.get("identifier") or "")
        track_index = data.get("track_index")
        if not identifier or not isinstance(track_index, int) or isinstance(track_index, bool) \
                or track_index < 0:
            return jsonify({"success": False, "error": "Missing required fields"}), 400
        state, platform = find_state(data.get("platform") or "", identifier)
        if not state:
            return jsonify({"success": False, "error": "Discovery state not found"}), 404
        return _respond(jsonify, state, platform, identifier, track_index,
                        data.get("unavailable", True) is not False)


def _respond(jsonify, state, platform, identifier, track_index, unavailable):
    from database.music_database import get_database
    try:
        payload, status = mark(state, platform, identifier, track_index, unavailable, get_database())
    except Exception as exc:  # noqa: BLE001
        logger.error("marking a discovery track failed: %s", exc, exc_info=True)
        payload, status = {"success": False, "error": str(exc)}, 500
    return jsonify(payload), status
