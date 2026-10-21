"""Identify a mirrored Qobuz track by its ISRC (old fork 2.3.9).

Upstream's discovery identifies a mirrored track by its own id when the
playlist and the metadata source are the same service, and a Beatport track by
its ISRC (``core.discovery.direct_match``). A Qobuz track has neither path: no
metadata source knows a Qobuz id, and the Qobuz client never handed its ISRC
on - so a Qobuz playlist was matched by title and artist alone, and an
extended mix or a remaster could come back as whichever version a title search
ranked first.

Qobuz's own ``track/get`` carries the ISRC. This reads it and hands it to
upstream's ``isrc_match``, which looks it up on the active source (Spotify's
``isrc:`` search, Deezer's ``/track/isrc:``) and only accepts a hit whose
artists agree. Anything that doesn't pan out - Qobuz not signed in, no ISRC,
iTunes as the source (no ISRC lookup), a disagreeing artist - is None, and the
normal search runs, so this can never do worse than before.
"""

from __future__ import annotations

import threading
from collections import OrderedDict
from typing import Any, List, Optional, Tuple

from utils.logging_config import get_logger

logger = get_logger("commissary.qobuz_isrc")

_CACHE_SIZE = 5000
_cache: "OrderedDict[str, Tuple[str, List[str]]]" = OrderedDict()
_lock = threading.Lock()


def _running_qobuz():
    """The orchestrator's Qobuz client - never builds an orchestrator."""
    try:
        import core.download_orchestrator as orchestration
        orchestrator = orchestration._default_orchestrator
        client = orchestrator.client("qobuz") if orchestrator is not None else None
        return client if client is not None and client.is_authenticated() else None
    except Exception:  # noqa: BLE001 - no Qobuz just means search
        return None


def track_isrc(track_id: str, client: Any = None) -> Tuple[str, List[str]]:
    """``(isrc, artists)`` for a Qobuz track id, ``('', [])`` when unknown."""
    track_id = str(track_id or "").strip()
    if not track_id.isdigit():
        return "", []
    with _lock:
        if track_id in _cache:
            _cache.move_to_end(track_id)
            return _cache[track_id]
    client = client or _running_qobuz()
    if client is None:
        return "", []
    raw = client.get_track(track_id) or {}
    from core.discovery.isrc_match import clean_isrc
    isrc = clean_isrc(raw.get("isrc"))
    artists = [name for name in (
        (raw.get("performer") or {}).get("name"),
        ((raw.get("album") or {}).get("artist") or {}).get("name"),
    ) if name]
    found = (isrc, artists)
    with _lock:
        _cache[track_id] = found
        while len(_cache) > _CACHE_SIZE:
            _cache.popitem(last=False)
    return found


def qobuz_isrc_match(track_id: Any, discovery_source: str, use_spotify: bool,
                     spotify_client: Any = None, fallback_client: Any = None,
                     qobuz_client: Any = None) -> Optional[Any]:
    """The active source's track for this Qobuz track's ISRC, or None."""
    isrc, artists = track_isrc(track_id, qobuz_client)
    if not isrc:
        return None
    from core.discovery.isrc_match import isrc_match
    return isrc_match(isrc, discovery_source, use_spotify, spotify_client=spotify_client,
                      fallback_client=fallback_client, artists=artists)
