"""A torrent the client already has is not a refusal — and a failure says why.

Reported twice, three weeks apart, as the same symptom: a title that will not
import, sits on the wishlist forever, and re-grabs every hour. Both times the
torrent was in the download client the whole while, frequently already finished.

    torrent grab refused for Shang-Chi and the Legend of the Ten Rings:
    The torrent client didn't accept the release.

...logged hourly, 01:23 through 07:26, matching search_attempts=8 on the row.

The chain is short and entirely mechanical. ``add_torrent`` returns None when
the client refuses. Deluge RAISES on a duplicate and ``_rpc_sync`` maps every
raise to None; aria2 errors and ``_rpc`` does the same. So "already have it" and
"could not add it" become the same answer. No download row is created, nothing
polls the torrent sitting right there, ``has_file`` stays 0, the wishlist row
stays wanted, and the drain re-grabs it next hour. Forever.

The other two adapters never had this. qBittorrent derives the info-hash up
front and CONFIRMS it present (``_confirm_or_poll``); Transmission reads
``torrent-duplicate`` straight off the RPC reply. Both already do exactly what
this adds — this brings the remaining two up to the same contract, now stated
in ``base.py`` rather than left as an accident of implementation.

The second half is the diagnosis that was not possible. When the monitor gave
up on a download it logged NOTHING: the stall verdict, the client error and the
vanished transfer all just stopped, and the requery worker then overwrote
``error`` with its own summary — so the original reason reached neither the row
nor the log. Two incidents had to be reconstructed from database timestamps.
"""

from __future__ import annotations

import asyncio
from unittest.mock import MagicMock

import pytest

from core.torrent_clients import infohash
from core.torrent_clients.aria2 import Aria2Adapter
from core.torrent_clients.deluge import DelugeAdapter

# A real magnet and the hash it carries — the point of the whole exercise is
# that this is knowable WITHOUT asking the client anything.
HASH = "c7e76a7a4cf1351f12ba5ec176d924c1dbc610f4"
MAGNET = "magnet:?xt=urn:btih:%s&dn=Shang-Chi.2021.2160p.REMUX" % HASH
TORRENT_BYTES = b"d4:infod6:lengthi1e4:name4:filmee"


def _run(coro):
    return asyncio.new_event_loop().run_until_complete(coro)


class TestDeluge:
    def _adapter(self, *, add_result, holds=(), calls=None):
        a = DelugeAdapter()
        a._ensure_session_sync = lambda: object()
        a._apply_label = lambda h, label: (calls or []).append(("label", h, label))

        def _rpc(method, params):
            (calls if calls is not None else []).append((method, params))
            if method.startswith("core.add_torrent"):
                return add_result
            if method == "core.get_torrent_status":
                return {"hash": params[0]} if params[0] in holds else {}
            return None
        a._rpc_sync = _rpc
        return a

    def test_a_duplicate_magnet_resolves_to_the_hash_it_already_has(self):
        """THE bug. Deluge refuses, but it refused because it was already done."""
        a = self._adapter(add_result=None, holds={HASH})
        assert a._add_torrent_sync(MAGNET, "movies", None) == HASH

    def test_a_genuine_refusal_is_still_a_refusal(self):
        """The client does not have it and would not take it. Nothing to adopt —
        inventing a ref here would create a row that polls a torrent that isn't
        there, which is strictly worse than the failure."""
        a = self._adapter(add_result=None, holds=())
        assert a._add_torrent_sync(MAGNET, "movies", None) is None

    def test_a_normal_add_is_untouched(self):
        a = self._adapter(add_result=HASH, holds=())
        assert a._add_torrent_sync(MAGNET, "movies", None) == HASH

    def test_an_http_torrent_url_cannot_be_resolved_and_says_so_by_returning_none(self):
        """Its hash isn't derivable without fetching the file. Same limit
        qBittorrent's _confirm_or_poll has; it falls back rather than guessing."""
        a = self._adapter(add_result=None, holds={HASH})
        assert a._add_torrent_sync("https://indexer/x.torrent", "movies", None) is None

    def test_a_duplicate_torrent_FILE_resolves_too(self):
        """The magnet carrier (2.0.7) means .torrent bytes get pushed as often as
        magnets do, so the file path needs the same treatment."""
        expected = infohash.expected_hash(TORRENT_BYTES)
        assert expected, "the fixture must be valid bencode"
        a = self._adapter(add_result=None, holds={expected})
        assert a._add_torrent_file_sync(TORRENT_BYTES, "movies", None) == expected

    def test_the_adopted_torrent_gets_the_library_category(self):
        """It may have been added by hand, or by a build that lost track of it.
        Labelling it now is what puts it in the right Library."""
        calls = []
        a = self._adapter(add_result=None, holds={HASH}, calls=calls)
        a._add_torrent_sync(MAGNET, "anime", None)
        assert ("label", HASH, "anime") in calls

    def test_it_asks_only_for_the_hash(self):
        """A status call for one field, not the whole payload — this runs on a
        refusal, and only needs to know whether the torrent exists."""
        calls = []
        a = self._adapter(add_result=None, holds={HASH}, calls=calls)
        a._add_torrent_sync(MAGNET, "movies", None)
        status = [c for c in calls if c[0] == "core.get_torrent_status"]
        assert status and status[0][1] == [HASH, ["hash"]]


class TestAria2:
    def _adapter(self, *, add_result, queues=None):
        a = Aria2Adapter()
        queues = queues or {}

        def _rpc(method, *params):
            if method in ("aria2.addUri", "aria2.addTorrent"):
                return add_result
            for name in ("aria2.tellActive", "aria2.tellWaiting", "aria2.tellStopped"):
                if method == name:
                    return queues.get(name, [])
            return None
        a._rpc = _rpc
        return a

    def test_a_duplicate_resolves_to_the_existing_GID(self):
        """aria2 is the one adapter whose id is NOT the info-hash, so knowing the
        hash is not the answer by itself — it has to be mapped back to the GID
        that get_status will actually answer to."""
        a = self._adapter(add_result=None, queues={
            "aria2.tellActive": [{"gid": "2089b05ecca3d829", "infoHash": HASH}]})
        assert a._add_uri_sync(MAGNET, None) == "2089b05ecca3d829"

    def test_a_finished_torrent_in_the_stopped_queue_is_found(self):
        """The case that matters most: it already downloaded. If 'stopped' were
        skipped, the exact torrent worth adopting would be the one missed."""
        a = self._adapter(add_result=None, queues={
            "aria2.tellStopped": [{"gid": "ff00", "infoHash": HASH}]})
        assert a._add_uri_sync(MAGNET, None) == "ff00"

    def test_a_different_torrent_is_not_adopted(self):
        a = self._adapter(add_result=None, queues={
            "aria2.tellActive": [{"gid": "aaa", "infoHash": "b" * 40}]})
        assert a._add_uri_sync(MAGNET, None) is None

    def test_hash_case_does_not_matter(self):
        """aria2 reports upper-case hashes in some builds."""
        a = self._adapter(add_result=None, queues={
            "aria2.tellActive": [{"gid": "aaa", "infoHash": HASH.upper()}]})
        assert a._add_uri_sync(MAGNET, None) == "aaa"

    def test_a_normal_add_is_untouched(self):
        a = self._adapter(add_result="newgid")
        assert a._add_uri_sync(MAGNET, None) == "newgid"

    def test_a_genuine_refusal_is_still_a_refusal(self):
        assert self._adapter(add_result=None)._add_uri_sync(MAGNET, None) is None

    def test_a_duplicate_torrent_FILE_resolves_too(self):
        expected = infohash.expected_hash(TORRENT_BYTES)
        a = self._adapter(add_result=None, queues={
            "aria2.tellActive": [{"gid": "gid1", "infoHash": expected}]})
        assert a._add_file_sync(TORRENT_BYTES, None) == "gid1"

    def test_an_undecodable_payload_is_not_looked_up(self):
        a = self._adapter(add_result=None, queues={
            "aria2.tellActive": [{"gid": "aaa", "infoHash": HASH}]})
        assert a._add_uri_sync("https://indexer/x.torrent", None) is None


class TestTheContractIsWrittenDown:
    def test_base_says_a_duplicate_is_not_a_failure(self):
        """It was true of two adapters by accident and false of two others for
        the same reason: nothing said it anywhere."""
        from pathlib import Path
        src = (Path(__file__).resolve().parents[2] / "core" / "torrent_clients"
               / "base.py").read_text(encoding="utf-8")
        assert "A DUPLICATE IS NOT A FAILURE" in src

    def test_every_adapter_keeps_the_protocol(self):
        from core.torrent_clients.base import TorrentClientAdapter
        from core.torrent_clients.qbittorrent import QBittorrentAdapter
        from core.torrent_clients.transmission import TransmissionAdapter
        for cls in (QBittorrentAdapter, TransmissionAdapter, DelugeAdapter, Aria2Adapter):
            assert isinstance(cls(), TorrentClientAdapter)


# ── the monitor stops failing downloads in silence ──────────────────────────

class TestTheFailureSaysWhy:
    def _monitor(self, monkeypatch, plan):
        from core.video import download_monitor as dm
        monkeypatch.setattr(dm, "_blocked_pairs", lambda db: frozenset())
        monkeypatch.setattr(dm, "_blocked_users", lambda db: frozenset())
        monkeypatch.setattr("core.video.retry.plan_retry",
                            lambda row, **kw: plan)
        monkeypatch.setattr(dm, "_apply_candidate", lambda *a, **k: True)
        monkeypatch.setattr(dm, "_spawn_requery", lambda dl_id: None)
        monkeypatch.setattr(dm, "_wishlist_failed", lambda db, dl: None)
        monkeypatch.setattr(dm, "_archive_history", lambda db, dl, upd: None)
        return dm

    def _db(self):
        """Enough DB for the failure path to run; the assertions are on the log."""
        return MagicMock()

    def _row(self, **kw):
        base = {"id": 153, "title": "Shang-Chi and the Legend of the Ten Rings",
                "release_title": "Shang-Chi 2021 UHD BluRay 2160p REMUX-FraMeSToR",
                "source": "torrent", "username": "TorrentLeech"}
        base.update(kw)
        return base

    def test_it_names_the_title_the_reason_and_the_release(self, monkeypatch, caplog):
        """The line that would have answered two incidents in seconds instead of
        a database post-mortem."""
        import logging
        dm = self._monitor(monkeypatch, {"action": "requery", "query": "Shang-Chi 2021"})
        caplog.set_level(logging.WARNING, logger="soulsync.video.download_monitor")
        dm._fail_or_retry(self._db(), self._row(), "Stalled — no progress for 240 min")
        msgs = [r.getMessage() for r in caplog.records
                if r.name == "soulsync.video.download_monitor"]
        assert msgs, "the failure was silent"
        assert "Shang-Chi" in msgs[0]
        assert "no progress for 240 min" in msgs[0]
        assert "FraMeSToR" in msgs[0]

    @pytest.mark.parametrize("plan,expected", [
        ({"action": "candidate", "candidate": {"release_title": "Another.Release"}, "rest": []},
         "Another.Release"),
        ({"action": "requery", "query": "Shang-Chi 2021"}, "Shang-Chi 2021"),
        ({"action": "fail", "reason": "retry budget reached"}, "retry budget reached"),
    ])
    def test_it_says_what_happens_next(self, monkeypatch, caplog, plan, expected):
        """'Failed' alone doesn't tell you whether to expect another attempt."""
        import logging
        dm = self._monitor(monkeypatch, plan)
        caplog.set_level(logging.WARNING, logger="soulsync.video.download_monitor")
        dm._fail_or_retry(self._db(), self._row(), "boom")
        assert expected in [r.getMessage() for r in caplog.records
                            if r.name == "soulsync.video.download_monitor"][0]

    def test_a_diagnostic_never_breaks_the_retry(self, monkeypatch):
        dm = self._monitor(monkeypatch, {"action": "fail", "reason": "x"})
        monkeypatch.setattr(dm.logger, "warning",
                            lambda *a, **k: (_ for _ in ()).throw(RuntimeError()))
        dm._fail_or_retry(self._db(), self._row(), "boom")   # must not raise


class TestAVanishedTransferNamesTheRightClient:
    @pytest.mark.parametrize("source,expected", [
        ("torrent", "torrent client"),
        ("usenet", "usenet client"),
        ("slskd", "Soulseek"),
        (None, "Soulseek"),
    ])
    def test_it(self, source, expected):
        """It said "Soulseek transfer disappeared" for every row, including
        torrent ones the Soulseek path never touches — sending you to look at
        slskd for a torrent problem."""
        from core.video.download_monitor import _vanished_reason
        assert expected in _vanished_reason({"source": source})

    def test_the_monitor_actually_calls_it(self):
        """A perfect helper nobody calls is still the old bug. Pinned against the
        source rather than behaviourally, because reaching that branch means
        driving the whole tick loop; the trade is stated so nobody mistakes this
        for an end-to-end check."""
        from pathlib import Path
        src = (Path(__file__).resolve().parents[2] / "core" / "video"
               / "download_monitor.py").read_text(encoding="utf-8")
        assert "_fail_or_retry(db, dl, _vanished_reason(dl))" in src
        # and the hardcoded string survives ONLY as the slskd fallback inside it
        assert src.count("Soulseek transfer disappeared") == 1
