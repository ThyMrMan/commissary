"""Video Libraries: several labelled libraries per kind (Movies, Anime-Movies,
TV-Shows, Anime, ...), each one a Plex/Jellyfin section plus its folder on disk.

Upstream SoulSync scans ONE server section per kind and files every import into
ONE folder per kind (``movies_path`` / ``tv_path``). Its schema still has a
``root_folders`` table and ``root_folder_id`` columns, but nothing reads them.
The legacy Commissary fork used that table as a registry; this module brings it
back on upstream 3.5.7, owning the queries so upstream files only need hooks.

A library row carries:
  server, server_title    the Plex/Jellyfin section it is (scanned + stamped)
  path                    where new grabs for it are filed
  label, sort_order       display name; lowest sort_order is the kind's primary
  category                torrent/usenet client category for its grabs
  preferred_indexer_ids   Prowlarr indexers its searches are restricted to
  default_series_type     shows only: standard / daily / anime
  default_quality_profile_id   profile for titles with no profile of their own

``movies/shows/video_wishlist/video_watchlist.root_folder_id`` say which
library a title belongs to (or, for a wished/followed title, where it goes).

RULE: with no rows in ``root_folders`` every function here answers "nothing",
and every hook falls through to upstream's own behaviour unchanged.
"""

from __future__ import annotations

import sqlite3

from utils.logging_config import get_logger

logger = get_logger("commissary.video_libraries")

# Columns the registry needs beyond upstream's schema. Ensured AFTER upstream's
# data migrations: v49 rebuilds video_watchlist with a fixed column list and
# would drop root_folder_id if it were added before.
COLUMNS = (
    ("root_folders", "server", "TEXT"),
    ("root_folders", "server_title", "TEXT"),
    ("root_folders", "label", "TEXT"),
    ("root_folders", "sort_order", "INTEGER DEFAULT 0"),
    ("root_folders", "category", "TEXT"),
    ("root_folders", "preferred_indexer_ids", "TEXT"),
    ("root_folders", "default_series_type", "TEXT"),
    ("root_folders", "default_quality_profile_id", "INTEGER"),
    ("movies", "root_folder_id", "INTEGER"),
    ("shows", "root_folder_id", "INTEGER"),
    ("video_wishlist", "root_folder_id", "INTEGER"),
    ("video_watchlist", "root_folder_id", "INTEGER"),
)

# UI kind -> root_folders.content_kind
KIND = {"movies": "movie", "tv": "show", "youtube": "youtube"}
_CONTENT_KIND = {"movie": "movie", "movies": "movie", "show": "show", "shows": "show",
                 "tv": "show", "episode": "show", "season": "show", "youtube": "youtube"}
SERIES_TYPES = ("standard", "daily", "anime")

_ROW_COLS = ("id, path, content_kind, server, server_title, label, sort_order, category, "
             "preferred_indexer_ids, default_series_type, default_quality_profile_id")


def content_kind(kind) -> str:
    return _CONTENT_KIND.get(str(kind or "").lower(), str(kind or "").lower())


def ensure_columns(conn) -> None:
    """Idempotent ALTERs (a duplicate from a racing process is fine)."""
    for table, col, coltype in COLUMNS:
        cols = {r[1] for r in conn.execute(f"PRAGMA table_info({table})").fetchall()}
        if not cols or col in cols:
            continue
        try:
            conn.execute(f"ALTER TABLE {table} ADD COLUMN {col} {coltype}")
        except sqlite3.OperationalError as e:
            if "duplicate column" not in str(e).lower():
                raise


def norm_indexer_ids(raw) -> str | None:
    """'3, 7,x,12' -> '3,7,12'; None when empty. Same tolerant parsing as the
    global prowlarr.indexer_ids allowlist."""
    if isinstance(raw, (list, tuple, set)):
        raw = ",".join(str(x) for x in raw)
    ids = [p.strip() for p in str(raw or "").split(",") if p.strip().isdigit()]
    return ",".join(ids) if ids else None


def _rows(db, sql, params=()):
    conn = db._get_connection()
    try:
        conn.row_factory = sqlite3.Row
        return [dict(r) for r in conn.execute(sql, params).fetchall()]
    except sqlite3.Error:
        logger.exception("video library query failed")
        return []
    finally:
        conn.close()


# ── reading the registry ─────────────────────────────────────────────────────

def has_libraries(db, server=None) -> bool:
    sql, params = "SELECT 1 FROM root_folders WHERE COALESCE(server_title,'') <> ''", ()
    if server:
        sql += " AND server=?"
        params = (server,)
    return bool(_rows(db, sql + " LIMIT 1", params))


def all_rows(db, kind=None) -> list:
    """Every library, server-agnostic (folders on disk don't care which server
    indexes them), ordered movie/show/youtube then sort_order."""
    sql, params = f"SELECT {_ROW_COLS} FROM root_folders", ()
    if kind:
        sql += " WHERE content_kind=?"
        params = (content_kind(kind),)
    sql += (" ORDER BY CASE content_kind WHEN 'movie' THEN 0 WHEN 'show' THEN 1 ELSE 2 END,"
            " sort_order, id")
    return _rows(db, sql, params)


def paths(db, kind=None) -> list:
    return [p for p in (str(r.get("path") or "").strip() for r in all_rows(db, kind)) if p]


def list_libraries(db, server) -> dict:
    """{"movies": [...], "tv": [...], "youtube": [...]} for the settings editor."""
    out = {}
    for ui_kind, ck in KIND.items():
        out[ui_kind] = _rows(db, f"SELECT {_ROW_COLS} FROM root_folders "
                                 "WHERE server=? AND content_kind=? ORDER BY sort_order, id",
                             (server, ck))
    return out


def section_titles(db, server, kind) -> list:
    """Server sections to scan for a kind ('movies'/'tv'), in sort order."""
    ck = KIND.get(kind, content_kind(kind))
    return [r["server_title"] for r in _rows(
        db, "SELECT server_title FROM root_folders WHERE server=? AND content_kind=? "
            "AND COALESCE(server_title,'') <> '' ORDER BY sort_order, id", (server, ck))]


def ids_by_section(db, server, kind) -> dict:
    """{server_title: root_folder_id} in one query, for stamping scanned rows."""
    return {r["server_title"]: r["id"] for r in _rows(
        db, "SELECT server_title, id FROM root_folders WHERE server=? AND content_kind=?",
        (server, content_kind(kind))) if r.get("server_title")}


def get(db, root_folder_id) -> dict | None:
    try:
        rid = int(root_folder_id)
    except (TypeError, ValueError):
        return None
    rows = _rows(db, f"SELECT {_ROW_COLS} FROM root_folders WHERE id=?", (rid,))
    return rows[0] if rows else None


def primary(db, kind, server=None) -> dict | None:
    """The kind's primary library (lowest sort_order) — the destination for a
    title nothing else has an opinion about."""
    sql, params = f"SELECT {_ROW_COLS} FROM root_folders WHERE content_kind=?", [content_kind(kind)]
    if server:
        sql += " AND server=?"
        params.append(server)
    rows = _rows(db, sql + " AND COALESCE(path,'') <> '' ORDER BY sort_order, id LIMIT 1",
                 tuple(params))
    return rows[0] if rows else None


def for_path(db, path) -> dict | None:
    """The library a folder lies in (longest match wins for nested libraries)."""
    target = str(path or "").strip().replace("\\", "/").rstrip("/").lower()
    if not target:
        return None
    best, best_len = None, -1
    for r in all_rows(db):
        p = str(r.get("path") or "").strip().replace("\\", "/").rstrip("/").lower()
        if p and (target == p or target.startswith(p + "/")) and len(p) > best_len:
            best, best_len = r, len(p)
    return best


# ── which library a title belongs to ─────────────────────────────────────────

def owned_root_folder_id(db, kind, tmdb_id) -> int | None:
    """The library an owned movie/show is filed under, by tmdb id."""
    table = "movies" if content_kind(kind) == "movie" else "shows"
    try:
        tid = int(tmdb_id)
    except (TypeError, ValueError):
        return None
    rows = _rows(db, f"SELECT root_folder_id FROM {table} WHERE tmdb_id=? "
                     "AND root_folder_id IS NOT NULL ORDER BY id LIMIT 1", (tid,))
    return rows[0]["root_folder_id"] if rows else None


def resolve_destination(db, kind, tmdb_id) -> int | None:
    """Where a title should be filed: the library it already lives in (moving
    new episodes elsewhere would split a show), else, for shows, the library
    chosen when it was followed. None = nothing has an opinion (use primary)."""
    owned = owned_root_folder_id(db, kind, tmdb_id)
    if owned or content_kind(kind) != "show":
        return owned
    try:
        tid = int(tmdb_id)
    except (TypeError, ValueError):
        return None
    rows = _rows(db, "SELECT root_folder_id FROM video_watchlist WHERE kind='show' "
                     "AND tmdb_id=? AND root_folder_id IS NOT NULL LIMIT 1", (tid,))
    return rows[0]["root_folder_id"] if rows else None


def library_for_item(db, kind, *, item=None, tmdb_id=None, root_folder_id=None) -> dict | None:
    """The library a grab for this title goes into, or None when the registry
    has no say (empty registry -> upstream's own movies_path/tv_path).

    Order: an explicit choice, the wishlist row's own root_folder_id, the
    owned title's / followed show's library, then the kind's primary.
    A choice pointing at a deleted library falls through rather than failing.
    """
    ck = content_kind(kind)
    candidates = [root_folder_id]
    if item:
        candidates.append(item.get("root_folder_id"))
        tmdb_id = tmdb_id or item.get("tmdb_id")
    if tmdb_id:
        candidates.append(resolve_destination(db, ck, tmdb_id))
    for rid in candidates:
        row = get(db, rid) if rid not in (None, "", 0, "0") else None
        if row and row.get("content_kind") == ck and str(row.get("path") or "").strip():
            return row
    return primary(db, ck)


def target_dir(db, kind, **kw) -> str:
    row = library_for_item(db, kind, **kw)
    return str(row.get("path") or "").strip() if row else ""


def category(db, kind, **kw) -> str | None:
    row = library_for_item(db, kind, **kw)
    return (str(row.get("category") or "").strip() or None) if row else None


def indexer_ids(db, kind, **kw) -> list | None:
    """Prowlarr indexer ids a library's searches are limited to; None = no limit."""
    row = library_for_item(db, kind, **kw)
    ids = norm_indexer_ids(row.get("preferred_indexer_ids")) if row else None
    return [int(x) for x in ids.split(",")] if ids else None


# ── writing ──────────────────────────────────────────────────────────────────

def stamp(db, kind, row_id, root_folder_id) -> None:
    """Record which library a scanned movie/show came from."""
    if row_id is None or root_folder_id is None:
        return
    table = "movies" if content_kind(kind) == "movie" else "shows"
    conn = db._get_connection()
    try:
        conn.execute(f"UPDATE {table} SET root_folder_id=? WHERE id=? "
                     "AND COALESCE(root_folder_id, -1) <> ?",
                     (int(root_folder_id), row_id, int(root_folder_id)))
        conn.commit()
    except sqlite3.Error:
        logger.exception("stamping %s %s with library %s failed", table, row_id, root_folder_id)
    finally:
        conn.close()


def apply_default_series_type(db, root_folder_id=None) -> int:
    """Give shows whose own series_type is UNSET their library's default
    (a library named Anime knows its shows are searched by absolute number).
    A hand-set or detected type always wins. Idempotent. Returns rows changed."""
    sql = ("UPDATE shows SET series_type = (SELECT rf.default_series_type FROM root_folders rf"
           " WHERE rf.id = shows.root_folder_id) "
           "WHERE COALESCE(shows.series_type,'') = '' AND shows.root_folder_id IS NOT NULL "
           "AND EXISTS (SELECT 1 FROM root_folders rf WHERE rf.id = shows.root_folder_id "
           "AND COALESCE(rf.default_series_type,'') <> '')")
    params = ()
    if root_folder_id is not None:
        sql += " AND shows.root_folder_id = ?"
        params = (int(root_folder_id),)
    conn = db._get_connection()
    try:
        cur = conn.execute(sql, params)
        conn.commit()
        if cur.rowcount:
            logger.info("[Libraries] applied library default series type to %s show(s)", cur.rowcount)
        return cur.rowcount
    except sqlite3.Error:
        logger.exception("apply_default_series_type failed")
        return 0
    finally:
        conn.close()


def save_libraries(db, server, movies=None, tv=None, youtube=None) -> dict:
    """Replace a server's libraries of each SUPPLIED kind with the given list
    (order = sort_order, index 0 = primary). A kind passed as None is left
    alone, so a caller managing movies+tv can't delete YouTube roots. Removed
    libraries unassign their titles rather than deleting anything."""
    supplied = {"movies": movies, "tv": tv, "youtube": youtube}
    conn = db._get_connection()
    try:
        for ui_kind, ck in KIND.items():
            entries = supplied.get(ui_kind)
            if entries is None:
                continue
            keep = set()
            for i, e in enumerate(entries):
                e = e or {}
                title = str(e.get("server_title") or "").strip()
                if not title:
                    continue
                label = str(e.get("label") or "").strip() or None
                path = str(e.get("path") or "").strip()
                cat = str(e.get("category") or "").strip() or None
                idx = norm_indexer_ids(e.get("preferred_indexer_ids"))
                dst = str(e.get("default_series_type") or "").strip().lower()
                dst = dst if ck == "show" and dst in SERIES_TYPES else None
                try:
                    dqp = int(e.get("default_quality_profile_id") or 0) or None
                except (TypeError, ValueError):
                    dqp = None
                values = (title, label, path, i, cat, idx, dst, dqp)
                eid = e.get("id")
                if eid:
                    conn.execute(
                        "UPDATE root_folders SET server_title=?, label=?, path=?, sort_order=?, "
                        "category=?, preferred_indexer_ids=?, default_series_type=?, "
                        "default_quality_profile_id=? WHERE id=? AND server=? AND content_kind=?",
                        values + (int(eid), server, ck))
                    keep.add(int(eid))
                else:
                    cur = conn.execute(
                        "INSERT INTO root_folders (server_title, label, path, sort_order, category, "
                        "preferred_indexer_ids, default_series_type, default_quality_profile_id, "
                        "server, content_kind) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                        values + (server, ck))
                    keep.add(cur.lastrowid)
            existing = {r[0] for r in conn.execute(
                "SELECT id FROM root_folders WHERE server=? AND content_kind=?", (server, ck))}
            for gone in existing - keep:
                for table in ("movies", "shows", "video_wishlist", "video_watchlist"):
                    conn.execute(f"UPDATE {table} SET root_folder_id=NULL WHERE root_folder_id=?",
                                 (gone,))
                conn.execute("DELETE FROM root_folders WHERE id=?", (gone,))
        conn.commit()
    finally:
        conn.close()
    apply_default_series_type(db)
    return list_libraries(db, server)


def set_wishlist_library(db, kind, tmdb_id, root_folder_id) -> int:
    """Send a wished title to a library before it is on disk. Setting one also
    stamps the owned row so a show doesn't end up split; clearing ('Default')
    only clears the wishlist rows."""
    rid = _checked_id(db, kind, root_folder_id)
    if rid is False:
        return 0
    ck = content_kind(kind)
    wl_kind = "movie" if ck == "movie" else "episode"
    conn = db._get_connection()
    try:
        cur = conn.execute("UPDATE video_wishlist SET root_folder_id=? WHERE kind=? AND tmdb_id=?",
                           (rid, wl_kind, int(tmdb_id)))
        if rid is not None:
            conn.execute(f"UPDATE {'movies' if ck == 'movie' else 'shows'} SET root_folder_id=? "
                         "WHERE tmdb_id=?", (rid, int(tmdb_id)))
        conn.commit()
        return cur.rowcount
    except (sqlite3.Error, TypeError, ValueError):
        logger.exception("set_wishlist_library failed (%s %s)", kind, tmdb_id)
        return 0
    finally:
        conn.close()


def set_watchlist_library(db, tmdb_id, root_folder_id) -> int:
    """Send a FOLLOWED show to a library, weeks before its first episode is
    wished. Cascades to queued episodes and the owned show when set."""
    rid = _checked_id(db, "show", root_folder_id)
    if rid is False:
        return 0
    conn = db._get_connection()
    try:
        cur = conn.execute("UPDATE video_watchlist SET root_folder_id=? WHERE kind='show' AND tmdb_id=?",
                           (rid, int(tmdb_id)))
        if rid is not None:
            conn.execute("UPDATE video_wishlist SET root_folder_id=? WHERE kind='episode' AND tmdb_id=?",
                         (rid, int(tmdb_id)))
            conn.execute("UPDATE shows SET root_folder_id=? WHERE tmdb_id=?", (rid, int(tmdb_id)))
        conn.commit()
        return cur.rowcount
    except (sqlite3.Error, TypeError, ValueError):
        logger.exception("set_watchlist_library failed (%s)", tmdb_id)
        return 0
    finally:
        conn.close()


def _checked_id(db, kind, root_folder_id):
    """None to clear, the id if it is a library of this kind, False if not."""
    if root_folder_id in (None, "", "null", 0, "0"):
        return None
    row = get(db, root_folder_id)
    if not row or row.get("content_kind") != content_kind(kind):
        return False
    return int(row["id"])
