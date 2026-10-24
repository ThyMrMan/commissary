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


# Layout for the video Libraries editor (commissary-video-libraries.js).
_VLIB_CSS = (
    # Editor: upstream's own pieces (.settings-subheading, .form-group,
    # .setting-help-text, .test-button); each library is a card on the settings
    # surface, the way upstream draws its boxed lists.
    ".commissary-vlib{margin-top:8px}"
    ".commissary-vlib-row{margin:10px 0;padding:14px 16px;background:var(--stg-surface,var(--white-a03));"
    "border:1px solid var(--white-a09);border-radius:var(--radius-lg)}"
    ".commissary-vlib-head{display:flex;flex-wrap:wrap;gap:10px;align-items:center;"
    "justify-content:space-between;margin-bottom:12px}"
    ".commissary-vlib-title{font-size:13px;font-weight:600;color:var(--white-a85,#fff)}"
    ".commissary-vlib-actions{display:flex;flex-wrap:wrap;gap:8px;margin:0}"
    # Fields: one upstream .form-group row per setting (label left, control
    # right), exactly like the rest of this settings section.
    ".commissary-vlib-grid{display:block}"
    ".commissary-vlib-save{display:flex;flex-wrap:wrap;gap:12px;align-items:center}"
    # Library picker buttons (commissary-video-library-pickers.js) sit in the
    # next slot along from upstream's own card buttons and take their styles.
    # Bottom-right: the top row (status pill + three buttons) has no room for a
    # fourth on a narrow card without covering the pill.
    # Watchlist: looks like upstream's .vwl-btn (its row neighbour) but must not
    # BE one, since upstream toggles the watchlist on any .vwl-btn click.
    ".commissary-vlib-wbtn{width:30px;height:30px;border-radius:9px;display:flex;align-items:center;"
    "justify-content:center;background:rgba(0,0,0,.55);backdrop-filter:blur(8px);"
    "border:1px solid var(--white-a14);color:rgba(255,255,255,.82);cursor:pointer;opacity:0;"
    "transform:translateY(-2px);transition:opacity .18s ease,transform .18s ease,background .18s ease,"
    "color .18s ease,border-color .18s ease}"
    ".commissary-vlib-wbtn svg{width:15px;height:15px}"
    ".commissary-vlib-wbtn:hover{background:rgba(0,0,0,.78);color:#fff;border-color:var(--white-a30)}"
    ".vwlp-card:hover .commissary-vlib-wbtn,.commissary-vlib-wbtn.commissary-vlib-btn--set"
    "{opacity:1;transform:none}"
    "@media (hover:none){.commissary-vlib-wbtn{opacity:1;transform:none}}"
    ".commissary-vlib-btn--set{color:rgb(var(--accent-rgb))}"
)

# {Token} naming on the Library settings card (commissary-naming-settings.js):
# the old fork's token chips, and inline text buttons inside a settings-hint.
_NAMING_CSS = (
    ".commissary-link-btn{background:none;border:0;padding:0;font:inherit;cursor:pointer;"
    "color:rgb(var(--accent-rgb));text-decoration:underline;text-underline-offset:2px}"
    ".commissary-link-btn:hover{filter:brightness(1.2)}"
    ".commissary-token-list{display:flex;flex-wrap:wrap;gap:6px;margin:8px 0 4px;padding:10px;"
    "border:1px solid var(--white-a12,rgba(255,255,255,.12));border-radius:8px}"
    # display:flex above would otherwise beat the hidden attribute
    ".commissary-token-list[hidden]{display:none}"
    # Upstream's Import dialog ships a search icon with no size rule, so it
    # renders ~500px tall over the dialog; the fork's dialogs reuse that markup.
    ".vimp-search{position:relative}"
    ".vimp-search-icon{position:absolute;left:31px;top:11px;width:15px;height:15px;"
    "color:var(--white-a40,rgba(255,255,255,.4));pointer-events:none}"
    ".vimp-search-icon+.vimp-search-input{padding-left:34px}"
    # the fork's Import dialogs: their body scrolls inside upstream's card
    ".commissary-import-body{display:flex;flex-direction:column;flex:1;min-height:0;overflow-y:auto}"
    ".commissary-token{padding:2px 7px;border-radius:5px;background:var(--white-a08,rgba(255,255,255,.08));"
    "border:1px solid transparent;font-size:12px;white-space:nowrap;cursor:pointer;color:inherit;"
    "font-family:inherit}"
    ".commissary-token:hover{border-color:rgb(var(--accent-rgb));color:rgb(var(--accent-rgb))}"
)


def head_html(config_get=None) -> Markup:
    rules = [_VLIB_CSS, _NAMING_CSS]
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
