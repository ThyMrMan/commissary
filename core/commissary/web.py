"""Commissary's hook into the Flask app.

``install(app)`` is called once from web_server.py. It runs the fork's one-shot
startup migrations and exposes template context: index.html renders
``commissary_head`` in its <head>: the fork's few CSS rules and a small
``window.COMMISSARY`` object for scripts.
"""

from __future__ import annotations

import json

from markupsafe import Markup


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


# The fork's Import dialogs (commissary-import.js) reuse upstream's Import markup.
_IMPORT_CSS = (
    # Upstream's Import dialog ships a search icon with no size rule, so it
    # renders ~500px tall over the dialog; the fork's dialogs reuse that markup.
    ".vimp-search{position:relative}"
    ".vimp-search-icon{position:absolute;left:31px;top:11px;width:15px;height:15px;"
    "color:var(--white-a40,rgba(255,255,255,.4));pointer-events:none}"
    ".vimp-search-icon+.vimp-search-input{padding-left:34px}"
    # the fork's Import dialogs: their body scrolls inside upstream's card
    ".commissary-import-body{display:flex;flex-direction:column;flex:1;min-height:0;overflow-y:auto}"
)


def head_html() -> Markup:
    info = {"authOpen": auth_open_paths()}
    payload = json.dumps(info).replace("</", "<\\/")
    return Markup('<style id="commissary-style">' + _IMPORT_CSS + "</style>\n"
                  f"<script>window.COMMISSARY = {payload};</script>")


def template_context() -> dict:
    return {"commissary_head": head_html()}


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
