"""After a torrent or usenet download fails: don't grab it again, grab the next one now.

Upstream (rightly) never retries a client-managed download through Soulseek -
the old fork's 2.2.1 found that path asking the wrong network. Instead it fails
the row and puts the title back on the wishlist for the next hourly search.
But a stalled or dead torrent is not blocklisted (only a proven-bad FILE is),
so the next search can rank and grab the very same release again, and again.

* a failed client-managed release is remembered against its title for a week,
  and every search (the hourly drain, RSS, manual and "all sources") marks it
  rejected with that reason - not dropped, so a search's own audit still says
  why. A week, not forever: a swarm that was dead tonight may not be next month,
  which is why this isn't the permanent blocklist;
* the title is searched again straight away, the way the hourly drain would
  search it (source chain, the title's Library trackers, upgrade cutoff - not
  as a user click, which would allow an equal-quality replacement), at most
  MAX_RETRIES times a day, after which the hourly search carries on.
"""

from __future__ import annotations

import sqlite3
from typing import Any, Dict, List, Optional, Tuple

from utils.logging_config import get_logger

logger = get_logger("commissary.client_retry")

CLIENT_SOURCES = ("torrent", "usenet", "extto")
REMEMBER_DAYS = 7
MAX_RETRIES = 3
REJECT = "This release failed to download recently - trying others first"

TABLE_SQL = (
    "CREATE TABLE IF NOT EXISTS commissary_failed_releases ("
    " id INTEGER PRIMARY KEY,"
    " kind TEXT NOT NULL,"
    " tmdb_id INTEGER NOT NULL,"
    " season INTEGER,"
    " episode INTEGER,"
    " release TEXT NOT NULL,"
    " reason TEXT,"
    " failed_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP)")


def ensure_table(conn) -> None:
    conn.execute(TABLE_SQL)
    conn.execute("CREATE INDEX IF NOT EXISTS idx_commissary_failed_releases "
                 "ON commissary_failed_releases(kind, tmdb_id, season, episode)")


def _key(name: Any) -> str:
    return " ".join(str(name or "").lower().split())


def _norm(v) -> Optional[int]:
    try:
        return int(v) if v is not None and v != "" else None
    except (TypeError, ValueError):
        return None


def _identity(db, dl: Dict[str, Any]) -> Optional[Tuple[str, int, Optional[int], Optional[int]]]:
    from core.video.download_monitor import _wishlist_ids
    kind, tmdb_id, season, episode, _ctx = _wishlist_ids(db, dl)
    if kind not in ("movie", "episode") or not tmdb_id:
        return None
    return kind, int(tmdb_id), _norm(season), _norm(episode)


def _q(db, sql, params=()) -> list:
    try:
        conn = db._get_connection()
    except Exception:  # noqa: BLE001
        return []
    try:
        return conn.execute(sql, params).fetchall()
    except sqlite3.Error:
        return []           # table not on this handle (a test's bare DB): nothing remembered
    finally:
        conn.close()


def _where(kind, tmdb_id, season, episode):
    return ("kind=? AND tmdb_id=? AND season IS ? AND episode IS ?",
            (kind, int(tmdb_id), _norm(season), _norm(episode)))


def recent(db, kind, tmdb_id, season=None, episode=None, days: float = REMEMBER_DAYS) -> set:
    w, p = _where(kind, tmdb_id, season, episode)
    return {r[0] for r in _q(db, "SELECT release FROM commissary_failed_releases WHERE " + w +
                             " AND julianday('now') - julianday(failed_at) < ?", p + (float(days),))}


def record(db, dl: Dict[str, Any], reason: Any = None) -> Optional[tuple]:
    ident = _identity(db, dl)
    release = _key(dl.get("release_title") or dl.get("filename"))
    if not ident or not release:
        return None
    conn = db._get_connection()
    try:
        ensure_table(conn)
        conn.execute("INSERT INTO commissary_failed_releases (kind, tmdb_id, season, episode, release, "
                     "reason) VALUES (?, ?, ?, ?, ?, ?)", ident + (release, str(reason or "")[:300]))
        conn.commit()
    except sqlite3.Error:
        logger.exception("remembering a failed release failed")
        return None
    finally:
        conn.close()
    return ident


def mark_failed(item: Dict[str, Any], media_type: str, cands: List[Dict[str, Any]]):
    """A search's candidates, with this title's recently failed releases
    rejected. Called at every search's return; never raises."""
    if not cands:
        return cands
    try:
        from api.video import get_video_db
        if media_type == "movie":
            failed = recent(get_video_db(), "movie", item.get("tmdb_id"))
        else:
            failed = recent(get_video_db(), "episode", item.get("show_tmdb_id"),
                            item.get("season_number"), item.get("episode_number"))
    except Exception:  # noqa: BLE001
        return cands
    if not failed:
        return cands
    for c in cands:
        if c.get("accepted") and (_key(c.get("title")) in failed or _key(c.get("filename")) in failed):
            c["accepted"] = False
            c["rejected"] = REJECT
    return cands


def after_failure(db, dl: Dict[str, Any]) -> bool:
    """Never raises: called from upstream's failure path, which must finish
    failing the row whatever happens here."""
    try:
        return _after_failure(db, dl)
    except Exception:  # noqa: BLE001
        logger.exception("[Retry] after-failure handling failed for %s", (dl or {}).get("id"))
        return False


def _after_failure(db, dl: Dict[str, Any]) -> bool:
    """A client-managed download has just failed for good (upstream already
    put the title back on the wishlist). Remember the release; search again
    now unless this title has already used its retries today. True if kicked."""
    if str(dl.get("source") or "").lower() not in CLIENT_SOURCES:
        return False
    ident = record(db, dl, dl.get("error"))
    if not ident:
        return False
    kind, tmdb_id, season, episode = ident
    w, p = _where(kind, tmdb_id, season, episode)
    today = _q(db, "SELECT COUNT(*) FROM commissary_failed_releases WHERE " + w +
               " AND julianday('now') - julianday(failed_at) < 1", p)
    if today and today[0][0] > MAX_RETRIES:
        logger.info("[Retry] %s: %d releases failed today - leaving it to the hourly search",
                    dl.get("title") or tmdb_id, today[0][0])
        return False
    try:
        queued = search_again(db, kind, tmdb_id, season, episode)
        logger.info("[Retry] %s failed on %s; searching for another release now (%d queued)",
                    dl.get("release_title") or dl.get("title"), dl.get("source"), queued)
        return queued > 0
    except Exception:  # noqa: BLE001 - the hourly search still has it
        logger.exception("[Retry] immediate re-search failed for %s", dl.get("title"))
        return False


def search_again(db, kind, tmdb_id, season=None, episode=None, *, background: bool = True) -> int:
    """The wishlist search for one title, as the BACKGROUND drain would run it.

    Upstream's manual_search marks its items user-initiated, which skips the
    "already at your cutoff" check and allows an equal-quality replacement -
    right for a click, wrong for an automatic retry. So: upstream's own
    _prepare (upgrade judging, in-flight de-dupe), _default_search, pick_best
    and _default_enqueue, without that flag."""
    import threading

    from core.automation.handlers import video_process_wishlist as vpw
    from core.video import wishlist_search as ws
    media_type = "movie" if kind == "movie" else "episode"
    scope = "movie" if kind == "movie" else ("episode" if episode is not None else "season")
    try:
        profiles = db.wishlist_profiles_for_media(kind, int(tmdb_id), season_number=season,
                                                  episode_number=episode) or [1]
    except Exception:  # noqa: BLE001
        profiles = [1]
    items = db.wishlist_manual_search_items(scope, tmdb_id, season_number=season,
                                            episode_number=episode, profile_id=profiles[0])
    if media_type == "episode" and items:
        from core.video.numbering import annotate
        items = annotate(items)
    todo = ws._prepare(items, media_type, user_initiated=False) if items else []
    target = vpw._default_target_dir(media_type)
    if not todo or not target:
        if todo:
            ws._finish(todo, media_type)
        return 0

    def run():
        try:
            for it in todo:
                found = vpw._default_search(it, media_type)
                cands = found[0] if isinstance(found, tuple) else found
                best = vpw.pick_best(cands or [], int(it.get("_min_rank") or 0))
                if best:
                    vpw._default_enqueue(it, best, cands or [], media_type, target)
        except Exception:  # noqa: BLE001
            logger.exception("[Retry] re-search failed")
        finally:
            ws._finish(todo, media_type)

    if background:
        threading.Thread(target=run, name="commissary-client-retry", daemon=True).start()
    else:
        run()
    return len(todo)
