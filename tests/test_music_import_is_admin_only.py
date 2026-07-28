"""Music Import is library management, not a per-profile page.

Reported as: hide the Import button on the Music side from standard/Plex users.

It was an allowed_pages toggle that DEFAULTED ON, so every standard and Plex
profile could see it — while the equivalent on the video side (video-import) has
always been hard admin-only. And none of the /api/import/* endpoints had any
permission check at all, including album/process and singles/process, which move
staged files into the shared music library.

So Import now matches video's: admin-only in the nav, admin-only on navigation,
admin-only on every endpoint, and no longer offered as a checkbox in the profile
editor (a toggle that cannot change anything is worse than no toggle).
"""

from __future__ import annotations

import pathlib
import re

_ROOT = pathlib.Path(__file__).resolve().parents[1]


def _init_js():
    return (_ROOT / "webui" / "static" / "init.js").read_text(encoding="utf-8")


def _index_html():
    return (_ROOT / "webui" / "index.html").read_text(encoding="utf-8")


def _web_server():
    return (_ROOT / "web_server.py").read_text(encoding="utf-8")


# ── the nav button ───────────────────────────────────────────────────────────
def test_the_nav_button_is_admin_only():
    js = _init_js()
    assert "const MUSIC_ADMIN_ONLY = ['import'];" in js
    assert "if (page === 'settings' || MUSIC_ADMIN_ONLY.includes(page)) {" in js


def test_navigation_is_gated_too_not_just_the_button():
    """isPageAllowed is what stops /import being reached by typing the URL. The
    nav loop re-derives its own rule, so BOTH need the entry — with only one, a
    profile whose allowed_pages is null (all pages) still sees the button."""
    js = _init_js()
    fn = js.split("function isPageAllowed(", 1)[1].split("\nfunction ", 1)[0]
    assert "normalizedPageId === 'import'" in fn
    assert "return currentProfile.is_admin" in fn


def test_the_two_places_agree_about_import():
    """The bug this guards: hiding it in one and not the other."""
    js = _init_js()
    nav = js.split("const MUSIC_ADMIN_ONLY", 1)[1][:200]
    page_fn = js.split("function isPageAllowed(", 1)[1].split("\nfunction ", 1)[0]
    assert "'import'" in nav and "'import'" in page_fn


# ── the profile editor no longer offers it ───────────────────────────────────
def test_import_is_not_a_page_checkbox_any_more():
    html = _index_html()
    assert 'type="checkbox" value="import"' not in html, \
        "a toggle that can't change anything is worse than none"


def test_import_is_not_offered_as_a_home_page_either():
    """Separate control, same problem: a home page the profile is not allowed to
    open would just bounce them somewhere else on every sign-in."""
    html = _index_html()
    assert '<option value="import">' not in html


def test_the_music_group_says_so():
    html = _index_html()
    label = html.split('profile-page-group-label--first">Music', 1)[1][:200]
    assert "admin-only" in label


# ── every import endpoint ────────────────────────────────────────────────────
def test_every_import_route_is_admin_gated():
    """Including the GETs: the staging listings expose on-disk paths, and video's
    /api/video/import is admin for ANY method for the same reason."""
    src = _web_server()
    routes = re.findall(r"@app\.route\('(/api/import/[^']+)'[^\n]*\n(@[a-z_]+\n)*", src)
    assert routes, "expected to find /api/import/* routes"
    for path, _ in routes:
        block = src.split("@app.route('%s'" % path, 1)[1][:200]
        decorators = block.split("\ndef ", 1)[0]
        assert "@admin_only" in decorators, path


def test_the_two_that_write_to_the_library_are_covered():
    """Named explicitly — these move staged files into the shared music library,
    so they are the ones that most needed a gate."""
    src = _web_server()
    for path in ("/api/import/album/process", "/api/import/singles/process"):
        block = src.split("@app.route('%s'" % path, 1)[1][:200]
        assert "@admin_only" in block.split("\ndef ", 1)[0], path


def test_admin_only_is_the_shared_decorator():
    """Not a second, divergent rule."""
    src = _web_server()
    fn = src.split("def admin_only(view_fn):", 1)[1].split("\ndef ", 1)[0]
    assert "get_current_profile_id() != 1" in fn
    assert "403" in fn
