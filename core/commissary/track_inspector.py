"""Search every source for any track in the download window, and take one file.

Old fork 1.8.19 ("search every source yourself and pick"): manual search was
reachable only from a download that had already run and failed, so the
wishlist was the only route in. Upstream 3.5.7 has since built the Candidate
Inspector - every configured source, accepted and rejected hits with the
reasons - and opens it from a wishlist row, a failed download and a library
redownload. What it still lacks is the Download Missing Tracks window: every
album (artist pages, Discover, the Search page) and every single track from
Search opens there, and its rows offered nothing but Begin Analysis.

So this gives any track the inspector: ``POST /api/commissary/inspect`` streams
upstream's verdicts for the posted track, ``POST /api/commissary/inspect/download``
starts the picked file as a pinned one-task batch - exactly what upstream's
wishlist pick does, so the shared path applies AcoustID and the quality
quarantine, and a failure means THAT file failed rather than a hunt elsewhere.
Release-level hits (torrent/usenet/Lidarr) are whole releases and can't be
picked one track at a time, as upstream already says.
"""

from __future__ import annotations

from typing import Any, Callable, Dict, Optional

from utils.logging_config import get_logger

logger = get_logger("commissary.track_inspector")


def track_info(track: Any) -> Optional[Dict[str, Any]]:
    """The posted track as the download pipeline's track dict, or None when it
    has no name to search for."""
    if not isinstance(track, dict) or not str(track.get("name") or "").strip():
        return None
    from core.wishlist.payloads import ensure_wishlist_track_format
    info = dict(ensure_wishlist_track_format(dict(track)))
    for key in ("track_number", "disc_number", "explicit", "isrc"):
        if track.get(key) is not None:
            info.setdefault(key, track.get(key))
    album = info.get("album")
    if not isinstance(album, dict):
        # upstream's formatter passes a track whose artists are already dicts
        # through untouched, album string and all
        album = info["album"] = {"name": str(album or "Unknown Album")}
    if isinstance(album, dict) and album.get("name") == "Unknown Album" and track.get("name"):
        # a single from the Search page: the album IS the track, as upstream's
        # buildSingleTrackAlbum does
        album["name"] = track.get("name")
    return info


def _artist_name(info: Dict[str, Any]) -> str:
    artists = info.get("artists") or []
    first = artists[0] if artists else ""
    return (first.get("name") if isinstance(first, dict) else str(first or "")) or "Unknown"


def register(app, *, inspect_stream: Callable, pinned_batch, pinned_deps: Callable,
             check_permission: Callable, profile_id: Callable, add_activity: Callable,
             mark_override: Callable[[list], None]) -> None:
    """The two routes, on upstream's own inspector stream and pinned batches."""
    from flask import jsonify, request

    @app.route("/api/commissary/inspect", methods=["POST"])
    def commissary_inspect_track():
        try:
            info = track_info((request.get_json(silent=True) or {}).get("track"))
            if info is None:
                return jsonify({"success": False, "error": "A track name is required"}), 400
            return inspect_stream(pinned_batch.track_object(info), info.get("quality_profile_id"),
                                  log_tag="Inspector")
        except Exception as exc:  # noqa: BLE001
            logger.error("track inspection failed: %s", exc, exc_info=True)
            return jsonify({"success": False, "error": str(exc)}), 500

    @app.route("/api/commissary/inspect/download", methods=["POST"])
    def commissary_inspect_download():
        denied = check_permission()
        if denied:
            return denied
        try:
            data = request.get_json(silent=True) or {}
            candidate = data.get("candidate") or {}
            if not candidate.get("username") or not candidate.get("filename"):
                return jsonify({"success": False,
                                "error": "candidate with username and filename required"}), 400
            if not pinned_batch.is_pinnable(candidate.get("username")):
                return jsonify({"success": False, "error": (
                    "Torrent and Usenet hits are whole releases and can't be picked one track "
                    "at a time. Use Begin Analysis instead.")}), 400
            info = track_info(data.get("track"))
            if info is None:
                return jsonify({"success": False, "error": "A track name is required"}), 400

            name = f"{_artist_name(info)} - {info.get('name')}"
            batch_id, task_ids = pinned_batch.create_pinned_batch(
                [pinned_batch.PinnedFile(candidate=pinned_batch.candidate_from_result(candidate),
                                         track_info=info)],
                name=name, profile_id=profile_id(),
                source_page="Search", playlist_prefix="commissary_pick",
            )
            from core.downloads.decisions import is_quality_override
            if is_quality_override(data.get("override")):
                mark_override(task_ids)
            pinned_batch.dispatch_pinned_batch(batch_id, task_ids, pinned_deps())
            add_activity("", "Download Started", name, "Now")
            return jsonify({"success": True, "batch_id": batch_id, "task_id": task_ids[0]})
        except Exception as exc:  # noqa: BLE001
            logger.error("starting a picked file failed: %s", exc, exc_info=True)
            return jsonify({"success": False, "error": str(exc)}), 500
