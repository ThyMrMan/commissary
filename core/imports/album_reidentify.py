"""Re-identify a whole library album under a different release.

The per-track Re-identify (#889) copies one library file into the staging root
and leaves a single-use hint for the auto-import worker. That cannot simply be
repeated for every track of an album: the worker groups loose staging files by
their album tag, so twelve copies from one album become ONE candidate, and
``_resolve_rematch_hint`` only honours single-file candidates. Every hint would
be ignored and the album re-filed under the very tags it already had.

So an album takes the path the Import page uses to import an album it has
matched, one confirmed track at a time:

1. **preview** -- pair the library album's tracks with the chosen release's
   tracklist, using the album matcher with the library's own durations, ISRCs
   and MusicBrainz IDs, so the user confirms every pairing before anything
   moves;
2. **apply**, per track -- copy the library file somewhere the auto-import
   worker never scans, import the COPY against the confirmed release track, and
   remove the original row and file only once that import has landed.

A track whose import is rejected (AcoustID, integrity, silence) leaves the
original exactly where it was.
"""

from __future__ import annotations

import os
import shutil
import tempfile
import threading
import time
import uuid
from typing import Any, Callable, Dict, List, Optional, Tuple

import core.imports.album as album_mod
from core.imports.album_matching import default_quality_rank, match_files_to_tracks
from utils.logging_config import get_logger

logger = get_logger("imports.album_reidentify")

# Applying runs one request per track, and each needs the release's tracklist.
# Keep a successful lookup briefly so a 20-track album costs one fetch, not 20.
RELEASE_CACHE_TTL_SECONDS = 600.0
_release_cache: Dict[Tuple[str, str], Tuple[float, Dict[str, Any]]] = {}
_release_cache_lock = threading.Lock()


class PairError(ValueError):
    """A requested (library track, release track) pairing is not valid."""


def release_track_key(track: Dict[str, Any]) -> str:
    """A release track's identity within its release: ``"<disc>-<track>"``.

    Not the provider's track id, because some sources (Discogs) return tracks
    without one -- a confirmed pairing has to be addressable either way."""
    try:
        disc = int(track.get("disc_number") or 1)
    except (TypeError, ValueError):
        disc = 1
    try:
        number = int(track.get("track_number") or 0)
    except (TypeError, ValueError):
        number = 0
    return "%d-%d" % (disc, number)


def _row_value(row: Any, key: str, default: Any = None) -> Any:
    try:
        return row[key] if key in row.keys() else default
    except Exception:       # noqa: BLE001 - a missing column reads as absent
        return default


def _as_int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def load_library_album(database: Any, library_album_id: Any) -> Optional[Dict[str, Any]]:
    """The library album and its tracks, or ``None`` if there is no such album.

    Ids are opaque. Plex keys look numeric, but Jellyfin and Navidrome ids are
    strings and the schema stores them that way, so an id is passed through as
    given -- never coerced to an integer, which would make every such album
    "not found"."""
    if library_album_id is None or str(library_album_id).strip() == "":
        return None
    conn = database._get_connection()
    try:
        cur = conn.cursor()
        cur.execute("SELECT * FROM albums WHERE id = ?", (str(library_album_id).strip(),))
        album_row = cur.fetchone()
        if album_row is None:
            return None
        artist_name = ""
        artist_id = _row_value(album_row, "artist_id")
        if artist_id is not None:
            cur.execute("SELECT * FROM artists WHERE id = ?", (artist_id,))
            artist_row = cur.fetchone()
            if artist_row is not None:
                artist_name = _row_value(artist_row, "name", "") or ""
        cur.execute("SELECT * FROM tracks WHERE album_id = ?", (_row_value(album_row, "id"),))
        rows = cur.fetchall()
    finally:
        conn.close()

    tracks = [{
        "id": _row_value(r, "id"),
        "title": _row_value(r, "title", "") or "",
        "track_number": _as_int(_row_value(r, "track_number"), 0),
        "disc_number": _as_int(_row_value(r, "disc_number"), 1) or 1,
        "duration_ms": _as_int(_row_value(r, "duration"), 0),     # stored in ms
        "file_path": _row_value(r, "file_path", "") or "",
        "isrc": str(_row_value(r, "isrc", "") or "").strip().upper(),
        "mbid": str(_row_value(r, "musicbrainz_recording_id", "") or "").strip().lower(),
    } for r in rows]
    tracks.sort(key=lambda t: (t["disc_number"], t["track_number"], t["title"].lower()))

    return {
        "id": _row_value(album_row, "id"),
        "title": _row_value(album_row, "title", "") or "",
        "year": _row_value(album_row, "year"),
        "thumb_url": _row_value(album_row, "thumb_url"),
        "artist_name": artist_name,
        "tracks": tracks,
    }


def fetch_release(
    release_album_id: Any,
    *,
    source: str,
    album_name: str = "",
    album_artist: str = "",
    now: Optional[float] = None,
) -> Dict[str, Any]:
    """The chosen release and its normalized tracklist.

    Normalized with the Import page's own helpers, so ``build_album_import_context``
    receives exactly the shapes it receives there. Only successful lookups are
    cached; a failure is retried on the next request."""
    key = (str(source or "").strip().lower(), str(release_album_id))
    now = time.time() if now is None else now
    with _release_cache_lock:
        hit = _release_cache.get(key)
        if hit and now - hit[0] < RELEASE_CACHE_TTL_SECONDS:
            return hit[1]

    response = album_mod.get_artist_album_tracks(
        str(release_album_id),
        artist_name=album_artist or "",
        album_name=album_name or "",
        source=(source or None),
    ) or {}
    raw_tracks = list(response.get("tracks") or [])
    if not response.get("success") or not raw_tracks:
        return {"success": False, "error": response.get("error") or "Release not found"}

    album = album_mod._strip_legacy_source_fields(dict(response.get("album") or {}))
    resolved_source = album_mod._normalize_album_source(album, response.get("source") or source or "")
    tracks = [album_mod._normalize_match_track(t, resolved_source, album) for t in raw_tracks]
    release = {"success": True, "album": album, "tracks": tracks, "source": resolved_source}

    with _release_cache_lock:
        _release_cache[key] = (now, release)
    return release


def clear_release_cache() -> None:
    with _release_cache_lock:
        _release_cache.clear()


def _public_library_track(track: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "id": track["id"],
        "title": track["title"],
        "track_number": track["track_number"],
        "disc_number": track["disc_number"],
        "duration_ms": track["duration_ms"],
        "has_file": bool(track.get("file_path")),
    }


def _release_artist(album: Dict[str, Any]) -> str:
    named = album.get("artist") or album.get("artist_name")
    if named:
        return str(named)
    names = [str(a.get("name")) for a in (album.get("artists") or [])
             if isinstance(a, dict) and a.get("name")]
    return ", ".join(names)


def build_preview(library_album: Dict[str, Any], release: Dict[str, Any]) -> Dict[str, Any]:
    """Pair the library album's tracks with the release's tracklist.

    Pure. Uses the same matcher as the Import page, keyed on library track ids
    (with the file's extension, so the quality dedup still sees a format)."""
    lib_tracks = library_album["tracks"]
    by_key: Dict[str, Dict[str, Any]] = {}
    for t in lib_tracks:
        ext = os.path.splitext(t.get("file_path") or "")[1]
        by_key["lib:%s%s" % (t["id"], ext)] = t
    file_tags = {
        key: {
            "title": t["title"],
            "artist": library_album.get("artist_name") or "",
            "album": library_album.get("title") or "",
            "track_number": t["track_number"],
            "disc_number": t["disc_number"],
            "duration_ms": t["duration_ms"],
            "isrc": t["isrc"],
            "mbid": t["mbid"],
        }
        for key, t in by_key.items()
    }
    album = release["album"]
    result = match_files_to_tracks(
        list(by_key), file_tags, release["tracks"],
        target_album=album.get("name") or "", quality_rank=default_quality_rank,
    )
    match_by_track = {id(m["track"]): m for m in result["matches"]}

    pairs: List[Dict[str, Any]] = []
    matched_ids = set()
    for rt in release["tracks"]:
        hit = match_by_track.get(id(rt))
        lt = by_key.get(hit["file"]) if hit else None
        if lt is not None:
            matched_ids.add(lt["id"])
        pairs.append({
            "release_track": {
                "key": release_track_key(rt),
                "name": rt.get("name") or "",
                "track_number": rt.get("track_number"),
                "disc_number": rt.get("disc_number"),
                "duration_ms": rt.get("duration_ms"),
            },
            "library_track": _public_library_track(lt) if lt is not None else None,
            "confidence": round(hit["confidence"], 2) if hit else 0,
        })

    return {
        "success": True,
        "release": {
            "id": album.get("id"),
            "name": album.get("name") or "",
            "artist": _release_artist(album),
            "image_url": album.get("image_url") or "",
            "release_date": album.get("release_date") or "",
            "total_tracks": len(release["tracks"]),
            "source": release["source"],
        },
        "library_album": {
            "id": library_album["id"],
            "title": library_album["title"],
            "artist_name": library_album.get("artist_name") or "",
        },
        "pairs": pairs,
        "unmatched_library_tracks": [
            _public_library_track(t) for t in lib_tracks if t["id"] not in matched_ids
        ],
    }


def resolve_pair(
    library_album: Dict[str, Any],
    release: Dict[str, Any],
    library_track_id: Any,
    release_key: Any,
) -> Tuple[Dict[str, Any], Dict[str, Any]]:
    """Validate a confirmed pairing against the server's own data.

    The browser sends only ids. Both must belong to what the server loaded:
    a track id from another album, or a position the release does not have, is
    refused rather than trusted."""
    lt = next((t for t in library_album["tracks"] if str(t["id"]) == str(library_track_id)), None)
    if lt is None:
        raise PairError("That track is not part of this album.")
    candidates = [t for t in release["tracks"] if release_track_key(t) == str(release_key)]
    if not candidates:
        raise PairError("That track is not on the chosen release.")
    if len(candidates) > 1:
        raise PairError("The release lists more than one track at that position.")
    return lt, candidates[0]


def apply_track(
    *,
    database: Any,
    library_album: Dict[str, Any],
    release: Dict[str, Any],
    library_track_id: Any,
    release_key: Any,
    replace: bool,
    resolve_file: Callable[[str], Optional[str]],
    post_process: Callable[[str, Dict[str, Any], str], Any],
    is_media_server_ready: Callable[[], Tuple[bool, str]],
    copy_fn: Callable[[str, str], Any] = shutil.copy2,
    make_temp_dir: Optional[Callable[[], str]] = None,
) -> Tuple[Dict[str, Any], int]:
    """Re-file one library track under its confirmed release track.

    Returns ``(payload, http_status)``. The original is removed only after the
    copy's import has landed somewhere we can name -- if the pipeline does not
    report where it wrote the file, the original is kept, because the same-home
    guard cannot run without that path and deleting blind could remove the very
    file the import just wrote."""
    try:
        lt, rt = resolve_pair(library_album, release, library_track_id, release_key)
    except PairError as exc:
        return {"success": False, "error": str(exc)}, 400

    ready, reason = is_media_server_ready()
    if not ready:
        return {"success": False, "error": reason,
                "error_code": "media_server_not_connected"}, 503

    real_path = resolve_file(lt["file_path"]) if lt.get("file_path") else None
    if not real_path or not os.path.isfile(real_path):
        return {"success": False, "original_kept": True,
                "error": "Couldn't find this track's file on disk."}, 404

    tmp_dir = (make_temp_dir or (lambda: tempfile.mkdtemp(prefix="commissary-reidentify-")))()
    try:
        staged = os.path.join(tmp_dir, os.path.basename(real_path))
        copy_fn(real_path, staged)

        album = release["album"]
        source = release["source"]
        total_discs = max((_as_int(t.get("disc_number"), 1) for t in release["tracks"]), default=1)
        artist_context = album_mod.resolve_album_artist_context(album, source=source)
        context = album_mod.build_album_import_context(
            album, rt, artist_context=artist_context, total_discs=total_discs, source=source)
        if isinstance(context, dict):
            context["is_local_import"] = True
            # The user confirmed this exact pairing, so the quality profile has no
            # veto -- the same rule the Import page applies (#1017). AcoustID,
            # integrity and silence guards still run.
            context["_skip_quarantine_check"] = ["quality", "bit_depth"]
            # A person chose this file for this track: it outranks the copy the
            # library already holds at the destination, instead of being
            # discarded by the same-or-better comparison.
            context["_user_manual_pick"] = True

        context_key = "reidentify_album_%s_%s_%s" % (
            library_album["id"], release_track_key(rt), uuid.uuid4().hex[:8])
        try:
            post_process(context_key, context, staged)
        except Exception as exc:     # noqa: BLE001 - reported per track, original kept
            logger.warning("[Re-identify album] import failed for track %s: %s", lt["id"], exc)
            return {"success": False, "original_kept": True,
                    "error": "Import failed: %s" % exc}, 500

        from core.imports.pipeline import import_rejection_reason
        rejected = import_rejection_reason(context) if isinstance(context, dict) else None
        if rejected:
            return {"success": False, "original_kept": True, "error": rejected}, 422

        landed = context.get("_final_processed_path") if isinstance(context, dict) else None
        removed = None
        if replace and landed:
            from core.imports.rematch_hints import delete_replaced_track
            conn = database._get_connection()
            try:
                removed = delete_replaced_track(
                    conn.cursor(), lt["id"], resolve_fn=resolve_file, new_paths=[landed])
                conn.commit()
            finally:
                conn.close()

        logger.info("[Re-identify album] track %s -> '%s' #%s (%s), landed=%s removed=%s",
                    lt["id"], album.get("name") or "?", release_track_key(rt), source,
                    landed, removed)
        return {
            "success": True,
            "landed_path": landed,
            "removed_original": removed,
            "original_kept": not bool(removed),
        }, 200
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)


__all__ = [
    "PairError",
    "apply_track",
    "build_preview",
    "clear_release_cache",
    "fetch_release",
    "load_library_album",
    "release_track_key",
    "resolve_pair",
]
