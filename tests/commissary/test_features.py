"""Chat and the donation button are switched off by default, not deleted."""

from core.commissary import features
from core.commissary.web import head_html


def _cfg(values):
    return lambda key, default=None: values.get(key, default)


def test_chat_and_support_are_off_by_default():
    assert features.chat_enabled(_cfg({})) is False
    assert features.support_button_enabled(_cfg({})) is False


def test_off_features_are_hidden_with_upstreams_own_selectors():
    html = str(head_html(_cfg({})))
    for selector in ('.nav-button[data-page="chat"]',
                     '.nav-button[data-video-page="video-chat"]',
                     '#chat-page', '.support-section', '#support-modal-overlay'):
        assert selector in html


def test_the_selectors_still_exist_in_upstream_markup():
    """If upstream renames one of these, the hide rule silently stops working."""
    from pathlib import Path

    index = (Path(__file__).resolve().parents[2] / "webui" / "index.html").read_text(encoding="utf-8")
    for needle in ('data-page="chat"', 'data-video-page="video-chat"', 'id="chat-page"',
                   'class="support-section"', 'id="support-modal-overlay"'):
        assert needle in index


def test_turning_features_on_removes_the_hide_rules():
    html = str(head_html(_cfg({"commissary.chat_enabled": True,
                               "commissary.support_button": True})))
    assert "#chat-page" not in html and ".support-section" not in html
    assert '"chatEnabled": true' in html


def test_chat_api_is_not_registered_by_default():
    import web_server

    rules = {r.rule for r in web_server.app.url_map.iter_rules()}
    assert not any(rule.startswith("/api/chat") for rule in rules)
