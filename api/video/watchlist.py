"""Video watchlist API — the user's curated follow-list of shows + people.

Mirrors the music watchlist's add/remove/list/check shape. v1 just manages
membership (so cards can toggle + the Watchlist page can render); the
monitoring/discovery engine that turns a follow into new downloads is a later
phase. Reads/writes only video_library.db via the shared VideoDatabase.
"""

from __future__ import annotations

from flask import g, jsonify, request

from utils.logging_config import get_logger

logger = get_logger("video_api.watchlist")

_KINDS = ("show", "person", "studio")


def _me():
    return int(getattr(g, "profile_id", 1) or 1)


def _is_admin():
    return bool(getattr(g, "is_admin", _me() == 1))


def _may_acquire():
    """Whether this profile's follows take effect immediately, or land pending.

    Keyed on can_download ALONE, deliberately not on is_admin. The blueprint's
    permission gate treats the two as orthogonal — a download-disabled admin is
    still refused /downloads/grab — so letting is_admin alone approve here would
    have handed that same admin a way around their own gate, since an approved
    follow expands straight into wishlist writes."""
    return bool(getattr(g, "can_download", True))


def _server():
    """Active video server_source (scopes the airing-show default). None on error."""
    try:
        from core.video.sources import resolve_video_server
        return resolve_video_server()
    except Exception:
        return None


def register_routes(bp):
    @bp.route("/watchlist", methods=["GET"])
    def video_watchlist_list():
        """All watchlist entries grouped by kind (for the tabbed page).

        Shows include actively-airing library shows by default, scoped to the
        active video server so Plex/Jellyfin libraries don't commingle."""
        from . import get_video_db
        try:
            db = get_video_db()
            server = _server()
            kind = request.args.get("kind")
            counts = db.watchlist_counts(server_source=server)
            if kind in _KINDS:
                # Paged + searchable, like the library page.
                res = db.query_watchlist(
                    kind, search=request.args.get("search", ""),
                    sort=request.args.get("sort", "default"),
                    page=request.args.get("page", 1), limit=request.args.get("limit", 60),
                    server_source=server)
                return jsonify({"success": True, "kind": kind, "counts": counts, **res})
            # No kind → grouped (counts + first-glance lists).
            rows = db.list_watchlist(server_source=server)
            shows = [r for r in rows if r.get("kind") == "show"]
            people = [r for r in rows if r.get("kind") == "person"]
            studios = [r for r in rows if r.get("kind") == "studio"]
            return jsonify({"success": True, "shows": shows, "people": people,
                            "studios": studios, "counts": counts})
        except Exception:
            logger.exception("Failed to list video watchlist")
            return jsonify({"success": False, "error": "Failed to load watchlist"}), 500

    @bp.route("/watchlist/counts", methods=["GET"])
    def video_watchlist_counts():
        from . import get_video_db
        try:
            return jsonify({"success": True, **get_video_db().watchlist_counts(server_source=_server())})
        except Exception:
            logger.exception("Failed to count video watchlist")
            return jsonify({"success": False, "error": "Failed"}), 500

    @bp.route("/watchlist/add", methods=["POST"])
    def video_watchlist_add():
        """Add a show/person. Body: {kind, tmdb_id, title, poster_url?, library_id?,
        monitor?}. ``monitor`` (shows only; arr-parity P2) picks what to wish at
        follow time: future (default) | all | first_season | latest_season | pilot —
        back-catalog expansion is best-effort, the follow itself always lands.

        A profile WITHOUT download rights may follow, but the row is filed
        ``approved=0``: it appears on the watchlist right away and no acquisition
        path acts on it until an admin approves. Their chosen ``monitor`` is
        stored rather than expanded, so approving later wishes what they actually
        asked for instead of silently downgrading them to 'future'."""
        from . import get_video_db
        body = request.get_json(silent=True) or {}
        kind = body.get("kind")
        tmdb_id = body.get("tmdb_id")
        title = (body.get("title") or "").strip()
        if kind not in _KINDS or not tmdb_id or not title:
            return jsonify({"success": False, "error": "kind, tmdb_id and title are required"}), 400
        try:
            db = get_video_db()
            approved = _may_acquire()
            monitor = str(body.get("monitor") or "future").lower()
            ok = db.add_to_watchlist(
                kind, int(tmdb_id), title,
                poster_url=body.get("poster_url") or None,
                library_id=body.get("library_id") or None,
                approved=approved,
                requested_by=None if approved else _me(),
                requested_by_name=None if approved else getattr(g, "profile_name", None),
                monitor=None if approved else monitor)
            if not ok:
                return jsonify({"success": False, "error": "Could not add to watchlist"}), 400
            if not approved:
                return jsonify({"success": True, "watched": True, "wished": 0,
                                "pending": True,
                                "message": "Added — waiting for an admin to approve it."})
            wished = 0
            if kind == "show" and monitor != "future":
                try:
                    from datetime import date

                    from core.video.enrichment.engine import get_video_enrichment_engine
                    from core.video.monitor_policy import episodes_for_policy
                    eps = episodes_for_policy(get_video_enrichment_engine(), int(tmdb_id),
                                              monitor, date.today().isoformat())
                    if eps:
                        wished = db.add_episodes_to_wishlist(
                            int(tmdb_id), title, eps,
                            poster_url=body.get("poster_url") or None,
                            library_id=body.get("library_id") or None)
                except Exception:   # noqa: BLE001 - policy expansion must never sink the follow
                    logger.exception("monitor policy expansion failed for %s", tmdb_id)
            return jsonify({"success": True, "watched": True, "wished": wished})
        except Exception:
            logger.exception("Failed to add to video watchlist")
            return jsonify({"success": False, "error": "Failed"}), 500

    @bp.route("/watchlist/remove", methods=["POST"])
    def video_watchlist_remove():
        """Remove a show/person. Body: {kind, tmdb_id}.

        A profile that can't acquire may only withdraw its OWN pending follow —
        it must not be able to un-follow the shared, admin-approved watchlist.
        (Before Plex profiles were given video access this endpoint was
        unreachable for them, so nothing guarded it.)"""
        from . import get_video_db
        body = request.get_json(silent=True) or {}
        kind = body.get("kind")
        tmdb_id = body.get("tmdb_id")
        if kind not in _KINDS or not tmdb_id:
            return jsonify({"success": False, "error": "kind and tmdb_id are required"}), 400
        try:
            db = get_video_db()
            if not _may_acquire():
                owner = db.watchlist_entry_requester(kind, int(tmdb_id))
                if not owner or owner[0] or owner[1] != _me():
                    return jsonify({"success": False,
                                    "error": "You can only withdraw your own pending requests."}), 403
            removed = db.remove_from_watchlist(kind, int(tmdb_id))
            return jsonify({"success": True, "watched": False, "removed": removed})
        except Exception:
            logger.exception("Failed to remove from video watchlist")
            return jsonify({"success": False, "error": "Failed"}), 500

    @bp.route("/watchlist/library", methods=["POST"])
    def video_watchlist_set_library():
        """Choose which Library a FOLLOWED show belongs in.
        Body: ``{tmdb_id, root_folder_id|null}``.

        The wishlist has had this for a while, but a wishlist row is created by
        the airing automation minutes before the grab — there is no moment at
        which a person could set one. This is the moment: at follow time, before
        the show exists anywhere. It matters because the FIRST import decides
        where a show lives forever — the library scan reads it back from
        whichever server section it landed in and stamps that onto the show row.

        Metadata only; nothing on disk moves. Restricted to profiles that may
        acquire, since it redirects where downloads land."""
        from . import get_video_db
        if not _may_acquire():
            return jsonify({"success": False,
                            "error": "You don't have permission to change download destinations."}), 403
        body = request.get_json(silent=True) or {}
        tmdb_id = body.get("tmdb_id")
        if not tmdb_id:
            return jsonify({"success": False, "error": "tmdb_id is required"}), 400
        try:
            db = get_video_db()
            rfid = body.get("root_folder_id")
            # Validate here rather than inferring from the row count — a show
            # that is followed but has no row to update also reports 0, and
            # calling that "no such Library" would be a lie.
            if rfid not in (None, "", "null"):
                row = db.get_root_folder(rfid)
                if not row or str(row.get("content_kind") or "") != "show":
                    return jsonify({"success": False,
                                    "error": "That Library doesn't exist, or isn't a TV Library"}), 400
            return jsonify({"success": True,
                            "updated": db.set_watchlist_root_folder(int(tmdb_id), rfid)})
        except Exception:
            logger.exception("Failed to set watchlist library")
            return jsonify({"success": False, "error": "Failed"}), 500

    @bp.route("/watchlist/pending", methods=["GET"])
    def video_watchlist_pending():
        """Follows awaiting approval — all of them for an admin, a member's own
        otherwise. ``count`` feeds the nav badge."""
        from . import get_video_db
        try:
            db = get_video_db()
            scope = None if _is_admin() else _me()
            return jsonify({"success": True,
                            "pending": db.pending_watchlist_entries(requested_by=scope),
                            "count": db.pending_watchlist_count(requested_by=scope)})
        except Exception:
            logger.exception("Failed to list pending watchlist entries")
            return jsonify({"success": False, "pending": [], "count": 0}), 500

    @bp.route("/watchlist/approve", methods=["POST"])
    def video_watchlist_approve():
        """Admin approves a pending follow — acquisition starts here. Body:
        {kind, tmdb_id}. The requester's stored monitor policy is expanded now
        (it was deliberately NOT expanded at follow time), so approving gives
        them the back catalog they asked for."""
        from . import get_video_db
        if not _is_admin():
            return jsonify({"success": False, "error": "Admin only."}), 403
        body = request.get_json(silent=True) or {}
        kind = body.get("kind")
        tmdb_id = body.get("tmdb_id")
        if kind not in _KINDS or not tmdb_id:
            return jsonify({"success": False, "error": "kind and tmdb_id are required"}), 400
        try:
            db = get_video_db()
            row = db.approve_watchlist_entry(kind, int(tmdb_id))
            if row is None:
                return jsonify({"success": False,
                                "error": "Unknown entry, or already approved."}), 404
            wished = 0
            monitor = str(row.get("monitor") or "future").lower()
            if kind == "show" and monitor != "future":
                try:
                    from datetime import date

                    from core.video.enrichment.engine import get_video_enrichment_engine
                    from core.video.monitor_policy import episodes_for_policy
                    eps = episodes_for_policy(get_video_enrichment_engine(), int(tmdb_id),
                                              monitor, date.today().isoformat())
                    if eps:
                        wished = db.add_episodes_to_wishlist(
                            int(tmdb_id), row.get("title") or "", eps,
                            poster_url=row.get("poster_url") or None,
                            library_id=row.get("library_id") or None)
                except Exception:   # noqa: BLE001 - the approval itself must still stand
                    logger.exception("approve: monitor policy expansion failed for %s", tmdb_id)
            return jsonify({"success": True, "approved": True, "wished": wished})
        except Exception:
            logger.exception("Failed to approve watchlist entry")
            return jsonify({"success": False, "error": "Failed"}), 500

    @bp.route("/watchlist/deny", methods=["POST"])
    def video_watchlist_deny():
        """Admin rejects a pending follow. Body: {kind, tmdb_id}. Removes it the
        same way an un-follow does (a 'mute' tombstone), so the airing-library
        default can't quietly re-add it."""
        from . import get_video_db
        if not _is_admin():
            return jsonify({"success": False, "error": "Admin only."}), 403
        body = request.get_json(silent=True) or {}
        kind = body.get("kind")
        tmdb_id = body.get("tmdb_id")
        if kind not in _KINDS or not tmdb_id:
            return jsonify({"success": False, "error": "kind and tmdb_id are required"}), 400
        try:
            db = get_video_db()
            owner = db.watchlist_entry_requester(kind, int(tmdb_id))
            if not owner or owner[0]:
                return jsonify({"success": False,
                                "error": "Unknown entry, or already approved."}), 404
            db.remove_from_watchlist(kind, int(tmdb_id))
            return jsonify({"success": True, "denied": True})
        except Exception:
            logger.exception("Failed to deny watchlist entry")
            return jsonify({"success": False, "error": "Failed"}), 500

    @bp.route("/watchlist/person/<int:tmdb_id>/settings", methods=["GET"])
    def video_watchlist_person_settings(tmdb_id):
        """A followed person's back-catalog window {tmdb_id, title, date_added, lookback_years}
        (0=forward-only, N=years, -1=everything) — for the person settings modal."""
        from . import get_video_db
        s = get_video_db().get_person_lookback(tmdb_id)
        if not s:
            return jsonify({"success": False, "error": "not followed"}), 404
        return jsonify({"success": True, "settings": s})

    @bp.route("/watchlist/person/<int:tmdb_id>/settings", methods=["POST"])
    def video_watchlist_person_settings_save(tmdb_id):
        """Set a person's lookback window. Body: {lookback_years} (0=forward-only, N=years,
        -1=everything). The next daily scan backfills any newly-included films."""
        from . import get_video_db
        body = request.get_json(silent=True) or {}
        ok = get_video_db().set_person_lookback(tmdb_id, body.get("lookback_years"))
        if not ok:
            return jsonify({"success": False, "error": "not followed or invalid value"}), 400
        return jsonify({"success": True, "settings": get_video_db().get_person_lookback(tmdb_id)})

    @bp.route("/watchlist/studio/<int:tmdb_id>/settings", methods=["GET"])
    def video_watchlist_studio_settings(tmdb_id):
        """A followed studio's back-catalog window {tmdb_id, title, date_added, lookback_years}
        (0=forward-only, N=years, -1=everything) — for the studio settings modal."""
        from . import get_video_db
        s = get_video_db().get_studio_lookback(tmdb_id)
        if not s:
            return jsonify({"success": False, "error": "not followed"}), 404
        return jsonify({"success": True, "settings": s})

    @bp.route("/watchlist/studio/<int:tmdb_id>/settings", methods=["POST"])
    def video_watchlist_studio_settings_save(tmdb_id):
        """Set a studio's lookback window. Body: {lookback_years} (0=forward-only, N=years,
        -1=everything). The next daily scan backfills any newly-included films."""
        from . import get_video_db
        body = request.get_json(silent=True) or {}
        ok = get_video_db().set_studio_lookback(tmdb_id, body.get("lookback_years"))
        if not ok:
            return jsonify({"success": False, "error": "not followed or invalid value"}), 400
        return jsonify({"success": True, "settings": get_video_db().get_studio_lookback(tmdb_id)})

    @bp.route("/watchlist/check", methods=["POST"])
    def video_watchlist_check():
        """Hydrate cards. Body: {kind, tmdb_ids: [...]} → {results: {id: true}}.
        Only watched ids appear in results (absent = not watched)."""
        from . import get_video_db
        body = request.get_json(silent=True) or {}
        kind = body.get("kind")
        ids = body.get("tmdb_ids") or []
        if kind not in _KINDS:
            return jsonify({"success": False, "error": "kind is required"}), 400
        try:
            state = get_video_db().watchlist_state(kind, ids, server_source=_server())
            # JSON object keys must be strings.
            return jsonify({"success": True, "results": {str(k): True for k in state}})
        except Exception:
            logger.exception("Failed to check video watchlist")
            return jsonify({"success": False, "error": "Failed"}), 500
