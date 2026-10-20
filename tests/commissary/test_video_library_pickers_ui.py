"""The library pickers attach to upstream markup; pin what they rely on.

They are added after upstream renders, keyed off attributes and classes the
pages already carry. If upstream renames one, the pickers silently disappear,
so each hook is pinned here against upstream's own source."""

from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
STATIC = ROOT / "webui" / "static"


def _read(rel):
    return (ROOT / rel).read_text(encoding="utf-8").replace("\r\n", "\n")


PICKERS = _read("webui/static/commissary-video-library-pickers.js")


def test_the_pickers_are_loaded_on_the_page():
    assert "filename='commissary-video-library-pickers.js'" in _read("webui/index.html")


def test_upstream_wishlist_still_carries_the_hooks():
    wishlist = _read("webui/static/video/video-wishlist.js")
    for hook in ('data-vwsh-open-movie="', 'data-vwsh-src="', "data-vwsh-tmdb=",
                 "data-vwsh-source=", "class=\"vwsh-movie-art\"", "'<div class=\"wl-orb-group\"",
                 "class=\"wl-orb-label\""):
        assert hook in wishlist, hook


def test_upstream_watchlist_and_manage_panel_still_carry_the_hooks():
    assert "data-vwlp-open=\"' + kind" in _read("webui/static/video/video-watchlist.js")
    assert ".vcard-ctrls" in _read("webui/static/video/video-side.css")
    manage = _read("webui/static/video/video-manage-panel.js")
    assert "data-vmg-quality-profile" in manage and "vmg-field" in manage and "vmg-hint" in manage
    assert "window.VideoManage = " in manage or "window.VideoManage=" in manage


def test_the_menu_reuses_upstreams_menu_styles():
    css = _read("webui/static/video/video-side.css")
    assert ".vreq-menu {" in css and ".vreq-menu-item {" in css
    assert "'vreq-menu'" in PICKERS and "'vreq-menu-item'" in PICKERS


def test_the_watchlist_button_is_never_a_vwl_btn():
    """Upstream catches every .vwl-btn click page-wide (capture phase) and
    toggles the watchlist: a picker carrying that class would unfollow the show."""
    assert "closest('.vwl-btn')" in _read("webui/static/video/video-watchlist-btn.js")
    assert "refresh(row, 'commissary-vlib-wbtn'" in PICKERS
    assert "'vwl-btn'" not in PICKERS


def test_redecorating_leaves_an_unchanged_button_alone():
    """Replacing buttons on every pass is a DOM change that wakes the observer
    again: an endless loop."""
    assert "old.getAttribute('data-commissary-vlib-cur') === cur) return;" in PICKERS
    assert "requestAnimationFrame(" not in PICKERS, "rAF is paused in background tabs"
