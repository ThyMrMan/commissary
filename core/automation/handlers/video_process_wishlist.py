"""Automation handlers: ``video_process_movie_wishlist`` / ``video_process_episode_wishlist``.

The Soulseek counterpart of the YouTube wishlist drain — the piece that finally makes the
people/airing scans pay off. For wished, RELEASED movies (and aired episodes) it searches
Soulseek, picks the best release per the quality profile, and enqueues the download; the
existing ``download_monitor`` finishes + organises + archives it, exactly like a manual grab.

Shape mirrors the YouTube drain (Boulder: "same standard"): it processes the WHOLE eligible
wishlist (no total cap), but the slow part — each item needs a ~20s blocking Soulseek search
— runs only a FEW at a time (``max_concurrent``). A ``guard`` keeps the next hourly tick from
overlapping a run that's still working, so it can't pile up.

Movies are gated on ``status='wanted'`` (released; skips 'monitored'/unreleased). Episodes
are all-wished (the airing scan only adds aired ones). Items already downloading are skipped
so re-runs never double-grab. The pick is the top ACCEPTED release — the ranker already
encodes the quality profile's accept/reject/score, so no extra rules here.

Shared automation side (may import ``core.video`` / ``api.video``); owns its own progress.
The search + enqueue are injected seams, so selection/pick/record are pure + unit-tested.
"""

from __future__ import annotations

import json
import threading
from concurrent.futures import ThreadPoolExecutor
from typing import Any, Callable, Dict, Iterable, List, Optional

from core.automation.deps import AutomationDeps
from utils.logging_config import get_logger

logger = get_logger("automation.video_process_wishlist")


# ── pure helpers ──────────────────────────────────────────────────────────────
def pick_best(candidates: List[Dict[str, Any]], min_rank: int = 0) -> Optional[Dict[str, Any]]:
    """The best ACCEPTED release from a ranked candidate list. ``_evaluate_hits`` already
    sorts best-first (accepted, score, availability), so the first accepted is the pick.

    ``min_rank`` > 0 = an UPGRADE pick for an owned item: only releases with a
    resolution STRICTLY better than the current copy qualify (a same-quality
    re-grab would just import_fail as 'not an upgrade' — the old re-download
    loop). Unknown-resolution releases can't prove they're better, so they
    don't qualify either."""
    from core.video.quality_eval import resolution_rank
    for c in candidates or []:
        if not c.get("accepted"):
            continue
        if min_rank and resolution_rank(c.get("resolution")) <= min_rank:
            continue
        return c
    return None


def annotate_upgrades(items: List[Dict[str, Any]], cutoff_rank: int,
                      cutoff_for: Optional[Callable[[Dict[str, Any]], int]] = None) -> List[Dict[str, Any]]:
    """Upgrade-until-cutoff eligibility over the wishlist rows (pure).

    Unowned items pass through untouched. Owned items (the queries annotate
    ``owned`` + ``owned_resolutions``) are judged against the cutoff:
      · already meet it       → skipped (their row should be gone; the Wishlist
                                Audit job sweeps stragglers)
      · below it              → kept, carrying ``_min_rank`` = the current
                                copy's rank so only strictly-better wins
      · resolution unreadable → skipped (can't prove an upgrade; the audit job
                                surfaces these)
    An empty cutoff ('always chase the best') means owned items are never
    'done' — they stay upgrade-eligible forever.

    ``cutoff_for`` (P2, per-title profiles): when given, each owned item is
    judged against ITS OWN profile's cutoff instead of the global
    ``cutoff_rank``. Still pure — the callable is injected."""
    from core.video.quality_eval import resolution_rank
    out = []
    for it in items or []:
        if not it.get("owned"):
            out.append(it)
            continue
        rks = [resolution_rank(r) for r in str(it.get("owned_resolutions") or "").split(",")
               if r.strip()]
        cur = max(rks, default=0)
        if cur == 0:
            continue
        eff_cutoff = cutoff_for(it) if cutoff_for is not None else cutoff_rank
        if eff_cutoff and cur >= eff_cutoff:
            continue
        it = dict(it)
        it["_min_rank"] = cur
        out.append(it)
    return out


def _cutoff_rank_for_item(item: Dict[str, Any]) -> int:
    """The cutoff rank under the item's OWN profile (per-title, P2)."""
    from api.video import get_video_db
    from core.video.quality_eval import resolution_rank
    from core.video.quality_profile import load_for_item
    return resolution_rank((load_for_item(get_video_db(), item) or {}).get("cutoff_resolution"))


def _default_cutoff_rank() -> int:
    """The profile cutoff as a resolution rank (0 = no cutoff set)."""
    from api.video import get_video_db
    from core.video.quality_eval import resolution_rank
    from core.video.quality_profile import load as load_profile
    return resolution_rank((load_profile(get_video_db()) or {}).get("cutoff_resolution"))


def item_key(item: Dict[str, Any], media_type: str) -> tuple:
    """Stable identity for de-duping a wished item against active downloads."""
    if media_type == "movie":
        return ("movie", str(item.get("tmdb_id")))
    return ("episode", str(item.get("show_tmdb_id")),
            int(item.get("season_number") or 0), int(item.get("episode_number") or 0))


def season_key(item: Dict[str, Any], media_type: str):
    """The season an episode belongs to, or None for a movie. A season PACK in
    flight covers every episode in it, so the drain checks this alongside
    item_key — otherwise the next tick would grab each episode individually
    while the pack carrying them is still downloading."""
    if media_type == "movie":
        return None
    return ("season", str(item.get("show_tmdb_id")), int(item.get("season_number") or 0))


def active_download_keys(active: Iterable[Dict[str, Any]]) -> set:
    """Identity keys for the movie/episode downloads already in flight, so we don't
    re-grab them. Episodes read season/episode out of the row's ``search_ctx``."""
    keys = set()
    for d in active or []:
        kind = str(d.get("kind") or "").lower()
        if kind == "movie":
            keys.add(("movie", str(d.get("media_id"))))
            continue
        ctx = d.get("search_ctx")
        if isinstance(ctx, str):
            try:
                ctx = json.loads(ctx)
            except (ValueError, TypeError):
                ctx = {}
        ctx = ctx if isinstance(ctx, dict) else {}
        scope = str(ctx.get("scope") or "").lower()
        # Keyed off the CONTEXT's scope, not the row's kind: the interactive grab
        # stores kind='season' for a pack while the drain's own packs are
        # kind='episode', and a kind-only test would miss one of them and let the
        # drain re-grab every episode of a season already downloading.
        if scope in ("season", "series"):
            keys.add(("season", str(d.get("media_id")), int(ctx.get("season") or 0)))
        elif kind == "episode" or scope == "episode":
            keys.add(("episode", str(d.get("media_id")),
                      int(ctx.get("season") or 0), int(ctx.get("episode") or 0)))
    return keys


SEASON_PACK_MIN_EPISODES = 4      # video.season_pack_min_episodes


def season_pack_groups(items: List[Dict[str, Any]], *,
                       min_episodes: int = SEASON_PACK_MIN_EPISODES,
                       mode_for=None) -> List[Dict[str, Any]]:
    """Seasons with enough wanted episodes to be worth one pack instead of N grabs.

    Returns pseudo-items flagged ``_season_pack``, each a copy of a REPRESENTATIVE
    member so all its routing (Library, category, quality profile, series type)
    is the season's own — only the scope differs. ``_pack_members`` carries the
    item keys the pack would satisfy, so the caller can drop them from the
    per-episode pass.

    Deliberately conservative about what counts:
      • UPGRADES are excluded (``_min_rank``). Wanting a better copy of two
        episodes must not pull a whole season; the per-episode upgrade path
        already handles those, and a pack would mostly re-download what you have.
      • a season needs ``min_episodes`` genuinely-missing episodes. Below that a
        pack is usually more bytes than the episodes are worth, and packs are
        rarer than singles so the search often comes back empty anyway.
      • season 0 (specials) never packs — 'S00' is not a thing releases ship.
      • a show whose effective mode is 'never' is skipped entirely.

    ``mode_for(item) -> 'prefer'|'only'|'never'`` supplies the per-show
    decision. Passed IN rather than looked up here so this stays pure and
    unit-testable; the caller owns the config and DB reads.
    """
    try:
        min_episodes = max(2, int(min_episodes))
    except (TypeError, ValueError):
        min_episodes = SEASON_PACK_MIN_EPISODES
    groups: Dict[tuple, List[Dict[str, Any]]] = {}
    for it in items or []:
        if it.get("_min_rank"):          # an upgrade, not a hole
            continue
        try:
            season = int(it.get("season_number") or 0)
        except (TypeError, ValueError):
            continue
        if season <= 0:
            continue
        if mode_for is not None and mode_for(it) == "never":
            continue
        groups.setdefault((str(it.get("show_tmdb_id")), season), []).append(it)
    out = []
    for (_show, _season), members in groups.items():
        if len(members) < min_episodes:
            continue
        rep = dict(members[0])
        rep["_season_pack"] = True
        rep["_pack_members"] = [item_key(m, "episode") for m in members]
        rep["_pack_size"] = len(members)
        rep["_pack_mode"] = mode_for(rep) if mode_for is not None else "prefer"
        rep.pop("_min_rank", None)
        out.append(rep)
    return out


def _season_pack_settings() -> dict:
    """The global season-pack settings, through the ONE normalizer that also
    serves the settings page (core.video.download_config).

    Season packs are OFF by default. One pack can be tens of GB and the drain
    runs unattended, so an existing install must not start spending disk on this
    because it updated — the operator turns it on."""
    try:
        from core.video.download_config import season_pack_settings
        return season_pack_settings()
    except Exception:      # noqa: BLE001 - unreadable config behaves like off
        logger.debug("season pack settings unreadable; treating as off", exc_info=True)
        return {"season_packs": False, "season_pack_min_episodes": SEASON_PACK_MIN_EPISODES,
                "season_pack_mode": "prefer"}


def _pack_mode_resolver(settings: Dict[str, Any]):
    """``item -> 'prefer' | 'only' | 'never'``, memoised per show.

    A per-show override BEATS the global in both directions: a show set to
    'only' or 'prefer' packs even with the global switch off, and a show set to
    'never' stays per-episode even with it on. Without that the override would
    only ever subtract, and "always get this one as packs" — the case someone
    actually opens the panel for — would be unexpressible.
    """
    default = settings["season_pack_mode"] if settings["season_packs"] else "never"
    try:
        from api.video import get_video_db
        overrides = get_video_db().all_season_pack_overrides()
    except Exception:       # noqa: BLE001 - no overrides readable → everyone follows the global
        logger.debug("season pack overrides unreadable; using the global for every show",
                     exc_info=True)
        overrides = {}

    def resolve(item):
        try:
            mode = overrides.get(int(item.get("show_tmdb_id")))
        except (TypeError, ValueError):
            mode = None
        return mode if mode in ("prefer", "only", "never") else default

    return resolve


def _season_has_finished_airing(item) -> bool:
    """Whether a complete pack for this season could exist yet.

    'Packs only' is a preference, not a licence to stop acquiring a show. A
    season still airing has no full pack to find, so holding its episodes back
    pack-or-nothing would skip them week after week — the show would simply
    stop arriving, with nothing in the UI saying why. Not-provably-finished
    counts as still airing."""
    try:
        from api.video import get_video_db
        return get_video_db().season_fully_aired(
            item.get("show_tmdb_id"), item.get("season_number")) is True
    except Exception:       # noqa: BLE001 - unknown → treat as still airing
        logger.debug("season air state unreadable", exc_info=True)
        return False


def _try_season_packs(todo, *, root, search, enqueue, deps, automation_id,
                      settings=None, mode_for=None):
    """Grab a pack per eligible season; return (remaining per-episode todo, grabs).

    Never fatal: a season whose pack search finds nothing — the common case,
    packs are much rarer than singles — falls through to the per-episode pass
    with its items untouched, UNLESS that season is in 'only' mode and has
    finished airing, in which case its episodes are held for a later tick
    rather than assembled from a dozen unrelated releases.
    """
    settings = settings or _season_pack_settings()
    try:
        mode_for = mode_for or _pack_mode_resolver(settings)
        groups = season_pack_groups(todo, min_episodes=settings["season_pack_min_episodes"],
                                    mode_for=mode_for)
    except Exception:       # noqa: BLE001 - grouping must never break the normal drain
        logger.exception("season pack grouping failed")
        return todo, 0
    if not groups:
        return todo, 0

    claimed, held, grabs = set(), set(), 0
    for pack in groups:
        name = "%s S%02d" % (pack.get("show_title") or "?", int(pack.get("season_number") or 0))
        try:
            found = search(pack, "episode")
            cands, _err = found if isinstance(found, tuple) else (found, None)
            best = pick_best(cands or [])
            if best and enqueue(pack, best, cands or [], "episode",
                                _item_target_dir(pack, root)):
                claimed.update(pack.get("_pack_members") or [])
                grabs += 1
                deps.update_progress(
                    automation_id,
                    log_line="Grabbed the %s season pack — covers %d wanted episode(s)"
                             % (name, pack.get("_pack_size") or 0),
                    log_type='success')
                continue
            # No pack (or the grab was refused). In 'only' mode a FINISHED
            # season waits for one instead of being built out of singles; a
            # season still airing falls through, because pack-or-nothing on
            # something that cannot have a pack yet is just "never download".
            if pack.get("_pack_mode") == "only" and _season_has_finished_airing(pack):
                held.update(pack.get("_pack_members") or [])
                deps.update_progress(
                    automation_id,
                    log_line="No %s season pack yet — holding %d episode(s); this show is "
                             "set to season packs only" % (name, pack.get("_pack_size") or 0),
                    log_type='info')
        except Exception:   # noqa: BLE001 - one season failing must not stop the rest
            logger.exception("season pack attempt failed for %s", name)
            continue
    skip = claimed | held
    if not skip:
        return todo, grabs
    return [it for it in todo if item_key(it, "episode") not in skip], grabs


def _acceptable_titles(primary: Any, kind: str, tmdb_id: Any) -> List[str]:
    """[primary title, *user AKAs, *TMDB alternative titles] — deduped, primary first.
    The alias set the release-title gate matches against (so a release named by a known
    aka still matches). Best-effort: just the primary when nothing else is available.

    User AKAs come FIRST after the primary because they're the deliberate override:
    someone typed them precisely because TMDB's own aliases didn't cover the release
    naming they were seeing."""
    user_akas: List[str] = []
    aliases: List[str] = []
    if tmdb_id:
        try:
            from api.video import get_video_db
            user_akas = get_video_db().aka_titles_for_tmdb(kind, tmdb_id) or []
        except Exception:   # noqa: BLE001 - a matching assist must never break a grab
            user_akas = []
        try:
            from core.video.enrichment.engine import get_video_enrichment_engine
            aliases = get_video_enrichment_engine().alt_titles_for(kind, tmdb_id) or []
        except Exception:   # noqa: BLE001
            aliases = []
    out, seen = [], set()
    for t in [primary, *user_akas, *aliases]:
        t = str(t or "").strip()
        if t and t.lower() not in seen:
            seen.add(t.lower())
            out.append(t)
    return out


def _absolute_hint(item: Dict[str, Any], stype: str):
    """The wanted ABSOLUTE episode number, or None — scene anime is numbered
    'Show - 1071' with no season, and that number is the only episode identity
    such a release carries. Consumed accept-only downstream, which is what makes
    a wrong answer here dangerous and a missing one merely unhelpful.

    Two sources, and which are allowed depends on how confident we are:

    · ANIME — both. The library's own episode list is authoritative and is the
      only thing that can answer for a later season (S02E01 of a show with 3
      episodes in season 1 is absolute 4). Falls back to the season-1 identity
      when the show isn't in the library, which is precisely the state of a show
      whose FIRST episode is being grabbed.

    · UNTYPED — the season-1 identity ONLY. A brand-new anime has no shows row,
      so nothing has typed it yet; refusing it a hint is what made the first
      episode of a new show the one grab that could never match. But 'untyped'
      is not 'anime', so it does not get the library lookup: for season 1 the
      absolute number IS the episode number, which is true of every show
      regardless of type, and that makes this derivation safe by construction
      rather than by assumption.

    · STANDARD / DAILY — neither. Wanting S02E01 with absolute 4, a release
      named 'Show - 04' is far more likely season ONE's episode 4. Dailies match
      on air date instead.

    Season 1 only, in every case: specials sit in season 0 and are excluded, so
    the identity holds exactly."""
    if stype in ("standard", "daily"):
        return None
    s, e = item.get("season_number"), item.get("episode_number")
    if stype == "anime":
        try:
            from api.video import get_video_db
            n = get_video_db().episode_absolute_number(item.get("show_tmdb_id"), s, e)
            if n is not None:
                return n
        except Exception:   # noqa: BLE001 - a numbering assist must never break a grab
            pass
    try:
        return int(e) if int(s) == 1 and int(e) >= 1 else None
    except (TypeError, ValueError):
        return None


def search_context(item: Dict[str, Any], media_type: str) -> Dict[str, Any]:
    """The ``search_ctx`` the download row carries (drives the monitor's requery).
    Carries ``titles`` — the primary title plus TMDB aliases — so both the initial pick
    and every retry gate releases against the full alias set."""
    if media_type == "movie":
        ctx = {"scope": "movie", "title": item.get("title"), "year": item.get("year")}
        tmdb_id, kind = item.get("tmdb_id"), "movie"
    elif item.get("_season_pack"):
        # One grab for a whole season. Built by season_pack_groups() from a
        # REPRESENTATIVE member item, so every routing field it carries (Library,
        # category, quality profile, series type, poster) is the season's own —
        # only the scope changes. Deliberately no episode/air_date/absolute: those
        # identify one episode and would make the pack fail its own scope gate.
        ctx = {"scope": "season", "title": item.get("show_title"),
               "season": item.get("season_number"),
               "year": (str(item.get("air_date") or "")[:4] or None)}
        stype = str(item.get("series_type") or "").strip().lower()
        if stype in ("daily", "anime"):
            ctx["series_type"] = stype
        tmdb_id, kind = item.get("show_tmdb_id"), "show"
        titles = _acceptable_titles(ctx["title"], kind, tmdb_id)
        if len(titles) > 1:
            ctx["titles"] = titles
        return ctx
    else:
        ctx = {"scope": "episode", "title": item.get("show_title"),
               "season": item.get("season_number"), "episode": item.get("episode_number"),
               "year": (str(item.get("air_date") or "")[:4] or None),
               # full air date — daily series (Daily Show / Kimmel / soaps) release by
               # DATE, not SxxExx; the ranker + retry queries key off this.
               "air_date": (str(item.get("air_date") or "")[:10] or None)}
        # Series type (P8): daily/anime shows QUERY differently. Anime also carries
        # the wanted ABSOLUTE episode number (scene anime is numbered 'Show - 1071',
        # no season) — derived from the library's episode list, best-effort.
        stype = str(item.get("series_type") or "").strip().lower()
        if stype in ("daily", "anime"):
            ctx["series_type"] = stype
        # ``series_type`` still decides how we QUERY. It no longer decides
        # whether the absolute number is carried at all — see _absolute_hint for
        # what each type is trusted with, and why an untyped show has to be
        # served (a brand-new anime has no shows row for a type to live on).
        n = _absolute_hint(item, stype)
        if n is not None:
            ctx["absolute"] = n
        tmdb_id, kind = item.get("show_tmdb_id"), "show"
    titles = _acceptable_titles(ctx["title"], kind, tmdb_id)
    if len(titles) > 1:
        ctx["titles"] = titles
    return ctx


def build_download_record(item: Dict[str, Any], best: Dict[str, Any], candidates: List[Dict[str, Any]],
                          *, media_type: str, target_dir: str, query: Any) -> Dict[str, Any]:
    """The ``add_video_download`` row for a chosen release — identical shape to a manual
    grab, so the monitor finishes it the same way (other accepted hits become the retry
    pool)."""
    ctx = search_context(item, media_type)
    # stash the chosen source's peer stats so the drawer can show its availability snapshot
    # (free slot / queue depth / speed at grab time). Retry ignores the extra key.
    peer = {k: best.get(k) for k in ("slots", "queue", "speed", "availability") if best.get(k) is not None}
    if peer:
        ctx = {**ctx, "peer": peer}
    media_id = str(item.get("tmdb_id") if media_type == "movie" else item.get("show_tmdb_id"))
    source = str(best.get("source") or "soulseek").lower()
    common = {
        "kind": media_type, "title": ctx["title"],
        "release_title": best.get("title") or best.get("filename"),
        "size_bytes": int(best.get("size_bytes") or 0), "quality_label": best.get("quality_label"),
        "target_dir": target_dir, "status": "downloading",
        "media_id": media_id, "media_source": "tmdb", "year": ctx.get("year"),
        "poster_url": item.get("poster_url"), "search_ctx": json.dumps(ctx), "attempts": 0,
        # the profile this grab was judged under — the monitor's cutoff/requery
        # decisions stay consistent even if the title is reassigned mid-flight
        "quality_profile_id": item.get("quality_profile_id"),
    }
    if source == "soulseek":
        rest = [c for c in (candidates or []) if c.get("filename") != best.get("filename")]
        return {**common, "source": "soulseek", "username": best.get("username"),
                "filename": best.get("filename"), "candidates": json.dumps(rest),
                "tried_queries": json.dumps([query] if query else []),
                "tried_files": json.dumps([best.get("filename")])}
    # torrent / usenet — tracked by the client ref the grab returned; no Soulseek requery pool.
    return {**common, "source": source, "username": best.get("username"),   # indexer (display)
            "filename": best.get("title") or best.get("filename"), "client_ref": best.get("_client_ref"),
            "candidates": json.dumps([]), "tried_queries": json.dumps([]), "tried_files": json.dumps([])}


# ── production seams ──────────────────────────────────────────────────────────
def _default_fetch_items(media_type: str) -> List[Dict[str, Any]]:
    from api.video import get_video_db
    db = get_video_db()
    return db.movie_wishlist_to_download() if media_type == "movie" else db.episode_wishlist_to_download()


def _backfill_movie_available_dates(limit: int = 25) -> None:
    """Resolve the DOWNLOADABLE (home/digital) date for wished movies that don't have one yet —
    TMDB digital/physical, or theatrical + a home-release window (Radarr's 'minimum availability
    = released'). This is what lets the drain SKIP a film that's still only in cinemas instead of
    grabbing a wrong/fake copy. Bounded per run + engine-cached; best-effort. A past-date sentinel
    is stored when TMDB knows nothing, so it isn't re-queried forever (the year check still guards)."""
    try:
        from api.video import get_video_db
        from core.video.enrichment.engine import get_video_enrichment_engine
        db = get_video_db()
        # One-time reset: an earlier version anchored the estimate on TMDB PREMIERE dates
        # (festival screenings months before release), so previously-derived dates are wrong.
        # Wipe them once and re-derive with the wide-theatrical logic.
        if db.get_setting("avail_dates_logic") != "v2":
            db.clear_wishlist_movie_release_dates()
            db.set_setting("avail_dates_logic", "v2")
        need = db.wishlist_movies_missing_release_date(limit)
        if not need:
            return
        eng = get_video_enrichment_engine()
        for tmdb_id in need:
            try:
                db.set_wishlist_release_date(tmdb_id, eng.movie_available_date(tmdb_id) or "1970-01-01")
            except Exception:   # noqa: BLE001 - one lookup failing shouldn't stall the rest
                logger.debug("available-date backfill failed for %s", tmdb_id, exc_info=True)
    except Exception:   # noqa: BLE001 - the backfill is an assist; never block the drain
        logger.debug("movie available-date backfill failed", exc_info=True)


def _default_active_keys(media_type: str) -> set:
    from api.video import get_video_db
    return active_download_keys(get_video_db().get_active_video_downloads())


def _default_target_dir(media_type: str) -> str:
    """The FALLBACK unattended-grab destination when an item isn't already
    filed under a specific Library (or the caller has no per-item info at
    all, e.g. the batch-level pre-flight check): the PRIMARY configured
    Library for this kind, falling back further to the legacy scalar
    movies_path/tv_path for installs with no Libraries configured yet.
    ``_item_target_dir`` is the actual per-item resolver — it prefers an
    item's own Library and only reaches this when it has none. Thin wrapper
    around the shared resolver so every unattended call site keeps working
    unmodified."""
    from api.video import get_video_db
    from core.video.download_pipeline import resolve_download_root
    from core.video.sources import resolve_video_server
    db = get_video_db()
    kind = "movie" if media_type == "movie" else "show"
    server = resolve_video_server(db)
    primary = db.primary_root_folder(server, kind) if server else None
    paths = {"movies_path": db.get_setting("movies_path"),
             "tv_path": db.get_setting("tv_path"),
             "transfer_path": db.get_setting("transfer_path")}
    return resolve_download_root(kind, primary_root_folder=primary, paths=paths)


def _default_category(media_type: str) -> Optional[str]:
    """The FALLBACK torrent/usenet category — the PRIMARY configured
    Library's category for this kind, else None (the client adapter falls
    back to the global torrent_client.category / usenet_client.category
    setting). ``_category_for_item`` is the actual per-item resolver and
    only reaches this when the item has no Library of its own."""
    from api.video import get_video_db
    from core.video.download_pipeline import resolve_torrent_category
    from core.video.sources import resolve_video_server
    db = get_video_db()
    kind = "movie" if media_type == "movie" else "show"
    server = resolve_video_server(db)
    primary = db.primary_root_folder(server, kind) if server else None
    return resolve_torrent_category(primary_root_folder=primary)


def _root_folder_for_item(item: Dict[str, Any]) -> Optional[dict]:
    """The specific Library an item is ALREADY filed under, if any —
    ``root_folder_id`` is joined in by the wishlist queries (from the
    show's/movie's own row via ``library_id``) or stamped on by a
    tmdb_id-only caller (repair grabs). None when the item has no Library
    yet, which callers treat as 'use the primary'."""
    rfid = item.get("root_folder_id")
    if not rfid:
        return None
    from api.video import get_video_db
    return get_video_db().get_root_folder(rfid)


def _item_label(item: Dict[str, Any]) -> str:
    """Best human name for an item, for logging. Episode rows carry the show
    under ``show_title``; movies use ``title``."""
    for key in ("show_title", "title", "episode_title"):
        val = (item or {}).get(key)
        if val:
            return str(val)
    return "?"


def _item_target_dir(item: Dict[str, Any], fallback: str) -> str:
    """Per-item grab destination (multi-library #1105): an item already
    filed under a Library (an existing show/movie's ``root_folder_id``)
    grabs into THAT Library — e.g. a show cataloged under Anime stays in
    Anime instead of always landing in the primary TV Library. ``fallback``
    is whatever the caller already resolved as the batch/primary default
    (so a test-injected ``target_dir`` fake is respected exactly as before
    for any item with no Library of its own)."""
    root_folder = _root_folder_for_item(item)
    if root_folder and root_folder.get("path"):
        return root_folder["path"]
    # Falling back is a DECISION, and it used to be an invisible one. Nothing
    # said which Library a grab chose or why, so a show landing in the primary
    # TV Library because no one had ever told it otherwise looked exactly like
    # a show landing there correctly — and once it imported, the next library
    # scan stamped that Library onto the show row and made it permanent. Say it
    # out loud, once, at the moment it happens.
    logger.info(
        "[Library] '%s' has no Library of its own — falling back to the primary "
        "(%s). If that is the wrong shelf, set one on its watchlist/wishlist row "
        "BEFORE it grabs: the first import decides where the show lives from then on.",
        _item_label(item), fallback,
    )
    return fallback


def _category_for_item(item: Dict[str, Any], media_type: str) -> Optional[str]:
    """Per-item torrent/usenet category, mirroring ``_item_target_dir`` so a
    grab's destination folder and its category always come from the SAME
    Library."""
    root_folder = _root_folder_for_item(item)
    if root_folder and (root_folder.get("category") or "").strip():
        return root_folder["category"].strip()
    return _default_category(media_type)


def _preferred_indexer_ids_for_item(item: Dict[str, Any]) -> set:
    """The trackers this item's Library is RESTRICTED to, if any.

    This used to be a soft +25 ranking nudge while every indexer was still
    searched, which meant unticking a tracker in Settings → Libraries removed a
    bonus but never stopped the tracker being used — reported as "it still tries
    to grab from a tracker I deselected". It is now a search filter: only the
    ticked trackers are queried for grabs into that Library.

    An item with no Library yet (root_folder_id unset) has no restriction — this
    deliberately does NOT fall back to the primary Library's selection the way
    ``_item_target_dir``/``_category_for_item`` fall back for destination and
    category. Inheriting a RESTRICTION from an unrelated 'primary' Library could
    silently narrow a search to trackers the user never chose for this item, and
    an over-narrow search looks identical to "no releases exist"."""
    root_folder = _root_folder_for_item(item)
    raw = (root_folder or {}).get("preferred_indexer_ids") or ""
    out = set()
    for p in str(raw).split(","):
        p = p.strip()
        if p.isdigit():
            out.add(int(p))
    return out


def _search_one_source(source: str, item: Dict[str, Any], media_type: str):
    """Search ONE source → (ranked candidates tagged with source, error). soulseek via slskd,
    torrent/usenet via Prowlarr. Returns (None, error) when the search couldn't run."""
    from api.video import get_video_db
    from api.video.downloads import _evaluate_hits
    from core.video.quality_profile import load_for_item
    ctx = search_context(item, media_type)
    profile = load_for_item(get_video_db(), item)   # per-title profile (P2)
    if source == "soulseek":
        from core.video.download_monitor import _search_for_retry
        from core.video.slskd_search import build_query
        query = build_query(ctx["scope"], ctx["title"], year=ctx.get("year"),
                            season=ctx.get("season"), episode=ctx.get("episode"),
                            air_date=ctx.get("air_date"), absolute=ctx.get("absolute"),
                            series_type=ctx.get("series_type"))
        res = _search_for_retry(query) or {}
        if res.get("started") is False:
            return None, res.get("error")
        hits = res.get("hits") or []
    elif source in ("torrent", "usenet"):
        from core.video.prowlarr_search import prowlarr_search
        # THE unattended path — the wishlist drain. This is where a deselected
        # tracker was still being searched, because the Library's selection only
        # ever reached the ranking step.
        pres = prowlarr_search(ctx["scope"], ctx["title"], year=ctx.get("year"),
                               season=ctx.get("season"), episode=ctx.get("episode"), source=source,
                               air_date=ctx.get("air_date"), absolute=ctx.get("absolute"),
                               series_type=ctx.get("series_type"),
                               indexer_ids=_preferred_indexer_ids_for_item(item))
        if not pres.get("configured"):
            return None, "Prowlarr not configured"
        if pres.get("error"):
            return None, pres["error"]
        hits = pres["hits"]
    else:
        return None, "unsupported source %r" % source
    cands = _evaluate_hits(hits, profile, ctx["scope"], ctx.get("season"), ctx.get("episode"),
                           want_year=ctx.get("year"),
                           want_title=ctx.get("titles") or ctx.get("title"),
                           want_date=ctx.get("air_date"), want_absolute=ctx.get("absolute"))
    for c in cands:
        c["source"] = source
    return cands, None


def _default_search(item: Dict[str, Any], media_type: str):
    """Ranked candidates for a wished item, honoring the download mode/order. In hybrid mode the
    sources are tried IN ORDER — the first that yields an ACCEPTED release wins (mirrors the
    music per-item quality-fallback). Returns [] for a real empty result across all sources, or
    **None** (with the error) if no source's search could even run.

    When SOME source in the chain couldn't run (e.g. torrent is first but Prowlarr
    isn't configured), that skip rides back in the error slot alongside the surviving
    results — silent degradation to a weaker source misled a whole run once ('why
    can't it find what's plainly on TPB?'), so the run log must say it every time."""
    from core.video import download_config
    from api.video import get_video_db
    cfg = download_config.load(get_video_db())
    mode = str(cfg.get("download_mode") or "soulseek")
    chain = (cfg.get("hybrid_order") or ["soulseek"]) if mode == "hybrid" else [mode]
    skips: List[str] = []
    fallback = None      # hits that didn't pass the profile — kept so the caller can say 'rejected'
    for src in chain:
        cands, err = _search_one_source(src, item, media_type)
        if cands is None:
            skips.append("%s skipped — %s" % (src, err or "search didn't run"))
            continue
        if any(c.get("accepted") for c in cands):
            return cands, None                       # first source with a usable release wins
        if cands:
            fallback = cands
    note = "; ".join(skips) or None
    if fallback is not None:
        return fallback, note                        # → 'rejected' (hits, none accepted)
    if len(skips) == len(chain):
        return None, note                            # → 'search didn't run' (nothing ran at all)
    return [], note                                  # → 'source empty' (+ any skip note)


def _default_enqueue(item: Dict[str, Any], best: Dict[str, Any], candidates: List[Dict[str, Any]],
                     media_type: str, target_dir: str) -> bool:
    """Start the slskd transfer + write the download row (exactly like the manual flow),
    then ensure the monitor is running. Returns True if slskd accepted it."""
    from api.video import get_video_db
    from core.video import disk_guard, organization
    from core.video.download_monitor import ensure_started
    from core.video.slskd_search import build_query
    ok_room, free = disk_guard.has_room(target_dir, organization.load(get_video_db()))
    if not ok_room:
        logger.warning("disk guard: %.1f GB free on %s — skipping grab of %s",
                       free or 0, target_dir,
                       item.get("title") or item.get("show_title") or "?")
        return False
    source = str(best.get("source") or "soulseek").lower()
    if source == "soulseek":
        from core.video.slskd_download import start_download
        started = start_download(best.get("username"), best.get("filename"), best.get("size_bytes") or 0)
        if not started.get("ok"):
            return False
    else:
        # torrent / usenet — hand off to the shared client; carry the returned ref into the row.
        # Category comes from the SAME Library target_dir was resolved from — the item's
        # own Library when it has one, else the primary (multi-library #1105).
        from core.video.client_grab import grab
        # The hit carries the magnet beside the URL (#1139) — the automation
        # grabs unattended, so a dead-magnet stall here is one nobody sees.
        res = grab(source, best.get("download_url"),
                   category=_category_for_item(item, media_type),
                   fallback_magnet=best.get("magnet_uri"))
        if not res.get("ok"):
            # Episode items alias the show name as `show_title`; reading only
            # `title` made every episode refusal log 'refused for None' — the
            # same aliasing already handled in _default_record_outcome.
            logger.warning("video hybrid: %s grab refused for %s: %s", source,
                           item.get("title") or item.get("show_title") or "?",
                           res.get("error"))
            return False
        best = {**best, "_client_ref": res["ref"]}
    ctx = search_context(item, media_type)
    query = build_query(ctx["scope"], ctx["title"], year=ctx.get("year"),
                        season=ctx.get("season"), episode=ctx.get("episode"))
    get_video_db().add_video_download(
        build_download_record(item, best, candidates, media_type=media_type,
                              target_dir=target_dir, query=query))
    ensure_started(get_video_db)
    return True


# ── guard: keep an in-progress drain from overlapping the next tick ───────────
_running: Dict[str, bool] = {"movie": False, "episode": False}


def _default_record_outcome(item: Dict[str, Any], media_type: str, grabbed: bool) -> None:
    """Persist the drain search outcome on the wishlist row (#liveleak-failing-hub):
    a grab resets search_attempts, a fruitless search increments it. Callers skip
    this entirely when the search never ran (slskd down) — that's not evidence
    the release doesn't exist. Best-effort: a db hiccup never breaks the drain."""
    try:
        from api.video import get_video_db
        # episode drain items alias the show id as show_tmdb_id (movie items
        # carry plain tmdb_id) — read both or episodes silently never record
        tmdb = item.get('tmdb_id') or item.get('show_tmdb_id')
        if not tmdb:
            return
        get_video_db().record_wishlist_search_outcome(
            'movie' if media_type == 'movie' else 'episode',
            tmdb, grabbed,
            season_number=item.get('season_number'),
            episode_number=item.get('episode_number'))
    except Exception:   # noqa: BLE001 - visibility must never break acquisition
        logger.debug("record_wishlist_search_outcome failed", exc_info=True)


def is_running(media_type: str) -> bool:
    return bool(_running.get(media_type))


def auto_video_process_wishlist(
    config: Dict[str, Any],
    deps: AutomationDeps,
    *,
    media_type: str = "movie",
    fetch_items: Optional[Callable[[str], List[Dict[str, Any]]]] = None,
    active_keys: Optional[Callable[[str], Iterable]] = None,
    target_dir: Optional[Callable[[str], str]] = None,
    search: Optional[Callable[[Dict[str, Any], str], List[Dict[str, Any]]]] = None,
    enqueue: Optional[Callable[..., bool]] = None,
    record_outcome: Optional[Callable[[Dict[str, Any], str, bool], None]] = None,
) -> Dict[str, Any]:
    """Auto-grab the wished movies (or episodes): search Soulseek, pick the best release,
    enqueue. Processes the whole eligible wishlist, a few searches at a time.

    Returns ``{'status': 'completed', 'searched': int, 'grabbed': int, ...}``."""
    fetch_items = fetch_items or _default_fetch_items
    active_keys = active_keys or _default_active_keys
    target_dir = target_dir or _default_target_dir
    search = search or _default_search
    enqueue = enqueue or _default_enqueue
    record_outcome = record_outcome or _default_record_outcome
    automation_id = config.get('_automation_id')
    concurrency = max(1, int(config.get('max_concurrent', 3) or 3))
    label = 'movie' if media_type == 'movie' else 'episode'

    _running[media_type] = True
    try:
        root = target_dir(media_type)
        if not root:
            where = 'Movie' if media_type == 'movie' else 'TV'
            deps.update_progress(automation_id, status='finished', progress=100, phase='Complete',
                                 log_line='%s library folder not set — skipping (Settings → Downloads)' % where,
                                 log_type='info')
            return {'status': 'completed', 'searched': 0, 'grabbed': 0,
                    'skipped': 'no_folder', '_manages_own_progress': True}

        deps.update_progress(automation_id, phase='Checking the wishlist…', progress=5,
                             log_line='Looking for wished %ss to grab' % label, log_type='info')
        if media_type == 'movie':
            _backfill_movie_available_dates()   # resolve downloadable dates so the gate can skip cinema-only films
        items = fetch_items(media_type) or []
        # Upgrade-until-cutoff: owned rows are judged against the profile cutoff
        # (skip when met; strictly-better-only when below). Only loaded when an
        # owned row is actually present — the common all-new case stays DB-free.
        if any(it.get("owned") for it in items):
            try:
                cutoff_rank = _default_cutoff_rank()
            except Exception:   # noqa: BLE001 - no profile → treat as no cutoff
                cutoff_rank = 0
            # per-title profiles: judge each owned item against ITS profile's
            # cutoff when any assignment exists (the common no-assignment case
            # stays on the single global read)
            per_item = _cutoff_rank_for_item if any(
                it.get("quality_profile_id") for it in items) else None
            items = annotate_upgrades(items, cutoff_rank, cutoff_for=per_item)
        active = set(active_keys(media_type) or set())
        # A season pack already downloading claims every episode in it, so those
        # episodes must not also be grabbed one by one while it lands.
        todo = [it for it in items
                if item_key(it, media_type) not in active
                and season_key(it, media_type) not in active]
        # Season packs (opt-in): try ONE grab for a season with several holes
        # before falling back to per-episode. Runs first so a successful pack
        # removes its episodes from this tick's per-episode work.
        pack_grabs = 0
        if media_type == "episode":
            _sp = _season_pack_settings()
            # The global switch is not the only way in: a show with its OWN
            # 'prefer'/'only' override packs even while the global is off, so
            # the pass runs whenever ANY show in this tick opts in.
            _mode_for = _pack_mode_resolver(_sp)
            if any(_mode_for(it) != "never" for it in todo):
                todo, pack_grabs = _try_season_packs(
                    todo, root=root, search=search, enqueue=enqueue, deps=deps,
                    automation_id=automation_id, settings=_sp, mode_for=_mode_for)
        if not todo:
            # A season pack can claim every remaining episode, emptying todo — that
            # is a fully successful run, not "nothing to grab", so the pack count
            # has to survive this early return or the tick reports 0 grabbed.
            done_msg = ('Grabbed %d season pack(s) — nothing else outstanding' % pack_grabs
                        if pack_grabs else
                        'Nothing new to grab (%d already in flight)' % len(active))
            deps.update_progress(automation_id, status='finished', progress=100, phase='Complete',
                                 log_line=done_msg,
                                 log_type='success' if pack_grabs else 'info')
            return {'status': 'completed', 'searched': 0, 'grabbed': pack_grabs,
                    'season_packs': pack_grabs, '_manages_own_progress': True}

        grabbed = [0]
        searched = [0]
        noresults = [0]    # search came back empty (the source had nothing)
        rejected = [0]     # source had hits, but none passed the quality profile
        notrun = [0]       # the search never ran (slskd didn't accept it)
        total = len(todo)
        lock = threading.Lock()

        def _one(it):
            found = search(it, media_type)
            # the seam returns (candidates, error); tolerate a bare list/None too (test fakes)
            cands, err = found if isinstance(found, tuple) else (found, None)
            didnt_run = cands is None       # slskd not configured / errored / rate-limited
            cands = cands or []
            best = pick_best(cands, it.get("_min_rank") or 0)
            # multi-library #1105: an item already filed under its own Library
            # (e.g. Anime) grabs there, not always into the batch's primary `root`.
            item_target = _item_target_dir(it, root)
            ok = bool(best) and bool(enqueue(it, best, cands, media_type, item_target))
            name = it.get('title') or it.get('show_title') or '?'
            if media_type == 'episode':
                name = "%s S%02dE%02d" % (name, int(it.get('season_number') or 0),
                                          int(it.get('episode_number') or 0))
            # tell apart: grabbed / search-didn't-run / source-empty / hits-but-all-rejected.
            # `err` alongside RESULTS is a non-fatal note (a chain source was skipped,
            # e.g. 'torrent skipped — Prowlarr not configured') — always show it, or a
            # mis-configured first source silently degrades every search.
            if ok:
                msg, lt = "Grabbed '%s'" % name, 'success'
            elif didnt_run:
                msg = ("Search didn't run for '%s' — %s" % (name, err)) if err \
                    else ("Search didn't run for '%s' — slskd not responding?" % name)
                lt = 'warning'
            elif not cands:
                msg, lt = "No search results for '%s'" % name, 'info'
                if err:
                    msg, lt = msg + " · " + str(err), 'warning'
            elif it.get('_min_rank'):
                msg = ("%d result(s) for '%s', none better than your current copy — "
                       "still watching for an upgrade" % (len(cands), name))
                lt = 'info'
            else:
                why = (cands[0].get('rejected') or 'none met your quality profile')
                msg, lt = "%d result(s) for '%s', none accepted — %s" % (len(cands), name, why), 'info'
                if err:
                    msg, lt = msg + " · " + str(err), 'warning'
            # Failing visibility (#liveleak-failing-hub): record the outcome on
            # the wishlist row — but only when the search actually RAN. A search
            # that never ran (slskd down / rate-limited) says nothing about
            # whether the release exists and must not push a row toward failing.
            if not didnt_run:
                record_outcome(it, media_type, ok)
            with lock:
                searched[0] += 1
                if ok:
                    grabbed[0] += 1
                elif didnt_run:
                    notrun[0] += 1
                elif not cands:
                    noresults[0] += 1
                else:
                    rejected[0] += 1
                deps.update_progress(
                    automation_id, phase='Searching + grabbing…',
                    progress=10 + int(85 * searched[0] / max(total, 1)),
                    log_line=msg, log_type=lt)

        with ThreadPoolExecutor(max_workers=concurrency) as ex:
            list(ex.map(_one, todo))

        # Headline with the WHY breakdown: it's the difference between "the source has
        # nothing" (noresults) and "it has stuff but your quality profile rejects it" (rejected).
        tail = []
        if notrun[0]:
            tail.append("%d search(es) didn't run (slskd?)" % notrun[0])
        if noresults[0]:
            tail.append('%d had no results' % noresults[0])
        if rejected[0]:
            tail.append('%d rejected on quality' % rejected[0])
        breakdown = (' · ' + ', '.join(tail)) if tail else ''
        done = ('Grabbed %d %s(s) of %d searched%s' % (grabbed[0], label, searched[0], breakdown)) if grabbed[0] \
            else ('Searched %d %s(s), grabbed 0%s' % (searched[0], label, breakdown))
        deps.update_progress(automation_id, status='finished', progress=100, phase='Complete',
                             log_line=done, log_type='success' if grabbed[0] else 'info')
        return {'status': 'completed', 'searched': searched[0],
                # season packs are grabs too — a tick that landed one pack and no
                # singles must not report 'grabbed 0'
                'grabbed': grabbed[0] + pack_grabs, 'season_packs': pack_grabs,
                'noresults': noresults[0], 'rejected': rejected[0], 'notrun': notrun[0],
                '_manages_own_progress': True}
    except Exception as e:  # noqa: BLE001
        deps.update_progress(automation_id, status='error', phase='Error', log_line=str(e), log_type='error')
        return {'status': 'error', 'error': str(e), '_manages_own_progress': True}
    finally:
        _running[media_type] = False
