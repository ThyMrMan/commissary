"""Plex sees the same device across restarts instead of a new one each boot."""

import plexapi
import pytest

from core.commissary import plex_identity


class _Cfg:
    def __init__(self, **values):
        self.values = dict(values)

    def get(self, key, default=None):
        return self.values.get(key, default)

    def set(self, key, value):
        self.values[key] = value


@pytest.fixture()
def restore_plexapi():
    saved = (plexapi.X_PLEX_IDENTIFIER, plexapi.X_PLEX_PRODUCT, plexapi.X_PLEX_DEVICE,
             plexapi.X_PLEX_DEVICE_NAME, plexapi.X_PLEX_VERSION, dict(plexapi.BASE_HEADERS))
    yield
    (plexapi.X_PLEX_IDENTIFIER, plexapi.X_PLEX_PRODUCT, plexapi.X_PLEX_DEVICE,
     plexapi.X_PLEX_DEVICE_NAME, plexapi.X_PLEX_VERSION, headers) = saved
    plexapi.BASE_HEADERS.clear()
    plexapi.BASE_HEADERS.update(headers)


def test_an_existing_legacy_identifier_is_kept_and_shared_with_sign_in():
    cfg = _Cfg(**{"plex.client_identifier": "legacy-id"})
    assert plex_identity.get_client_identifier(cfg) == "legacy-id"
    assert cfg.values["plex_signin.client_id"] == "legacy-id"


def test_upstreams_sign_in_id_is_adopted_when_there_is_no_legacy_one():
    cfg = _Cfg(**{"plex_signin.client_id": "soulsync-abc"})
    assert plex_identity.get_client_identifier(cfg) == "soulsync-abc"
    assert cfg.values["plex.client_identifier"] == "soulsync-abc"


def test_a_minted_identifier_is_persisted_and_stable():
    cfg = _Cfg()
    first = plex_identity.get_client_identifier(cfg)
    assert first and plex_identity.get_client_identifier(cfg) == first
    assert cfg.values["plex.client_identifier"] == first == cfg.values["plex_signin.client_id"]


def test_plexapi_requests_carry_the_stable_identity(restore_plexapi):
    cfg = _Cfg(**{"plex.client_identifier": "stable-123"})
    assert plex_identity.apply_plex_identity(cfg, "3.5.7-c.1") == "stable-123"
    assert plexapi.BASE_HEADERS["X-Plex-Client-Identifier"] == "stable-123"
    assert plexapi.BASE_HEADERS["X-Plex-Device-Name"] == "SoulSync"
    import plexapi.myplex as myplex
    assert myplex.X_PLEX_IDENTIFIER == "stable-123"


def test_the_headers_dict_plexapi_server_holds_is_updated_in_place(restore_plexapi):
    """plexapi.server imported BASE_HEADERS itself; rebinding the name would
    leave the server module sending the old MAC-derived id."""
    import plexapi.server as server
    held = server.BASE_HEADERS
    plex_identity.apply_plex_identity(_Cfg(**{"plex.client_identifier": "in-place"}))
    assert held is plexapi.BASE_HEADERS
    assert held["X-Plex-Client-Identifier"] == "in-place"


def test_the_app_applies_it_at_startup():
    import web_server
    from core.settings import config_manager

    ident = config_manager.get("plex.client_identifier", "")
    assert ident
    assert web_server  # imported: the startup hook ran
    assert "_apply_plex_identity(config_manager" in open(web_server.__file__, encoding="utf-8").read()


def test_failure_never_stops_boot(monkeypatch):
    class Broken(_Cfg):
        def get(self, key, default=None):
            raise RuntimeError("config down")

    assert plex_identity.apply_plex_identity(Broken()) == ""
