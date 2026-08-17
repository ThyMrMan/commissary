"""Stable Plex client identity.

plexapi derives ``X-Plex-Client-Identifier`` from ``hex(uuid.getnode())`` — the
machine's MAC address — and the device name from the hostname. Inside Docker
BOTH are regenerated on every container start, so Plex saw a brand-new device
after each reboot: the account got a "new device signed in" mail, and the
Devices list filled up with anonymous ``Linux`` entries named after a random
container hostname.

Fix: mint one identifier, persist it in config, and hand Plex a real product
and device name so the entry is recognisable. plexapi reads these from module
globals at import time, so applying them means rewriting the globals AND the
already-built ``BASE_HEADERS`` dict in place.
"""

from __future__ import annotations

import sys
import uuid

from config.settings import config_manager
from utils.logging_config import get_logger

logger = get_logger("plex_identity")

# Where the minted identifier is persisted. Deliberately under ``plex`` so it
# travels with the rest of the connection settings through config export and
# the backup/restore round-trip — a restored install keeps its Plex identity
# instead of announcing itself as new.
IDENTIFIER_KEY = "plex.client_identifier"

# What the user will see in Plex's Devices list, in place of "Linux".
PLEX_PRODUCT = "Commissary"
PLEX_DEVICE = "Commissary"
PLEX_DEVICE_NAME = "Commissary"


def get_client_identifier() -> str:
    """Return the persisted client identifier, minting one on first call."""
    existing = config_manager.get(IDENTIFIER_KEY, "")
    if isinstance(existing, str) and existing.strip():
        return existing.strip()

    minted = str(uuid.uuid4())
    config_manager.set(IDENTIFIER_KEY, minted)
    logger.info("Minted a new stable Plex client identifier")
    return minted


def apply_plex_identity(version: str = "") -> str:
    """Point plexapi at our stable identity. Returns the identifier in use.

    Safe to call more than once, and never fatal: a failure here only means
    Plex keeps seeing new devices, which must not stop the app from booting.
    """
    try:
        import plexapi
        from plexapi.config import reset_base_headers

        identifier = get_client_identifier()

        plexapi.X_PLEX_IDENTIFIER = identifier
        plexapi.X_PLEX_PRODUCT = PLEX_PRODUCT
        plexapi.X_PLEX_DEVICE = PLEX_DEVICE
        plexapi.X_PLEX_DEVICE_NAME = PLEX_DEVICE_NAME
        if version:
            plexapi.X_PLEX_VERSION = version

        # Submodules do ``from plexapi import X_PLEX_IDENTIFIER``, which binds
        # the value at THEIR import time — notably plexapi.myplex, which uses
        # it as the default clientId for PIN login (Sign in with Plex). Setting
        # the package global alone would leave those copies stale.
        for name, module in list(sys.modules.items()):
            if name == "plexapi" or not name.startswith("plexapi."):
                continue
            if getattr(module, "X_PLEX_IDENTIFIER", None) is not None:
                module.X_PLEX_IDENTIFIER = identifier

        # ``plexapi.server`` did ``from plexapi import BASE_HEADERS`` and copies
        # that dict per request, so rebinding plexapi.BASE_HEADERS would be
        # invisible to it. Mutate the existing dict instead.
        plexapi.BASE_HEADERS.clear()
        plexapi.BASE_HEADERS.update(reset_base_headers())

        return identifier
    except Exception as e:
        logger.warning("Could not apply the stable Plex identity: %s", e)
        return ""
