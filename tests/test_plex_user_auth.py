"""core.plex_user_auth — server-access identity check for multi-user support
(bulk import + Sign in with Plex). No real network calls: MyPlexAccount is
mocked throughout, matching tests/media_server/test_plex_pinning.py's style."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import core.plex_user_auth as pua


def _user(id, title=None, username=None, email=None, thumb=None, home=False):
    return MagicMock(id=id, title=title, username=username, email=email, thumb=thumb, home=home)


# ── resolve_admin_plex_account ──────────────────────────────────────────────

def test_resolve_admin_account_none_when_not_configured():
    with patch('core.plex_user_auth.config_manager') as cfg:
        cfg.get_plex_config.return_value = {}
        assert pua.resolve_admin_plex_account() is None


def test_resolve_admin_account_uses_configured_token():
    with patch('core.plex_user_auth.config_manager') as cfg, \
         patch('core.plex_user_auth.MyPlexAccount') as MockAccount:
        cfg.get_plex_config.return_value = {'token': 'tok123'}
        MockAccount.return_value = 'the-account'
        assert pua.resolve_admin_plex_account() == 'the-account'
        MockAccount.assert_called_once_with(token='tok123')


def test_resolve_admin_account_explicit_token_skips_config():
    with patch('core.plex_user_auth.config_manager') as cfg, \
         patch('core.plex_user_auth.MyPlexAccount') as MockAccount:
        MockAccount.return_value = 'acct'
        assert pua.resolve_admin_plex_account(admin_token='explicit') == 'acct'
        MockAccount.assert_called_once_with(token='explicit')
        cfg.get_plex_config.assert_not_called()


def test_resolve_admin_account_none_on_plexapi_error():
    with patch('core.plex_user_auth.config_manager') as cfg, \
         patch('core.plex_user_auth.MyPlexAccount', side_effect=Exception('bad token')):
        cfg.get_plex_config.return_value = {'token': 'expired'}
        assert pua.resolve_admin_plex_account() is None


# ── get_server_authorized_plex_ids ──────────────────────────────────────────

def test_get_server_authorized_ids_includes_admin_and_users():
    admin = _user(1, title='Admin', username='admin', home=True)
    friend = _user(2, title='Friend', username='friend')
    home_kid = _user(3, title='Kid', username=None, home=True)   # home users often lack username
    admin.users.return_value = [friend, home_kid]

    with patch('core.plex_user_auth.resolve_admin_plex_account', return_value=admin):
        ids = pua.get_server_authorized_plex_ids()

    assert set(ids.keys()) == {1, 2, 3}
    assert ids[3]['username'] is None and ids[3]['home'] is True


def test_get_server_authorized_ids_none_when_unconfigured():
    with patch('core.plex_user_auth.resolve_admin_plex_account', return_value=None):
        assert pua.get_server_authorized_plex_ids() is None


def test_get_server_authorized_ids_none_on_users_call_failure():
    admin = _user(1)
    admin.users.side_effect = Exception('network error')
    with patch('core.plex_user_auth.resolve_admin_plex_account', return_value=admin):
        assert pua.get_server_authorized_plex_ids() is None


# ── plex_account_has_server_access ──────────────────────────────────────────

def test_has_server_access_true_for_admin_and_friends():
    admin = _user(1, title='Admin')
    friend = _user(2, title='Friend')
    admin.users.return_value = [friend]
    with patch('core.plex_user_auth.resolve_admin_plex_account', return_value=admin):
        assert pua.plex_account_has_server_access(1) is True    # admin itself
        assert pua.plex_account_has_server_access(2) is True    # listed friend
        assert pua.plex_account_has_server_access(999) is False  # not listed


def test_has_server_access_false_when_plex_unconfigured():
    with patch('core.plex_user_auth.resolve_admin_plex_account', return_value=None):
        assert pua.plex_account_has_server_access(1) is False


def test_has_server_access_false_for_non_numeric_id():
    admin = _user(1)
    admin.users.return_value = []
    with patch('core.plex_user_auth.resolve_admin_plex_account', return_value=admin):
        assert pua.plex_account_has_server_access('not-an-id') is False


# ── PLEX_PROFILE_DEFAULTS ───────────────────────────────────────────────────

def test_plex_profile_defaults_are_restrictive():
    """Bulk-imported / auto-provisioned profiles start non-admin with downloads
    OFF — stricter than the shipped manual-create default (can_download defaults
    True there) since these weren't individually vetted by the admin.

    allowed_sides is 'both' so a new Plex user can browse the video side and
    follow a show. That is not a download right: can_download=False keeps every
    acquisition and destructive endpoint gated, and a follow from such a profile
    is filed video_watchlist.approved=0, acquiring nothing until an admin
    approves it. can_download is the security boundary here, not allowed_sides."""
    assert pua.PLEX_PROFILE_DEFAULTS == {
        'allowed_sides': 'both', 'can_download': False, 'is_admin': False,
    }
    # The part that must never loosen without a deliberate decision.
    assert pua.PLEX_PROFILE_DEFAULTS['can_download'] is False
    assert pua.PLEX_PROFILE_DEFAULTS['is_admin'] is False
