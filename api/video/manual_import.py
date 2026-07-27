"""Manual / failed-import resolution API (isolated).

When a finished download can't be auto-placed it's parked as ``import_failed`` with
the file left on disk (``dest_path`` points at it). The Import page surfaces these and
lets the user place them by hand:

  GET  /api/video/import/failed          → the queue of unplaced downloads
  POST /api/video/import/add             → queue an arbitrary on-disk file for placement
  POST /api/video/import/<id>/place      → force-import to the user's chosen identity
  POST /api/video/import/<id>/dismiss    → drop the row (optionally delete the file)

``add`` exists so manual placement isn't gated on SoulSync having failed a download
first — a file that was never grabbed through SoulSync at all (moved in by hand, left
over from another tool) gets the exact same ``import_failed`` row shape and rides the
same queue/place/dismiss UI, no separate code path.

The identity picker on the page reuses the existing /api/video/search (TMDB, with a
``library_id`` annotation for owned titles) — no new search endpoint needed here.
Reads only the video engine + video.db; nothing from the music side.
"""

from __future__ import annotations

import json
import os

from flask import jsonify, request

from utils.logging_config import get_logger

logger = get_logger("video_api.manual_import")

_KIND_FOR_SCOPE = {"movie": "movie", "episode": "show"}


def _guess_scope(name: str) -> dict:
    """Movie or episode, guessed from a bare filename — {scope, season, episode}.

    A manually added file used to be filed as a movie unconditionally, so the
    Place dialog opened on the Movie tab and an episode landed in a movie
    Library. Two shapes count as an episode: the SxxExx/Season-N forms
    ``parse_release`` already understands, and the fansub absolute-numbering
    convention (``[SubsPlease] Show - 40 [1080p]``), which carries no season
    at all — those get ``season: None`` so the user still fills it in, but at
    least open on the right tab.
    """
    from core.video.release_parse import fansub_absolute_episode, parse_release
    stem = os.path.splitext(str(name or ""))[0]
    p = parse_release(stem)
    if p.get("episode") is not None or p.get("season") is not None:
        return {"scope": "episode", "season": p.get("season"), "episode": p.get("episode")}
    absolute = fansub_absolute_episode(stem)
    if absolute is not None:
        return {"scope": "episode", "season": None, "episode": absolute}
    return {"scope": "movie", "season": None, "episode": None}


def _ctx(row):
    try:
        c = json.loads(row.get("search_ctx") or "{}")
        return c if isinstance(c, dict) else {}
    except (ValueError, TypeError):
        return {}


def _failed_view(row):
    """The render-ready shape for one unplaced download — everything the card's
    expand drawer shows (grab provenance, on-disk facts, identity context)."""
    c = _ctx(row)
    path = row.get("dest_path")
    file_size, file_exists = None, False
    if path:
        try:
            file_size = os.path.getsize(path)
            file_exists = True
        except OSError:
            pass
    return {
        "id": row.get("id"),
        "title": row.get("title"),
        "kind": row.get("kind"),
        "year": row.get("year"),
        "reason": row.get("error"),
        "file": path,                         # where the file is sitting, unplaced
        "file_exists": file_exists,
        "file_size": file_size,               # actual bytes on disk (None if gone)
        "release_title": row.get("release_title"),
        "poster_url": row.get("poster_url"),
        "quality_label": row.get("quality_label"),
        "size_bytes": row.get("size_bytes"),  # the release's advertised size
        "source": row.get("source"),
        "username": row.get("username"),
        "attempts": row.get("attempts"),
        "grabbed_at": row.get("created_at"),
        "media_id": row.get("media_id"),
        "media_source": row.get("media_source"),
        "scope": c.get("scope"),
        "season": c.get("season"),
        "episode": c.get("episode"),
    }


def _browse_shortcuts(db) -> list:
    """Sensible starting points for the folder browser, in the order a user is
    likely to want them: where downloads land first, then the libraries. Only
    folders that actually exist are offered — a shortcut to a path that isn't
    mounted is worse than no shortcut. Deduped, first label wins."""
    from config.settings import config_manager

    out, seen = [], set()

    def _add(label, path):
        p = str(path or "").strip()
        if not p:
            return
        key = os.path.normcase(os.path.abspath(p))
        if key in seen or not os.path.isdir(p):
            return
        seen.add(key)
        out.append({"label": label, "path": os.path.abspath(p)})

    _add("Downloads", config_manager.get("soulseek.download_path", ""))
    for i, p in enumerate(config_manager.get("soulseek.torrent_download_path", []) or []):
        _add("Torrents" if i == 0 else "Torrents %d" % (i + 1), p)
    for i, p in enumerate(config_manager.get("soulseek.usenet_download_path", []) or []):
        _add("Usenet" if i == 0 else "Usenet %d" % (i + 1), p)
    try:
        for row in db.all_library_rows():
            _add(str(row.get("label") or row.get("server_title") or "Library"), row.get("path"))
    except Exception:   # noqa: BLE001 - a registry hiccup must not kill the browser
        logger.exception("browse: could not read library rows for shortcuts")
    return out


# A big folder shouldn't ship 50k rows to the browser; the user navigates or types.
_BROWSE_LIMIT = 1000


def register_routes(bp):
    @bp.route("/import/browse", methods=["GET"])
    def video_import_browse():
        """Walk the server's filesystem to pick a file, instead of typing its full
        path. ``?path=`` lists that folder; with none, opens on the first existing
        shortcut (normally the download folder) so the common case needs no typing.

        Admin-only — it inherits the blueprint's ``/api/video/import`` prefix gate,
        which is admin for every method. That matters: this enumerates directories
        on the host.

        Returns folders and VIDEO files only (the same ``is_video`` test the import
        itself uses), so nothing offered here can be rejected on the next step."""
        from . import get_video_db
        from core.video.importer import is_video

        db = get_video_db()
        shortcuts = _browse_shortcuts(db)
        raw = str(request.args.get("path") or "").strip()
        path = os.path.abspath(raw) if raw else (shortcuts[0]["path"] if shortcuts else "")
        if not path:
            return jsonify({"success": True, "path": "", "parent": None,
                            "dirs": [], "files": [], "shortcuts": [],
                            "error": "No download or library folder is configured yet."})
        if not os.path.isdir(path):
            return jsonify({"success": False, "path": path, "shortcuts": shortcuts,
                            "error": "That folder doesn't exist."}), 404

        dirs, files = [], []
        try:
            with os.scandir(path) as it:
                for e in it:
                    if e.name.startswith("."):      # .git, .DS_Store, thumbnail caches…
                        continue
                    try:
                        if e.is_dir():
                            dirs.append({"name": e.name, "path": e.path})
                        elif is_video(e.name):
                            files.append({"name": e.name, "path": e.path,
                                          "size": e.stat().st_size})
                    except OSError:
                        continue                    # a single unreadable entry is skipped
        except PermissionError:
            return jsonify({"success": False, "path": path, "shortcuts": shortcuts,
                            "error": "No permission to read that folder."}), 403
        except OSError:
            logger.exception("browse failed for %s", path)
            return jsonify({"success": False, "path": path, "shortcuts": shortcuts,
                            "error": "Couldn't read that folder."}), 500

        dirs.sort(key=lambda d: d["name"].lower())
        files.sort(key=lambda f: f["name"].lower())
        truncated = len(dirs) + len(files) > _BROWSE_LIMIT
        parent = os.path.dirname(path.rstrip(os.sep)) or None
        return jsonify({"success": True, "path": path,
                        "parent": parent if parent and parent != path else None,
                        "dirs": dirs[:_BROWSE_LIMIT],
                        "files": files[:max(0, _BROWSE_LIMIT - len(dirs))],
                        "shortcuts": shortcuts, "truncated": truncated})

    @bp.route("/import/failed", methods=["GET"])
    def video_import_failed():
        from . import get_video_db
        rows = get_video_db().get_import_failed_video_downloads()
        return jsonify({"success": True, "items": [_failed_view(r) for r in rows]})

    @bp.route("/import/add", methods=["POST"])
    def video_import_add():
        """Queue an arbitrary on-disk video file for manual placement, with no
        prior download/grab involved. Body: {path}. Idempotent — re-adding a path
        already queued returns the existing row instead of duplicating it."""
        from . import get_video_db
        from core.video.importer import is_video

        body = request.get_json(silent=True) or {}
        path = str(body.get("path") or "").strip()
        if not path:
            return jsonify({"success": False, "error": "Enter a file path."}), 400
        if not os.path.isfile(path):
            return jsonify({"success": False, "error": "No file at that path."}), 404
        if not is_video(path):
            return jsonify({"success": False, "error": "Not a recognized video file."}), 400

        db = get_video_db()
        for row in db.get_import_failed_video_downloads():
            if row.get("dest_path") == path:
                return jsonify({"success": True, "id": row.get("id"), "already": True})

        try:
            size_bytes = os.path.getsize(path)
        except OSError:
            size_bytes = None
        name = os.path.basename(path)
        guess = _guess_scope(name)
        dl_id = db.add_video_download({
            "kind": _KIND_FOR_SCOPE[guess["scope"]], "title": name, "release_title": name,
            "source": "manual",
            "filename": name, "size_bytes": size_bytes, "status": "downloading",
            "candidates": "[]", "search_ctx": json.dumps(guess), "tried_queries": "[]",
            "tried_files": "[]", "attempts": 0,
        })
        db.update_video_download(dl_id, status="import_failed",
                                  error="Added for manual placement", dest_path=path)
        return jsonify({"success": True, "id": dl_id})

    @bp.route("/import/<int:dl_id>/place", methods=["POST"])
    def video_import_place(dl_id):
        """Force-import an unplaced file to the user's chosen identity. Body:
        {scope, title, year, season, episode, episode_title, media_id}."""
        from . import get_video_db
        from api.video.downloads import _resolve_target
        from core.video import organization
        from core.video.importer import real_fs, run_import
        from core.video.mediainfo import probe

        db = get_video_db()
        row = db.get_video_download(dl_id)
        if not row or row.get("status") != "import_failed":
            return jsonify({"success": False, "error": "Not an unplaced import."}), 404
        src = row.get("dest_path")
        if not src or not os.path.exists(src):
            return jsonify({"success": False, "error": "The file is no longer on disk."}), 410

        body = request.get_json(silent=True) or {}
        scope = str(body.get("scope") or "").lower()
        if scope not in ("movie", "episode"):
            return jsonify({"success": False, "error": "Choose a movie or an episode."}), 400

        # The Place dialog sends the chosen Library as root_folder_id. With none —
        # the picker hides itself when there's only one Library for the kind — fall
        # back to the Library the chosen TITLE is already filed under before the
        # primary default, so placing a new episode of an existing Anime show lands
        # beside the rest of that show instead of in the standard TV root.
        _kind = _KIND_FOR_SCOPE[scope]
        _rfid = body.get("root_folder_id") or db.root_folder_id_for_tmdb(_kind, body.get("media_id"))
        override = {
            "scope": scope,
            "title": body.get("title"),
            "year": body.get("year"),
            "season": body.get("season"),
            "episode": body.get("episode"),
            "episode_title": body.get("episode_title"),
            "media_id": body.get("media_id"),
            "target_dir": _resolve_target(db, _kind, _rfid),
        }
        settings = organization.load(db)
        prober = probe if settings.get("verify_with_ffprobe", True) else None
        from core.video.recycle import discarder
        patch = run_import(row, src, fs=real_fs(), prober=prober, settings=settings,
                           force=True, override=override, recycle=discarder(db, settings))
        try:
            db.update_video_download(dl_id, **patch)
        except Exception:
            logger.exception("manual place: failed to persist import %s", dl_id)
            return jsonify({"success": False, "error": "Couldn't save the result."}), 500
        ok = patch.get("status") == "completed"
        if ok:
            # Write NFO + artwork sidecars for the chosen identity (best-effort), then
            # refresh the server + DB the same way the auto-download path does
            # (batch-complete → scan chain), so the manually-placed title shows up
            # without waiting for a scheduled scan.
            sidecar_dl = {"kind": _KIND_FOR_SCOPE[scope], "media_source": "tmdb",
                          "media_id": override.get("media_id"),
                          "poster_url": row.get("poster_url"),
                          "search_ctx": json.dumps({"scope": scope, "season": override.get("season"),
                                                    "episode": override.get("episode")})}
            if settings.get("save_artwork") or settings.get("write_nfo"):
                try:
                    from core.video.download_monitor import write_sidecars
                    from core.video.importer import real_fs
                    write_sidecars(db, sidecar_dl, patch["dest_path"], settings, real_fs())
                except Exception:
                    logger.exception("manual place: sidecar write failed for %s", dl_id)
            if settings.get("download_subtitles"):
                try:
                    from core.video.download_monitor import write_subtitles_for
                    from core.video.importer import real_fs
                    write_subtitles_for(db, sidecar_dl, patch["dest_path"], settings, real_fs())
                except Exception:
                    logger.exception("manual place: subtitle fetch failed for %s", dl_id)
            try:
                from core.video.download_events import notify_batch_complete, publish
                publish("video_download_completed", {
                    "kind": _KIND_FOR_SCOPE[scope], "title": body.get("title") or row.get("title") or "",
                    "year": body.get("year") or "", "season": body.get("season") or "",
                    "episode": body.get("episode") or "", "channel": "",
                    "quality": patch.get("quality_label") or "", "source": row.get("source") or "",
                    "dest_path": patch.get("dest_path") or ""})
                notify_batch_complete({"completed": 1, "manual": True})
            except Exception:
                logger.exception("manual place: batch-complete notify failed for %s", dl_id)
        return jsonify({"success": ok, "status": patch.get("status"),
                        "dest_path": patch.get("dest_path"), "error": patch.get("error")})

    @bp.route("/import/<int:dl_id>/dismiss", methods=["POST"])
    def video_import_dismiss(dl_id):
        """Drop a failed-import row. Body {delete_file: bool} optionally removes the
        unplaced file from disk too."""
        from . import get_video_db
        db = get_video_db()
        row = db.get_video_download(dl_id)
        if not row or row.get("status") != "import_failed":
            return jsonify({"success": False, "error": "Not an unplaced import."}), 404
        body = request.get_json(silent=True) or {}
        if body.get("delete_file"):
            src = row.get("dest_path")
            if src and os.path.exists(src):
                from core.video import organization, recycle
                res = recycle.discard(src, organization.load(db), db, reason="dismissed import")
                if not res.get("ok"):
                    logger.warning("dismiss: could not delete %s", src)
        db.update_video_download(dl_id, status="cancelled",
                                 error="Dismissed from manual import")
        return jsonify({"success": True})
