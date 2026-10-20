"""Commissary: video Libraries endpoints (the registry editor and pickers).

Under /api/video/libraries/... so upstream's video gate treats every method as
admin-only, like the rest of the library settings.

GET  /libraries/registry            libraries + server sections + quality profiles
POST /libraries/registry            save {movies: [...], tv: [...]} for the server
POST /libraries/assign/wishlist     {kind, tmdb_id, root_folder_id} send a wished title
POST /libraries/assign/watchlist    {tmdb_id, root_folder_id} send a followed show
"""

from __future__ import annotations

from flask import jsonify, request

from utils.logging_config import get_logger

logger = get_logger("video_api.commissary_libraries")


def register_routes(bp):
    @bp.route("/libraries/registry", methods=["GET"])
    def commissary_library_registry():
        from . import get_video_db
        from core.commissary import video_libraries as vl
        from core.video.sources import list_video_libraries, resolve_video_server
        db = get_video_db()
        try:
            available = list_video_libraries() or {}
        except Exception:  # noqa: BLE001 - an unreachable server still shows the saved rows
            logger.exception("listing server libraries failed")
            available = {}
        server = available.get("server") or resolve_video_server()
        try:
            from core.video.quality_profile import list_profiles
            profiles = [{"id": p.get("id"), "name": p.get("name")} for p in list_profiles(db)]
        except Exception:  # noqa: BLE001
            profiles = []
        return jsonify({
            "server": server,
            "available": {k: [x.get("title") for x in available.get(k) or [] if x.get("title")]
                          for k in ("movies", "tv")},
            "libraries": vl.list_libraries(db, server) if server else {"movies": [], "tv": []},
            "profiles": profiles,
            "series_types": list(vl.SERIES_TYPES),
        })

    @bp.route("/libraries/registry", methods=["POST"])
    def commissary_library_registry_save():
        from . import get_video_db
        from core.commissary import video_libraries as vl
        from core.video.sources import resolve_video_server
        body = request.get_json(silent=True) or {}
        server = body.get("server") or resolve_video_server()
        if server not in ("plex", "jellyfin"):
            return jsonify({"error": "Connect a Plex or Jellyfin server first."}), 400
        for kind in ("movies", "tv"):
            if body.get(kind) is not None and not isinstance(body.get(kind), list):
                return jsonify({"error": f"{kind} must be a list"}), 400
        saved = vl.save_libraries(get_video_db(), server, movies=body.get("movies"),
                                  tv=body.get("tv"))
        return jsonify({"status": "saved", "server": server, "libraries": saved})

    @bp.route("/libraries/assign/wishlist", methods=["POST"])
    def commissary_library_assign_wishlist():
        from . import get_video_db
        from core.commissary import video_libraries as vl
        body = request.get_json(silent=True) or {}
        if not body.get("tmdb_id") or body.get("kind") not in ("movie", "show", "episode"):
            return jsonify({"error": "kind and tmdb_id are required"}), 400
        n = vl.set_wishlist_library(get_video_db(), body["kind"], body["tmdb_id"],
                                    body.get("root_folder_id"))
        return jsonify({"status": "saved", "updated": n})

    @bp.route("/libraries/assign/watchlist", methods=["POST"])
    def commissary_library_assign_watchlist():
        from . import get_video_db
        from core.commissary import video_libraries as vl
        body = request.get_json(silent=True) or {}
        if not body.get("tmdb_id"):
            return jsonify({"error": "tmdb_id is required"}), 400
        n = vl.set_watchlist_library(get_video_db(), body["tmdb_id"], body.get("root_folder_id"))
        return jsonify({"status": "saved", "updated": n})
