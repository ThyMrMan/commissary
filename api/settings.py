"""
Settings and API key management endpoints.
"""

from flask import request, current_app, session
from .auth import require_api_key, generate_api_key, _hash_key
from .helpers import api_success, api_error


def _session_is_admin() -> bool:
    """Whether the cookie session belongs to an admin profile.

    Resolved from the DB rather than flask.g: web_server's profile-context hook
    skips /api/v1/ entirely (it's key-authed, not session-authed), so g.is_admin
    is never populated on this blueprint. Fails CLOSED — an unreadable profile
    is not an admin, the opposite of the app-wide default where a missing
    session resolves to profile 1.
    """
    try:
        from database.music_database import get_database
        pid = session.get("profile_id")
        if not pid:
            return False
        profile = get_database().get_profile(pid)
        return bool(profile and profile.get("is_admin"))
    except Exception:
        return False

# Keys that must NEVER be exposed via the API
_SENSITIVE_KEYS = {
    "spotify.client_id",
    "spotify.client_secret",
    "tidal.client_id",
    "tidal.client_secret",
    "tidal_tokens",
    "tidal_download.session",
    "qobuz.session",
    "plex.token",
    "jellyfin.api_key",
    "navidrome.password",
    "soulseek.api_key",
    "listenbrainz.token",
    "acoustid.api_key",
    "lastfm.api_key",
    "genius.access_token",
    "hydrabase.api_key",
}


def register_routes(bp):

    # ---- Settings ----

    @bp.route("/settings", methods=["GET"])
    @require_api_key
    def get_settings():
        """Get current settings (sensitive values redacted)."""
        try:
            cfg = current_app.soulsync["config_manager"]
            raw = dict(cfg.config_data) if hasattr(cfg, "config_data") else {}

            sanitized = _redact_sensitive(raw)
            return api_success({"settings": sanitized})
        except Exception as e:
            return api_error("SETTINGS_ERROR", str(e), 500)

    @bp.route("/settings", methods=["PATCH"])
    @require_api_key
    def update_settings():
        """Update settings (partial).

        Body: {"key": "value", ...}  — dot-notation keys accepted.
        """
        body = request.get_json(silent=True) or {}
        if not body:
            return api_error("BAD_REQUEST", "Empty body.", 400)

        try:
            cfg = current_app.soulsync["config_manager"]
            updated = []
            for key, value in body.items():
                # Block writing API keys through settings endpoint
                if key == "api_keys":
                    continue
                cfg.set(key, value)
                updated.append(key)

            return api_success({"message": "Settings updated.", "updated_keys": updated})
        except Exception as e:
            return api_error("SETTINGS_ERROR", str(e), 500)

    # ---- API Key Management ----

    @bp.route("/api-keys", methods=["GET"])
    @require_api_key
    def list_api_keys():
        """List all API keys (prefix + label only, never the full key)."""
        try:
            cfg = current_app.soulsync["config_manager"]
            keys = cfg.get("api_keys", [])
            return api_success({
                "keys": [
                    {
                        "id": k.get("id"),
                        "label": k.get("label", ""),
                        "key_prefix": k.get("key_prefix", ""),
                        "created_at": k.get("created_at"),
                        "last_used_at": k.get("last_used_at"),
                    }
                    for k in keys
                ]
            })
        except Exception as e:
            return api_error("SETTINGS_ERROR", str(e), 500)

    @bp.route("/api-keys", methods=["POST"])
    @require_api_key
    def create_api_key():
        """Generate a new API key.

        Body: {"label": "My Bot"}
        The raw key is returned ONCE in the response.
        """
        body = request.get_json(silent=True) or {}
        label = body.get("label", "")

        try:
            cfg = current_app.soulsync["config_manager"]
            raw_key, record = generate_api_key(label)
            keys = cfg.get("api_keys", [])
            keys.append(record)
            cfg.set("api_keys", keys)

            return api_success({
                "key": raw_key,
                "id": record["id"],
                "label": record["label"],
                "key_prefix": record["key_prefix"],
                "created_at": record["created_at"],
            }, status=201)
        except Exception as e:
            return api_error("SETTINGS_ERROR", str(e), 500)

    @bp.route("/api-keys/<key_id>", methods=["DELETE"])
    @require_api_key
    def revoke_api_key(key_id):
        """Revoke (delete) an API key by its ID."""
        try:
            cfg = current_app.soulsync["config_manager"]
            keys = cfg.get("api_keys", [])
            original_len = len(keys)
            keys = [k for k in keys if k.get("id") != key_id]

            if len(keys) == original_len:
                return api_error("NOT_FOUND", "API key not found.", 404)

            cfg.set("api_keys", keys)
            return api_success({"message": "API key revoked."})
        except Exception as e:
            return api_error("SETTINGS_ERROR", str(e), 500)

    # ---- Bootstrap endpoint (no auth required) ----

    @bp.route("/api-keys/bootstrap", methods=["POST"])
    def bootstrap_api_key():
        """Generate the first API key when none exist. Authenticated admins only.

        This used to require no auth at all, which made it a login bypass on any
        reachable instance. /api/v1/ is deliberately exempt from the login gate
        (core/security/login_gate.py) because it authenticates with its own keys —
        so a route here that mints a key WITHOUT one let an anonymous caller
        obtain a credential, and PATCH /api/v1/settings could then set
        security.require_login back to false. Two sound decisions, one hole
        between them.

        Requiring login mode is not belt-and-braces, it is the actual gate: with
        login OFF every request resolves to profile 1 and therefore "admin", so
        an admin check alone would still authorise anonymous callers. Same
        reasoning (and same refusal) as the plaintext credential export in
        web_server.export_config_bundle.

        No UI depends on this: the Settings page creates keys through
        /api/v1/api-keys-internal/generate, which is @admin_only and is the one
        /api/v1/ path the login gate does NOT exempt.

        Body: {"label": "My First Key"}
        """
        try:
            cfg = current_app.soulsync["config_manager"]
            if not cfg.get("security.require_login", False):
                return api_error(
                    "LOGIN_REQUIRED",
                    "Minting an API key requires login mode. Enable Settings → "
                    "Security → Require login, or create the key in Settings → "
                    "API keys.", 403)
            if not session.get("login_authenticated", False):
                return api_error("AUTH_REQUIRED", "Sign in first.", 401)
            if not _session_is_admin():
                return api_error("FORBIDDEN", "Admin only.", 403)

            existing = cfg.get("api_keys", [])
            if existing:
                return api_error("FORBIDDEN",
                                 "API keys already exist. Use an authenticated request to create more.", 403)

            body = request.get_json(silent=True) or {}
            label = body.get("label", "Default")

            raw_key, record = generate_api_key(label)
            cfg.set("api_keys", [record])

            return api_success({
                "key": raw_key,
                "id": record["id"],
                "label": record["label"],
                "key_prefix": record["key_prefix"],
                "created_at": record["created_at"],
            }, status=201)
        except Exception as e:
            return api_error("SETTINGS_ERROR", str(e), 500)


def _redact_sensitive(config, prefix=""):
    """Recursively redact sensitive values from a config dict."""
    if not isinstance(config, dict):
        return config

    result = {}
    for key, value in config.items():
        full_key = f"{prefix}.{key}" if prefix else key
        if any(full_key.startswith(s) for s in _SENSITIVE_KEYS):
            result[key] = "***REDACTED***"
        elif isinstance(value, dict):
            result[key] = _redact_sensitive(value, full_key)
        else:
            result[key] = value
    return result
