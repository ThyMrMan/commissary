"""The "Prefer deluxe editions" setting saves, and the settings page carries it.

The save is driven through the real app: /api/settings persists only the sections
it knows, so a new key is proven by saving it, not by reading the source.
"""

from __future__ import annotations

import os
import tempfile
from pathlib import Path

import pytest

# Redirect the DB before importing web_server so it never touches a real library.
_TMP = tempfile.mkdtemp(prefix='soulsync-testdb-deluxe-')
os.environ['DATABASE_PATH'] = os.path.join(_TMP, 'prefer_deluxe.db')
os.environ['SOULSYNC_TEST_DB_READY'] = '1'

web_server = pytest.importorskip('web_server')

_ROOT = Path(__file__).resolve().parent.parent


def _source(*parts):
    """A webui source with line endings normalised (LF in git, CRLF on Windows)."""
    return _ROOT.joinpath(*parts).read_text(encoding="utf-8").replace("\r\n", "\n")


@pytest.fixture
def client():
    return web_server.app.test_client()


def test_the_setting_saves_through_the_settings_endpoint(client):
    from config.settings import config_manager
    try:
        response = client.post('/api/settings', json={'wishlist': {'prefer_deluxe_editions': True}})
        assert response.status_code == 200 and response.get_json()['success']
        assert config_manager.get('wishlist.prefer_deluxe_editions') is True

        client.post('/api/settings', json={'wishlist': {'prefer_deluxe_editions': False}})
        assert config_manager.get('wishlist.prefer_deluxe_editions') is False
    finally:
        config_manager.set('wishlist.prefer_deluxe_editions', False)


def test_the_settings_page_loads_and_saves_the_checkbox():
    html = _source("webui", "index.html")
    js = _source("webui", "static", "settings.js")

    assert 'id="prefer-deluxe-editions"' in html
    assert ("document.getElementById('prefer-deluxe-editions').checked = "
            "settings.wishlist?.prefer_deluxe_editions === true;") in js
    # Saved inside the same `wishlist` object as its neighbour, not a new section.
    assert ("allow_duplicate_tracks: document.getElementById('allow-duplicate-tracks').checked,\n"
            "            prefer_deluxe_editions: document.getElementById('prefer-deluxe-editions').checked,") in js
