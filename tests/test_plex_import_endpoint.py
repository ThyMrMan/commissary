"""Bulk import from Plex: GET /api/profiles/plex/candidates + POST
/api/profiles/plex/import. Admin-only, dedups against already-linked
accounts, retries a name collision with a suffix, applies the shared
PLEX_PROFILE_DEFAULTS. No real network calls — get_server_authorized_plex_ids
is monkeypatched directly (its own behavior is covered by
tests/test_plex_user_auth.py)."""

from __future__ import annotations

import os
import tempfile

import pytest

_TMP = tempfile.mkdtemp(prefix='soulsync-testdb-pleximport-')
os.environ['DATABASE_PATH'] = os.path.join(_TMP, 'i.db')
os.environ['SOULSYNC_TEST_DB_READY'] = '1'

web_server = pytest.importorskip('web_server')


@pytest.fixture
def client():
    return web_server.app.test_client()


def _as_non_admin(client, db):
    pid = db.create_profile(name='NotAdmin_' + os.urandom(4).hex())
    with client.session_transaction() as sess:
        sess['profile_id'] = pid
    return pid


def _users(*entries):
    """entries: (id, title, username, thumb, home) tuples -> the dict shape
    get_server_authorized_plex_ids() returns."""
    return {e[0]: {'id': e[0], 'title': e[1], 'username': e[2], 'email': None,
                   'thumb': e[3] if len(e) > 3 else None, 'home': e[4] if len(e) > 4 else False}
           for e in entries}


def test_candidates_requires_admin(client, monkeypatch):
    db = web_server.get_database()
    _as_non_admin(client, db)
    r = client.get('/api/profiles/plex/candidates')
    assert r.status_code == 403


def test_import_requires_admin(client, monkeypatch):
    db = web_server.get_database()
    _as_non_admin(client, db)
    r = client.post('/api/profiles/plex/import', json={'plex_account_ids': [1]})
    assert r.status_code == 403


def test_candidates_error_when_plex_not_configured(client, monkeypatch):
    monkeypatch.setattr('core.plex_user_auth.get_server_authorized_plex_ids', lambda: None)
    r = client.get('/api/profiles/plex/candidates')
    assert r.status_code == 400 and r.get_json()['success'] is False


def test_candidates_flags_already_imported(client, monkeypatch):
    db = web_server.get_database()
    pid = db.create_profile(name='AlreadyIn', plex_account_id=11)
    monkeypatch.setattr('core.plex_user_auth.get_server_authorized_plex_ids',
                        lambda: _users((11, 'AlreadyIn', 'a11'), (22, 'NewOne', 'a22')))
    r = client.get('/api/profiles/plex/candidates')
    body = r.get_json()
    assert body['success'] is True
    by_id = {c['plex_account_id']: c for c in body['candidates']}
    assert by_id[11]['already_imported'] is True and by_id[11]['existing_profile_id'] == pid
    assert by_id[22]['already_imported'] is False


def test_import_creates_profiles_with_shared_defaults(client, monkeypatch):
    db = web_server.get_database()
    monkeypatch.setattr('core.plex_user_auth.get_server_authorized_plex_ids',
                        lambda: _users((31, 'Bob', 'bob', 'http://thumb', False)))
    r = client.post('/api/profiles/plex/import', json={'plex_account_ids': [31]})
    body = r.get_json()
    assert body['success'] is True and len(body['imported']) == 1
    new_id = body['imported'][0]['profile_id']
    created = db.get_profile(new_id)
    assert created['plex_account_id'] == 31
    assert created['allowed_sides'] == 'music' and created['can_download'] is False and created['is_admin'] is False
    assert created['avatar_url'] == 'http://thumb'


def test_import_honors_custom_defaults(client, monkeypatch):
    db = web_server.get_database()
    monkeypatch.setattr('core.plex_user_auth.get_server_authorized_plex_ids',
                        lambda: _users((32, 'Carol', 'carol')))
    r = client.post('/api/profiles/plex/import', json={
        'plex_account_ids': [32], 'defaults': {'allowed_sides': 'both', 'can_download': True}})
    body = r.get_json()
    created = db.get_profile(body['imported'][0]['profile_id'])
    assert created['allowed_sides'] == 'both' and created['can_download'] is True


def test_import_dedups_already_linked(client, monkeypatch):
    db = web_server.get_database()
    db.create_profile(name='Dup', plex_account_id=41)
    monkeypatch.setattr('core.plex_user_auth.get_server_authorized_plex_ids',
                        lambda: _users((41, 'Dup', 'dup')))
    r = client.post('/api/profiles/plex/import', json={'plex_account_ids': [41]})
    body = r.get_json()
    assert body['imported'] == []
    assert body['skipped'] == [{'plex_account_id': 41, 'reason': 'already_imported'}]
    assert sum(1 for p in db.get_all_profiles() if p.get('plex_account_id') == 41) == 1


def test_import_skips_no_longer_authorized(client, monkeypatch):
    monkeypatch.setattr('core.plex_user_auth.get_server_authorized_plex_ids', lambda: _users())
    r = client.post('/api/profiles/plex/import', json={'plex_account_ids': [999]})
    body = r.get_json()
    assert body['imported'] == []
    assert body['skipped'] == [{'plex_account_id': 999, 'reason': 'no_longer_authorized'}]


def test_import_name_collision_gets_suffix(client, monkeypatch):
    db = web_server.get_database()
    db.create_profile(name='Existing')   # ordinary local profile with this name
    monkeypatch.setattr('core.plex_user_auth.get_server_authorized_plex_ids',
                        lambda: _users((51, 'Existing', 'existing')))
    r = client.post('/api/profiles/plex/import', json={'plex_account_ids': [51]})
    body = r.get_json()
    assert body['imported'][0]['name'] == 'Existing'   # requested name, but...
    created = db.get_profile(body['imported'][0]['profile_id'])
    assert created['name'] == 'Existing (Plex)'        # ...the ACTUAL stored name got the suffix


def test_import_batch_continues_past_one_failure(client, monkeypatch):
    db = web_server.get_database()
    db.create_profile(name='Blocker')
    db.create_profile(name='Blocker (Plex)')   # both fallback names taken -> forces a failure
    monkeypatch.setattr('core.plex_user_auth.get_server_authorized_plex_ids',
                        lambda: _users((61, 'Blocker', 'blocker'), (62, 'Fine', 'fine')))
    r = client.post('/api/profiles/plex/import', json={'plex_account_ids': [61, 62]})
    body = r.get_json()
    assert body['success'] is True
    assert {f['plex_account_id'] for f in body['failed']} == {61}
    assert {i['plex_account_id'] for i in body['imported']} == {62}
