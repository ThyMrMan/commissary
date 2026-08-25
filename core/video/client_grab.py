"""Hand a chosen torrent/usenet release off to the SHARED download client.

MUSIC-SAFE: imports and CALLS the shared ``core.torrent_clients`` / ``core.usenet_clients``
adapters (same config the music side uses) — never edits them. The adapter methods are async;
we run them on a private event loop so the sync grab handler + monitor can call in. Returns a
small result carrying the client's tracking id (qBittorrent info-hash / SAB nzo_id), which the
video monitor polls for progress + completion.
"""

from __future__ import annotations

import asyncio
from typing import Any, Optional

from utils.logging_config import get_logger

logger = get_logger("video.client_grab")


def _run(coro):
    """Run an async adapter call from sync code on a throwaway loop (no running loop here)."""
    return asyncio.run(coro)


def _torrent_category() -> str:
    from config.settings import config_manager
    return str(config_manager.get("torrent_client.category", "") or "soulsync")


def _usenet_category() -> str:
    from config.settings import config_manager
    return str(config_manager.get("usenet_client.category", "") or "soulsync")


def grab_torrent(url_or_magnet: str, *, category: Optional[str] = None,
                 save_path: Optional[str] = None,
                 fallback_magnet: Optional[str] = None) -> dict:
    """Add a magnet/.torrent URL to the active torrent client. ``category``
    overrides the global default (e.g. a per-Library category resolved from
    root_folders) — omitted/blank falls back to torrent_client.category.
    Returns ``{ok, ref}`` (ref = the info-hash to poll) or ``{ok: False, error}``.

    ``fallback_magnet`` is the same release's magnet, carried from the search
    hit. Callers hand over the .torrent URL first (#1139) so it can be fetched
    server-side and pushed as a file; without the magnet to fall back on, a URL
    this process cannot reach would be a dead end where the magnet worked."""
    from core.torrent_clients import get_active_adapter
    adapter = get_active_adapter()
    if adapter is None or not adapter.is_configured():
        return {"ok": False, "error": "No torrent client configured — set it on Settings → Downloads."}
    try:
        from core.torrent_clients.base import add_torrent_smart
        cat = category or _torrent_category()
        ref = _run(add_torrent_smart(adapter, url_or_magnet, category=cat,
                                     save_path=save_path,
                                     fallback_magnet=fallback_magnet))
    except Exception as e:   # noqa: BLE001 - surface the client error to the grab handler
        logger.warning("torrent add failed: %s", e, exc_info=True)
        return {"ok": False, "error": "Torrent client: " + str(e)}
    if not ref:
        # Name the handoff kind. A release the indexer only offers as a magnet
        # behaves differently from one with a .torrent URL, and "didn't accept
        # the release" alone cannot be acted on — it was logged 324 times for a
        # single title over a week without ever saying what was tried.
        kind = ("magnet" if str(url_or_magnet or '').lower().startswith("magnet:")
                else "a .torrent URL" if str(url_or_magnet or '').lower().startswith("http")
                else "an empty URL")
        logger.warning("torrent client refused %s for this release (category=%s)",
                       kind, cat)
        return {"ok": False,
                "error": "The torrent client didn't accept the release (handed over %s)." % kind}
    return {"ok": True, "ref": str(ref)}


def grab_usenet(url_or_nzb: Any, *, category: Optional[str] = None,
                save_path: Optional[str] = None) -> dict:
    """Add an NZB (URL or bytes) to the active usenet client. ``category``
    overrides the global default the same way ``grab_torrent`` does. Returns
    ``{ok, ref}`` (ref = the nzo_id/NZBID to poll) or ``{ok: False, error}``."""
    from core.usenet_clients import get_active_adapter
    adapter = get_active_adapter()
    if adapter is None or not adapter.is_configured():
        return {"ok": False, "error": "No usenet client configured — set it on Settings → Downloads."}
    try:
        cat = category or _usenet_category()
        ref = _run(adapter.add_nzb(url_or_nzb, category=cat, save_path=save_path))
    except Exception as e:   # noqa: BLE001
        logger.warning("usenet add failed: %s", e, exc_info=True)
        return {"ok": False, "error": "Usenet client: " + str(e)}
    if not ref:
        return {"ok": False, "error": "The usenet client didn't accept the NZB."}
    return {"ok": True, "ref": str(ref)}


def grab(source: str, url: Any, *, category: Optional[str] = None,
        save_path: Optional[str] = None, fallback_magnet: Optional[str] = None) -> dict:
    """Dispatch a grab by source (torrent | usenet).

    ``fallback_magnet`` is torrent-only and ignored for usenet, which has no
    such thing — keeping it on the shared signature means callers do not have to
    branch on the source just to pass it."""
    if str(source).lower() == "torrent":
        return grab_torrent(url, category=category, save_path=save_path,
                            fallback_magnet=fallback_magnet)
    if str(source).lower() == "usenet":
        return grab_usenet(url, category=category, save_path=save_path)
    return {"ok": False, "error": "Unsupported source %r" % source}
