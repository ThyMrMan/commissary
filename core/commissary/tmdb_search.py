"""TMDB multi-search that reaches past page one (the old fork's 1.8.1).

Upstream asks /search/multi once, without a page, so only TMDB's first 20
results - popularity-ordered - are ever considered: a 2025 indie called
"Another World" is unreachable under the long-running soap of the same name,
however exactly it is typed. And /search/multi has no year parameter, so
"Another World 2025" sends the year as part of the TITLE and finds nothing.

* up to three pages, de-duplicated, stopping at total_pages; a later page
  that errors keeps what the earlier ones gave;
* a trailing year is stripped ONLY when the exact query found nothing - plenty
  of real titles end in a year ("Blade Runner 2049"), so stripping up front
  would bury the film under the 1982 original. The year then only RANKS that
  year's matches first; it never filters, so a wrong year can't empty results.
"""

from __future__ import annotations

import re
from typing import Any, Dict, List, Optional

from utils.logging_config import get_logger

logger = get_logger("commissary.tmdb_search")

PAGES = 3
_TRAILING_YEAR = re.compile(r"^(?P<title>.*\S)\s*\(?(?P<year>(?:19|20)\d{2})\)?\s*$")


def _year_of(it: Dict[str, Any]) -> Optional[str]:
    return str(it.get("release_date") or it.get("first_air_date") or "")[:4] or None


def _pages(client, query: str, pages: int = PAGES) -> List[Dict[str, Any]]:
    import requests
    out: List[Dict[str, Any]] = []
    seen = set()
    for page in range(1, pages + 1):
        try:
            r = requests.get(client.BASE + "/search/multi", params={
                "api_key": client.api_key, "query": query, "include_adult": "false",
                "page": page}, timeout=15)
            r.raise_for_status()
            data = r.json() or {}
        except Exception:  # noqa: BLE001
            if page == 1:
                raise              # the first page failing is upstream's error, as before
            logger.debug("TMDB search page %s failed for %r", page, query, exc_info=True)
            break
        for it in data.get("results") or []:
            key = (it.get("media_type"), it.get("id"))
            if key not in seen:
                seen.add(key)
                out.append(it)
        if page >= int(data.get("total_pages") or 1):
            break
    return out


def raw_results(client, query: str) -> List[Dict[str, Any]]:
    """The raw /search/multi results upstream's projection loop reads."""
    results = _pages(client, query)
    if results:
        return results
    m = _TRAILING_YEAR.match(str(query or "").strip())
    if not m:
        return results
    year = m.group("year")
    retry = _pages(client, m.group("title"))
    # ranks, never filters: that year's matches first, the rest in TMDB's order
    return sorted(retry, key=lambda it: 0 if _year_of(it) == year else 1)
