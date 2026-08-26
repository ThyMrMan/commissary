"""The download source list, saved for real (real app, real HTTP, real config).

Reported as: "Music download settings aren't saving — after rearranging or
modifying sources and hitting save, navigating to another tab resets it."

The save was reaching the server. The server then undid it.

``POST /api/settings`` writes every posted key, then syncs the collapsed
``download_source.sources`` list that every consumer resolves through. That
sync read the chain via ``resolve_chain``, which consults ``sources`` FIRST —
the right precedence for "what is configured?", and precisely the wrong one
here, because ``sources`` held the PREVIOUS chain and was written straight back
over the keys this very request had just written. The first save after
upgrading stuck, because nothing was stored yet. Every save after it was
reverted to that first one, and ``GET /api/settings`` on the next page load
served the reverted value — which is the "another tab resets it" half.

The same shadowing had two more faces:

  * ``POST /api/profiles/active-sources`` (the sidebar quick-switch modal)
    writes only the legacy keys, so the stale collapsed list kept answering and
    reordering there changed the modal's own display and nothing whatsoever
    about where music was downloaded from.
  * Choosing a single source in Settings was flipped back to "Hybrid" on save,
    because the stored multi-source chain answered for the posted ``mode``.

These drive the real endpoints rather than the derivation, because the
derivation was never wrong — the wiring around it was, and only an actual
request proves the wiring.

Heavy (imports web_server once), so isolated in its own module, following
tests/test_credentials_endpoints.py.
"""

from __future__ import annotations

import os
import tempfile

import pytest

# Redirect the DB before importing web_server so it never touches a real library.
_TMP = tempfile.mkdtemp(prefix='soulsync-testdb-dlsrc-')
os.environ['DATABASE_PATH'] = os.path.join(_TMP, 'dlsrc_ep.db')
os.environ['SOULSYNC_TEST_DB_READY'] = '1'

web_server = pytest.importorskip('web_server')

from config.settings import config_manager                       # noqa: E402
from core.downloads.source_chain import resolve_chain            # noqa: E402

_KEYS = ['download_source.sources', 'download_source.mode',
         'download_source.hybrid_order', 'download_source.hybrid_primary',
         'download_source.hybrid_secondary']


@pytest.fixture
def client():
    """A test client with the download-source config restored afterwards, so
    these tests can't leak a chain into the rest of the session."""
    saved = {k: config_manager.get(k) for k in _KEYS}
    yield web_server.app.test_client()
    for k, v in saved.items():
        config_manager.set(k, v)


def _save(client, **download_source):
    r = client.post('/api/settings', json={'download_source': download_source})
    assert r.status_code == 200, r.get_data(as_text=True)
    return r.get_json()


def _reload(client):
    """What the Settings page reads when you navigate back to it."""
    body = client.get('/api/settings').get_json()
    return body['download_source']


# ── the reported bug ─────────────────────────────────────────────────────────

def test_rearranging_the_sources_a_second_time_actually_saves(client):
    """The exact arc: save an order, save a different one, come back to the
    page. The second save used to come back as the first."""
    _save(client, mode='hybrid', hybrid_order=['soulseek', 'youtube'])
    assert _reload(client)['hybrid_order'] == ['soulseek', 'youtube']

    _save(client, mode='hybrid', hybrid_order=['youtube', 'soulseek'])
    assert _reload(client)['hybrid_order'] == ['youtube', 'soulseek']


def test_adding_a_source_actually_saves(client):
    """"...or modifying sources". Same defect, arriving as a longer list."""
    _save(client, mode='hybrid', hybrid_order=['soulseek', 'youtube'])
    _save(client, mode='hybrid', hybrid_order=['soulseek', 'youtube', 'tidal'])
    assert _reload(client)['hybrid_order'] == ['soulseek', 'youtube', 'tidal']


def test_the_saved_order_is_the_one_downloads_resolve(client):
    """The page agreeing with itself is not enough — the collapsed list is what
    the orchestrator reads, and it is what was going stale."""
    _save(client, mode='hybrid', hybrid_order=['soulseek', 'youtube'])
    _save(client, mode='hybrid', hybrid_order=['tidal', 'qobuz', 'soulseek'])
    assert resolve_chain(config_manager.get) == ['tidal', 'qobuz', 'soulseek']


def test_choosing_a_single_source_is_not_flipped_back_to_hybrid(client):
    _save(client, mode='hybrid', hybrid_order=['hifi', 'youtube', 'soulseek'])
    _save(client, mode='soulseek', hybrid_order=['hifi', 'youtube', 'soulseek'])
    assert _reload(client)['mode'] == 'soulseek'
    assert resolve_chain(config_manager.get) == ['soulseek']


def test_a_trip_through_a_single_source_does_not_flatten_the_list(client):
    """Picking "Soulseek Only" must not cost you the order you arranged — the
    legacy list is not read while the mode names one source, so there is no
    reason to overwrite it and every reason not to."""
    ordered = ['hifi', 'youtube', 'soulseek']
    _save(client, mode='hybrid', hybrid_order=list(ordered))
    _save(client, mode='soulseek', hybrid_order=list(ordered))
    assert _reload(client)['hybrid_order'] == ordered

    _save(client, mode='hybrid', hybrid_order=list(ordered))
    assert resolve_chain(config_manager.get) == ordered


def test_a_save_that_says_nothing_about_sources_leaves_the_chain_alone(client):
    """Most saves are somebody editing an unrelated box. The sync must be a
    no-op for them, not a restatement of a value nobody sent."""
    _save(client, mode='hybrid', hybrid_order=['tidal', 'qobuz'])
    before = resolve_chain(config_manager.get)
    r = client.post('/api/settings', json={'download_source': {'max_concurrent': 4}})
    assert r.status_code == 200
    assert resolve_chain(config_manager.get) == before
    assert _reload(client)['hybrid_order'] == ['tidal', 'qobuz']


# ── the same defect, seen from the sidebar quick-switch modal ────────────────

def test_the_quick_switch_reorder_reaches_what_downloads_use(client):
    """It wrote `hybrid_order` and nothing else, so the stale collapsed list
    kept answering: the modal redrew in the new order and downloads carried on
    using the old one. Nothing in the UI said so."""
    _save(client, mode='hybrid', hybrid_order=['soulseek', 'youtube', 'tidal'])
    r = client.post('/api/profiles/active-sources',
                    json={'hybrid_order': ['tidal', 'soulseek', 'youtube']})
    assert r.status_code == 200 and r.get_json()['success']
    assert resolve_chain(config_manager.get) == ['tidal', 'soulseek', 'youtube']


def test_the_quick_switch_single_source_choice_reaches_what_downloads_use(client):
    _save(client, mode='hybrid', hybrid_order=['soulseek', 'youtube', 'tidal'])
    r = client.post('/api/profiles/active-sources', json={'download_mode': 'youtube'})
    assert r.status_code == 200 and r.get_json()['success']
    assert resolve_chain(config_manager.get) == ['youtube']


def test_the_quick_switch_keeps_the_list_for_the_trip_back_to_hybrid(client):
    ordered = ['soulseek', 'youtube', 'tidal']
    _save(client, mode='hybrid', hybrid_order=list(ordered))
    client.post('/api/profiles/active-sources', json={'download_mode': 'youtube'})
    r = client.post('/api/profiles/active-sources', json={'download_mode': 'hybrid'})
    assert r.status_code == 200 and r.get_json()['success']
    assert resolve_chain(config_manager.get) == ordered


def test_the_quick_switch_does_not_delete_a_source_its_grid_never_heard_of(client):
    """The modal renders the stored order verbatim, but validated a reorder
    against its own seven-name card list. Settings offers eleven, so Deezer,
    Amazon, Lidarr and SoundCloud appear in the drag list and are absent from
    the whitelist — a reorder silently dropped whichever of them you had.

    That was survivable only while this endpoint couldn't reach the chain.
    Making it work would have turned a cosmetic filter into "reordering in the
    sidebar removes your Deezer source"."""
    from web_server import _QS_DOWNLOAD_SOURCES
    assert 'deezer_dl' not in _QS_DOWNLOAD_SOURCES, "pick a source the grid really lacks"

    _save(client, mode='hybrid', hybrid_order=['soulseek', 'deezer_dl', 'youtube'])
    # The modal echoes back exactly what it displayed, reordered.
    r = client.post('/api/profiles/active-sources',
                    json={'hybrid_order': ['deezer_dl', 'soulseek', 'youtube']})
    assert r.status_code == 200 and r.get_json()['success']
    assert resolve_chain(config_manager.get) == ['deezer_dl', 'soulseek', 'youtube']


def test_the_quick_switch_still_refuses_a_name_it_could_not_have_shown(client):
    """Widening the filter must not turn it off: the endpoint takes arbitrary
    JSON, and a name that is neither a known source nor already in the chain
    was never on screen and has no business reaching config."""
    _save(client, mode='hybrid', hybrid_order=['soulseek', 'youtube'])
    r = client.post('/api/profiles/active-sources',
                    json={'hybrid_order': ['soulseek', 'not_a_real_source', 'youtube']})
    assert r.status_code == 200 and r.get_json()['success']
    assert resolve_chain(config_manager.get) == ['soulseek', 'youtube']


def test_the_quick_switch_and_the_settings_page_agree_afterwards(client):
    """Both write the same five keys now, so neither can be showing one thing
    while the other is in force — the split-brain the collapsed list exists to
    remove, reintroduced by only half the writers using it."""
    _save(client, mode='hybrid', hybrid_order=['soulseek', 'youtube', 'tidal'])
    client.post('/api/profiles/active-sources',
                json={'hybrid_order': ['tidal', 'soulseek', 'youtube']})
    modal = client.get('/api/profiles/me/active-sources').get_json()['download']
    page = _reload(client)
    assert modal['hybrid_order'] == page['hybrid_order'] == ['tidal', 'soulseek', 'youtube']
    assert modal['mode'] == page['mode'] == 'hybrid'
    assert resolve_chain(config_manager.get) == ['tidal', 'soulseek', 'youtube']
