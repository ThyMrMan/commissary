"""Which tracker served a torrent/usenet result, and a link to it (fork 1.9.8).

Prowlarr puts many indexers behind one "torrent" (or "usenet") source, so the
candidate picker said "torrent" for every row — no help choosing between two
releases. Prowlarr already returns ``indexerName`` and ``infoUrl``; upstream
keeps the name in ``_source_metadata`` but never sends it to the browser, and
drops the URL.

``info_url`` is a third-party string rendered as an href, so it is checked
where it enters, not trusted to the renderer:

- only http(s) — a ``javascript:``/``data:`` URL would run in the page;
- never anything that could be the indexer DOWNLOAD link, or a link carrying
  credentials. Those stay server-side (audit P0-03, see candidate_store).
"""

from __future__ import annotations

from typing import Any, Optional
from urllib.parse import parse_qsl, urlparse

# Query keys that mark a URL as carrying an indexer secret. A details page
# never needs one; a download/RSS link usually does.
_SECRET_KEYS = frozenset({
    'apikey', 'api_key', 'api', 'key', 'passkey', 'authkey', 'auth', 'token',
    'torrent_pass', 'pass', 'password', 'r', 'rsskey', 'uid', 'sig', 'signature',
})


def safe_info_url(value: Any, *, download_url: Any = None, magnet_uri: Any = None) -> Optional[str]:
    """The indexer's details page, or None when it is unsafe to show."""
    raw = str(value or '').strip()
    if not raw:
        return None
    try:
        parsed = urlparse(raw)
        query = parse_qsl(parsed.query, keep_blank_values=True)
    except ValueError:
        return None
    if parsed.scheme not in ('http', 'https') or not parsed.netloc:
        return None
    if parsed.username or parsed.password:
        return None
    if any(k.lower() in _SECRET_KEYS for k, _ in query):
        return None
    if raw in {str(download_url or '').strip(), str(magnet_uri or '').strip()}:
        return None
    if '/download' in parsed.path.lower():
        return None
    return raw


def candidate_indexer_fields(candidate) -> dict:
    """``{indexer, info_url}`` for a Prowlarr-backed candidate, else ``{}``.

    A source without the metadata contributes no keys rather than nulls.
    ``info_url`` was already checked by :func:`safe_info_url` in the plugin;
    it is re-checked here so nothing else on the metadata dict can ever be
    forwarded as a link.
    """
    meta = getattr(candidate, '_source_metadata', None)
    if not isinstance(meta, dict):
        return {}
    out = {}
    if meta.get('indexer'):
        out['indexer'] = str(meta['indexer'])
    url = safe_info_url(meta.get('info_url'))
    if url:
        out['info_url'] = url
    return out
