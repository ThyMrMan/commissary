"""Which metadata provider owns a show's EPISODE NUMBERING.

Episodes are keyed ``UNIQUE(show_id, season_number, episode_number)`` and the
numbers come from the media server. Filling in missing episodes from a provider
that splits the show into different seasons writes rows into seasons they don't
belong to, and nothing downstream can undo that, because the numbers are the key.

Bleach is the case that forced this. TMDB has it as three seasons (specials, the
366-episode 2004-2012 run, then Thousand-Year Blood War); TVDB has seventeen,
which is what Plex reports. Upstream cascades TMDB's season numbers into the
library, and hands the same numbers to TVDB, which may also insert - so TMDB's
"season 2" (2022-2026) lands on top of the 2005 arc, and season 17, where the
library keeps that run, can never fill because TMDB has no season 17.

So the provider is chosen per show, by comparing each one's season structure
against what the server reports (or set by hand in Manage), and only that
provider may CREATE episodes. The other one may only fill in episodes that
already exist.
"""

from __future__ import annotations

import sqlite3
from typing import Any, Dict, List, Optional

from utils.logging_config import get_logger

logger = get_logger("commissary.episode_numbering")

SOURCES = ("auto", "tmdb", "tvdb")

# A show whose structure can't be told apart keeps upstream's behaviour (TMDB),
# so this never rearranges a library that was fine.
DEFAULT_SOURCE = "tmdb"

# How much better the challenger must score before displacing the default: the
# point is a WHOLESALE disagreement (3 seasons vs 17), not one missing special.
MIN_MARGIN = 0.25

COLUMNS = [("shows", "episode_source", "TEXT")]   # NULL = detect


def ensure_columns(conn) -> None:
    for table, col, coltype in COLUMNS:
        cols = {r[1] for r in conn.execute(f"PRAGMA table_info({table})").fetchall()}
        if cols and col not in cols:
            try:
                conn.execute(f"ALTER TABLE {table} ADD COLUMN {col} {coltype}")
            except sqlite3.OperationalError as e:
                if "duplicate column" not in str(e).lower():
                    raise


# ── the decision (pure) ─────────────────────────────────────────────────────

def _regular(seasons) -> set:
    """Season numbers, specials dropped: season 0's contents differ between
    providers for reasons that say nothing about the real seasons."""
    out = set()
    for s in seasons or []:
        try:
            n = int(s)
        except (TypeError, ValueError):
            continue
        if n > 0:
            out.add(n)
    return out


def structure_score(server_seasons, provider_seasons) -> float:
    """The fraction of the server's real seasons the provider also has.
    Asymmetric on purpose: a provider knowing seasons you don't own yet is
    normal; one MISSING seasons you do own has nowhere correct to put them."""
    have = _regular(server_seasons)
    if not have:
        return 0.0
    return len(have & _regular(provider_seasons)) / len(have)


def choose_source(server_seasons, tmdb_seasons, tvdb_seasons, override=None) -> str:
    """'tmdb' | 'tvdb'. An explicit override is obeyed even when it scores
    badly - it exists for when this heuristic is wrong."""
    if override in ("tmdb", "tvdb"):
        return override
    if not _regular(tvdb_seasons):
        return DEFAULT_SOURCE
    tmdb = structure_score(server_seasons, tmdb_seasons)
    tvdb = structure_score(server_seasons, tvdb_seasons)
    if tvdb - tmdb >= MIN_MARGIN:
        return "tvdb"
    return DEFAULT_SOURCE


def explain(server_seasons, tmdb_seasons, tvdb_seasons, override=None) -> dict:
    """The decision and the numbers behind it, for Manage and the log."""
    have = _regular(server_seasons)
    return {
        "source": choose_source(server_seasons, tmdb_seasons, tvdb_seasons, override),
        "override": override if override in ("tmdb", "tvdb") else None,
        "tmdb_score": round(structure_score(server_seasons, tmdb_seasons), 3),
        "tvdb_score": round(structure_score(server_seasons, tvdb_seasons), 3),
        "server_seasons": sorted(have),
        "missing_from_tmdb": sorted(have - _regular(tmdb_seasons)),
        "missing_from_tvdb": sorted(have - _regular(tvdb_seasons)),
    }


# ── the database ────────────────────────────────────────────────────────────

def _q(db, sql, params=()) -> list:
    try:
        conn = db._get_connection()
    except Exception:  # noqa: BLE001 - a stub DB has nothing
        return []
    try:
        return conn.execute(sql, params).fetchall()
    except (sqlite3.Error, TypeError, ValueError):
        return []
    finally:
        conn.close()


def override(db, show_id) -> Optional[str]:
    rows = _q(db, "SELECT episode_source FROM shows WHERE id=?", (int(show_id),))
    v = str(rows[0][0] or "").strip().lower() if rows else ""
    return v if v in ("tmdb", "tvdb") else None


def set_override(db, show_id, value) -> bool:
    val = str(value or "auto").strip().lower()
    if val not in SOURCES:
        return False
    conn = db._get_connection()
    try:
        cur = conn.execute("UPDATE shows SET episode_source=? WHERE id=?",
                           (None if val == "auto" else val, int(show_id)))
        conn.commit()
        return cur.rowcount > 0
    except sqlite3.Error:
        logger.exception("saving episode numbering for show %s failed", show_id)
        return False
    finally:
        conn.close()


def server_season_numbers(db, show_id) -> List[int]:
    """Seasons the MEDIA SERVER reports - not every season row, which would
    include ones a mis-numbered fill invented and score a provider against its
    own mistakes."""
    return [r[0] for r in _q(db, "SELECT DISTINCT season_number FROM episodes "
                                 "WHERE show_id=? AND server_id IS NOT NULL ORDER BY 1",
                                 (int(show_id),))]


def uses_tvdb(db, show_tmdb_id) -> bool:
    """Whether a show (by TMDB id) is set to TVDB numbering. Only an explicit
    choice counts here: detecting it costs provider calls."""
    if not show_tmdb_id:
        return False
    try:
        rows = _q(db, "SELECT 1 FROM shows WHERE tmdb_id=? AND episode_source='tvdb' LIMIT 1",
                  (int(show_tmdb_id),))
    except (TypeError, ValueError):
        return False
    return bool(rows)


def fill_existing(db, show_id, season_number, episodes) -> int:
    """Upstream's backfill, minus the inserts: fill blanks on episodes that
    already exist and create nothing - what a provider that does NOT own the
    numbering may still do (TVDB often has a just-aired synopsis first)."""
    conn = db._get_connection()
    touched = 0
    try:
        for e in episodes or []:
            en = e.get("episode_number")
            if en is None:
                continue
            sets, params = [], []
            for col in ("title", "still_url", "overview", "air_date", "rating", "runtime_minutes"):
                if e.get(col) is None:
                    continue
                sets.append(f"{col}=COALESCE(NULLIF({col}, ''), ?)")
                params.append(e[col])
            if sets:
                cur = conn.execute(f"UPDATE episodes SET {', '.join(sets)} "
                                   "WHERE show_id=? AND season_number=? AND episode_number=?",
                                   params + [int(show_id), int(season_number), int(en)])
                touched += cur.rowcount
        conn.commit()
        return touched
    finally:
        conn.close()


def episode_count(db, show_id) -> int:
    rows = _q(db, "SELECT COUNT(*) FROM episodes WHERE show_id=?", (int(show_id),))
    return int(rows[0][0]) if rows else 0


_UNLISTED_SQL = (
    "SELECT e.id, e.season_number, e.episode_number, e.title, e.air_date "
    "FROM episodes e WHERE e.show_id=? AND e.season_number=? "
    "AND e.server_id IS NULL AND COALESCE(e.has_file, 0)=0 "
    "AND NOT EXISTS (SELECT 1 FROM media_files f WHERE f.episode_id = e.id) "
    "ORDER BY e.episode_number")


def unlisted_rows(db, show_id, season_number, keep_numbers) -> List[Dict[str, Any]]:
    """Server-less, file-less rows in a season the owning provider doesn't
    list: episodes nothing can match. An empty ``keep_numbers`` (a failed
    lookup) returns nothing, never 'everything missing'."""
    keep = {int(n) for n in keep_numbers or [] if str(n).lstrip("-").isdigit()}
    if not keep:
        return []
    cols = ("id", "season_number", "episode_number", "title", "air_date")
    return [dict(zip(cols, r, strict=True)) for r in _q(db, _UNLISTED_SQL, (int(show_id), int(season_number)))
            if r[2] not in keep]


def delete_unlisted(db, show_id, season_number, keep_numbers) -> int:
    """Delete exactly what ``unlisted_rows`` reports, re-derived here: a scan
    landing after the preview must not let a stale list remove a real episode."""
    rows = unlisted_rows(db, show_id, season_number, keep_numbers)
    if not rows:
        return 0
    conn = db._get_connection()
    try:
        conn.execute("PRAGMA foreign_keys=ON")
        cur = conn.execute("DELETE FROM episodes WHERE id IN (%s)" % ",".join("?" * len(rows)),
                           [r["id"] for r in rows])
        conn.commit()
        return cur.rowcount
    except sqlite3.Error:
        logger.exception("removing out-of-place episodes for show %s failed", show_id)
        return 0
    finally:
        conn.close()


# ── TVDB ────────────────────────────────────────────────────────────────────

def tvdb_season_numbers(client, series_id) -> List[int]:
    """TVDB's aired-order season list. [] on any error, which leaves TVDB out
    of the comparison."""
    if client is None or not getattr(client, "api_key", None) or series_id is None:
        return []
    try:
        d = client._authed_get("/series/" + str(series_id) + "/extended", {}) or {}
    except Exception:  # noqa: BLE001
        logger.debug("TVDB season list failed for %s", series_id, exc_info=True)
        return []
    out = set()
    for s in ((d.get("data") or {}).get("seasons")) or []:
        # TVDB carries several orderings (aired / dvd / absolute); only aired
        # ("official") is comparable with what a media server reports
        stype = str(((s.get("type") or {}).get("type")) or "").lower()
        if stype and stype != "official":
            continue
        if isinstance(s.get("number"), int):
            out.add(s["number"])
    return sorted(out)


def tvdb_season_episodes(client, series_id, season_number) -> List[Dict[str, Any]]:
    """A TVDB season's episodes, EVERY page. /episodes/default is the whole
    series in pages and its ``season`` filter isn't reliably applied, so reading
    only page 0 left a long show's later seasons empty - indistinguishable from
    'no episodes', which makes every caller quietly do nothing."""
    if client is None or not getattr(client, "api_key", None) or series_id is None:
        return []
    try:
        sn = int(season_number)
    except (TypeError, ValueError):
        return []
    out, seen = [], set()
    for page in range(0, 20):           # a hard cap: never spin on a bad endpoint
        try:
            d = client._authed_get("/series/" + str(series_id) + "/episodes/default",
                                   {"season": sn, "page": page}) or {}
        except Exception:  # noqa: BLE001 - keep what earlier pages gave
            logger.debug("TVDB season episodes failed for %s S%s p%s", series_id, sn, page,
                         exc_info=True)
            break
        batch = ((d.get("data") or {}).get("episodes")) or []
        if not batch:
            break
        for e in batch:
            num = e.get("number")
            if e.get("seasonNumber") != sn or num is None or num in seen:
                continue
            seen.add(num)
            out.append({"episode_number": num, "title": e.get("name") or None,
                        "overview": e.get("overview") or None, "air_date": e.get("aired") or None,
                        "runtime_minutes": e.get("runtime") or None,
                        "still_url": e.get("image") or None})
        links = d.get("links")
        if isinstance(links, dict) and "next" in links:
            if not links.get("next"):
                break
        elif len(batch) < 100:          # no paging info: a short page is the last
            break
    return out


# ── the engine side ─────────────────────────────────────────────────────────

def _tvdb_client(engine):
    w = (getattr(engine, "workers", None) or {}).get("tvdb")
    return w.client if w is not None and getattr(w, "enabled", False) else None


def _tmdb_season_numbers(engine, info) -> List[int]:
    w = (getattr(engine, "workers", None) or {}).get("tmdb")
    if w is None or not getattr(w, "enabled", False):
        return []
    res = w.client.match("show", info.get("title"), info.get("year"),
                         known_id=info.get("tmdb_id")) or {}
    return [s.get("season_number") for s in ((res.get("metadata") or {}).get("seasons") or [])
            if s.get("season_number") is not None]


def decide(db, show_id, tvdb_client, tmdb_season_nums=None, tmdb_lookup=None,
           info=None) -> str:
    """'tmdb' | 'tvdb' for one show. The per-show choice wins; otherwise both
    providers' seasons are compared with the server's. ``tmdb_lookup(info)``
    supplies TMDB's seasons when the caller doesn't have them. Any failure
    keeps upstream's behaviour (TMDB)."""
    try:
        ov = override(db, show_id)
        if ov:
            return ov
        info = info or db.show_match_info(show_id) or {}
        tvdb_nums = tvdb_season_numbers(tvdb_client, info.get("tvdb_id"))
        if not tvdb_nums:
            return DEFAULT_SOURCE
        if tmdb_season_nums is None:
            tmdb_season_nums = tmdb_lookup(info) if tmdb_lookup else []
        if not tmdb_season_nums:
            return DEFAULT_SOURCE   # nothing to compare against: never switch blind
        server = server_season_numbers(db, show_id)
        chosen = choose_source(server, tmdb_season_nums, tvdb_nums)
        if chosen != DEFAULT_SOURCE:
            logger.info("episode numbering for show %s: %s", show_id,
                        explain(server, tmdb_season_nums, tvdb_nums))
        return chosen
    except Exception:  # noqa: BLE001 - a probe must never break a refresh
        logger.debug("episode numbering check failed for show %s", show_id, exc_info=True)
        return DEFAULT_SOURCE


def source_for(engine, show_id, tmdb_season_nums=None, info=None) -> str:
    return decide(engine.db, show_id, _tvdb_client(engine), tmdb_season_nums,
                  lambda i: _tmdb_season_numbers(engine, i), info)


def _running_engine():
    """The engine the workers belong to, if one exists. Never CREATES one:
    get_video_enrichment_engine() would build and start a whole engine (in a
    test, against the real database) just to answer a question."""
    try:
        from core.video.enrichment import engine as _engine_mod
        return getattr(_engine_mod, "_engine", None)
    except Exception:  # noqa: BLE001
        return None


def owns(worker, show_id, season_nums) -> bool:
    """May this enrichment worker CREATE episodes for the show? Its unattended
    cascades would otherwise re-create exactly the rows the out-of-place check
    removes. Decided on the worker's own database; the other provider is
    borrowed from the engine. Fails open only for TMDB (upstream's writer)."""
    service = getattr(worker, "service", None)
    try:
        db = worker.db
        if service == "tvdb":
            eng = _running_engine()
            tvdb_client, tmdb_nums = worker.client, None
            lookup = (lambda i: _tmdb_season_numbers(eng, i)) if eng else None  # noqa: E731
        else:
            eng = _running_engine()
            tmdb_nums, lookup = season_nums, None
            tvdb_client = _tvdb_client(eng) if eng else None
        src = decide(db, show_id, tvdb_client, tmdb_nums, lookup)
    except Exception:  # noqa: BLE001
        return service == DEFAULT_SOURCE
    return src == service


def cascade(engine, show_id, info, tmdb_id, tmdb_nums, recent_only: bool = False,
            latest=None) -> str:
    """refresh_show_art's episode step, by the owning provider. Returns it.

    TMDB owns: upstream's TMDB cascade, then TVDB fills in only what exists.
    TVDB owns: TVDB supplies the list over ITS OWN season numbers (the only
    way a season TMDB lacks can fill); TMDB writes nothing."""
    source = source_for(engine, show_id, tmdb_nums, info)
    w = engine.workers.get("tmdb")
    tvdb = _tvdb_client(engine)
    tvdb_id = (info or {}).get("tvdb_id")
    if source == "tmdb":
        nums = latest(tmdb_nums) if (recent_only and latest) else tmdb_nums
        if w is not None:
            w._cascade_episodes(show_id, tmdb_id, nums, mark_synced=not recent_only)
        if tvdb is not None and tvdb_id:
            for sn in nums or []:
                eps = tvdb_season_episodes(tvdb, tvdb_id, sn)
                if eps:
                    fill_existing(engine.db, show_id, sn, eps)
        return source
    nums = tvdb_season_numbers(tvdb, tvdb_id) if tvdb is not None else []
    if recent_only and latest:
        nums = latest(nums)
    for sn in nums:
        eps = tvdb_season_episodes(tvdb, tvdb_id, sn)
        if eps:
            # authoritative: a rescheduled episode's date moves, like TMDB's does
            engine.db.backfill_episodes(show_id, sn, eps, overwrite_air_date=True)
    if nums and not recent_only:
        try:
            engine.db.mark_episodes_synced(show_id)
        except Exception:  # noqa: BLE001
            logger.debug("marking show %s synced failed", show_id, exc_info=True)
    return source


def authoritative_numbers(engine, show_id) -> Dict[str, Any]:
    """{source, seasons: {season: [episode numbers]}} from the owning
    provider, for the out-of-place check. A season the provider returned
    nothing for is left out, so it is never treated as 'all wrong'."""
    db = engine.db
    info = db.show_match_info(show_id) or {}
    tmdb_nums = _tmdb_season_numbers(engine, info)
    source = source_for(engine, show_id, tmdb_nums, info)
    seasons: Dict[int, List[int]] = {}
    if source == "tvdb":
        tvdb = _tvdb_client(engine)
        for sn in tvdb_season_numbers(tvdb, info.get("tvdb_id")):
            nums = [e["episode_number"] for e in tvdb_season_episodes(tvdb, info.get("tvdb_id"), sn)]
            if nums:
                seasons[int(sn)] = nums
    else:
        w = engine.workers.get("tmdb")
        for sn in tmdb_nums:
            data = w.client.season_episodes(info.get("tmdb_id"), sn) or {}
            nums = [e.get("episode_number") for e in data.get("episodes") or []
                    if e.get("episode_number") is not None]
            if nums:
                seasons[int(sn)] = nums
    return {"source": source, "seasons": seasons}


def out_of_place(engine, show_id, remove: bool = False) -> Dict[str, Any]:
    """Episodes listed under a season number the owning provider doesn't give
    them: preview, or remove. Nothing on disk is touched; only rows with no
    server id, no file and no media file are ever candidates."""
    auth = authoritative_numbers(engine, show_id)
    found, removed = [], 0
    for sn, keep in sorted(auth["seasons"].items()):
        rows = unlisted_rows(engine.db, show_id, sn, keep)
        if not rows:
            continue
        found.extend(rows)
        if remove:
            removed += delete_unlisted(engine.db, show_id, sn, keep)
    return {"source": auth["source"], "episodes": found, "removed": removed}
