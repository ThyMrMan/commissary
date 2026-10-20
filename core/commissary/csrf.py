"""Reject cross-site, cookie-authenticated, state-changing requests (CSRF).

Browsers attach the session cookie to cross-site requests too, so without a
check any web page could POST to a logged-in user's instance and act with
their rights. Upstream has no CSRF defence (its CORS module only serves
API-key callers like the Companion extension and grants nothing by itself).

WHY ORIGIN CHECKING, NOT TOKENS
Tokens would mean threading a value through hundreds of hand-written fetch()
calls. Origin validation is enforced server-side, needs no frontend change, and
is an accepted defence (OWASP CSRF cheat sheet, "Verifying Origin With
Standard Headers"). Browsers send ``Origin`` on cross-origin requests and on
same-origin POSTs, and page JavaScript cannot forge it.

ALLOWED ON PURPOSE
- Safe methods (GET/HEAD/OPTIONS).
- No Origin AND no Referer: not a browser (curl, scripts, native apps), which
  has no ambient cookie to abuse.
- ``/api/v1/`` except ``/api/v1/api-keys-internal``: key-authed, not cookie.
- Any request carrying a valid API key: the key, not a cookie, authenticates it
  (the Companion extension posts cross-origin this way). The hook asks for this
  only when the request would otherwise be rejected.
- ``/socket.io/``: engine.io applies its own allowed-origins check.

Host comparison ignores scheme and port: behind a TLS-terminating proxy the
browser says ``https://host`` while the app may see ``http://host:8008``. In
reverse-proxy mode upstream's ProxyFix makes ``request.host`` the forwarded
host. Anything else (a proxy that rewrites Host) goes in
``security.csrf_trusted_origins``.

``security.csrf_protection`` (default on) turns the whole check off.
"""

from __future__ import annotations

from urllib.parse import urlparse

CONFIG_KEY = "security.csrf_protection"
EXTRA_ORIGINS_KEY = "security.csrf_trusted_origins"

_UNSAFE_METHODS = frozenset({"POST", "PUT", "PATCH", "DELETE"})


def _host_of(value: str) -> str:
    """Lowercased hostname of an Origin/Referer/Host value, or ''."""
    raw = (value or "").strip()
    if not raw or raw == "null":        # sandboxed iframe / opaque origin
        return ""
    if "//" not in raw:                 # a bare Host header like 'example.com:8008'
        raw = "//" + raw
    try:
        return (urlparse(raw).hostname or "").strip("[]").lower()
    except Exception:                   # noqa: BLE001 - unparseable is not a match
        return ""


def trusted_hosts(request_host: str, extra) -> set:
    hosts = {_host_of(request_host)}
    if isinstance(extra, str):
        extra = [p for p in extra.replace(",", " ").split() if p]
    for item in extra or []:
        host = _host_of(str(item))
        if host:
            hosts.add(host)
    hosts.discard("")
    return hosts


def request_is_csrf(path: str, method: str, *, enabled: bool,
                    origin: str = "", referer: str = "",
                    request_host: str = "", extra_origins=None) -> bool:
    """True when this request must be rejected as cross-site. Pure."""
    if not enabled:
        return False
    if (method or "GET").upper() not in _UNSAFE_METHODS:
        return False
    path = path or ""
    if path.startswith("/api/v1/") and not path.startswith("/api/v1/api-keys-internal"):
        return False
    if path.startswith("/socket.io/"):
        return False

    allowed = trusted_hosts(request_host, extra_origins)
    if not allowed:                     # can't identify ourselves: no verdict
        return False

    # PRESENCE picks the branch, not parseability: a sandboxed iframe posts
    # `Origin: null`, and treating that as "no header" would hand an attacker
    # the permissive branch for one sandbox attribute.
    if (origin or "").strip():
        return _host_of(origin) not in allowed
    if (referer or "").strip():
        return _host_of(referer) not in allowed
    return False


def make_guard(config_get, has_valid_api_key, logger):
    """A Flask before_request hook enforcing the policy.

    ``has_valid_api_key`` is called only for a request about to be rejected.
    Never raises: a bug here must not take the app offline.
    """
    from flask import jsonify, request

    def _enforce_csrf():
        try:
            try:
                enabled = bool(config_get(CONFIG_KEY, True))
            except Exception:  # noqa: BLE001
                enabled = True
            try:
                extra = config_get(EXTRA_ORIGINS_KEY, []) or []
            except Exception:  # noqa: BLE001
                extra = []
            if not request_is_csrf(
                request.path, request.method, enabled=enabled,
                origin=request.headers.get("Origin", ""),
                referer=request.headers.get("Referer", ""),
                request_host=request.host or "",
                extra_origins=extra,
            ):
                return None
            if has_valid_api_key():
                return None
            logger.warning("[Security] CSRF: rejected %s %s from origin=%r referer=%r",
                           request.method, request.path,
                           request.headers.get("Origin", ""), request.headers.get("Referer", ""))
            return jsonify({"error": "cross_site_request_blocked",
                            "message": "This request appears to come from another site."}), 403
        except Exception:  # noqa: BLE001
            logger.debug("CSRF check skipped", exc_info=True)
            return None

    return _enforce_csrf
