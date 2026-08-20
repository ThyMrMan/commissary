"""A magnet won whenever an indexer offered both, and could stall forever.

Upstream #1139 was reported against the music album flow. This fork took it
THERE — core/download_plugins/torrent.py's album path already retried with the
magnet — and nowhere else. Three sites still read ``magnet_uri or
download_url``: the single-track music grab, the video Prowlarr search, and the
video RSS sync.

A magnet hands the client an info-hash and nothing else; it has to find the
swarm itself, and one that cannot parks on "downloading metadata" with zero
size and zero peers indefinitely. ``add_torrent_smart`` exists to fetch the real
.torrent server-side and push the file bytes, and with a magnet in hand it could
never be reached.

Flipping the preference ALONE would trade one failure for another: in a split
install where Commissary cannot reach the indexer but the CLIENT can, a magnet
that worked would be lost. So the magnet is carried the whole way — search hit
-> candidate token (music) or grab payload (video) -> grab() -> grab_torrent()
-> add_torrent_smart's fallback. That carrier is the work; the preference is a
two-word change, which is why the guards below sit on the ``or`` expressions
themselves: a revert would be exactly as small.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from types import SimpleNamespace

import pytest

_ROOT = Path(__file__).resolve().parent.parent


# ── the preference itself ───────────────────────────────────────────────────

class TestTheOrExpressions:
    """Source guards. The defect was a one-line ``or`` in each file."""

    @pytest.mark.parametrize("rel", [
        "core/video/prowlarr_search.py",
        "core/video/rss_sync.py",
    ])
    def test_the_video_paths_prefer_the_torrent_url(self, rel):
        src = (_ROOT / rel).read_text(encoding="utf-8")
        assert 'getattr(r, "download_url", None) or getattr(r, "magnet_uri", None)' in src
        assert 'getattr(r, "magnet_uri", None) or getattr(r, "download_url", None)' not in src

    def test_the_music_single_track_grab_prefers_it_too(self):
        src = (_ROOT / "core/download_plugins/torrent.py").read_text(encoding="utf-8")
        assert "download_url = result.download_url or result.magnet_uri" in src
        assert "download_url = result.magnet_uri or result.download_url" not in src


# ── the carrier: add_torrent_smart ──────────────────────────────────────────

class _Adapter:
    """Records what it was handed, and can refuse the URL handoff."""

    def __init__(self, *, refuse_url=False):
        self.refuse_url = refuse_url
        self.added = []
        self.files = []

    async def add_torrent(self, what, category=None, save_path=None):
        self.added.append(what)
        if self.refuse_url and str(what).startswith("http"):
            return None
        return "HASH-" + str(what)[:12]

    async def add_torrent_file(self, data, category=None, save_path=None):
        self.files.append(data)
        return "HASH-FILE"


def _smart(adapter, url, magnet=None, *, payload=(None, None), monkeypatch=None):
    from core.torrent_clients import base
    monkeypatch.setattr(base, "fetch_torrent_payload", lambda u: payload)
    return asyncio.run(base.add_torrent_smart(adapter, url, fallback_magnet=magnet))


class TestAddTorrentSmartFallback:
    def test_a_refused_url_retries_as_the_magnet(self, monkeypatch):
        """THE point of carrying it: a split install where this process cannot
        reach the indexer but the client can."""
        a = _Adapter(refuse_url=True)
        ref = _smart(a, "https://indexer/dl?apikey=x", "magnet:?xt=urn:btih:abc",
                     monkeypatch=monkeypatch)
        # the URL was tried FIRST and the magnet only after it was refused
        assert a.added == ["https://indexer/dl?apikey=x", "magnet:?xt=urn:btih:abc"]
        assert ref is not None and ref.startswith("HASH-magnet")

    def test_a_working_url_never_reaches_the_magnet(self, monkeypatch):
        a = _Adapter()
        ref = _smart(a, "https://indexer/dl", "magnet:?xt=urn:btih:abc",
                     monkeypatch=monkeypatch)
        assert ref and a.added == ["https://indexer/dl"]

    def test_the_server_side_fetch_still_wins_when_it_works(self, monkeypatch):
        """The whole reason to prefer the URL — real .torrent bytes pushed to
        the client, no swarm discovery needed."""
        a = _Adapter(refuse_url=True)
        ref = _smart(a, "https://indexer/dl", "magnet:?xt=urn:btih:abc",
                     payload=(b"d8:announce", None), monkeypatch=monkeypatch)
        assert ref == "HASH-FILE" and a.files == [b"d8:announce"]

    def test_no_magnet_carried_is_the_old_behaviour(self, monkeypatch):
        a = _Adapter(refuse_url=True)
        assert _smart(a, "https://indexer/dl", None, monkeypatch=monkeypatch) is None

    def test_a_bare_magnet_url_goes_straight_through(self, monkeypatch):
        a = _Adapter()
        ref = _smart(a, "magnet:?xt=urn:btih:zzz", None, monkeypatch=monkeypatch)
        assert ref and a.added == ["magnet:?xt=urn:btih:zzz"]


# ── the carrier: the music candidate store ─────────────────────────────────

class TestCandidateStoreCarriesTheMagnet:
    def _store(self):
        from core.download_plugins.candidate_store import CandidateStore
        return CandidateStore()

    def test_the_magnet_comes_back_with_its_token(self):
        s = self._store()
        t = s.put("https://indexer/dl", magnet="magnet:?xt=urn:btih:abc")
        assert s.resolve(t) == "https://indexer/dl"
        assert s.resolve_magnet(t) == "magnet:?xt=urn:btih:abc"

    def test_a_candidate_with_no_magnet_reports_none(self):
        s = self._store()
        t = s.put("https://indexer/dl")
        assert s.resolve_magnet(t) is None

    def test_an_unknown_token_reports_none(self):
        s = self._store()
        assert s.resolve_magnet("ssc1-nope") is None
        assert s.resolve_magnet("not-a-token") is None

    def test_re_registering_refreshes_the_magnet(self):
        """A second search sees the same release; the magnet must not be lost
        just because the URL deduplicated to an existing token."""
        s = self._store()
        t1 = s.put("https://indexer/dl")
        t2 = s.put("https://indexer/dl", magnet="magnet:?xt=urn:btih:abc")
        assert t1 == t2 and s.resolve_magnet(t2) == "magnet:?xt=urn:btih:abc"

    def test_an_expired_token_forgets_its_magnet_too(self):
        from core.download_plugins.candidate_store import CandidateStore
        s = CandidateStore(ttl_seconds=-1)
        t = s.put("https://indexer/dl", magnet="magnet:?xt=urn:btih:abc")
        assert s.resolve(t) is None
        assert s.resolve_magnet(t) is None

    def test_eviction_does_not_leave_the_magnet_behind(self):
        from core.download_plugins.candidate_store import CandidateStore
        s = CandidateStore(max_entries=2)
        first = s.put("https://a", magnet="magnet:a")
        s.put("https://b", magnet="magnet:b")
        s.put("https://c", magnet="magnet:c")
        # the store is capped; whatever was evicted must have taken its magnet
        for tok in (first,):
            if s.resolve(tok) is None:
                assert s.resolve_magnet(tok) is None
        assert len(s._magnet_by_token) <= len(s._by_token)


# ── the carrier: the video hit and the shared dispatcher ───────────────────

class TestTheVideoHitCarriesIt:
    def test_the_projection_includes_the_magnet(self):
        from core.video.prowlarr_search import _project
        r = SimpleNamespace(title="Show S01E01", size=100, seeders=5, leechers=1,
                            indexer_name="ix", protocol="torrent", indexer_id=3,
                            guid="g", info_url=None, publish_date=None, grabs=0,
                            magnet_uri="magnet:?xt=urn:btih:abc",
                            download_url="https://indexer/dl")
        hit = _project(r, "https://indexer/dl", "torrent")
        assert hit["download_url"] == "https://indexer/dl"
        assert hit["magnet_uri"] == "magnet:?xt=urn:btih:abc"

    def test_a_release_with_no_magnet_projects_none(self):
        from core.video.prowlarr_search import _project
        r = SimpleNamespace(title="X", size=1, seeders=None, leechers=None,
                            indexer_name=None, protocol="torrent", indexer_id=None,
                            guid=None, info_url=None, publish_date=None, grabs=0,
                            download_url="https://indexer/dl")
        assert _project(r, "https://indexer/dl", "torrent")["magnet_uri"] is None


class TestGrabDispatch:
    def test_torrent_receives_the_fallback(self, monkeypatch):
        from core.video import client_grab
        seen = {}
        monkeypatch.setattr(client_grab, "grab_torrent",
                            lambda url, **kw: seen.update(kw) or {"ok": True, "ref": "r"})
        client_grab.grab("torrent", "https://x", fallback_magnet="magnet:?xt=1")
        assert seen.get("fallback_magnet") == "magnet:?xt=1"

    def test_usenet_is_never_handed_one(self, monkeypatch):
        """An NZB has no magnet. The parameter rides the shared signature so
        callers need not branch on source — it must not leak across."""
        from core.video import client_grab
        seen = {}
        monkeypatch.setattr(client_grab, "grab_usenet",
                            lambda url, **kw: seen.update(kw) or {"ok": True, "ref": "r"})
        client_grab.grab("usenet", "https://x", fallback_magnet="magnet:?xt=1")
        assert "fallback_magnet" not in seen


def test_every_hop_of_the_video_chain_passes_it_along():
    """Source guard over the carrier. Any hop that drops it silently restores
    the original failure for the paths beyond it, and nothing else would fail."""
    api = (_ROOT / "api/video/downloads.py").read_text(encoding="utf-8")
    assert 'fallback_magnet=body.get("magnet_uri")' in api

    auto = (_ROOT / "core/automation/handlers/video_process_wishlist.py").read_text(
        encoding="utf-8")
    assert 'fallback_magnet=best.get("magnet_uri")' in auto

    for rel in ("webui/static/video/video-grab.js",
                "webui/static/video/video-download-view.js"):
        js = (_ROOT / rel).read_text(encoding="utf-8")
        assert "magnet_uri" in js, rel
