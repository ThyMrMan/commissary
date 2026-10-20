"""Music NZBs carry the configured usenet category (fork 1.9.8).

The music plugin calls ``adapter.add_nzb(url)`` with no category. The
adapters resolve ``category or self._category``, but the default used to be
the truthy literal ``"soulsync"``, so the configured category was never used.
"""

from __future__ import annotations

import asyncio
import inspect
from itertools import count
from unittest.mock import MagicMock, patch

from core.usenet_clients.base import UsenetClientAdapter
from core.usenet_clients.nzbget import NZBGetAdapter
from core.usenet_clients.sabnzbd import SABnzbdAdapter


def _run(coro):
    return asyncio.run(coro)


def _ok(body):
    resp = MagicMock()
    resp.ok = True
    resp.status_code = 200
    resp.json.return_value = body
    return resp


def test_sab_add_nzb_without_category_uses_configured_category():
    adapter = SABnzbdAdapter.__new__(SABnzbdAdapter)
    adapter._url = 'http://sab:8080'
    adapter._api_key = 'k'
    adapter._category = 'Music'
    with patch('core.usenet_clients.sabnzbd.http_requests.get',
               return_value=_ok({'status': True, 'nzo_ids': ['SAB_1']})) as get:
        assert _run(adapter.add_nzb('https://idx/x.nzb')) == 'SAB_1'
    assert get.call_args.kwargs['params']['cat'] == 'Music'


def test_nzbget_add_nzb_without_category_uses_configured_category():
    adapter = NZBGetAdapter.__new__(NZBGetAdapter)
    adapter._id_counter = count(1)
    adapter._url = 'http://nzbget:6789'
    adapter._username = 'u'
    adapter._password = 'p'
    adapter._category = 'Music'
    captured = {}

    def fake_post(url, json=None, auth=None, headers=None, timeout=None):
        captured['params'] = json['params']
        return _ok({'result': 5})

    with patch('core.usenet_clients.nzbget.http_requests.post', side_effect=fake_post):
        assert _run(adapter.add_nzb('https://idx/x.nzb')) == '5'
    assert captured['params'][2] == 'Music'


def test_explicit_category_still_wins():
    adapter = SABnzbdAdapter.__new__(SABnzbdAdapter)
    adapter._url = 'http://sab:8080'
    adapter._api_key = 'k'
    adapter._category = 'Music'
    with patch('core.usenet_clients.sabnzbd.http_requests.get',
               return_value=_ok({'status': True, 'nzo_ids': ['SAB_2']})) as get:
        _run(adapter.add_nzb('https://idx/x.nzb', category='Movies'))
    assert get.call_args.kwargs['params']['cat'] == 'Movies'


def test_no_add_nzb_signature_defaults_to_a_literal_category():
    for fn in (UsenetClientAdapter.add_nzb, SABnzbdAdapter.add_nzb, NZBGetAdapter.add_nzb):
        assert inspect.signature(fn).parameters['category'].default is None, fn
