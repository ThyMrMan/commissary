"""Importing from a folder other than the one configured Import folder.

``get_staging_path()`` pinned every import to ``import.staging_path``, so a
release sitting where the download client left it had to be moved by hand
before it could be imported at all. The Import page now browses the server's
configured roots and can point the scan at any folder inside them.

The security shape is the point, and it is deliberately asymmetric:

* **browse** lists names and sizes. Admin-only, and bounded to the configured
  roots so it can't be used to enumerate the host.
* **the scan endpoints** open every file they find and read its tags. Letting
  those walk an arbitrary path would turn a directory listing into a content
  read of anywhere on disk — so the same root allowlist gates them, checked
  through ``realpath`` on both sides so a symlink can't step outside.

A rejected path always degrades to "the configured Import folder", which is
exactly the behaviour before any of this existed.
"""

from __future__ import annotations

import os
import tempfile

import pytest

_TMP = tempfile.mkdtemp(prefix='soulsync-testdb-browse-')
os.environ.setdefault('DATABASE_PATH', os.path.join(_TMP, 'browse.db'))
os.environ.setdefault('SOULSYNC_TEST_DB_READY', '1')

web_server = pytest.importorskip('web_server')


@pytest.fixture
def roots(tmp_path, monkeypatch):
    """A download root with a nested release, plus an unrelated folder that is
    NOT under any configured root."""
    downloads = tmp_path / 'downloads'
    release = downloads / 'complete' / 'Artist - Album (2020) [FLAC]'
    release.mkdir(parents=True)
    (release / '01 - One.flac').write_bytes(b'x' * 10)
    (release / '02 - Two.mp3').write_bytes(b'y' * 20)
    (release / 'cover.jpg').write_bytes(b'z')          # not audio
    (release / '.hidden.flac').write_bytes(b'h')       # dotfile
    (downloads / 'empty').mkdir()

    outside = tmp_path / 'outside'
    outside.mkdir()
    (outside / 'secret.flac').write_bytes(b's')

    staging = tmp_path / 'staging'
    staging.mkdir()

    from config.settings import config_manager
    original = config_manager.get

    def _fake_get(key, default=None):
        if key == 'soulseek.download_path':
            return str(downloads)
        if key == 'import.staging_path':
            return str(staging)
        if key in ('soulseek.torrent_download_path', 'soulseek.usenet_download_path',
                   'library.music_paths'):
            return []
        if key == 'soulseek.transfer_path':
            return ''
        return original(key, default)

    monkeypatch.setattr(config_manager, 'get', _fake_get)
    return {'downloads': downloads, 'release': release,
            'outside': outside, 'staging': staging}


@pytest.fixture
def client():
    return web_server.app.test_client()


@pytest.fixture
def nonadmin(client):
    pid = web_server.get_database().create_profile(name=f'u_{os.urandom(3).hex()}')
    with client.session_transaction() as sess:
        sess['profile_id'] = pid
    return pid


# ── the gate ─────────────────────────────────────────────────────────────────
@pytest.mark.parametrize('path', [
    '/api/import/browse',
    '/api/import/staging/files',
    '/api/import/staging/groups',
])
def test_a_non_admin_cannot_enumerate_the_host(client, nonadmin, path):
    """Browse lists directories and the scan endpoints read file tags. Neither
    is something a standard user on a shared install should reach."""
    assert client.get(path).status_code == 403


# ── the allowlist ────────────────────────────────────────────────────────────
def test_a_folder_inside_a_configured_root_is_allowed(roots, client):
    path, err = web_server._resolve_import_scan_path(str(roots['release']))
    assert err is None
    assert path == os.path.abspath(str(roots['release']))


def test_a_folder_outside_every_root_is_refused(roots, client):
    path, err = web_server._resolve_import_scan_path(str(roots['outside']))
    assert path is None
    assert 'outside' in err


def test_a_sibling_with_a_shared_prefix_is_not_inside(tmp_path):
    """``startswith`` would accept /downloads-elsewhere as being inside
    /downloads. It isn't."""
    root = tmp_path / 'downloads'
    sibling = tmp_path / 'downloads-elsewhere'
    root.mkdir()
    sibling.mkdir()
    assert web_server._is_within(str(root), str(sibling)) is False
    assert web_server._is_within(str(root), str(root)) is True
    assert web_server._is_within(str(root), str(root / 'sub')) is True


def test_traversal_out_of_a_root_is_refused(roots):
    escape = os.path.join(str(roots['downloads']), '..', 'outside')
    path, err = web_server._resolve_import_scan_path(escape)
    assert path is None
    assert err


def test_a_missing_folder_is_reported_as_missing_not_as_forbidden(roots):
    """Different problems deserve different answers — 'you may not' when the
    truth is 'it isn't there' sends the user looking in the wrong place."""
    path, err = web_server._resolve_import_scan_path(
        str(roots['downloads'] / 'no-such-folder'))
    assert path is None
    assert 'exist' in err


def test_no_path_means_the_configured_import_folder(roots):
    """Absence is not an error — it's today's behaviour."""
    assert web_server._resolve_import_scan_path('') == (None, None)
    assert web_server._resolve_import_scan_path(None) == (None, None)
    assert web_server._resolve_import_scan_path('   ') == (None, None)


# ── browse output ────────────────────────────────────────────────────────────
def test_browse_lists_folders_and_audio_only(roots, client):
    r = client.get('/api/import/browse', query_string={'path': str(roots['release'])})
    assert r.status_code == 200
    body = r.get_json()
    names = {f['name'] for f in body['files']}
    # Only audio, and only what the staging scan itself would accept — so
    # nothing offered here can be rejected at the next step.
    assert names == {'01 - One.flac', '02 - Two.mp3'}
    assert 'cover.jpg' not in names
    assert '.hidden.flac' not in names, "dotfiles are noise, not content"
    assert body['audio_count'] == 2
    assert all('size' in f for f in body['files'])


def test_browse_opens_on_a_configured_root_with_no_path(roots, client):
    r = client.get('/api/import/browse')
    assert r.status_code == 200
    body = r.get_json()
    assert body['path'] == os.path.abspath(str(roots['downloads']))
    assert any(s['label'] == 'Downloads' for s in body['shortcuts'])


def test_browse_refuses_a_path_outside_the_roots(roots, client):
    r = client.get('/api/import/browse', query_string={'path': str(roots['outside'])})
    assert r.status_code == 403
    assert r.get_json()['success'] is False


def test_up_stops_at_the_root_rather_than_leading_somewhere_unusable(roots, client):
    """Offering a parent the scan endpoints will refuse is a dead end that
    looks like a bug."""
    r = client.get('/api/import/browse', query_string={'path': str(roots['downloads'])})
    assert r.get_json()['parent'] is None

    deeper = client.get('/api/import/browse',
                        query_string={'path': str(roots['release'])})
    assert deeper.get_json()['parent'] is not None


def test_shortcuts_only_offer_folders_that_exist(roots, client, monkeypatch):
    """A shortcut to an unmounted path reads as a SoulSync bug rather than a
    missing mount."""
    from config.settings import config_manager
    inner = config_manager.get

    def _with_ghost(key, default=None):
        if key == 'library.music_paths':
            return ['/definitely/not/mounted/anywhere']
        return inner(key, default)

    monkeypatch.setattr(config_manager, 'get', _with_ghost)
    shortcuts = web_server._import_browse_shortcuts()
    assert all(os.path.isdir(s['path']) for s in shortcuts)
    assert not any('not/mounted' in s['path'].replace('\\', '/') for s in shortcuts)


# ── the scan follows the chosen folder ───────────────────────────────────────
def test_the_scan_reads_the_folder_the_request_names(roots, client):
    r = client.get('/api/import/staging/files',
                   query_string={'path': str(roots['release'])})
    assert r.status_code == 200
    body = r.get_json()
    if body.get('scanning'):
        pytest.skip('background scan still running; path echo asserted below')
    assert body['staging_path'] == os.path.abspath(str(roots['release']))


def test_the_scan_refuses_a_folder_outside_the_roots(roots, client):
    r = client.get('/api/import/staging/files',
                   query_string={'path': str(roots['outside'])})
    assert r.status_code == 403
    assert r.get_json()['success'] is False


def test_the_runtime_falls_back_to_the_configured_folder(roots):
    """The whole feature is opt-in: build the runtime with no override and it
    resolves the configured Import folder exactly as before."""
    from core.imports.staging import get_staging_path

    default_runtime = web_server._build_import_route_runtime()
    assert default_runtime.get_staging_path() == get_staging_path()

    scoped = web_server._build_import_route_runtime(str(roots['release']))
    assert scoped.get_staging_path() == str(roots['release'])
