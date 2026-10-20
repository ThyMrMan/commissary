"""Encrypt the video side's own Plex/Jellyfin credentials at rest.

``video_plex_token`` and ``video_jellyfin_key`` live in video.db's
``video_settings`` table in PLAINTEXT, while music's equivalents
(``plex.token``, ``jellyfin.api_key``) are Fernet-encrypted by the config
manager: the same secret, protected on one side only.

They stay where upstream keeps them. ``VideoDatabase.get_setting`` /
``set_setting`` call ``unseal`` / ``seal`` for just these keys, using the same
key file and Fernet as the music config, so upstream's readers and the settings
UI are unchanged. ``migrate`` re-seals any plaintext row once at startup.

Export: upstream's config export dumps every video setting as-is, which put
these two secrets into the file in plaintext even when the user asked for a
redacted export. They are now left out of the export entirely; after an import
the video side inherits music's server connection until re-entered.
"""

from __future__ import annotations

from utils.logging_config import get_logger

logger = get_logger("commissary.video_secrets")

SECRET_KEYS = frozenset({"video_plex_token", "video_jellyfin_key"})

_FERNET_PREFIX = "gAAAAA"


def _cm():
    from core.settings import config_manager
    return config_manager


def seal(key: str, value):
    """The value to store for ``key``: encrypted when it is a secret."""
    if key not in SECRET_KEYS or not isinstance(value, str) or not value.strip():
        return value
    if value.startswith(_FERNET_PREFIX):       # already sealed (e.g. re-saved row)
        return value
    try:
        return _cm()._encrypt_value(value)
    except Exception:  # noqa: BLE001
        logger.exception("Could not encrypt %s; storing it unencrypted", key)
        return value


def unseal(key: str, value):
    """The usable value of a stored ``key``: decrypted when it is a secret."""
    if key not in SECRET_KEYS or not isinstance(value, str):
        return value
    try:
        return _cm()._decrypt_value(value)
    except Exception:  # noqa: BLE001
        return value


def migrate(db) -> int:
    """Encrypt any plaintext secret rows. Idempotent. Returns rows sealed."""
    sealed = 0
    for key in SECRET_KEYS:
        try:
            raw = db.raw_setting(key)
            if isinstance(raw, str) and raw.strip() and not raw.startswith(_FERNET_PREFIX):
                db.set_setting(key, raw)       # set_setting seals it
                sealed += 1
        except Exception:  # noqa: BLE001
            logger.debug("video secret migration skipped for %s", key, exc_info=True)
    if sealed:
        logger.info("Encrypted %d plaintext video server credential(s)", sealed)
    return sealed
