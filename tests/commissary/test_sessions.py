"""Signing in survives closing the browser.

A non-permanent Flask session sends a cookie with no Expires, which the browser
discards on close, so every browser restart meant signing in (or redoing the
Plex link) again.
"""

from datetime import timedelta

import pytest

from core.commissary.sessions import DEFAULT_DAYS, session_days


@pytest.fixture()
def client():
    import web_server
    web_server.app.config["TESTING"] = True
    return web_server.app.test_client()


def test_the_cookie_actually_carries_an_expiry(client):
    """Asserted on the wire, not on config."""
    resp = client.get("/status")
    cookie = resp.headers.get("Set-Cookie", "")
    assert "soulsync_session=" in cookie, cookie
    assert "Expires=" in cookie or "Max-Age=" in cookie, cookie


def test_the_request_marks_the_session_permanent(client):
    client.get("/status")
    with client.session_transaction() as sess:
        assert sess.permanent is True


def test_the_lifetime_is_the_configured_window():
    import web_server
    assert web_server.app.permanent_session_lifetime == timedelta(days=DEFAULT_DAYS)


def test_the_lifetime_is_configurable_and_clamped():
    def cfg(value):
        return lambda key, default=None: value

    assert session_days(cfg(7)) == 7
    assert session_days(cfg(0)) == 1
    assert session_days(cfg(9999)) == 365
    assert session_days(cfg("nonsense")) == DEFAULT_DAYS


def test_secure_is_not_forced_on_a_plain_http_install(client):
    """Secure on plain-http LAN would stop the browser sending the cookie at
    all; upstream's reverse-proxy mode owns that flag."""
    cookie = client.get("/status").headers.get("Set-Cookie", "")
    assert "Secure" not in cookie
