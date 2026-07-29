"""Signing in survives closing the browser — and logging out really logs you out.

Reported as: Plex sign-in status is lost when the browser closes, so the Plex
link has to be done again every time.

Nothing ever configured the session cookie, so Flask's default applied: a
BROWSER-SESSION cookie with no Expires, discarded on close. That was invisible
while the profile picker let anyone click straight back into any profile. Once
switching required having actually authenticated (1.8.13), it meant redoing the
whole Plex link on every browser restart — the earlier change did not break
persistence, it made a pre-existing gap hurt.

Sessions are now permanent with a sliding 30-day window
(security.session_days), so someone who keeps using SoulSync is never signed out.

Persistence forces a second fix. /api/profiles/logout only popped 'profile_id',
leaving login_authenticated and — the one that matters — the list of profiles
this browser had authenticated as. Survivable when the whole session died on
browser close; with a 30-day cookie it would mean "log out" on a shared computer
still handing the next person every account you had signed into.
"""

from __future__ import annotations

import os
import tempfile
from datetime import timedelta

import pytest

_TMP = tempfile.mkdtemp(prefix='soulsync-testdb-session-')
os.environ.setdefault('DATABASE_PATH', os.path.join(_TMP, 'session.db'))
os.environ.setdefault('SOULSYNC_TEST_DB_READY', '1')

web_server = pytest.importorskip('web_server')


@pytest.fixture
def client():
    return web_server.app.test_client()


# ── the session outlives the browser ─────────────────────────────────────────
def test_the_session_is_permanent_with_a_real_lifetime():
    """A non-permanent Flask session sends no Expires, so the browser bins it on
    close — which is the entire reported bug."""
    assert web_server.app.permanent_session_lifetime > timedelta(days=1)


def test_the_cookie_actually_carries_an_expiry(client):
    """The property that matters, asserted on the wire rather than on config."""
    resp = client.get('/status')
    cookie = resp.headers.get('Set-Cookie', '')
    assert cookie, "expected a session cookie"
    assert 'Expires=' in cookie or 'Max-Age=' in cookie, cookie


def test_the_request_marks_the_session_permanent(client):
    client.get('/status')
    with client.session_transaction() as sess:
        assert sess.permanent is True


def test_the_lifetime_is_configurable_and_clamped(monkeypatch):
    """A silly value must not make sessions un-storable or effectively eternal."""
    def _cfg(value):
        monkeypatch.setattr(web_server, 'config_manager',
                            type('C', (), {'get': staticmethod(lambda k, d=None: value)})())
    _cfg(7)
    assert web_server._session_lifetime_days() == 7
    _cfg(0)
    assert web_server._session_lifetime_days() == 1
    _cfg(9999)
    assert web_server._session_lifetime_days() == 365
    _cfg('nonsense')
    assert web_server._session_lifetime_days() == web_server._SESSION_DAYS_DEFAULT


def test_secure_is_not_forced_on(client):
    """Marking the cookie Secure on a plain-http LAN install would stop the
    browser sending it at all — the same bug, permanently. reverse_proxy.py sets
    it only when the operator opts into proxy mode."""
    cookie = client.get('/status').headers.get('Set-Cookie', '')
    assert 'Secure' not in cookie
    rp = (__import__('pathlib').Path(__file__).resolve().parents[1]
          / 'core' / 'security' / 'reverse_proxy.py').read_text(encoding='utf-8')
    assert 'SESSION_COOKIE_SECURE' in rp, "the Secure flag must still be owned somewhere"


def test_httponly_is_still_set(client):
    cookie = client.get('/status').headers.get('Set-Cookie', '')
    assert 'HttpOnly' in cookie


# ── logging out actually logs you out ────────────────────────────────────────
def test_logout_clears_the_whole_session(client):
    with client.session_transaction() as sess:
        sess['profile_id'] = 5
        sess['login_authenticated'] = True
        sess['launch_pin_verified'] = True
        sess[web_server._AUTHORIZED_KEY] = [5, 9]

    assert client.post('/api/profiles/logout').status_code == 200

    with client.session_transaction() as sess:
        assert sess.get('profile_id') is None
        assert sess.get('login_authenticated') is None
        assert sess.get('launch_pin_verified') is None
        assert not sess.get(web_server._AUTHORIZED_KEY)


def test_logout_makes_a_signed_in_account_unreachable_again(client):
    """The security point of the above, stated as behaviour: after logging out on
    a shared computer, the next person must not be able to walk into the account
    you had signed into."""
    db = web_server.get_database()
    pid = db.create_profile(name='plexish_%s' % os.urandom(3).hex(),
                            plex_account_id=5150, plex_username='shared@example.com')
    with client.session_transaction() as sess:
        sess[web_server._AUTHORIZED_KEY] = [pid]
    assert client.post('/api/profiles/select', json={'profile_id': pid}).status_code == 200

    client.post('/api/profiles/logout')
    assert client.post('/api/profiles/select', json={'profile_id': pid}).status_code == 403


def test_logout_leaves_the_admin_reachable(client):
    """Clearing everything must not strand you at an empty picker."""
    client.post('/api/profiles/logout')
    ids = [p['id'] for p in client.get('/api/profiles/switchable').get_json()['profiles']]
    assert 1 in ids
