"""Prowlarr-backed search for the VIDEO side — movies/TV via torrent + usenet indexers.

Best-in-class (Sonarr/Radarr-parity) query strategy: for each search we run BOTH

  1. a STRUCTURED Newznab search (``tvsearch`` / ``movie``) carrying season/ep +
     external ids (tvdb/imdb/tmdb) — the precise, id-aware form the *arr apps use;
     Prowlarr routes each hint to the indexers that support it, and
  2. the SCENE-FORMATTED free-text search ("Title SxxExx" / "Title Year") — which is
     often tighter than a structured query on public trackers that only do text.

Results are merged + deduped (by guid / download URL), then the shared ranker
(``_evaluate_hits`` → ``evaluate_release`` / scope validation) filters out anything
that doesn't actually match the requested movie / season / episode — so a broad
structured result set is cleaned up exactly like Sonarr cleans up its own.

MUSIC-SAFE BY CONSTRUCTION: only READS the shared ``prowlarr.*`` config and CALLS the
shared ``ProwlarrClient`` (via arguments it already accepts); never modifies a music
module. Hits are projected into the SAME shape ``core/video/slskd_search`` produces so
the ranking, UI, and grab path stay source-agnostic.
"""

from __future__ import annotations

import re
from concurrent.futures import ThreadPoolExecutor
from typing import Any, List, Optional, Tuple

from utils.logging_config import get_logger

logger = get_logger("video.prowlarr_search")

# Newznab standard categories (Prowlarr uses these): Movies 2xxx, TV 5xxx.
_MOVIE_CATS = [2000, 2010, 2020, 2030, 2040, 2045, 2050, 2060]
_TV_CATS = [5000, 5020, 5030, 5040, 5045, 5050, 5060]

_TV_SCOPES = ("episode", "season", "series", "show")


def _categories(scope: str) -> List[int]:
    return _TV_CATS if str(scope or "").lower() in _TV_SCOPES else _MOVIE_CATS


def _client():
    from core.prowlarr_client import ProwlarrClient
    return ProwlarrClient()


def is_configured() -> bool:
    """True when Prowlarr's URL + key are set (shared music config)."""
    try:
        return bool(_client().is_configured())
    except Exception:   # noqa: BLE001 - a config read never blocks the caller
        return False


def list_indexers() -> List[dict]:
    """The configured indexers as ``{id, name, protocol, enable, privacy}``.

    Deliberately NOT the raw Prowlarr payload: that carries each indexer's URL
    and API key, and leaking indexer URLs to the browser was a real security bug
    once already. Only the identity fields a picker needs cross the boundary.

    Exists because the per-Library "Preferred trackers" setting stores numeric
    Prowlarr ids, and nothing in the app ever showed the user what those ids
    were — so the field asked for something it gave you no way to find out."""
    try:
        c = _client()
        if not c.is_configured():
            return []
        return [{"id": ix.id, "name": ix.name, "protocol": ix.protocol,
                 "enable": bool(ix.enable), "privacy": ix.privacy}
                for ix in c._get_indexers_sync()]
    except Exception:   # noqa: BLE001 - an unreachable Prowlarr just means no picker
        logger.warning("could not list Prowlarr indexers", exc_info=True)
        return []


def _indexer_ids() -> List[int]:
    """The optional Prowlarr indexer allowlist (shared ``prowlarr.indexer_ids``)."""
    from config.settings import config_manager
    raw = str(config_manager.get("prowlarr.indexer_ids", "") or "").strip()
    return [int(p) for p in (x.strip() for x in raw.split(",")) if p.isdigit()]


# Sentinel for "the caller asked for specific trackers, and none of them survive
# the global allowlist". Distinct from [] (= no restriction, search everything),
# which is what an empty list has always meant here.
_NO_INDEXERS = "no_indexers"


def effective_indexer_ids(restrict_to=None):
    """Which Prowlarr indexers a search may actually use.

    Two independent restrictions, ANDed:
      * the global ``prowlarr.indexer_ids`` allowlist (Settings → Indexers), and
      * ``restrict_to`` — the Library's own tracker selection, for grabs filed
        into that Library.

    Either being empty/None means "that one imposes no restriction", so with
    neither set the result is ``[]`` and Prowlarr searches every enabled indexer,
    exactly as before.

    When both are set the result is their INTERSECTION. If that intersection is
    empty the two settings contradict each other — the Library asks for trackers
    the global allowlist forbids. Returning ``[]`` there would read as "no
    restriction" and quietly search EVERY indexer, which is the opposite of what
    both settings asked for, so this returns the ``_NO_INDEXERS`` sentinel and
    the caller reports the misconfiguration instead of guessing.
    """
    allowed = [int(i) for i in (_indexer_ids() or [])]
    wanted = []
    for i in (restrict_to or []):
        try:
            wanted.append(int(i))
        except (TypeError, ValueError):
            continue
    if not wanted:
        return allowed
    if not allowed:
        return wanted
    both = [i for i in wanted if i in set(allowed)]
    return both if both else _NO_INDEXERS


def _imdb_num(imdb_id: Any) -> Optional[str]:
    """Newznab wants the imdb id as digits, no ``tt`` prefix ('tt0111161' → '0111161')."""
    s = str(imdb_id or "").strip()
    if not s:
        return None
    m = re.match(r"^(?:tt)?(\d{6,9})$", s, re.IGNORECASE)
    return m.group(1) if m else None


def _as_int(v: Any) -> Optional[int]:
    try:
        return int(v)
    except (TypeError, ValueError):
        return None


def build_strategies(scope: str, title: Any, *, year: Any = None, season: Any = None,
                     episode: Any = None, imdb_id: Any = None, tmdb_id: Any = None,
                     tvdb_id: Any = None, air_date: Any = None, absolute: Any = None,
                     series_type: Any = None) -> List[Tuple[str, str, List[tuple]]]:
    """The set of Prowlarr searches to run for one request, as ``(type, query, extra)``.

    Pure (no I/O) so it's unit-tested. Always includes the scene-formatted text search;
    adds the structured tv/movie search (with whatever ids we have) so id-aware indexers
    resolve exactly. Identical strategies are collapsed."""
    from core.video.slskd_search import build_query
    t = str(title or "").strip()
    scope = str(scope or "movie").lower()
    imdb, tmdb, tvdb = _imdb_num(imdb_id), _as_int(tmdb_id), _as_int(tvdb_id)
    s_i, e_i = _as_int(season), _as_int(episode)
    strat: List[Tuple[str, str, List[tuple]]] = []

    if scope == "movie":
        extra: List[tuple] = []
        if year:
            extra.append(("year", year))
        if imdb:
            extra.append(("imdbid", imdb))
        if tmdb:
            extra.append(("tmdbid", tmdb))
        strat.append(("movie", t, extra))
        strat.append(("search", build_query("movie", t, year=year), []))
    elif scope == "episode":
        extra = []
        if s_i is not None:
            extra.append(("season", s_i))
        if e_i is not None:
            extra.append(("ep", e_i))
        if tvdb:
            extra.append(("tvdbid", tvdb))
        if imdb:
            extra.append(("imdbid", imdb))
        strat.append(("tvsearch", t, extra))
        # The text search speaks the scene's naming for this SERIES TYPE (P8):
        # daily → 'Title 2026.07.08', anime → 'Title 1071'. The plain SxxExx text
        # query stays as an extra strategy (some indexers normalize numbering).
        q_typed = build_query("episode", t, season=season, episode=episode,
                              air_date=air_date, absolute=absolute, series_type=series_type)
        strat.append(("search", q_typed, []))
        q_std = build_query("episode", t, season=season, episode=episode)
        if q_std != q_typed:
            strat.append(("search", q_std, []))
    elif scope == "season":
        extra = []
        if s_i is not None:
            extra.append(("season", s_i))
        if tvdb:
            extra.append(("tvdbid", tvdb))
        if imdb:
            extra.append(("imdbid", imdb))
        strat.append(("tvsearch", t, extra))
        strat.append(("search", build_query("season", t, season=season), []))
    else:   # series / whole show
        extra = []
        if tvdb:
            extra.append(("tvdbid", tvdb))
        if imdb:
            extra.append(("imdbid", imdb))
        strat.append(("tvsearch", t, extra))
        strat.append(("search", t, []))

    # Collapse identical (type, query, extra) — e.g. a movie with no year makes the
    # structured 'movie' query and the text 'search' query the same term.
    seen, out = set(), []
    for st_type, q, extra in strat:
        if not str(q or "").strip():
            continue
        keyv = (st_type, q, tuple(extra))
        if keyv in seen:
            continue
        seen.add(keyv)
        out.append((st_type, q, extra))
    return out


# Prowlarr's per-call result cap. Configurable because "how many releases exist"
# varies enormously by title and by how many indexers are attached — a long
# running show across a dozen trackers blows past the old hard-coded 100 before
# the ranking gets a chance to choose.
_DEFAULT_SEARCH_LIMIT = 200
_SEARCH_LIMIT_KEY = "prowlarr.search_limit"


# How long to wait for ONE Prowlarr search. Separate from the client's 15s
# metadata timeout: a cold search fans out to every configured indexer and
# several authenticate on first use, so it can take a minute even when healthy.
_DEFAULT_SEARCH_TIMEOUT = 90
_SEARCH_TIMEOUT_KEY = "prowlarr.search_timeout"


def _search_timeout() -> float:
    try:
        from config.settings import config_manager
        n = float(config_manager.get(_SEARCH_TIMEOUT_KEY, _DEFAULT_SEARCH_TIMEOUT)
                  or _DEFAULT_SEARCH_TIMEOUT)
    except Exception:   # noqa: BLE001 - config must never break search
        return _DEFAULT_SEARCH_TIMEOUT
    # Below ~5s nothing real completes; above a few minutes the user has given up.
    return max(5.0, min(300.0, n))


def _search_limit() -> int:
    try:
        from config.settings import config_manager
        n = int(config_manager.get(_SEARCH_LIMIT_KEY, _DEFAULT_SEARCH_LIMIT)
                or _DEFAULT_SEARCH_LIMIT)
    except (TypeError, ValueError, Exception):   # noqa: BLE001 - config must never break search
        return _DEFAULT_SEARCH_LIMIT
    # Prowlarr rejects absurd values and a tiny one silently starves the ranker.
    return max(20, min(1000, n))


def _safe_info_url(value: Any) -> str | None:
    """An indexer's details page, but ONLY if it's http(s).

    This string comes from a third party and is rendered as a link the user
    clicks. A ``javascript:`` or ``data:`` URL there would execute in the page,
    so the scheme is checked here — at the boundary where the untrusted value
    enters — rather than trusted to whatever renders it later."""
    raw = str(value or "").strip()
    if not raw:
        return None
    try:
        from urllib.parse import urlparse
        return raw if urlparse(raw).scheme in ("http", "https") else None
    except ValueError:
        return None


def _project(r: Any, url: str, want_proto: str) -> dict:
    """One Prowlarr result → the slskd-shaped hit ``_evaluate_hits`` consumes."""
    size = int(getattr(r, "size", 0) or 0)
    seeders = getattr(r, "seeders", None)
    return {
        # The indexer's own page for this release, so the title can link to where
        # it came from. Prowlarr calls it infoUrl; not every indexer supplies one.
        "info_url": _safe_info_url(getattr(r, "info_url", None)),
        "publish_date": getattr(r, "publish_date", None),
        "title": r.title,
        "size_bytes": size,
        "seeders": seeders,
        "peers": getattr(r, "leechers", None),
        "username": getattr(r, "indexer_name", None) or "indexer",   # shown as the "source"
        "availability": (seeders if seeders is not None else (getattr(r, "grabs", 0) or 0)),
        "filename": r.title,                 # the grab uses the URL carriers below, not this
        "files": [], "file_count": 0, "folder_size_bytes": size,
        "download_url": url,
        "protocol": getattr(r, "protocol", want_proto),
        "indexer_id": getattr(r, "indexer_id", None),
        "guid": getattr(r, "guid", None),
    }


def prowlarr_search(scope: str, title: Any, *, year: Any = None, season: Any = None,
                    episode: Any = None, source: str = "torrent", imdb_id: Any = None,
                    tmdb_id: Any = None, tvdb_id: Any = None, air_date: Any = None,
                    absolute: Any = None, series_type: Any = None,
                    indexer_ids: Any = None) -> dict:
    """Search Prowlarr for a video release with the multi-strategy (structured + text)
    approach. ``source`` picks the protocol to keep (``torrent`` | ``usenet``). Returns
    ``{configured, error?, hits:[...]}`` — the hit shape ``_evaluate_hits`` consumes.

    ``indexer_ids`` restricts this ONE search to specific Prowlarr indexers — the
    Library's own tracker selection. It narrows the global allowlist, never widens
    it. None/empty leaves the global setting in sole charge."""
    client = _client()
    if not client.is_configured():
        return {"configured": False, "hits": []}
    want_proto = "usenet" if str(source or "").lower() == "usenet" else "torrent"
    cats = _categories(scope)
    ids = effective_indexer_ids(indexer_ids)
    if ids is _NO_INDEXERS:
        return {"configured": True, "hits": [],
                "error": "This library's chosen trackers are all excluded by the global "
                         "'Restrict to indexer IDs' setting, so there is nothing left to "
                         "search. Widen one of the two."}
    strategies = build_strategies(scope, title, year=year, season=season, episode=episode,
                                  imdb_id=imdb_id, tmdb_id=tmdb_id, tvdb_id=tvdb_id,
                                  air_date=air_date, absolute=absolute, series_type=series_type)
    if not strategies:
        return {"configured": True, "hits": []}

    def _run(strat):
        st_type, q, extra = strat
        try:
            # strict=True: a timeout or transport error must RAISE, so _run
            # records it and the picker says what went wrong. Swallowed into an
            # empty list it is indistinguishable from "the indexers found
            # nothing" — the search never ran, and the UI claimed no releases
            # exist. That is the bug where searching in Prowlarr's own UI first
            # "fixed" it: the second query hit Prowlarr's cache and came back
            # inside the timeout.
            return client._search_sync(q, cats, ids, _search_limit(),
                                       search_type=st_type, extra_params=extra,
                                       strict=True, timeout=_search_timeout())
        except Exception as e:   # noqa: BLE001 - one strategy failing shouldn't sink the rest
            logger.warning("prowlarr %s search failed for %r: %s", st_type, q, e)
            return e

    # The strategies are independent Prowlarr calls — fan them out so the extra recall
    # doesn't cost extra wall-clock (each is a blocking HTTP round-trip).
    with ThreadPoolExecutor(max_workers=min(4, len(strategies))) as ex:
        outcomes = list(ex.map(_run, strategies))

    hits: dict = {}          # dedupe key (guid or url) → projected hit; first wins
    errors: List[str] = []
    for res in outcomes:
        if isinstance(res, Exception):
            errors.append(str(res))
            continue
        for r in res:
            if getattr(r, "protocol", "") != want_proto:
                continue
            url = getattr(r, "magnet_uri", None) or getattr(r, "download_url", None)
            if not url:
                continue
            keyv = getattr(r, "guid", None) or url
            if keyv in hits:
                continue
            hits[keyv] = _project(r, url, want_proto)

    if not hits and errors:
        return {"configured": True, "error": errors[0], "hits": []}
    return {"configured": True, "hits": list(hits.values())}
