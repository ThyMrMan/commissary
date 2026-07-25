"""Video library mapping endpoints — the "Libraries" registry (multi-library, P.4).

GET  /api/video/libraries -> discover the active server's Movies/TV sections,
                             plus the user's CONFIGURED Libraries for it (each:
                             {id, server_title, label, path, sort_order}) —
                             including any manually-named YouTube libraries.
                             Open to any video-side profile (the Library tab bar
                             and the download destination picker both need it);
                             non-admins get only the configured registry, with
                             no server discovery and no filesystem paths.
POST /api/video/libraries -> save {movies, tv, youtube} — arrays of Library
                             entries ({id?, server_title, label, path}) for
                             the active server. The scanner then reads only
                             their titles; each entry's ``path`` is where new
                             grabs assigned to it land. ``youtube`` entries
                             have no server-side discovery (YouTube isn't
                             scanned from a Plex/Jellyfin section) — they're
                             named by hand in the Settings UI.
"""

from __future__ import annotations

from flask import jsonify, request

from utils.logging_config import get_logger

logger = get_logger("video_api.libraries")


def register_routes(bp):
    # What a non-admin sees per configured Library: enough to render a tab and
    # pick a download destination, and nothing else. Filesystem paths are
    # settings-only detail, so they stay out of the member-facing payload.
    _MEMBER_LIB_FIELDS = ("id", "server_title", "label", "sort_order", "category")

    @bp.route("/libraries", methods=["GET"])
    def video_libraries():
        from flask import g
        from . import get_video_db
        try:
            from core.video.sources import list_video_libraries, resolve_video_server
            is_admin = bool(getattr(g, "is_admin", getattr(g, "profile_id", 1) == 1))

            if is_admin:
                libs = list_video_libraries() or {"server": None, "movies": [], "tv": []}
                server = libs.get("server") or resolve_video_server()
            else:
                # Members skip server DISCOVERY entirely — it's a live Plex/Jellyfin
                # round-trip that enumerates every section on the server (Settings-only
                # data). They only need the registry the admin already configured.
                server = resolve_video_server()
                libs = {"server": server, "movies": [], "tv": []}

            configured = (get_video_db().list_libraries(server)
                          if server else {"movies": [], "tv": [], "youtube": []})
            if not is_admin:
                configured = {
                    kind: [{k: e.get(k) for k in _MEMBER_LIB_FIELDS} for e in (entries or [])]
                    for kind, entries in configured.items()
                }
            libs["configured"] = configured
            return jsonify(libs)
        except Exception:
            logger.exception("Failed to list video libraries")
            return jsonify({"error": "Failed to list video libraries"}), 500

    @bp.route("/server", methods=["GET"])
    def video_server_status():
        """Which server the video side uses + which of Plex/Jellyfin are configured
        (so the UI can show a picker, or a 'connect a server' message)."""
        try:
            from core.video.sources import (resolve_video_server,
                                             video_plex_config, video_jellyfin_config)
            plex = bool(video_plex_config().get("base_url"))
            jelly = bool(video_jellyfin_config().get("base_url"))
            return jsonify({"server": resolve_video_server(), "plex": plex, "jellyfin": jelly})
        except Exception:
            logger.exception("video server status failed")
            return jsonify({"server": None, "plex": False, "jellyfin": False})

    @bp.route("/server", methods=["POST"])
    def video_server_set():
        """Set the explicit video-side server pick (only meaningful when both Plex
        and Jellyfin are configured)."""
        from . import get_video_db
        body = request.get_json(silent=True) or {}
        choice = body.get("server")
        if choice not in ("plex", "jellyfin"):
            return jsonify({"error": "bad server"}), 400
        get_video_db().set_setting("video_server", choice)
        return jsonify({"status": "saved", "server": choice})

    @bp.route("/service-status", methods=["GET"])
    def video_service_status():
        """Unified sidebar status for the video side: metadata (TMDB/TVDB keys), the active
        media server, and the download preference. Deliberately 'configured'-based (no live
        network probe) so the 5s sidebar poll stays cheap and never hammers Plex/TMDB."""
        from . import get_video_db
        try:
            from core.video import download_config
            from core.video.sources import (resolve_video_server, video_plex_config,
                                             video_jellyfin_config)
            db = get_video_db()
            tmdb = bool((db.get_setting("tmdb_api_key") or "").strip())
            tvdb = bool((db.get_setting("tvdb_api_key") or "").strip())
            server = resolve_video_server()
            plex_ok = bool(video_plex_config().get("base_url"))
            jelly_ok = bool(video_jellyfin_config().get("base_url"))
            dl = download_config.load(db)
            mode = dl.get("download_mode") or "soulseek"
            order = dl.get("hybrid_order") or []
            dl_name = (" → ".join(s.capitalize() for s in order)) if mode == "hybrid" \
                else str(mode).capitalize()
            return jsonify({
                "metadata": {"configured": bool(tmdb and tvdb), "tmdb": tmdb, "tvdb": tvdb,
                             "name": "TMDB / TVDB"},
                "server": {"active": server, "configured": bool(plex_ok or jelly_ok),
                           "plex": plex_ok, "jellyfin": jelly_ok,
                           "name": server.capitalize() if server else "No server"},
                "download": {"configured": True, "mode": mode, "hybrid_order": order,
                             "name": dl_name},
            })
        except Exception:
            logger.exception("video service-status failed")
            return jsonify({
                "metadata": {"configured": False, "name": "TMDB / TVDB"},
                "server": {"active": None, "configured": False, "name": "No server"},
                "download": {"configured": True, "name": "Soulseek"},
            })

    @bp.route("/server-config", methods=["GET"])
    def video_server_config_get():
        """The video side's OWN server connection — its stored creds when set, else
        the values INHERITED (read-only) from music. 'inherited' flags tell the UI a
        field is a placeholder it can override; tokens/keys are returned masked."""
        try:
            from core.video.sources import video_plex_config, video_jellyfin_config
            p, j = video_plex_config(), video_jellyfin_config()

            def mask(v):
                v = v or ""
                return ("•" * 12) if v else ""
            return jsonify({
                "plex": {"base_url": p.get("base_url") or "", "token": mask(p.get("token")),
                         "has_token": bool(p.get("token")), "inherited": p.get("source") == "music"},
                "jellyfin": {"base_url": j.get("base_url") or "", "api_key": mask(j.get("api_key")),
                             "has_key": bool(j.get("api_key")), "inherited": j.get("source") == "music"},
            })
        except Exception:
            logger.exception("video server-config get failed")
            return jsonify({"plex": {}, "jellyfin": {}})

    @bp.route("/server-config", methods=["POST"])
    def video_server_config_set():
        """Save the video side's OWN Plex/Jellyfin creds (NEVER the music server's).
        They live in the app-wide config under video_plex.* / video_jellyfin.* so the
        tokens are encrypted at rest like music's; they used to sit in video.db in
        plaintext. An empty/blank field clears that override → video falls back to
        inheriting music's value. A masked token (all •) is left untouched."""
        from config.settings import config_manager
        from core.video.sources import _VIDEO_SERVER_KEYS, promote_video_server_creds_once
        from . import get_video_db
        body = request.get_json(silent=True) or {}
        promote_video_server_creds_once(get_video_db())

        def is_mask(v):
            return bool(v) and set(str(v)) == {"•"}

        for kind, secret_field in (("plex", "token"), ("jellyfin", "api_key")):
            (cfg_url, cfg_secret), _legacy = _VIDEO_SERVER_KEYS[kind]
            section = body.get(kind) or {}
            if "base_url" in section:
                config_manager.set(cfg_url, (section.get("base_url") or "").strip())
            if secret_field in section:
                val = section.get(secret_field)
                if not is_mask(val):        # a mask means "unchanged" — keep the stored one
                    val = (val or "").strip()
                    if val:
                        config_manager.set(cfg_secret, val)
                    else:
                        # ConfigManager.set() ignores '' on a sensitive path (so a
                        # settings autosave can't wipe a secret), so clearing has to
                        # go through the URL: the override is all-or-nothing, and a
                        # blank base_url turns it off. The old encrypted token stays
                        # behind, inert, until a new one replaces it.
                        config_manager.set(cfg_url, "")
        return jsonify({"status": "saved"})

    @bp.route("/server-config/test", methods=["POST"])
    def video_server_config_test():
        """Test the video side's effective connection for one server, using its OWN
        stored/inherited creds (so it verifies exactly what the video scan will use)."""
        body = request.get_json(silent=True) or {}
        which = body.get("server")
        if which not in ("plex", "jellyfin"):
            return jsonify({"success": False, "error": "bad server"}), 400
        try:
            if which == "plex":
                from core.video.sources import video_plex_config, PLEX_SCAN_TIMEOUT
                cfg = video_plex_config()
                if not cfg.get("base_url") or not cfg.get("token"):
                    return jsonify({"success": False, "error": "Plex URL/token not set"})
                from plexapi.server import PlexServer
                srv = PlexServer(cfg["base_url"], cfg["token"], timeout=PLEX_SCAN_TIMEOUT)
                return jsonify({"success": True, "message": "Connected to " + (srv.friendlyName or "Plex")})
            from core.video.sources import video_jellyfin_config, video_jellyfin_test
            ok, message = video_jellyfin_test(video_jellyfin_config())
            if ok:
                return jsonify({"success": True, "message": message})
            return jsonify({"success": False, "error": message})
        except Exception as e:
            return jsonify({"success": False, "error": str(e) or "connection failed"})

    @bp.route("/jellyfin/users", methods=["GET"])
    def video_jellyfin_users():
        """List the Jellyfin server's users so the video side can pick one (its
        libraries are scoped to that user) — mirrors the music user picker. Uses
        video's own effective Jellyfin creds."""
        from . import get_video_db
        try:
            from core.video.sources import video_jellyfin_config
            cfg = video_jellyfin_config()
            base = (cfg.get("base_url") or "").rstrip("/")
            key = cfg.get("api_key") or ""
            if not base or not key:
                return jsonify({"success": False, "users": []})
            import requests
            r = requests.get(base + "/Users", headers={"X-Emby-Token": key}, timeout=8)
            if r.status_code != 200:
                return jsonify({"success": False, "users": [], "error": "HTTP %d" % r.status_code})
            users = r.json() or []
            out = [{"id": u.get("Id"), "name": u.get("Name"),
                    "admin": bool((u.get("Policy") or {}).get("IsAdministrator"))}
                   for u in users if u.get("Id")]
            selected = get_video_db().get_setting("video_jellyfin_user") or ""
            return jsonify({"success": True, "users": out, "selected": selected})
        except Exception as e:
            return jsonify({"success": False, "users": [], "error": str(e)})

    @bp.route("/jellyfin/user", methods=["POST"])
    def video_jellyfin_user_set():
        """Persist the chosen Jellyfin user (its Id) for the video side."""
        from . import get_video_db
        body = request.get_json(silent=True) or {}
        get_video_db().set_setting("video_jellyfin_user", (body.get("user") or "").strip())
        return jsonify({"status": "saved"})

    @bp.route("/libraries", methods=["POST"])
    def save_video_libraries():
        from . import get_video_db
        try:
            from core.video.sources import resolve_video_server
            body = request.get_json(silent=True) or {}
            server = resolve_video_server()
            if not server:
                return jsonify({"error": "no video server"}), 400
            # ``youtube`` is deliberately NOT accepted: a content_kind='youtube'
            # root_folder was never read back (primary_root_folder maps only
            # movie/show), so the editor that wrote them was decorative. The
            # YouTube destination is the youtube_path scalar.
            configured = get_video_db().save_libraries(
                server, body.get("movies"), body.get("tv"), None)
            return jsonify({"status": "saved", "server": server, "configured": configured})
        except Exception:
            logger.exception("Failed to save video libraries")
            return jsonify({"error": "Failed to save libraries"}), 500
