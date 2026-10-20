"""Commissary: video Libraries endpoints (the registry editor and pickers).

Under /api/video/libraries/... so upstream's video gate treats every method as
admin-only, like the rest of the library settings.

GET  /libraries/registry            libraries + server sections + quality profiles
POST /libraries/registry            save {movies: [...], tv: [...]} for the server
POST /libraries/assign/wishlist     {kind, tmdb_id, root_folder_id} send a wished title
POST /libraries/assign/watchlist    {tmdb_id, root_folder_id} send a followed show
GET  /libraries/assignments         library choices + current wished/followed choices
POST /libraries/assign/title        {kind, library_id, root_folder_id} move an owned title

GET  /libraries/<kind>/<id>/import-lock                 {locked, seasons}
POST /libraries/<kind>/<id>/import-lock                 {locked} lock a movie or show
POST /libraries/show/<id>/season/<n>/import-lock        {locked} lock one season
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

    @bp.route("/libraries/assignments", methods=["GET"])
    def commissary_library_assignments():
        from . import get_video_db
        from core.commissary import video_libraries as vl
        return jsonify(vl.picker_state(get_video_db(), request.args.get("kind"),
                                       request.args.get("library_id")))

    @bp.route("/libraries/assign/title", methods=["POST"])
    def commissary_library_assign_title():
        from . import get_video_db
        from core.commissary import video_libraries as vl
        body = request.get_json(silent=True) or {}
        if body.get("kind") not in ("movie", "show") or body.get("library_id") in (None, ""):
            return jsonify({"error": "kind and library_id are required"}), 400
        n = vl.set_title_library(get_video_db(), body["kind"], body["library_id"],
                                 body.get("root_folder_id"))
        return jsonify({"status": "saved", "updated": n})

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

    # ── Lock automatic edits (core/commissary/import_lock.py) ────────────────
    @bp.route("/libraries/<kind>/<int:item_id>/import-lock", methods=["GET"])
    def commissary_import_lock_state(kind, item_id):
        from . import get_video_db
        from core.commissary import import_lock
        st = import_lock.state(get_video_db(), kind, item_id)
        if st is None:
            return jsonify({"error": "not found"}), 404
        return jsonify(st)

    @bp.route("/libraries/<kind>/<int:item_id>/import-lock", methods=["POST"])
    def commissary_import_lock_set(kind, item_id):
        from . import get_video_db
        from core.commissary import import_lock
        body = request.get_json(silent=True) or {}
        if kind not in ("movie", "show") or not isinstance(body.get("locked"), bool):
            return jsonify({"error": "kind movie|show and locked true|false are required"}), 400
        if not import_lock.set_lock(get_video_db(), kind, item_id, body["locked"]):
            return jsonify({"error": "not found"}), 404
        return jsonify({"status": "saved", "locked": body["locked"]})

    @bp.route("/libraries/show/<int:show_id>/season/<int:season_number>/import-lock",
              methods=["POST"])
    def commissary_season_import_lock_set(show_id, season_number):
        from . import get_video_db
        from core.commissary import import_lock
        body = request.get_json(silent=True) or {}
        if not isinstance(body.get("locked"), bool):
            return jsonify({"error": "locked true|false is required"}), 400
        if not import_lock.set_season_lock(get_video_db(), show_id, season_number, body["locked"]):
            return jsonify({"error": "no such season"}), 404
        return jsonify({"status": "saved", "season": season_number, "locked": body["locked"]})
