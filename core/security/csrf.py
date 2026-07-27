"""Pure CSRF decision for cookie-authenticated, state-changing requests.

The app authenticates browsers with a session cookie, which the browser attaches
to cross-site requests too — so any page on the internet could POST to a
logged-in user's instance and have it act with their rights. That is the whole
of CSRF, and nothing in the app stopped it.

WHY ORIGIN CHECKING AND NOT TOKENS
Synchroniser tokens would mean threading a value through several hundred hand-
written ``fetch()`` calls spread across dozens of JS files; every one missed is a
broken button, and a missed one fails at runtime rather than at build time.
Origin validation is enforced entirely server-side, needs no frontend change,
and is an accepted defence in its own right (OWASP CSRF Prevention Cheat Sheet,
"Verifying Origin With Standard Headers"). Browsers set ``Origin`` on every
cross-origin request and — critically — on same-origin POSTs too, and it cannot
be forged by page JavaScript.

WHAT IS DELIBERATELY ALLOWED
A request carrying NEITHER Origin NOR Referer is allowed. That is not a hole:
mounting CSRF requires a browser, and a browser always sends Origin on a
cross-site state-changing request (form POST included). No headers at all means
curl / a script / a native app — which has no ambient session cookie to abuse
and no way to be tricked by a web page. Rejecting those would break every
existing integration for no security gain.

WHAT IS EXEMPT
``/api/v1/`` authenticates with an API key in a header, not a cookie, so a
malicious page cannot make an authenticated call to it — CSRF does not apply,
and demanding an Origin there would break legitimate scripted clients. The one
carve-out from the carve-out is ``/api/v1/api-keys-internal``, which IS session-
authed (it is what the Settings page uses), so it stays protected. Same split
the login gate makes, for the same reason.

Host comparison ignores scheme and port on purpose. Behind a TLS-terminating
proxy the browser sends ``Origin: https://example.com`` while the app may see
``http://example.com:8008`` — comparing full origins would reject every request
on the most common deployment, and a scheme/port mismatch is not a CSRF signal
anyway. The registrable host is what distinguishes attacker from app.
"""

from __future__ import annotations

from urllib.parse import urlparse

CONFIG_KEY = "security.csrf_protection"
EXTRA_ORIGINS_KEY = "security.csrf_trusted_origins"

_UNSAFE_METHODS = frozenset({"POST", "PUT", "PATCH", "DELETE"})


def _host_of(value: str) -> str:
    """Hostname of an Origin/Referer/Host value, lowercased, or '' if unusable."""
    raw = (value or "").strip()
    if not raw or raw == "null":        # sandboxed iframe / opaque origin
        return ""
    if "//" not in raw:                 # a bare Host header like 'example.com:8008'
        raw = "//" + raw
    try:
        return (urlparse(raw).hostname or "").strip("[]").lower()
    except Exception:                   # noqa: BLE001 - unparseable is simply not a match
        return ""


def trusted_hosts(request_host: str, extra) -> set:
    """The hostnames a state-changing request may legitimately come from."""
    hosts = {_host_of(request_host)}
    if isinstance(extra, str):
        extra = [p for p in extra.replace(",", " ").split() if p]
    for item in extra or []:
        h = _host_of(str(item))
        if h:
            hosts.add(h)
    hosts.discard("")
    return hosts


def request_is_csrf(path: str, method: str, *, enabled: bool,
                    origin: str = "", referer: str = "",
                    request_host: str = "", extra_origins=None) -> bool:
    """True when this request must be rejected as cross-site.

    Pure: every input is passed in, so the whole policy is testable without a
    Flask context.
    """
    if not enabled:
        return False

    if (method or "GET").upper() not in _UNSAFE_METHODS:
        return False

    path = path or ""
    # Key-authed public API — no cookie, so no CSRF. Its session-authed
    # sub-tree stays guarded (mirrors core/security/login_gate.py).
    if path.startswith("/api/v1/") and not path.startswith("/api/v1/api-keys-internal"):
        return False

    allowed = trusted_hosts(request_host, extra_origins)
    if not allowed:                     # can't identify ourselves → don't invent a verdict
        return False

    # PRESENCE decides which branch, not parseability. A sandboxed iframe posts
    # with `Origin: null`, which has no hostname — treating that as "no header"
    # and falling through to the permissive branch would hand an attacker the
    # bypass for the price of one sandbox attribute. A header that was sent but
    # does not match is a rejection, whatever it contained.
    if (origin or "").strip():
        return _host_of(origin) not in allowed
    if (referer or "").strip():
        return _host_of(referer) not in allowed

    # Neither header sent: a non-browser client. See module docstring.
    return False


__all__ = ["request_is_csrf", "trusted_hosts", "CONFIG_KEY", "EXTRA_ORIGINS_KEY"]
