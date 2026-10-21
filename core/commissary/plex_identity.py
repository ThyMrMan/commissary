"""Stable Plex client identity across container restarts.

plexapi derives ``X-Plex-Client-Identifier`` from ``hex(uuid.getnode())`` (the
MAC address) and the device name from the hostname. Inside Docker both change
on every container start, so Plex sees a brand-new device after each restart:
the account gets a "new device signed in" mail and the Devices list fills with
anonymous ``Linux`` entries. Upstream keeps a stable id only for its own
Sign-in-with-Plex PIN flow (``plex_signin.client_id``), not for plexapi.

One identifier is used for both. Precedence keeps an existing install looking
like the device Plex already knows:

1. ``plex.client_identifier`` (what the legacy fork persisted)
2. ``plex_signin.client_id`` (what upstream's sign-in minted)
3. a newly minted UUID

and whichever is chosen is written to both keys.
"""

from __future__ import annotations

import sys
import uuid

from utils.logging_config import get_logger

logger = get_logger("commissary.plex_identity")

IDENTIFIER_KEY = "plex.client_identifier"
SIGNIN_KEY = "plex_signin.client_id"

# What Plex shows in its Devices list in place of "Linux".
PLEX_PRODUCT = "SoulSync"
PLEX_DEVICE = "SoulSync"
PLEX_DEVICE_NAME = "SoulSync"


def _clean(value) -> str:
    return value.strip() if isinstance(value, str) else ""


def get_client_identifier(config_manager) -> str:
    """The persisted identifier, adopting or minting one on first call."""
    ident = _clean(config_manager.get(IDENTIFIER_KEY, "")) or _clean(config_manager.get(SIGNIN_KEY, ""))
    if not ident:
        ident = str(uuid.uuid4())
        logger.info("Minted a new stable Plex client identifier")
    if _clean(config_manager.get(IDENTIFIER_KEY, "")) != ident:
        config_manager.set(IDENTIFIER_KEY, ident)
    if not _clean(config_manager.get(SIGNIN_KEY, "")):
        config_manager.set(SIGNIN_KEY, ident)
    return ident


def apply_plex_identity(config_manager, version: str = "") -> str:
    """Point plexapi at the stable identity. Returns the identifier in use.

    Never fatal: failing here only means Plex keeps seeing new devices, which
    must not stop the app from booting.
    """
    try:
        import plexapi
        from plexapi.config import reset_base_headers

        identifier = get_client_identifier(config_manager)

        plexapi.X_PLEX_IDENTIFIER = identifier
        plexapi.X_PLEX_PRODUCT = PLEX_PRODUCT
        plexapi.X_PLEX_DEVICE = PLEX_DEVICE
        plexapi.X_PLEX_DEVICE_NAME = PLEX_DEVICE_NAME
        if version:
            plexapi.X_PLEX_VERSION = version

        # Submodules did ``from plexapi import X_PLEX_IDENTIFIER`` and hold
        # their own copy (plexapi.myplex uses it as the default PIN clientId).
        for name, module in list(sys.modules.items()):
            if name.startswith("plexapi.") and getattr(module, "X_PLEX_IDENTIFIER", None) is not None:
                module.X_PLEX_IDENTIFIER = identifier

        # plexapi.server imported the BASE_HEADERS dict itself and copies it per
        # request, so rebinding the name would be invisible: mutate in place.
        plexapi.BASE_HEADERS.clear()
        plexapi.BASE_HEADERS.update(reset_base_headers())
        return identifier
    except Exception as e:  # noqa: BLE001
        logger.warning("Could not apply the stable Plex identity: %s", e)
        return ""
