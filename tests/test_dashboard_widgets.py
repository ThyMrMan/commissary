"""Admin-controlled dashboard widgets.

An admin picks, in Settings, which dashboard cards non-admin profiles see
(``dashboard_widgets.member_hidden``). Admins always see everything.

Three things have to hold or the feature silently does nothing:

  * the section must be in POST /api/settings' hard-coded allowlist, or saving
    it writes nothing at all;
  * the policy must ride on GET /api/profiles/current, because /api/settings is
    @admin_only and the members it applies to could never read it there;
  * the widget registry in webui/static/dashboard-widgets.js must match both
    VALID_WIDGET_IDS and the data-card attributes actually in index.html — a
    card missing from the registry is simply not configurable, and nobody would
    notice until someone asked why it won't hide.

The last one is the drift guard, and it is the reason this file exists.

Heavy (imports web_server once), so isolated in its own module per the
convention in tests/test_settings_partial_save.py.
"""

from __future__ import annotations

import os
import re
import tempfile

import pytest

# Redirect the DB before importing web_server so it never touches a real library.
_TMP = tempfile.mkdtemp(prefix='soulsync-testdb-dashwidgets-')
os.environ['DATABASE_PATH'] = os.path.join(_TMP, 'dash_widgets.db')
os.environ['SOULSYNC_TEST_DB_READY'] = '1'

web_server = pytest.importorskip('web_server')

from config.settings import config_manager  # noqa: E402

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _read(rel):
    with open(os.path.join(_ROOT, *rel.split('/')), encoding='utf-8') as fh:
        return fh.read()


_WIDGETS_JS = _read('webui/static/dashboard-widgets.js')
_INDEX = _read('webui/index.html')
_SETTINGS_JS = _read('webui/static/settings.js')


@pytest.fixture
def client():
    return web_server.app.test_client()


@pytest.fixture(autouse=True)
def _restore_config():
    """config_manager is a process-wide singleton. These tests write real
    config, so snapshot and restore it (see tests/test_settings_partial_save)."""
    import copy
    before = copy.deepcopy(config_manager.config_data)
    yield
    config_manager.config_data = copy.deepcopy(before)
    config_manager._save_config()


# ── defaults ─────────────────────────────────────────────────────────────────

def test_default_hides_nothing():
    """An install that has never touched the setting must behave exactly as it
    did before the feature existed."""
    defaults = config_manager._get_default_config()
    assert defaults['dashboard_widgets']['member_hidden'] == []


# ── the save path ────────────────────────────────────────────────────────────

def test_section_is_in_the_endpoint_allowlist(client):
    """The endpoint only writes sections named in its allowlist; one that isn't
    there saves silently-nothing."""
    r = client.post('/api/settings',
                    json={'dashboard_widgets': {'member_hidden': ['music.stats']}})
    assert r.status_code == 200 and r.get_json()['success']
    assert config_manager.get('dashboard_widgets.member_hidden') == ['music.stats']


def test_unknown_widget_ids_are_dropped(client):
    """Mirrors how allowed_pages is filtered through VALID_PAGE_IDS: junk is
    dropped rather than 400'd, so a stale client can't wedge the setting."""
    client.post('/api/settings', json={'dashboard_widgets': {
        'member_hidden': ['music.stats', 'not.a.widget', 'video.studios', '']}})
    assert config_manager.get('dashboard_widgets.member_hidden') == [
        'music.stats', 'video.studios']


def test_saving_widgets_leaves_other_sections_intact(client):
    config_manager.set('spotify.client_id', 'MUSIC-ONLY-VALUE')
    config_manager.set('prowlarr.url', 'http://keep-me:9696')

    r = client.post('/api/settings',
                    json={'dashboard_widgets': {'member_hidden': ['music.services']}})
    assert r.status_code == 200 and r.get_json()['success']

    assert config_manager.get('spotify.client_id') == 'MUSIC-ONLY-VALUE'
    assert config_manager.get('prowlarr.url') == 'http://keep-me:9696'


def test_every_id_is_hideable(client):
    """Every registered widget must survive a round-trip — one that silently
    vanishes is a checkbox that does nothing."""
    all_ids = sorted(web_server.VALID_WIDGET_IDS)
    client.post('/api/settings', json={'dashboard_widgets': {'member_hidden': all_ids}})
    assert config_manager.get('dashboard_widgets.member_hidden') == all_ids


# ── the delivery channel ─────────────────────────────────────────────────────

def test_policy_is_readable_without_admin(client):
    """/api/settings is @admin_only, so the policy has to arrive some other
    way. If this regresses, members silently see every card."""
    config_manager.set('dashboard_widgets.member_hidden', ['music.stats'])

    r = client.get('/api/profiles/current')
    assert r.status_code == 200
    body = r.get_json()
    assert body.get('dashboard_widgets_hidden') == ['music.stats']


def test_policy_is_present_before_a_profile_is_chosen(client):
    """The profile-picker path reaches the dashboard without ever hitting the
    success branch, so the early returns must carry the policy too."""
    config_manager.set('dashboard_widgets.member_hidden', ['video.studios'])

    with client.session_transaction() as sess:
        sess.pop('profile_id', None)

    body = client.get('/api/profiles/current').get_json()
    assert body.get('dashboard_widgets_hidden') == ['video.studios']


def test_settings_endpoint_still_requires_admin(client):
    """The delivery workaround exists BECAUSE this is admin-only. Pinned as a
    real request: if /api/settings ever opened up, the extra payload field on
    /api/profiles/current would be pointless indirection."""
    with client.session_transaction() as sess:
        sess['profile_id'] = 2  # anything but the always-admin profile 1

    # 401 (an earlier session gate) or 403 (@admin_only) — either way, closed.
    assert client.get('/api/settings').status_code in (401, 403)


# ── registry drift: the guard that keeps this maintainable ───────────────────

def _registry_ids():
    return set(re.findall(r"id:\s*'([\w.-]+)'", _WIDGETS_JS))


def _cards_between(start_marker, end_marker):
    """data-card values inside one dashboard's markup."""
    start = _INDEX.index(start_marker)
    end = _INDEX.index(end_marker, start)
    return set(re.findall(r'data-card="([\w-]+)"', _INDEX[start:end]))


def test_registry_matches_server_side_whitelist():
    assert _registry_ids() == set(web_server.VALID_WIDGET_IDS), (
        "dashboard-widgets.js and VALID_WIDGET_IDS disagree — ids only in the "
        "JS are silently dropped on save; ids only in Python are unreachable")


def test_every_music_card_is_registered():
    cards = _cards_between('id="dashboard-page"', 'id="sync-page"')
    registered = {i.split('.', 1)[1] for i in _registry_ids() if i.startswith('music.')}
    assert cards <= registered, (
        f"music dashboard cards missing from the registry: {sorted(cards - registered)} "
        "— they render for everyone and can't be turned off")


def test_every_video_card_is_registered():
    cards = _cards_between('data-video-subpage="video-dashboard"',
                           'data-video-subpage="video-search"')
    registered = {i.split('.', 1)[1] for i in _registry_ids() if i.startswith('video.')}
    assert cards <= registered, (
        f"video dashboard cards missing from the registry: {sorted(cards - registered)}")


# Registry entries that address their element by CSS selector rather than a
# data-card attribute (header controls and sidebar nav entries).
_NON_CARD_SUFFIXES = ('header-enrich', 'manage-workers', 'nav-', 'help-button')


def _is_card(widget_id):
    name = widget_id.split('.', 1)[1]
    return not any(name.startswith(s) or name == s for s in _NON_CARD_SUFFIXES)


def test_no_registered_card_is_missing_from_the_markup():
    """The other direction: a registry entry with no matching card is a
    checkbox that does nothing."""
    in_markup = set(re.findall(r'data-card="([\w-]+)"', _INDEX))
    for wid in _registry_ids():
        if not _is_card(wid):
            continue
        assert wid.split('.', 1)[1] in in_markup, f"{wid} has no data-card in index.html"


# ── nav entries: hiding the link must also block the page ────────────────────

def test_nav_widgets_name_a_real_page():
    """A nav entry with no `page` couldn't block navigation, and one naming a
    page the server rejects would be unenforceable from the profile side."""
    pages = set(re.findall(r"page:\s*'([\w-]+)'", _WIDGETS_JS))
    assert {'automations', 'tools', 'video-tools'} <= pages
    # Every nav widget's selector must target the nav button for that page.
    for page in ('tools', 'automations'):
        assert f'data-page="{page}"' in _WIDGETS_JS


def test_hidden_nav_blocks_navigation_not_just_the_link():
    """Without this the page is still reachable by URL and the toggle is
    decorative. isPageAllowed is the single gate navigateToPage consults."""
    init_js = _read('webui/static/init.js')
    start = init_js.index('function isPageAllowed')
    body = init_js[start:init_js.index('\nfunction ', start + 10)]
    assert 'isPageHiddenByPolicy' in body, (
        "isPageAllowed ignores the widget policy — a hidden Tools/Automations "
        "page would still load by typing its URL")


def test_nav_filter_loop_applies_the_policy_itself():
    """The loop reruns on every profile change and sets display unconditionally,
    so it must consult the policy or it silently undoes applyWidgetPolicy."""
    init_js = _read('webui/static/init.js')
    assert init_js.count('isPageHiddenByPolicy') >= 3, (
        "expected the policy check in isPageAllowed plus BOTH nav filter loops")


def test_policy_composes_with_allowed_pages_rather_than_replacing_it():
    """Hidden by either must mean hidden — the per-profile list still narrows."""
    init_js = _read('webui/static/init.js')
    start = init_js.index('Filter sidebar pages based on profile permissions')
    # Slice to the END OF THE LOOP, not a fixed character count: a comment added
    # inside it used to push these assertions out of the window and fail for
    # reasons that had nothing to do with the behaviour being pinned.
    body = init_js[start:].split('\n    });', 1)[0]
    assert 'allowed_pages' in body and 'isPageHiddenByPolicy' in body


# ── the 1.2.0 -> 1.3.0 header split ──────────────────────────────────────────

def test_manage_workers_is_separately_hideable():
    assert 'music.manage-workers' in web_server.VALID_WIDGET_IDS
    assert 'video.manage-workers' in web_server.VALID_WIDGET_IDS


def test_header_icons_selector_excludes_the_manage_button():
    """Otherwise hiding the icons would take Manage Workers with it and the
    split would be meaningless."""
    assert ':not(.em-manage-btn)' in _WIDGETS_JS


def test_header_split_migration_carries_the_old_setting_across(monkeypatch):
    """An install that hid the combined 1.2.0 widget meant "hide Manage Workers
    too"; upgrading must not silently reveal the button."""
    config_manager.set('dashboard_widgets.member_hidden',
                       ['music.header-enrich', 'video.header-enrich'])
    config_manager.set('dashboard_widgets.header_split_migrated', False)
    monkeypatch.setattr(web_server, '_header_split_checked', False)

    web_server.migrate_header_widget_split_once()

    hidden = config_manager.get('dashboard_widgets.member_hidden')
    assert 'music.manage-workers' in hidden
    assert 'video.manage-workers' in hidden
    assert config_manager.get('dashboard_widgets.header_split_migrated') is True


def test_header_split_migration_is_idempotent(monkeypatch):
    config_manager.set('dashboard_widgets.member_hidden', ['music.header-enrich'])
    config_manager.set('dashboard_widgets.header_split_migrated', False)
    monkeypatch.setattr(web_server, '_header_split_checked', False)
    web_server.migrate_header_widget_split_once()
    first = list(config_manager.get('dashboard_widgets.member_hidden'))

    # Re-running must not re-add, and must not undo a deliberate later unhide.
    monkeypatch.setattr(web_server, '_header_split_checked', False)
    config_manager.set('dashboard_widgets.member_hidden', ['music.header-enrich'])
    web_server.migrate_header_widget_split_once()
    assert config_manager.get('dashboard_widgets.member_hidden') == ['music.header-enrich']
    assert 'music.manage-workers' in first


def test_migration_does_not_touch_an_untouched_install(monkeypatch):
    config_manager.set('dashboard_widgets.member_hidden', [])
    config_manager.set('dashboard_widgets.header_split_migrated', False)
    monkeypatch.setattr(web_server, '_header_split_checked', False)
    web_server.migrate_header_widget_split_once()
    assert config_manager.get('dashboard_widgets.member_hidden') == []


# ── source guards: the wiring the feature depends on ─────────────────────────

def test_selectors_are_scoped_per_side():
    """"stats", "library" and "tools" exist as data-card values on BOTH
    dashboards. An unscoped lookup hides the wrong card."""
    assert 'MUSIC_DASH_ROOT' in _WIDGETS_JS and 'VIDEO_DASH_ROOT' in _WIDGETS_JS
    assert 'root.querySelector' in _WIDGETS_JS, (
        "widget lookup must be rooted at its side's page element")
    assert not re.search(r'document\.querySelector\(\s*[`\'"]\[data-card',
                         _WIDGETS_JS), "unscoped [data-card] lookup"


def test_visibility_fails_open():
    """A failed policy fetch must degrade to today's behavior (everything
    visible), never to an empty dashboard."""
    body = _WIDGETS_JS[_WIDGETS_JS.index('function isWidgetVisible'):]
    body = body[:body.index('\nfunction ')]
    assert 'if (dashboardWidgetsIsAdmin) return true;' in body
    assert 'if (!dashboardWidgetsHidden) return true;' in body


def test_admins_are_exempt_everywhere():
    assert 'dashboardWidgetsIsAdmin' in _WIDGETS_JS
    assert 'is_admin' in _read('webui/static/init.js')


def test_settings_group_is_shared_so_it_saves_from_the_video_side():
    """One policy covers both dashboards, so the group must be data-shared —
    otherwise editing it from the video Settings nav is silently discarded."""
    start = _INDEX.index('<h3>Standard User Interface</h3>')
    block = _INDEX[start:start + 2000]
    assert 'data-shared' in block
    assert 'id="dashboard-widget-options"' in block


def test_builder_is_registered_as_a_shared_section():
    start = _SETTINGS_JS.index('const SHARED_SECTION_BUILDERS')
    block = _SETTINGS_JS[start:_SETTINGS_JS.index('\n};', start)]
    assert 'dashboard_widgets:' in block


def test_hidden_cards_do_not_poll():
    """The perf half of the feature: a hidden card's timer never starts."""
    src = _read('webui/static/wishlist-tools.js')
    start = src.index('async function loadDashboardData')
    body = src[start:src.index('\n}', start)]
    assert "isWidgetVisible('music.stats')" in body
    assert "isWidgetVisible('music.activity')" in body


def test_service_status_fetch_is_never_gated():
    """fetchAndUpdateServiceStatus also drives the SIDEBAR indicators every
    user sees, and is polled globally from initApp. Gating it would break the
    sidebar for members — that card is presentation-only."""
    src = _read('webui/static/shared-helpers.js')
    start = src.index('async function fetchAndUpdateServiceStatus')
    body = src[start:start + 400]
    assert 'isWidgetVisible' not in body, (
        "service status feeds the sidebar for all users; it must not be gated")


def test_socket_push_path_is_guarded():
    """The server broadcasts dashboard updates to every client, so the polling
    guard alone doesn't cover it."""
    src = _read('webui/static/core.js')
    for handler, widget in (('handleDashboardStats', 'music.stats'),
                            ('handleDashboardActivity', 'music.activity')):
        start = src.index(f'function {handler}')
        body = src[start:src.index('\n}', start)]
        assert f"isWidgetVisible('{widget}')" in body, f"{handler} unguarded"


# ── app chrome: the Interactive Help button ──────────────────────────────────

def test_help_button_is_hideable():
    assert 'shared.help-button' in web_server.VALID_WIDGET_IDS
    assert "selector: '#helper-float-btn'" in _WIDGETS_JS


def test_help_button_belongs_to_neither_side():
    """It floats over both sides, so filing it under Music or Video would
    misrepresent what unchecking it does."""
    start = _WIDGETS_JS.index("id: 'shared.help-button'")
    entry = _WIDGETS_JS[start:_WIDGETS_JS.index('}', start)]
    assert "side: 'shared'" in entry
    assert 'global: true' in entry, (
        "the button lives at body level, outside both dashboard roots — a "
        "side-scoped lookup would never find it")


def test_help_button_group_is_rendered_in_settings():
    start = _SETTINGS_JS.index('const groups = [')
    block = _SETTINGS_JS[start:_SETTINGS_JS.index('];', start)]
    assert "'shared', 'chrome'" in block, (
        "a registry entry with no matching group row renders nowhere — the "
        "checkbox would silently not exist")


def test_helper_js_never_fights_the_policy():
    """applyWidgetPolicy hides via inline display:none. helper.js may toggle
    classes on the button all it likes, but if it ever set style.display the
    button would reappear for members."""
    src = _read('webui/static/helper.js')
    assert 'floatBtn.style.display' not in src
    assert "getElementById('helper-float-btn').style.display" not in src
