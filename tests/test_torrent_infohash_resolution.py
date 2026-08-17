"""A successful torrent add must not be reported as a rejection.

Reported on a manual grab into an Anime-Movies Library: "The torrent client
didn't accept the release" — while the torrent was in the client and
downloading.

The adapter learned a new torrent's hash by listing the client's torrents before
and after the add and diffing, polling ~5s for the difference to appear. That is
a race. qBittorrent takes longer whenever it is resolving magnet metadata, the
client is busy, or the torrent list is big enough that /torrents/info is slow.

The consequence is worse than the wrong message: with no hash the grab is
recorded as FAILED, so nothing ever polls the download, and the completed file is
never imported. It arrives and Commissary does not know it exists.

The hash is knowable before the add in both real cases — a magnet carries it, a
.torrent is the SHA-1 of its bencoded ``info`` value — so it only has to be
confirmed present, which is a lookup rather than a race.
"""

from __future__ import annotations

import base64
import hashlib

import pytest

from core.torrent_clients import infohash


def _bs(s: bytes) -> bytes:
    """A bencoded byte string with its length computed, so the fixtures can't
    drift out of spec the way a hand-counted one does."""
    return str(len(s)).encode() + b":" + s


INFO = (b"d" + _bs(b"length") + b"i1024e" + _bs(b"name") + _bs(b"file.mkv")
        + _bs(b"piece length") + b"i16384e" + b"e")
TORRENT = (b"d" + _bs(b"announce") + _bs(b"http://tracker/announce")
           + _bs(b"info") + INFO + b"e")
WANT = hashlib.sha1(INFO).hexdigest()          # noqa: S324
HEX = "0123456789abcdef0123456789abcdef01234567"


# ── deriving the hash ────────────────────────────────────────────────────────
def test_a_torrent_file_hashes_its_own_info_dict():
    assert infohash.from_torrent_bytes(TORRENT) == WANT


def test_info_is_found_wherever_it_sits_in_the_dict():
    """Key order is not guaranteed, and a real torrent carries announce-list,
    comment, creation date… before it."""
    t = (b"d" + _bs(b"announce-list") + b"l" + b"l" + _bs(b"http://a") + b"e" + b"e"
         + _bs(b"comment") + _bs(b"hi") + _bs(b"creation date") + b"i1700000000e"
         + _bs(b"info") + INFO + _bs(b"zz") + b"i7e" + b"e")
    assert infohash.from_torrent_bytes(t) == WANT


def test_the_original_bytes_are_hashed_not_a_re_encoding():
    """The info-hash is defined over the exact bytes. Re-encoding a parsed
    structure could reorder keys and produce a hash matching nothing."""
    assert hashlib.sha1(TORRENT[TORRENT.index(b"4:info") + 6:-1]).hexdigest() == WANT   # noqa: S324


def test_a_magnet_carries_its_hash():
    assert infohash.from_magnet("magnet:?xt=urn:btih:%s&dn=Some.Release" % HEX) == HEX


def test_an_uppercase_magnet_hash_is_normalised():
    assert infohash.from_magnet("magnet:?xt=urn:btih:%s" % HEX.upper()) == HEX


def test_a_base32_magnet_is_converted_to_hex():
    """Some indexers still emit base32; every client API speaks hex."""
    b32 = base64.b32encode(bytes.fromhex(HEX)).decode()
    assert infohash.from_magnet("magnet:?xt=urn:btih:%s" % b32) == HEX


@pytest.mark.parametrize("bad", [
    None, "", "   ", "https://tracker/file.torrent", "magnet:?dn=NoHashHere",
    "magnet:?xt=urn:btih:nothex", "magnet:?xt=urn:sha1:%s" % HEX,
])
def test_nothing_is_invented_when_the_hash_is_absent(bad):
    assert infohash.from_magnet(bad) is None


@pytest.mark.parametrize("bad", [
    b"", b"not a torrent", b"d", b"dxyz", b"d4:infoe", None, "a string",
])
def test_malformed_input_returns_none_rather_than_raising(bad):
    assert infohash.from_torrent_bytes(bad) is None


def test_expected_hash_dispatches_on_payload_type():
    assert infohash.expected_hash(TORRENT) == WANT
    assert infohash.expected_hash("magnet:?xt=urn:btih:%s" % HEX) == HEX
    assert infohash.expected_hash("https://tracker/f.torrent") is None


# ── the adapter uses it ──────────────────────────────────────────────────────
class _Q:
    """Just enough qBittorrent adapter to drive _confirm_or_poll."""

    def __init__(self, hashes_over_time):
        from core.torrent_clients.qbittorrent import QBittorrentAdapter
        self._seq = list(hashes_over_time)
        self.polls = 0
        self._confirm_or_poll = QBittorrentAdapter._confirm_or_poll.__get__(self)
        self._poll_for_new_hash = QBittorrentAdapter._poll_for_new_hash.__get__(self)

    def _all_hashes(self):
        self.polls += 1
        return self._seq[min(self.polls - 1, len(self._seq) - 1)]


def test_a_known_hash_is_confirmed_without_needing_a_new_one(monkeypatch):
    """The fix: the torrent is present, so return it — no diff required."""
    import core.torrent_clients.qbittorrent as q
    monkeypatch.setattr(q, "_CONFIRM_INTERVAL", 0)
    a = _Q([{"other", HEX}])
    assert a._confirm_or_poll(HEX, before={"other"}) == HEX


def test_a_slow_client_is_waited_out(monkeypatch):
    """It took longer than the old ~5s window; that is the reported bug."""
    import core.torrent_clients.qbittorrent as q
    monkeypatch.setattr(q, "_CONFIRM_INTERVAL", 0)
    a = _Q([{"other"}] * 12 + [{"other", HEX}])
    assert a._confirm_or_poll(HEX, before={"other"}) == HEX


def test_a_duplicate_add_resolves_instead_of_looking_like_a_failure(monkeypatch):
    """Re-adding a torrent the client already has produces NO new hash, so the
    diff found nothing and called it a rejection. The hash is still correct."""
    import core.torrent_clients.qbittorrent as q
    monkeypatch.setattr(q, "_CONFIRM_INTERVAL", 0)
    a = _Q([{HEX}])
    assert a._confirm_or_poll(HEX, before={HEX}) == HEX


def test_an_underivable_hash_still_falls_back_to_the_diff(monkeypatch):
    """An http .torrent URL handed straight to the client — the old path has to
    keep working."""
    import core.torrent_clients.qbittorrent as q
    monkeypatch.setattr(q, "_CONFIRM_INTERVAL", 0)
    monkeypatch.setattr(q, "_CONFIRM_ATTEMPTS", 1)
    a = _Q([{"other"}])
    # Assigned AFTER construction: __init__ binds the real method onto the
    # instance, so a subclass override would be overwritten.
    a._poll_for_new_hash = lambda before: "from-diff"
    assert a._confirm_or_poll(None, before={"other"}) == "from-diff"


def test_a_torrent_that_really_never_arrives_still_reports_failure(monkeypatch):
    """The check must not become unconditional success — a genuinely rejected
    add has to stay an error."""
    import core.torrent_clients.qbittorrent as q
    monkeypatch.setattr(q, "_CONFIRM_INTERVAL", 0)
    monkeypatch.setattr(q, "_CONFIRM_ATTEMPTS", 2)
    a = _Q([{"other"}])
    a._poll_for_new_hash = lambda before: None      # the diff finds nothing either
    assert a._confirm_or_poll(HEX, before={"other"}) is None


def test_the_confirm_window_is_longer_than_the_old_poll():
    import core.torrent_clients.qbittorrent as q
    assert q._CONFIRM_ATTEMPTS * q._CONFIRM_INTERVAL >= 10
