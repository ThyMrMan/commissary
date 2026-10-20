"""Commissary: a title's search settings before it is in the library.

GET /commissary/title/<kind>/<tmdb_id>/overrides
PUT /commissary/title/<kind>/<tmdb_id>/overrides
    {manual_aliases: [...], series_type: standard|daily|anime,
     pack_preference: auto|prefer|never}

The path ends in /overrides, so upstream's video gate makes the PUT admin-only
exactly like the owned title's own /detail/<kind>/<id>/overrides. A title that
is already owned is written through upstream's row methods instead (they mirror
back to the TMDB id), so the two never disagree.
"""

from __future__ import annotations

from flask import jsonify, request

from utils.logging_config import get_logger

logger = get_logger("video_api.commissary_titles")


def _state(db, kind, tmdb_id):
    from core.commissary import search_identity as si
    from core.commissary import title_overrides as to
    library_id = to.library_row(db, kind, tmdb_id)
    ovr = db.title_overrides_for(kind, tmdb_id=tmdb_id)
    out = {"success": True, "kind": kind, "tmdb_id": tmdb_id, "library_id": library_id,
           "manual_aliases": ovr.get("manual_aliases") or []}
    if kind == "show":
        out["series_type"] = si.series_type(db, tmdb_id) or "standard"
        out["pack_preference"] = ovr.get("pack_preference") or "auto"
    return out


def register_routes(bp):
    @bp.route("/commissary/title/<kind>/<int:tmdb_id>/overrides", methods=["GET"])
    def commissary_title_overrides_get(kind, tmdb_id):
        from . import get_video_db
        if kind not in ("movie", "show"):
            return jsonify({"success": False, "error": "kind must be movie|show"}), 400
        return jsonify(_state(get_video_db(), kind, tmdb_id))

    @bp.route("/commissary/title/<kind>/<int:tmdb_id>/overrides", methods=["PUT"])
    def commissary_title_overrides_put(kind, tmdb_id):
        from . import get_video_db
        from core.commissary import title_overrides as to
        if kind not in ("movie", "show"):
            return jsonify({"success": False, "error": "kind must be movie|show"}), 400
        body = request.get_json(silent=True) or {}
        fields = {}
        if "manual_aliases" in body:
            if not isinstance(body["manual_aliases"], list):
                return jsonify({"success": False, "error": "manual_aliases must be a list"}), 400
            fields["manual_aliases"] = body["manual_aliases"]
        if kind == "show" and "pack_preference" in body:
            if str(body["pack_preference"]) not in to.PACK_PREFS:
                return jsonify({"success": False,
                                "error": "pack_preference must be auto|prefer|never"}), 400
            fields["pack_preference"] = body["pack_preference"]
        if kind == "show" and "series_type" in body:
            if str(body["series_type"]) not in to.SERIES_TYPES:
                return jsonify({"success": False,
                                "error": "series_type must be standard|daily|anime"}), 400
            fields["series_type"] = body["series_type"]
        if not fields:
            return jsonify({"success": False, "error": "nothing to set"}), 400
        db = get_video_db()
        library_id = to.library_row(db, kind, tmdb_id)
        if library_id:
            row_fields = {k: v for k, v in fields.items() if k != "series_type"}
            ok = (not row_fields or db.set_title_overrides(kind, library_id, **row_fields))
            if "series_type" in fields:
                ok = db.set_show_series_type(library_id, fields["series_type"]) and ok
        else:
            ok = to.set_values(db, kind, tmdb_id, **fields)
        if not ok:
            return jsonify({"success": False, "error": "Couldn't save."}), 500
        return jsonify(_state(db, kind, tmdb_id))

    @bp.route("/commissary/library-tabs", methods=["GET"])
    def commissary_library_tabs():
        """The video Libraries each kind can be split into, with title counts -
        for the Library page's tabs. Readable by every profile on the video side
        (no folder paths in it), unlike the admin-only registry."""
        from . import get_video_db
        from core.commissary.video_libraries import tabs, wishlist_tab_counts
        from core.video.sources import resolve_video_server
        db = get_video_db()
        out = tabs(db, resolve_video_server())
        if request.args.get("scope") == "wishlist":
            # this profile's wished titles per Library, instead of owned titles
            try:
                from core.profile_context import get_current_profile_id
                pid = int(get_current_profile_id() or 1)
            except Exception:  # noqa: BLE001
                pid = 1
            counts = wishlist_tab_counts(db, pid)
            for kind, libs in out.items():
                for lib in libs:
                    lib["count"] = counts.get(kind, {}).get(lib["id"], 0)
        return jsonify(out)
