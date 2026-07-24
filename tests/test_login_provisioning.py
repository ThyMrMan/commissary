"""No-gaps invariant: while login mode is on, every profile must have a login
password. Pure policy seam + endpoint enforcement at every write-point (create,
clear, enable-login)."""

from __future__ import annotations

import os
import tempfile

import pytest

from core.security.login_provisioning import (
    members_without_password, create_needs_password, removing_password_strands)


# ── pure policy ─────────────────────────────────────────────────────────────
def test_members_without_password_flags_only_passwordless_nonadmins():
    profiles = [
        {'id': 1, 'name': 'Admin', 'is_admin': True, 'has_password': False},   # admin: own anti-lockout
        {'id': 2, 'name': 'HasPw', 'is_admin': False, 'has_password': True},    # fine
        {'id': 3, 'name': 'NoPw', 'is_admin': False, 'has_password': False},    # stranded
    ]
    out = members_without_password(profiles)
    assert out == [{'id': 3, 'name': 'NoPw'}]


def test_members_without_password_empty_when_all_set():
    assert members_without_password([{'id': 2, 'is_admin': False, 'has_password': True}]) == []
    assert members_without_password(None) == []


def test_members_without_password_exempts_plex_linked():
    """A Plex-linked profile (bulk import or Sign in with Plex) is exempt —
    completing Plex OAuth is its own way in."""
    profiles = [
        {'id': 3, 'name': 'NoPw', 'is_admin': False, 'has_password': False, 'plex_account_id': None},
        {'id': 4, 'name': 'PlexLinked', 'is_admin': False, 'has_password': False, 'plex_account_id': 555},
    ]
    assert members_without_password(profiles) == [{'id': 3, 'name': 'NoPw'}]


def test_create_needs_password_only_when_login_on_and_nonadmin():
    assert create_needs_password(True) is True
    assert create_needs_password(False) is False
    assert create_needs_password(True, is_admin=True) is False


def test_create_needs_password_exempts_plex_linked():
    assert create_needs_password(True, plex_linked=True) is False
    assert create_needs_password(True, plex_linked=False) is True
    assert create_needs_password(False, plex_linked=False) is False


def test_removing_password_strands_only_when_login_on():
    assert removing_password_strands(True) is True
    assert removing_password_strands(False) is False


def test_removing_password_strands_exempts_plex_linked():
    assert removing_password_strands(True, plex_linked=True) is False
    assert removing_password_strands(True, plex_linked=False) is True
    assert removing_password_strands(False, plex_linked=True) is False


# ── endpoint enforcement ────────────────────────────────────────────────────
_TMP = tempfile.mkdtemp(prefix='soulsync-testdb-prov-')
os.environ['DATABASE_PATH'] = os.path.join(_TMP, 'p.db')
os.environ['SOULSYNC_TEST_DB_READY'] = '1'
web_server = pytest.importorskip('web_server')


@pytest.fixture
def client():
    return web_server.app.test_client()


def _login_on(monkeypatch, on=True):
    real = web_server.config_manager.get
    monkeypatch.setattr(web_server.config_manager, 'get',
                        lambda k, d=None: on if k == 'security.require_login' else real(k, d))


def _auth(c):
    # Turning login mode on activates the HTTP gate — authenticate the session as
    # admin so the request reaches the endpoint (we're testing the endpoint logic).
    with c.session_transaction() as sess:
        sess['login_authenticated'] = True
        sess['profile_id'] = 1


def test_create_without_password_blocked_when_login_on(monkeypatch, client):
    _login_on(monkeypatch, True); _auth(client)
    r = client.post('/api/profiles', json={'name': 'NoPwMember'})
    assert r.status_code == 400
    assert 'login' in r.get_json()['error'].lower()


def test_create_with_password_succeeds_when_login_on(monkeypatch, client):
    _login_on(monkeypatch, True); _auth(client)
    r = client.post('/api/profiles', json={'name': 'PwMember', 'password': 'secret9'})
    assert r.status_code == 200 and r.get_json()['success'] is True
    pid = r.get_json()['profile_id']
    assert web_server.get_database().verify_profile_password(pid, 'secret9') is True


def test_create_without_password_fine_when_login_off(monkeypatch, client):
    _login_on(monkeypatch, False)
    r = client.post('/api/profiles', json={'name': 'PinOnlyMember'})
    assert r.status_code == 200 and r.get_json()['success'] is True   # no friction when off


def test_clear_password_blocked_when_login_on(monkeypatch, client):
    db = web_server.get_database()
    r = client.post('/api/profiles', json={'name': 'Clearable', 'password': 'x12345'})
    pid = r.get_json()['profile_id']
    _login_on(monkeypatch, True); _auth(client)
    r2 = client.post(f'/api/profiles/{pid}/set-password', json={'password': ''})
    assert r2.status_code == 400 and 'login mode' in r2.get_json()['error'].lower()
    assert db.verify_profile_password(pid, 'x12345') is True          # still set


def test_clear_password_allowed_for_plex_linked_when_login_on(monkeypatch, client):
    """A Plex-linked profile's login password can be cleared even while login
    mode is on — Plex sign-in still gets them in."""
    db = web_server.get_database()
    pid = db.create_profile('PlexMember', plex_account_id=777)
    db.set_profile_password(pid, 'temp12345')
    _login_on(monkeypatch, True); _auth(client)
    r = client.post(f'/api/profiles/{pid}/set-password', json={'password': ''})
    assert r.status_code == 200 and r.get_json()['success'] is True
    assert db.profile_has_password(pid) is False
