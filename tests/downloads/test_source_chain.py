"""The download source chain — one ordered list replacing four config keys.

`download_source.mode` / `hybrid_order` / `hybrid_primary` / `hybrid_secondary`
all encoded the same thing, and every consumer re-derived it with its own
defaults — `master.py` defaulted mode to 'hybrid' while `album_bundle_dispatch`
defaulted it to 'soulseek', so one install could disagree with itself about
what was configured.

The migration is the risky part: an install that never opens Settings after
upgrading must behave EXACTLY as before, so most of this file pins the legacy
derivations rather than the new key.
"""

from __future__ import annotations

import pytest

from core.downloads.source_chain import (
    DEFAULT_CHAIN,
    is_multi_source,
    primary_source,
    resolve_chain,
    store_chain,
)


def cfg(**values):
    """A config_get over a plain dict."""
    return lambda key, default=None: values.get(key, default)


# ── the new key ──────────────────────────────────────────────────────────────
def test_the_stored_list_wins():
    get = cfg(**{"download_source.sources": ["torrent", "soulseek"],
                 "download_source.mode": "youtube"})     # stale legacy value
    assert resolve_chain(get) == ["torrent", "soulseek"]


def test_order_is_preserved_and_duplicates_dropped():
    get = cfg(**{"download_source.sources": ["Soulseek", "torrent", "soulseek", ""]})
    assert resolve_chain(get) == ["soulseek", "torrent"]


# ── legacy derivation: the upgrade path ──────────────────────────────────────
def test_legacy_single_source_becomes_a_one_entry_chain():
    """Single-source mode meant "this source, and nothing to fall back to".
    A one-entry chain says the same thing without a separate flag."""
    get = cfg(**{"download_source.mode": "youtube",
                 "download_source.hybrid_order": ["hifi", "soulseek"]})
    # hybrid_order is ignored while a single source is selected — matching the
    # old behaviour, where mode gated whether the order was consulted at all.
    assert resolve_chain(get) == ["youtube"]
    assert is_multi_source(get) is False


def test_legacy_hybrid_uses_the_order():
    get = cfg(**{"download_source.mode": "hybrid",
                 "download_source.hybrid_order": ["hifi", "youtube", "soulseek"]})
    assert resolve_chain(get) == ["hifi", "youtube", "soulseek"]
    assert is_multi_source(get) is True


def test_legacy_hybrid_falls_back_to_the_primary_secondary_pair():
    """Installs predating hybrid_order still have only the pair."""
    get = cfg(**{"download_source.mode": "hybrid",
                 "download_source.hybrid_primary": "soulseek",
                 "download_source.hybrid_secondary": "youtube"})
    assert resolve_chain(get) == ["soulseek", "youtube"]


def test_a_pair_naming_the_same_source_twice_collapses():
    get = cfg(**{"download_source.mode": "hybrid",
                 "download_source.hybrid_primary": "soulseek",
                 "download_source.hybrid_secondary": "soulseek"})
    assert resolve_chain(get) == ["soulseek"]
    assert is_multi_source(get) is False


def test_nothing_configured_gets_the_shipped_default():
    """Returning [] would silently disable downloading entirely."""
    assert resolve_chain(cfg()) == list(DEFAULT_CHAIN)


def test_names_no_plugin_answers_to_are_dropped():
    """Caught by the pre-existing orchestrator tests, not by this file's first
    draft: a name left in the chain resolves no client and fails at download
    time. The orchestrator's original chain builder dropped them, so this has
    to as well."""
    get = cfg(**{"download_source.sources": ["nonsense", "soulseek", "also_fake"]})
    known = {"soulseek", "youtube"}
    assert resolve_chain(get, normalize=lambda n: n if n in known else "") == ["soulseek"]


def test_an_unrecognised_single_source_is_kept_not_replaced():
    """The opposite call, deliberately: silently substituting the default
    chain would download from sources the user never chose. Keeping the name
    fails loudly with "client not available", which is today's behaviour."""
    get = cfg(**{"download_source.mode": "some_removed_plugin"})
    assert resolve_chain(get, normalize=lambda n: "") == ["some_removed_plugin"]


def test_the_chain_is_never_empty_even_if_nothing_normalizes():
    """An empty chain means no downloading at all — a worse failure than
    naming a source that isn't installed."""
    assert resolve_chain(cfg(), normalize=lambda n: "") == list(DEFAULT_CHAIN)


def test_aliases_are_normalized():
    """A legacy `deezer_dl` value has to find the canonical `deezer` plugin."""
    get = cfg(**{"download_source.sources": ["deezer_dl", "soulseek"]})
    chain = resolve_chain(get, normalize=lambda n: "deezer" if n == "deezer_dl" else n)
    assert chain == ["deezer", "soulseek"]


# ── what the consumers actually ask ──────────────────────────────────────────
def test_primary_source_is_the_album_bundle_claimant():
    get = cfg(**{"download_source.sources": ["torrent", "soulseek"]})
    assert primary_source(get) == "torrent"


def test_primary_source_of_a_single_entry_chain_is_that_entry():
    assert primary_source(cfg(**{"download_source.mode": "soulseek"})) == "soulseek"


def test_is_multi_source_is_the_old_hybrid_question():
    assert is_multi_source(cfg(**{"download_source.sources": ["a", "b"]})) is True
    assert is_multi_source(cfg(**{"download_source.sources": ["a"]})) is False


# ── storing ──────────────────────────────────────────────────────────────────
def test_storing_keeps_the_legacy_keys_consistent():
    """A downgrade reads the old keys, and any consumer not yet converted
    still reads them — leaving them stale would recreate the split-brain."""
    written = {}
    store_chain(["torrent", "soulseek"], config_set=written.__setitem__)
    assert written["download_source.sources"] == ["torrent", "soulseek"]
    assert written["download_source.mode"] == "hybrid"
    assert written["download_source.hybrid_order"] == ["torrent", "soulseek"]
    assert written["download_source.hybrid_primary"] == "torrent"
    assert written["download_source.hybrid_secondary"] == "soulseek"


def test_storing_one_source_writes_it_as_the_mode():
    written = {}
    store_chain(["youtube"], config_set=written.__setitem__)
    assert written["download_source.mode"] == "youtube"
    assert written["download_source.hybrid_secondary"] == "youtube"


def test_storing_an_empty_list_is_refused(monkeypatch):
    """An empty chain would disable downloading; a UI bug must not be able
    to do that silently."""
    written = {}
    monkeypatch.setattr("core.downloads.source_chain.resolve_chain",
                        lambda *a, **k: ["soulseek"])
    assert store_chain([], config_set=written.__setitem__) == ["soulseek"]
    assert written == {}
