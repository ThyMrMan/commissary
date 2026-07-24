"""Plex-account identity helpers for multi-user support (bulk import + Sign in
with Plex). Separate from core/plex_client.py — that module connects a
PlexServer for library scanning; this one only ever talks to plex.tv's
account API (MyPlexAccount), since all it needs to answer is "who is allowed
to use this app," not "what's in the library."
"""

from __future__ import annotations

from typing import Any, Dict, Optional

from plexapi.myplex import MyPlexAccount

from config.settings import config_manager
from utils.logging_config import get_logger

logger = get_logger(__name__)

# Starting permissions for a profile created FROM a Plex account (bulk import
# or a first Sign-in-with-Plex auto-provision) — stricter than the shipped
# default for a manually-created profile (can_download defaults True there):
# these are bulk-added friends/Home members the admin didn't individually
# vet, so downloads start OFF and they can be loosened per-profile afterward
# in Manage Profiles.
PLEX_PROFILE_DEFAULTS: Dict[str, Any] = {
    'allowed_sides': 'music',
    'can_download': False,
    'is_admin': False,
}


def resolve_admin_plex_account(admin_token: Optional[str] = None) -> Optional[MyPlexAccount]:
    """The MyPlexAccount for the app's configured Plex connection (the same
    global config core/plex_client.py reads via config_manager.get_plex_config()),
    or None if Plex isn't configured or the account can't be resolved (bad/
    expired token, network error)."""
    token = admin_token
    if not token:
        token = (config_manager.get_plex_config() or {}).get('token')
    if not token:
        return None
    try:
        return MyPlexAccount(token=token)
    except Exception as e:   # noqa: BLE001 - surfaced as "Plex unreachable" to callers
        logger.warning("Could not resolve the configured Plex account: %s", e)
        return None


def _user_dict(u) -> Dict[str, Any]:
    return {
        'id': u.id, 'title': getattr(u, 'title', None), 'username': getattr(u, 'username', None),
        'email': getattr(u, 'email', None), 'thumb': getattr(u, 'thumb', None),
        'home': bool(getattr(u, 'home', False)),
    }


def get_server_authorized_plex_ids(admin_token: Optional[str] = None) -> Optional[Dict[int, Dict[str, Any]]]:
    """Every Plex account with access to the app's configured Plex server:
    the admin account itself, plus every friend/Home member plex.tv reports
    for it (account.users()). Keyed by the stable int account id (NOT uuid —
    MyPlexUser entries, unlike a full MyPlexAccount, don't carry one). None if
    Plex isn't configured or the account can't be reached.

    v1 scope: doesn't disambiguate which specific server a multi-server admin
    account owns (account.users() is account-wide, not per-server) — matches
    the rigor of the existing /api/plex/pin/status endpoint, which doesn't
    cross-check server identity either. Fine for this app's existing
    single-server assumption; a stricter check would compare each user's
    MyPlexServerShare.machineIdentifier against the configured server's own."""
    account = resolve_admin_plex_account(admin_token)
    if account is None:
        return None
    try:
        out: Dict[int, Dict[str, Any]] = {account.id: _user_dict(account)}
        for u in account.users():
            out[u.id] = _user_dict(u)
        return out
    except Exception as e:   # noqa: BLE001 - surfaced as "Plex unreachable" to callers
        logger.warning("Could not list Plex server users: %s", e)
        return None


def plex_account_has_server_access(plex_account_id: int, admin_token: Optional[str] = None) -> bool:
    """True if plex_account_id is the admin's own account or is listed among
    the accounts with access to the configured Plex server."""
    ids = get_server_authorized_plex_ids(admin_token)
    if ids is None:
        return False
    try:
        return int(plex_account_id) in ids
    except (TypeError, ValueError):
        return False


__all__ = [
    "PLEX_PROFILE_DEFAULTS",
    "resolve_admin_plex_account",
    "get_server_authorized_plex_ids",
    "plex_account_has_server_access",
]
