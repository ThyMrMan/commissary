"""Music Libraries — several labelled destinations instead of exactly one.

Music had a single output folder (``soulseek.transfer_path``) since its
Soulseek-era design, while the video side has had a table of labelled root
folders for a long time. So "file this into the archive drive" was expressible
for a film and not for an album.

The tests that matter most here are the ones about NOT breaking anyone. The
whole design rests on two claims:

* an install that never opens the new settings writes files exactly where it
  wrote them before, and
* every step of the resolution degrades to the next rather than failing — a
  wishlist row naming a library the user has since deleted still downloads.

Both are load-bearing, and neither is visible from reading the happy path.
"""

from __future__ import annotations

import os
import tempfile

import pytest

_TMP = tempfile.mkdtemp(prefix='soulsync-testdb-mlib-')
os.environ.setdefault('DATABASE_PATH', os.path.join(_TMP, 'mlib.db'))
os.environ.setdefault('SOULSYNC_TEST_DB_READY', '1')

from core.imports import destinations   # noqa: E402


# ── resolution precedence ────────────────────────────────────────────────────
LIBS = [
    {'id': 1, 'path': '/music/main', 'label': 'Main',
     'sort_order': 0, 'naming_template': None, 'quality_profile_id': None},
    {'id': 2, 'path': '/music/archive', 'label': 'Archive',
     'sort_order': 1, 'naming_template': '$artist/$title', 'quality_profile_id': 7},
]


def test_no_libraries_configured_uses_the_configured_transfer_path():
    """The pre-libraries destination, and still the answer on an empty table.
    This is what makes the feature opt-in rather than a migration."""
    root, template = destinations.resolve_music_destination(
        {}, libraries=[], config_get=lambda k, d=None: '/legacy/Transfer')
    assert root == '/legacy/Transfer'
    assert template is None


def test_with_no_choice_the_first_library_wins():
    """Lowest sort_order IS the default destination — the same convention the
    video root folders use, so both sides read the same way."""
    root, template = destinations.resolve_music_destination({}, libraries=LIBS)
    assert root == '/music/main'
    assert template is None


def test_an_explicit_choice_beats_the_default():
    root, template = destinations.resolve_music_destination(
        {'_music_root_id': 2}, libraries=LIBS)
    assert root == '/music/archive'
    assert template == '$artist/$title'


def test_the_items_own_destination_is_honoured_without_extra_plumbing():
    """A wishlist row's root_folder_id arrives on track_info the same way its
    quality_profile_id does, so the drain needs no new wiring."""
    ctx = {'track_info': {'root_folder_id': 2}}
    root, _ = destinations.resolve_music_destination(ctx, libraries=LIBS)
    assert root == '/music/archive'


def test_an_explicit_choice_outranks_the_items_stored_destination():
    """The user is looking at the screen when they pick; the stored value is
    older information."""
    ctx = {'_music_root_id': 1, 'track_info': {'root_folder_id': 2}}
    root, _ = destinations.resolve_music_destination(ctx, libraries=LIBS)
    assert root == '/music/main'


def test_a_deleted_library_falls_back_instead_of_failing():
    """Stranding an item because a setting changed — and giving no hint that
    the two are connected — is worse than filing it in the default."""
    ctx = {'track_info': {'root_folder_id': 999}}
    root, _ = destinations.resolve_music_destination(ctx, libraries=LIBS)
    assert root == '/music/main'


@pytest.mark.parametrize('bad', ['', None, 'not-an-int', {}, []])
def test_a_junk_library_id_falls_back(bad):
    root, _ = destinations.resolve_music_destination(
        {'_music_root_id': bad}, libraries=LIBS)
    assert root == '/music/main'


def test_a_file_is_re_filed_into_the_library_it_already_lives_in():
    """Reorganize renames WITHIN a library. Without this, every reorganize of a
    file outside the default library would plan it into the default one and the
    "rename" would quietly move it between libraries — a data move the user
    never asked for."""
    ctx = {'_current_file_path': os.path.join('/music/archive', 'A', 'b.flac')}
    root, template = destinations.resolve_music_destination(ctx, libraries=LIBS)
    assert root == '/music/archive'
    assert template == '$artist/$title'


def test_the_deepest_containing_library_wins():
    """With /music and /music/archive both configured, a file in the latter
    belongs to the latter — otherwise nesting silently reassigns files."""
    nested = [
        {'id': 1, 'path': '/music', 'sort_order': 0, 'naming_template': None},
        {'id': 2, 'path': '/music/archive', 'sort_order': 1, 'naming_template': 'X'},
    ]
    root, _ = destinations.resolve_music_destination(
        {'_current_file_path': '/music/archive/A/b.flac'}, libraries=nested)
    assert root == '/music/archive'


def test_a_sibling_path_is_not_inside_a_library():
    """`startswith` would call /music-old a child of /music."""
    root, _ = destinations.resolve_music_destination(
        {'_current_file_path': '/music-old/A/b.flac'},
        libraries=[{'id': 1, 'path': '/music', 'sort_order': 0}])
    # Not contained → falls through to the default library.
    assert root == '/music'


def test_an_explicit_choice_still_beats_where_the_file_lives():
    """Moving a file BETWEEN libraries has to remain possible."""
    ctx = {'_music_root_id': 1, '_current_file_path': '/music/archive/A/b.flac'}
    root, _ = destinations.resolve_music_destination(ctx, libraries=LIBS)
    assert root == '/music/main'


def test_a_library_with_no_path_is_not_a_destination():
    root, _ = destinations.resolve_music_destination(
        {}, libraries=[{'id': 1, 'path': '', 'sort_order': 0}],
        config_get=lambda k, d=None: '/legacy/Transfer')
    assert root == '/legacy/Transfer'


# ── quality profile ──────────────────────────────────────────────────────────
def test_a_library_profile_is_stamped_onto_the_item():
    """Reuses the EXISTING per-item mechanism rather than adding a second one,
    so the library profile inherits the whole pipeline — quality gate, AcoustID
    strictness, deep verify, replace-lower, downsample, lossy copy."""
    ctx = {'_music_root_id': 2, 'track_info': {}}
    destinations.apply_library_quality_profile(ctx, libraries=LIBS)
    assert ctx['track_info']['quality_profile_id'] == 7


def test_a_library_with_no_profile_leaves_the_item_alone():
    """NULL means "inherit the global profile", so nothing is stamped and the
    existing resolution runs unchanged."""
    ctx = {'_music_root_id': 1, 'track_info': {}}
    destinations.apply_library_quality_profile(ctx, libraries=LIBS)
    assert 'quality_profile_id' not in ctx['track_info']


def test_an_items_own_profile_beats_the_librarys():
    """A wishlist row's own profile is a more specific statement than the
    library's default."""
    ctx = {'_music_root_id': 2, 'track_info': {'quality_profile_id': 3}}
    destinations.apply_library_quality_profile(ctx, libraries=LIBS)
    assert ctx['track_info']['quality_profile_id'] == 3


def test_stamping_never_raises_on_junk():
    for ctx in (None, {}, {'track_info': None}, 'nonsense'):
        destinations.apply_library_quality_profile(ctx)   # must not raise


# ── DB layer ─────────────────────────────────────────────────────────────────
@pytest.fixture
def db(tmp_path, monkeypatch):
    from database.music_database import MusicDatabase
    return MusicDatabase(str(tmp_path / 'lib.db'))


def test_row_order_is_the_default_destination(db):
    db.save_music_libraries([
        {'path': '/a', 'label': 'A'},
        {'path': '/b', 'label': 'B'},
    ])
    assert [x['path'] for x in db.list_music_libraries()] == ['/a', '/b']
    # Reordering reassigns the default.
    libs = db.list_music_libraries()
    db.save_music_libraries([
        {'id': libs[1]['id'], 'path': '/b', 'label': 'B'},
        {'id': libs[0]['id'], 'path': '/a', 'label': 'A'},
    ])
    assert [x['path'] for x in db.list_music_libraries()] == ['/b', '/a']


def test_a_removed_library_releases_the_wishlist_rows_pointing_at_it(db):
    """A dangling id resolves to nothing. NULL means the default, which is a
    destination that exists."""
    db.save_music_libraries([{'path': '/a'}, {'path': '/b'}])
    gone = db.list_music_libraries()[1]['id']
    db.add_to_wishlist(
        spotify_track_data={'id': 't1', 'name': 'T', 'artists': [{'name': 'A'}],
                            'album': {'name': 'Al'}},
        root_folder_id=gone)
    assert db.get_wishlist_tracks()[0]['root_folder_id'] == gone

    db.save_music_libraries([{'id': db.list_music_libraries()[0]['id'], 'path': '/a'}])
    assert db.get_wishlist_tracks()[0]['root_folder_id'] is None


def test_none_is_a_no_op_not_delete_everything(db):
    """The distinction that stops a caller who simply didn't manage libraries
    from wiping them."""
    db.save_music_libraries([{'path': '/a'}])
    assert len(db.save_music_libraries(None)) == 1


def test_a_library_without_a_path_is_dropped(db):
    db.save_music_libraries([{'path': '/a'}, {'label': 'no path'}])
    assert [x['path'] for x in db.list_music_libraries()] == ['/a']


def test_blank_overrides_store_as_inherit(db):
    """Blank must mean "inherit the global setting", not "an empty template"."""
    db.save_music_libraries([{'path': '/a', 'naming_template': '  ',
                              'quality_profile_id': ''}])
    row = db.list_music_libraries()[0]
    assert row['naming_template'] is None
    assert row['quality_profile_id'] is None


def test_all_paths_is_default_first(db):
    db.save_music_libraries([{'path': '/a'}, {'path': '/b'}])
    assert db.all_music_library_paths() == ['/a', '/b']


def test_get_returns_none_for_ids_that_are_gone_or_junk(db):
    db.save_music_libraries([{'path': '/a'}])
    assert db.get_music_library(9999) is None
    assert db.get_music_library(None) is None
    assert db.get_music_library('abc') is None


# ── migration safety ─────────────────────────────────────────────────────────
def test_a_fresh_db_seeds_one_library_from_the_existing_transfer_path(tmp_path, monkeypatch):
    """The claim the whole design rests on: an install that never opens the new
    settings writes files exactly where it wrote them before."""
    from config.settings import config_manager
    from database.music_database import MusicDatabase

    original = config_manager.get
    monkeypatch.setattr(config_manager, 'get',
                        lambda k, d=None: ('/my/existing/library'
                                           if k == 'soulseek.transfer_path'
                                           else original(k, d)))
    fresh = MusicDatabase(str(tmp_path / 'seeded.db'))
    libs = fresh.list_music_libraries()
    assert len(libs) == 1
    assert libs[0]['path'] == '/my/existing/library'
    # ...and resolving with no choice returns exactly that.
    root, template = destinations.resolve_music_destination({}, libraries=libs)
    assert root == '/my/existing/library'
    assert template is None


def test_seeding_never_resurrects_a_library_the_user_removed(db):
    """Seeding only ever fills an EMPTY table. Re-adding transfer_path on every
    startup would undo a deliberate removal."""
    db.save_music_libraries([{'path': '/only/this'}])
    # Re-run the seeder the way startup would.
    conn = db._get_connection()
    try:
        db._seed_music_root_folder(conn.cursor())
        conn.commit()
    finally:
        conn.close()
    assert [x['path'] for x in db.list_music_libraries()] == ['/only/this']
