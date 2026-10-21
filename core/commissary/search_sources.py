"""Every configured download source is searchable by hand (old fork 1.9.0).

Upstream offers manual search - the candidates window's source dropdown and
the Search page's Files picker - only the sources of the download mode: the
``hybrid_order`` chain, or the one configured source. But the chain says which
sources the AUTOMATIC cascade tries, unattended and in what order. It was never
a claim the others don't work, yet it hid a connected, working source from
manual search unless the fallback chain was re-ordered too.

So search intent and dispatch intent are separate questions here: every
configured source is offered, the chain first in its order, then the rest by
name. ``in_chain`` / ``chain_position`` only annotate, so the UI can still lead
with the user's preference without enforcing it. Upstream's newer Candidate
Inspector already searches every configured source; this brings the older
surfaces in line with it.
"""

from __future__ import annotations

from typing import Any, Dict, List, Tuple

from utils.logging_config import get_logger

logger = get_logger("commissary.search_sources")


def _canonical(registry, name: str) -> str:
    spec = registry.get_spec(name)
    return spec.name if spec else name


def _display(registry, name: str) -> str:
    spec = registry.get_spec(name)
    return spec.display_name if spec else name.title()


def _chain(registry, mode: str, config_manager) -> List[str]:
    """The automatic cascade's order, read the way upstream's own source list did."""
    if mode != "hybrid":
        return [_canonical(registry, mode)]
    order = config_manager.get("download_source.hybrid_order", ["hifi", "youtube", "soulseek"]) or []
    return list(dict.fromkeys(_canonical(registry, raw) for raw in order))


def _configured(client) -> bool:
    try:
        return not hasattr(client, "is_configured") or bool(client.is_configured())
    except Exception:  # noqa: BLE001 - a source that can't say is not offered
        return False


def sources(orchestrator, config_manager=None) -> List[Dict[str, Any]]:
    """Every configured source as ``{name, display_name, in_chain, chain_position,
    release_level}``, the chain first in its order, then the rest by name.

    A single-source mode the user picked is offered even when ``is_configured()``
    disagrees - upstream did the same: they chose it and may be mid-setup."""
    from core.downloads.pinned_batch import RELEASE_LEVEL_SOURCES

    if config_manager is None:
        from core.settings import config_manager
    registry = orchestrator.registry
    mode = config_manager.get("download_source.mode", "soulseek") or "soulseek"
    chain = _chain(registry, mode, config_manager)
    position = {name: i for i, name in enumerate(chain)}

    names = [name for name, client in registry.all_plugins() if client and _configured(client)]
    if mode != "hybrid" and chain and chain[0] not in names and orchestrator.client(chain[0]):
        names.insert(0, chain[0])

    out = [{
        "name": name,
        "display_name": _display(registry, name),
        "in_chain": name in position,
        "chain_position": position.get(name),
        "release_level": name in RELEASE_LEVEL_SOURCES,
    } for name in dict.fromkeys(names)]
    out.sort(key=lambda s: (0 if s["in_chain"] else 1, s["chain_position"] or 0,
                            str(s["display_name"]).lower()))
    return out


def search_page_sources(orchestrator, config_manager=None) -> Dict[str, Any]:
    """``/api/search/sources``: upstream's ``{mode, sources}`` with every source."""
    return {"mode": orchestrator.mode, "sources": sources(orchestrator, config_manager)}


def manual_search_sources(orchestrator, config_manager) -> Tuple[str, List[Dict[str, Any]]]:
    """``_list_available_download_sources``: ``(download_mode, [{id, label, ...}])``."""
    mode = config_manager.get("download_source.mode", "soulseek")
    return mode, [dict(s, id=s["name"], label=s["display_name"])
                  for s in sources(orchestrator, config_manager)]
