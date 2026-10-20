"""The candidate picker names the tracker and links its details page (fork 1.9.8).

Upstream keeps ``indexer`` in a Prowlarr result's ``_source_metadata`` but
never sends it to the browser, and drops ``info_url``. The link is a
third-party string rendered as an href, and indexer download links carry API
keys that must never reach the browser (P0-03).
"""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest

from core.commissary.tracker_link import candidate_indexer_fields, safe_info_url
from core.download_plugins.torrent import TorrentDownloadPlugin
from core.download_plugins.usenet import UsenetDownloadPlugin
from core.prowlarr_client import ProwlarrSearchResult

DETAILS = 'https://tracker.example/torrents.php?id=4242'
DOWNLOAD = 'http://prowlarr:9696/3/download?apikey=SECRETKEY&link=abc'


def _result(**overrides) -> ProwlarrSearchResult:
    base = dict(
        guid='guid-1', title='Danny Brown - Atrocity Exhibition [FLAC]', indexer_id=3,
        indexer_name='RED', protocol='torrent', download_url=DOWNLOAD, magnet_uri=None,
        info_url=DETAILS, size=500_000_000, seeders=12, leechers=3, grabs=100,
        publish_date='2026-01-01', categories=[3040], raw={},
    )
    base.update(overrides)
    return ProwlarrSearchResult(**base)


# ── the URL check ───────────────────────────────────────────────────────────

@pytest.mark.parametrize('url', [DETAILS, 'http://nzbgeek.info/geekseek.php?guid=abc123'])
def test_a_details_page_is_kept(url):
    assert safe_info_url(url) == url


@pytest.mark.parametrize('url', [
    'javascript:alert(1)',
    'data:text/html,<script>alert(1)</script>',
    'JaVaScRiPt:alert(1)',
    '//tracker.example/x',
    'ftp://tracker.example/x',
    'https://user:pw@tracker.example/details/1',
    'https://tracker.example/details/1?apikey=SECRET',
    'https://tracker.example/details/1?passkey=SECRET',
    'https://tracker.example/torrents.php?action=download&id=1&torrent_pass=SECRET',
    'https://tracker.example/download/1.torrent',
    '',
    None,
])
def test_unsafe_or_secret_bearing_urls_are_dropped(url):
    assert safe_info_url(url) is None


def test_a_url_identical_to_the_download_link_is_dropped():
    same = 'https://tracker.example/t/1'
    assert safe_info_url(same, download_url=same) is None
    assert safe_info_url(same, magnet_uri=same) is None


# ── the plugins carry it ────────────────────────────────────────────────────

@pytest.mark.parametrize('plugin,protocol', [
    (TorrentDownloadPlugin, 'torrent'), (UsenetDownloadPlugin, 'usenet'),
])
def test_plugins_carry_the_checked_details_page(plugin, protocol):
    tracks, _ = plugin()._project_results([_result(protocol=protocol)])
    meta = tracks[0]._source_metadata
    assert meta['indexer'] == 'RED'
    assert meta['info_url'] == DETAILS


@pytest.mark.parametrize('plugin,protocol', [
    (TorrentDownloadPlugin, 'torrent'), (UsenetDownloadPlugin, 'usenet'),
])
def test_plugins_never_carry_a_download_link_as_the_details_page(plugin, protocol):
    """Some indexers hand back the download link as infoUrl; it carries the key."""
    tracks, _ = plugin()._project_results([_result(protocol=protocol, info_url=DOWNLOAD)])
    assert tracks[0]._source_metadata['info_url'] is None


# ── the serializer sends it to the picker ───────────────────────────────────

def test_candidate_fields_are_empty_for_a_source_without_metadata():
    assert candidate_indexer_fields(SimpleNamespace(username='peer')) == {}


def test_candidate_fields_recheck_the_link():
    c = SimpleNamespace(_source_metadata={'indexer': 'RED', 'info_url': 'javascript:alert(1)'})
    assert candidate_indexer_fields(c) == {'indexer': 'RED'}


def test_serialized_candidate_names_the_tracker_and_never_leaks_the_download_url():
    import web_server
    tracks, _ = TorrentDownloadPlugin()._project_results([_result()])
    out = web_server._serialize_candidate(tracks[0])
    assert out['indexer'] == 'RED'
    assert out['info_url'] == DETAILS
    assert 'SECRETKEY' not in json.dumps(out)


def test_serialized_soulseek_candidate_has_no_tracker_keys():
    import web_server
    from core.download_plugins.types import TrackResult
    peer = TrackResult(username='peer', filename='a/b.flac', size=1, bitrate=None,
                       duration=None, quality='flac', free_upload_slots=1,
                       upload_speed=0, queue_length=0)
    out = web_server._serialize_candidate(peer)
    assert 'indexer' not in out and 'info_url' not in out


# ── the picker renders it (node) ────────────────────────────────────────────

_JS = Path(__file__).with_name('tracker_link.mjs')


def test_picker_renders_tracker_link_js():
    node = shutil.which('node') or shutil.which('node.exe')
    if not node:
        pytest.skip('node not installed')
    r = subprocess.run([node, '--test', str(_JS)], capture_output=True, text=True, timeout=60,
                       cwd=str(Path(__file__).resolve().parents[2]))
    assert r.returncode == 0, r.stdout + r.stderr
