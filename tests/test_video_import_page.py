"""Video Import page — frontend wiring (string-contract, like the other video page
tests). Pins the page module, its container, nav registration, and the endpoints it
calls so a refactor can't quietly unhook the manual-import flow. The placement LOGIC
itself is covered by tests/test_video_importer.py + tests/test_video_manual_import.py.
"""

from __future__ import annotations

from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
_JS = (_ROOT / "webui" / "static" / "video" / "video-import.js").read_text(encoding="utf-8")
_SIDE = (_ROOT / "webui" / "static" / "video" / "video-side.js").read_text(encoding="utf-8")
_INDEX = (_ROOT / "webui" / "index.html").read_text(encoding="utf-8")
_CSS = (_ROOT / "webui" / "static" / "video" / "video-side.css").read_text(encoding="utf-8")


def test_module_is_an_isolated_iife():
    s = _JS.strip()
    assert s.startswith("/*") or s.startswith("(function")
    assert "(function" in _JS and "})();" in _JS
    # isolated: wrapped in an IIFE and never ASSIGNS a global (reads like
    # window.confirm are fine). No `window.<name> =` and no `var X` at top level.
    import re
    assert not re.search(r"window\.\w+\s*=", _JS)
    assert "PAGE_ID = 'video-import'" in _JS


def test_page_is_a_real_video_page_not_shared():
    # the nav entry exists and is NO LONGER flagged shared (it's a true video page now)
    assert 'data-video-page="video-import"' in _INDEX
    assert "{ id: 'video-import', label: 'Import' }" in _SIDE
    assert "'video-import', label: 'Import', shared" not in _SIDE


def test_subpage_container_and_script_present():
    assert 'data-video-subpage="video-import"' in _INDEX
    assert "data-vimp-grid" in _INDEX and "data-vimp-empty" in _INDEX
    assert "video/video-import.js" in _INDEX           # script include
    assert ".vimp-card" in _CSS and ".vimp-modal" in _CSS


def test_loads_and_polls_the_failed_queue():
    assert "/api/video/import/failed" in _JS
    assert "soulsync:video-page-shown" in _JS
    assert "setInterval(" in _JS                       # 5s poll while shown


def test_resolve_flow_wired_to_place_and_search():
    # the picker reuses the existing TMDB search, library-owned floated to the top
    assert "/api/video/search?q=" in _JS
    assert "library first" in _JS or "owned" in _JS
    # movie vs episode placement + the place/dismiss endpoints. Matched on the
    # URL rather than the exact expression building it: the id was hoisted to a
    # local so the reconcile/poll closures could share it, and pinning the
    # spelling of that made a refactor look like an unwired endpoint.
    assert "'/api/video/import/' + id + '/place'" in _JS
    assert "scope: r.kind" in _JS
    assert "/dismiss'" in _JS


def test_a_lost_response_is_reconciled_rather_than_called_a_failure():
    """A long copy can outlive a proxy timeout while the server finishes it
    happily, so the request outcome alone cannot tell a failed placement from a
    placement whose response was lost. Reported as 'couldn't place the file, but
    the import goes fine'."""
    assert "/place/status'" in _JS
    assert "function reconcile" in _JS
    # every failure route asks what actually happened before saying it failed
    body = _JS[_JS.index("function place("):]
    body = body[:body.index("\n    function ", 10)]
    assert body.count("reconcile(") >= 4, body
    assert "toast('Couldn’t place the file', 'error')" not in body, \
        "a failure is still announced without checking the outcome first"


def test_the_list_refreshes_even_when_a_placement_reports_failure():
    """load() used to run only on success, so a placement that actually landed
    stayed on screen as unplaced — which is what made the false error
    convincing."""
    body = _JS[_JS.index("function failed("):]
    body = body[:body.index("\n        }", 10)]
    assert "load()" in body


def test_endpoints_registered_on_the_blueprint():
    init = (_ROOT / "api" / "video" / "__init__.py").read_text(encoding="utf-8")
    assert "reg_manual_import(bp)" in init


# ── card redesign: Cinema language + expand drawer + real actions ────────────

def test_cards_use_the_cinema_card_language():
    assert 'data-vtype="' in _JS                    # movie azure / tv violet accent
    assert '.vimp-card[data-vtype="movie"]' in _CSS and '.vimp-card[data-vtype="tv"]' in _CSS
    assert "vimp-art" in _JS                        # poster tile (poster_url finally used)
    assert "vimp-art-badge" in _JS                  # type corner badge


def test_cards_expand_into_a_detail_drawer():
    assert "function drawerHTML(" in _JS
    assert "state.expanded" in _JS                  # open state survives the poll
    assert "vimp-dr-facts" in _JS and ".vimp-dr-facts" in _CSS
    assert "data-vimp-copy" in _JS                  # copy-path action
    # the 5s poll must not blink an open drawer away — renders are signature-gated
    assert "_lastSig" in _JS


def test_reasons_are_classified_into_chips():
    assert "function classifyReason(" in _JS
    for cls in ("vimp-rchip--sample", "vimp-rchip--upgrade", "vimp-rchip--corrupt",
                "vimp-rchip--identify", "vimp-rchip--other"):
        assert cls in _CSS, cls


def test_drawer_offers_delete_file_with_destructive_confirm():
    assert "data-vimp-delete" in _JS
    assert "delete_file: !!del" in _JS
    assert "destructive: true" in _JS               # Commissary confirm modal, red button
    assert ".vimp-btn--danger" in _CSS


# ── Place dialog: pick which Library the file lands in ───────────────────────
# The dialog had only Movie/Episode tabs, so the backend always fell back to the
# PRIMARY Library for the kind — a show from a separate Anime library had no way
# to reach it.

def _func(name: str) -> str:
    i = _JS.index("function " + name + "(")
    nxt = _JS.find("\n    function ", i + 1)
    return _JS[i:nxt if nxt != -1 else len(_JS)]


def test_place_dialog_has_a_library_picker():
    assert "data-vimp-lib-row" in _JS and "data-vimp-lib" in _JS
    assert ".vimp-lib" in _CSS
    assert "function renderLibraryPicker(" in _JS


def test_library_picker_reads_the_configured_registry():
    """d.configured is the Library registry (ids + labels); d.movies/d.tv are the
    admin-only server-section discovery list, which carries neither."""
    body = _func("loadLibraries")
    assert "/api/video/libraries" in body
    assert "d.configured" in body
    assert "d.movies" not in body and "d.tv" not in body


def test_library_picker_follows_the_kind_tab():
    body = _func("renderLibraryPicker")
    # Keyed off the resolve dialog's kind, whatever the expression around it —
    # a whole-folder import ('season') maps to the TV libraries, so the literal
    # `LIB_KEY[r.kind]` this once asserted is no longer the only correct form.
    assert "LIB_KEY[" in body and "r.kind" in body
    assert "libs.length < 2" in body            # one Library is not a choice
    assert "renderLibraryPicker()" in _func("renderModal")


def test_a_season_pack_uses_the_tv_libraries():
    """'season' is a show-shaped import. Passed through raw it would miss
    LIB_KEY entirely and offer no Library picker at all."""
    body = _func("renderLibraryPicker")
    assert "'season'" in body and "'episode'" in body


def test_place_sends_the_chosen_library():
    body = _func("place")
    assert "root_folder_id: r.rootFolderId || null" in body
