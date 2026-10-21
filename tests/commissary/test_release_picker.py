"""Choose which release of an album downloads - torrent, NZB or Soulseek folder."""

from __future__ import annotations

import inspect
import json
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from core.commissary import release_picker as rp
from core.download_plugins.candidate_store import get_candidate_store

_ROOT = Path(__file__).resolve().parents[2]


# ── the pin a start request may carry ───────────────────────────────────────
def test_a_prowlarr_pin_is_a_candidate_store_token_never_a_url():
    token = get_candidate_store().put("https://indexer/x.torrent?apikey=SECRET")
    assert rp.pinned_release({"source": "torrent", "token": token, "title": "T"}) == \
        {"source": "torrent", "token": token, "title": "T"}
    assert rp.pinned_release({"source": "torrent", "token": "https://indexer/x.torrent"}) is None
    assert rp.pinned_release({"source": "lidarr", "token": token}) is None


def test_a_soulseek_pin_needs_both_the_user_and_the_folder():
    assert rp.pinned_release({"source": "soulseek", "username": "u", "folder_path": "Music\\A"})["folder_path"] == "Music\\A"
    assert rp.pinned_release({"source": "soulseek", "username": "u"}) is None
    assert rp.pinned_release("junk") is None


def test_the_master_gets_the_plugins_own_arguments():
    token = get_candidate_store().put("https://indexer/y.nzb")
    assert rp.bundle_pin({"source": "usenet", "token": token, "title": "T"}) == \
        ("usenet", {"preferred_release": {"token": token, "title": "T"}})
    source, kwargs = rp.bundle_pin({"source": "soulseek", "username": "u", "folder_path": "F"})
    assert source == "soulseek"
    assert kwargs["preferred_source"] == {"username": "u", "folder_path": "F"}
    assert kwargs["preferred_tracks"] is None, "no track list: the plugin browses the folder"
    assert rp.bundle_pin(None) == (None, None)


@pytest.mark.parametrize("source", ["torrent", "usenet", "soulseek"])
def test_every_pin_binds_against_its_plugin(source):
    from core.download_plugins.torrent import TorrentDownloadPlugin
    from core.download_plugins.usenet import UsenetDownloadPlugin
    from core.soulseek_client import SoulseekClient
    plugin = {"torrent": TorrentDownloadPlugin, "usenet": UsenetDownloadPlugin,
              "soulseek": SoulseekClient}[source]
    pin = {"source": source, "token": get_candidate_store().put(f"https://x/{source}"),
           "username": "u", "folder_path": "F"}
    _src, kwargs = rp.bundle_pin(pin)
    inspect.signature(plugin.download_album_to_staging).bind(
        object(), "Album", "Artist", "/tmp/staging", None,
        quality_profile_id=None, expected_duration_seconds=None, **kwargs)


# ── the plugins take the pinned release ────────────────────────────────────
def _torrent(**over):
    from core.prowlarr_client import ProwlarrSearchResult
    base = dict(guid="g1", title="Danny Brown - Atrocity Exhibition [FLAC]", indexer_id=3,
                indexer_name="Indexer", protocol="torrent", download_url="https://x/a.torrent",
                magnet_uri=None, info_url=None, size=500_000_000, seeders=50, leechers=3, grabs=100,
                publish_date="2026-01-01", categories=[3040], raw={})
    base.update(over)
    return ProwlarrSearchResult(**base)


def _run_torrent(results, preferred_release, tmp_path):
    from core.download_plugins.torrent import TorrentDownloadPlugin
    plugin = TorrentDownloadPlugin()
    adapter = MagicMock()
    adapter.is_configured.return_value = True
    added, emitted = [], []
    with patch.object(plugin, "is_configured", return_value=True), \
         patch.object(plugin._prowlarr, "search", new=AsyncMock(return_value=results)), \
         patch("core.download_plugins.torrent.get_active_torrent_adapter", return_value=adapter), \
         patch("core.torrent_clients.base.add_torrent_smart",
               new=AsyncMock(side_effect=lambda _a, url, **_k: added.append(url))):
        outcome = plugin.download_album_to_staging(
            "Atrocity Exhibition", "Danny Brown", str(tmp_path), emitted.append,
            preferred_release=preferred_release)
    return outcome, added, emitted


def test_torrent_downloads_the_pinned_release_not_its_own_pick(tmp_path):
    best = _torrent()
    chosen = _torrent(guid="g2", title="Danny Brown - Atrocity Exhibition (2016) [MP3 320]",
                      download_url="https://x/b.torrent", seeders=2)
    token = get_candidate_store().put(rp.torrent_key(chosen))
    _outcome, added, emitted = _run_torrent([best, chosen], {"token": token, "title": chosen.title}, tmp_path)
    assert added and added[0] == "https://x/b.torrent"
    assert any(e.get("state") == "queued" and e.get("release") == chosen.title for e in emitted)


def test_a_pin_no_longer_offered_fails_the_album_visibly(tmp_path):
    token = get_candidate_store().put("https://x/gone.torrent")
    outcome, added, _emitted = _run_torrent([_torrent()], {"token": token, "title": "gone"}, tmp_path)
    assert outcome["error"] == rp.PIN_GONE
    assert not outcome.get("fallback"), "never quietly a different release"
    assert added == []


def test_without_a_pin_upstream_still_picks(tmp_path):
    _outcome, added, _emitted = _run_torrent([_torrent()], None, tmp_path)
    assert added == ["https://x/a.torrent"]


def test_usenet_downloads_the_pinned_release(tmp_path):
    from core.download_plugins.usenet import UsenetDownloadPlugin
    from core.prowlarr_client import ProwlarrSearchResult

    def nzb(guid, url, grabs):
        return ProwlarrSearchResult(guid=guid, title=f"Danny Brown - Atrocity Exhibition [FLAC] {guid}",
                                    indexer_id=1, indexer_name="NZB", protocol="usenet",
                                    download_url=url, size=500_000_000, grabs=grabs, categories=[3040])

    best, chosen = nzb("n1", "https://x/1.nzb", 900), nzb("n2", "https://x/2.nzb", 1)
    token = get_candidate_store().put(rp.usenet_key(chosen))
    plugin = UsenetDownloadPlugin()
    adapter = MagicMock()
    adapter.is_configured.return_value = True
    adapter.add_nzb = AsyncMock(return_value=None)
    with patch.object(plugin, "is_configured", return_value=True), \
         patch.object(plugin._prowlarr, "search", new=AsyncMock(return_value=[best, chosen])), \
         patch("core.download_plugins.usenet.get_active_usenet_adapter", return_value=adapter):
        plugin.download_album_to_staging("Atrocity Exhibition", "Danny Brown", str(tmp_path),
                                         preferred_release={"token": token})
    assert adapter.add_nzb.await_args_list[0].args[0] == "https://x/2.nzb"


# ── the master worker ───────────────────────────────────────────────────────
from tests.downloads.test_downloads_master import (  # noqa: E402
    _build_deps, _FakeAlbumBundleSoulseek, _FakeConfig, _FakeDB, _FakePluginWrapper, _seed_batch,
    mw, no_persisted_album_pins, reset_state,
)
from core.runtime_state import download_batches  # noqa: E402

TRACKS = [{"name": "T1", "artists": ["Artist"], "track_number": 1}]


def _master(monkeypatch, config, plugins, pin):
    monkeypatch.setattr("database.music_database.MusicDatabase", lambda: _FakeDB())
    deps = _build_deps(config=_FakeConfig(config), soulseek=_FakePluginWrapper(plugins))
    _seed_batch("BP", is_album_download=True, album_context={"name": "Test Album", "total_tracks": 1},
                artist_context={"name": "Artist"}, commissary_pinned_release=pin)
    mw.run_full_missing_tracks_process("BP", "album:1", TRACKS, deps)


def test_a_pin_overrides_the_configured_source(monkeypatch, reset_state, no_persisted_album_pins):
    usenet, torrent = _FakeAlbumBundleSoulseek(), _FakeAlbumBundleSoulseek()
    token = get_candidate_store().put("https://x/pinned.torrent")
    _master(monkeypatch, {"download_source.mode": "usenet"}, {"usenet": usenet, "torrent": torrent},
            {"source": "torrent", "token": token, "title": "Pinned"})
    assert usenet.calls == []
    assert torrent.calls[0][3]["preferred_release"] == {"token": token, "title": "Pinned"}
    assert download_batches["BP"]["album_bundle_source"] == "torrent"


def test_a_pinned_soulseek_folder_skips_the_preflight(monkeypatch, reset_state, no_persisted_album_pins):
    soulseek = _FakeAlbumBundleSoulseek()
    soulseek.search = MagicMock(return_value=([], []))
    _master(monkeypatch, {"download_source.mode": "hybrid", "download_source.hybrid_order": ["hifi"]},
            {"soulseek": soulseek},
            {"source": "soulseek", "username": "peer", "folder_path": "Music\\Test Album"})
    assert soulseek.search.call_count == 0, "no pre-flight search for a picked folder"
    kwargs = soulseek.calls[0][3]
    assert kwargs["preferred_source"] == {"username": "peer", "folder_path": "Music\\Test Album"}
    assert kwargs["preferred_tracks"] is None


def test_no_pin_keeps_upstreams_choice(monkeypatch, reset_state, no_persisted_album_pins):
    usenet = _FakeAlbumBundleSoulseek()
    _master(monkeypatch, {"download_source.mode": "usenet"}, {"usenet": usenet}, None)
    assert "preferred_release" not in usenet.calls[0][3]


# ── listing ─────────────────────────────────────────────────────────────────
class _Registry:
    def display_name(self, name):
        return name.title()


class _Orch:
    def __init__(self, clients):
        self.clients, self.registry = clients, _Registry()

    def client(self, name):
        return self.clients.get(name)


class _Plugin:
    def __init__(self, configured=True):
        self.configured = configured

    def is_configured(self):
        return self.configured

    def download_album_to_staging(self, *a, **k):
        raise AssertionError("listing never downloads")


def test_the_stream_answers_per_source_and_survives_one_failing():
    orch = _Orch({"torrent": _Plugin(), "usenet": _Plugin(), "soulseek": _Plugin(configured=False)})

    def lister(source, plugin, album, artist):
        if source == "usenet":
            raise RuntimeError("indexer down")
        return [{"source": source, "title": f"{artist} - {album}"}]

    lines = [json.loads(line) for line in rp.stream_releases(orch, "Album", "Artist", lister=lister)]
    assert lines[0] == {"type": "header", "sources": ["torrent", "usenet"]}
    by_source = {line["source"]: line for line in lines if line["type"] == "source"}
    assert by_source["torrent"]["releases"][0]["title"] == "Artist - Album"
    assert by_source["usenet"]["error"] == "indexer down"
    assert lines[-1] == {"type": "done"}


def test_torrent_listing_runs_the_album_downloads_own_search_and_marks_its_pick():
    from core.download_plugins.torrent import TorrentDownloadPlugin
    plugin = TorrentDownloadPlugin()
    best = _torrent()
    other = _torrent(guid="g2", title="Danny Brown - Atrocity Exhibition [MP3]",
                     download_url="https://x/b.torrent", seeders=1)
    with patch.object(plugin._prowlarr, "search", new=AsyncMock(return_value=[other, best])):
        rows = rp.releases_for("torrent", plugin, "Atrocity Exhibition", "Danny Brown")
    assert rows[0]["title"] == best.title and rows[0]["auto_pick"] is True
    assert all(get_candidate_store().is_token(r["token"]) for r in rows)
    assert "https://" not in json.dumps(rows), "no indexer URL reaches the browser"
    assert get_candidate_store().resolve(rows[1]["token"]) == rp.torrent_key(other)


def test_the_route_needs_download_rights(monkeypatch):
    web_server = pytest.importorskip("web_server")
    web_server.app.config["TESTING"] = True
    monkeypatch.setattr(web_server, "check_download_permission",
                        lambda: (web_server.jsonify({"error": "no"}), 403))
    with web_server.app.test_client() as client:
        assert client.post("/api/commissary/album-releases",
                           json={"album": "A", "artist": "B"}).status_code == 403


def test_the_start_route_keeps_a_valid_pin_for_an_album_only():
    web_server = pytest.importorskip("web_server")
    token = get_candidate_store().put("https://x/c.torrent")
    pin = {"source": "torrent", "token": token, "title": "C"}
    assert web_server._commissary_release_pin({"commissary_pinned_release": pin}, True)["token"] == token
    assert web_server._commissary_release_pin({"commissary_pinned_release": pin}, False) is None
    src = (_ROOT / "web_server.py").read_text(encoding="utf-8")
    assert "'commissary_pinned_release': _commissary_release_pin(data, is_album_download)," in src


def test_the_album_window_offers_it_and_sends_the_pick():
    helpers = (_ROOT / "webui" / "static" / "shared-helpers.js").read_text(encoding="utf-8")
    assert "window.commissaryChooseReleaseButton(virtualPlaylistId)" in helpers
    downloads = (_ROOT / "webui" / "static" / "downloads.js").read_text(encoding="utf-8")
    assert "requestBody.commissary_pinned_release = process.commissaryPinnedRelease;" in downloads
    index = (_ROOT / "webui" / "index.html").read_text(encoding="utf-8")
    assert "filename='commissary-release-picker.js'" in index
