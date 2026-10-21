"""Configurable caps on a video manual search (fork 1.8.6).

Upstream hard-codes them: Prowlarr is asked for 100 results per call and the
ranked list is cut to 40 accepted + 15 rejected rows, which is why a popular
title "only shows so many torrents" — the ranker had more and the response threw
them away. Defaults stay at upstream's numbers; the keys are the legacy fork's,
so an existing config carries over.

Rejects are capped separately and lower on purpose: the structured tv/movie
search casts a wide net, and uncapped they would bury the accepted rows. Every
value is clamped — a huge one renders thousands of cards, zero looks exactly
like "no releases found".
"""

from __future__ import annotations

MAX_ACCEPTED_KEY = "video.search.max_results"
MAX_REJECTED_KEY = "video.search.max_rejected"
PROWLARR_LIMIT_KEY = "prowlarr.search_limit"

DEFAULT_MAX_ACCEPTED = 40
DEFAULT_MAX_REJECTED = 15
DEFAULT_PROWLARR_LIMIT = 100


def _setting(key: str, default: int, floor: int, ceiling: int) -> int:
    try:
        from core.settings import config_manager
        n = int(config_manager.get(key, default) or default)
    except Exception:   # noqa: BLE001 - config must never break a search
        return default
    return max(floor, min(ceiling, n))


def max_accepted() -> int:
    return _setting(MAX_ACCEPTED_KEY, DEFAULT_MAX_ACCEPTED, 1, 500)


def max_rejected() -> int:
    return _setting(MAX_REJECTED_KEY, DEFAULT_MAX_REJECTED, 0, 200)


def prowlarr_limit() -> int:
    return _setting(PROWLARR_LIMIT_KEY, DEFAULT_PROWLARR_LIMIT, 20, 1000)
