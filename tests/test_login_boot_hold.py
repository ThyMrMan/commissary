"""Auth-gated API calls wait until we know whether we're signed in.

The 1.7.1 poller guards stopped the RECURRING 401s on the login screen, but ~15
modules also fire a one-shot fetch each at boot (video dashboard, libraries,
scan status, issue/watchlist/wishlist counts, YouTube channels, search sources,
overlay status, …). None can know yet whether a session exists — that answer
only arrives when /api/profiles/current returns — so with login mode on they all
went out and were refused.

webui/static/auth-hold.js parks those until auth is known. It DEFERS rather than
rejecting, which is the whole safety property: on an ordinary install auth state
is briefly unknown too, so failing fast would make every boot fetch return a
synthetic 401, paint an "unavailable" state, and never retry.

Measured on the login screen: 17 requests before, 2 after — exactly the two the
sign-in screen legitimately needs.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
_JS = (_ROOT / "webui" / "static" / "auth-hold.js").read_text(encoding="utf-8")
_HTML = (_ROOT / "webui" / "index.html").read_text(encoding="utf-8")
_INIT = (_ROOT / "webui" / "static" / "init.js").read_text(encoding="utf-8")


def _js_list(name: str) -> set:
    """Pull a JS array literal of string paths out of auth-hold.js."""
    body = _JS.split(f"var {name} = ", 1)[1].split("];", 1)[0] + "]"
    return set(ast.literal_eval(body.replace("'", '"')))


# ── the allowlist must not drift from the server's ───────────────────────────
def test_the_js_allowlist_matches_the_server_gate():
    """auth-hold.js decides what may go out before auth; login_gate.py decides
    what the server will answer. If they disagree, either the login screen
    deadlocks (a held request whose answer releases the hold) or requests go out
    only to be refused — which is the noise this exists to remove."""
    from core.security.login_gate import _ALLOWED_GET, _ALLOWED_POST
    assert _js_list("ALLOW_GET") == set(_ALLOWED_GET)
    assert _js_list("ALLOW_POST") == set(_ALLOWED_POST)


def test_profiles_current_is_never_held():
    """The deadlock case, called out explicitly: its response is what releases
    the hold, so holding it would freeze the app until the watchdog fired."""
    assert "/api/profiles/current" in _js_list("ALLOW_GET")


def test_the_plex_signin_flow_is_never_held():
    """1.7.1's bug was Plex sign-in appearing dead on the login screen. Holding
    its endpoints would reintroduce that symptom by a different route."""
    assert "/api/auth/plex/start" in _js_list("ALLOW_POST")
    assert "/api/auth/plex/status" in _js_list("ALLOW_GET")


# ── the safety properties ────────────────────────────────────────────────────
def test_it_defers_rather_than_rejecting():
    """The property that keeps ordinary installs working: a held call resolves
    to the REAL request once auth turns out to be fine."""
    assert "known.then(" in _JS
    assert "if (!isLocked) return raw(input, init);" in _JS


def test_a_watchdog_always_releases_the_hold():
    """Fail-open. A JS error or a hung /api/profiles/current must cost a delay,
    never a permanently mute app."""
    assert re.search(r"setTimeout\(function \(\) \{ settle\(false\); \}, \d+\)", _JS)


def test_settle_is_idempotent_and_first_call_wins():
    """initProfileSystem's finally settles false on every path; a lock screen
    settles true before it. Without first-call-wins the finally would overwrite
    the lock verdict and every parked request would go out and 401."""
    fn = _JS.split("function settle(", 1)[1].split("\n    }", 1)[0]
    assert "if (settled) return;" in fn


def test_streaming_and_key_authed_calls_are_never_delayed():
    assert "'/stream'" in _JS or '"/stream"' in _JS
    assert "/api/v1/" in _JS


def test_the_wrapper_never_fails_a_call_on_its_own_error():
    """A bug in the hold must not be able to break a request outright."""
    tail = _JS.split("function shouldHold", 1)[1].split("window.fetch =", 1)[0]
    assert "catch (e)" in tail and "return false;" in tail


# ── wiring ───────────────────────────────────────────────────────────────────
def test_it_loads_after_the_dedupe_wrapper():
    """Order matters: auth-hold must be the OUTER wrapper, or a held request
    reaches the dedupe cache and memoises the synthetic 401 for 2.5s — starving
    the real request after unlock."""
    dedupe = _HTML.index("fetch-dedupe.js")
    hold = _HTML.index("auth-hold.js")
    assert dedupe < hold, "auth-hold.js must be loaded after fetch-dedupe.js"


def test_both_lock_screens_settle_the_hold_as_locked():
    for fn in ("showLoginScreen", "showLaunchPinScreen"):
        body = _INIT.split(f"function {fn}(", 1)[1][:600]
        assert "__soulsyncAuthSettled(true)" in body, fn


def test_every_other_path_settles_it_as_unlocked():
    """The finally in initProfileSystem — including the catch path, so a failure
    to determine auth state releases rather than strands."""
    body = _INIT.split("async function initProfileSystem", 1)[1].split("\n}", 1)[0]
    assert "finally" in body and "__soulsyncAuthSettled(false)" in body
