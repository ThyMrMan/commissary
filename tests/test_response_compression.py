"""HTTP response compression + conditional caching for the app shell.

Nothing was compressed before: index.html shipped 1.02 MB raw and style.css
1.94 MB. Measured on the wire, the text assets a page load pulls went from
3.90 MB to 0.64 MB (6.1x) once gzip was on.

The interesting part of this feature is everything it must NOT compress. Two
cases would be outright bugs rather than missed savings:

  * ``text/event-stream`` — SSE responses are open-ended generators. Reading the
    body to compress it blocks until the stream ends, i.e. never. It matches the
    ``text/`` prefix, so it has to be excluded by name.
  * unknown-length streamed responses — the artwork proxy hands back
    ``Response(iter_content(...))``; buffering it would defeat the streaming and
    hold a whole image in memory.

Static files ARE ``direct_passthrough`` but have a known Content-Length and a
real file behind them, so they're deliberately materialised and compressed —
they're most of the payload, and skipping them missed nearly all of the win the
first time round.
"""

from __future__ import annotations

import gzip
import os
import tempfile
from pathlib import Path

import pytest
from flask import Flask, Response

_TMP = tempfile.mkdtemp(prefix='soulsync-testdb-compress-')
os.environ.setdefault('DATABASE_PATH', os.path.join(_TMP, 'c.db'))
os.environ.setdefault('SOULSYNC_TEST_DB_READY', '1')

web_server = pytest.importorskip('web_server')

_ROOT = Path(__file__).resolve().parent.parent
_INDEX = (_ROOT / 'webui' / 'index.html').read_text(encoding='utf-8')

_GZIP = {'Accept-Encoding': 'gzip'}
_NONE = {'Accept-Encoding': 'identity'}


@pytest.fixture()
def client():
    return web_server.app.test_client()


# ── what gets compressed ─────────────────────────────────────────────────────
def test_static_css_and_js_are_compressed(client):
    """The bulk of the payload. These are direct_passthrough responses, which an
    earlier version of the guard skipped entirely — the whole point of the
    change was lost until this was covered."""
    for path in ('/static/style.css', '/static/discover.js'):
        r = client.get(path, headers=_GZIP)
        assert r.status_code == 200, path
        assert r.headers.get('Content-Encoding') == 'gzip', path
        raw = gzip.decompress(r.data)
        assert len(raw) > len(r.data) * 2, path        # a real saving, not noise
        assert 'Accept-Encoding' in r.headers.get('Vary', '')


def test_the_app_shell_is_compressed(client):
    r = client.get('/', headers=_GZIP)
    assert r.headers.get('Content-Encoding') == 'gzip'
    assert len(gzip.decompress(r.data)) > len(r.data) * 2


def test_a_client_that_does_not_ask_gets_plain_bytes(client):
    r = client.get('/static/style.css', headers=_NONE)
    assert r.headers.get('Content-Encoding') is None
    # Vary is still declared: the same URL is served both ways, so a shared
    # cache has to key on Accept-Encoding regardless of this response.
    assert 'Accept-Encoding' in r.headers.get('Vary', '')


# ── what must NOT be compressed ──────────────────────────────────────────────
def test_images_are_left_alone(client):
    r = client.get('/static/trans2.png', headers=_GZIP)
    assert r.status_code == 200
    assert r.headers.get('Content-Encoding') is None


def _mini_app(build):
    """A tiny app carrying only the real compressor, so a hostile response shape
    can be exercised without standing up the whole server."""
    app = Flask(__name__)
    app.after_request(web_server._compress_response)
    app.add_url_rule('/x', 'x', build)
    return app.test_client()


def test_an_sse_stream_is_never_buffered():
    """The bug this guards: text/event-stream matches the 'text/' prefix, and its
    body is an endless generator — compressing it would hang the request forever
    rather than merely wasting CPU."""
    consumed = {'n': 0}

    def gen():
        for i in range(3):
            consumed['n'] += 1
            yield 'data: %d\n\n' % i

    # Call the compressor DIRECTLY. Going through the test client would consume
    # the generator itself when it materialises the body, which says nothing
    # about whether the compressor touched it.
    app = Flask(__name__)
    with app.test_request_context('/x', headers=_GZIP):
        resp = Response(gen(), mimetype='text/event-stream')
        out = web_server._compress_response(resp)
        assert out.headers.get('Content-Encoding') is None
        assert consumed['n'] == 0, "the compressor consumed the SSE stream"
    assert out.is_streamed, "the response must still be streaming"


def test_an_unknown_length_stream_is_left_streaming():
    """The artwork proxy's shape: Response(iter_content(...)) with no length."""
    def build():
        def gen():
            yield b'x' * 4096
        return Response(gen(), content_type='text/plain')

    r = _mini_app(build).get('/x', headers=_GZIP)
    assert r.headers.get('Content-Encoding') is None


def test_partial_content_is_left_alone():
    def build():
        resp = Response(b'y' * 4096, status=206, content_type='text/plain')
        resp.headers['Content-Range'] = 'bytes 0-4095/999999'
        return resp

    r = _mini_app(build).get('/x', headers=_GZIP)
    assert r.status_code == 206
    assert r.headers.get('Content-Encoding') is None


def test_tiny_bodies_are_not_worth_it():
    r = _mini_app(lambda: Response('ok', content_type='text/plain')).get('/x', headers=_GZIP)
    assert r.headers.get('Content-Encoding') is None


def test_an_already_encoded_body_is_not_double_compressed():
    def build():
        resp = Response(gzip.compress(b'z' * 8192), content_type='text/plain')
        resp.headers['Content-Encoding'] = 'gzip'
        return resp

    r = _mini_app(build).get('/x', headers=_GZIP)
    assert r.headers.get('Content-Encoding') == 'gzip'
    assert gzip.decompress(r.data) == b'z' * 8192      # exactly one layer


def test_a_strong_etag_is_weakened_when_the_body_is_transformed():
    """A strong ETag identifies specific bytes; after gzip the bytes differ, so
    keeping it strong would be a lie to any cache doing byte-range work."""
    def build():
        resp = Response('a' * 8192, content_type='text/plain')
        resp.set_etag('constant')
        return resp

    r = _mini_app(build).get('/x', headers=_GZIP)
    assert r.headers.get('Content-Encoding') == 'gzip'
    assert r.headers['ETag'].startswith('W/')


# ── the app shell revalidates instead of re-downloading ──────────────────────
def test_the_shell_sends_a_validator_and_can_304(client):
    """1.02 MB of markup was refetched in full on every single visit."""
    first = client.get('/', headers=_GZIP)
    assert first.status_code == 200
    assert first.headers.get('Cache-Control') == 'no-cache'
    etag = first.headers.get('ETag')
    assert etag

    again = client.get('/', headers={**_GZIP, 'If-None-Match': etag})
    assert again.status_code == 304
    assert not again.data


# ── offscreen images defer ───────────────────────────────────────────────────
def test_offscreen_images_are_lazy_but_the_shell_chrome_is_not():
    """69 img tags, only 18 visible at load, none deferred. The sidebar logo and
    the first header icons stay eager so nothing above the fold is delayed."""
    assert _INDEX.count('loading="lazy"') >= 50
    head = _INDEX.split('\n')[:600]
    assert not any('loading="lazy"' in ln for ln in head), \
        "above-the-fold chrome should stay eager"
    # the sidebar logo specifically
    logo = next(ln for ln in head if 'sidebar-logo' in ln)
    assert 'loading=' not in logo
