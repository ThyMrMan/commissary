"""Commissary's hook into the Flask app.

``install(app)`` is called once from web_server.py. It runs the fork's one-shot
startup migrations and exposes template context: index.html renders
``commissary_head`` in its <head>: CSS that hides switched-off upstream
features, and a small ``window.COMMISSARY`` object for scripts.
"""

from __future__ import annotations

import json

from markupsafe import Markup

from core.commissary import features

# Hidden rather than removed: upstream markup stays byte-identical, so merges
# never conflict on it. Selectors are upstream's own.
_HIDE_CHAT = ('.nav-button[data-page="chat"], .nav-button[data-video-page="video-chat"], '
              '#chat-page { display: none !important; }')
_HIDE_SUPPORT = '.support-section, #support-modal-overlay { display: none !important; }'


def auth_open_paths() -> dict:
    """Endpoints the sign-in, launch-PIN and profile-picker screens reach before
    authentication: the union of upstream's own gate lists, so
    commissary-auth-hold.js never holds one (holding /api/profiles/current
    would deadlock; holding a sign-in call would break the lock screen)."""
    from core.security import launch_lock, login_gate, session_profile

    gates = (login_gate, launch_lock, session_profile)
    return {
        "get": sorted(set().union(*(g._ALLOWED_GET for g in gates))),
        "post": sorted(set().union(*(g._ALLOWED_POST for g in gates))),
    }


def head_html(config_get=None) -> Markup:
    rules = []
    if not features.chat_enabled(config_get):
        rules.append(_HIDE_CHAT)
    if not features.support_button_enabled(config_get):
        rules.append(_HIDE_SUPPORT)
    info = {"chatEnabled": features.chat_enabled(config_get), "authOpen": auth_open_paths()}
    payload = json.dumps(info).replace("</", "<\\/")
    parts = []
    if rules:
        parts.append('<style id="commissary-style">' + " ".join(rules) + "</style>")
    parts.append(f"<script>window.COMMISSARY = {payload};</script>")
    return Markup("\n".join(parts))


def template_context(config_get=None) -> dict:
    return {"commissary_head": head_html(config_get)}


def install(app) -> None:
    app.context_processor(template_context)
    _run_startup_migrations()


def _run_startup_migrations() -> None:
    """One-shot, idempotent data fixes. Never fatal."""
    try:
        from core.commissary.video_secrets import migrate
        from database.video_database import VideoDatabase
        migrate(VideoDatabase())
    except Exception:  # noqa: BLE001
        from utils.logging_config import get_logger
        get_logger("commissary.web").debug("video secret migration failed", exc_info=True)
