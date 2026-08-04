"""Pin the structural conformance of every download source plugin
class to ``DownloadSourcePlugin``.

Each registered source class MUST:
- Implement every protocol method by name.
- Mark async methods as `async def` so the orchestrator can `await`
  them uniformly.

When someone adds a new source (e.g. Usenet) and forgets one of
these methods, this test fails at the contract — long before the
first real download attempt would have raised AttributeError in
production. When someone CHANGES the contract (adds a method to
the protocol), this test forces every existing source to be
updated.

Catches the smell that motivated the refactor in the first place:
8 sources independently grew the same shape because every
consumer site needed the same calls, but nothing enforced parity.

NOTE on test design: these tests check CLASSES, not instances.
Instantiating real client classes (TidalDownloadClient, etc.) at
fixture setup pollutes module-level state in tidalapi / spotipy
imports and breaks downstream tests that rely on a clean import
graph. Class-level checks are equally strict for structural
conformance — the protocol only constrains the method surface, not
runtime instance behavior.
"""

from __future__ import annotations

import inspect

import pytest


REQUIRED_SYNC_METHODS = {'is_configured'}

REQUIRED_ASYNC_METHODS = {
    'check_connection',
    'search',
    'download',
    'get_all_downloads',
    'get_download_status',
    'cancel_download',
    'clear_all_completed_downloads',
}


def _import_plugin_classes():
    """Import every download source class lazily inside the test
    rather than at module load — avoids dragging tidalapi /
    spotipy / yt-dlp imports into every other test module's
    collection phase."""
    from core.soulseek_client import SoulseekClient
    from core.youtube_client import YouTubeClient
    from core.tidal_download_client import TidalDownloadClient
    from core.qobuz_client import QobuzClient
    from core.hifi_client import HiFiClient
    from core.deezer_download_client import DeezerDownloadClient
    from core.lidarr_download_client import LidarrDownloadClient
    from core.soundcloud_client import SoundcloudClient
    from core.amazon_download_client import AmazonDownloadClient

    return {
        'soulseek': SoulseekClient,
        'youtube': YouTubeClient,
        'tidal': TidalDownloadClient,
        'qobuz': QobuzClient,
        'hifi': HiFiClient,
        'deezer': DeezerDownloadClient,
        'lidarr': LidarrDownloadClient,
        'soundcloud': SoundcloudClient,
        'amazon': AmazonDownloadClient,
    }


def test_default_registry_registers_all_sources():
    """Smoke check that the foundation registry knows about every
    source the orchestrator historically dispatched to. If someone
    drops a registration here, every other test in this module would
    silently miss the missing source."""
    from core.download_plugins.registry import build_default_registry

    registry = build_default_registry()
    expected = {
        'soulseek', 'youtube', 'tidal', 'qobuz',
        'hifi', 'deezer', 'lidarr', 'soundcloud', 'amazon',
        'torrent', 'usenet',
    }
    assert set(registry.names()) == expected


def test_only_prowlarr_backed_sources_are_release_level():
    """Torrent and usenet search at the RELEASE level — one indexer hit per
    album, not per track. The picker groups and explains those separately, so
    the flag has to live on the registry entry rather than being re-derived
    (and eventually forgotten) at each consumer."""
    from core.download_plugins.registry import build_default_registry

    registry = build_default_registry()
    release_level = {n for n in registry.names() if registry.is_release_level(n)}
    assert release_level == {'torrent', 'usenet'}


def test_is_release_level_is_false_for_names_it_has_never_heard_of():
    """A stream can name a source the registry doesn't have. Guessing "maybe
    it's a release source" would silently bury its results under the
    whole-album heading."""
    from core.download_plugins.registry import build_default_registry

    registry = build_default_registry()
    assert registry.is_release_level('not_a_source') is False
    assert registry.is_release_level('') is False


def test_searchable_plugins_is_not_narrowed_by_the_dispatch_chain():
    """``searchable_plugins`` answers "what can the user look in?" and must
    stay independent of ``download_source.mode`` / ``hybrid_order``, which
    only order the unattended download cascade. Pinning it to
    ``configured_plugins`` is the whole contract: connected == searchable."""
    from core.download_plugins.registry import (
        DownloadPluginRegistry, PluginSpec,
    )

    class _Stub:
        def __init__(self, configured):
            self._configured = configured

        def is_configured(self):
            return self._configured

    registry = DownloadPluginRegistry()
    for name, configured in (('alpha', True), ('beta', False), ('gamma', True)):
        registry.register(PluginSpec(name=name,
                                     factory=lambda c=configured: _Stub(c),
                                     display_name=name.title()))
    registry.initialize()

    assert {n for n, _ in registry.searchable_plugins()} == {'alpha', 'gamma'}
    assert {n for n, _ in registry.searchable_plugins()} == \
           {n for n, _ in registry.configured_plugins()}


def test_deezer_dl_alias_is_registered_against_deezer_spec():
    """Legacy ``deezer_dl`` source-name string used in config + per-
    source dispatch must keep resolving — frontend, settings,
    download_orchestrator's username dispatch all depend on it."""
    from core.download_plugins.registry import build_default_registry

    registry = build_default_registry()
    spec = registry.get_spec('deezer_dl')
    assert spec is not None
    assert spec.name == 'deezer'
    assert 'deezer_dl' in spec.aliases


@pytest.mark.parametrize('plugin_name', [
    'soulseek', 'youtube', 'tidal', 'qobuz',
    'hifi', 'deezer', 'lidarr', 'soundcloud', 'amazon',
])
def test_plugin_class_has_all_required_methods(plugin_name):
    """Every registered plugin class exposes every protocol method
    by name. Diagnostic-friendly: tells you WHICH method is missing
    when a new source is added without all the required methods."""
    classes = _import_plugin_classes()
    cls = classes[plugin_name]

    missing = []
    for method_name in REQUIRED_SYNC_METHODS | REQUIRED_ASYNC_METHODS:
        if not hasattr(cls, method_name):
            missing.append(method_name)
    assert not missing, (
        f"{plugin_name} ({cls.__name__}) missing methods: {missing}"
    )


@pytest.mark.parametrize('plugin_name', [
    'soulseek', 'youtube', 'tidal', 'qobuz',
    'hifi', 'deezer', 'lidarr', 'soundcloud', 'amazon',
])
def test_plugin_class_async_methods_are_coroutines(plugin_name):
    """Methods declared async in the protocol must be async on every
    plugin class. A sync `download()` would silently skip the
    orchestrator's `await` and return a coroutine object instead of
    a download_id — the kind of bug that only surfaces at runtime
    against a live user."""
    classes = _import_plugin_classes()
    cls = classes[plugin_name]

    not_async = []
    for method_name in REQUIRED_ASYNC_METHODS:
        method = getattr(cls, method_name, None)
        if method is None:
            continue
        if not inspect.iscoroutinefunction(method):
            not_async.append(method_name)
    assert not not_async, (
        f"{plugin_name} ({cls.__name__}) declared these methods as "
        f"sync but the protocol requires async: {not_async}"
    )


def test_orchestrator_uses_registry_for_dispatch():
    """The orchestrator must hold a registry reference and the generic
    ``client(name)`` accessor must return the same instances the
    registry holds. Per-source attribute aliases (``orchestrator.soulseek``
    etc.) were removed in favor of ``orchestrator.client('soulseek')``;
    the legacy alias name (``deezer_dl``) still resolves to the canonical
    deezer plugin via the registry's alias map."""
    from core.download_orchestrator import DownloadOrchestrator

    orchestrator = DownloadOrchestrator()
    assert hasattr(orchestrator, 'registry')
    assert orchestrator.client('soulseek') is orchestrator.registry.get('soulseek')
    assert orchestrator.client('youtube') is orchestrator.registry.get('youtube')
    assert orchestrator.client('deezer_dl') is orchestrator.registry.get('deezer')
    assert orchestrator.client('lidarr') is orchestrator.registry.get('lidarr')
