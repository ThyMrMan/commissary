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
    chain_from_payload,
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
    assert written["download_source.sources"] == ["youtube"]
    assert written["download_source.mode"] == "youtube"


def test_storing_one_source_leaves_the_multi_source_keys_alone():
    """It used to write hybrid_order/primary/secondary as ["youtube"] too.
    Harmless while the sync was a no-op; the moment a save actually took
    effect, picking "YouTube Only" would have flattened the hybrid list the
    user had ordered, and switching back to Hybrid would show one entry.

    The legacy derivation never reads those keys while `mode` names a single
    source, so leaving them is invisible to every reader and keeps the list
    for the trip back."""
    written = {}
    store_chain(["youtube"], config_set=written.__setitem__)
    assert "download_source.hybrid_order" not in written
    assert "download_source.hybrid_primary" not in written
    assert "download_source.hybrid_secondary" not in written


def test_the_list_survives_a_round_trip_through_a_single_source():
    store = {}
    get = lambda key, default=None: store.get(key, default)     # noqa: E731

    def save(payload):
        for leaf, value in payload.items():
            store["download_source." + leaf] = value
        store_chain(chain_from_payload(payload, get), store.__setitem__)

    ordered = ["tidal", "soulseek", "youtube"]
    save({"mode": "hybrid", "hybrid_order": list(ordered)})
    save({"mode": "soulseek", "hybrid_order": list(ordered)})
    assert resolve_chain(get) == ["soulseek"]
    save({"mode": "hybrid", "hybrid_order": list(ordered)})
    assert resolve_chain(get) == ordered


def test_storing_an_empty_list_is_refused(monkeypatch):
    """An empty chain would disable downloading; a UI bug must not be able
    to do that silently."""
    written = {}
    monkeypatch.setattr("core.downloads.source_chain.resolve_chain",
                        lambda *a, **k: ["soulseek"])
    assert store_chain([], config_set=written.__setitem__) == ["soulseek"]
    assert written == {}

# ── deriving a WRITE from the write, not from what is already stored ────────
#
# "Music download settings aren't saving — rearrange the sources, hit save,
# navigate to another tab and it's back the way it was."
#
# The settings POST wrote `hybrid_order` and then called
# `store_chain(resolve_chain(get))` to keep the collapsed list in step.
# resolve_chain consults `sources` FIRST — correct when the question is "what
# is configured?", and exactly wrong here, because `sources` held the PREVIOUS
# chain and got written straight back over the keys the same request had just
# written. The first save after upgrading stuck (nothing was stored yet); every
# save after it was silently reverted to that first one.

def test_a_posted_order_beats_the_stored_chain():
    """THE bug. Both are present; the one the request just sent must win."""
    get = cfg(**{"download_source.sources": ["hifi", "youtube", "soulseek"],
                 "download_source.mode": "hybrid"})
    posted = {"mode": "hybrid", "hybrid_order": ["soulseek", "hifi", "youtube"]}
    assert chain_from_payload(posted, get) == ["soulseek", "hifi", "youtube"]


def test_the_second_save_is_not_reverted_to_the_first():
    """The reported arc, replayed: the batch write followed by the sync, twice.
    Before, save #2 came back as save #1's value."""
    store = {}
    get = lambda key, default=None: store.get(key, default)     # noqa: E731

    def save(payload):
        for leaf, value in payload.items():
            store["download_source." + leaf] = value            # the batch write
        store_chain(chain_from_payload(payload, get), store.__setitem__)

    save({"mode": "hybrid", "hybrid_order": ["soulseek", "youtube"]})
    assert resolve_chain(get) == ["soulseek", "youtube"]

    save({"mode": "hybrid", "hybrid_order": ["youtube", "soulseek"]})
    assert resolve_chain(get) == ["youtube", "soulseek"]

    save({"mode": "hybrid", "hybrid_order": ["youtube", "soulseek", "tidal"]})
    assert resolve_chain(get) == ["youtube", "soulseek", "tidal"]


def test_a_posted_single_source_mode_beats_the_stored_chain():
    """The same shadowing flipped "Soulseek Only" back to "Hybrid" on save."""
    get = cfg(**{"download_source.sources": ["hifi", "youtube", "soulseek"]})
    posted = {"mode": "soulseek", "hybrid_order": ["hifi", "youtube", "soulseek"]}
    assert chain_from_payload(posted, get) == ["soulseek"]


def test_a_posted_sources_list_wins_outright():
    """A caller that already speaks the collapsed form is taken at its word."""
    get = cfg(**{"download_source.sources": ["hifi"],
                 "download_source.hybrid_order": ["youtube"]})
    assert chain_from_payload({"sources": ["torrent", "usenet"]}, get) == ["torrent", "usenet"]


def test_a_key_the_payload_did_not_name_still_reads_from_config():
    """The sidebar's quick-switch modal posts ONLY `hybrid_order`; its mode has
    to come from the stored config for the derivation to reach the order at
    all. Only the stored COLLAPSED list is suppressed, not the whole config."""
    get = cfg(**{"download_source.sources": ["hifi", "youtube"],
                 "download_source.mode": "hybrid"})
    assert chain_from_payload({"hybrid_order": ["tidal", "qobuz"]}, get) == ["tidal", "qobuz"]


def test_precedence_inside_the_payload_is_the_ordinary_one():
    """This re-points resolve_chain's reads and changes nothing else: a
    single-source mode still beats an order, exactly as a stored one does."""
    posted = {"mode": "youtube", "hybrid_order": ["soulseek", "tidal"]}
    assert chain_from_payload(posted, cfg()) == ["youtube"]
    stored = cfg(**{"download_source.mode": "youtube",
                    "download_source.hybrid_order": ["soulseek", "tidal"]})
    assert resolve_chain(stored) == chain_from_payload(posted, cfg())


def test_a_payload_naming_no_source_key_leaves_the_chain_alone():
    """Saving the concurrency box must not restate — or disturb — the chain."""
    get = cfg(**{"download_source.sources": ["hifi", "youtube"]})
    assert chain_from_payload({"max_concurrent": 5}, get) == ["hifi", "youtube"]
    assert chain_from_payload({}, get) == ["hifi", "youtube"]


def test_a_payload_that_is_not_a_dict_falls_back_to_the_stored_chain():
    get = cfg(**{"download_source.sources": ["hifi", "youtube"]})
    assert chain_from_payload(None, get) == ["hifi", "youtube"]
    assert chain_from_payload("hifi", get) == ["hifi", "youtube"]


def test_an_empty_posted_order_does_not_erase_the_chain():
    """`getHybridOrder()` returns [] when every source is toggled off. The
    floor in store_chain refuses that, but the derivation must not pretend the
    user asked for nothing either — it falls through to the legacy pair."""
    get = cfg(**{"download_source.sources": ["hifi", "youtube"],
                 "download_source.hybrid_primary": "soulseek",
                 "download_source.hybrid_secondary": "tidal"})
    assert chain_from_payload({"mode": "hybrid", "hybrid_order": []}, get) == ["soulseek", "tidal"]


def test_the_payload_derivation_normalizes_like_the_stored_one():
    posted = {"mode": "hybrid", "hybrid_order": ["deezer_dl", "soulseek"]}
    chain = chain_from_payload(posted, cfg(),
                               normalize=lambda n: "deezer" if n == "deezer_dl" else n)
    assert chain == ["deezer", "soulseek"]

