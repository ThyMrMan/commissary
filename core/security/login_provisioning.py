"""Login-mode password provisioning policy.

Invariant: while ``security.require_login`` is on, every profile must have a login
password OR an alternate auth method — otherwise it's fail-closed locked out
(usable only after the admin provisions one). That's not a security hole
(no-password = can't get in), but it's a usability gap, and the point here is to
make it impossible to OPEN one from any write-point: creating a profile, clearing
a password, or flipping login mode on.

A profile linked to a Plex account (bulk import or Sign in with Plex) is exempt —
completing Plex OAuth IS its way in, so it doesn't need a local password too. The
exemption is named generically (``plex_linked``, not Plex-specific in the function
bodies) so a future alt-auth method doesn't need a second parallel carve-out.

These are pure decisions so they're the single source of truth + unit-testable;
web_server wires them into the create / set-password / enable-login endpoints, and
the UI mirrors them.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional


def members_without_password(profiles: Optional[List[Dict[str, Any]]]) -> List[Dict[str, Any]]:
    """Non-admin profiles with no login password AND no alternate auth method
    (e.g. a linked Plex account) — they can't sign in once login mode is on.
    The admin is covered separately by its own anti-lockout, so it's excluded
    here. Returns ``[{'id', 'name'}, …]`` (empty = no gap)."""
    out: List[Dict[str, Any]] = []
    for p in (profiles or []):
        if not p.get('is_admin') and not p.get('has_password') and not p.get('plex_account_id'):
            out.append({'id': p.get('id'), 'name': p.get('name')})
    return out


def create_needs_password(require_login: bool, is_admin: bool = False, plex_linked: bool = False) -> bool:
    """A non-admin profile created while login mode is on must carry a password
    or an alternate auth method (a Plex link), or it's born unable to sign in."""
    return bool(require_login) and not is_admin and not plex_linked


def removing_password_strands(require_login: bool, plex_linked: bool = False) -> bool:
    """Clearing a profile's password while login mode is on would lock it out —
    unless it can still authenticate via a linked Plex account."""
    return bool(require_login) and not plex_linked


__all__ = [
    "members_without_password",
    "create_needs_password",
    "removing_password_strands",
]
