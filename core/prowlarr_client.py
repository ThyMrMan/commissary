"""Prowlarr client — indexer aggregator.

Prowlarr is the indexer manager component of the *arr stack. It exposes
configured Usenet / torrent indexers behind a single Newznab-style API
so downstream apps (Lidarr, Sonarr, Radarr, Commissary) don't have to
implement an indexer integration per provider.

This client is NOT a download source plugin. It does not implement
``DownloadSourcePlugin`` — Prowlarr only *searches*. The torrent /
usenet download plugins (built in subsequent commits) own the
add-to-client / poll-status / extract flow and call this client for
the search step.

Surface:
- ``is_configured()`` — URL + API key present.
- ``check_connection()`` — hits ``/api/v1/system/status``.
- ``get_indexers()`` — list of configured indexers (id, name, protocol,
  capabilities).
- ``search(query, categories, indexer_ids)`` — Newznab search across
  selected indexers. Music categories default to the full audio tree.

Auth: ``X-Api-Key`` header. Found in Prowlarr → Settings → General →
Security → API Key.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Sequence

import requests as http_requests

from config.settings import config_manager
from utils.logging_config import get_logger

logger = get_logger("prowlarr_client")


# Newznab Music category tree. Prowlarr / Jackett / Newznab indexers
# all agree on these numeric IDs. 3000 is the parent — most indexers
# tag releases against the parent OR a leaf; searching the parent
# pulls everything.
MUSIC_CATEGORY_ALL = 3000
MUSIC_CATEGORY_MP3 = 3010
MUSIC_CATEGORY_VIDEO = 3020
MUSIC_CATEGORY_AUDIOBOOK = 3030
MUSIC_CATEGORY_LOSSLESS = 3040
MUSIC_CATEGORY_OTHER = 3050
MUSIC_CATEGORY_FOREIGN = 3060

DEFAULT_MUSIC_CATEGORIES: tuple = (
    MUSIC_CATEGORY_ALL,
    MUSIC_CATEGORY_MP3,
    MUSIC_CATEGORY_LOSSLESS,
    MUSIC_CATEGORY_OTHER,
)


@dataclass
class ProwlarrIndexer:
    """One configured indexer exposed by Prowlarr."""

    id: int
    name: str
    protocol: str          # "torrent" | "usenet"
    enable: bool
    privacy: str           # "public" | "private" | "semiPrivate"
    categories: List[int] = field(default_factory=list)
    capabilities: Dict[str, Any] = field(default_factory=dict)


class ProwlarrUnavailable(Exception):
    """Prowlarr could not answer — timeout, transport error, bad HTTP, junk body.

    Distinct from "the indexers returned nothing", which is a perfectly valid
    empty result. Collapsing the two is what made a timed-out search render as
    "No matching releases found".
    """


def safe_info_url(value: Any) -> Optional[str]:
    """An indexer's details page, but ONLY if it's http(s).

    This string comes from a third party and is rendered as a link the user
    clicks. A ``javascript:`` or ``data:`` URL there would execute in the page,
    so the scheme is checked here — at the boundary where the untrusted value
    enters — rather than trusted to whatever renders it later.

    Lives on the Prowlarr client because both the music and video sides show
    these links, and a second copy is how one of them ends up missing a scheme
    the other learned to reject.
    """
    raw = str(value or "").strip()
    if not raw:
        return None
    try:
        from urllib.parse import urlparse
        return raw if urlparse(raw).scheme in ("http", "https") else None
    except ValueError:
        return None


@dataclass
class ProwlarrSearchResult:
    """One release returned by a Prowlarr search.

    ``download_url`` is the link the torrent / usenet client gets fed.
    For torrent indexers it may be either a ``.torrent`` HTTP URL or
    a magnet URI (sometimes both — ``magnet_uri`` is set when the
    indexer exposes the magnet separately).
    """

    guid: str
    title: str
    indexer_id: int
    indexer_name: str
    protocol: str           # "torrent" | "usenet"
    download_url: Optional[str] = None
    magnet_uri: Optional[str] = None
    info_url: Optional[str] = None
    size: int = 0           # bytes
    seeders: Optional[int] = None
    leechers: Optional[int] = None
    grabs: Optional[int] = None
    publish_date: Optional[str] = None
    categories: List[int] = field(default_factory=list)
    raw: Dict[str, Any] = field(default_factory=dict)


class ProwlarrClient:
    """Thin sync-backed async wrapper around the Prowlarr v1 API."""

    DEFAULT_TIMEOUT = 15
    # Searches are not metadata calls. Prowlarr queries every configured indexer,
    # some of which authenticate on first use, so a cold search routinely runs
    # well past 15s — and the old shared timeout turned that into "no results".
    DEFAULT_SEARCH_TIMEOUT = 90

    def __init__(self) -> None:
        self._load_config()

    def _load_config(self) -> None:
        self._url = (config_manager.get('prowlarr.url', '') or '').rstrip('/')
        self._api_key = config_manager.get('prowlarr.api_key', '') or ''

    def reload_settings(self) -> None:
        self._load_config()
        logger.info("Prowlarr settings reloaded")

    def is_configured(self) -> bool:
        return bool(self._url and self._api_key)

    async def check_connection(self) -> bool:
        if not self.is_configured():
            return False
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(None, self._check_connection_sync)

    def _check_connection_sync(self) -> bool:
        data = self._api_get('system/status')
        return bool(data and 'version' in data)

    async def get_indexers(self) -> List[ProwlarrIndexer]:
        if not self.is_configured():
            return []
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(None, self._get_indexers_sync)

    def _get_indexers_sync(self) -> List[ProwlarrIndexer]:
        data = self._api_get('indexer')
        if not isinstance(data, list):
            return []
        return [self._parse_indexer(entry) for entry in data if isinstance(entry, dict)]

    async def search(
        self,
        query: str,
        categories: Sequence[int] = DEFAULT_MUSIC_CATEGORIES,
        indexer_ids: Optional[Sequence[int]] = None,
        limit: int = 100,
        search_type: str = "search",
        extra_params: Optional[Sequence[tuple]] = None,
    ) -> List[ProwlarrSearchResult]:
        """Run a Newznab search across the selected indexers.

        ``indexer_ids`` is the list of Prowlarr internal indexer IDs to
        query. ``None`` means all enabled indexers.

        ``search_type`` selects the Newznab search mode — ``search`` (generic
        free-text, the default), ``tvsearch`` or ``movie`` (structured). For the
        structured modes, ``extra_params`` carries the id/season/ep hints
        (``[('season', 3), ('ep', 4), ('tvdbid', 12345)]``); Prowlarr passes each
        to the indexers that advertise support for it and falls back to the text
        ``query`` on those that don't. Both are additive — the existing music
        callers keep the plain free-text behaviour.
        """
        if not self.is_configured() or not query.strip():
            return []
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(
            None, self._search_sync, query, list(categories), list(indexer_ids or []),
            limit, search_type, list(extra_params or []),
        )

    def _search_sync(
        self,
        query: str,
        categories: List[int],
        indexer_ids: List[int],
        limit: int,
        search_type: str = "search",
        extra_params: Optional[Sequence[tuple]] = None,
        strict: bool = False,
        timeout: Optional[float] = None,
    ) -> List[ProwlarrSearchResult]:
        # Prowlarr's search endpoint accepts repeated params: ``categories=3000&categories=3010``.
        # ``requests`` serializes lists in that exact form when passed as tuples of pairs.
        params: List[tuple] = [('query', query), ('type', search_type or 'search'), ('limit', limit)]
        for cat in categories:
            params.append(('categories', cat))
        for indexer_id in indexer_ids:
            params.append(('indexerIds', indexer_id))
        for key, value in (extra_params or []):
            if value is not None and value != '':
                params.append((key, value))

        data = self._api_get('search', params=params,
                             timeout=timeout or self.DEFAULT_SEARCH_TIMEOUT,
                             strict=strict)
        if not isinstance(data, list):
            if strict:
                raise ProwlarrUnavailable("Prowlarr returned no result list")
            return []
        return [self._parse_result(entry) for entry in data if isinstance(entry, dict)]

    def _parse_indexer(self, entry: Dict[str, Any]) -> ProwlarrIndexer:
        return ProwlarrIndexer(
            id=int(entry.get('id') or 0),
            name=entry.get('name') or '',
            protocol=entry.get('protocol') or '',
            enable=bool(entry.get('enable', True)),
            privacy=entry.get('privacy') or '',
            categories=[int(c.get('id') or 0) for c in entry.get('capabilities', {}).get('categories', []) if isinstance(c, dict)],
            capabilities=entry.get('capabilities', {}) or {},
        )

    def _parse_result(self, entry: Dict[str, Any]) -> ProwlarrSearchResult:
        cats = entry.get('categories') or []
        category_ids: List[int] = []
        for cat in cats:
            if isinstance(cat, dict) and cat.get('id') is not None:
                try:
                    category_ids.append(int(cat['id']))
                except (TypeError, ValueError):
                    continue
            elif isinstance(cat, int):
                category_ids.append(cat)

        return ProwlarrSearchResult(
            guid=str(entry.get('guid') or entry.get('infoUrl') or entry.get('downloadUrl') or ''),
            title=entry.get('title') or '',
            indexer_id=int(entry.get('indexerId') or 0),
            indexer_name=entry.get('indexer') or '',
            protocol=entry.get('protocol') or '',
            download_url=entry.get('downloadUrl') or None,
            magnet_uri=entry.get('magnetUrl') or None,
            info_url=entry.get('infoUrl') or None,
            size=int(entry.get('size') or 0),
            seeders=entry.get('seeders'),
            leechers=entry.get('leechers'),
            grabs=entry.get('grabs'),
            publish_date=entry.get('publishDate'),
            categories=category_ids,
            raw=entry,
        )

    def _api_get(self, path: str, params=None, timeout=None,
                 strict: bool = False) -> Optional[Any]:
        """GET a Prowlarr endpoint.

        ``strict=True`` RAISES on failure instead of returning None. Callers that
        show results to a user need this: a timeout swallowed into None becomes an
        empty list downstream, which is indistinguishable from "the indexers found
        nothing" — so a search that never actually ran renders as "no results".
        That is exactly what a cold multi-indexer search looked like, until you
        warmed Prowlarr's cache by searching in its own UI and the same query then
        came back inside the timeout.

        Default stays False so existing (music-side) callers are unchanged."""
        if not self.is_configured():
            if strict:
                raise ProwlarrUnavailable("Prowlarr isn't configured.")
            return None
        url = f"{self._url}/api/v1/{path.lstrip('/')}"
        try:
            resp = http_requests.get(
                url,
                headers={'X-Api-Key': self._api_key, 'Accept': 'application/json'},
                params=params,
                timeout=timeout or self.DEFAULT_TIMEOUT,
            )
            if not resp.ok:
                logger.warning("Prowlarr %s returned HTTP %s", path, resp.status_code)
                if strict:
                    raise ProwlarrUnavailable(
                        "Prowlarr returned HTTP %s" % resp.status_code)
                return None
            return resp.json()
        except http_requests.exceptions.Timeout as e:
            logger.error("Prowlarr request to %s timed out: %s", path, e)
            if strict:
                raise ProwlarrUnavailable(
                    "the search timed out after %ss. A first search across many "
                    "indexers can be slow; raise prowlarr.search_timeout if it "
                    "keeps happening." % (timeout or self.DEFAULT_TIMEOUT)) from e
            return None
        except http_requests.exceptions.RequestException as e:
            logger.error("Prowlarr request to %s failed: %s", path, e)
            if strict:
                raise ProwlarrUnavailable("couldn't reach Prowlarr (%s)" % e) from e
            return None
        except ValueError as e:
            logger.error("Prowlarr response to %s was not JSON: %s", path, e)
            if strict:
                raise ProwlarrUnavailable("Prowlarr sent a response we couldn't read") from e
            return None
