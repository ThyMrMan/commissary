"""Deluge / aria2: a duplicate add adopts the torrent already held (fork 2.0.10).

Both clients answer a duplicate add with an RPC error, and both adapters map
every error to None. So "I already have this" read as "I refuse this": no
download row was created, nothing polled the torrent sitting in the client
(often already finished), and the wishlist re-grabbed it every hour.
qBittorrent and Transmission already resolve duplicates upstream.

On a refused add we derive the expected info-hash (a magnet carries it; a
.torrent is the sha1 of its info dict, via upstream's helpers) and look for it.
An http .torrent URL can't be hashed without fetching it, so it stays a refusal
— the same limit qBittorrent has.
"""

from __future__ import annotations

from typing import Optional, Union

from utils.logging_config import get_logger

logger = get_logger("commissary.torrent_adopt")


def expected_hash(payload: Union[str, bytes, None]) -> Optional[str]:
    """Lowercase v1 info-hash of a magnet or raw .torrent, else None."""
    if isinstance(payload, (bytes, bytearray)):
        from core.quality.torrent_contents import torrent_info_hash
        found = torrent_info_hash(bytes(payload))
        return found.lower() if found else None
    from core.torrent_clients.qbittorrent import _magnet_hash
    return _magnet_hash(payload)


def deluge_held(adapter, payload, category: Optional[str]) -> Optional[str]:
    """The hash of a torrent Deluge already holds, labelled; None otherwise."""
    wanted = expected_hash(payload)
    if not wanted:
        return None
    row = adapter._rpc_sync('core.get_torrent_status', [wanted, ['hash']])
    if not isinstance(row, dict) or not row:
        return None
    logger.info("Deluge already had %s — adopting it instead of reporting a refusal",
                wanted[:12])
    # it may have been added by hand or by an older build: label it so it
    # still lands in the right category
    adapter._apply_label(wanted, category or adapter._category)
    return wanted


def aria2_held(adapter, payload) -> Optional[str]:
    """The gid of a torrent aria2 already holds (active, waiting or stopped).

    aria2's id is a gid, not the hash, so the hash is mapped back across all
    three queues. Stopped counts: a finished torrent is the one most worth
    adopting.
    """
    wanted = expected_hash(payload)
    if not wanted:
        return None
    for status in adapter._get_all_sync():
        if (status.info_hash or '').lower() == wanted and status.id:
            logger.info("aria2 already had %s as gid %s — adopting it", wanted[:12], status.id)
            return status.id
    return None
