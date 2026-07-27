"""Username/password login endpoints + gate (opt-in login mode)."""

from __future__ import annotations

import os
import tempfile

import pytest

_TMP = tempfile.mkdtemp(prefix='soulsync-testdb-login-')
os.environ['DATABASE_PATH'] = os.path.join(_TMP, 'l.db')
os.environ['SOULSYNC_TEST_DB_READY'] = '1'

web_server = pytest.importorskip('web_server')


@pytest.fixture
def client():
    return web_server.app.test_client()


def _enable_login(monkeypatch):
    real_get = web_server.config_manager.get
    monkeypatch.setattr(web_server.config_manager, 'get',
                        lambda k, d=None: True if k == 'security.require_login' else real_get(k, d))
    web_server._login_limiter.record_success('127.0.0.1')  # clean slate


_GATED = '/api/profiles/me/connections'   # a normal, non-allowlisted endpoint


def test_login_gate_blocks_then_authenticated_access(client, monkeypatch):
    db = web_server.get_database()
    pid = db.create_profile(name='LoginUser')
    db.set_profile_password(pid, 'secretpw')
    _enable_login(monkeypatch)

    assert client.get(_GATED).status_code == 401                       # not logged in → blocked

    r = client.post('/api/auth/login', json={'username': 'LoginUser', 'password': 'secretpw'})
    assert r.status_code == 200 and r.get_json()['success'] is True

    assert client.get(_GATED).status_code == 200                       # authenticated → in

    assert client.post('/api/auth/logout').get_json()['success'] is True
    assert client.get(_GATED).status_code == 401                       # logged out → blocked again


def test_login_is_case_insensitive_on_username(client, monkeypatch):
    db = web_server.get_database()
    db.set_profile_password(db.create_profile(name='CaseUser'), 'pw')
    _enable_login(monkeypatch)
    assert client.post('/api/auth/login', json={'username': 'caseuser', 'password': 'pw'}).status_code == 200


def test_wrong_password_401_generic(client, monkeypatch):
    db = web_server.get_database()
    db.set_profile_password(db.create_profile(name='WrongPwUser'), 'right')
    _enable_login(monkeypatch)
    r = client.post('/api/auth/login', json={'username': 'WrongPwUser', 'password': 'nope'})
    assert r.status_code == 401
    assert 'username or password' in r.get_json()['error'].lower()     # generic — no name-leak


def test_passwordless_profile_cannot_login(client, monkeypatch):
    db = web_server.get_database()
    db.create_profile(name='NoPwUser')   # no password set
    _enable_login(monkeypatch)
    assert client.post('/api/auth/login', json={'username': 'NoPwUser', 'password': 'x'}).status_code == 401


def test_unknown_user_401(client, monkeypatch):
    _enable_login(monkeypatch)
    assert client.post('/api/auth/login', json={'username': 'ghost', 'password': 'x'}).status_code == 401


def test_cannot_enable_login_without_admin_password(client):
    # admin (1) has no password → enabling login mode is refused (anti-lockout)
    web_server.get_database().set_profile_password(1, '')
    r = client.post('/api/settings', json={'security': {'require_login': True}})
    assert r.status_code == 400
    assert 'password' in r.get_json().get('error', '').lower()


def test_set_password_endpoint(client):
    db = web_server.get_database()
    pid = db.create_profile(name='SetPwTest')
    # admin (default session) can set any profile's login password
    r = client.post(f'/api/profiles/{pid}/set-password', json={'password': 'newpw123'})
    body = r.get_json()
    assert body['success'] is True and body['has_password'] is True
    assert db.verify_profile_password(pid, 'newpw123') is True
    # clearing it
    assert client.post(f'/api/profiles/{pid}/set-password', json={'password': ''}).get_json()['has_password'] is False


def test_profiles_current_signals_login_required(client, monkeypatch):
    _enable_login(monkeypatch)
    body = client.get('/api/profiles/current').get_json()
    assert body.get('login_required') is True   # frontend uses this to show the sign-in screen


def test_pin_gate_unaffected_when_login_off(client, monkeypatch):
    # THE guarantee: with login mode OFF (default) and the launch PIN ON, the PIN
    # gate must STILL enforce — the login feature must not weaken or bypass it.
    real_get = web_server.config_manager.get
    def fake_get(key, default=None):
        if key == 'security.require_login':
            return False                       # login OFF (default)
        if key == 'security.require_pin_on_launch':
            return True                        # PIN ON
        return real_get(key, default)
    monkeypatch.setattr(web_server.config_manager, 'get', fake_get)

    # Unverified session, PIN required → the launch-PIN gate still 401s.
    assert client.get('/api/profiles/me/connections').status_code == 401
    # And /api/profiles/current reports the PIN screen, NOT login.
    body = client.get('/api/profiles/current').get_json()
    assert body.get('login_required') is not True


def test_everything_normal_when_both_off(client, monkeypatch):
    # Default install: login OFF + PIN OFF → no gate at all (today's behavior).
    real_get = web_server.config_manager.get
    monkeypatch.setattr(web_server.config_manager, 'get',
        lambda k, d=None: False if k in ('security.require_login', 'security.require_pin_on_launch') else real_get(k, d))
    assert client.get('/api/profiles/me/connections').status_code == 200   # reachable, unguarded


def test_recovery_flow_resets_password(client, monkeypatch):
    db = web_server.get_database()
    pid = db.create_profile(name='RecoverMe')
    db.set_profile_password(pid, 'oldpassword')
    db.set_profile_recovery(pid, 'First pet?', 'Rex')
    _enable_login(monkeypatch)

    # forgot-password flow is reachable pre-auth
    q = client.get('/api/auth/recovery-question?username=RecoverMe').get_json()
    assert q['success'] and q['question'] == 'First pet?'

    # wrong answer → 401, password unchanged
    bad = client.post('/api/auth/recovery-reset',
                      json={'username': 'RecoverMe', 'answer': 'Fido', 'new_password': 'newpass1'})
    assert bad.status_code == 401
    assert db.verify_profile_password(pid, 'oldpassword') is True

    # correct answer → password reset + authenticated
    ok = client.post('/api/auth/recovery-reset',
                     json={'username': 'RecoverMe', 'answer': 'rex', 'new_password': 'brandnew1'})
    assert ok.status_code == 200 and ok.get_json()['success'] is True
    assert db.verify_profile_password(pid, 'brandnew1') is True
    assert db.verify_profile_password(pid, 'oldpassword') is False


def test_recovery_question_404_for_unknown(client, monkeypatch):
    _enable_login(monkeypatch)
    assert client.get('/api/auth/recovery-question?username=ghost').status_code == 404


def test_set_recovery_endpoint(client):
    db = web_server.get_database()
    pid = db.create_profile(name='SetRec')
    r = client.post(f'/api/profiles/{pid}/set-recovery', json={'question': 'Q?', 'answer': 'A'})
    assert r.get_json()['has_recovery'] is True
    assert db.verify_profile_recovery_answer(pid, 'a') is True


# ── Sign in with Plex ────────────────────────────────────────────────────────

def _enable_plex_login(monkeypatch, on=True):
    real_get = web_server.config_manager.get
    monkeypatch.setattr(web_server.config_manager, 'get',
                        lambda k, d=None: on if k == 'security.allow_plex_login' else real_get(k, d))


class _FakePinLogin:
    def __init__(self, pin='4242', token='FAKETOKEN', logged_in=True, expired=False):
        self.pin = pin
        self.token = token
        self.expires_at = None
        self._logged_in = logged_in
        self.expired = expired

    def checkLogin(self):
        return self._logged_in


class _FakeAccount:
    def __init__(self, id, title=None, username=None, thumb=None):
        self.id = id
        self.title = title
        self.username = username
        self.thumb = thumb


def _start_plex_signin(client, monkeypatch, pinlogin):
    monkeypatch.setattr(web_server, 'MyPlexPinLogin', lambda oauth=False: pinlogin)
    r = client.post('/api/auth/plex/start')
    assert r.status_code == 200 and r.get_json()['success'] is True
    return r.get_json()['request_id']


def test_plex_signin_disabled_by_default(client):
    r = client.post('/api/auth/plex/start')
    assert r.status_code == 403


def test_plex_signin_matches_existing_linked_profile(client, monkeypatch):
    db = web_server.get_database()
    pid = db.create_profile(name='PlexAlice', plex_account_id=101)
    _enable_plex_login(monkeypatch)
    rid = _start_plex_signin(client, monkeypatch, _FakePinLogin())
    monkeypatch.setattr(web_server, 'MyPlexAccount',
                        lambda token: _FakeAccount(101, title='PlexAlice', username='alice'))

    r = client.get(f'/api/auth/plex/status?request_id={rid}')
    body = r.get_json()
    assert r.status_code == 200 and body['success'] is True
    assert body['is_new'] is False and body['profile']['id'] == pid
    with client.session_transaction() as sess:
        assert sess['login_authenticated'] is True and sess['profile_id'] == pid
    # Only the one pre-existing profile is linked to this Plex account — no duplicate created.
    assert sum(1 for p in db.get_all_profiles() if p.get('plex_account_id') == 101) == 1


def test_plex_signin_auto_provisions_authorized_new_account(client, monkeypatch):
    db = web_server.get_database()
    _enable_plex_login(monkeypatch)
    monkeypatch.setattr('core.plex_user_auth.plex_account_has_server_access', lambda pid, admin_token=None: True)
    rid = _start_plex_signin(client, monkeypatch, _FakePinLogin())
    monkeypatch.setattr(web_server, 'MyPlexAccount',
                        lambda token: _FakeAccount(202, title='NewPlexUser', username='newuser', thumb='http://t'))

    r = client.get(f'/api/auth/plex/status?request_id={rid}')
    body = r.get_json()
    assert r.status_code == 200 and body['success'] is True and body['is_new'] is True
    new_id = body['profile']['id']
    created = db.get_profile(new_id)
    assert created['plex_account_id'] == 202
    assert created['allowed_sides'] == 'both' and created['can_download'] is False and created['is_admin'] is False
    with client.session_transaction() as sess:
        assert sess['profile_id'] == new_id


def test_plex_signin_rejects_unauthorized_account_and_creates_no_profile(client, monkeypatch):
    db = web_server.get_database()
    before = len(db.get_all_profiles())
    _enable_plex_login(monkeypatch)
    monkeypatch.setattr('core.plex_user_auth.plex_account_has_server_access', lambda pid, admin_token=None: False)
    rid = _start_plex_signin(client, monkeypatch, _FakePinLogin())
    monkeypatch.setattr(web_server, 'MyPlexAccount',
                        lambda token: _FakeAccount(303, title='Stranger'))

    r = client.get(f'/api/auth/plex/status?request_id={rid}')
    assert r.status_code == 403
    assert db.get_profile_by_plex_id(303) is None
    assert len(db.get_all_profiles()) == before
    with client.session_transaction() as sess:
        assert 'login_authenticated' not in sess


def test_plex_signin_name_collision_gets_suffix(client, monkeypatch):
    db = web_server.get_database()
    db.create_profile(name='Sam')   # local profile, not Plex-linked, same name as the incoming Plex title
    _enable_plex_login(monkeypatch)
    monkeypatch.setattr('core.plex_user_auth.plex_account_has_server_access', lambda pid, admin_token=None: True)
    rid = _start_plex_signin(client, monkeypatch, _FakePinLogin())
    monkeypatch.setattr(web_server, 'MyPlexAccount', lambda token: _FakeAccount(404, title='Sam'))

    r = client.get(f'/api/auth/plex/status?request_id={rid}')
    body = r.get_json()
    assert r.status_code == 200 and body['success'] is True
    assert body['profile']['name'] == 'Sam (Plex)'


def test_plex_signin_expired_pin(client, monkeypatch):
    _enable_plex_login(monkeypatch)
    rid = _start_plex_signin(client, monkeypatch, _FakePinLogin(expired=True))
    r = client.get(f'/api/auth/plex/status?request_id={rid}')
    body = r.get_json()
    assert body['success'] is False and body['expired'] is True


def test_plex_signin_waiting_for_authorization(client, monkeypatch):
    _enable_plex_login(monkeypatch)
    rid = _start_plex_signin(client, monkeypatch, _FakePinLogin(logged_in=False))
    r = client.get(f'/api/auth/plex/status?request_id={rid}')
    body = r.get_json()
    assert body['success'] is False and 'status' in body and 'error' not in body


def test_plex_signin_status_disabled(client, monkeypatch):
    _enable_plex_login(monkeypatch, on=True)
    rid = _start_plex_signin(client, monkeypatch, _FakePinLogin())
    _enable_plex_login(monkeypatch, on=False)   # disabled between start and poll
    r = client.get(f'/api/auth/plex/status?request_id={rid}')
    assert r.status_code == 403


def test_profiles_current_reports_plex_login_enabled(client, monkeypatch):
    _enable_plex_login(monkeypatch, on=True)
    body = client.get('/api/profiles/current').get_json()
    assert body.get('plex_login_enabled') is True
