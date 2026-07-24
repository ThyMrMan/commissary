"""Pure login-gate decision (opt-in username/password mode)."""

from __future__ import annotations

from core.security.login_gate import login_request_is_blocked as blocked


def test_off_never_blocks():
    assert blocked('/api/anything', 'GET', require_login=False, authenticated=False) is False


def test_authenticated_never_blocked():
    assert blocked('/api/anything', 'GET', require_login=True, authenticated=True) is False


def test_unauthenticated_blocked_on_normal_api():
    assert blocked('/api/library', 'GET', require_login=True, authenticated=False) is True


def test_login_flow_and_shell_allowed_unauthenticated():
    for p in ('/', '/static/app.js', '/favicon.ico'):
        assert blocked(p, 'GET', require_login=True, authenticated=False) is False
    assert blocked('/api/auth/login', 'POST', require_login=True, authenticated=False) is False
    assert blocked('/api/auth/logout', 'POST', require_login=True, authenticated=False) is False
    assert blocked('/api/profiles/current', 'GET', require_login=True, authenticated=False) is False
    assert blocked('/api/setup/status', 'GET', require_login=True, authenticated=False) is False


def test_profile_list_NOT_exposed_pre_auth():
    # login mode = type your name, don't pick from an exposed roster
    assert blocked('/api/profiles', 'GET', require_login=True, authenticated=False) is True


def test_key_authed_public_api_allowed():
    assert blocked('/api/v1/search', 'GET', require_login=True, authenticated=False) is False
    assert blocked('/api/v1/api-keys-internal', 'GET', require_login=True, authenticated=False) is True


def test_plex_signin_flow_allowed_unauthenticated():
    """Sign in with Plex must be reachable BEFORE authentication — it's how
    the user gets authenticated in the first place — mirroring /api/auth/login."""
    assert blocked('/api/auth/plex/start', 'POST', require_login=True, authenticated=False) is False
    assert blocked('/api/auth/plex/status', 'GET', require_login=True, authenticated=False) is False


def test_plex_import_endpoints_NOT_exposed_pre_auth():
    # Admin-only bulk import — only reachable by an already-authenticated admin.
    assert blocked('/api/profiles/plex/candidates', 'GET', require_login=True, authenticated=False) is True
    assert blocked('/api/profiles/plex/import', 'POST', require_login=True, authenticated=False) is True
