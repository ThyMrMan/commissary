"""Library management is not a member's business — and it never was, server-side.

Reported as: standard/Plex users can reach Manage and Manage Poster on the Media
Details page.

Everything behind those two buttons is ALREADY admin-only in the video
blueprint's gate — /poster/set, and the /metadata, /lock, /aka, /library,
/quality-profile, /series-type, /rescan-episodes, /episode-source suffixes. So
this was never an authorization hole; it was a dead end. A member could open the
full metadata editor, change things, and have every save come back 403.

Synchronize sits in the same button row and hits the admin-only /sync suffix, so
it went with them.

Both panels are also guarded at their own entry points, not just behind hidden
buttons — the same defense-in-depth shape the dashboard's Overlay Studio and
Collection Studio launchers already used. The server stays the authority.
"""

from __future__ import annotations

import pathlib

_ROOT = pathlib.Path(__file__).resolve().parents[1]


def _js(name):
    return (_ROOT / "webui" / "static" / "video" / name).read_text(encoding="utf-8")


# ── the buttons on the Media Details page ────────────────────────────────────
def test_the_detail_page_derives_an_admin_flag():
    js = _js("video-detail.js")
    assert "var _isAdmin =" in js


def test_manage_poster_is_admin_only():
    js = _js("video-detail.js")
    block = js.split("// Manage Poster — library items only")[1][:400]
    assert "_isAdmin &&" in block


def test_manage_is_admin_only():
    js = _js("video-detail.js")
    assert "if (_isAdmin && window.VideoManage &&" in js


def test_synchronize_went_with_them():
    """Same button row, same admin-only endpoint (the '/sync' suffix) — leaving it
    would have kept one dead button between two fixed ones."""
    js = _js("video-detail.js")
    assert "d.kind === 'show' && ownLibItem && _isAdmin" in js


def test_the_ask_controls_are_untouched():
    """A member must still be able to ASK. The Request button and the wishlist /
    watchlist controls are gated on canDownload, NOT on the new admin flag —
    tying them to is_admin would take away the only thing members can do."""
    js = _js("video-detail.js")
    assert "var _canDl = (typeof canDownload !== 'function') || canDownload();" in js
    request_block = js.split("data-vd-act=\"request\"")[0][-400:]
    assert "_isAdmin" not in request_block


# ── the panels guard themselves too ──────────────────────────────────────────
def test_the_manage_panel_refuses_to_open_for_a_member():
    """Hiding the launcher is not the check. A panel whose every save 403s should
    not open at all, however it was reached."""
    js = _js("video-manage-panel.js")
    body = js.split("function open(opts) {")[1][:600]
    assert "currentProfile.is_admin" in body


def test_both_poster_entries_are_guarded():
    """open() is the detail page's; openSearch() is the dashboard quick action.
    Guarding only the one named in the report would leave the other reachable."""
    js = _js("video-poster-modal.js")
    assert "function _isAdmin()" in js
    assert "function open(opts) {\n        if (!_isAdmin()) return;" in js
    assert "function openSearch() { if (!_isAdmin()) return;" in js


def test_the_dashboard_poster_tile_matches_its_siblings():
    """Overlay Studio and Collection Studio both guard their launcher click; the
    Poster Manager tile next to them did not."""
    js = _js("video-dashboard.js")
    block = js.split("data-video-poster-manager")[1][:500]
    assert "currentProfile.is_admin" in block


# ── the server was already the authority ─────────────────────────────────────
def test_the_endpoints_behind_these_buttons_are_admin_gated():
    """The point of the change is that these were dead ends, not holes. If the
    gate ever stops covering them this test fails and the UI hiding becomes the
    only thing standing there — which is never enough."""
    src = (_ROOT / "api" / "video" / "__init__.py").read_text(encoding="utf-8")
    admin_block = src.split("if admin and not is_admin:")[0]
    assert "/api/video/poster/set" in admin_block
    for suffix in ("/metadata", "/lock", "/aka", "/library", "/sync",
                   "/quality-profile", "/series-type", "/rescan-episodes"):
        assert '"%s"' % suffix in admin_block, suffix
