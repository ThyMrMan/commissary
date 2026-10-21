"""Commissary: a show's episode numbering, and repairing its episode list.

Under /api/video/libraries/..., so upstream's video gate makes every method
admin-only, like the rest of library management.

GET  /libraries/show/<id>/numbering        which provider owns the episode list, and why
PUT  /libraries/show/<id>/numbering        {episode_source: auto|tmdb|tvdb}
POST /libraries/show/<id>/rescan-episodes  re-read the full episode list now
GET  /libraries/show/<id>/out-of-place     episodes listed under a season the provider doesn't give them
POST /libraries/show/<id>/out-of-place     remove them (re-derived, never from the preview's ids)
"""

from __future__ import annotations

from flask import jsonify, request

from utils.logging_config import get_logger

logger = get_logger("video_api.commissary_episodes")

_PROVIDER_DOWN = "Couldn't reach the metadata providers — see app.log"


def _engine():
    from core.video.enrichment.engine import get_video_enrichment_engine
    return get_video_enrichment_engine()


def register_routes(bp):
    @bp.route("/libraries/show/<int:show_id>/numbering", methods=["GET"])
    def commissary_show_numbering(show_id):
        """'Auto' is otherwise unfalsifiable: this says which provider is in
        force, and the seasons each one can't serve."""
        from . import get_video_db
        from core.commissary import episode_numbering as en
        db = get_video_db()
        info = db.show_match_info(show_id)
        if not info:
            return jsonify({"success": False, "error": "Unknown show"}), 404
        try:
            eng = _engine()
            tmdb_nums = en._tmdb_season_numbers(eng, info)
            tvdb = en._tvdb_client(eng)
            tvdb_nums = en.tvdb_season_numbers(tvdb, info.get("tvdb_id"))
            out = en.explain(en.server_season_numbers(db, show_id), tmdb_nums, tvdb_nums,
                             en.override(db, show_id))
        except Exception:
            logger.exception("episode numbering probe failed for show %s", show_id)
            return jsonify({"success": False, "error": _PROVIDER_DOWN}), 502
        out.update(success=True, setting=en.override(db, show_id) or "auto",
                   tvdb_configured=tvdb is not None, has_tvdb_id=bool(info.get("tvdb_id")))
        return jsonify(out)

    @bp.route("/libraries/show/<int:show_id>/numbering", methods=["PUT"])
    def commissary_show_numbering_set(show_id):
        """Changing it rewrites nothing by itself: re-scan, then check for
        out-of-place episodes."""
        from . import get_video_db
        from core.commissary import episode_numbering as en
        src = (request.get_json(silent=True) or {}).get("episode_source")
        if str(src or "") not in en.SOURCES:
            return jsonify({"success": False,
                            "error": "episode_source must be auto, tmdb or tvdb"}), 400
        if not en.set_override(get_video_db(), show_id, src):
            return jsonify({"success": False, "error": "Unknown show"}), 404
        return jsonify({"success": True, "episode_source": src})

    @bp.route("/libraries/show/<int:show_id>/rescan-episodes", methods=["POST"])
    def commissary_show_rescan_episodes(show_id):
        """Re-read the full episode list from the owning provider. A show is
        cascaded once and then marked synced, so episodes a provider gains later
        otherwise never appear. Reports what landed instead of claiming success."""
        from . import get_video_db
        from core.commissary import episode_numbering as en
        db = get_video_db()
        before = en.episode_count(db, show_id)
        try:
            # with_ratings=False: this is about episodes; OMDb has a daily quota
            res = _engine().refresh_show_art(show_id, with_ratings=False) or {}
        except Exception:
            logger.exception("episode re-scan failed for show %s", show_id)
            return jsonify({"success": False, "error": _PROVIDER_DOWN}), 502
        if not res.get("ok"):
            msg = {"not_found": "That show isn't in the library.",
                   "no_match": "This show has no metadata match to read episodes from.",
                   "match_error": "Couldn't reach the metadata provider just now.",
                   "tmdb_not_configured": "Add a TMDB key in Settings first."}.get(
                       res.get("reason"), "Couldn't refresh the episode list.")
            return jsonify({"success": False, "reason": res.get("reason"), "error": msg}), 400
        after = en.episode_count(db, show_id)
        return jsonify({"success": True, "added": max(0, after - before), "total": after,
                        "before": before})

    @bp.route("/libraries/show/<int:show_id>/out-of-place", methods=["GET", "POST"])
    def commissary_show_out_of_place(show_id):
        """Episodes nothing on your server has and the owning provider doesn't
        list under that season number. Nothing on disk is touched."""
        from . import get_video_db
        from core.commissary import episode_numbering as en
        if not get_video_db().show_match_info(show_id):
            return jsonify({"success": False, "error": "Unknown show"}), 404
        try:
            res = en.out_of_place(_engine(), show_id, remove=request.method == "POST")
        except Exception:
            logger.exception("out-of-place episode check failed for show %s", show_id)
            return jsonify({"success": False, "error": _PROVIDER_DOWN}), 502
        return jsonify({"success": True, **res})
