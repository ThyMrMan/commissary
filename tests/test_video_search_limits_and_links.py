"""Manual search: how many results survive, and where a release came from.

Three separate caps used to sit between Prowlarr and the picker, all silent:

  prowlarr_search   limit=100 per call, hard-coded
  _evaluate_hits    accepted[:40] + rejected[:15]   <- the one people actually hit
  the renderer      no cap at all

So "only N torrents show up" was the API discarding rows the ranker had already
scored. Both are configurable now, with higher defaults.

The release title also links to the indexer's page for it. That URL comes from a
third party and is rendered as a clickable link, so its scheme is validated at
the boundary where it enters — a javascript: URL there would execute in the page.
"""

from __future__ import annotations

import pytest


# ── the caps ─────────────────────────────────────────────────────────────────
def _hits(n, accepted=True):
    return [{"title": "R%d" % i, "size_bytes": 1, "seeders": i, "accepted": accepted}
            for i in range(n)]


def test_the_result_caps_are_configurable(monkeypatch):
    from api.video import downloads

    assert downloads._max_accepted() == downloads._DEF_MAX_ACCEPTED
    assert downloads._max_rejected() == downloads._DEF_MAX_REJECTED

    class _Cfg:
        @staticmethod
        def get(key, default=None):
            return {"video.search.max_results": 250, "video.search.max_rejected": 7}.get(key, default)

    import config.settings as settings
    monkeypatch.setattr(settings, "config_manager", _Cfg())
    assert downloads._max_accepted() == 250
    assert downloads._max_rejected() == 7


def test_the_caps_are_bounded_rather_than_trusted(monkeypatch):
    """A huge value would render thousands of cards; a zero would return nothing
    and look exactly like 'no releases found'."""
    from api.video import downloads
    import config.settings as settings

    class _Cfg:
        def __init__(self, v): self.v = v
        def get(self, key, default=None): return self.v

    monkeypatch.setattr(settings, "config_manager", _Cfg(999999))
    assert downloads._max_accepted() == 500
    monkeypatch.setattr(settings, "config_manager", _Cfg(0))
    assert downloads._max_accepted() == downloads._DEF_MAX_ACCEPTED   # 0 → falsy → default
    monkeypatch.setattr(settings, "config_manager", _Cfg(-5))
    assert downloads._max_accepted() == 1


def test_a_broken_config_never_breaks_a_search(monkeypatch):
    from api.video import downloads
    import config.settings as settings

    class _Boom:
        @staticmethod
        def get(*a, **k):
            raise RuntimeError("config unavailable")

    monkeypatch.setattr(settings, "config_manager", _Boom())
    assert downloads._max_accepted() == downloads._DEF_MAX_ACCEPTED
    assert downloads._max_rejected() == downloads._DEF_MAX_REJECTED


def test_the_prowlarr_per_call_limit_is_configurable(monkeypatch):
    from core.video import prowlarr_search as ps
    import config.settings as settings

    assert ps._search_limit() == ps._DEFAULT_SEARCH_LIMIT

    class _Cfg:
        def __init__(self, v): self.v = v
        def get(self, key, default=None): return self.v

    monkeypatch.setattr(settings, "config_manager", _Cfg(400))
    assert ps._search_limit() == 400
    monkeypatch.setattr(settings, "config_manager", _Cfg(5))
    assert ps._search_limit() == 20          # too small starves the ranker
    monkeypatch.setattr(settings, "config_manager", _Cfg(99999))
    assert ps._search_limit() == 1000


def test_the_default_is_higher_than_the_old_hard_coded_one():
    """The reported symptom. Pin it so a future tidy-up can't quietly restore 40."""
    from api.video import downloads
    from core.video import prowlarr_search as ps
    assert downloads._DEF_MAX_ACCEPTED > 40
    assert ps._DEFAULT_SEARCH_LIMIT > 100


# ── the source link ──────────────────────────────────────────────────────────
@pytest.mark.parametrize("url", [
    "https://tracker.example/details/1", "http://tracker.example/t/2",
])
def test_a_normal_details_url_is_kept(url):
    from core.video.prowlarr_search import _safe_info_url
    assert _safe_info_url(url) == url


@pytest.mark.parametrize("url", [
    "javascript:alert(document.cookie)",
    "JavaScript:alert(1)",
    "data:text/html,<script>alert(1)</script>",
    "vbscript:msgbox(1)",
    "file:///etc/passwd",
    "  ", "", None,
])
def test_anything_that_is_not_http_is_dropped(url):
    """This string is rendered as a link the user clicks, and it comes from a
    third-party indexer. A javascript: URL there executes in the page."""
    from core.video.prowlarr_search import _safe_info_url
    assert _safe_info_url(url) is None


def test_the_link_is_carried_on_the_projected_hit():
    from core.video.prowlarr_search import _project

    class _R:
        title, size, seeders, leechers, grabs = "Some.Release", 100, 5, 1, 0
        indexer_name, indexer_id, protocol, guid = "Tracker", 3, "torrent", "g1"
        info_url = "https://tracker.example/details/1"
        publish_date = "2026-01-01"

    hit = _project(_R(), "magnet:?xt=1", "torrent")
    assert hit["info_url"] == "https://tracker.example/details/1"
    assert hit["publish_date"] == "2026-01-01"


def test_a_hostile_link_never_reaches_the_hit():
    from core.video.prowlarr_search import _project

    class _R:
        title, size, seeders, leechers, grabs = "R", 1, 1, 0, 0
        indexer_name, indexer_id, protocol, guid = "T", 1, "torrent", "g"
        info_url = "javascript:alert(1)"
        publish_date = None

    assert _project(_R(), "magnet:?xt=1", "torrent")["info_url"] is None


# ── the frontend (no JS runner in this repo) ─────────────────────────────────
def _js(rel="webui/static/video/video-download-view.js"):
    import pathlib
    return (pathlib.Path(__file__).resolve().parents[1] / rel).read_text(encoding="utf-8")


def test_the_title_links_out_safely():
    js = _js()
    assert "vdl-r-title--link" in js
    # Third-party sites must not get window.opener or a referrer leaking the
    # SoulSync URL.
    assert 'rel="noopener noreferrer"' in js
    assert 'target="_blank"' in js


def test_a_release_with_no_details_page_still_renders():
    """Not every indexer supplies infoUrl; those rows must stay plain text."""
    js = _js()
    assert "r.info_url" in js and "vdl-r-title\" title=" in js


def test_filtering_does_not_renumber_the_cards():
    """The Grab button indexes into the ORIGINAL row array. Re-indexing the
    filtered list would make it grab the wrong release."""
    js = _js()
    assert "rows.indexOf(r)" in js


def test_filters_are_applied_at_render_not_stored():
    """A live search re-renders every couple of seconds. Filtering the stored
    rows would discard results the user never asked to hide."""
    js = _js()
    assert "resultsEl._rows = rows" in js
    assert "applyFilter(rows, f)" in js


def test_every_filter_control_is_read_back():
    js = _js()
    for attr in ("data-vdl-f-text", "data-vdl-f-res", "data-vdl-f-indexer",
                 "data-vdl-f-seed", "data-vdl-f-ok", "data-vdl-f-clear"):
        assert js.count(attr) >= 2, attr      # rendered AND handled


def test_typing_in_the_filter_survives_the_re_render():
    js = _js()
    assert "setSelectionRange" in js and "document.activeElement" in js


def test_the_hidden_count_is_surfaced():
    """Silently showing fewer rows is exactly the confusion this set out to fix."""
    assert "hidden by filters" in _js()
