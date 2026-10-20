"""Upstream features Commissary switches off by default instead of deleting.

Deleting upstream code makes every later upstream change to it a merge
conflict. Gating it at a couple of entry points keeps the diff tiny and lets
anyone turn a feature back on from config.

- ``commissary.chat_enabled`` (default False): the Soulseek chat page, its
  /api/chat blueprint, and the two background loops that poll slskd for room
  and PM activity.
- ``commissary.support_button`` (default False): upstream's "Support SoulSync"
  donation button. Commissary takes no donations; upstream's own button would
  point at upstream's maintainer, which on a fork reads as ours.
"""

from __future__ import annotations

from typing import Any, Callable, Optional


def _get(key: str, default: Any, config_get: Optional[Callable[..., Any]]) -> Any:
    if config_get is None:
        from core.settings import config_manager
        config_get = config_manager.get
    try:
        return config_get(key, default)
    except Exception:  # noqa: BLE001 - config unavailable (early init / tests)
        return default


def chat_enabled(config_get: Optional[Callable[..., Any]] = None) -> bool:
    return bool(_get("commissary.chat_enabled", False, config_get))


def support_button_enabled(config_get: Optional[Callable[..., Any]] = None) -> bool:
    return bool(_get("commissary.support_button", False, config_get))
