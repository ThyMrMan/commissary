"""Video Library Maintenance API — the music /api/repair surface, video-scoped.

Same route set, same response shapes (see the music table in web_server.py):
status/toggle/pause/resume, jobs list + per-job toggle/settings/run/stop,
findings list/counts/fix/resolve/dismiss/bulk-fix/bulk/clear, history, and a
progress snapshot for page load (the socket event 'video:repair:progress'
keeps it live afterwards).
"""

from __future__ import annotations

from flask import jsonify, request

from utils.logging_config import get_logger

logger = get_logger("video_api.repair")


def _worker():
    from core.video.repair.worker import get_video_repair_worker

    from . import get_video_db
    return get_video_repair_worker(get_video_db())


def register_routes(bp):
    @bp.route("/repair/duplicate-episodes", methods=["GET"])
    def video_duplicate_episodes():
        """Preview: episodes listed twice because the media server and TMDB
        number the show's seasons differently (Plex files Bleach's newer run as
        S2, TMDB calls it S17 — different rows under
        UNIQUE(show_id, season_number, episode_number)).

        Read-only, and deliberately its own step: the clean-up deletes rows, so
        it must be possible to look before agreeing. ``?show_id=`` scopes it to
        one show."""
        from . import get_video_db
        show_id = request.args.get("show_id", type=int)
        rows = get_video_db().duplicate_episode_rows(show_id)
        return jsonify({"ok": True, "count": len(rows), "items": rows})

    @bp.route("/repair/duplicate-episodes", methods=["POST"])
    def video_remove_duplicate_episodes():
        """Delete the phantom rows. Body: {ids: [...]} or {show_id: N}.

        Only ever removes a row that is server-less, has no file and no media
        files, and whose air date matches exactly one OWNED episode under a
        different season — re-checked at delete time, so a preview that has gone
        stale cannot destroy something that became real in between.

        Nothing on disk is touched: these rows are placeholders for episodes you
        do not have, listed under the wrong season number."""
        from . import get_video_db
        db = get_video_db()
        body = request.get_json(silent=True) or {}
        ids = body.get("ids")
        if ids is None:
            show_id = body.get("show_id")
            if show_id is None:
                return jsonify({"ok": False,
                                "error": "Pass ids, or show_id to clear one show."}), 400
            ids = [r["id"] for r in db.duplicate_episode_rows(show_id)]
        removed = db.delete_episode_rows(ids)
        return jsonify({"ok": True, "removed": removed,
                        "requested": len(ids or [])})

    def _tmdb_season_numbers(db, show_id):
        """{season_number: {episode numbers TMDB lists}} for a show, straight from
        TMDB. Returns ({}, reason) when it can't be established — the caller must
        NOT fall back to 'no episodes', which would make every missing episode a
        deletion candidate."""
        info = db.show_match_info(show_id)
        if not info:
            return {}, "That show isn't in the library."
        from core.video.enrichment.engine import get_video_enrichment_engine
        w = get_video_enrichment_engine().workers.get("tmdb")
        if not w or not w.enabled:
            return {}, "TMDB isn't configured, so there's nothing to check against."
        res = w.client.match("show", info.get("title"), info.get("year"),
                             known_id=info.get("tmdb_id"))
        if not res or not res.get("id"):
            return {}, "This show has no TMDB match to check against."
        out = {}
        for s in ((res.get("metadata") or {}).get("seasons") or []):
            sn = s.get("season_number")
            if sn is None:
                continue
            data = w.client.season_episodes(res["id"], sn) or {}
            nums = {e["episode_number"] for e in (data.get("episodes") or [])
                    if e.get("episode_number") is not None}
            if nums:                     # a season that answered empty proves nothing
                out[sn] = nums
        if not out:
            return {}, "Couldn't read this show's episode list from TMDB just now."
        return out, None

    @bp.route("/repair/unlisted-episodes", methods=["GET", "POST"])
    def video_unlisted_episodes():
        """Episodes a SECONDARY metadata provider invented, which TMDB doesn't
        list for the season they were filed under.

        The Bleach case: TMDB's season 2 is the 2005 arc, TVDB's season 2 is
        Thousand-Year Blood War. The TVDB gap-fill was handed TMDB's season
        numbers and allowed to insert, so seventeen TYBW episodes (25, 26,
        39-53) landed inside the 21-episode 2005 season. A wished episode under
        a season number no release uses can never be matched, which is why the
        missing episodes were never found. ``update_only`` on the TVDB cascade
        stops new ones; this clears what was already written.

        GET previews, POST removes — deleting rows, so looking first is a
        separate step. Only ever removes a row with no server_id, no file and no
        media_files, whose episode number TMDB does not list for that season,
        re-derived at delete time. Nothing on disk is touched."""
        from . import get_video_db
        db = get_video_db()
        body = request.get_json(silent=True) or {}
        show_id = request.args.get("show_id", type=int) or body.get("show_id")
        if not show_id:
            return jsonify({"ok": False, "error": "show_id is required"}), 400
        try:
            listed, err = _tmdb_season_numbers(db, int(show_id))
        except Exception:
            logger.exception("unlisted-episodes: TMDB read failed for show %s", show_id)
            return jsonify({"ok": False, "error": "Couldn't reach TMDB — see app.log"}), 502
        if err:
            return jsonify({"ok": False, "error": err}), 400
        if request.method == "GET":
            items = []
            for sn, keep in sorted(listed.items()):
                items += db.unlisted_episode_rows(int(show_id), sn, keep)
            return jsonify({"ok": True, "count": len(items), "items": items})
        removed = sum(db.delete_unlisted_episode_rows(int(show_id), sn, keep)
                      for sn, keep in listed.items())
        return jsonify({"ok": True, "removed": removed})

    @bp.route("/repair/status", methods=["GET"])
    def video_repair_status():
        return jsonify(_worker().get_stats())

    @bp.route("/repair/toggle", methods=["POST"])
    def video_repair_toggle():
        w = _worker()
        body = request.get_json(silent=True) or {}
        enabled = bool(body.get("enabled", not w.master_enabled()))
        w.set_master(enabled)
        return jsonify({"enabled": enabled})

    @bp.route("/repair/pause", methods=["POST"])
    def video_repair_pause():
        _worker().pause()
        return jsonify({"status": "paused"})

    @bp.route("/repair/resume", methods=["POST"])
    def video_repair_resume():
        _worker().resume()
        return jsonify({"status": "running"})

    @bp.route("/repair/jobs", methods=["GET"])
    def video_repair_jobs():
        return jsonify({"jobs": _worker().get_all_job_info()})

    @bp.route("/repair/jobs/<job_id>/toggle", methods=["POST"])
    def video_repair_job_toggle(job_id):
        w = _worker()
        cfg = w.job_config(job_id)
        if not cfg:
            return jsonify({"error": "unknown job"}), 404
        body = request.get_json(silent=True) or {}
        enabled = bool(body.get("enabled", not cfg["enabled"]))
        w.set_job_config(job_id, enabled=enabled)
        return jsonify({"job_id": job_id, "enabled": enabled})

    @bp.route("/repair/jobs/<job_id>/settings", methods=["PUT"])
    def video_repair_job_settings(job_id):
        w = _worker()
        if not w.job_config(job_id):
            return jsonify({"error": "unknown job"}), 404
        body = request.get_json(silent=True) or {}
        w.set_job_config(job_id, interval_hours=body.get("interval_hours"),
                         settings=body.get("settings"))
        return jsonify({"success": True})

    @bp.route("/repair/jobs/<job_id>/run", methods=["POST"])
    def video_repair_job_run(job_id):
        w = _worker()
        w.start()   # ensure the scheduler thread exists (force queue drains even when disabled)
        if not w.run_job_now(job_id):
            return jsonify({"error": "unknown job"}), 404
        return jsonify({"success": True, "job_id": job_id})

    @bp.route("/repair/jobs/<job_id>/stop", methods=["POST"])
    def video_repair_job_stop(job_id):
        res = _worker().stop_current_job(job_id)
        return jsonify({"job_id": job_id, **res})

    @bp.route("/repair/findings", methods=["GET"])
    def video_repair_findings():
        from . import get_video_db
        q = request.args
        return jsonify(get_video_db().repair_get_findings(
            job_id=q.get("job_id") or None, status=q.get("status") or None,
            severity=q.get("severity") or None,
            page=q.get("page", 1), limit=q.get("limit", 50)))

    @bp.route("/repair/findings/counts", methods=["GET"])
    def video_repair_findings_counts():
        from . import get_video_db
        return jsonify(get_video_db().repair_counts())

    @bp.route("/repair/findings/<int:fid>/fix", methods=["POST"])
    def video_repair_finding_fix(fid):
        body = request.get_json(silent=True) or {}
        res = _worker().fix_finding(fid, body.get("fix_action"))
        return jsonify(res), (200 if res.get("success") else 400)

    @bp.route("/repair/findings/<int:fid>/resolve", methods=["POST"])
    def video_repair_finding_resolve(fid):
        body = request.get_json(silent=True) or {}
        ok = _worker().resolve_finding(fid, body.get("action"))
        return jsonify({"success": ok}), (200 if ok else 404)

    @bp.route("/repair/findings/<int:fid>/dismiss", methods=["POST"])
    def video_repair_finding_dismiss(fid):
        ok = _worker().dismiss_finding(fid)
        return jsonify({"success": ok}), (200 if ok else 404)

    @bp.route("/repair/findings/bulk-fix", methods=["POST"])
    def video_repair_bulk_fix():
        body = request.get_json(silent=True) or {}
        return jsonify(_worker().bulk_fix_findings(
            job_id=body.get("job_id"), severity=body.get("severity"),
            ids=body.get("ids"), fix_action=body.get("fix_action")))

    @bp.route("/repair/findings/bulk", methods=["POST"])
    def video_repair_bulk():
        body = request.get_json(silent=True) or {}
        ids, action = body.get("ids") or [], body.get("action")
        if action not in ("dismiss", "resolve") or not ids:
            return jsonify({"error": "bad request"}), 400
        return jsonify({"success": True,
                        "updated": _worker().bulk_update_findings(ids, action)})

    @bp.route("/repair/findings/clear", methods=["POST"])
    def video_repair_clear():
        from . import get_video_db
        body = request.get_json(silent=True) or {}
        deleted = get_video_db().repair_clear_findings(
            job_id=body.get("job_id"), status=body.get("status"))
        return jsonify({"success": True, "deleted": deleted})

    @bp.route("/repair/history", methods=["GET"])
    def video_repair_history():
        from . import get_video_db
        q = request.args
        return jsonify({"runs": get_video_db().repair_history(
            job_id=q.get("job_id") or None, limit=int(q.get("limit", 50)))})

    @bp.route("/repair/progress", methods=["GET"])
    def video_repair_progress():
        return jsonify(_worker().progress_snapshot())
