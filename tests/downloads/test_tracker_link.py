"""Which tracker served a result, and a link to its page.

Prowlarr aggregates many indexers behind the single "torrent" source, so
"torrent" does not answer the question you actually have when choosing between
two releases: which tracker is this, and can I go look at it. Prowlarr already
returned both ``indexer_name`` and ``info_url`` — the music side simply dropped
the URL on the floor.

The security property is the reason most of this file exists: ``info_url``
comes from a third party and is rendered as an ``href``. A ``javascript:`` URL
there would execute in the page.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from core.prowlarr_client import safe_info_url

_ROOT = Path(__file__).resolve().parents[2]
_DOWNLOADS = (_ROOT / "webui" / "static" / "downloads.js").read_text(encoding="utf-8")


# ── the scheme check ─────────────────────────────────────────────────────────
@pytest.mark.parametrize("url", [
    "https://tracker.example/torrent/1",
    "http://tracker.example/torrent/1",
])
def test_http_urls_pass(url):
    assert safe_info_url(url) == url


@pytest.mark.parametrize("url", [
    "javascript:alert(1)",
    "JavaScript:alert(1)",
    "data:text/html,<script>alert(1)</script>",
    "vbscript:msgbox(1)",
    "file:///etc/passwd",
])
def test_dangerous_schemes_are_refused(url):
    """These would execute or exfiltrate if they reached an href."""
    assert safe_info_url(url) is None


@pytest.mark.parametrize("value", ["", "   ", None, 0])
def test_empty_values_are_none(value):
    assert safe_info_url(value) is None


def test_the_video_side_uses_the_same_check():
    """It was promoted out of the video module precisely so the two cannot
    drift — a second copy is how one of them ends up missing a scheme."""
    from core.video.prowlarr_search import _safe_info_url
    assert _safe_info_url is safe_info_url


# ── the plugins carry it ─────────────────────────────────────────────────────
@pytest.mark.parametrize("plugin", ["torrent", "usenet"])
def test_the_plugin_records_a_checked_info_url(plugin):
    src = (_ROOT / "core" / "download_plugins" / f"{plugin}.py").read_text(encoding="utf-8")
    assert "'info_url': safe_info_url(result.info_url)" in src, plugin


# ── the API forwards it ──────────────────────────────────────────────────────
def test_both_serializers_forward_the_tracker():
    """Album picker and track picker must agree on the field names, or the one
    shared renderer works in only one of them."""
    src = (_ROOT / "web_server.py").read_text(encoding="utf-8")
    assert "entry['info_url'] = meta.get('info_url')" in src        # album picker
    assert "def _candidate_indexer_fields" in src                    # track picker
    assert "**_candidate_indexer_fields(c)" in src


def test_a_source_without_metadata_contributes_no_keys():
    """Soulseek has no indexer; the picker should see the field absent rather
    than present-and-null, which reads as "a tracker with no name"."""
    import web_server

    class _Bare:
        pass

    assert web_server._candidate_indexer_fields(_Bare()) == {}


def test_only_populated_fields_are_forwarded():
    import web_server

    class _C:
        _source_metadata = {"indexer": "Redacted", "info_url": None, "seeders": 5}

    assert web_server._candidate_indexer_fields(_C()) == {"indexer": "Redacted"}


# ── the renderer ─────────────────────────────────────────────────────────────
def test_the_link_opens_detached_and_untrusted():
    """`target=_blank` without `rel=noopener` hands the opened page a handle on
    ours, and clicking mid-download must not navigate the app away."""
    block = _DOWNLOADS.split("function _candidateTrackerHtml", 1)[1][:900]
    assert 'target="_blank"' in block
    assert 'rel="noopener noreferrer"' in block


def test_the_url_and_name_are_escaped():
    block = _DOWNLOADS.split("function _candidateTrackerHtml", 1)[1][:900]
    assert "escapeHtml(c.info_url)" in block
    assert "escapeHtml(c.indexer)" in block


def test_no_indexer_falls_back_to_the_username():
    """Soulseek rows keep showing the peer they came from."""
    block = _DOWNLOADS.split("function _candidateTrackerHtml", 1)[1][:900]
    assert "if (!c || !c.indexer) return escapeHtml(fallback || '-')" in block


def test_a_tracker_without_a_details_page_renders_as_plain_text():
    """Not every indexer exposes one; the name is still worth showing."""
    block = _DOWNLOADS.split("function _candidateTrackerHtml", 1)[1][:900]
    assert "if (!c.info_url) return name;" in block


def test_both_pickers_use_the_one_renderer():
    assert "_candidateTrackerHtml(c, c.username)" in _DOWNLOADS   # track rows
    assert "_candidateTrackerHtml(c, null)" in _DOWNLOADS         # album rows


def test_the_link_is_styled():
    css = (_ROOT / "webui" / "static" / "style.css").read_text(encoding="utf-8")
    assert ".candidates-tracker-link" in css
