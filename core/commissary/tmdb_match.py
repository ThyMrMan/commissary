"""TMDB identity in descending order of certainty (the old fork's 1.9.11).

Upstream's TMDBClient.match, given no TMDB id, title-searches and takes the
FIRST result on trust - which is how 'Silo' was matched to an unrelated show,
silently and permanently (a wrong id is never revisited; 'not found' is).

  1. the server's own TMDB id (upstream already does this);
  2. another provider's id (TVDB, IMDb) the server gave us, resolved EXACTLY
     through /find - the media server routinely identifies a show by its TVDB
     guid and gives no TMDB id at all;
  3. only then a title search, which must PROVE itself: a result has to carry
     the title we searched for, or nothing is chosen. The year is tried first,
     then dropped - a wrong or episode-derived year excludes the real title and
     leaves only namesakes, the exact shape of the Silo mis-match.

Refusing is the point: 'not_found' is retried and shows in Metadata Gaps.
"""

from __future__ import annotations

from typing import Any, Dict, Optional, Tuple

from utils.logging_config import get_logger

logger = get_logger("commissary.tmdb_match")

_FIND_SOURCES = {"show": ("tvdb_id", "imdb_id"), "movie": ("imdb_id",)}


def external_ids_for(db, kind: str, row_id) -> Dict[str, Any]:
    """The TVDB/IMDb ids a library row already carries (from the server scan)."""
    table = "movies" if kind == "movie" else "shows"
    cols = "imdb_id" if kind == "movie" else "tvdb_id, imdb_id"
    try:
        conn = db._get_connection()
        try:
            row = conn.execute(f"SELECT {cols} FROM {table} WHERE id=?", (int(row_id),)).fetchone()
        finally:
            conn.close()
    except Exception:  # noqa: BLE001
        return {}
    if not row:
        return {}
    keys = ("imdb_id",) if kind == "movie" else ("tvdb_id", "imdb_id")
    return {k: v for k, v in zip(keys, tuple(row), strict=True) if v not in (None, "")}


def find_by_external_id(client, kind, external_ids) -> Tuple[Optional[int], Optional[str]]:
    import requests
    ids = external_ids if isinstance(external_ids, dict) else {}
    if not client.api_key or not ids:
        return None, None
    key = "movie_results" if kind == "movie" else "tv_results"
    for source in _FIND_SOURCES.get("movie" if kind == "movie" else "show", ()):
        val = str(ids.get(source) or "").strip()
        if not val:
            continue
        r = requests.get(client.BASE + "/find/" + val,
                         params={"api_key": client.api_key, "external_source": source}, timeout=15)
        if r.status_code == 404:
            continue                  # TMDB doesn't carry that id - try the next
        r.raise_for_status()          # any other failure propagates: 'error', retried
        for hit in (r.json() or {}).get(key) or []:
            if hit.get("id") is not None:
                return hit["id"], hit.get("overview")
    return None, None


def _names(r) -> list:
    return [n for n in (r.get("title"), r.get("name"), r.get("original_title"),
                        r.get("original_name")) if n]


def search_verified(client, kind, title, year) -> Tuple[Optional[int], Optional[str]]:
    import requests
    from core.video.release_parse import normalize_title
    want = normalize_title(title)
    if not want:
        return None, None
    path = "/search/movie" if kind == "movie" else "/search/tv"
    year_key = "year" if kind == "movie" else "first_air_date_year"
    seen: list = []
    for extra in ([{year_key: year}] if year else []) + [{}]:
        resp = requests.get(client.BASE + path,
                            params={"api_key": client.api_key, "query": title, **extra}, timeout=15)
        resp.raise_for_status()
        for r in (resp.json() or {}).get("results") or []:
            if r.get("id") is None:
                continue
            if any(normalize_title(n) == want for n in _names(r)):
                return r["id"], r.get("overview")
            if len(seen) < 5:
                seen.extend(n for n in _names(r) if n not in seen)
    logger.info("TMDB: no result named %r (%s) - refusing to guess; saw: %s",
                title, kind, ", ".join(repr(s) for s in seen[:5]) or "nothing")
    return None, None


def resolve(client, kind, title, year, external_ids=None) -> Tuple[Optional[int], Optional[str]]:
    """Steps 2 and 3, for a row without a TMDB id."""
    tmdb_id, overview = find_by_external_id(client, kind, external_ids)
    if tmdb_id is not None:
        return tmdb_id, overview
    if not title:
        return None, None
    return search_verified(client, kind, title, year)
