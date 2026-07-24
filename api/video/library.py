"""Video library listing endpoint.

GET /api/video/library -> {"movies": [...], "shows": [...]}
Reads what the last scan mirrored from the media server into video.db.
"""

from __future__ import annotations

from flask import jsonify, request

from utils.logging_config import get_logger

logger = get_logger("video_api.library")


def register_routes(bp):
    @bp.route("/library", methods=["GET"])
    def video_library():
        from . import get_video_db
        from core.video.sources import resolve_video_server
        try:
            # Channels tab: followed YouTube channels (no media server involved —
            # ownership comes from the permanent download history).
            if request.args.get("kind") == "channels":
                return jsonify(get_video_db().query_channel_library(
                    search=request.args.get("search") or None,
                    letter=request.args.get("letter") or None,
                    sort=request.args.get("sort", "title"),
                    page=request.args.get("page", 1),
                    limit=request.args.get("limit", 75),
                ))
            db = get_video_db()
            kind = request.args.get("kind", "movies")
            server = resolve_video_server()
            root_folder_id = request.args.get("root_folder_id")
            include_unassigned = False
            if root_folder_id:
                try:
                    root_folder_id = int(root_folder_id)
                except (TypeError, ValueError):
                    root_folder_id = None
            if root_folder_id and server:
                # Items scanned before Libraries existed have no root_folder_id yet —
                # bucket them into the PRIMARY tab so nothing vanishes post-upgrade
                # until a rescan re-attributes them to their real library.
                content_kind = "movie" if kind == "movies" else "show"
                primary = db.primary_root_folder(server, content_kind)
                include_unassigned = bool(primary and primary.get("id") == root_folder_id)
            return jsonify(db.query_library(
                kind,
                search=request.args.get("search") or None,
                letter=request.args.get("letter") or None,
                sort=request.args.get("sort", "title"),
                status=request.args.get("status", "all"),
                genre=request.args.get("genre") or None,
                resolution=request.args.get("resolution") or None,
                page=request.args.get("page", 1),
                limit=request.args.get("limit", 75),
                server_source=server,
                root_folder_id=root_folder_id,
                include_unassigned=include_unassigned,
            ))
        except Exception:
            logger.exception("Failed to query video library")
            return jsonify({"error": "Failed to load video library"}), 500

    @bp.route("/library/resolutions", methods=["GET"])
    def video_library_resolutions():
        """File resolutions in use in the movie library — the library page's
        resolution filter dropdown (movies tab only)."""
        from . import get_video_db
        from core.video.sources import resolve_video_server
        try:
            return jsonify({"resolutions": get_video_db().library_resolutions(
                server_source=resolve_video_server())})
        except Exception:
            logger.exception("Failed to list library resolutions")
            return jsonify({"resolutions": []})

    @bp.route("/library/genres", methods=["GET"])
    def video_library_genres():
        """Genre names in use for the given kind — the library filter dropdown.
        Read-only and ungated (unlike the collections field-suggestions endpoint)."""
        from . import get_video_db
        from core.video.sources import resolve_video_server
        try:
            return jsonify({"genres": get_video_db().library_genres(
                request.args.get("kind", "movies"), server_source=resolve_video_server())})
        except Exception:
            logger.exception("Failed to list library genres")
            return jsonify({"genres": []})
