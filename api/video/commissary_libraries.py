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

    # ── Rename Files for one title (core/video/mass_rename, scoped) ─────────
    def _rename_scope(kind, item_id):
        if kind not in ("movie", "show"):
            return None
        return (kind, int(item_id))

    @bp.route("/libraries/rename/<kind>/<int:item_id>", methods=["GET"])
    def commissary_rename_preview(kind, item_id):
        """Every file of this title whose name differs from the template - the
        saved one, or ?template= typed in the panel (never saved). Plus the
        variables with their values for this title."""
        from . import get_video_db
        from core.commissary import naming
        from core.video import mass_rename, organization
        scope = _rename_scope(kind, item_id)
        if not scope:
            return jsonify({"error": "kind must be movie|show"}), 400
        db = get_video_db()
        settings = organization.load(db)
        saved = settings.get("movie_template" if kind == "movie" else "episode_template")
        template = request.args.get("template") or None
        res = mass_rename.preview(scope=scope, template=template)
        movies, episodes = naming.scope_rows(db, db.repair_owned_movie_files(),
                                             db.rename_owned_episode_files(), scope)
        rows = naming.with_library_facts(db, (movies or episodes)[:1],
                                         "movie" if kind == "movie" else "episode")
        fields = ((mass_rename._movie_fields if kind == "movie" else mass_rename._episode_fields)(rows[0])
                  if rows else {})
        used = template or saved
        return jsonify({**res, "template": saved, "rendered_with": used,
                        "import_only": naming.template_uses_unavailable_tokens(used),
                        "tokens": naming.tokens_for(kind, fields)})

    @bp.route("/libraries/rename/<kind>/<int:item_id>", methods=["POST"])
    def commissary_rename_apply(kind, item_id):
        """Rename the picked files. The plan is rebuilt server-side with the SAME
        scope and template the panel previewed, never trusted from the browser."""
        from core.video import mass_rename
        scope = _rename_scope(kind, item_id)
        if not scope:
            return jsonify({"error": "kind must be movie|show"}), 400
        body = request.get_json(silent=True) or {}
        keys = body.get("keys")
        if keys is not None and not isinstance(keys, list):
            return jsonify({"error": "keys must be a list"}), 400
        res = mass_rename.apply(keys or None, scope=scope, template=body.get("template") or None)
        if res.get("status") == "skipped":
            return jsonify({**res, "error": "Another rename is running - try again in a moment."}), 409
        return jsonify(res)

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
