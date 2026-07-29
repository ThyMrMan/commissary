"""You may only become a profile this browser has authenticated as.

Reported as: from the swap-account screen you could enter any Plex user's
account; it should only show the accounts you are signed into.

The hole, exactly:

  * GET /api/profiles was ungated and returned EVERY profile
  * POST /api/profiles/select accepted any profile_id and asked for a PIN only
    if that profile happened to have one
  * a Plex-provisioned profile is created with no PIN and no password
    (PLEX_PROFILE_DEFAULTS sets only allowed_sides / can_download / is_admin)

so clicking a Plex user's card made you them, with no Plex authentication. It was
never Plex-specific either: any LOCAL profile whose owner never set a PIN was
equally open, which is why the fix keys on "has this browser authenticated as
it", not on "is it Plex-linked".

The session now records the profiles it has proven access to. Hiding cards is the
courtesy; /profiles/select refusing is the check, and these tests exercise the
refusal rather than the hiding.

Profile 1 is deliberately always selectable — the lock-out hatch, so an install
whose admin set neither PIN nor password cannot shut itself out. That is not a
bypass: profile 1's own PIN, when it has one, is still verified.
"""

from __future__ import annotations

import os
import tempfile

import pytest

_TMP = tempfile.mkdtemp(prefix='soulsync-testdb-switch-')
os.environ.setdefault('DATABASE_PATH', os.path.join(_TMP, 'switch.db'))
os.environ.setdefault('SOULSYNC_TEST_DB_READY', '1')

web_server = pytest.importorskip('web_server')


@pytest.fixture
def client():
    return web_server.app.test_client()


def _mk(name, pin=None, **kw):
    db = web_server.get_database()
    pid = db.create_profile(name=f'{name}_{os.urandom(3).hex()}', **kw)
    if pin:
        from werkzeug.security import generate_password_hash
        db.update_profile(pid, pin_hash=generate_password_hash(pin))
    return pid


@pytest.fixture
def plex_profile():
    """A Plex-provisioned profile: no PIN, no password — the reported case."""
    from core.plex_user_auth import PLEX_PROFILE_DEFAULTS
    return _mk('plexuser', plex_account_id=987654, plex_username='someone@example.com',
               **PLEX_PROFILE_DEFAULTS)


@pytest.fixture
def local_profile():
    """A local profile with no PIN — the same hole, without Plex involved."""
    return _mk('localuser')


# ── the refusal ──────────────────────────────────────────────────────────────
def test_cannot_switch_into_a_plex_account_without_signing_in(client, plex_profile):
    """The reported bug. A fresh browser must not be able to become a Plex user."""
    r = client.post('/api/profiles/select', json={'profile_id': plex_profile})
    assert r.status_code == 403, r.get_data(as_text=True)
    body = r.get_json()
    assert body['success'] is False
    assert body.get('auth_required') is True


def test_the_session_is_not_switched_by_a_refused_attempt(client, plex_profile):
    """A 403 that still set session['profile_id'] would be no fix at all."""
    client.post('/api/profiles/select', json={'profile_id': plex_profile})
    with client.session_transaction() as sess:
        assert sess.get('profile_id') != plex_profile


def test_a_passwordless_local_profile_is_equally_refused(client, local_profile):
    """The hole was never Plex-specific — keying the fix on plex_account_id would
    have left every PIN-less local profile open."""
    assert client.post('/api/profiles/select',
                       json={'profile_id': local_profile}).status_code == 403


def test_authenticating_makes_it_switchable(client, plex_profile):
    """What 'signed into' means: once this browser has proved it, swapping back is
    free — that is the whole point of the swap screen."""
    with client.session_transaction() as sess:
        sess[web_server._AUTHORIZED_KEY] = [plex_profile]
    r = client.post('/api/profiles/select', json={'profile_id': plex_profile})
    assert r.status_code == 200 and r.get_json()['success'] is True


def test_authorizing_one_does_not_authorize_another(client, plex_profile):
    """Signing in as one Plex user must not hand you the rest of them."""
    other = _mk('otherplex', plex_account_id=112233, plex_username='other@example.com')
    with client.session_transaction() as sess:
        sess[web_server._AUTHORIZED_KEY] = [plex_profile]
    assert client.post('/api/profiles/select',
                       json={'profile_id': other}).status_code == 403


def test_the_root_admin_is_always_selectable(client):
    """The lock-out hatch: an install whose admin set no PIN and no password must
    still be able to reach its own admin profile."""
    assert 1 in web_server._authorized_profile_ids()
    assert client.post('/api/profiles/select', json={'profile_id': 1}).status_code == 200


# ── the list the picker reads ────────────────────────────────────────────────
def test_the_switchable_list_hides_accounts_you_have_not_signed_into(client, plex_profile):
    r = client.get('/api/profiles/switchable')
    assert r.status_code == 200
    ids = [p['id'] for p in r.get_json()['profiles']]
    assert plex_profile not in ids
    assert 1 in ids, "the root admin must never be hidden"


def test_it_appears_once_signed_in(client, plex_profile):
    with client.session_transaction() as sess:
        sess[web_server._AUTHORIZED_KEY] = [plex_profile]
    ids = [p['id'] for p in client.get('/api/profiles/switchable').get_json()['profiles']]
    assert plex_profile in ids


def test_the_list_and_the_gate_agree(client, plex_profile, local_profile):
    """The picker must never show a card that select() refuses, or the screen
    offers something that fails on click."""
    with client.session_transaction() as sess:
        sess[web_server._AUTHORIZED_KEY] = [plex_profile]
    shown = [p['id'] for p in client.get('/api/profiles/switchable').get_json()['profiles']]
    for pid in shown:
        assert client.post('/api/profiles/select',
                           json={'profile_id': pid}).status_code == 200, pid


def test_the_full_profile_list_is_admin_only(client, plex_profile):
    """It enumerates every account including the Plex username behind each one."""
    nonadmin = _mk('member')
    with client.session_transaction() as sess:
        sess['profile_id'] = nonadmin
        sess[web_server._AUTHORIZED_KEY] = [nonadmin]
    assert client.get('/api/profiles').status_code == 403


def test_switchable_still_reports_the_real_total(client, plex_profile):
    """The PIN rule keys on how many profiles EXIST, not how many you can see, so
    the count has to survive the filtering."""
    body = client.get('/api/profiles/switchable').get_json()
    assert body['total_profiles'] >= 2
    assert len(body['profiles']) < body['total_profiles']


# ── a correct PIN is still proof ─────────────────────────────────────────────
def test_a_correct_pin_authorizes_the_profile(client):
    pid = _mk('pinned', pin='4321')
    r = client.post('/api/profiles/select', json={'profile_id': pid, 'pin': '4321'})
    assert r.status_code == 200, r.get_data(as_text=True)
    with client.session_transaction() as sess:
        assert pid in (sess.get(web_server._AUTHORIZED_KEY) or [])


def test_a_wrong_pin_does_not(client):
    pid = _mk('pinned2', pin='4321')
    assert client.post('/api/profiles/select',
                       json={'profile_id': pid, 'pin': '0000'}).status_code == 401
    with client.session_transaction() as sess:
        assert pid not in (sess.get(web_server._AUTHORIZED_KEY) or [])


# ── the helpers themselves ───────────────────────────────────────────────────
def test_a_corrupt_cookie_does_not_lock_anyone_out(client):
    with client.application.test_request_context():
        from flask import session
        session[web_server._AUTHORIZED_KEY] = ['nonsense', None, {'x': 1}]
        assert web_server._authorized_profile_ids() == {1}


def test_the_authorized_list_is_capped(client):
    with client.application.test_request_context():
        from flask import session
        for pid in range(2, 60):
            web_server._authorize_profile(pid)
        assert len(session[web_server._AUTHORIZED_KEY]) <= 24
