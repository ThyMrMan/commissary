"""The ordered list of download sources — one answer, one place.

The Soulseek-era config asked the same question four ways:

    download_source.mode              'soulseek' | 'hybrid' | <a source name>
    download_source.hybrid_order      ['hifi', 'youtube', 'soulseek']
    download_source.hybrid_primary    legacy pair, pre-dating hybrid_order
    download_source.hybrid_secondary

Every consumer wanted one of exactly three things out of that: the whole
ordered chain, its first entry ("who may claim a whole album"), or whether
there is more than one entry ("is there anything to fall back to"). Each
worked it out for itself, with its own defaults — ``master.py`` defaulted
``mode`` to 'hybrid' while ``album_bundle_dispatch`` defaulted it to
'soulseek', so the same install could disagree with itself about what was
configured.

This module is that derivation, once. ``download_source.sources`` is the
stored form; the legacy keys are still READ so an install that has never
opened Settings since upgrading keeps its exact behaviour, and a rollback
to the previous release still finds its configuration intact.

Single-source is not a special case here — it is a chain of length one.
That is the whole point of the collapse: "hybrid" was never a mode, just
an observation about how many sources were listed.
"""

from __future__ import annotations

from typing import Callable, List, Optional

from utils.logging_config import get_logger

logger = get_logger("downloads.source_chain")

# The stored key. Legacy keys below are read-only fallbacks.
SOURCES_KEY = "download_source.sources"

LEGACY_MODE_KEY = "download_source.mode"
LEGACY_ORDER_KEY = "download_source.hybrid_order"
LEGACY_PRIMARY_KEY = "download_source.hybrid_primary"
LEGACY_SECONDARY_KEY = "download_source.hybrid_secondary"

# What the app shipped with before any of this was configurable.
DEFAULT_CHAIN = ("hifi", "youtube", "soulseek")


def _config_get(config_get: Optional[Callable] = None) -> Callable:
    if config_get is not None:
        return config_get
    from config.settings import config_manager
    return config_manager.get


def clean_sources(raw, normalize: Optional[Callable] = None,
                  drop_unknown: bool = True) -> List[str]:
    """Lower-cased, alias-resolved, de-duplicated, order-preserving.

    When ``normalize`` is supplied and returns nothing for an entry, that
    entry is DROPPED — a name no plugin answers to would otherwise sit in the
    chain and fail to resolve a client at download time. This mirrors the
    orchestrator's original chain building, which is where the behaviour was
    first established.

    ``drop_unknown=False`` keeps the raw name instead. Used for a legacy
    single-source ``mode``: silently replacing an unrecognised one would
    download from a source the user did not choose, where keeping it fails
    loudly with "client not available" exactly as it does today.
    """
    out: List[str] = []
    seen = set()
    for item in (raw or []):
        name = str(item or "").strip().lower()
        if not name:
            continue
        if normalize:
            canonical = normalize(name)
            if canonical:
                name = canonical
            elif drop_unknown:
                continue
        if name not in seen:
            seen.add(name)
            out.append(name)
    return out


_clean = clean_sources     # internal alias, kept for readability below


def resolve_chain(config_get: Optional[Callable] = None,
                  normalize: Optional[Callable] = None) -> List[str]:
    """The ordered download sources, most-preferred first.

    ``normalize`` maps a raw config value to its canonical plugin name (the
    registry's alias resolution), so a legacy ``deezer_dl`` entry still finds
    the ``deezer`` plugin. Omitted in tests that don't care.

    Never returns empty: an install with nothing configured gets the shipped
    default, because returning [] here would silently disable downloading.
    """
    get = _config_get(config_get)

    stored = _clean(get(SOURCES_KEY, None), normalize)
    if stored:
        return stored

    # ── legacy derivation ────────────────────────────────────────────────
    mode = str(get(LEGACY_MODE_KEY, "") or "").strip().lower()
    if mode and mode != "hybrid":
        # A single-source install. One entry, and nothing to fall back to —
        # which is exactly what single-source mode meant.
        single = _clean([mode], normalize, drop_unknown=False)
        if single:
            return single
    if not mode or mode == "hybrid":
        ordered = _clean(get(LEGACY_ORDER_KEY, None), normalize)
        if ordered:
            return ordered
        pair = _clean([get(LEGACY_PRIMARY_KEY, ""), get(LEGACY_SECONDARY_KEY, "")], normalize)
        if pair:
            return pair
    # The floor. `drop_unknown=False` matters here: a registry that recognises
    # none of the defaults (a stripped-down build, or a test double) would
    # otherwise leave this empty, and an empty chain means "no downloading at
    # all" — a far worse failure than naming a source that isn't installed.
    return _clean(DEFAULT_CHAIN, normalize, drop_unknown=False) or list(DEFAULT_CHAIN)


def is_multi_source(config_get: Optional[Callable] = None,
                    normalize: Optional[Callable] = None) -> bool:
    """Whether anything can be fallen back TO — the old ``mode == 'hybrid'``.

    Asked as a question about the chain rather than a stored flag, so a user
    who trims their list to one source gets single-source behaviour without
    also having to find and change a mode setting.
    """
    return len(resolve_chain(config_get, normalize)) > 1


def primary_source(config_get: Optional[Callable] = None,
                   normalize: Optional[Callable] = None) -> str:
    """The first source — the one allowed to claim a whole album bundle.

    Later entries stay per-track fallback: letting a fallback source grab an
    entire album would hand the release to a source the user ranked below
    the one that just failed a single track.
    """
    chain = resolve_chain(config_get, normalize)
    return chain[0] if chain else ""


def store_chain(sources, config_set: Optional[Callable] = None) -> List[str]:
    """Persist the chain, keeping the legacy keys consistent for one release.

    The legacy keys are still written because a downgrade reads them, and
    because consumers outside this module may not have been converted yet —
    leaving them stale would recreate the exact split-brain this module
    exists to remove.
    """
    if config_set is None:
        from config.settings import config_manager
        config_set = config_manager.set
    chain = _clean(sources)
    if not chain:
        logger.warning("Refusing to store an empty download source list; keeping the current one")
        return resolve_chain()
    config_set(SOURCES_KEY, chain)
    config_set(LEGACY_MODE_KEY, "hybrid" if len(chain) > 1 else chain[0])
    config_set(LEGACY_ORDER_KEY, chain)
    config_set(LEGACY_PRIMARY_KEY, chain[0])
    config_set(LEGACY_SECONDARY_KEY, chain[1] if len(chain) > 1 else chain[0])
    return chain


__all__ = [
    "DEFAULT_CHAIN",
    "SOURCES_KEY",
    "clean_sources",
    "is_multi_source",
    "primary_source",
    "resolve_chain",
    "store_chain",
]
