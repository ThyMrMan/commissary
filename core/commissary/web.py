"""Commissary's hook into the Flask app.

``install(app)`` is called once from web_server.py. index.html renders
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


def head_html(config_get=None) -> Markup:
    rules = []
    if not features.chat_enabled(config_get):
        rules.append(_HIDE_CHAT)
    if not features.support_button_enabled(config_get):
        rules.append(_HIDE_SUPPORT)
    info = {"chatEnabled": features.chat_enabled(config_get)}
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
