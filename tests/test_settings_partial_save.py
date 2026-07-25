"""POST /api/settings is a PARTIAL, per-section, per-key merge.

The video side reuses the music settings page (video-side.js maps
video-settings -> settings, CSS filters it per side). The sections it
legitimately shows that are backed by the MUSIC config — Prowlarr, the
torrent/usenet clients, appearance, security, db workers — are marked
``data-shared`` and saved by ``saveSharedSettings()`` in settings.js, which
posts ONLY those sections.

That is only safe because this endpoint merges instead of replacing:
  * ``active_media_server`` is written only when that key is present, so a
    body without it can never repoint the music server from the video page.
  * a section absent from the body is untouched.
  * within a section, a key absent from the body is untouched.
Plus ConfigManager.set()'s guard: an empty/sentinel value for a sensitive
path is a no-op, so a partial save can't blank a secret it never showed.

These pin that contract. If it ever regresses to a whole-config replace, the
video side would silently wipe music-only config on every shared-field edit.

Heavy (imports web_server once), so isolated in its own module per the
convention in tests/test_credentials_endpoints.py.
"""

from __future__ import annotations

import os
import tempfile

import pytest

# Redirect the DB before importing web_server so it never touches a real library.
_TMP = tempfile.mkdtemp(prefix='soulsync-testdb-partialsave-')
os.environ['DATABASE_PATH'] = os.path.join(_TMP, 'partial_save.db')
os.environ['SOULSYNC_TEST_DB_READY'] = '1'

web_server = pytest.importorskip('web_server')

from config.settings import ConfigManager, config_manager  # noqa: E402

SENTINEL = ConfigManager.REDACTED_SENTINEL

# Exactly what settings.js's SHARED_SECTION_BUILDERS emits — the payload the
# video side posts. Kept literal here so a change to the builders that widens
# the video side's reach into the music config has to be made deliberately.
SHARED_SECTIONS = ('prowlarr', 'torrent_client', 'usenet_client',
                   'ui_appearance', 'security', 'database')


@pytest.fixture
def client():
    return web_server.app.test_client()


@pytest.fixture(autouse=True)
def _restore_config():
    """config_manager is a process-wide singleton shared with every other test
    in the run. These tests deliberately write real config (that IS the thing
    under test), so snapshot and restore it — otherwise e.g. leaving a
    torrent_client.url set makes tests/video/test_client_grab.py's
    "no client configured" case fail depending on collection order."""
    import copy
    before = copy.deepcopy(config_manager.config_data)
    yield
    config_manager.config_data = copy.deepcopy(before)
    config_manager._save_config()


def _shared_payload():
    return {
        'prowlarr': {'url': 'http://prowlarr:9696', 'api_key': 'pk', 'indexer_ids': '1,2'},
        'torrent_client': {'type': 'qbittorrent', 'url': 'http://qb:8080',
                           'username': 'u', 'password': 'p', 'category': 'soulsync',
                           'save_path': '', 'seed_ratio_goal': 0,
                           'seed_time_goal_hours': 0, 'seed_remove_data': True,
                           'seed_mode': 'soulsync'},
        'usenet_client': {'type': 'sabnzbd', 'url': '', 'api_key': '',
                          'username': '', 'password': '', 'category': 'soulsync'},
        'ui_appearance': {'accent_preset': '#1db954'},
        'security': {'require_pin_on_launch': False},
        'database': {'max_workers': 5},
    }


# ── the merge contract ───────────────────────────────────────────────────────

def test_absent_section_is_untouched(client):
    config_manager.set('spotify.client_id', 'MUSIC-ONLY-VALUE')
    r = client.post('/api/settings', json={'prowlarr': {'url': 'http://p:9696'}})
    assert r.status_code == 200 and r.get_json()['success']
    assert config_manager.get('spotify.client_id') == 'MUSIC-ONLY-VALUE', (
        "a partial save wiped a section it never sent — the video side would "
        "destroy music-only config on every shared-field edit")
    assert config_manager.get('prowlarr.url') == 'http://p:9696'


def test_absent_key_within_a_sent_section_is_untouched(client):
    config_manager.set('prowlarr.url', 'http://keep-me:9696')
    config_manager.set('prowlarr.indexer_ids', '7,8,9')
    client.post('/api/settings', json={'prowlarr': {'api_key': 'newkey'}})
    assert config_manager.get('prowlarr.api_key') == 'newkey'
    assert config_manager.get('prowlarr.url') == 'http://keep-me:9696'
    assert config_manager.get('prowlarr.indexer_ids') == '7,8,9'


def test_active_media_server_unchanged_when_key_absent(client):
    """The whole reason the video side can post to this endpoint at all."""
    config_manager.set_active_media_server('jellyfin')
    r = client.post('/api/settings', json=_shared_payload())
    assert r.status_code == 200 and r.get_json()['success']
    assert config_manager.get_active_media_server() == 'jellyfin', (
        "posting the shared sections repointed the music server — the video "
        "side must never be able to do that")


def test_full_shared_payload_leaves_every_other_section_intact(client):
    config_manager.set('spotify.client_id', 'SPOT')
    config_manager.set('soulseek.slskd_url', 'http://slskd:5030')
    config_manager.set('lastfm.api_key', 'LFM')
    config_manager.set('metadata_enhancement.enabled', True)

    r = client.post('/api/settings', json=_shared_payload())
    assert r.status_code == 200 and r.get_json()['success']

    assert config_manager.get('spotify.client_id') == 'SPOT'
    assert config_manager.get('soulseek.slskd_url') == 'http://slskd:5030'
    assert config_manager.get('lastfm.api_key') == 'LFM'
    assert config_manager.get('metadata_enhancement.enabled') is True
    # ...and the shared sections did land.
    assert config_manager.get('prowlarr.api_key') == 'pk'
    assert config_manager.get('torrent_client.url') == 'http://qb:8080'


def test_sentinel_in_partial_save_does_not_clobber_a_secret(client):
    """A masked field round-tripped by the video page must not wipe the real
    value (the #832/#992 guard, exercised through a partial body)."""
    config_manager.set('prowlarr.api_key', 'REAL-KEY')
    client.post('/api/settings', json={'prowlarr': {'api_key': SENTINEL}})
    assert config_manager.get('prowlarr.api_key') == 'REAL-KEY'
    client.post('/api/settings', json={'prowlarr': {'api_key': ''}})
    assert config_manager.get('prowlarr.api_key') == 'REAL-KEY'


def test_every_shared_section_is_in_the_endpoint_allowlist():
    """settings.js posts these section names; the endpoint only writes sections
    in its hard-coded allowlist. A name that isn't in it saves silently-nothing
    — exactly the class of bug this whole change exists to fix."""
    import inspect
    src = inspect.getsource(web_server)
    # The allowlist is one long literal list in the POST /api/settings handler.
    missing = [s for s in SHARED_SECTIONS if f"'{s}'" not in src]
    assert missing == [], f"shared sections absent from web_server: {missing}"


# ── source guards: the two JS call sites this contract depends on ────────────

def _read(rel):
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    with open(os.path.join(root, *rel.split('/')), encoding='utf-8') as fh:
        return fh.read()


def test_video_save_button_flushes_shared_settings():
    src = _read('webui/static/video/video-settings.js')
    assert 'saveSharedSettings' in src, (
        "the video Save-Settings intercept must flush the data-shared sections; "
        "without it every Prowlarr/torrent/usenet/appearance edit made on the "
        "video side is silently discarded")


def test_video_autosave_is_scoped_to_shared_and_never_calls_music_save():
    src = _read('webui/static/settings.js')
    start = src.index('function debouncedAutoSaveSettings')
    body = src[start:src.index('\nfunction ', start + 10)]
    # Just the video if-block: from the side check to the line that closes it.
    branch_start = body.index("=== 'video'")
    video_branch = body[branch_start:body.index('\n    }', branch_start)]
    assert "closest('[data-shared]')" in video_branch, (
        "video-side auto-save must fire only inside a [data-shared] section — "
        "the listener is bound to EVERY input on the page, so an unscoped "
        "branch would post the music config when a TMDB key is typed")
    assert 'saveSettings(true)' not in video_branch, (
        "the video branch must call saveSharedSettings, never music's full save")


def test_shared_sections_are_built_in_exactly_one_place():
    """SHARED_SECTION_BUILDERS is the single definition of the shared payload.
    saveSettings() must FOLD IT IN rather than re-declaring the same sections
    inline — that duplication is precisely how the music and video save paths
    would drift apart again."""
    src = _read('webui/static/settings.js')
    assert 'SHARED_SECTION_BUILDERS' in src and 'collectSharedSettings' in src

    start = src.index('async function saveSettings')
    body = src[start:src.index('\nasync function ', start + 10)]
    assert 'collectSharedSettings()' in body, (
        "saveSettings no longer folds in the shared builders — the music side "
        "would stop saving the shared sections entirely")
    redeclared = [s for s in SHARED_SECTIONS if f'{s}: {{' in body]
    assert redeclared == [], (
        f"saveSettings re-declares shared sections inline: {redeclared}")
