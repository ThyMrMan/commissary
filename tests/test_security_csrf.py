"""Cross-site state-changing requests are refused.

The app authenticates browsers with a session cookie, and browsers attach that
cookie to cross-site requests too — so before this, any page on the internet
could POST to a logged-in user's instance and have it act with their rights,
across all ~543 state-changing routes.

Enforced by Origin/Referer validation rather than synchroniser tokens: tokens
would mean threading a value through several hundred hand-written fetch() calls,
where every miss is a broken button discovered at runtime. See the module
docstring in core/security/csrf.py for the full reasoning.
"""

from __future__ import annotations

import pytest

from core.security.csrf import request_is_csrf, trusted_hosts

APP = "soulsync.example.com"


def _csrf(path="/api/settings", method="POST", **kw):
    kw.setdefault("enabled", True)
    kw.setdefault("request_host", APP)
    return request_is_csrf(path, method, **kw)


# ── the attack it exists to stop ─────────────────────────────────────────────
def test_a_cross_site_post_is_rejected():
    assert _csrf(origin="https://evil.example.net") is True


def test_a_lookalike_host_is_not_good_enough():
    """Substring matching would be the classic way to get this wrong."""
    for bad in ("https://soulsync.example.com.evil.net",
                "https://notsoulsync.example.com",
                "https://evil.net/?soulsync.example.com"):
        assert _csrf(origin=bad) is True, bad


def test_a_sandboxed_iframe_cannot_launder_its_origin():
    """`<iframe sandbox>` posts with `Origin: null`. If a present-but-unparseable
    Origin were treated as 'no header sent', the permissive non-browser branch
    would hand over the bypass for one HTML attribute."""
    assert _csrf(origin="null") is True
    # A whitespace-only header is not something any browser emits; it counts as
    # absent and falls through to the non-browser branch.
    assert _csrf(origin="   ") is False


def test_a_present_but_junk_origin_is_rejected_not_waved_through():
    for junk in ("null", "://", "http://", "%%%"):
        assert _csrf(origin=junk) is True, junk


def test_a_cross_site_referer_is_rejected_when_origin_is_absent():
    assert _csrf(referer="https://evil.example.net/page") is True


# ── what must keep working ───────────────────────────────────────────────────
def test_the_apps_own_requests_pass():
    assert _csrf(origin=f"https://{APP}") is False
    assert _csrf(referer=f"https://{APP}/video-watchlist") is False


def test_scheme_and_port_mismatches_still_pass():
    """Behind a TLS-terminating proxy the browser sends https://host while the
    app sees http://host:8008. Comparing full origins would reject every request
    on the most common deployment — and a scheme/port difference is not a CSRF
    signal."""
    assert request_is_csrf("/api/settings", "POST", enabled=True,
                           request_host=f"{APP}:8008", origin=f"https://{APP}") is False
    assert request_is_csrf("/api/settings", "POST", enabled=True,
                           request_host=APP, origin=f"http://{APP}:8443") is False


def test_reads_are_never_blocked():
    for m in ("GET", "HEAD", "OPTIONS"):
        assert _csrf(method=m, origin="https://evil.example.net") is False, m


def test_a_client_that_sends_no_headers_is_allowed():
    """CSRF needs a browser, and browsers always send Origin on a cross-site
    state-changing request. No headers at all means curl/script/native app —
    which has no ambient cookie to abuse. Rejecting those breaks every existing
    integration for no gain."""
    assert _csrf() is False
    assert _csrf(origin="", referer="") is False


def test_localhost_and_lan_installs_work():
    for host in ("localhost:8008", "192.168.1.20:8008", "soulsync"):
        assert request_is_csrf("/api/settings", "POST", enabled=True,
                               request_host=host, origin=f"http://{host}") is False, host


def test_extra_trusted_origins_are_honoured():
    """An operator fronting the app under a second hostname needs a way in."""
    for extra in (["https://media.example.org"], "https://media.example.org",
                  "https://media.example.org, https://other.example.org"):
        assert request_is_csrf("/api/settings", "POST", enabled=True, request_host=APP,
                               origin="https://media.example.org", extra_origins=extra) is False
    # and it does not become a wildcard
    assert request_is_csrf("/api/settings", "POST", enabled=True, request_host=APP,
                           origin="https://evil.example.net",
                           extra_origins=["https://media.example.org"]) is True


# ── the exemption, and its carve-out ─────────────────────────────────────────
def test_the_key_authed_api_is_exempt():
    """/api/v1/ authenticates with a header key, not a cookie, so a malicious
    page cannot make an authenticated call to it — and demanding an Origin would
    break legitimate scripted clients."""
    assert _csrf(path="/api/v1/settings", origin="https://evil.example.net") is False
    assert _csrf(path="/api/v1/library/artists", origin="null") is False


def test_but_the_session_authed_part_of_it_is_not():
    """api-keys-internal is what the Settings PAGE uses — cookie-authed, and so
    CSRF-able. Same split core/security/login_gate.py makes."""
    assert _csrf(path="/api/v1/api-keys-internal/generate",
                 origin="https://evil.example.net") is True
    assert _csrf(path="/api/v1/api-keys-internal/revoke/x", method="DELETE",
                 origin="https://evil.example.net") is True


def test_every_unsafe_method_is_covered():
    for m in ("POST", "PUT", "PATCH", "DELETE"):
        assert _csrf(method=m, origin="https://evil.example.net") is True, m


def test_disabled_is_a_strict_no_op():
    assert request_is_csrf("/api/settings", "POST", enabled=False,
                           request_host=APP, origin="https://evil.example.net") is False


def test_an_unidentifiable_host_does_not_invent_a_verdict():
    """If we cannot tell what we are, we cannot say what is foreign."""
    assert request_is_csrf("/api/settings", "POST", enabled=True,
                           request_host="", origin="https://evil.example.net") is False


def test_trusted_hosts_normalises():
    h = trusted_hosts("Example.COM:8008", ["HTTPS://Other.Example.org/path"])
    assert h == {"example.com", "other.example.org"}
    assert "" not in trusted_hosts("", [None, "", "   "])


# ── wired into the app ───────────────────────────────────────────────────────
@pytest.fixture()
def client():
    import web_server
    web_server.app.config["TESTING"] = True
    with web_server.app.test_client() as c:
        yield c


def test_the_app_rejects_a_real_cross_site_post(client):
    r = client.post("/api/settings", json={"x": 1},
                    headers={"Origin": "https://evil.example.net"})
    assert r.status_code == 403
    assert r.get_json()["error"] == "cross_site_request_blocked"


def test_the_app_allows_its_own_post(client):
    """localhost is the test client's host — the same-origin path must not 403."""
    r = client.post("/api/settings", json={}, headers={"Origin": "http://localhost"})
    assert r.status_code != 403


def test_the_gate_runs_before_the_login_gate(client):
    """A cross-site probe should learn 'cross-site', not whether login is on."""
    import web_server
    cm = web_server.app.soulsync["config_manager"]
    prev = cm.get("security.require_login", False)
    try:
        cm.set("security.require_login", True)
        r = client.post("/api/settings", json={},
                        headers={"Origin": "https://evil.example.net"})
        assert r.status_code == 403
        assert r.get_json()["error"] == "cross_site_request_blocked"
    finally:
        cm.set("security.require_login", prev)
