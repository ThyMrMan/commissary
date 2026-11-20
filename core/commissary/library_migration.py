"""Move the fork's video Libraries onto upstream's own (3.6.4, #2029). Runs once.

Up to 3.6.1 the fork kept its own registry in ``root_folders`` (one row per
Library: a Plex/Jellyfin section, its folder, category, trackers, default
quality profile and series type) and each title's choice in
``root_folder_id`` columns. Upstream 3.6.4 added the same idea as
``video_libraries`` (main / 4K / custom, with options) and
``video_title_libraries`` (a library picked for a title by hand), so the fork's
registry retires. This copies it over once and leaves the old rows alone (they
are never read again), so nothing is lost if this ever has to be looked at.

Per kind, in the fork's order:

- the first Library is the kind's primary: upstream's builtin ``main`` library
  takes its name, category, trackers and default profile (its folder stays in
  the ``movies_path`` / ``tv_path`` setting, filled from the Library only when
  that setting is empty);
- a TV Library whose folder IS the anime folder becomes no library at all:
  upstream files anime there by itself, and its section is marked anime;
- every other Library becomes a custom library with its own folder;
- a Library of series type ``anime`` marks its server section anime
  (``<server>.anime_libraries``), which is how upstream knows a show is anime.

Server sections: extra TV sections go to upstream's ``<server>.tv_libraries_more``;
extra MOVIE sections, which upstream can't scan, go to the fork's
``<server>.movies_libraries_more`` (read by :func:`movie_sections`).

Titles: a choice of anything but the primary becomes a
``video_title_libraries`` row (owned titles, wished titles, followed shows), so
the next grab files where it did before.
"""

from __future__ import annotations

import json
import os
import sqlite3

from utils.logging_config import get_logger

logger = get_logger("commissary.library_migration")

DONE_KEY = "commissary_libraries_migrated"
LOCKS_KEY = "commissary_locks_migrated"
MOVIES_MORE = ".movies_libraries_more"

_COLS = ("id", "path", "content_kind", "server", "server_title", "label", "sort_order",
         "category", "preferred_indexer_ids", "default_series_type",
         "default_quality_profile_id")


def _setting(conn, key):
    row = conn.execute("SELECT value FROM video_settings WHERE key=?", (key,)).fetchone()
    return row[0] if row else None


def _set(conn, key, value) -> None:
    conn.execute("INSERT INTO video_settings (key, value) VALUES (?, ?) "
                 "ON CONFLICT(key) DO UPDATE SET value=excluded.value", (key, value))


def _titles(raw) -> list:
    try:
        out = json.loads(raw) if raw else []
    except (TypeError, ValueError):
        return []
    return [str(t) for t in out if str(t or "").strip()] if isinstance(out, list) else []


def _add_titles(conn, key, titles) -> None:
    have = _titles(_setting(conn, key))
    for t in titles:
        if t and t not in have:
            have.append(t)
    _set(conn, key, json.dumps(have))


def _same(a, b) -> bool:
    a, b = str(a or "").strip(), str(b or "").strip()
    return bool(a and b) and os.path.normcase(os.path.normpath(a)) == os.path.normcase(os.path.normpath(b))


def _columns(conn, table) -> set:
    return {r[1] for r in conn.execute(f"PRAGMA table_info({table})").fetchall()}


def _options(row) -> dict:
    cat = str(row.get("category") or "").strip()
    ids = [int(p) for p in str(row.get("preferred_indexer_ids") or "").split(",")
           if p.strip().isdigit()]
    out = {"indexer_ids": ids, "torrent_category": cat, "usenet_category": cat,
           "quality_profile_id": int(row.get("default_quality_profile_id") or 0)}
    from core.video.libraries import normalize_options
    return normalize_options(out)


def _merge_options(raw, new) -> dict:
    """the main library keeps anything it already had; the Library fills gaps"""
    from core.video.libraries import normalize_options
    try:
        have = json.loads(raw or "{}")
    except (TypeError, ValueError):
        have = {}
    have = have if isinstance(have, dict) else {}
    for key, value in new.items():
        if value and not have.get(key):
            have[key] = value
    return normalize_options(have)


def _fork_rows(conn) -> list:
    if not {"label", "server_title", "sort_order"} <= _columns(conn, "root_folders"):
        return []
    conn.row_factory = sqlite3.Row
    rows = conn.execute(
        "SELECT " + ", ".join(_COLS) + " FROM root_folders "
        "WHERE content_kind IN ('movie', 'show') "
        "AND (COALESCE(server_title, '') <> '' OR COALESCE(label, '') <> '') "
        "ORDER BY content_kind, COALESCE(sort_order, 0), id").fetchall()
    return [dict(r) for r in rows]


def run(conn) -> dict | None:
    """Copy the fork's Libraries over, once. Returns what it did (None when
    there was nothing to do). Never raises: a failure is logged and retried
    on the next start."""
    try:
        if str(_setting(conn, DONE_KEY) or "") == "1":
            return None
        rows = _fork_rows(conn)
        if not rows:
            _set(conn, DONE_KEY, "1")
            return None
        mapped, made, sections = {}, 0, {}
        anime_path = str(_setting(conn, "anime_path") or "")
        for kind in ("movie", "show"):
            mine = [r for r in rows if r["content_kind"] == kind]
            for n, row in enumerate(mine):
                name = (row.get("label") or row.get("server_title") or "Library").strip()[:80]
                server, section = row.get("server") or "", row.get("server_title") or ""
                if server and section:
                    sections.setdefault((server, kind), []).append(section)
                if kind == "show" and str(row.get("default_series_type") or "") == "anime" \
                        and server and section:
                    _add_titles(conn, server + ".anime_libraries", [section])
                if n == 0:
                    main = conn.execute("SELECT id, options FROM video_libraries "
                                        "WHERE kind=? AND builtin='main'", (kind,)).fetchone()
                    if main is None:
                        continue
                    conn.execute("UPDATE video_libraries SET name=?, options=? WHERE id=?",
                                 (name, json.dumps(_merge_options(main[1], _options(row))), main[0]))
                    key = "movies_path" if kind == "movie" else "tv_path"
                    if row.get("path") and not str(_setting(conn, key) or "").strip():
                        _set(conn, key, row["path"])
                    mapped[row["id"]] = None          # the default: nothing to pin
                    continue
                if kind == "show" and _same(row.get("path"), anime_path):
                    if server and section:
                        _add_titles(conn, server + ".anime_libraries", [section])
                    mapped[row["id"]] = None          # upstream files anime there itself
                    continue
                have = conn.execute("SELECT id FROM video_libraries WHERE kind=? AND path=? "
                                    "AND builtin IS NULL", (kind, row.get("path") or "")).fetchone()
                if have:
                    mapped[row["id"]] = have[0]
                    continue
                pos = conn.execute("SELECT COALESCE(MAX(position), 1) + 1 FROM video_libraries "
                                   "WHERE kind=?", (kind,)).fetchone()[0]
                cur = conn.execute(
                    "INSERT INTO video_libraries (kind, name, path, enabled, position, options) "
                    "VALUES (?, ?, ?, 1, ?, ?)",
                    (kind, name, row.get("path") or "", pos, json.dumps(_options(row))))
                mapped[row["id"]] = cur.lastrowid
                made += 1
        # the server sections each kind scans
        for (server, kind), titles in sections.items():
            if kind == "movie":
                main = str(_setting(conn, server + ".movies_library") or "").strip() or titles[0]
                _set(conn, server + ".movies_library", main)
                _add_titles(conn, server + MOVIES_MORE, [t for t in titles if t != main])
            else:
                main = str(_setting(conn, server + ".tv_library") or "").strip() or titles[0]
                _set(conn, server + ".tv_library", main)
                _add_titles(conn, server + ".tv_libraries_more", [t for t in titles if t != main])
        # each title's choice
        pinned = 0
        for table, kind_sql in (("movies", "'movie'"), ("shows", "'show'"),
                                ("video_wishlist", "CASE WHEN kind='movie' THEN 'movie' ELSE 'show' END"),
                                ("video_watchlist", "'show'")):
            if not {"root_folder_id", "tmdb_id"} <= _columns(conn, table):
                continue
            for kind, tmdb_id, rid in conn.execute(
                    f"SELECT DISTINCT {kind_sql}, tmdb_id, root_folder_id FROM {table} "
                    "WHERE root_folder_id IS NOT NULL AND tmdb_id IS NOT NULL").fetchall():
                lib = mapped.get(rid)
                if not lib:
                    continue
                cur = conn.execute("INSERT OR IGNORE INTO video_title_libraries "
                                   "(kind, tmdb_id, library_id) VALUES (?, ?, ?)",
                                   (kind, int(tmdb_id), lib))
                pinned += cur.rowcount or 0
        _set(conn, DONE_KEY, "1")
        out = {"libraries": len(rows), "created": made, "titles": pinned}
        logger.info("video Libraries moved onto upstream's libraries: %s", out)
        return out
    except Exception:   # noqa: BLE001 - never block a start; try again next time
        logger.exception("could not move the video Libraries onto upstream's libraries")
        return None
    finally:
        conn.row_factory = None


def move_locks(conn) -> int:
    """The fork's "Lock automatic imports" on a whole movie or show becomes
    upstream's padlock (3.6.6, ``hands_off``: automation leaves the title and
    its files alone), once. A lock on one season has no upstream equivalent
    and is left behind."""
    try:
        if str(_setting(conn, LOCKS_KEY) or "") == "1":
            return 0
        moved = 0
        for table in ("movies", "shows"):
            cols = _columns(conn, table)
            if {"import_locked", "hands_off"} <= cols:
                moved += conn.execute(f"UPDATE {table} SET hands_off=1 "
                                      "WHERE import_locked=1 AND hands_off=0").rowcount or 0
        _set(conn, LOCKS_KEY, "1")
        if moved:
            logger.info("import locks moved onto the padlock: %d title(s)", moved)
        return moved
    except Exception:   # noqa: BLE001 - never block a start; try again next time
        logger.exception("could not move the import locks onto the padlock")
        return 0


def movie_sections(db, server, selection: dict) -> dict:
    """Upstream's selection with the extra MOVIE sections added: upstream scans
    one movie section and any number of TV ones (``tv_more``); the fork keeps
    the same for movies under ``<server>.movies_libraries_more``."""
    sel = dict(selection or {})
    try:
        more = _titles(db.get_setting(server + MOVIES_MORE)) if server else []
    except Exception:   # noqa: BLE001 - the plain selection, as upstream reads it
        return sel
    main = str(sel.get("movies") or "").strip()
    extra = [t for t in more if t != main]
    if extra and main:
        sel["movies"] = [main] + extra
    return sel


__all__ = ["run", "move_locks", "movie_sections", "DONE_KEY", "LOCKS_KEY", "MOVIES_MORE"]
