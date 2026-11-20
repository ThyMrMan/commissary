"""Commissary: the {Token} naming settings' endpoints.

Under /api/video/libraries/... so upstream's video gate treats every method as
admin-only, like the rest of the library settings.

GET  /libraries/naming/tokens         every {Token} the renderer knows, per scope
POST /libraries/naming/preview        the sample title through render_path
GET|POST /libraries/naming/rename-first   rename a title's files before an import
"""

from __future__ import annotations

from flask import jsonify, request

from utils.logging_config import get_logger

logger = get_logger("video_api.commissary_naming")


def register_routes(bp):
    # ── {Token} naming: the settings preview, rendered by the real engine ────
    @bp.route("/libraries/naming/tokens", methods=["GET"])
    def commissary_naming_tokens():
        from core.commissary import naming
        return jsonify({"movie": naming.brace_token_names("movie"),
                        "episode": naming.brace_token_names("episode"),
                        "import_only": list(naming.LIBRARY_UNAVAILABLE_TOKENS)})

    @bp.route("/libraries/naming/preview", methods=["POST"])
    def commissary_naming_preview():
        """What each template names the sample title, through the same
        render_path an import uses - not a JavaScript copy of the engine."""
        from core.commissary import naming
        from core.video import organization
        body = request.get_json(silent=True) or {}
        out = {}
        for scope, key, ext in (("movie", "movie_template", ".mkv"),
                                ("episode", "episode_template", ".mkv"),
                                ("youtube", "youtube_template", ".mp4")):
            tmpl = str(body.get(key) or "").strip()
            if not tmpl:
                continue
            dest = organization.render_path(scope, "", naming.PREVIEW_SAMPLES[scope],
                                            {key: tmpl}, ext)
            out[scope] = dest["path"].replace("\\", "/").lstrip("/")
        return jsonify(out)

    @bp.route("/libraries/naming/rename-first", methods=["GET", "POST"])
    def commissary_rename_first():
        from . import get_video_db
        from core.commissary import naming
        db = get_video_db()
        if request.method == "POST":
            body = request.get_json(silent=True) or {}
            if not isinstance(body.get("enabled"), bool):
                return jsonify({"error": "enabled true|false is required"}), 400
            naming.set_rename_before_import(db, body["enabled"])
        return jsonify({"enabled": naming.rename_before_import_enabled(db)})
