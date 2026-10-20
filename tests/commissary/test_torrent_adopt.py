"""Deluge / aria2 adopt a torrent they already hold (fork 2.0.10).

Both clients raise on a duplicate add, which the adapters used to turn into
None — read upstream as "the client didn't accept the release".
"""

from __future__ import annotations

import asyncio
import hashlib
import threading
from itertools import count
from unittest.mock import patch

from core.torrent_clients.aria2 import Aria2Adapter
from core.torrent_clients.deluge import DelugeAdapter

HASH = 'ab' * 20
MAGNET = f'magnet:?xt=urn:btih:{HASH.upper()}&dn=Album'
INFO = b'd4:name5:Album12:piece lengthi16384ee'
TORRENT = b'd8:announce3:url4:info' + INFO + b'e'
TORRENT_HASH = hashlib.sha1(INFO).hexdigest()


def _run(coro):
    return asyncio.run(coro)


# ── Deluge ──────────────────────────────────────────────────────────────────

def _deluge(held_hash):
    adapter = DelugeAdapter.__new__(DelugeAdapter)
    adapter._session = None
    adapter._session_lock = threading.Lock()
    adapter._id_counter = count(1)
    adapter._url = 'http://deluge:8112'
    adapter._password = 'pw'
    adapter._category = 'Music'
    adapter._save_path = ''
    calls = []

    def rpc(method, params):
        calls.append((method, params))
        if method.startswith('core.add_torrent'):
            return None                         # duplicate → RPC error → None
        if method == 'core.get_torrent_status':
            return {'hash': params[0]} if params[0] == held_hash else {}
        return True

    adapter._rpc_sync = rpc
    adapter._ensure_session_sync = lambda: object()
    return adapter, calls


def test_deluge_duplicate_magnet_adopts_and_labels_held_torrent():
    adapter, calls = _deluge(HASH)
    # category=None, as add_torrent_smart passes it: the configured label
    assert _run(adapter.add_torrent(MAGNET, category=None)) == HASH
    assert ('label.set_torrent', [HASH, 'Music']) in calls


def test_deluge_duplicate_torrent_file_adopts_held_torrent():
    adapter, _ = _deluge(TORRENT_HASH)
    assert _run(adapter.add_torrent_file(TORRENT)) == TORRENT_HASH


def test_deluge_real_refusal_stays_none():
    adapter, calls = _deluge('cd' * 20)         # holds something else
    assert _run(adapter.add_torrent(MAGNET)) is None
    assert not any(m == 'label.set_torrent' for m, _ in calls)


def test_deluge_http_url_cannot_be_hashed_so_stays_none():
    adapter, calls = _deluge(HASH)
    assert _run(adapter.add_torrent('https://idx/dl/1.torrent')) is None
    assert not any(m == 'core.get_torrent_status' for m, _ in calls)


# ── aria2 ───────────────────────────────────────────────────────────────────

def _aria2(rows_by_queue):
    with patch('core.torrent_clients.aria2.config_manager') as cm:
        cm.get.side_effect = lambda k, d=None: {'torrent_client.url': 'http://nas:6800'}.get(k, d)
        adapter = Aria2Adapter()

    def rpc(method, *params):
        if method in ('aria2.addUri', 'aria2.addTorrent'):
            return None                         # "Duplicate torrent" → None
        return rows_by_queue.get(method, [])

    adapter._rpc = rpc
    return adapter


def test_aria2_duplicate_magnet_maps_hash_back_to_gid_in_stopped_queue():
    adapter = _aria2({
        'aria2.tellActive': [{'gid': 'g1', 'infoHash': 'cd' * 20}],
        'aria2.tellStopped': [{'gid': 'g9', 'infoHash': HASH, 'status': 'complete'}],
    })
    assert _run(adapter.add_torrent(MAGNET)) == 'g9'


def test_aria2_duplicate_torrent_file_adopts_held_gid():
    adapter = _aria2({'aria2.tellWaiting': [{'gid': 'g4', 'infoHash': TORRENT_HASH}]})
    assert _run(adapter.add_torrent_file(TORRENT)) == 'g4'


def test_aria2_real_refusal_stays_none():
    adapter = _aria2({'aria2.tellActive': [{'gid': 'g1', 'infoHash': 'cd' * 20}]})
    assert _run(adapter.add_torrent(MAGNET)) is None
