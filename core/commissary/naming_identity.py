"""The LIBRARY's own naming facts for a title (fork 2.0.3).

An import used to name its destination from the GRAB — the wishlist title, the
episode's air year, and ``media_id`` — while the renamer names it from the
library row, so for one show they disagreed:

    import  ->  Kitchen Nightmares (US) (2026) (tmdb-)
    rename  ->  Kitchen Nightmares (US) (2007) (tmdb-235884)

and the media server saw two shows. A series template's year is the PREMIERE
year, and ``media_id`` is a TMDB id (or a local row id) — never a TVDB one.

``core.video.importer.plan_import`` is pure, so the caller resolves these facts
and hands them over as ``identity``, exactly like ``library_dir``.
"""

from __future__ import annotations

import sqlite3

from utils.logging_config import get_logger

logger = get_logger("commissary.naming_identity")


def naming_identity(db, kind: str, tmdb_id) -> dict | None:
    """``{title, year, tmdbid, tvdbid, imdbid[, episodes]}`` for an owned title,
    or None. ``episodes`` is keyed ``(season, episode)`` and covers the whole
    show in one query, so a season pack names every member from the library."""
    table = {"movie": "movies", "show": "shows", "episode": "shows"}.get(str(kind or "").lower())
    if not table or tmdb_id is None:
        return None
    try:
        tmdb_id = int(tmdb_id)
    except (TypeError, ValueError):
        return None
    conn = db._get_connection()
    try:
        # The year falls back to first_air_date/release_date: enrichment fills
        # those even when the media server omits the year.
        row = conn.execute(
            "SELECT title, "
            "COALESCE(year, CAST(substr(NULLIF(%s, ''), 1, 4) AS INTEGER)) AS year, "
            "tmdb_id, imdb_id%s FROM %s WHERE tmdb_id=? ORDER BY id LIMIT 1"
            % ("first_air_date" if table == "shows" else "release_date",
               ", tvdb_id, id" if table == "shows" else "", table),
            (tmdb_id,)).fetchone()
        if not row:
            return None
        out = {"title": row["title"], "year": row["year"],
               "tmdbid": row["tmdb_id"], "imdbid": row["imdb_id"],
               "tvdbid": row["tvdb_id"] if table == "shows" else None}
        if table == "shows":
            out["episodes"] = {
                (r["season_number"], r["episode_number"]):
                    {"episode_title": r["title"], "air_date": r["air_date"]}
                for r in conn.execute(
                    "SELECT season_number, episode_number, title, air_date "
                    "FROM episodes WHERE show_id=?", (row["id"],)).fetchall()}
        return out
    except sqlite3.Error:
        logger.exception("naming identity lookup failed (%s %s)", kind, tmdb_id)
        return None
    finally:
        conn.close()


def library_identity(db, dl) -> dict | None:
    """The naming identity for a download row's title, or None when it isn't
    owned yet. Best-effort: None keeps the grab-based naming."""
    try:
        from core.video.download_monitor import _wishlist_ids
        kind, tmdb_id, _sn, _en, _ctx = _wishlist_ids(db, dl)
        if kind == "youtube" or not tmdb_id:
            return None
        return naming_identity(db, "movie" if kind == "movie" else "show", tmdb_id)
    except Exception:   # noqa: BLE001 - a naming assist must never wedge an import
        logger.debug("library-identity resolution failed", exc_info=True)
        return None
