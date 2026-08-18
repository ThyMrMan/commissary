"""Video detail payloads (drill-in pages).

GET /api/video/detail/show/<id>   → show + seasons→episodes tree (owned roll-ups)
GET /api/video/detail/movie/<id>  → movie + owned/file info

Reads only video.db; isolated from the music API.
"""

from __future__ import annotations

from flask import jsonify, request

from utils.logging_config import get_logger

logger = get_logger("video_api.detail")


def register_routes(bp):
    @bp.route("/monitor", methods=["POST"])
    def video_set_monitor():
        from . import get_video_db
        body = request.get_json(silent=True) or {}
        kind, item_id = body.get("kind"), body.get("id")
        if kind not in ("movie", "show") or not isinstance(item_id, int):
            return jsonify({"error": "bad request"}), 400
        ok = get_video_db().set_monitored(kind, item_id, bool(body.get("monitored")))
        if not ok:
            return jsonify({"error": "not found"}), 404
        return jsonify({"success": True, "monitored": bool(body.get("monitored"))})

    # ── Manage sidebar: metadata edits + field locks + watched ────────────────
    @bp.route("/detail/<kind>/<int:item_id>/metadata", methods=["PUT"])
    def video_edit_metadata(kind, item_id):
        """Apply user edits (title/sort/year/rating/genres/summary/tagline…).
        Writes locally + auto-locks the fields, then pushes to Plex/Jellyfin
        with the server's own field locks set."""
        from core.video import metadata as med

        from . import get_video_db
        if kind not in ("movie", "show"):
            return jsonify({"error": "bad kind"}), 400
        changes = (request.get_json(silent=True) or {}).get("changes")
        if not isinstance(changes, dict) or not changes:
            return jsonify({"error": "no changes"}), 400
        try:
            res = med.edit_item(get_video_db(), kind, item_id, changes)
        except ValueError as e:
            return jsonify({"error": str(e)}), 400
        if not res.get("ok"):
            return jsonify({"error": res.get("error", "not found")}), 404
        return jsonify(res)

    @bp.route("/detail/aka/<kind>/<int:tmdb_id>", methods=["GET"])
    def video_get_aka_titles(kind, tmdb_id):
        """A title's user AKAs by TMDB id. Readable with no library row, which is
        the point — the manage panel opens on titles that aren't in the library
        yet, and there is nothing else to read them from."""
        from . import get_video_db
        if kind not in ("movie", "show"):
            return jsonify({"error": "bad kind"}), 400
        db = get_video_db()
        return jsonify({"ok": True,
                        "aka_titles": db.aka_titles_for_tmdb(kind, tmdb_id),
                        "series_type": (db.series_type_for_tmdb(tmdb_id)
                                        if kind == "show" else None)})

    @bp.route("/detail/show/<int:show_id>/rescan-episodes", methods=["POST"])
    def video_rescan_episodes(show_id):
        """Re-read a show's FULL episode list, right now.

        Reads from whichever provider owns this show's NUMBERING (see
        core/video/episode_numbering) — TMDB for most shows, TVDB for one whose
        seasons your media server splits the way TVDB does. The caller must not
        assume TMDB: for a show like Bleach the correct list only exists in
        TVDB, and re-scanning against TMDB would find nothing to add.

        Exists because nothing else could. A show is cascaded once, then
        ``episodes_synced=1`` and the background pass only ever picks
        ``episodes_synced=0`` — so episodes TMDB gains later never appear. The
        page's lazy refresh is gated on ``needs`` (no logo / missing season art /
        not-yet-synced), which an established show fails, so opening it does
        nothing. "Sync show now" reconciles against PLEX, which by definition
        cannot know about episodes your server hasn't got. The nightly schedule
        refresh is behind the video-automations master switch (off by default)
        and only covers the latest seasons.

        Deliberately NOT rematch_item: that is built for pointing at a different
        TMDB entry and clears everything derived from the old match. This only
        re-reads episodes — it never clears artwork, locked fields, or the match
        itself.

        Season numbers come from TMDB's own season list rather than the local
        ``seasons`` table, so a season Commissary has never seen is picked up too.
        Returns the before/after counts so the caller can report what landed
        instead of just claiming success."""
        from . import get_video_db
        db = get_video_db()
        before = db.show_episode_count(show_id)
        try:
            from core.video.enrichment.engine import get_video_enrichment_engine
            # with_ratings=False: this is about the episode list, and the per-show
            # OMDb call would spend the daily quota for nothing.
            res = get_video_enrichment_engine().refresh_show_art(
                show_id, with_ratings=False) or {}
        except Exception:
            logger.exception("episode re-scan failed for show %s", show_id)
            return jsonify({"ok": False, "reason": "error",
                            "error": "Couldn't reach the metadata provider "
                                     "— see app.log"}), 502
        if not res.get("ok"):
            reason = res.get("reason") or "error"
            msg = {"not_found": "That show isn't in the library.",
                   "no_match": "This show has no metadata match to read episodes from.",
                   "match_error": "Couldn't reach the metadata provider just now."}.get(
                       reason, "Couldn't refresh the episode list.")
            return jsonify({"ok": False, "reason": reason, "error": msg}), 400
        after = db.show_episode_count(show_id)
        return jsonify({"ok": True, "added": max(0, after - before),
                        "total": after, "before": before})

    @bp.route("/detail/show/<int:show_id>/episode-source", methods=["GET"])
    def video_get_episode_source(show_id):
        """Which provider currently owns this show's episode list, AND why.

        Exists because 'Auto' is otherwise unfalsifiable: when a re-scan does
        nothing there is no way to tell whether auto picked the provider you
        expected, or silently kept the default because a probe failed. Returns
        the scores and the seasons each provider can't serve."""
        from core.video.episode_numbering import explain
        from . import get_video_db
        db = get_video_db()
        info = db.show_match_info(show_id)
        if not info:
            return jsonify({"success": False, "error": "Unknown show"}), 404
        try:
            from core.video.enrichment.engine import get_video_enrichment_engine
            eng = get_video_enrichment_engine()
            w = eng.workers.get("tmdb")
            tmdb_nums = []
            if w and w.enabled:
                res = w.client.match("show", info.get("title"), info.get("year"),
                                     known_id=info.get("tmdb_id")) or {}
                tmdb_nums = [s.get("season_number")
                             for s in ((res.get("metadata") or {}).get("seasons") or [])
                             if s.get("season_number") is not None]
            tvdb_nums = eng._tvdb_season_numbers(info.get("tvdb_id"))
            out = explain(db.server_season_numbers(show_id), tmdb_nums, tvdb_nums,
                          info.get("episode_source"))
            out["tmdb_seasons"], out["tvdb_seasons"] = tmdb_nums, tvdb_nums
            out["success"] = True
            return jsonify(out)
        except Exception:
            logger.exception("episode-source probe failed for show %s", show_id)
            return jsonify({"success": False,
                            "error": "Couldn't reach the metadata providers — see app.log"}), 502

    @bp.route("/detail/show/<int:show_id>/episode-source", methods=["PUT"])
    def video_set_episode_source(show_id):
        """Which provider supplies this show's episode list.
        Body: {episode_source: 'auto'|'tmdb'|'tvdb'}.

        Episodes are keyed by the MEDIA SERVER's season numbers, so a provider
        that splits the show differently can only write rows into seasons they
        don't belong to. 'auto' compares each provider's season structure against
        what your server reports; the explicit values are for when that gets it
        wrong. Changing this doesn't rewrite anything by itself — re-scan after."""
        from . import get_video_db
        body = request.get_json(silent=True) or {}
        src = body.get("episode_source")
        if not get_video_db().set_show_episode_source(show_id, src):
            return jsonify({"success": False,
                            "error": "episode_source must be auto, tmdb or tvdb"}), 400
        return jsonify({"success": True, "episode_source": src})

    @bp.route("/detail/<kind>/<int:item_id>/library", methods=["PUT"])
    def video_set_item_library(kind, item_id):
        """File a movie/show under one of the configured Libraries.
        Body: {root_folder_id: int|null}.

        Metadata only — this moves NOTHING on disk. It corrects where the title's
        FUTURE work goes (wishlist drain, RSS instant-grab, upgrades all resolve
        their destination from this column), which is the fix for something that
        landed in the wrong Library. Files already placed stay where they are;
        moving them is a separate, much riskier operation.

        Kept out of /metadata deliberately, like /aka: that endpoint pushes edits
        to Plex/Jellyfin and locks the field there, and a media server has no
        concept of Commissary's Library registry.

        null clears the assignment — a real state, not an error. An unassigned
        title falls back to the primary Library for its kind, the pre-Libraries
        behaviour."""
        from . import get_video_db
        if kind not in ("movie", "show"):
            return jsonify({"error": "bad kind"}), 400
        body = request.get_json(silent=True) or {}
        db = get_video_db()
        rid = body.get("root_folder_id")
        if not db.set_item_root_folder(kind, item_id, rid):
            return jsonify({"error": "Unknown title, or that Library isn't valid for a "
                                     + ("movie" if kind == "movie" else "TV show") + "."}), 400
        return jsonify({"ok": True, "root_folder_id": (int(rid) if rid not in (None, "", "null") else None)})

    @bp.route("/detail/<kind>/<int:item_id>/aka", methods=["PUT"])
    def video_set_aka_titles(kind, item_id):
        """Replace a title's user "also known as" list.
        Body: {titles: [...] | "a\\nb", source?: 'library'|'tmdb'}.

        ``item_id`` is a library row id by default; ``source: 'tmdb'`` says it is
        a TMDB id instead. That matters because these aliases are stored AGAINST
        the tmdb id — the releases they fix are for titles you do NOT own yet, so
        there is frequently no library row to hang one off.

        A Commissary-LOCAL matching aid, deliberately not part of /metadata: that
        endpoint pushes edits to Plex/Jellyfin and locks the field there, which
        would be wrong for something the media server has no concept of. These
        titles only widen what the release-title gate accepts.

        Exists because TMDB's alias coverage is patchy — most visibly for anime,
        where fansub groups release under a translation of the original title
        while TMDB lists a different localised name, leaving no automatic bridge
        between the two."""
        from . import get_video_db
        if kind not in ("movie", "show"):
            return jsonify({"error": "bad kind"}), 400
        body = request.get_json(silent=True) or {}
        db = get_video_db()
        if str(body.get("source") or "").lower() == "tmdb":
            tmdb_id = item_id
        else:
            tmdb_id = db.tmdb_id_for_library_row(kind, item_id)
            if tmdb_id is None:
                return jsonify({"error": "unknown item, or it has no TMDB match yet"}), 404
        stored = db.set_aka_titles(kind, tmdb_id, body.get("titles"))
        if stored is None:
            return jsonify({"error": "could not save"}), 400
        return jsonify({"ok": True, "aka_titles": stored})

    @bp.route("/detail/<kind>/<int:item_id>/lock", methods=["POST"])
    def video_field_lock(kind, item_id):
        """Lock or release one field. Releasing hands it back to the server:
        the next scan re-adopts the server's value."""
        from core.video import metadata as med

        from . import get_video_db
        body = request.get_json(silent=True) or {}
        field = body.get("field")
        if kind not in ("movie", "show") or not field:
            return jsonify({"error": "bad request"}), 400
        db = get_video_db()
        if body.get("locked"):
            locks = db.set_field_lock(kind, item_id, field, True)
            if locks is None:
                return jsonify({"error": "unknown item or field"}), 404
            return jsonify({"ok": True, "locked": locks})
        res = med.release_lock(db, kind, item_id, field)
        if not res.get("ok"):
            return jsonify({"error": res.get("error", "not found")}), 404
        return jsonify(res)

    @bp.route("/detail/<kind>/<int:item_id>/import-lock", methods=["POST"])
    def video_import_lock(kind, item_id):
        """"Lock automatic edits" for a movie or a whole show. Body: {locked}.

        A locked item refuses every UNATTENDED import — a replacement, an
        upgrade, and a brand-new episode alike — so a release whose name or
        season was mis-parsed cannot touch content you have already curated. The
        download still happens and still reports what it found; it just stops at
        placement, as import_failed, naming the lock. Manual import is the way
        past it, which is the review the lock exists to demand.

        Nothing about metadata: that is the /lock route above, which is a
        per-FIELD enrichment lock and unrelated."""
        from . import get_video_db
        if kind not in ("movie", "show"):
            return jsonify({"error": "bad kind"}), 400
        locked = bool((request.get_json(silent=True) or {}).get("locked"))
        if not get_video_db().set_import_lock(kind, item_id, locked):
            return jsonify({"error": "unknown item"}), 404
        return jsonify({"ok": True, "import_locked": locked})

    @bp.route("/detail/show/<int:show_id>/season/<int:season_number>/import-lock",
              methods=["POST"])
    def video_season_import_lock(show_id, season_number):
        """The narrower lock: this SEASON only. Body: {locked}.

        A show-level lock covers everything; this covers one season, so a
        finished season can be sealed while the show keeps acquiring the one
        currently airing. A season pack spanning both imports the unlocked half
        and refuses the rest."""
        from . import get_video_db
        locked = bool((request.get_json(silent=True) or {}).get("locked"))
        if not get_video_db().set_season_import_lock(show_id, season_number, locked):
            return jsonify({"error": "unknown season"}), 404
        return jsonify({"ok": True, "season": season_number, "import_locked": locked})

    @bp.route("/detail/<kind>/<int:item_id>/watched", methods=["POST"])
    def video_set_watched(kind, item_id):
        """Played/unplayed toggle — local watch state + server markPlayed."""
        from core.video import metadata as med

        from . import get_video_db
        if kind not in ("movie", "show"):
            return jsonify({"error": "bad kind"}), 400
        watched = bool((request.get_json(silent=True) or {}).get("watched"))
        res = med.set_watched(get_video_db(), kind, item_id, watched)
        if not res.get("ok"):
            return jsonify({"error": res.get("error", "not found")}), 404
        return jsonify(res)

    @bp.route("/detail/<kind>/<int:item_id>/history", methods=["GET"])
    def video_title_history(kind, item_id):
        """This title's permanent acquisition history (arr-parity P9): grabs,
        imports, upgrades, failures — matched under both the library and TMDB
        identities the title may have been grabbed as."""
        from . import get_video_db
        if kind not in ("movie", "show"):
            return jsonify({"error": "bad kind"}), 400
        db = get_video_db()
        detail = db.movie_detail(item_id) if kind == "movie" else db.show_detail(item_id)
        if not detail:
            return jsonify({"error": "not found"}), 404
        rows = db.title_download_history(kind, library_id=item_id,
                                         tmdb_id=detail.get("tmdb_id"))
        return jsonify({"success": True, "history": rows})

    @bp.route("/detail/show/<int:show_id>", methods=["GET"])
    def video_show_detail(show_id):
        from . import get_video_db
        data = get_video_db().show_detail(show_id)
        if not data:
            return jsonify({"error": "not found"}), 404
        return jsonify(data)

    @bp.route("/detail/movie/<int:movie_id>", methods=["GET"])
    def video_movie_detail(movie_id):
        from . import get_video_db
        data = get_video_db().movie_detail(movie_id)
        if not data:
            return jsonify({"error": "not found"}), 404
        return jsonify(data)

    @bp.route("/detail/show/<int:show_id>/sync", methods=["POST"])
    def video_show_sync(show_id):
        """Per-show Synchronize: re-read THIS show from the server and
        reconcile local rows (adds, updates, prunes vanished episodes; removes
        the show if the server verifiably no longer has it). Admin-gated via
        the blueprint's /sync write rule. Synchronous — a single show reads in
        seconds, and the response carries what changed."""
        from . import get_video_db
        try:
            from core.video.show_sync import ShowSyncError, sync_show
            res = sync_show(get_video_db(), show_id)
            return jsonify({"success": True, **res})
        except ShowSyncError as e:
            return jsonify({"success": False, "error": str(e)}), 409
        except Exception:
            logger.exception("show sync failed for %s", show_id)
            return jsonify({"success": False, "error": "Sync failed — see app.log"}), 500

    @bp.route("/detail/show/<int:show_id>/refresh-art", methods=["POST"])
    def video_show_refresh_art(show_id):
        """Lazy on-view backfill: pull missing season posters / episode art from
        TMDB and cache them. Best-effort — never errors the page."""
        try:
            from core.video.enrichment.engine import get_video_enrichment_engine
            res = get_video_enrichment_engine().refresh_show_art(show_id)
        except Exception:
            logger.exception("refresh-art failed for show %s", show_id)
            res = {"ok": False, "reason": "error"}
        return jsonify(res)

    @bp.route("/detail/movie/<int:movie_id>/refresh-art", methods=["POST"])
    def video_movie_refresh_art(movie_id):
        """Lazy on-view backfill for a movie (cast / genres / backdrop / ratings)."""
        try:
            from core.video.enrichment.engine import get_video_enrichment_engine
            res = get_video_enrichment_engine().refresh_movie_art(movie_id)
        except Exception:
            logger.exception("refresh-art failed for movie %s", movie_id)
            res = {"ok": False, "reason": "error"}
        return jsonify(res)

    @bp.route("/tmdb/<kind>/<int:tmdb_id>", methods=["GET"])
    def video_tmdb_detail(kind, tmdb_id):
        """Full detail for a TMDB title not in the library (the search → detail
        view). May return {redirect:{source,kind,id}} if it's actually owned."""
        if kind not in ("movie", "show"):
            return jsonify({"error": "bad kind"}), 400
        try:
            from core.video.enrichment.engine import get_video_enrichment_engine
            d = get_video_enrichment_engine().tmdb_detail(kind, tmdb_id)
        except Exception:
            logger.exception("tmdb detail failed for %s %s", kind, tmdb_id)
            d = None
        if not d:
            return jsonify({"error": "not found"}), 404
        return jsonify(d)

    @bp.route("/tmdb/show/<int:tv_id>/season/<int:season_number>", methods=["GET"])
    def video_tmdb_season(tv_id, season_number):
        """Lazy per-season episodes for a TMDB (un-owned) show detail."""
        try:
            from core.video.enrichment.engine import get_video_enrichment_engine
            d = get_video_enrichment_engine().tmdb_season(tv_id, season_number)
        except Exception:
            logger.exception("tmdb season failed for %s S%s", tv_id, season_number)
            d = None
        if not d:
            return jsonify({"error": "not found"}), 404
        return jsonify(d)

    @bp.route("/episode/<int:tmdb_id>/<int:season>/<int:episode>", methods=["GET"])
    def video_episode_extra(tmdb_id, season, episode):
        """Episode expand: guest stars + bigger still (by the SHOW's tmdb id)."""
        try:
            from core.video.enrichment.engine import get_video_enrichment_engine
            d = get_video_enrichment_engine().episode_extra(tmdb_id, season, episode)
        except Exception:
            logger.exception("episode extra failed for %s S%sE%s", tmdb_id, season, episode)
            d = None
        if not d:
            return jsonify({"error": "not found"}), 404
        return jsonify(d)

    @bp.route("/person/<int:tmdb_id>", methods=["GET"])
    def video_person_detail(tmdb_id):
        """In-app person page: bio + filmography (each credit annotated owned/not)."""
        try:
            from core.video.enrichment.engine import get_video_enrichment_engine
            d = get_video_enrichment_engine().person_detail(tmdb_id)
        except Exception:
            logger.exception("person detail failed for %s", tmdb_id)
            d = None
        if not d:
            return jsonify({"error": "not found"}), 404
        return jsonify(d)

    @bp.route("/detail/<kind>/<int:item_id>/extras", methods=["GET"])
    def video_detail_extras(kind, item_id):
        """Live TMDB extras (trailer / where-to-watch / similar) for the detail page."""
        if kind not in ("movie", "show"):
            return jsonify({}), 400
        try:
            from core.video.enrichment.engine import get_video_enrichment_engine
            return jsonify(get_video_enrichment_engine().item_extras(kind, item_id))
        except Exception:
            logger.exception("extras failed for %s %s", kind, item_id)
            return jsonify({})
