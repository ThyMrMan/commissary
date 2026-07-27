"""Video-side download SETTINGS (isolated).

Persists the video download configuration in video.db's ``video_settings`` KV
table — fully separate from the music ``soulseek.*`` paths so the two libraries
never share a folder or collide — plus the queue/history/blocklist endpoints the
Downloads page reads. The fulfillment engine itself lives in the wishlist drain
(``core/automation/handlers/video_process_wishlist``) + ``download_monitor``.

Folders:
  - INPUT (download) folder is SHARED with the music side — it's the same
    ``config_manager`` key the music Download Settings use (``soulseek.download_path``),
    so changing it on either side changes both (one physical download dir, simpler
    Docker mounts). We only READ/WRITE that shared key; no music code is touched.
  - OUTPUT (library) folders are video-specific and live in video.db, one per type:
      ``movies_path`` / ``tv_path`` / ``youtube_path``.
  The engine routes a finished download to the library path matching its type. (Legacy
  single video ``transfer_path`` is migrated into ``movies_path`` on first read.)

Connection settings that are genuinely SHARED with music (the slskd instance, the
torrent/usenet clients, Prowlarr indexers) are NOT stored here — those live in the
music config_manager and are surfaced on the shared Indexers tab + shared slskd
block (a deliberate shared boundary, since they're one physical resource).
"""

from __future__ import annotations

from flask import jsonify, request

from utils.logging_config import get_logger

logger = get_logger("video_api.downloads")

# Video-specific OUTPUT library folders (video.db). The INPUT folder is the shared
# music key below — not in this list.
_PATH_KEYS = ("movies_path", "tv_path", "youtube_path")
# The shared input/download dir — the SAME config key the music side reads/writes.
_SHARED_DOWNLOAD_KEY = "soulseek.download_path"

# slskd CONNECTION settings genuinely SHARED with music — one slskd instance serves
# both sides, so these live in the app-wide config_manager (soulseek.*), NOT video.db.
# Deliberately excludes the music download/transfer PATHS and source mode/quality —
# those are video-specific (stored in video.db). Maps the video field name -> the
# shared config key + default. (config_manager is shared app config, not music code.)
_SLSKD_KEYS = {
    "slskd_url": ("soulseek.slskd_url", "http://localhost:5030"),
    "api_key": ("soulseek.api_key", ""),
    "search_timeout": ("soulseek.search_timeout", 60),
    "search_timeout_buffer": ("soulseek.search_timeout_buffer", 15),
    "search_min_delay_seconds": ("soulseek.search_min_delay_seconds", 0),
    "min_peer_upload_speed": ("soulseek.min_peer_upload_speed", 0),
    "max_peer_queue": ("soulseek.max_peer_queue", 0),
    "download_timeout": ("soulseek.download_timeout", 600),   # seconds (UI shows minutes)
    "auto_clear_searches": ("soulseek.auto_clear_searches", True),
}


def _resolve_target(db, kind: str, root_folder_id) -> str:
    """Where a grab of this kind lands (multi-library, P.4): the explicitly
    picked Library (``root_folder_id``, from the interactive "Get" dropdown)
    when given, else the PRIMARY configured Library for the active server,
    else the legacy movies_path/tv_path scalar — unchanged behavior for
    installs with no Libraries configured yet."""
    from core.video.download_pipeline import resolve_download_root
    from core.video.sources import resolve_video_server
    root_folder = None
    if root_folder_id:
        try:
            root_folder = db.get_root_folder(int(root_folder_id))
        except (TypeError, ValueError):
            root_folder = None
    k = "movie" if str(kind or "").lower() in ("movie", "movies", "film", "films") else "show"
    server = resolve_video_server(db)
    primary = db.primary_root_folder(server, k) if server else None
    paths = {"movies_path": db.get_setting("movies_path"),
             "tv_path": db.get_setting("tv_path"),
             "transfer_path": db.get_setting("transfer_path")}
    return resolve_download_root(kind, root_folder=root_folder,
                                 primary_root_folder=primary, paths=paths)


def _resolve_category(db, kind: str, root_folder_id) -> str | None:
    """The torrent/usenet category for a grab of this kind (multi-category,
    P.4): the explicitly picked Library's own category when given, else the
    PRIMARY configured Library's category, else None (the client adapter
    falls back to the global torrent_client.category/usenet_client.category
    setting). Mirrors ``_resolve_target``'s tiers so a grab's destination
    folder and its category always come from the SAME Library."""
    from core.video.download_pipeline import resolve_torrent_category
    from core.video.sources import resolve_video_server
    root_folder = None
    if root_folder_id:
        try:
            root_folder = db.get_root_folder(int(root_folder_id))
        except (TypeError, ValueError):
            root_folder = None
    k = "movie" if str(kind or "").lower() in ("movie", "movies", "film", "films") else "show"
    server = resolve_video_server(db)
    primary = db.primary_root_folder(server, k) if server else None
    return resolve_torrent_category(root_folder=root_folder, primary_root_folder=primary)


def _root_folder_id_for_grab(db, body) -> int | str | None:
    """The Library a manual grab belongs to.

    An explicit pick from the Library dropdown always wins. With none — the
    dropdown hides itself when there's only one Library for the kind, and the
    inline ⬇ buttons on the detail page never render one — fall back to the
    Library the title is ALREADY filed under. This is the manual twin of
    ``video_process_wishlist._root_folder_for_item``, which reads root_folder_id
    straight off the wishlist row; here the payload's media_id/media_source pair
    is the equivalent handle (same convention as ``download_monitor``: a 'tmdb'
    media_source means media_id is a tmdb_id, anything else a library row id).

    Returns None only when the title is unknown or unassigned, which leaves the
    caller's existing primary-Library fallback in charge — unchanged behavior
    for installs with a single Library per kind."""
    explicit = (body or {}).get("root_folder_id")
    if explicit:
        return explicit
    kind = "movie" if str((body or {}).get("kind") or "").lower() in (
        "movie", "movies", "film", "films") else "show"
    media_id = (body or {}).get("media_id")
    if not media_id:
        return None
    if str((body or {}).get("media_source") or "").lower() == "tmdb":
        return db.root_folder_id_for_tmdb(kind, media_id)
    return db.root_folder_id_for_library_row(kind, media_id)


def _want_titles(db, body, title=None):
    """The title(s) a manual search should accept, primary first.

    The unattended paths (wishlist drain, RSS sync, the monitor's requery) have
    always gated on ``ctx['titles']`` — the primary title PLUS its TMDB aliases —
    but the interactive search passed a bare ``body['title']``, so a release named
    by any AKA was rejected as a wrong title only when a human searched for it.
    That is the wrong way round: the manual path is where a user is watching and
    expects it to work.

    Falls back to just the primary title whenever the tmdb id can't be resolved or
    TMDB isn't configured — never fewer titles than before."""
    primary = title if title is not None else (body or {}).get("title")
    out = [primary] if primary else []
    kind = "movie" if str((body or {}).get("kind") or "").lower() in (
        "movie", "movies", "film", "films") else "show"
    if str((body or {}).get("scope") or "").lower() in ("episode", "season", "series", "show"):
        kind = "show"
    tmdb_id = _tmdb_id_from(db, body)
    if not tmdb_id:
        return out or primary
    # User AKAs first — they're the deliberate override, typed precisely because
    # TMDB's own aliases didn't cover the release naming being seen.
    try:
        for a in (db.aka_titles_for_tmdb(kind, tmdb_id) or []):
            if a and a not in out:
                out.append(a)
    except Exception:   # noqa: BLE001 - a matching assist must never break a search
        logger.debug("aka lookup failed for %s %s", kind, tmdb_id, exc_info=True)
    try:
        from core.video.enrichment.engine import get_video_enrichment_engine
        for a in (get_video_enrichment_engine().alt_titles_for(kind, tmdb_id) or []):
            if a and a not in out:
                out.append(a)
    except Exception:   # noqa: BLE001
        logger.debug("alias lookup failed for %s %s", kind, tmdb_id, exc_info=True)
    return out or primary


def _tmdb_id_from(db, body):
    """The tmdb id behind a search/grab payload — media_id is a tmdb id when
    media_source says 'tmdb', otherwise a movies/shows row id."""
    media_id, source = (body or {}).get("media_id"), str((body or {}).get("media_source") or "").lower()
    if not media_id:
        return None
    if source == "tmdb":
        return media_id
    kind = "movie" if str((body or {}).get("kind") or "").lower() in (
        "movie", "movies", "film", "films") else "show"
    try:
        return db.tmdb_id_for_library_row(kind, media_id)
    except Exception:   # noqa: BLE001
        return None


def _episode_hints(db, body, season=None, episode=None):
    """``(want_date, want_absolute)`` for an episode search — the two ways a
    release can identify an episode WITHOUT carrying SxxExx.

    Releases named by air date (daily shows: 'The.Daily.Show.2026.07.08') or by
    ABSOLUTE number (fansub anime: '[SubsPlease] Show - 03') parse to
    ``episode=None``, and the scope check then has nothing to match on and
    rejects them. The unattended paths have always supplied both from
    ``search_context``; the interactive search supplied neither, so searching by
    hand for an anime episode always failed while the hourly drain grabbed it
    fine.

    Deliberately NOT gated on ``series_type`` the way ``search_context`` is.
    Both hints are accept-only — ``has_absolute_episode`` documents that a miss
    means 'not proven', never a rejection — so computing them for every show is
    safe, and it removes the dependency on a show being correctly tagged as
    anime, which is itself a common way for this to go wrong."""
    body = body or {}
    scope = str(body.get("scope") or "").lower()
    if scope and scope not in ("episode",):
        return None, None
    s = body.get("season") if season is None else season
    e = body.get("episode") if episode is None else episode
    tmdb_id = _tmdb_id_from(db, body)
    if not tmdb_id or s is None or e is None:
        return None, None
    want_date = want_absolute = None
    try:
        want_date = db.episode_air_date(tmdb_id, s, e)
    except Exception:   # noqa: BLE001 - a matching assist must never break a search
        logger.debug("air-date hint failed for %s S%sE%s", tmdb_id, s, e, exc_info=True)
    try:
        want_absolute = db.episode_absolute_number(tmdb_id, s, e)
    except Exception:   # noqa: BLE001
        logger.debug("absolute hint failed for %s S%sE%s", tmdb_id, s, e, exc_info=True)
    if want_absolute is None:
        # episode_absolute_number counts the show's own episode rows, so it can
        # only answer for a show already IN the library — searching for one you
        # don't own yet gets nothing, which is exactly when you're searching.
        #
        # For SEASON 1 the absolute number IS the episode number, by definition
        # (specials sit in season 0 and are excluded). Deriving it only for
        # season 1 is safe; guessing for a later season would be a real risk,
        # since has_absolute_episode only ever ACCEPTS — wanting S02E05 and
        # matching a bare "Show - 05" would grab season ONE's episode 5.
        try:
            if int(s) == 1 and int(e) >= 1:
                want_absolute = int(e)
        except (TypeError, ValueError):
            pass
    return want_date, want_absolute


def _parse_text(hit) -> str:
    """What the release parser should read for a hit. Soulseek hits are grouped by
    FOLDER — the folder title carries the show/release name, but on library-style
    shares the episode number, date and quality live in the FILENAME. Join both so
    one parse sees everything ('90 Day Fiancé/Season 12' + '90.Day.Fiance.S12E09.
    1080p.mkv'). Prowlarr/torrent hits have no meaningful filename beyond the title,
    and the join is a no-op when the basename is already the title."""
    title = str((hit or {}).get("title") or "")
    fn = str((hit or {}).get("filename") or "").replace("\\", "/").rstrip("/")
    base = fn.rsplit("/", 1)[-1]
    if base and base.lower() not in title.lower():
        return (title + "/" + base) if title else base
    return title


_PREFERRED_INDEXER_SCORE = 25   # between prefer_repack's +10 and prefer_hdr's +30


def _evaluate_hits(raw, profile, scope, want_season, want_episode, blocked=None, want_year=None,
                   want_title=None, blocked_users=None, want_date=None, want_absolute=None,
                   preferred_indexer_ids=None) -> list:
    """Parse → evaluate → rank a list of raw indexer hits against the quality profile.
    Shared by the mock search and the live slskd start/poll endpoints.

    ``blocked`` = the per-release blocklist as {(username, filename)}; ``blocked_users``
    = uploaders blocked source-wide (every release from them skipped). Both None = look
    them up. Blocked hits stay VISIBLE in manual search (greyed, with the reason — the
    Sonarr behaviour) but are never `accepted`, so every auto-picker skips them; a manual
    grab of one is a deliberate user override.

    ``preferred_indexer_ids`` (multi-library torrent preferences) = the calling
    title's Library's preferred Prowlarr indexer id(s), a SOFT ranking nudge —
    same shape as every other ``prefer_*`` signal (never a hard requirement, so
    a Library with no hits from its preferred tracker never comes up empty)."""
    from core.video.custom_formats import format_score, load_formats
    from core.video.quality_eval import evaluate_release
    from core.video.release_parse import parse_release
    # Custom formats (P3): loaded once per evaluation batch; scored per hit
    # under THIS profile's overrides. Failure = no formats, never a 500.
    try:
        from . import get_video_db as _gdb
        _formats = load_formats(_gdb())
    except Exception:   # noqa: BLE001
        _formats = []
    if blocked is None or blocked_users is None:
        try:
            from . import get_video_db
            db = get_video_db()
            if blocked is None:
                blocked = db.video_blocklist_pairs()
            if blocked_users is None:
                blocked_users = db.blocked_usernames()
        except Exception:   # noqa: BLE001 - filtering is an assist, never a 500
            blocked = blocked or frozenset()
            blocked_users = blocked_users or frozenset()
    results = []
    for hit in raw:
        parsed = parse_release(_parse_text(hit))
        size_gb = round((hit.get("size_bytes") or 0) / (1024 ** 3), 1)
        verdict = evaluate_release(parsed, profile, scope=scope, want_season=want_season,
                                   want_episode=want_episode, size_gb=size_gb, want_year=want_year,
                                   want_title=want_title, want_date=want_date,
                                   want_absolute=want_absolute)
        # Custom formats: matched formats ADD their (per-profile) score; a
        # summed score under the profile's floor hard-rejects (Radarr's
        # min custom format score).
        fscore, fnames = (0, [])
        if _formats:
            fscore, fnames = format_score(_parse_text(hit), _formats, profile)
            verdict = {**verdict, "score": verdict["score"] + fscore}
            floor = (profile or {}).get("min_format_score") or 0
            if floor and verdict["accepted"] and fscore < floor:
                verdict = {**verdict, "accepted": False,
                           "rejected": "Format score %d is below your minimum %d" % (fscore, floor)}
        # Per-Library preferred tracker (multi-library torrent preferences) — a
        # soft nudge, same shape as every prefer_* signal in quality_eval.py.
        if preferred_indexer_ids and hit.get("indexer_id") in preferred_indexer_ids:
            verdict = {**verdict, "score": verdict["score"] + _PREFERRED_INDEXER_SCORE}
        user = hit.get("username")
        is_blocked = bool(user and user in blocked_users) or (user, hit.get("filename")) in blocked
        if user and user in blocked_users:
            verdict = {**verdict, "accepted": False,
                       "rejected": (verdict.get("rejected") or []) + ["Uploader blocklisted"]}
        elif (user, hit.get("filename")) in blocked:
            verdict = {**verdict, "accepted": False,
                       "rejected": (verdict.get("rejected") or []) + ["Blocklisted release"]}
        # Availability = how downloadable the source is (slskd: free slot/queue/speed score
        # from group_video_files; torrents/mock: seeders/peers). Ranks within a quality tier
        # so we grab a free-slot/empty-queue release over one stuck behind a huge queue.
        avail = hit.get("availability")
        if avail is None:
            avail = hit.get("seeders") if hit.get("seeders") is not None else (hit.get("peers") or 0)
        results.append({
            "title": hit.get("title"), "size_gb": size_gb, "size_bytes": hit.get("size_bytes") or 0,
            "seeders": hit.get("seeders"), "peers": hit.get("peers"),
            "username": hit.get("username"), "slots": hit.get("slots"),
            "queue": hit.get("queue"), "speed": hit.get("speed"),
            "filename": hit.get("filename"), "_avail": avail,
            # Folder contents for a pack: the chosen peer's video files (episodes),
            # so the UI can expand a season card and a pack grab can pull them all.
            "files": hit.get("files") or [], "file_count": hit.get("file_count") or 0,
            "folder_size_bytes": hit.get("folder_size_bytes") or 0,
            "quality_label": verdict["quality_label"], "accepted": verdict["accepted"],
            "rejected": verdict["rejected"], "score": verdict["score"], "blocked": is_blocked,
            "format_score": fscore, "formats": fnames,
            "resolution": parsed.get("resolution"), "source": parsed.get("source"),
            "codec": parsed.get("codec"), "hdr": parsed.get("hdr"),
            "audio": parsed.get("audio"), "group": parsed.get("group"),
            "repack": parsed.get("repack") or parsed.get("proper"),
            # torrent/usenet grab carriers (present only for Prowlarr hits) — the magnet/NZB
            # URL + protocol the grab hands to the shared torrent/usenet client.
            "download_url": hit.get("download_url"), "protocol": hit.get("protocol"),
            "indexer_id": hit.get("indexer_id"), "guid": hit.get("guid"),
        })
    # accepted first, then quality-profile score, then availability, then bigger file.
    results.sort(key=lambda r: (r["accepted"], r["score"], r["_avail"], r["size_bytes"]), reverse=True)
    for r in results:
        r.pop("_avail", None)
    # Keep every accepted release, but cap the greyed-out rejects — the structured
    # tv/movie search casts a wide net (whole-season noise) that our scope filter rejects,
    # so without a cap the list would be flooded with wrong-episode releases. A handful of
    # rejects stay visible (with their reason) for a deliberate manual override.
    accepted = [r for r in results if r["accepted"]]
    rejected = [r for r in results if not r["accepted"]]
    return (accepted[:40] + rejected[:15])


def _preferred_indexer_ids_for_root_folder(db, root_folder_id) -> set:
    """The manual-search picker's equivalent of
    video_process_wishlist._preferred_indexer_ids_for_item — resolves straight
    from a root_folder_id (the modal's library picker, not a wishlist item)."""
    if not root_folder_id:
        return set()
    try:
        root = db.get_root_folder(int(root_folder_id))
    except (TypeError, ValueError):
        return set()
    raw = (root or {}).get("preferred_indexer_ids") or ""
    out = set()
    for p in str(raw).split(","):
        p = p.strip()
        if p.isdigit():
            out.add(int(p))
    return out


def _active_episode_keys(db, title) -> set:
    """(season, episode) pairs already downloading/queued for a show (by title) — for
    in-flight dedup so a grab/pack doesn't create a duplicate row for the same episode."""
    import json as _j
    keys = set()
    try:
        for d in (db.get_active_video_downloads() or []):
            if str(d.get("kind") or "").lower() != "show" or str(d.get("title") or "") != str(title):
                continue
            ctx = d.get("search_ctx")
            if isinstance(ctx, str):
                try:
                    ctx = _j.loads(ctx)
                except (ValueError, TypeError):
                    ctx = {}
            ctx = ctx or {}
            if ctx.get("season") is not None and ctx.get("episode") is not None:
                keys.add((ctx["season"], ctx["episode"]))
    except Exception:
        return set()
    return keys


def _search_ints(body):
    def _int(v):
        try:
            return int(v)
        except (TypeError, ValueError):
            return None
    return _int(body.get("season")), _int(body.get("episode")), _int(body.get("season_end"))


def register_routes(bp):
    @bp.route("/downloads/config", methods=["GET"])
    def video_downloads_config():
        from . import get_video_db
        from core.video.download_config import load as load_source
        from config.settings import config_manager
        db = get_video_db()
        out = {k: db.get_setting(k) or "" for k in _PATH_KEYS}
        if not out["movies_path"]:        # migrate the legacy single transfer folder → Movies
            out["movies_path"] = db.get_setting("transfer_path") or ""
        out["download_path"] = config_manager.get(_SHARED_DOWNLOAD_KEY, "") or ""   # shared w/ music
        out.update(load_source(db))   # download_mode + hybrid_order
        return jsonify(out)

    @bp.route("/downloads/config", methods=["POST"])
    def video_downloads_config_save():
        from . import get_video_db
        from core.video.download_config import save as save_source
        db = get_video_db()
        body = request.get_json(silent=True) or {}
        for key in _PATH_KEYS:
            if key in body:
                db.set_setting(key, (str(body.get(key) or "")).strip())
        if "download_path" in body:   # SHARED with music — write the same config key
            from config.settings import config_manager
            config_manager.set(_SHARED_DOWNLOAD_KEY, (str(body.get("download_path") or "")).strip())
        save_source(db, body)         # download_mode + hybrid_order (validated)
        return jsonify({"status": "saved"})

    # ── import lists (arr-parity P6). Nested under /downloads/config so the
    #    blueprint's write-admin gate covers mutations automatically. ─────────
    @bp.route("/downloads/config/import-lists", methods=["GET"])
    def video_import_lists_get():
        from core.video.import_lists import load_lists

        from . import get_video_db
        return jsonify({"lists": load_lists(get_video_db())})

    @bp.route("/downloads/config/import-lists", methods=["POST"])
    def video_import_lists_save():
        from core.video.import_lists import save_list

        from . import get_video_db
        entry = save_list(get_video_db(), request.get_json(silent=True) or {})
        if not entry:
            return jsonify({"success": False,
                            "error": "A list needs a valid source (and a list id/ref)."}), 400
        return jsonify({"success": True, **entry})

    @bp.route("/downloads/config/import-lists/<int:list_id>", methods=["DELETE"])
    def video_import_lists_delete(list_id):
        from core.video.import_lists import delete_list

        from . import get_video_db
        if not delete_list(get_video_db(), list_id):
            return jsonify({"success": False, "error": "Unknown list."}), 404
        return jsonify({"success": True})

    # ── mass rename (arr-parity P7). Under /organization so the blueprint's
    #    admin gate covers it (renaming the library is management). ───────────
    @bp.route("/organization/rename/preview", methods=["GET"])
    def video_rename_preview():
        """Kick off (or report) the background rename preview. Scanning a big
        library resolves every stored path against the filesystem, which is too
        slow to block the request — so it runs on a worker and the UI polls this.
        Returns {success, ready, done, total, entries?, unresolved?, error?}."""
        from core.video.mass_rename import start_preview
        st = start_preview()
        # If a fresh result is already sitting there, hand it straight back.
        ready = (not st["running"]) and (st["result"] is not None or st["error"])
        out = {"success": True, "ready": bool(ready),
               "done": st["done"], "total": st["total"]}
        if st["error"]:
            out.update(success=False, error=st["error"])
        elif ready and st["result"]:
            out.update(st["result"])
        return jsonify(out)

    @bp.route("/organization/rename/preview/status", methods=["GET"])
    def video_rename_preview_status():
        """Poll the in-flight preview without starting a new one."""
        from core.video.mass_rename import preview_state
        st = preview_state()
        ready = (not st["running"]) and (st["result"] is not None or st["error"])
        out = {"success": True, "ready": bool(ready),
               "running": st["running"], "done": st["done"], "total": st["total"]}
        if st["error"]:
            out.update(success=False, error=st["error"])
        elif ready and st["result"]:
            out.update(st["result"])
        return jsonify(out)

    @bp.route("/organization/rename/apply", methods=["POST"])
    def video_rename_apply():
        """Apply renames from a fresh preview. Body: {keys?: [...]} — omitted
        keys means everything the preview found."""
        from core.video.mass_rename import apply as apply_renames
        body = request.get_json(silent=True) or {}
        res = apply_renames(body.get("keys"))
        if res.get("status") == "skipped":
            return jsonify({"success": False, "error": "A rename run is already in progress."}), 409
        return jsonify({"success": True, **res})

    @bp.route("/downloads/blocklist", methods=["GET"])
    def video_downloads_blocklist():
        """The release blocklist — exact remote files that will never be re-picked."""
        from . import get_video_db
        return jsonify({"success": True, "items": get_video_db().list_video_blocklist()})

    @bp.route("/downloads/indexers", methods=["GET"])
    def video_downloads_indexers():
        """The configured Prowlarr indexers, so the Preferred-Trackers setting can
        offer a pick-list instead of demanding numeric ids the app never showed.

        Identity fields only — never the indexer URL or API key. Admin-gated via
        the blueprint's prefix list, matching the other indexer/credential
        surfaces. An unconfigured or unreachable Prowlarr returns an empty list
        with ``configured`` false, which the UI falls back on rather than
        pretending there are no trackers."""
        from core.video.prowlarr_search import is_configured, list_indexers
        try:
            return jsonify({"configured": is_configured(), "indexers": list_indexers()})
        except Exception:
            logger.exception("failed to list prowlarr indexers")
            return jsonify({"configured": False, "indexers": []})

    @bp.route("/downloads/blocklist", methods=["POST"])
    def video_downloads_blocklist_add():
        """Manually block a release. Body: {download_id} (a failed queue row),
        {history_id} (a history row), or raw {username, filename, ...}."""
        from . import get_video_db
        db = get_video_db()
        body = request.get_json(silent=True) or {}
        row = None
        if body.get("download_id"):
            dl = db.get_video_download(body["download_id"])
            if dl:
                import json as _j
                try:
                    ctx = _j.loads(dl.get("search_ctx") or "{}") or {}
                except (ValueError, TypeError):
                    ctx = {}
                row = {"kind": dl.get("kind"), "title": dl.get("title"),
                       "media_id": dl.get("media_id"), "media_source": dl.get("media_source"),
                       "season_number": ctx.get("season"), "episode_number": ctx.get("episode"),
                       "username": dl.get("username"), "filename": dl.get("filename"),
                       "release_title": dl.get("release_title"),
                       "reason": body.get("reason") or dl.get("error") or "Blocked by user"}
        elif body.get("history_id"):
            h = db.download_history_detail(body["history_id"])
            if h:
                row = {"kind": h.get("kind"), "title": h.get("title"),
                       "media_id": h.get("media_id"), "media_source": h.get("media_source"),
                       "season_number": h.get("season_number"), "episode_number": h.get("episode_number"),
                       "username": h.get("username"), "filename": h.get("filename"),
                       "release_title": h.get("release_title"),
                       "reason": body.get("reason") or h.get("error") or "Blocked by user"}
        elif body.get("username") and body.get("filename"):
            row = {k: body.get(k) for k in ("kind", "title", "media_id", "media_source",
                                            "season_number", "episode_number", "username",
                                            "filename", "release_title", "reason")}
            row.setdefault("reason", "Blocked by user")
        # scope='source' → block the whole UPLOADER (peer), so every future search skips
        # them, not just this one file (Boulder: 'blacklist a source on a completed download').
        if str(body.get("scope") or "").lower() == "source":
            username = (row or {}).get("username") or body.get("username")
            if not username:
                return jsonify({"success": False, "error": "That row has no uploader to block."}), 400
            rid = db.block_video_source(username, reason=body.get("reason") or "Uploader blocked")
            return jsonify({"success": bool(rid), "id": rid, "scope": "source", "username": username})
        if not row or not row.get("username") or not row.get("filename"):
            return jsonify({"success": False,
                            "error": "That row has no release to block."}), 400
        rid = db.add_video_blocklist(row)
        return jsonify({"success": bool(rid), "id": rid})

    @bp.route("/downloads/blocklist/<int:row_id>", methods=["DELETE"])
    def video_downloads_blocklist_remove(row_id):
        from . import get_video_db
        return jsonify({"success": get_video_db().remove_video_blocklist(row_id)})

    @bp.route("/downloads/blocklist/clear", methods=["POST"])
    def video_downloads_blocklist_clear():
        from . import get_video_db
        return jsonify({"success": True, "removed": get_video_db().clear_video_blocklist()})

    @bp.route("/downloads/history", methods=["GET"])
    def video_downloads_history():
        """Paged permanent history of grabs (movies + episodes + YouTube). ?kind=
        movie|show|youtube, ?search=, ?outcome=, ?root_folder_id= (a configured
        Library — e.g. Anime, not just the movie/show kind split), ?page=, ?limit=.
        Always returns counts."""
        from . import get_video_db
        try:
            db = get_video_db()
            kind = request.args.get("kind")
            root_folder_id = request.args.get("root_folder_id")
            try:
                root_folder_id = int(root_folder_id) if root_folder_id else None
            except (TypeError, ValueError):
                root_folder_id = None
            res = db.query_download_history(
                kind=kind if kind in ("movie", "show", "youtube") else None,
                search=request.args.get("search", ""),
                outcome=request.args.get("outcome") or None,
                root_folder_id=root_folder_id,
                page=request.args.get("page", 1), limit=request.args.get("limit", 40))
            return jsonify({"success": True, "counts": db.download_history_counts(), **res})
        except Exception:
            logger.exception("Failed to list video download history")
            return jsonify({"success": False, "error": "Failed to load history"}), 500

    @bp.route("/downloads/history/<int:history_id>", methods=["GET"])
    def video_downloads_history_detail(history_id):
        from . import get_video_db
        d = get_video_db().download_history_detail(history_id)
        if not d:
            return jsonify({"success": False, "error": "not found"}), 404
        return jsonify({"success": True, "item": d})

    @bp.route("/downloads/history/<int:history_id>", methods=["DELETE"])
    def video_downloads_history_delete(history_id):
        """Forget one grab — the 'Re-download' action. Removing its history row lets the
        scans re-add + re-grab it (useful after you've deleted the file)."""
        from . import get_video_db
        return jsonify({"success": get_video_db().delete_download_history(history_id)})

    @bp.route("/downloads/history/clear", methods=["POST"])
    def video_downloads_history_clear():
        """Clear the permanent history (all, or one kind via {kind})."""
        from . import get_video_db
        body = request.get_json(silent=True) or {}
        n = get_video_db().clear_download_history(kind=body.get("kind"))
        return jsonify({"success": True, "removed": n})

    @bp.route("/downloads/meta/<kind>/<int:tmdb_id>", methods=["GET"])
    def video_download_meta(kind, tmdb_id):
        """Lazy TMDB detail for a download's expand drawer (logo, cast w/ photos, trailer,
        where-to-watch, rating/runtime/genres…), keyed by the grabbed title's TMDB id.
        Best-effort — the drawer still shows the download facts without it."""
        if kind not in ("movie", "show"):
            return jsonify({}), 400
        try:
            from core.video.enrichment.engine import get_video_enrichment_engine
            d = get_video_enrichment_engine().tmdb_full_detail(kind, tmdb_id) or {}
            if not d:
                return jsonify({})
            extras = d.get("_extras") or {}
            tr = extras.get("trailer") or {}
            director = next((c.get("name") for c in (d.get("crew") or [])
                             if (c.get("job") or "").lower() in ("director", "creator")), None)
            # episode-specific detail (still + that episode's own title/overview/air date) when a
            # specific episode is downloading — more relevant than the show synopsis.
            episode = None
            sn, en = request.args.get("season"), request.args.get("episode")
            if kind == "show" and sn and en:
                try:
                    season = get_video_enrichment_engine().tmdb_season(tmdb_id, int(sn)) or {}
                    ep = next((e for e in (season.get("episodes") or [])
                               if str(e.get("episode_number")) == str(int(en))), None)
                    if ep:
                        episode = {"season": int(sn), "episode": int(en), "title": ep.get("title"),
                                   "overview": ep.get("overview"), "air_date": ep.get("air_date"),
                                   "still_url": ep.get("still_url")}
                except (ValueError, TypeError):
                    pass
            return jsonify({
                "title": d.get("title"), "overview": d.get("overview"), "tagline": d.get("tagline"),
                "backdrop_url": d.get("backdrop_url"), "logo": d.get("logo"),
                "genres": d.get("genres") or [], "rating": d.get("rating"),
                "runtime_minutes": d.get("runtime_minutes"), "year": d.get("year"),
                "network": d.get("network"), "studio": d.get("studio"),
                "status": d.get("status"), "director": director, "episode": episode,
                "cast": [{"name": c.get("name"), "character": c.get("character"), "photo": c.get("photo")}
                         for c in (d.get("cast") or [])[:10]],
                "trailer_url": ("https://www.youtube.com/watch?v=" + tr["key"]) if tr.get("key") else None,
                "providers": (extras.get("providers") or [])[:6],
                "providers_link": extras.get("providers_link"),
            })
        except Exception:
            logger.exception("download meta failed for %s %s", kind, tmdb_id)
            return jsonify({})

    @bp.route("/downloads/yt-meta/<video_id>", methods=["GET"])
    def video_download_yt_meta(video_id):
        """Cached extra detail for a YouTube download's drawer (duration / views / thumbnail)."""
        from . import get_video_db
        try:
            return jsonify(get_video_db().youtube_video_detail(video_id) or {})
        except Exception:
            logger.exception("yt meta failed for %s", video_id)
            return jsonify({})

    @bp.route("/downloads/quality", methods=["GET"])
    def video_quality_profile():
        from . import get_video_db
        from core.video.quality_profile import load
        return jsonify(load(get_video_db()))

    @bp.route("/downloads/quality", methods=["POST"])
    def video_quality_profile_save():
        from . import get_video_db
        from core.video.quality_profile import save
        body = request.get_json(silent=True) or {}
        return jsonify(save(get_video_db(), body))

    # ── named quality profiles (per-title assignment; arr-parity P2) ─────────
    @bp.route("/downloads/quality/profiles", methods=["GET"])
    def video_quality_profiles_list():
        """Every selectable profile, Default (id 0) first."""
        from . import get_video_db
        from core.video.quality_profile import list_profiles
        return jsonify({"profiles": list_profiles(get_video_db())})

    @bp.route("/downloads/quality/profiles", methods=["POST"])
    def video_quality_profiles_save():
        """Create (no id) or update (id) a named profile; id 0 = the Default."""
        from . import get_video_db
        from core.video.quality_profile import save_named
        body = request.get_json(silent=True) or {}
        entry = save_named(get_video_db(), body.get("id"), body.get("name"), body.get("profile"))
        return jsonify({"success": True, **entry})

    @bp.route("/downloads/quality/profiles/<int:profile_id>", methods=["DELETE"])
    def video_quality_profiles_delete(profile_id):
        """Remove a named profile. Titles pointing at it fall back to Default."""
        from . import get_video_db
        from core.video.quality_profile import delete_named
        if not delete_named(get_video_db(), profile_id):
            return jsonify({"success": False, "error": "Unknown profile (Default can't be deleted)."}), 404
        return jsonify({"success": True})

    # ── custom formats (scored release matchers; arr-parity P3) ──────────────
    @bp.route("/downloads/quality/formats", methods=["GET"])
    def video_custom_formats_list():
        from core.video.custom_formats import load_formats

        from . import get_video_db
        return jsonify({"formats": load_formats(get_video_db())})

    @bp.route("/downloads/quality/formats", methods=["POST"])
    def video_custom_formats_save():
        """Create (no id) or update a format:
        {id?, name, include: [term|/regex/], exclude: [...], score}."""
        from core.video.custom_formats import save_format

        from . import get_video_db
        f = save_format(get_video_db(), request.get_json(silent=True) or {})
        if not f:
            return jsonify({"success": False,
                            "error": "A format needs a name and at least one term."}), 400
        return jsonify({"success": True, **f})

    @bp.route("/downloads/quality/formats/<int:format_id>", methods=["DELETE"])
    def video_custom_formats_delete(format_id):
        from core.video.custom_formats import delete_format

        from . import get_video_db
        if not delete_format(get_video_db(), format_id):
            return jsonify({"success": False, "error": "Unknown format."}), 404
        return jsonify({"success": True})

    @bp.route("/detail/<kind>/<int:library_id>/quality-profile", methods=["PUT"])
    def video_title_quality_profile(kind, library_id):
        """Assign a quality profile to an owned movie/show (0/null = Default).
        The title's wishlist rows follow so in-flight wishes are judged the
        same way."""
        from . import get_video_db
        if kind not in ("movie", "show"):
            return jsonify({"success": False, "error": "kind must be movie|show"}), 400
        body = request.get_json(silent=True) or {}
        ok = get_video_db().set_title_quality_profile(kind, library_id, body.get("profile_id"))
        if not ok:
            return jsonify({"success": False, "error": "Title not found."}), 404
        return jsonify({"success": True})

    @bp.route("/detail/show/<int:library_id>/series-type", methods=["PUT"])
    def video_show_series_type(library_id):
        """Set a show's series type (P8): standard | daily | anime. Drives how the
        drain queries for its episodes (SxxExx vs air date vs absolute number).

        Body may carry ``source: 'tmdb'``, in which case ``library_id`` is a TMDB
        id for a show NOT in the library and the value is stored as an override
        keyed by that id. Which matters: series type decides how episodes are
        HUNTED, so it's most needed while you're still trying to acquire the
        show — exactly when no library row exists to write it to."""
        from . import get_video_db
        body = request.get_json(silent=True) or {}
        st = str(body.get("series_type") or "").strip().lower()
        if st not in ("standard", "daily", "anime"):
            return jsonify({"success": False, "error": "series_type must be standard|daily|anime"}), 400
        db = get_video_db()
        if str(body.get("source") or "").lower() == "tmdb":
            if db.set_series_type_override(library_id, st) is None:
                return jsonify({"success": False, "error": "Could not save."}), 400
            return jsonify({"success": True})
        if not db.set_show_series_type(library_id, st):
            return jsonify({"success": False, "error": "Show not found."}), 404
        return jsonify({"success": True})

    @bp.route("/organization", methods=["GET"])
    def video_organization():
        """The library-organisation settings: naming templates + post-process toggles."""
        from . import get_video_db
        from core.video.organization import load
        return jsonify(load(get_video_db()))

    @bp.route("/organization", methods=["POST"])
    def video_organization_save():
        from . import get_video_db
        from core.video.organization import save
        body = request.get_json(silent=True) or {}
        return jsonify(save(get_video_db(), body))

    @bp.route("/downloads/evaluate", methods=["POST"])
    def video_quality_evaluate():
        """Judge a video file the user already owns against their quality profile —
        powers the Download modal's 'In your library · … (below your target)' line.
        Body: {"file": {resolution, video_codec, …}}."""
        from . import get_video_db
        from core.video.quality_eval import evaluate_owned
        from core.video.quality_profile import load
        body = request.get_json(silent=True) or {}
        profile = load(get_video_db())
        return jsonify(evaluate_owned(body.get("file"), profile))

    def _profile_for_request(db, src):
        """The quality profile a get-modal search/grab should be judged under:
        the title's own assignment (resolved from tmdb_id when the client sends
        it) → else the Default profile (P2, per-title profiles)."""
        from core.video.quality_profile import profile_by_id
        pid = src.get("quality_profile_id")
        if pid in (None, "", 0, "0"):
            tmdb = src.get("tmdb_id") or src.get("media_id")
            scope = str(src.get("scope") or src.get("kind") or "movie").lower()
            kind = "movie" if scope == "movie" else "show"
            if tmdb and str(src.get("media_source") or "tmdb") == "tmdb":
                try:
                    pid = db.quality_profile_id_for(kind, tmdb_id=int(tmdb))
                except (TypeError, ValueError):
                    pid = None
        return profile_by_id(db, pid), pid

    @bp.route("/downloads/search", methods=["POST"])
    def video_downloads_search():
        """Search a scope (movie / episode / season / series) and return candidates
        ranked + filtered against the stored quality profile. The indexer is mocked
        for now (core.video.mock_search) — the parse→evaluate→rank pipeline is real,
        so swapping in slskd/Prowlarr later needs no change here.
        Body: {scope, title, year?, season?, episode?, season_end?}."""
        from . import get_video_db
        from core.video.mock_search import mock_search

        body = request.get_json(silent=True) or {}
        scope = str(body.get("scope") or "movie").lower()
        title = body.get("title") or ""
        source = str(body.get("source") or "").lower()
        want_season, want_episode, season_end = _search_ints(body)
        profile, _pid = _profile_for_request(get_video_db(), body)
        live = False
        if source == "soulseek":
            from core.video.slskd_search import build_query, slskd_search
            sres = slskd_search(build_query(scope, title, year=body.get("year"),
                                            season=want_season, episode=want_episode))
            if not sres.get("configured"):
                return jsonify({"scope": scope, "results": [], "error": "slskd isn't configured — set its URL on Settings → Downloads."})
            if sres.get("error"):
                return jsonify({"scope": scope, "results": [], "error": "slskd: " + str(sres["error"])})
            raw, live = sres["hits"], True
        elif source in ("torrent", "usenet"):
            from core.video.prowlarr_search import prowlarr_search
            pres = prowlarr_search(scope, title, year=body.get("year"),
                                   season=want_season, episode=want_episode, source=source)
            if not pres.get("configured"):
                return jsonify({"scope": scope, "results": [],
                                "error": "Prowlarr isn't configured — set its URL + key on Settings → Downloads."})
            if pres.get("error"):
                return jsonify({"scope": scope, "results": [], "error": "Prowlarr: " + str(pres["error"])})
            raw, live = pres["hits"], True
        else:
            raw = mock_search(scope, title, year=body.get("year"), season=want_season,
                              episode=want_episode, season_end=season_end, source=source)
        preferred = _preferred_indexer_ids_for_root_folder(get_video_db(), _root_folder_id_for_grab(get_video_db(), body))
        _ep_hints = _episode_hints(get_video_db(), body, want_season, want_episode)
        return jsonify({"scope": scope, "live": live,
                        "results": _evaluate_hits(raw, profile, scope, want_season, want_episode, want_year=body.get("year"), want_title=_want_titles(get_video_db(), body),
                                                 want_date=_ep_hints[0], want_absolute=_ep_hints[1],
                                                 preferred_indexer_ids=preferred)})

    @bp.route("/downloads/search/start", methods=["POST"])
    def video_downloads_search_start():
        """Begin a search. For mock sources the results come back immediately; for
        Soulseek it returns a slskd search id to poll (results trickle in over ~30s,
        like the music side) — fixes 'no results' from waiting too briefly."""
        from . import get_video_db
        from core.video.mock_search import mock_search
        body = request.get_json(silent=True) or {}
        scope = str(body.get("scope") or "movie").lower()
        title = body.get("title") or ""
        source = str(body.get("source") or "").lower()
        want_season, want_episode, season_end = _search_ints(body)
        # Air date / absolute number, for releases that carry no SxxExx.
        _ep_hints = _episode_hints(get_video_db(), body, want_season, want_episode)

        if source == "soulseek":
            from core.video.slskd_search import (
                _INTERACTIVE_MAX_WAIT_SECONDS, build_query, search_timeout_ms, start_search)
            res = start_search(build_query(scope, title, year=body.get("year"),
                                           season=want_season, episode=want_episode),
                               max_throttle_wait=_INTERACTIVE_MAX_WAIT_SECONDS)
            if not res.get("configured"):
                return jsonify({"error": "slskd isn't configured — set its URL on Settings → Downloads."})
            if res.get("error"):
                return jsonify({"error": "slskd: " + str(res["error"])})
            # how long the client should keep polling (slskd keeps searching this long).
            return jsonify({"id": res["id"], "live": True, "complete": False,
                            "poll_ms": search_timeout_ms() + 8000})
        profile, _pid = _profile_for_request(get_video_db(), body)
        if source in ("torrent", "usenet"):
            # Prowlarr is synchronous — like the old mock, results come back in one shot
            # (no polling id), so the client renders immediately.
            from core.video.prowlarr_search import prowlarr_search
            pres = prowlarr_search(scope, title, year=body.get("year"),
                                   season=want_season, episode=want_episode, source=source)
            if not pres.get("configured"):
                return jsonify({"error": "Prowlarr isn't configured — set its URL + key on Settings → Downloads."})
            if pres.get("error"):
                return jsonify({"error": "Prowlarr: " + str(pres["error"])})
            preferred = _preferred_indexer_ids_for_root_folder(get_video_db(), _root_folder_id_for_grab(get_video_db(), body))
            return jsonify({"id": None, "live": True, "complete": True,
                            "results": _evaluate_hits(pres["hits"], profile, scope, want_season, want_episode, want_year=body.get("year"), want_title=_want_titles(get_video_db(), body),
                                                 want_date=_ep_hints[0], want_absolute=_ep_hints[1],
                                                 preferred_indexer_ids=preferred)})
        # remaining mock sources (e.g. youtube placeholder) resolve in one shot
        raw = mock_search(scope, title, year=body.get("year"), season=want_season,
                          episode=want_episode, season_end=season_end, source=source)
        return jsonify({"id": None, "live": False, "complete": True,
                        "results": _evaluate_hits(raw, profile, scope, want_season, want_episode, want_year=body.get("year"), want_title=_want_titles(get_video_db(), body),
                                                 want_date=_ep_hints[0], want_absolute=_ep_hints[1])})

    @bp.route("/downloads/search/poll", methods=["GET"])
    def video_downloads_search_poll():
        """Current ranked results for an in-flight slskd search. Query: id, scope,
        title, season?, episode?. The client polls until it stops growing or times out."""
        from . import get_video_db
        from core.video.slskd_search import poll_search
        sid = request.args.get("id")
        scope = str(request.args.get("scope") or "movie").lower()
        want_season, want_episode, _ = _search_ints(request.args)
        if not sid:
            return jsonify({"results": [], "live": True, "total_files": 0})
        profile, _pid = _profile_for_request(get_video_db(), request.args)
        _poll_hints = _episode_hints(get_video_db(), request.args, want_season, want_episode)
        polled = poll_search(sid)
        return jsonify({"live": True, "total_files": polled["total_files"],
                        "results": _evaluate_hits(polled["hits"], profile, scope, want_season, want_episode, want_year=request.args.get("year"),
                                                 want_title=_want_titles(get_video_db(), request.args,
                                                                         request.args.get("title")),
                                                 want_date=_poll_hints[0], want_absolute=_poll_hints[1])})

    @bp.route("/downloads/grab", methods=["POST"])
    def video_downloads_grab():
        """Start a real download of a chosen release and track it. v1: Soulseek only.
        Body: {kind, title, release_title, source, username, filename, size_bytes,
        quality_label}."""
        from . import get_video_db
        from config.settings import config_manager
        from core.video.download_monitor import ensure_started
        from core.video.slskd_download import start_download

        body = request.get_json(silent=True) or {}
        source = str(body.get("source") or "soulseek").lower()
        if source not in ("soulseek", "torrent", "usenet"):
            return jsonify({"ok": False, "error": "Unsupported download source."}), 400
        username, filename = body.get("username"), body.get("filename")
        if source == "soulseek" and (not username or not filename):
            return jsonify({"ok": False, "error": "Missing the release's source info."}), 400
        if source in ("torrent", "usenet") and not body.get("download_url"):
            return jsonify({"ok": False, "error": "Missing the release's download URL."}), 400

        db = get_video_db()
        if str(body.get("kind") or "").lower() == "youtube":
            target = db.get_setting("youtube_path") or ""
        else:
            target = _resolve_target(db, body.get("kind"), _root_folder_id_for_grab(db, body))
        from core.video import disk_guard, organization
        ok_room, free = disk_guard.has_room(target, organization.load(get_video_db()))
        if not ok_room:
            return jsonify({"ok": False, "error": "Drive is nearly full (%.1f GB free) — "
                            "below your minimum free space setting." % (free or 0)}), 507
        if not target:
            return jsonify({"ok": False, "error": "Set the library folder for this type on Settings → Downloads."}), 400

        # In-flight dedup — if this exact episode is already downloading/queued, don't
        # start a duplicate (e.g. grabbing an episode that a pack grab already queued).
        _ctx = body.get("search_ctx") if isinstance(body.get("search_ctx"), dict) else {}
        if (str(body.get("kind") or "").lower() == "show"
                and _ctx.get("season") is not None and _ctx.get("episode") is not None
                and (_ctx["season"], _ctx["episode"]) in _active_episode_keys(db, body.get("title") or "")):
            return jsonify({"ok": True, "already": True})

        import json as _json
        from core.video.slskd_search import build_query
        ctx = body.get("search_ctx") if isinstance(body.get("search_ctx"), dict) else {}
        _prof, _pid = _profile_for_request(db, body)
        common = {
            "kind": str(body.get("kind") or "movie"), "title": body.get("title"),
            "release_title": body.get("release_title") or body.get("filename") or body.get("title"),
            "size_bytes": int(body.get("size_bytes") or 0), "quality_label": body.get("quality_label"),
            "target_dir": target, "status": "downloading",
            "media_id": (str(body.get("media_id")) if body.get("media_id") is not None else None),
            "media_source": body.get("media_source"), "year": body.get("year"),
            "poster_url": body.get("poster_url"), "search_ctx": _json.dumps(ctx), "attempts": 0,
            "quality_profile_id": _pid,   # the profile this grab is judged under (P2)
        }
        if source == "soulseek":
            started = start_download(username, filename, body.get("size_bytes") or 0)
            if not started.get("ok"):
                return jsonify({"ok": False, "error": started.get("error") or "slskd refused the download."}), 502
            # The OTHER accepted results become the retry pool; the search context drives
            # the alternate-query requery when the pool runs dry.
            candidates = [c for c in (body.get("candidates") or []) if isinstance(c, dict) and c.get("filename") != filename]
            first_query = build_query(ctx.get("scope") or body.get("kind") or "movie", ctx.get("title") or body.get("title"),
                                      year=ctx.get("year"), season=ctx.get("season"), episode=ctx.get("episode"))
            dl_id = db.add_video_download({**common, "source": "soulseek", "username": username, "filename": filename,
                                           "candidates": _json.dumps(candidates),
                                           "tried_queries": _json.dumps([first_query] if first_query else []),
                                           "tried_files": _json.dumps([filename])})
        else:
            # torrent / usenet — hand the magnet/NZB to the SHARED download client; the monitor
            # tracks progress + completion by client_ref. No Soulseek-style alternate requery.
            from core.video.client_grab import grab
            category = None
            if str(body.get("kind") or "").lower() != "youtube":
                category = _resolve_category(db, body.get("kind"), _root_folder_id_for_grab(db, body))
            res = grab(source, body.get("download_url"), category=category)
            if not res.get("ok"):
                return jsonify({"ok": False, "error": res.get("error") or "The download client refused it."}), 502
            dl_id = db.add_video_download({**common, "source": source,
                                           "username": body.get("username"),   # indexer name (display only)
                                           "filename": body.get("release_title") or body.get("title"),
                                           "client_ref": res["ref"],
                                           "candidates": _json.dumps([]), "tried_queries": _json.dumps([]),
                                           "tried_files": _json.dumps([])})
        ensure_started(get_video_db)
        return jsonify({"ok": True, "id": dl_id})

    @bp.route("/downloads/grab-pack", methods=["POST"])
    def video_downloads_grab_pack():
        """Grab a whole season pack: fan the folder's episode files out into individual
        episode downloads so each imports through the normal per-episode pipeline
        (parse → ffprobe-verify → template rename → file into TV/Show/Season).
        Body: {username, files:[{filename, size_bytes}], title, media_id,
        media_source, year, poster_url, quality_label}."""
        from . import get_video_db
        from core.video.download_monitor import ensure_started
        from core.video.slskd_download import start_download
        from core.video.release_parse import parse_release
        from core.video.slskd_search import build_query
        import json as _json
        import os as _os

        body = request.get_json(silent=True) or {}
        username = body.get("username")
        files = [f for f in (body.get("files") or []) if isinstance(f, dict) and f.get("filename")]
        if not username or not files:
            return jsonify({"ok": False, "error": "Missing the pack's source info."}), 400

        db = get_video_db()
        target = _resolve_target(db, "show", _root_folder_id_for_grab(db, body))
        from core.video import disk_guard, organization
        ok_room, free = disk_guard.has_room(target, organization.load(get_video_db()))
        if not ok_room:
            return jsonify({"ok": False, "error": "Drive is nearly full (%.1f GB free) — "
                            "below your minimum free space setting." % (free or 0)}), 507
        if not target:
            return jsonify({"ok": False, "error": "Set the TV library folder on Settings → Downloads."}), 400

        title = body.get("title") or ""
        # Skip episodes already in the library (owned) OR already downloading/queued
        # (in-flight) so a pack doesn't re-download what you have or duplicate a row.
        owned = set()
        if body.get("media_id") is not None and str(body.get("media_source") or "").lower() != "tmdb":
            try:
                owned = db.owned_episode_keys(int(body["media_id"]))
            except (ValueError, TypeError):
                owned = set()
        in_flight = _active_episode_keys(db, title)

        started, ids, skipped = 0, [], 0
        for f in files:
            fn = f.get("filename")
            parsed = parse_release(_os.path.basename(str(fn).replace("\\", "/")))
            sn, en = parsed.get("season"), parsed.get("episode")
            if sn is None or en is None:
                skipped += 1
                continue   # not a parseable single episode (samples/extras) — skip
            if (sn, en) in owned or (sn, en) in in_flight:
                skipped += 1
                continue   # already in the library or already downloading — don't dup
            res = start_download(username, fn, f.get("size_bytes") or 0)
            if not res.get("ok"):
                skipped += 1
                continue
            ctx = {"scope": "episode", "title": title, "season": sn, "episode": en, "year": body.get("year")}
            first_query = build_query("episode", title, season=sn, episode=en)
            dl_id = db.add_video_download({
                "kind": "show", "title": title, "release_title": _os.path.basename(str(fn)),
                "source": "soulseek", "username": username, "filename": fn,
                "size_bytes": int(f.get("size_bytes") or 0), "quality_label": body.get("quality_label"),
                "target_dir": target, "status": "queued",
                "media_id": (str(body.get("media_id")) if body.get("media_id") is not None else None),
                "media_source": body.get("media_source"), "year": body.get("year"),
                "poster_url": body.get("poster_url"),
                "candidates": _json.dumps([]), "search_ctx": _json.dumps(ctx),
                "tried_queries": _json.dumps([first_query] if first_query else []),
                "tried_files": _json.dumps([fn]), "attempts": 0,
            })
            started += 1
            ids.append(dl_id)
        if started:
            ensure_started(get_video_db)
        return jsonify({"ok": started > 0, "started": started, "skipped": skipped, "ids": ids})

    def _annotate_upgrade_watches(db, rows) -> None:
        """Mark COMPLETED movie/episode rows that still hold a wishlist row —
        the upgrade-until-cutoff watches. Without this, a below-cutoff grab
        looks identical to a final one on the Downloads page. Identity comes
        from the same resolver the monitor uses; two set queries total."""
        try:
            from core.video.download_monitor import _as_int, _wishlist_ids
            completed = [r for r in rows if r.get("status") == "completed"
                         and str(r.get("kind") or "") in ("movie", "show")]
            if not completed:
                return
            conn = db._get_connection()
            try:
                movie_watches = {r[0] for r in conn.execute(
                    "SELECT tmdb_id FROM video_wishlist WHERE kind='movie'")}
                ep_watches = {(r[0], r[1], r[2]) for r in conn.execute(
                    "SELECT tmdb_id, season_number, episode_number "
                    "FROM video_wishlist WHERE kind='episode'")}
            finally:
                conn.close()
            for r in completed:
                kind, tmdb, sn, en, _ctx = _wishlist_ids(db, r)
                if not tmdb:
                    continue
                if kind == "movie":
                    r["upgrade_watch"] = int(tmdb) in movie_watches
                else:
                    r["upgrade_watch"] = (int(tmdb), _as_int(sn), _as_int(en)) in ep_watches
        except Exception:   # noqa: BLE001 — an annotation, never a 500
            logger.debug("upgrade-watch annotation failed", exc_info=True)

    @bp.route("/downloads/active", methods=["GET"])
    def video_downloads_active():
        from . import get_video_db
        from core.video.download_monitor import ensure_started
        db = get_video_db()
        ensure_started(get_video_db)   # also (re)start the monitor when the page is open
        rows = db.list_video_downloads()
        _annotate_upgrade_watches(db, rows)
        return jsonify({"downloads": rows})

    @bp.route("/downloads/status", methods=["GET"])
    def video_downloads_status():
        """Lightweight live-tracking lookup — used by the Download modal's result
        card (by ``id``) and a movie/show detail page (by ``media_id`` +
        ``media_source``, returning that title's most relevant download so the page
        can show live progress). Returns ``{"download": {...}|null}``."""
        from . import get_video_db
        from core.video.download_monitor import ensure_started
        db = get_video_db()
        ensure_started(get_video_db)
        dl_id = request.args.get("id")
        if dl_id:
            try:
                return jsonify({"download": db.get_video_download(int(dl_id))})
            except (TypeError, ValueError):
                return jsonify({"download": None})
        media_id = request.args.get("media_id")
        if media_id:
            media_source = request.args.get("media_source")
            match = [r for r in db.list_video_downloads()
                     if str(r.get("media_id")) == str(media_id)
                     and (not media_source or r.get("media_source") == media_source)]
            # list_video_downloads orders active-first then newest — so an active
            # download (or else the most recent) for this title is simply match[0].
            return jsonify({"download": match[0] if match else None})
        return jsonify({"download": None})

    @bp.route("/downloads/cancel", methods=["POST"])
    def video_downloads_cancel():
        from . import get_video_db
        from core.video.slskd_download import cancel_download
        body = request.get_json(silent=True) or {}
        db = get_video_db()
        dl = db.get_video_download(body.get("id"))
        if not dl:
            return jsonify({"ok": False, "error": "Download not found."}), 404
        if dl["status"] in ("completed", "failed", "cancelled"):
            return jsonify({"ok": True, "already": True})
        cancel_download(dl.get("username"), dl.get("filename"))   # best-effort; mark regardless
        import time
        db.update_video_download(dl["id"], status="cancelled", error="Cancelled",
                                 completed_at=time.strftime("%Y-%m-%d %H:%M:%S"))
        return jsonify({"ok": True})

    @bp.route("/downloads/retry", methods=["POST"])
    def video_downloads_retry():
        """Retry a download. Default: re-grab the SAME release (soulseek only —
        that's the only source where the same-release re-grab is wired).
        ``next: true`` — or any non-soulseek source — retries with a DIFFERENT
        release via the monitor's candidate/requery/wishlist-search machinery
        (blocklist-filtered). The block-and-retry button uses next; the old
        behavior re-grabbed the exact release just blocked and 502'd on
        torrents (Boulder's report)."""
        from . import get_video_db
        from core.video.download_monitor import ensure_started, retry_another_release
        from core.video.slskd_download import start_download
        body = request.get_json(silent=True) or {}
        db = get_video_db()
        dl = db.get_video_download(body.get("id"))
        if not dl:
            return jsonify({"ok": False, "error": "Download not found."}), 404
        want_next = bool(body.get("next")) or \
            str(dl.get("source") or "soulseek").lower() != "soulseek"
        if want_next:
            res = retry_another_release(db, dl)
            ensure_started(get_video_db)
            if res.get("status") in ("downloading", "searching"):
                return jsonify({"ok": True, "mode": "next", "status": res["status"]})
            if res.get("wishlist_search"):
                return jsonify({"ok": True, "mode": "next", "status": "failed",
                                "wishlist_search": True})
            return jsonify({"ok": False,
                            "error": "No other release available — the item is back "
                                     "on the wishlist for the next sweep."}), 502
        if not dl.get("username") or not dl.get("filename"):
            return jsonify({"ok": False, "error": "Nothing to retry from."}), 400
        started = start_download(dl["username"], dl["filename"], dl.get("size_bytes") or 0)
        if not started.get("ok"):
            return jsonify({"ok": False, "error": started.get("error") or "slskd refused the download."}), 502
        db.update_video_download(dl["id"], status="downloading", progress=0, error=None,
                                 dest_path=None, completed_at=None)
        ensure_started(get_video_db)
        return jsonify({"ok": True})

    @bp.route("/downloads/clear", methods=["POST"])
    def video_downloads_clear():
        from . import get_video_db
        return jsonify({"cleared": get_video_db().clear_finished_video_downloads()})

    @bp.route("/downloads/youtube-quality", methods=["GET"])
    def video_youtube_quality():
        # Separate, smaller profile — YouTube is yt-dlp, not scene/p2p releases.
        from . import get_video_db
        from core.video.youtube_quality import load
        return jsonify(load(get_video_db()))

    @bp.route("/downloads/youtube-quality", methods=["POST"])
    def video_youtube_quality_save():
        from . import get_video_db
        from core.video.youtube_quality import save
        body = request.get_json(silent=True) or {}
        return jsonify(save(get_video_db(), body))

    @bp.route("/downloads/slskd", methods=["GET"])
    def video_slskd_config():
        # SHARED with music — same slskd instance. Reads the app-wide config_manager.
        from config.settings import config_manager
        return jsonify({k: config_manager.get(cfg, default)
                        for k, (cfg, default) in _SLSKD_KEYS.items()})

    @bp.route("/downloads/slskd", methods=["POST"])
    def video_slskd_config_save():
        from config.settings import config_manager
        body = request.get_json(silent=True) or {}
        for k, (cfg, _default) in _SLSKD_KEYS.items():
            if k in body:
                config_manager.set(cfg, body.get(k))
        return jsonify({"status": "saved", "shared": True})
