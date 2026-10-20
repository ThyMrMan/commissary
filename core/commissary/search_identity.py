"""What a manual video search knows about the title it is searching for.

Upstream's manual search judges releases against the bare title in the request
and nothing else, while the unattended drain uses the full alias set, the air
date and the absolute episode number. So a release named by an alias, a daily
named by its air date, or an anime episode named by its absolute number
('[SubsPlease] Show - 07') was rejected exactly when a person searched by hand
(legacy fork 1.6.10, 1.6.11, 2.0.1, 2.3.3, 2.4.2).

A search request identifies its title with a PAIR: media_id plus media_source,
where 'tmdb' means media_id is a TMDB id and anything else means a movies/shows
row id. Everything here hangs off resolving that pair to a TMDB id.

The Soulseek poll carries only scope/title/season/episode, so the identity a
search started with is remembered by its slskd search id (``remember`` /
``recall``) and the poll judges with the same facts as the start.
"""

from __future__ import annotations

import sqlite3
import threading
import time
from typing import Any, Optional

from utils.logging_config import get_logger

logger = get_logger("commissary.search_identity")

_MOVIE_KINDS = ("movie", "movies", "film", "films")


def kind_of(body) -> str:
    body = body or {}
    if str(body.get("scope") or "").lower() in ("episode", "season", "series", "show"):
        return "show"
    return "movie" if str(body.get("kind") or "").lower() in _MOVIE_KINDS else "show"


def _one(db, sql, params):
    try:
        conn = db._get_connection()
    except Exception:  # noqa: BLE001 - a stub DB: no identity
        return None
    try:
        row = conn.execute(sql, params).fetchone()
        return row[0] if row else None
    except (sqlite3.Error, TypeError, ValueError):
        return None
    finally:
        conn.close()


def tmdb_for(db, body) -> Optional[int]:
    """The TMDB id behind a search/grab payload, or None."""
    body = body or {}
    media_id = body.get("tmdb_id") or body.get("media_id")
    if not media_id:
        return None
    source = str(body.get("media_source") or "").lower()
    if body.get("tmdb_id") or source == "tmdb":
        try:
            return int(media_id)
        except (TypeError, ValueError):
            return None
    table = "movies" if kind_of(body) == "movie" else "shows"
    try:
        rid = int(media_id)
    except (TypeError, ValueError):
        return None
    return _one(db, f"SELECT tmdb_id FROM {table} WHERE id=?", (rid,))


def air_date(db, tmdb_id, season, episode) -> Optional[str]:
    if not tmdb_id or season is None or episode is None:
        return None
    value = _one(db, "SELECT e.air_date FROM episodes e WHERE e.show_id = "
                     "(SELECT id FROM shows WHERE tmdb_id=? ORDER BY id LIMIT 1) "
                     "AND e.season_number=? AND e.episode_number=?",
                 (int(tmdb_id), int(season), int(episode)))
    return str(value)[:10] if value else None


def series_type(db, tmdb_id) -> Optional[str]:
    if not tmdb_id:
        return None
    value = _one(db, "SELECT series_type FROM shows WHERE tmdb_id=? ORDER BY id LIMIT 1",
                 (int(tmdb_id),))
    if value and str(value).strip():
        return str(value).strip().lower()
    from core.commissary.title_overrides import series_type as stored
    return stored(db, tmdb_id)       # set before the show was owned


def episode_hints(db, body, season=None, episode=None):
    """(want_date, want_absolute) for an episode search. Both only ever ACCEPT a
    release (a miss is 'not proven'), so they are computed for every show
    rather than only ones tagged anime: a new anime has no row to be tagged on.

    The absolute number needs the show's episode list; for SEASON 1 it is the
    episode number by definition, which is the one safe fallback (guessing a
    later season would let 'Show - 05' satisfy S02E05)."""
    body = body or {}
    scope = str(body.get("scope") or "").lower()
    if scope and scope != "episode":
        return None, None
    s = body.get("season") if season is None else season
    e = body.get("episode") if episode is None else episode
    if s is None or e is None:
        return None, None
    tmdb = tmdb_for(db, body)
    want_date = want_absolute = None
    if tmdb:
        want_date = air_date(db, tmdb, s, e)
        try:
            want_absolute = db.episode_absolute_number(tmdb, s, e)
        except Exception:  # noqa: BLE001 - a matching assist never breaks a search
            want_absolute = None
    if want_absolute is None:
        try:
            if int(s) == 1 and int(e) >= 1:
                want_absolute = int(e)
        except (TypeError, ValueError):
            pass
    return want_date, want_absolute


def want_titles(db, body, title=None):
    """[primary, *TMDB aliases, *manual AKAs], the drain's own alias set, or just
    the primary when the title can't be resolved."""
    primary = title if title is not None else (body or {}).get("title")
    tmdb = tmdb_for(db, body)
    if not tmdb:
        return primary
    try:
        from core.automation.handlers.video_process_wishlist import _acceptable_titles
        titles = _acceptable_titles(primary, kind_of(body), tmdb)
        return titles if titles else primary
    except Exception:  # noqa: BLE001
        logger.debug("alias set unavailable for %s", tmdb, exc_info=True)
        return primary


class Identity:
    """Everything a manual search needs about its title, computed once."""

    def __init__(self, db, body, season=None, episode=None):
        body = body or {}
        self.body = body
        self.tmdb_id = tmdb_for(db, body)
        self.titles = want_titles(db, body)
        self.date, self.absolute = episode_hints(db, body, season, episode)
        self.series_type = series_type(db, self.tmdb_id) if kind_of(body) == "show" else None

    def judge(self) -> dict:
        """Keyword arguments for _evaluate_hits."""
        return {"want_title": self.titles, "want_date": self.date,
                "want_absolute": self.absolute}

    def query(self) -> dict:
        """Keyword arguments for build_query / prowlarr_search (only those set)."""
        out = {"air_date": self.date, "absolute": self.absolute, "series_type": self.series_type}
        return {k: v for k, v in out.items() if v}

    def ctx(self) -> dict:
        """What a grab records for the importer (the facts the search judged by)."""
        out = {"air_date": self.date, "absolute": self.absolute}
        return {k: v for k, v in out.items() if v}


# ── the Soulseek poll ────────────────────────────────────────────────────────
_REMEMBERED: dict = {}
_LOCK = threading.Lock()
_TTL = 15 * 60


def remember(search_id, body) -> None:
    if not search_id:
        return
    now = time.monotonic()
    with _LOCK:
        for key in [k for k, (at, _) in _REMEMBERED.items() if now - at > _TTL]:
            _REMEMBERED.pop(key, None)
        _REMEMBERED[str(search_id)] = (now, dict(body or {}))


def recall(search_id, fallback) -> Any:
    """The body a search started with, else the poll's own arguments."""
    with _LOCK:
        hit = _REMEMBERED.get(str(search_id or ""))
    return hit[1] if hit else dict(fallback or {})
