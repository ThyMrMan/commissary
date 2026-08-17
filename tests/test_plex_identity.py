"""The Plex client identity must survive a restart.

plexapi's defaults are MAC address + hostname, both of which Docker
regenerates on every container start — that is what made Plex announce a new
"Linux" device after each reboot. These tests pin the fix.
"""

import sys

import pytest

import core.plex_identity as plex_identity


class _FakeConfig:
    def __init__(self, initial=None):
        self.data = dict(initial or {})
        self.writes = 0

    def get(self, key, default=None):
        return self.data.get(key, default)

    def set(self, key, value):
        self.data[key] = value
        self.writes += 1


@pytest.fixture
def fake_config(monkeypatch):
    cfg = _FakeConfig()
    monkeypatch.setattr(plex_identity, "config_manager", cfg)
    return cfg


@pytest.fixture(autouse=True)
def restore_plexapi_globals():
    """plexapi's globals are process-wide; put them back after each test."""
    import plexapi

    names = (
        "X_PLEX_IDENTIFIER", "X_PLEX_PRODUCT", "X_PLEX_DEVICE",
        "X_PLEX_DEVICE_NAME", "X_PLEX_VERSION",
    )
    saved = {n: getattr(plexapi, n) for n in names}
    saved_headers = dict(plexapi.BASE_HEADERS)
    saved_myplex = getattr(sys.modules.get("plexapi.myplex"), "X_PLEX_IDENTIFIER", None)
    try:
        yield
    finally:
        for n, v in saved.items():
            setattr(plexapi, n, v)
        plexapi.BASE_HEADERS.clear()
        plexapi.BASE_HEADERS.update(saved_headers)
        if saved_myplex is not None:
            sys.modules["plexapi.myplex"].X_PLEX_IDENTIFIER = saved_myplex


def test_identifier_is_minted_once_and_persisted(fake_config):
    first = plex_identity.get_client_identifier()
    assert first
    assert fake_config.data[plex_identity.IDENTIFIER_KEY] == first
    assert fake_config.writes == 1

    # A restart re-reads the stored value rather than minting a new one —
    # this is the whole point of the fix.
    assert plex_identity.get_client_identifier() == first
    assert fake_config.writes == 1


def test_a_stored_identifier_is_never_overwritten(fake_config):
    fake_config.data[plex_identity.IDENTIFIER_KEY] = "already-known"
    assert plex_identity.apply_plex_identity() == "already-known"
    assert fake_config.writes == 0


def test_blank_stored_value_is_treated_as_missing(fake_config):
    fake_config.data[plex_identity.IDENTIFIER_KEY] = "   "
    minted = plex_identity.get_client_identifier()
    assert minted.strip() == minted and minted


def test_apply_rewrites_the_live_base_headers(fake_config):
    import plexapi

    plex_identity.apply_plex_identity("9.9.9")

    ident = fake_config.data[plex_identity.IDENTIFIER_KEY]
    assert plexapi.BASE_HEADERS["X-Plex-Client-Identifier"] == ident
    assert plexapi.BASE_HEADERS["X-Plex-Product"] == "Commissary"
    assert plexapi.BASE_HEADERS["X-Plex-Device-Name"] == "Commissary"
    assert plexapi.BASE_HEADERS["X-Plex-Version"] == "9.9.9"


def test_the_headers_dict_is_mutated_in_place(fake_config):
    """plexapi.server did ``from plexapi import BASE_HEADERS``, so rebinding
    the package attribute would leave every server request using the old MAC."""
    import plexapi
    from plexapi.server import BASE_HEADERS as server_headers

    plex_identity.apply_plex_identity()

    assert server_headers is plexapi.BASE_HEADERS
    assert server_headers["X-Plex-Client-Identifier"] == fake_config.data[plex_identity.IDENTIFIER_KEY]


def test_myplex_module_copy_is_refreshed(fake_config):
    """myplex binds X_PLEX_IDENTIFIER by value and uses it as the PIN-login
    clientId — Sign in with Plex would otherwise still use the MAC address."""
    import plexapi.myplex

    plex_identity.apply_plex_identity()

    assert plexapi.myplex.X_PLEX_IDENTIFIER == fake_config.data[plex_identity.IDENTIFIER_KEY]


def test_apply_never_raises(monkeypatch):
    """A broken identity must not stop the app from booting."""
    class _Boom:
        def get(self, *a, **k):
            raise RuntimeError("config is down")

        def set(self, *a, **k):
            raise RuntimeError("config is down")

    monkeypatch.setattr(plex_identity, "config_manager", _Boom())
    assert plex_identity.apply_plex_identity("1.0.0") == ""


def test_web_server_applies_the_identity_before_importing_plexapi_submodules():
    """Ordering matters: myplex is imported near the top of web_server."""
    from pathlib import Path

    src = Path(__file__).resolve().parent.parent / "web_server.py"
    text = src.read_text(encoding="utf-8", errors="ignore")

    apply_at = text.index("apply_plex_identity(SOULSYNC_VERSION)")
    myplex_at = text.index("from plexapi.myplex import")
    assert apply_at < myplex_at
