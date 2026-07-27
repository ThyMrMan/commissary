"""Wiring guards for the video "Manage Workers" modal (video-enrichment-manager.js).

Pins two behaviours the user asked for:
  - clicking a coverage card just SWITCHES THE VIEW — it must not change the global
    "process first" priority or silently re-queue failed items (those are the top
    Movies/Shows/Auto tabs and the explicit "Retry all failed" button);
  - "Retry all failed" re-queues EVERY coverage kind the worker handles, not just
    the tab being viewed.
"""

from __future__ import annotations

import re
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
_JS = (_ROOT / "webui" / "static" / "video" / "video-enrichment-manager.js").read_text(encoding="utf-8")


def _func(name: str) -> str:
    """Return the body of `function name(` up to the next top-level `function `."""
    i = _JS.index("function " + name + "(")
    nxt = _JS.find("\n    function ", i + 1)
    return _JS[i:nxt if nxt != -1 else len(_JS)]


def test_switch_kind_only_switches_view():
    body = _func("switchKind")
    assert "state.kind = kind" in body
    # must NOT reach across to the global priority or auto-retry
    assert "setPriority(" not in body
    assert "requeueFailed(" not in body


def test_priority_changes_only_from_top_tabs():
    # setPriority is still wired — but only to the data-em-priority (top tabs) click.
    assert "function setPriority(" in _JS
    assert "data-em-priority')) setPriority(" in _JS
    # the dead per-coverage requeue helper is gone
    assert "function requeueFailed(" not in _JS


def test_global_retry_all_failed_button_wired():
    # a topbar button that re-queues failed items across ALL workers in one call
    assert "data-em-retry-all-global" in _JS
    assert "function retryAllFailedGlobal(" in _JS
    assert "/api/video/enrichment/retry-all-failed" in _JS
    # it's distinct from the per-worker retry (which targets one worker)
    assert "retryAllFailedGlobal()" in _JS


# ── "process first everywhere" isn't just Movies/Shows/Auto anymore ───────────

def test_global_tabs_are_no_longer_a_fixed_static_list():
    """The three buttons used to be hardcoded HTML in ensureOverlay(); they're
    now built by renderGlobalTabs() from the configured Library list, so the
    static markup must be gone."""
    assert 'data-em-priority="movie">Movies</button>' not in _JS
    assert '<button data-em-priority="movie">Movies</button>' not in _JS
    assert 'id="vem-global-tabs"></div>' in _JS


def test_render_global_tabs_builds_per_library_buttons():
    body = _func("renderGlobalTabs")
    assert "state.libraries" in body
    assert "'movie:' +" in body and "'show:' +" in body
    # only offered when there's more than one Library for that kind — a single
    # -library install shouldn't see a redundant duplicate of the plain tab
    assert "libs.movies.length > 1" in body
    assert "libs.tv.length > 1" in body


def test_libraries_are_loaded_from_the_shared_endpoint():
    assert "function loadLibraries(" in _JS
    body = _func("loadLibraries")
    assert "/api/video/libraries" in body
    assert "renderGlobalTabs()" in body
    # fetched once opening the modal, alongside the existing priority load
    open_body = _func("open")
    assert "loadLibraries()" in open_body


def test_pinned_card_indicator_recognizes_a_library_scoped_priority():
    """A 'show:3' priority should still light up the Shows breakdown card as
    pinned, not just a bare 'show'."""
    body = _func("renderCards")
    assert "indexOf(e + ':')" in body


def test_retry_all_failed_covers_every_kind_of_the_worker():
    body = _func("retryAllFailed")
    # iterate the worker's kinds (movie+show / show / video …), retry each
    assert "workerDef(state.selected)" in body
    assert "w.kinds" in body
    assert re.search(r"kinds\.map\(", body)
    # the button is wired to the multi-kind handler, not the single-kind retry
    assert "data-em-retry-all')) retryAllFailed()" in _JS
