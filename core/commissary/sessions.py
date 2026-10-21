"""Signing in survives closing the browser.

Upstream never sets a session lifetime, so Flask issues a browser-session
cookie with no Expires and the browser discards it on close: every restart of
the browser means signing in (or redoing the Plex link) again.

Sessions are made permanent with a sliding window (``security.session_days``,
default 30, clamped 1-365). Flask re-sends a permanent session's cookie on
each request, so someone who keeps using the app stays signed in and one who
stops is signed out after the window.

Secure/SameSite are NOT set here: upstream's reverse-proxy mode owns them and
marks the cookie Secure only behind TLS. Forcing Secure on a plain-http LAN
install would stop the browser sending the cookie at all. Logout is upstream's:
it already drops the login, profile, device and launch-PIN keys, and the device
list / session epoch handle "sign out everywhere".
"""

from __future__ import annotations

from datetime import timedelta

CONFIG_KEY = "security.session_days"
DEFAULT_DAYS = 30


def session_days(config_get) -> int:
    try:
        return max(1, min(365, int(config_get(CONFIG_KEY, DEFAULT_DAYS))))
    except Exception:  # noqa: BLE001 - a bad setting must not break sessions
        return DEFAULT_DAYS


def install(app, config_get) -> None:
    from flask import session

    app.permanent_session_lifetime = timedelta(days=session_days(config_get))

    @app.before_request
    def _commissary_persistent_session():
        # Guarded: assigning on every request would mark the session dirty and
        # re-serialise it for every static asset too.
        if not session.permanent:
            session.permanent = True
