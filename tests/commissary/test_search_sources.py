"""Every configured download source is searchable by hand, the chain first.

The download order says which sources the AUTOMATIC cascade tries. It hid a
connected, working source from manual search unless the order was changed too.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from core.commissary import search_sources as ss
from core.download_plugins.registry import PluginSpec


class _Client:
    def __init__(self, configured=True):
        self.configured = configured

    def is_configured(self):
        return self.configured


class _Registry:
    def __init__(self, clients):
        self.clients = clients
        self.specs = {n: PluginSpec(name=n, factory=object, display_name=n.title()) for n in clients}

    def get_spec(self, name):
        return self.specs.get(name)

    def all_plugins(self):
        return list(self.clients.items())


class _Orchestrator:
    def __init__(self, mode, clients):
        self.mode, self.clients = mode, clients
        self.registry = _Registry(clients)

    def client(self, name):
        return self.clients.get(name)


class _Config:
    def __init__(self, values):
        self.values = values

    def get(self, key, default=None):
        return self.values.get(key, default)


def _orch(mode="hybrid", off=()):
    names = ("soulseek", "tidal", "qobuz", "torrent", "deezer")
    return _Orchestrator(mode, {n: _Client(n not in off) for n in names})


def _cfg(mode="hybrid", chain=("tidal", "soulseek")):
    return _Config({"download_source.mode": mode, "download_source.hybrid_order": list(chain)})


def test_every_configured_source_is_offered_the_chain_first():
    out = ss.sources(_orch(off=("deezer",)), _cfg())
    assert [s["name"] for s in out] == ["tidal", "soulseek", "qobuz", "torrent"]
    assert [s["in_chain"] for s in out] == [True, True, False, False]
    assert [s["chain_position"] for s in out] == [0, 1, None, None]
    assert [s["release_level"] for s in out] == [False, False, False, True]


def test_a_single_source_mode_offers_the_rest_too():
    out = ss.sources(_orch(mode="soulseek"), _cfg("soulseek"))
    assert out[0]["name"] == "soulseek" and out[0]["in_chain"]
    assert {s["name"] for s in out} == {"soulseek", "tidal", "qobuz", "torrent", "deezer"}


def test_the_mode_you_picked_stays_offered_while_it_is_mid_setup():
    out = ss.sources(_orch(mode="qobuz", off=("qobuz",)), _cfg("qobuz"))
    assert out[0]["name"] == "qobuz"


def test_manual_search_keeps_upstreams_field_names():
    mode, out = ss.manual_search_sources(_orch(), _cfg())
    assert mode == "hybrid"
    assert (out[0]["id"], out[0]["label"]) == ("tidal", "Tidal")


# ── the endpoints ───────────────────────────────────────────────────────────
@pytest.fixture
def app(monkeypatch):
    web_server = pytest.importorskip("web_server")
    monkeypatch.setattr(web_server, "download_orchestrator", _orch(mode="soulseek"))
    real_get = web_server.config_manager.get
    monkeypatch.setattr(web_server.config_manager, "get", lambda key, default=None:
                        "soulseek" if key == "download_source.mode" else real_get(key, default))
    web_server.app.config["TESTING"] = True
    return web_server


def test_the_search_page_lists_every_source(app):
    with app.app.test_client() as client:
        data = client.get("/api/search/sources").get_json()
    assert data["mode"] == "soulseek"
    assert [s["name"] for s in data["sources"]][0] == "soulseek"
    assert len(data["sources"]) == 5


def test_manual_search_lists_every_source(app):
    mode, out = app._list_available_download_sources()
    assert len(out) == 5 and out[0]["id"] == "soulseek"


def test_the_candidates_window_shows_a_dropdown_for_several_sources():
    src = (Path(__file__).resolve().parents[2] / "webui" / "static" / "downloads.js").read_text(encoding="utf-8")
    assert "const isHybrid = downloadMode === 'hybrid' || availableSources.length > 1;" in src
    assert '<optgroup label="Other sources">' in src
