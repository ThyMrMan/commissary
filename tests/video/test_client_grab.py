"""core.video.client_grab — the shared torrent/usenet hand-off. Covers the
per-Library category override (multi-category, P.4): an explicit ``category``
kwarg must win over the global torrent_client.category/usenet_client.category
setting, and omitting it must preserve the pre-existing global-default behavior."""

from __future__ import annotations

import core.video.client_grab as cg


class _FakeAdapter:
    def __init__(self):
        self.configured = True

    def is_configured(self):
        return self.configured


def test_grab_torrent_uses_explicit_category_over_global(monkeypatch):
    monkeypatch.setattr(cg, "_torrent_category", lambda: "soulsync")
    monkeypatch.setattr("core.torrent_clients.get_active_adapter", lambda: _FakeAdapter())
    seen = {}

    async def fake_add_torrent_smart(adapter, url, category=None, save_path=None,
                                     fallback_magnet=None):
        seen["category"] = category
        seen["save_path"] = save_path
        return "abc123"

    monkeypatch.setattr("core.torrent_clients.base.add_torrent_smart", fake_add_torrent_smart)

    res = cg.grab_torrent("magnet:?xt=x", category="anime", save_path="/dl")
    assert res == {"ok": True, "ref": "abc123"}
    assert seen == {"category": "anime", "save_path": "/dl"}


def test_grab_torrent_falls_back_to_global_category_when_omitted(monkeypatch):
    monkeypatch.setattr(cg, "_torrent_category", lambda: "soulsync")
    monkeypatch.setattr("core.torrent_clients.get_active_adapter", lambda: _FakeAdapter())
    seen = {}

    async def fake_add_torrent_smart(adapter, url, category=None, save_path=None,
                                     fallback_magnet=None):
        seen["category"] = category
        return "abc123"

    monkeypatch.setattr("core.torrent_clients.base.add_torrent_smart", fake_add_torrent_smart)

    res = cg.grab_torrent("magnet:?xt=x")
    assert res["ok"] is True
    assert seen["category"] == "soulsync"   # unchanged pre-existing behavior


def test_grab_torrent_no_client_configured():
    res = cg.grab_torrent("magnet:?xt=x", category="anime")
    assert res == {"ok": False, "error": "No torrent client configured — set it on Settings → Downloads."}


def test_grab_usenet_uses_explicit_category_over_global(monkeypatch):
    monkeypatch.setattr(cg, "_usenet_category", lambda: "soulsync")

    class _FakeUsenetAdapter(_FakeAdapter):
        async def add_nzb(self, url, category=None, save_path=None):
            seen["category"] = category
            return "SABnzbd_nzo_1"

    seen = {}
    monkeypatch.setattr("core.usenet_clients.get_active_adapter", lambda: _FakeUsenetAdapter())

    res = cg.grab_usenet("http://example.com/x.nzb", category="movies")
    assert res == {"ok": True, "ref": "SABnzbd_nzo_1"}
    assert seen["category"] == "movies"


def test_grab_dispatches_category_by_source(monkeypatch):
    calls = []
    monkeypatch.setattr(cg, "grab_torrent",
                        lambda url, *, category=None, save_path=None, fallback_magnet=None:
                        calls.append(("torrent", category)) or {"ok": True, "ref": "t"})
    monkeypatch.setattr(cg, "grab_usenet", lambda url, *, category=None, save_path=None:
                        calls.append(("usenet", category)) or {"ok": True, "ref": "u"})

    cg.grab("torrent", "magnet:?xt=x", category="tv")
    cg.grab("usenet", "http://x/y.nzb", category="anime")
    assert calls == [("torrent", "tv"), ("usenet", "anime")]
