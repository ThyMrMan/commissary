"""The lock screen must not hide the Plex PIN panel.

Reported: with Security → Require login on, "Sign in with Plex" did nothing.
Password sign-in on the same screen worked, which is what made it look like a
Plex problem.

It wasn't. showLoginScreen() puts `app-locked` on <body>, and the #852 rule
hides every direct child of <body> except the lock overlays. #plex-signin-overlay
is a lock overlay too — it is the panel that displays the plex.tv PIN code and it
is opened FROM the login screen — but it was never added to the exception list.
So the click did everything right (the POST returned a real code, polling
started) and the code was rendered underneath `display: none !important`.

It worked from the profile picker because that path never sets app-locked, which
is exactly why it only surfaced once Require Login was turned on.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parent.parent
_CSS = (_ROOT / "webui" / "static" / "style.css").read_text(encoding="utf-8")
_HTML = (_ROOT / "webui" / "index.html").read_text(encoding="utf-8")
_INIT = (_ROOT / "webui" / "static" / "init.js").read_text(encoding="utf-8")

# The single rule that blanks the app while the lock screen is up.
_LOCK_RULE = next(
    (ln for ln in _CSS.splitlines() if ln.startswith("body.app-locked >")), "")


def test_the_lock_rule_still_exists():
    """If this disappears, the hardening it provides is gone and the rest of this
    file is silently vacuous."""
    assert _LOCK_RULE, "body.app-locked child-hiding rule not found in style.css"
    assert "display: none !important" in _CSS.split(_LOCK_RULE, 1)[1][:80]


@pytest.mark.parametrize("overlay_id", [
    "launch-pin-overlay",     # PIN lock
    "login-overlay",          # username/password lock
    "plex-signin-overlay",    # the PIN-code panel opened FROM the login screen
])
def test_every_lock_overlay_survives_app_locked(overlay_id):
    assert f":not(#{overlay_id})" in _LOCK_RULE, (
        f"#{overlay_id} is hidden by body.app-locked — it will be invisible on "
        f"the lock screen even when its code sets display:flex")


def test_the_rule_still_hides_ordinary_app_chrome():
    """The exceptions must stay a short allowlist, not creep into 'everything'.
    The point of #852 is that removing the overlay reveals a blank page."""
    excepted = set(re.findall(r":not\(#([a-z0-9-]+)\)", _LOCK_RULE))
    assert excepted == {"launch-pin-overlay", "login-overlay", "plex-signin-overlay"}


def test_the_plex_panel_is_a_direct_child_of_body():
    """The rule only applies to `body > *`. If the overlay were ever nested inside
    another container, the exception above would stop protecting it — it would be
    hidden along with its (non-excepted) parent instead."""
    body = _HTML.split("<body", 1)[1]
    # everything nested one level deeper than a top-level element
    depth = 0
    for chunk in re.split(r"(<div[^>]*>|</div>)", body):
        if chunk.startswith('<div') and 'id="plex-signin-overlay"' in chunk:
            assert depth == 0, "plex-signin-overlay is nested; app-locked will hide its parent"
            return
        if chunk.startswith("<div"):
            depth += 1
        elif chunk == "</div>":
            depth -= 1
    pytest.fail("plex-signin-overlay not found in index.html")


def test_the_login_screen_is_what_sets_app_locked():
    """Anchors the cause: this is the function whose lock class hid the panel."""
    fn = _INIT.split("function showLoginScreen", 1)[1].split("\n}", 1)[0]
    assert "app-locked" in fn and "login-overlay" in fn


def test_both_entry_points_open_the_same_panel():
    """Login screen and profile picker share one overlay — so the picker path
    (which never sets app-locked) was fine while the login path was not."""
    for btn in ("login-plex-signin-btn", "picker-plex-signin-btn"):
        block = _HTML.split(f'id="{btn}"', 1)[1][:200]
        assert "startPlexSignIn()" in block, btn


# ── the other half: pollers must not hammer auth-gated endpoints while locked ──
_ENRICHMENT = (_ROOT / "webui" / "static" / "enrichment.js").read_text(encoding="utf-8")
_APIMON = (_ROOT / "webui" / "static" / "api-monitor.js").read_text(encoding="utf-8")
_SACT = (_ROOT / "webui" / "static" / "server-activity.js").read_text(encoding="utf-8")
_REQS = (_ROOT / "webui" / "static" / "video" / "video-requests.js").read_text(encoding="utf-8")

_GUARD = "document.body.classList.contains('app-locked')"


def test_every_enrichment_poller_is_lock_guarded():
    """15 pollers (13 services + hydrabase + repair) each on a 10s interval. While
    the login screen is up they are all auth-gated, so every cycle was a burst of
    401s that buried real errors in the console."""
    starts = _ENRICHMENT.count("if (socketConnected) return;")
    guards = _ENRICHMENT.count(_GUARD)
    assert starts == 15, f"poller count changed ({starts}); re-check the guards"
    assert guards >= starts, f"only {guards} of {starts} enrichment pollers are guarded"


# Parametrised on the PATH, not the file contents — pytest builds test ids from
# the params, and passing ~300KB of JS in makes the id (and any failure output)
# unreadable.
@pytest.mark.parametrize("rel", [
    "webui/static/api-monitor.js",              # system stats
    "webui/static/server-activity.js",          # activity badge, app-wide 20s
    "webui/static/video/video-requests.js",     # request-count badge
])
def test_the_other_recurring_pollers_are_guarded(rel):
    src = (_ROOT / rel).read_text(encoding="utf-8")
    assert _GUARD in src, f"{rel} still polls while the app is locked"


def test_the_guard_is_a_read_not_a_write():
    """It must only ever TEST the class. A poller that cleared app-locked would
    unlock the UI from a background timer."""
    for src in (_ENRICHMENT, _APIMON, _SACT, _REQS):
        assert "classList.remove('app-locked')" not in src
        assert "classList.add('app-locked')" not in src
