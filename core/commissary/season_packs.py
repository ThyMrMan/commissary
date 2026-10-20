"""Season packs: an "only" mode, packs from the RSS feed, one claimant per file.

Upstream already reaches for a season pack before searching a finished
season's episodes one by one, with a per-show auto/prefer/never override.
This adds what the old fork had on top of it:

* ``only`` - wait for a pack. When the pack search finds nothing, the
  season's episodes are held for a later tick instead of grabbed singly. It
  binds only on a season that has PROVABLY finished airing: one still going out
  weekly has no complete pack to find, and pack-or-nothing there would quietly
  stop the show arriving. Unknown counts as still airing.
* RSS sync sees packs: the feed is matched with the drain's own grouping and
  ranker, so a ``Show.S01`` pack posted to an indexer is grabbed within
  minutes, like a single episode is. A pack missing from the feed proves
  nothing (the feed is the last few hundred releases, not a search), so
  nothing is held or failed there except an ``only`` show's finished season.
* While a season pack is in flight, its episodes are not grabbed on their own:
  upstream's in-flight check only compared each episode's own key.
"""

from __future__ import annotations

import datetime as _dt
import sqlite3
from typing import Any, Dict, Iterable, Iterator, List, Optional

from utils.logging_config import get_logger

logger = get_logger("commissary.season_packs")

ONLY = "only"
HOLD_NOTE = "Waiting for a season pack (Season packs: only)"


def preference(item: Dict[str, Any]) -> str:
    from core.automation.handlers import video_process_wishlist as vpw
    return vpw._pack_preference_for(item)


def wants_pack(pref: str, pack_on: bool) -> bool:
    """Whether a show's episodes go to the pack try: its own prefer/only always
    does, 'never' never does, 'auto' follows the global switch."""
    return pref in ("prefer", ONLY) or (pack_on and pref != "never")


# ── is the season over? ──────────────────────────────────────────────────────

def _dates(db, show_tmdb_id, season) -> Optional[List[str]]:
    """Every air date known for the season (wished rows and library episodes),
    or None when any of them is undated."""
    try:
        conn = db._get_connection()
    except Exception:  # noqa: BLE001
        return None
    try:
        rows = conn.execute(
            "SELECT air_date FROM video_wishlist WHERE kind='episode' AND tmdb_id=? "
            "AND season_number=? "
            "UNION ALL SELECT e.air_date FROM episodes e JOIN shows s ON s.id = e.show_id "
            "WHERE s.tmdb_id=? AND e.season_number=?",
            (int(show_tmdb_id), int(season), int(show_tmdb_id), int(season))).fetchall()
    except (sqlite3.Error, TypeError, ValueError):
        return None
    finally:
        conn.close()
    out = []
    for (value,) in rows:
        text = str(value or "")[:10]
        if len(text) != 10:
            return None
        out.append(text)
    return out


def season_finished(db, rows: List[Dict[str, Any]], today: Optional[str] = None) -> bool:
    """True only when the season provably has nothing left to air: every
    episode known for it is dated, and the newest is past the quiet window."""
    from core.automation.handlers import video_process_wishlist as vpw
    if not rows:
        return False
    first = rows[0]
    dates = _dates(db, first.get("show_tmdb_id"), first.get("season_number"))
    if not dates:
        return False
    for r in rows:
        if len(str(r.get("air_date") or "")[:10]) != 10:
            return False
    today_d = _dt.date.fromisoformat(today) if today else _dt.date.today()
    try:
        newest = _dt.date.fromisoformat(max(dates))
    except ValueError:
        return False
    return newest <= today_d - _dt.timedelta(days=vpw.SEASON_PACK_QUIET_DAYS)


def hold(db, pack_item: Dict[str, Any], today: Optional[str] = None) -> bool:
    """A pack try that found nothing: hold its episodes instead of searching
    them one by one?"""
    rows = pack_item.get("_pack_rows") or []
    return bool(rows) and preference(pack_item) == ONLY and season_finished(db, rows, today)


# ── one claimant per file ────────────────────────────────────────────────────

def under_active_pack(item: Dict[str, Any], media_type: str, active: Iterable) -> bool:
    """An episode whose season pack is already downloading."""
    if media_type == "movie" or item.get("episode_number") is None:
        return False
    return ("season", str(item.get("show_tmdb_id")),
            int(item.get("season_number") or 0)) in set(active or ())


# ── RSS ─────────────────────────────────────────────────────────────────────

def rss_items(items: List[Dict[str, Any]], active: set, media_type: str,
              db=None, pack_on: bool = True, min_missing: Optional[int] = None,
              today: Optional[str] = None) -> Iterator[Dict[str, Any]]:
    """What RSS should try to match, in order: season packs first, then the
    single episodes no grabbed pack covers.

    A generator over the caller's live ``active`` set: after a pack is yielded
    the caller grabs it (adding its season key) or doesn't, and only then are
    its episodes released - or held, for a finished 'only' season."""
    from core.automation.handlers import video_process_wishlist as vpw
    if media_type == "movie":
        yield from items
        return
    try:
        packable = [it for it in items if wants_pack(preference(it), pack_on)]
        packs = vpw.season_pack_requests(packable, active,
                                         min_missing or vpw.MIN_SEASON_PACK_EPISODES, today=today)
    except Exception:  # noqa: BLE001 - packs are an extra; singles still run
        logger.exception("RSS season-pack grouping failed")
        packs = []
    in_pack = set()
    for pack in packs:
        yield pack
        rows = pack.get("_pack_rows") or []
        if vpw.item_key(pack, "episode") in active:
            in_pack.update(id(r) for r in rows)        # the pack covers them
        elif db is not None and hold(db, pack, today):
            in_pack.update(id(r) for r in rows)        # wait for a pack
    for it in items:
        if id(it) in in_pack or under_active_pack(it, media_type, active):
            continue
        yield it
