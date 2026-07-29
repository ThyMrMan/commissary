"""The search picker's configured-source lookup must work for a NON-admin.

Reported as: the Music Search page still shows unconnected sources for
standard/Plex users, after 1.8.9 hid them.

It hid them for admins only. The picker asked /api/settings/config-status, which
is @admin_only — so for a standard or Plex profile it returned 403, the client
fell through to its deliberately PERMISSIVE fallback (mark everything
configured, so a network blip cannot empty the row), and nothing was hidden.
Failing open is right for a network error and exactly wrong for a permission
error, and the client cannot tell those apart from a non-ok response.

Fixed with a purpose-built /api/search/source-status: readable by any signed-in
profile, restricted to the sources the picker renders, carrying nothing but
booleans. The Settings endpoint keeps its admin gate.

This file uses a REAL non-admin session rather than a client-side stand-in,
because the whole defect lives in what the SERVER returns to that profile — no
amount of overriding currentProfile in the page would have reproduced it.
"""

from __future__ import annotations

import os
import tempfile

import pytest

_TMP = tempfile.mkdtemp(prefix='soulsync-testdb-srcstatus-')
os.environ.setdefault('DATABASE_PATH', os.path.join(_TMP, 'srcstatus.db'))
os.environ.setdefault('SOULSYNC_TEST_DB_READY', '1')

web_server = pytest.importorskip('web_server')


@pytest.fixture
def client():
    return web_server.app.test_client()


@pytest.fixture
def nonadmin(client):
    pid = web_server.get_database().create_profile(name=f'u_{os.urandom(3).hex()}')
    with client.session_transaction() as sess:
        sess['profile_id'] = pid
    return pid


# ── the actual bug ───────────────────────────────────────────────────────────
def test_a_non_admin_can_read_the_search_source_status(client, nonadmin):
    """The one that was broken. A 403 here means the picker falls back to
    'everything is configured' and shows every source again."""
    r = client.get('/api/search/source-status')
    assert r.status_code == 200, r.get_data(as_text=True)
    assert isinstance(r.get_json(), dict)


def test_the_settings_endpoint_stays_admin_only(client, nonadmin):
    """The fix must not have been 'relax the Settings gate'."""
    assert client.get('/api/settings/config-status').status_code == 403


def test_an_admin_gets_the_same_shape(client):
    r = client.get('/api/search/source-status')
    assert r.status_code == 200
    body = r.get_json()
    assert '_experimental' in body


# ── it says what the picker needs and nothing more ───────────────────────────
def test_it_carries_only_booleans(client, nonadmin):
    """No keys, no URLs, no per-service settings — a member is being told which
    sources can answer a search, which the picker shows them anyway."""
    body = client.get('/api/search/source-status').get_json()
    for name, val in body.items():
        if name == '_experimental':
            continue
        assert isinstance(val, dict), name
        for k, v in val.items():
            assert isinstance(v, bool), '%s.%s is %r' % (name, k, type(v))


def test_it_is_scoped_to_the_pickers_own_sources(client, nonadmin):
    """Not a full copy of the Settings payload — it should not enumerate every
    configured service (notifications, servers, …) to a member."""
    body = client.get('/api/search/source-status').get_json()
    names = {k for k in body if k != '_experimental'}
    assert names <= set(web_server._SEARCH_PICKER_SOURCES)
    for leaked in ('plex', 'jellyfin', 'slskd', 'tidal', 'qobuz', 'lastfm'):
        assert leaked not in names, leaked


def test_spotify_still_reports_the_no_auth_case(client, nonadmin):
    """Spotify can serve metadata with no credentials when the opt-in no-creds
    source is on; the picker needs that distinction or it hides a usable source."""
    body = client.get('/api/search/source-status').get_json()
    if 'spotify' in body:
        assert 'metadata_available' in body['spotify']


# ── the client asks the right one ────────────────────────────────────────────
def test_the_picker_calls_the_unGated_endpoint():
    import pathlib
    js = (pathlib.Path(__file__).resolve().parents[1] / 'webui' / 'static'
          / 'shared-helpers.js').read_text(encoding='utf-8')
    fn = js.split('async function fetchSourceConfiguredMap(', 1)[1].split('\n}', 1)[0]
    assert "fetch('/api/search/source-status')" in fn
    # The important half: it must not be REQUESTING the admin-only one any more.
    # (A comment may still name it — that is the explanation of why not.)
    assert "fetch('/api/settings/config-status')" not in fn


def test_settings_page_still_uses_the_admin_endpoint():
    """Only the picker moved. The Settings → Connections indicator is admin-only
    UI and should keep reading the full payload."""
    import pathlib
    js = (pathlib.Path(__file__).resolve().parents[1] / 'webui' / 'static'
          / 'settings.js').read_text(encoding='utf-8')
    assert '/api/settings/config-status' in js
