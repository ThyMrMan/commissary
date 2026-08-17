"""The Soulseek chat feature and the Support/donate button are gone.

Chat was ~3,500 lines across a blueprint, two core modules, a JS module, a CSS
block and its page markup, with four genuine couplings into shared code (the
socket router, the search-result "message this user" buttons, the page router,
and the video side's shared-page map). This pins the removal so none of it
creeps back in piecemeal, and — more importantly — pins the two distinctions
that made the removal delicate:

  * Telegram notifications use `chat_id`. That is a DIFFERENT feature and must
    survive. Most "chat" hits in this repo were Telegram's, or the word inside
    "Bachata".
  * chat rode the SAME slskd REST client as search and transfers. The chat
    methods went; the shared plumbing had to stay.
"""

from __future__ import annotations

import os
import re
import sqlite3

import pytest

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _read(rel):
    with open(os.path.join(_ROOT, *rel.split('/')), encoding='utf-8', errors='replace') as fh:
        return fh.read()


def _exists(rel):
    return os.path.exists(os.path.join(_ROOT, *rel.split('/')))


_INDEX = _read('webui/index.html')
_STYLE = _read('webui/static/style.css')


# ── the files are gone ──────────────────────────────────────────────────────

@pytest.mark.parametrize('rel', [
    'api/chat.py',
    'core/chat_autoprove.py',
    'core/chat_codec.py',
    'webui/static/chat.js',
])
def test_chat_module_is_deleted(rel):
    assert not _exists(rel)


# ── no dangling references ──────────────────────────────────────────────────

def test_no_chat_markup_remains():
    for marker in ('id="chat-page"', 'data-page="chat"',
                   'data-video-page="video-chat"', "filename='chat.js'"):
        assert marker not in _INDEX, marker


def test_no_chat_css_remains():
    assert 'chat' not in _STYLE.lower()


def test_stylesheet_is_still_structurally_intact():
    """The chat rules were interleaved with the artist-image-picker ones, and
    one multi-selector rule listed .support-button alongside live selectors —
    a blunt range delete would have taken working CSS with it."""
    stripped = re.sub(r'/\*.*?\*/', '', _STYLE, flags=re.S)
    assert stripped.count('{') == stripped.count('}')
    assert _STYLE.count('/*') == _STYLE.count('*/')
    assert '.art-picker-overlay' in _STYLE
    assert 'body.helper-mode-active .nav-button:hover' in _STYLE


def test_socket_router_has_no_chat_handlers():
    core = _read('webui/static/core.js')
    assert "chat:" not in core
    assert 'ChatPage' not in core


def test_search_results_no_longer_offer_a_dead_message_button():
    """The 'Message this user on Soulseek' buttons only ever worked through
    chat; leaving them would be dead clicks."""
    dl = _read('webui/static/downloads.js')
    assert 'chat-user-link' not in dl
    assert 'data-chat-msg-user' not in dl


def test_page_router_has_no_chat_route():
    init = _read('webui/static/init.js')
    assert "'chat'" not in init
    assert 'ChatPage' not in init


def test_video_side_no_longer_maps_a_chat_page():
    vs = _read('webui/static/video/video-side.js')
    assert 'video-chat' not in vs
    assert "'chat'" not in vs


def test_widget_registry_has_no_chat_entries():
    assert 'nav-chat' not in _read('webui/static/dashboard-widgets.js')


def test_server_has_no_chat_surface():
    ws = _read('web_server.py')
    for marker in ('_emit_chat_push_loop', '_chat_auto_prove_loop',
                   'api.chat', 'nav-chat', '_chat_push_state'):
        assert marker not in ws, marker


# ── the shared slskd client survived ────────────────────────────────────────

def test_soulseek_client_dropped_chat_but_kept_its_plumbing():
    """Chat rode the same base_url + X-API-Key REST client as search and
    transfers. Removing the chat methods must not have touched that."""
    from core.soulseek_client import SoulseekClient

    for gone in ('send_room_message', 'join_room', 'get_room_messages',
                 'browse_user_shares', 'get_conversations', 'send_private_message'):
        assert not hasattr(SoulseekClient, gone), gone

    for kept in ('_make_request', '_get_headers'):
        assert hasattr(SoulseekClient, kept), kept


# ── the archive table is dropped ────────────────────────────────────────────

def _table_names(db):
    conn = db._get_connection()
    try:
        return {r[0] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'")}
    finally:
        conn.close()


def test_fresh_database_has_no_chat_table(tmp_path):
    from database.music_database import MusicDatabase
    db = MusicDatabase(database_path=str(tmp_path / 'chatless.db'))
    assert 'chat_room_messages' not in _table_names(db)


def test_upgrade_sheds_an_existing_chat_table(tmp_path):
    """An install that already had the table must lose it, not carry dead rows
    forever — that is what the guarded DROP is for."""
    from database.music_database import MusicDatabase
    path = str(tmp_path / 'legacy.db')
    conn = sqlite3.connect(path)
    conn.execute("CREATE TABLE chat_room_messages (id INTEGER PRIMARY KEY, room TEXT)")
    conn.execute("INSERT INTO chat_room_messages (room) VALUES ('Commissary')")
    conn.commit()
    conn.close()

    db = MusicDatabase(database_path=path)
    assert 'chat_room_messages' not in _table_names(db)


def test_database_has_no_chat_crud():
    from database.music_database import MusicDatabase
    for gone in ('add_chat_messages', 'get_chat_messages', 'search_chat_messages'):
        assert not hasattr(MusicDatabase, gone), gone


# ── Support button + donate links ───────────────────────────────────────────

def test_support_button_and_modal_are_gone():
    for marker in ('support-button', 'support-section', 'support-modal',
                   'showSupportModal', 'ko-fi'):
        assert marker not in _INDEX, marker
    assert 'showSupportModal' not in _read('webui/static/settings.js')
    assert 'copyAddress' not in _read('webui/static/settings.js')


def test_no_donate_links_anywhere():
    for rel in ('README.md', 'Support/UNRAID.md', 'templates/commissary.xml'):
        text = _read(rel)
        assert 'ko-fi' not in text.lower(), rel
        assert 'DonateLink' not in text, rel


def test_docs_name_a_pullable_image():
    """An image name is not a donation link. This guard was written when a
    donate-link sweep threatened to strip the upstream Docker Hub name; the
    fork now publishes its own image to GHCR, so the name changed but the
    concern did not — the docs must still name something users can pull."""
    assert 'ghcr.io/thymrman/commissary' in _read('README.md')
    assert 'ghcr.io/thymrman/commissary' in _read('templates/commissary.xml')


# ── the two distinctions that caused the false positives ────────────────────

def test_telegram_chat_id_is_untouched():
    """Telegram's chat_id is a different feature that merely shares a word."""
    assert 'chat_id' in _read('core/video/notifications.py')
    assert 'chat_id' in _read('webui/static/stats-automations.js')
    assert 'chat_id' in _read('core/automation_engine.py')


def test_bachata_is_still_a_genre():
    assert 'achata' in _read('core/genre_filter.py')
    assert 'achata' in _read('core/personalized_playlists.py')
