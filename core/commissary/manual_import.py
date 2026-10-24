"""Manual import of any file or folder on the server, not just a failed download.

Upstream's Import page only lists downloads that failed to import, and places
them one file at a time. The old fork (1.x-2.4.3) let you:

* browse the server's download and library folders, filtered as you type,
  and add any video file or season folder to that same queue;
* place a whole folder as one show: one identity choice, every episode in it.
  Members are claimed by upstream's own pack model (season_pack.map_pack:
  samples and extras skipped, the larger file wins a contested episode), so a
  hand-placed pack and an automatic one agree on what a pack contains;
* renumber a folder's season ("Import as season 7" for files named S08) - the
  EPISODE numbers still come from each file. A folder spanning several seasons
  refuses, since one number for two seasons would file S07E01 twice;
* place fansub-numbered folders ('[GHOST] Nana - 01'): the Nth aired episode of
  the chosen show is absolute N (specials excluded), else the typed season,
  else season 1, where the two numbers are the same by definition;
* survive a slow copy: placement runs off the request thread, and the page
  polls its status rather than reading a timeout as a failure.

Everything lives under /api/video/import/, which upstream's gate makes
admin-only for every method - this enumerates folders on the host.
"""

from __future__ import annotations

import json
import os
import threading
import time
from typing import Any, Dict, List, Optional, Tuple

from utils.logging_config import get_logger

logger = get_logger("commissary.manual_import")

BROWSE_LIMIT = 1000
PREVIEW_LIMIT = 60
GRACE_SECONDS = 8.0


# ── browsing ────────────────────────────────────────────────────────────────

def shortcuts(db) -> List[Dict[str, str]]:
    """Where downloads land, then the libraries. Only folders that exist."""
    out, seen = [], set()

    def add(label, path):
        p = str(path or "").strip()
        if not p:
            return
        key = os.path.normcase(os.path.abspath(p))
        if key in seen or not os.path.isdir(p):
            return
        seen.add(key)
        out.append({"label": label, "path": os.path.abspath(p)})

    try:
        from core.settings import config_manager
        from core.commissary.paths import split_paths
        add("Downloads", config_manager.get("soulseek.download_path", ""))
        for name, key in (("Torrents", "soulseek.torrent_download_path"),
                          ("Usenet", "soulseek.usenet_download_path")):
            for i, p in enumerate(split_paths(config_manager.get(key, ""))):
                add(name if i == 0 else "%s %d" % (name, i + 1), p)
    except Exception:  # noqa: BLE001 - a config hiccup must not kill the browser
        logger.exception("browse: download folders unavailable")
    try:
        from core.video.library_roots import library_roots
        for kind, label in (("movie", "Movies"), ("episode", "TV")):
            for p in library_roots(db, kind):
                add("%s: %s" % (label, os.path.basename(str(p).rstrip("/\\")) or p), p)
    except Exception:  # noqa: BLE001
        logger.exception("browse: library folders unavailable")
    return out


def _terms(q) -> List[str]:
    return [t for t in str(q or "").lower().split() if t]


def _matches(name, terms) -> bool:
    low = str(name or "").lower()
    return all(t in low for t in terms)


def browse(db, raw_path: str, q: str = "") -> Tuple[Dict[str, Any], int]:
    """One folder: sub-folders and VIDEO files only (anything else couldn't be
    imported on the next step). ``q`` narrows it BEFORE the cap - a folder too
    big to list is exactly the one worth searching. The whole-folder offer is
    counted from every file, so a filter never changes what it claims."""
    from core.video.importer import is_video
    marks = shortcuts(db)
    raw = str(raw_path or "").strip()
    path = os.path.abspath(raw) if raw else (marks[0]["path"] if marks else "")
    if not path:
        return {"success": True, "path": "", "parent": None, "dirs": [], "files": [],
                "shortcuts": [], "error": "No download or library folder is configured yet."}, 200
    if not os.path.isdir(path):
        return {"success": False, "path": path, "shortcuts": marks,
                "error": "That folder doesn't exist."}, 404
    dirs, files = [], []
    try:
        with os.scandir(path) as it:
            for e in it:
                if e.name.startswith("."):
                    continue
                try:
                    if e.is_dir():
                        dirs.append({"name": e.name, "path": e.path})
                    elif is_video(e.name):
                        files.append({"name": e.name, "path": e.path, "size": e.stat().st_size})
                except OSError:
                    continue
    except PermissionError:
        return {"success": False, "path": path, "shortcuts": marks,
                "error": "No permission to read that folder."}, 403
    except OSError:
        logger.exception("browse failed for %s", path)
        return {"success": False, "path": path, "shortcuts": marks,
                "error": "Couldn't read that folder."}, 500
    dirs.sort(key=lambda d: d["name"].lower())
    files.sort(key=lambda f: f["name"].lower())
    # the offer counts by the same claim model the import uses (samples and
    # extras out, a span counts each episode) - over the files listed here, not
    # recursively, so a huge downloads root stays cheap to open
    claim = _claim([{"path": f["path"], "size_bytes": f["size"]} for f in files], path)
    count = len(claim)
    seasons = {m["season"] for m in claim if m["season"] is not None}
    total = len(dirs) + len(files)
    terms = _terms(q)
    if terms:
        dirs = [d for d in dirs if _matches(d["name"], terms)]
        files = [f for f in files if _matches(f["name"], terms)]
    parent = os.path.dirname(path.rstrip("/\\")) or None
    return {"success": True, "path": path,
            "parent": parent if parent and parent != path else None,
            "dirs": dirs[:BROWSE_LIMIT], "files": files[:max(0, BROWSE_LIMIT - len(dirs))],
            "shortcuts": marks, "truncated": len(dirs) + len(files) > BROWSE_LIMIT,
            "q": " ".join(terms), "total": total, "matched": len(dirs) + len(files),
            # one numbered episode is just a file; a folder is offered from two
            "pack": {"count": count, "seasons": sorted(seasons)} if count >= 2 else None}, 200


# ── a folder's episodes ─────────────────────────────────────────────────────

def _walk(root: str) -> List[Dict[str, Any]]:
    out = []
    for base, _dirs, names in os.walk(root):
        for n in names:
            p = os.path.join(base, n)
            try:
                size = os.path.getsize(p)
            except OSError:
                size = None
            out.append({"path": p, "size_bytes": size})
    return out


def _claim(files: List[Dict[str, Any]], root: str) -> List[Dict[str, Any]]:
    """One entry per EPISODE claimed from these files (a span claims each)."""
    from core.commissary.release_titles import fansub_absolute_episode
    from core.video.season_pack import map_pack
    out = map_pack(files, root=root)
    got = [{"season": s, "episode": e} for (s, e) in out["claimed"]]
    for sk in out["skipped"]:
        if "no episode number" in sk.get("why", "") and fansub_absolute_episode(
                os.path.splitext(os.path.basename(sk["path"]))[0]):
            got.append({"season": None, "episode": None})
    return got


def members(path: str) -> List[Dict[str, Any]]:
    """The episodes in a folder: upstream's claim model for SxxExx names (a
    multi-episode file claims its span), plus fansub absolute numbering, which
    that model can't read without a show's episode list. Absolute members carry
    season None until a show is chosen."""
    from core.commissary.release_titles import fansub_absolute_episode
    from core.video.season_pack import map_pack
    out = map_pack(_walk(path), root=path)
    by_path: Dict[str, Dict[str, Any]] = {}
    for (season, episode), info in sorted(out["claimed"].items()):
        m = by_path.get(info["path"])
        if m is None:
            by_path[info["path"]] = {"path": info["path"], "size": info.get("size_bytes") or 0,
                                     "season": season, "episode": episode, "episode_end": None,
                                     "absolute": None}
        elif m["season"] == season:
            m["episode_end"] = max(m["episode_end"] or m["episode"], episode)
    for s in out["skipped"]:
        if "no episode number" not in s.get("why", ""):
            continue
        n = fansub_absolute_episode(os.path.splitext(os.path.basename(s["path"]))[0])
        if n:
            try:
                size = os.path.getsize(s["path"])
            except OSError:
                size = 0
            by_path[s["path"]] = {"path": s["path"], "size": size, "season": None,
                                  "episode": n, "episode_end": None, "absolute": n}
    return sorted(by_path.values(), key=lambda m: (m["season"] is None, m["season"] or 0, m["episode"]))


def absolute_lookup(db, show_tmdb_id) -> Dict[int, Tuple[int, int]]:
    """{absolute: (season, episode)} from the chosen show's library episode
    list: the Nth aired episode, specials excluded, IS absolute N."""
    if not show_tmdb_id:
        return {}
    try:
        conn = db._get_connection()
        try:
            rows = conn.execute(
                "SELECT e.season_number, e.episode_number FROM episodes e "
                "JOIN shows s ON s.id = e.show_id WHERE s.tmdb_id=? AND e.season_number > 0 "
                "ORDER BY e.season_number, e.episode_number", (int(show_tmdb_id),)).fetchall()
        finally:
            conn.close()
    except Exception:  # noqa: BLE001
        logger.exception("absolute episode lookup failed")
        return {}
    out: Dict[int, Tuple[int, int]] = {}
    seen = set()
    for s, e in rows:
        if (s, e) in seen:
            continue
        seen.add((s, e))
        out[len(out) + 1] = (int(s), int(e))
    return out


def resolve(found: List[Dict[str, Any]], season: Any = None,
            absolute: Optional[Dict[int, Tuple[int, int]]] = None) -> Tuple[List[Dict[str, Any]], Optional[str]]:
    """Each member's final (season, episode), or an error.

    A typed season RENUMBERS a single-season folder; each file keeps its own
    episode number. For absolute members it only seeds the fallback - the
    show's episode list places them first, or season 2's files would be
    refiled on top of season 1."""
    try:
        typed = int(season) if season not in (None, "") else None
    except (TypeError, ValueError):
        return [], "The season must be a number."
    parsed_seasons = sorted({m["season"] for m in found if m["season"] is not None})
    if typed is not None and len(parsed_seasons) > 1:
        return [], ("This folder holds seasons %s - one season number can't describe it. "
                    "Leave the season blank to keep each file's own."
                    % ", ".join(str(s) for s in parsed_seasons))
    out = []
    for m in found:
        if m["season"] is not None:
            target = typed if typed is not None else m["season"]
            out.append({**m, "to_season": target, "to_episode": m["episode"],
                        "renumbered_from": m["season"] if target != m["season"] else None})
        else:
            hit = (absolute or {}).get(m["absolute"])
            s, e = hit if hit else ((typed if typed is not None else 1), m["absolute"])
            out.append({**m, "to_season": s, "to_episode": e, "renumbered_from": None})
    seen: Dict[Tuple[int, int], str] = {}
    for p in out:
        key = (p["to_season"], p["to_episode"])
        if key in seen:
            return [], ("Two files would become S%02dE%02d (%s and %s)."
                        % (key[0], key[1], os.path.basename(seen[key]), os.path.basename(p["path"])))
        seen[key] = p["path"]
    return out, None


def preview(db, path: str, season: Any = None, show_tmdb_id: Any = None) -> Dict[str, Any]:
    found = members(path)
    plan, err = resolve(found, season, absolute_lookup(db, show_tmdb_id))
    rows = plan if not err else [{**m, "to_season": m["season"], "to_episode": m["episode"]}
                                 for m in found]
    return {"success": True, "count": len(found), "error": err,
            "seasons": sorted({m["season"] for m in found if m["season"] is not None}),
            "absolute": sum(1 for m in found if m["season"] is None),
            "items": [{"name": os.path.basename(m["path"]), "season": m["season"],
                       "episode": m["episode"], "episode_end": m.get("episode_end"),
                       "absolute": m.get("absolute"), "to_season": m.get("to_season"),
                       "to_episode": m.get("to_episode")} for m in rows[:PREVIEW_LIMIT]],
            "truncated": len(found) > PREVIEW_LIMIT}


# ── adding to the queue ─────────────────────────────────────────────────────

def _guess(name: str) -> Dict[str, Any]:
    from core.commissary.release_titles import fansub_absolute_episode
    from core.video.release_parse import parse_release
    stem = os.path.splitext(name)[0]
    p = parse_release(stem)
    if p.get("episode") is not None or p.get("season") is not None:
        return {"scope": "episode", "season": p.get("season"), "episode": p.get("episode")}
    n = fansub_absolute_episode(stem)
    if n is not None:
        return {"scope": "episode", "season": None, "episode": n}
    return {"scope": "movie", "season": None, "episode": None}


def add(db, raw_path: str) -> Tuple[Dict[str, Any], int]:
    """Queue a file or a season folder as an unplaced import - the same row
    shape a failed download has, so upstream's queue, Place and Dismiss work on
    it. A folder is ONE row: which show is this? is asked once."""
    from core.video.importer import is_video
    path = str(raw_path or "").strip()
    if not path:
        return {"success": False, "error": "Enter a file or folder path."}, 400
    is_dir = os.path.isdir(path)
    if not is_dir and not os.path.isfile(path):
        return {"success": False, "error": "Nothing at that path."}, 404
    if not is_dir and not is_video(path):
        return {"success": False, "error": "Not a recognized video file."}, 400
    found = members(path) if is_dir else []
    if is_dir and not found:
        return {"success": False, "error": "No numbered episode files in that folder."}, 400
    for row in db.get_import_failed_video_downloads():
        if row.get("dest_path") == path:
            return {"success": True, "id": row.get("id"), "already": True}, 200
    name = os.path.basename(path.rstrip("/\\")) or path
    if is_dir:
        seasons = sorted({m["season"] for m in found if m["season"] is not None})
        guess = {"scope": "season", "season": seasons[0] if len(seasons) == 1 else None,
                 "episode": None}
        size = sum(m.get("size") or 0 for m in found) or None
    else:
        guess = _guess(name)
        try:
            size = os.path.getsize(path)
        except OSError:
            size = None
    dl_id = db.add_video_download({
        "kind": "show" if is_dir or guess["scope"] == "episode" else "movie",
        "title": name, "release_title": name, "source": "manual", "filename": name,
        "size_bytes": size, "status": "downloading", "candidates": "[]",
        "search_ctx": json.dumps(guess), "tried_queries": "[]", "tried_files": "[]", "attempts": 0})
    db.update_video_download(dl_id, status="import_failed",
                             error="Added for manual placement", dest_path=path)
    return {"success": True, "id": dl_id, "scope": guess["scope"], "episodes": len(found)}, 200


# ── placing a folder, off the request thread ───────────────────────────────

_jobs: Dict[int, Dict[str, Any]] = {}
_jobs_lock = threading.Lock()


def job_state(dl_id) -> Optional[Dict[str, Any]]:
    with _jobs_lock:
        j = _jobs.get(int(dl_id))
        return dict(j) if j else None


def is_running(dl_id) -> bool:
    j = job_state(dl_id)
    return bool(j and j.get("running"))


def start(dl_id, work, grace: float = GRACE_SECONDS) -> Dict[str, Any]:
    """Run ``work()`` on a thread and wait briefly: a quick placement answers
    in this request; a slow copy switches the page to polling instead of a
    timeout that read as failure while the copy carried on."""
    key = int(dl_id)
    with _jobs_lock:
        if _jobs.get(key, {}).get("running"):
            return dict(_jobs[key])
        _jobs[key] = {"running": True, "result": None, "error": None, "started": time.time()}

    def run():
        try:
            res = work()
            with _jobs_lock:
                _jobs[key].update(running=False, result=res)
        except Exception as ex:  # noqa: BLE001 - reported to the page
            logger.exception("manual folder placement %s failed", key)
            with _jobs_lock:
                _jobs[key].update(running=False, error=str(ex))

    t = threading.Thread(target=run, name="commissary-place-%d" % key, daemon=True)
    t.start()
    t.join(grace)
    return job_state(key) or {}


def place_folder(db, row: Dict[str, Any], body: Dict[str, Any]) -> Dict[str, Any]:
    """Every episode in the folder, as the one show the user chose."""
    from core.commissary.naming_identity import naming_identity
    from core.commissary.video_libraries import manual_target
    from core.video import organization
    from core.video.download_monitor import _reclaim_source
    from core.video.download_pipeline import target_dir_for
    from core.video.importer import plan_import, real_fs, run_import
    from core.video.mediainfo import probe
    from core.video.recycle import discarder

    src = row.get("dest_path")
    media_id = body.get("media_id")
    found = members(src)
    if not found:
        return {"success": False, "error": "No numbered episode files in that folder any more."}
    plan, err = resolve(found, body.get("season"), absolute_lookup(db, media_id))
    if err:
        return {"success": False, "error": err}
    from api.video.downloads import _PATH_KEYS, _series_type_for
    paths = {k: db.get_setting(k) or "" for k in _PATH_KEYS}
    # the Library first; without Libraries, upstream's anime folder for an anime show
    target = manual_target(db, "show", body) or target_dir_for(
        "show", paths, _series_type_for(db, "show", {"media_id": media_id,
                                                     "media_source": body.get("media_source") or "tmdb"}))
    settings = organization.load(db)
    prober = probe if settings.get("verify_with_ffprobe", True) else None
    identity = naming_identity(db, "show", media_id)
    fs = real_fs()
    recycle = discarder(db, settings)
    imported, failed, dests = 0, [], []
    for p in plan:
        name = os.path.basename(p["path"])
        ctx = {"scope": "episode", "season": p["to_season"], "episode": p["to_episode"]}
        if p.get("renumbered_from") is not None:
            ctx["renumbered_from"] = p["renumbered_from"]
        ep_dl = {**row, "kind": "show", "release_title": name, "filename": name,
                 "size_bytes": p.get("size"), "search_ctx": json.dumps(ctx)}
        override = {"scope": "episode", "title": body.get("title"), "year": body.get("year"),
                    "season": p["to_season"], "episode": p["to_episode"],
                    "media_id": media_id, "target_dir": target}
        try:
            info = prober(p["path"]) if prober is not None else None
        except Exception:  # noqa: BLE001
            info = None
        try:
            pl = plan_import(ep_dl, p["path"], list_dir=fs.list_dir, probe=info, settings=settings,
                             force=True, override=override, identity=identity)
            patch = run_import(ep_dl, p["path"], fs=fs, settings=settings, force=True,
                               override=override, recycle=recycle, plan=pl)
        except Exception as ex:  # noqa: BLE001 - one file must not abandon the rest
            logger.exception("manual folder: %s raised", name)
            patch = {"status": "import_failed", "error": str(ex)}
        if patch.get("status") == "completed":
            imported += 1
            dests.append(patch.get("dest_path"))
            _reclaim_source(patch.get("_cleanup_source"))
        else:
            failed.append({"name": name, "error": patch.get("error") or "not placed"})
    status = "completed" if imported else "import_failed"
    note = None
    if failed:
        note = "%d of %d placed; %s" % (imported, len(plan), "; ".join(
            "%s: %s" % (f["name"], f["error"]) for f in failed[:3]))
    db.update_video_download(row["id"], status=status, error=note,
                             dest_path=(os.path.dirname(dests[0]) if dests else src))
    if imported:
        if media_id is not None:
            try:
                db.update_video_download(row["id"], media_source="tmdb", media_id=media_id)
            except Exception:  # noqa: BLE001
                logger.debug("manual folder: identity not recorded", exc_info=True)
        try:
            from core.video.download_events import notify_batch_complete
            notify_batch_complete({"completed": imported, "manual": True})
        except Exception:  # noqa: BLE001
            logger.exception("manual folder: batch-complete notify failed")
    return {"success": bool(imported), "status": status, "imported": imported,
            "total": len(plan), "failed": failed,
            "error": None if imported else (note or "Nothing was placed.")}
