"""Lock automatic edits: no unattended import into a show, season or movie.

An automatic import files a release wherever its download row says it
belongs, and an upgrade DELETES the copy it replaces. A release that names no
episode (fansub absolute numbering, a bare title, a mis-titled scene release)
has nothing for the wrong-episode check to disagree with, so a wrong season
number on the row is enough to replace a good file with a different episode.

A locked title refuses every UNATTENDED placement, a brand-new episode
included: a release that mis-identified itself is exactly the one whose "this
is new" can't be trusted either. A show or movie lock covers everything; a
season lock covers its own season, so a finished season can be sealed while
the airing one keeps arriving (a pack spanning both imports the unlocked half).

* the refusal is an ``import_failed`` row naming the lock, never a bad release,
  so the release is not blocklisted - it may be fine, just aimed at protected
  content;
* manual import (api/video/manual_import.py) never passes through here: placing
  the file by hand IS the check the lock asks for;
* the automatic search and RSS skip locked targets, since everything they grab
  for one would be refused;
* every lookup fails OPEN - a lock that silently swallowed downloads on a
  database hiccup would be worse than one that missed a protection.

Same ``import_locked`` columns as the old fork, so its locks carry over.
"""

from __future__ import annotations

import sqlite3
from typing import Any, Dict, List, Optional

from utils.logging_config import get_logger

logger = get_logger("commissary.import_lock")

COLUMNS = [("movies", "import_locked", "INTEGER NOT NULL DEFAULT 0"),
           ("shows", "import_locked", "INTEGER NOT NULL DEFAULT 0"),
           ("seasons", "import_locked", "INTEGER NOT NULL DEFAULT 0")]

_TABLE = {"movie": "movies", "show": "shows", "season": "seasons"}


def ensure_columns(conn) -> None:
    for table, col, coltype in COLUMNS:
        cols = {r[1] for r in conn.execute(f"PRAGMA table_info({table})").fetchall()}
        if cols and col not in cols:
            try:
                conn.execute(f"ALTER TABLE {table} ADD COLUMN {col} {coltype}")
            except sqlite3.OperationalError as e:
                if "duplicate column" not in str(e).lower():
                    raise


def _rows(db, sql, params=()) -> list:
    try:
        conn = db._get_connection()
    except Exception:  # noqa: BLE001
        return []
    try:
        return conn.execute(sql, params).fetchall()
    except (sqlite3.Error, TypeError, ValueError):
        logger.debug("import-lock lookup failed", exc_info=True)
        return []
    finally:
        conn.close()


# ── the verdict ─────────────────────────────────────────────────────────────

def reason(db, kind, tmdb_id, season_number=None) -> Optional[str]:
    """Why an unattended import into this target is refused, or None.

    By TMDB id: it's the only identity a download carries before it is placed.
    A title not in the library can't be locked - there is nothing to protect.
    Every library row with the id counts, so a second server's copy or a
    re-keyed row can't slip the lock."""
    try:
        tid = int(tmdb_id)
    except (TypeError, ValueError):
        return None
    if str(kind or "").lower() == "movie":
        rows = _rows(db, "SELECT title FROM movies WHERE tmdb_id=? AND import_locked=1 LIMIT 1", (tid,))
        return ("%s is locked — automatic imports are turned off for it"
                % (rows[0][0] or "This movie")) if rows else None
    rows = _rows(db, "SELECT title FROM shows WHERE tmdb_id=? AND import_locked=1 LIMIT 1", (tid,))
    if rows:
        return "%s is locked — automatic imports are turned off for it" % (rows[0][0] or "This show")
    try:
        sn = int(season_number)
    except (TypeError, ValueError):
        return None
    rows = _rows(db, "SELECT s.title FROM seasons x JOIN shows s ON s.id = x.show_id "
                     "WHERE s.tmdb_id=? AND x.season_number=? AND x.import_locked=1 LIMIT 1",
                 (tid, sn))
    return ("%s season %d is locked — automatic imports are turned off for it"
            % (rows[0][0] or "This show", sn)) if rows else None


def refusal(db, dl: Dict[str, Any]) -> Optional[str]:
    """The monitor's question for one finished download (or one pack episode,
    which carries its own season)."""
    try:
        from core.video.download_monitor import _wishlist_ids
        kind, tmdb_id, season, _episode, _ctx = _wishlist_ids(db, dl)
        if kind not in ("movie", "episode") or not tmdb_id:
            return None
        return reason(db, kind, tmdb_id, season)
    except Exception:  # noqa: BLE001 - never wedge an import on a lookup
        logger.debug("import-lock check failed for download %s", (dl or {}).get("id"), exc_info=True)
        return None


def locked_targets(db) -> Dict[str, set]:
    """Every lock at once, by TMDB id, for filtering a whole wishlist pass."""
    out = {"movie": set(), "show": set(), "season": set()}
    for (tid,) in _rows(db, "SELECT tmdb_id FROM movies WHERE import_locked=1 AND tmdb_id IS NOT NULL"):
        out["movie"].add(str(tid))
    for (tid,) in _rows(db, "SELECT tmdb_id FROM shows WHERE import_locked=1 AND tmdb_id IS NOT NULL"):
        out["show"].add(str(tid))
    for tid, sn in _rows(db, "SELECT s.tmdb_id, x.season_number FROM seasons x "
                             "JOIN shows s ON s.id = x.show_id "
                             "WHERE x.import_locked=1 AND s.tmdb_id IS NOT NULL"):
        out["season"].add((str(tid), int(sn)))
    return out


def unlocked(items: List[Dict[str, Any]], media_type: str, db=None) -> List[Dict[str, Any]]:
    """Wishlist rows minus the ones whose import would be refused."""
    if not items:
        return items
    try:
        if db is None:
            from api.video import get_video_db
            db = get_video_db()
        locks = locked_targets(db)
    except Exception:  # noqa: BLE001 - fail open
        return items
    if not any(locks.values()):
        return items
    keep = []
    for it in items:
        if media_type == "movie":
            locked = str(it.get("tmdb_id")) in locks["movie"]
        else:
            tid = str(it.get("show_tmdb_id"))
            locked = tid in locks["show"] or (tid, int(it.get("season_number") or 0)) in locks["season"]
        if not locked:
            keep.append(it)
    if len(keep) != len(items):
        logger.info("[Import lock] skipped %d wished %s(s) in locked titles or seasons",
                    len(items) - len(keep), media_type)
    return keep


# ── reading and setting ─────────────────────────────────────────────────────

def state(db, kind, item_id) -> Optional[Dict[str, Any]]:
    """{locked, seasons: [locked season numbers]} for a library row."""
    table = _TABLE.get(str(kind or "").lower())
    if table not in ("movies", "shows"):
        return None
    rows = _rows(db, f"SELECT import_locked FROM {table} WHERE id=?", (int(item_id),))
    if not rows:
        return None
    out = {"locked": bool(rows[0][0]), "seasons": []}
    if table == "shows":
        out["seasons"] = [r[0] for r in _rows(
            db, "SELECT season_number FROM seasons WHERE show_id=? AND import_locked=1 "
                "ORDER BY season_number", (int(item_id),))]
    return out


def _write(db, sql, params) -> bool:
    conn = db._get_connection()
    try:
        cur = conn.execute(sql, params)
        conn.commit()
        return cur.rowcount > 0
    except (sqlite3.Error, TypeError, ValueError):
        logger.exception("saving an import lock failed")
        return False
    finally:
        conn.close()


def set_lock(db, kind, item_id, locked: bool) -> bool:
    table = _TABLE.get(str(kind or "").lower())
    if table not in ("movies", "shows"):
        return False
    return _write(db, f"UPDATE {table} SET import_locked=? WHERE id=?",
                  (1 if locked else 0, int(item_id)))


def set_season_lock(db, show_id, season_number, locked: bool) -> bool:
    return _write(db, "UPDATE seasons SET import_locked=? WHERE show_id=? AND season_number=?",
                  (1 if locked else 0, int(show_id), int(season_number)))
