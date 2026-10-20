"""Per-title overrides for a show or movie you don't own yet.

Upstream keeps "Also known as", the season-pack preference and the series type
on the library row (``shows``/``movies``), so they can only be set once the
title is already on disk - after the grab they exist to fix. This stores the
same three settings against the TMDB id, in the old fork's
``video_title_overrides`` table (same columns, so existing rows carry over):

* reading: the library row wins wherever it has a value; this fills the blanks
  (and is all there is for a title with no row);
* writing through upstream's owned-title endpoints mirrors here, so clearing a
  value on an owned show doesn't let a stale tmdb-keyed copy bring it back;
* a stored series type is adopted onto the row when the show is scanned in,
  before any library default, because it was chosen by hand.
"""

from __future__ import annotations

import sqlite3
from typing import Optional

from utils.logging_config import get_logger

logger = get_logger("commissary.title_overrides")

TABLE_SQL = (
    "CREATE TABLE IF NOT EXISTS video_title_overrides ("
    " kind TEXT NOT NULL,"
    " tmdb_id INTEGER NOT NULL,"
    " aka_titles TEXT,"
    " series_type TEXT,"
    " updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,"
    " season_pack_mode TEXT,"
    " PRIMARY KEY (kind, tmdb_id))")

SERIES_TYPES = ("standard", "daily", "anime")
PACK_PREFS = ("auto", "prefer", "never")
# The old fork also had 'only' (packs and nothing else). Until that mode is
# ported it reads as 'prefer', the nearest upstream setting; the stored value
# is left alone so the port can pick it up.
_PACK_READ = {"only": "prefer"}


def ensure_table(conn) -> None:
    conn.execute(TABLE_SQL)
    cols = {r[1] for r in conn.execute("PRAGMA table_info(video_title_overrides)").fetchall()}
    if "season_pack_mode" not in cols:     # a 1.6.x table predates it
        conn.execute("ALTER TABLE video_title_overrides ADD COLUMN season_pack_mode TEXT")


def _kind(kind) -> Optional[str]:
    k = str(kind or "").lower()
    return {"tv": "show", "episode": "show", "season": "show"}.get(k, k) \
        if k in ("movie", "show", "tv", "episode", "season") else None


def _aliases_in(raw) -> list:
    """Stored one per line (the old fork's format)."""
    seen, out = set(), []
    for line in str(raw or "").splitlines():
        v = line.strip()
        if v and v.lower() not in seen:
            seen.add(v.lower())
            out.append(v)
    return out


def _aliases_out(values) -> Optional[str]:
    seen, out = set(), []
    for v in values or []:
        v = str(v).strip()
        if v and v.lower() not in seen:
            seen.add(v.lower())
            out.append(v)
    return "\n".join(out) or None


def _empty() -> dict:
    return {"manual_aliases": [], "series_type": None, "pack_preference": "auto"}


def get(db, kind, tmdb_id) -> dict:
    """What is stored for this TMDB id; empty values mean 'nothing set'."""
    out = _empty()
    k = _kind(kind)
    try:
        tid = int(tmdb_id)
    except (TypeError, ValueError):
        return out
    if not k:
        return out
    try:
        conn = db._get_connection()
    except Exception:  # noqa: BLE001 - a stub DB has no overrides
        return out
    try:
        row = conn.execute("SELECT aka_titles, series_type, season_pack_mode "
                           "FROM video_title_overrides WHERE kind=? AND tmdb_id=?",
                           (k, tid)).fetchone()
    except sqlite3.Error:
        return out       # table not created on this handle (tests' bare DBs)
    finally:
        conn.close()
    if not row:
        return out
    out["manual_aliases"] = _aliases_in(row[0])
    st = str(row[1] or "").strip().lower()
    out["series_type"] = st if (k == "show" and st in SERIES_TYPES) else None
    pack = str(row[2] or "").strip().lower()
    pack = _PACK_READ.get(pack, pack)
    out["pack_preference"] = pack if (k == "show" and pack in PACK_PREFS) else "auto"
    return out


def set_values(db, kind, tmdb_id, **values) -> bool:
    """Write the keys passed (manual_aliases, series_type, pack_preference);
    a row left with nothing in it is removed."""
    k = _kind(kind)
    try:
        tid = int(tmdb_id)
    except (TypeError, ValueError):
        return False
    if not k or not tid:
        return False
    cols = {}
    if "manual_aliases" in values:
        cols["aka_titles"] = _aliases_out(values["manual_aliases"])
    if k == "show" and "series_type" in values:
        st = str(values["series_type"] or "").strip().lower()
        cols["series_type"] = st if st in SERIES_TYPES else None
    if k == "show" and "pack_preference" in values:
        pack = str(values["pack_preference"] or "").strip().lower()
        cols["season_pack_mode"] = pack if pack in ("prefer", "never") else None
    if not cols:
        return False
    conn = db._get_connection()
    try:
        ensure_table(conn)
        conn.execute("INSERT OR IGNORE INTO video_title_overrides (kind, tmdb_id) VALUES (?, ?)",
                     (k, tid))
        conn.execute("UPDATE video_title_overrides SET %s, updated_at=CURRENT_TIMESTAMP "
                     "WHERE kind=? AND tmdb_id=?" % ", ".join("%s=?" % c for c in cols),
                     (*cols.values(), k, tid))
        conn.execute("DELETE FROM video_title_overrides WHERE kind=? AND tmdb_id=? "
                     "AND COALESCE(aka_titles,'')='' AND COALESCE(series_type,'')='' "
                     "AND COALESCE(season_pack_mode,'')=''", (k, tid))
        conn.commit()
        return True
    except sqlite3.Error:
        logger.exception("saving title overrides for %s %s failed", k, tid)
        return False
    finally:
        conn.close()


def _row_tmdb(db, kind, library_id) -> Optional[int]:
    table = "movies" if _kind(kind) == "movie" else "shows"
    try:
        conn = db._get_connection()
    except Exception:  # noqa: BLE001
        return None
    try:
        row = conn.execute(f"SELECT tmdb_id FROM {table} WHERE id=?", (int(library_id),)).fetchone()
        return int(row[0]) if row and row[0] else None
    except (sqlite3.Error, TypeError, ValueError):
        return None
    finally:
        conn.close()


def library_row(db, kind, tmdb_id) -> Optional[int]:
    """The owned row for this TMDB id, if there is one."""
    table = "movies" if _kind(kind) == "movie" else "shows"
    try:
        conn = db._get_connection()
    except Exception:  # noqa: BLE001
        return None
    try:
        row = conn.execute(f"SELECT id FROM {table} WHERE tmdb_id=? ORDER BY id LIMIT 1",
                           (int(tmdb_id),)).fetchone()
        return int(row[0]) if row else None
    except (sqlite3.Error, TypeError, ValueError):
        return None
    finally:
        conn.close()


# ── upstream hooks ──────────────────────────────────────────────────────────

def fill(db, kind, out: dict, *, tmdb_id=None, library_id=None) -> dict:
    """Upstream's override dict with its blanks filled from the TMDB-keyed
    store. A value on the library row always wins."""
    tid = tmdb_id or (_row_tmdb(db, kind, library_id) if library_id else None)
    if not tid:
        return out
    stored = get(db, kind, tid)
    if not out.get("manual_aliases") and stored["manual_aliases"]:
        out["manual_aliases"] = stored["manual_aliases"]
    if _kind(kind) == "show" and (out.get("pack_preference") or "auto") == "auto" \
            and stored["pack_preference"] != "auto":
        out["pack_preference"] = stored["pack_preference"]
    return out


def mirror(db, kind, library_id, values: dict) -> None:
    """An owned title's edit, copied to its TMDB id so both agree."""
    keep = {k: v for k, v in values.items()
            if k in ("manual_aliases", "pack_preference", "series_type")}
    if not keep:
        return
    tid = _row_tmdb(db, kind, library_id)
    if tid:
        set_values(db, kind, tid, **keep)


def series_type(db, tmdb_id) -> Optional[str]:
    return get(db, "show", tmdb_id)["series_type"]


# The wishlist queries' series type: the show row's own type first, then this.
WISHLIST_SERIES_TYPE_SQL = (
    "(SELECT o.series_type FROM video_title_overrides o WHERE o.kind='show' "
    "AND o.tmdb_id = w.tmdb_id AND o.series_type IN ('standard','daily','anime'))")


def adopt_series_types(conn) -> int:
    """Shows scanned in with no type of their own take the one set before
    they were owned. Runs before a library's default is applied."""
    try:
        cur = conn.execute(
            "UPDATE shows SET series_type = (SELECT o.series_type FROM video_title_overrides o "
            " WHERE o.kind='show' AND o.tmdb_id = shows.tmdb_id) "
            "WHERE COALESCE(shows.series_type,'') = '' AND shows.tmdb_id IS NOT NULL "
            "AND EXISTS (SELECT 1 FROM video_title_overrides o WHERE o.kind='show' "
            " AND o.tmdb_id = shows.tmdb_id AND o.series_type IN ('standard','daily','anime'))")
        return cur.rowcount
    except sqlite3.Error:
        return 0
