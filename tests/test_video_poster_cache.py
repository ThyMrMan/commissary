"""Video artwork is served through the on-disk image cache.

Every poster/backdrop/still request used to make a fresh blocking call to
Plex/TMDB — ``requests.get(..., timeout=15, stream=True)`` per image, with no
store behind it. gunicorn runs one worker with eight threads, so a library grid
on a cold browser cache could occupy the whole pool and stall unrelated API
requests behind it. It also sent ``max-age`` with no validator, so once a
browser's copy aged out every image was refetched in full.

``core.image_cache`` already existed and was already used by the music side; the
video proxy simply wasn't wired to it. It stores bytes on disk, dedupes
concurrent requests for one key behind a per-key lock, and serves a stale copy
when a refresh fails — so a Plex restart mid-scroll shows art rather than holes.
"""

from __future__ import annotations

from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parent.parent
_POSTER_PY = (_ROOT / 'api' / 'video' / 'poster.py').read_text(encoding='utf-8')


def test_the_proxy_actually_goes_through_the_cache():
    """Source guard: the whole point is that this file stopped hitting upstream
    directly. It previously contained zero references to the cache."""
    assert 'from core.image_cache import get_image_cache' in _POSTER_PY
    assert 'get_image_cache().get_url(' in _POSTER_PY


def test_both_upstream_paths_are_cached():
    """There are two: a full external URL (TMDB) and a media-server URL built
    with credentials in the query string (Plex/Jellyfin). Missing either leaves
    half the artwork uncached."""
    assert _POSTER_PY.count('_serve_cached(') >= 3     # definition + both call sites


def test_a_cache_failure_falls_back_to_streaming_not_an_error():
    """Artwork must never be the reason a page errors."""
    assert 'return None' in _POSTER_PY
    assert 'fall back to the live stream' in _POSTER_PY


def test_cached_art_carries_a_validator():
    """max-age alone meant a full refetch the moment the browser copy aged out;
    an ETag turns that into a 304."""
    assert 'resp.headers["ETag"]' in _POSTER_PY
    assert 'conditional=True' in _POSTER_PY


def test_serve_cached_returns_none_rather_than_raising(monkeypatch):
    """The fallback contract, exercised for real: a cache that blows up must
    yield None so the caller streams live."""
    import api.video.poster as poster

    class _Boom:
        def get_url(self, url):
            raise RuntimeError("cache is on fire")

    monkeypatch.setattr('core.image_cache.get_image_cache', lambda: _Boom())
    assert poster._serve_cached('https://image.tmdb.org/t/p/w500/x.jpg') is None


def test_serve_cached_folds_params_into_the_cache_key(monkeypatch):
    """Plex art needs its token/size in the URL. If params were dropped, every
    size of the same poster would collide on one cache entry."""
    import api.video.poster as poster
    seen = {}

    class _Cache:
        def get_url(self, url):
            seen['url'] = url
            raise RuntimeError("stop here — we only care about the key")

    monkeypatch.setattr('core.image_cache.get_image_cache', lambda: _Cache())
    poster._serve_cached('http://plex.local:32400/photo', {'w': 300, 'X-Plex-Token': 'tok'})
    assert 'w=300' in seen['url'] and 'X-Plex-Token=tok' in seen['url']

    # ...and a URL that already has a query string keeps it
    poster._serve_cached('http://plex.local:32400/photo?a=1', {'b': '2'})
    assert 'a=1' in seen['url'] and 'b=2' in seen['url']


def test_internal_media_server_hosts_are_fetchable():
    """The cache's host check must permit LAN/Docker hosts — Plex and Jellyfin
    artwork is almost always behind one, so a public-only allowlist would mean
    the cache silently never worked for the common case."""
    from core.image_cache import ImageCache
    c = ImageCache.__new__(ImageCache)
    assert c._is_fetch_allowed('http://192.168.1.50:32400/photo') is True
    assert c._is_fetch_allowed('http://plex:32400/photo') is True
    assert c._is_fetch_allowed('https://image.tmdb.org/t/p/w500/x.jpg') is True
    # ...but not credential-bearing or non-http URLs
    assert c._is_fetch_allowed('http://user:pw@host/x.jpg') is False
    assert c._is_fetch_allowed('file:///etc/passwd') is False
