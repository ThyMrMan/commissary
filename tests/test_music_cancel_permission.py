"""Cancelling a music download is a downloader's action.

The music-side twin of the video cancel gate closed in 1.8.8. All four cancel
routes were behind NO permission check, and the queue they act on is shared:

    /api/downloads/cancel           one slskd transfer
    /api/downloads/cancel-all       every active transfer at once
    /api/downloads/cancel_task      one task — AND re-adds it to the wishlist
    /api/downloads/cancel_task_v2   the atomic path the Downloads page calls

So any signed-in profile could stop the admin's in-flight downloads. cancel_task
is the worst of the four: it also writes the cancelled track back to the wishlist,
so one ungated call both killed a download and mutated shared state.

The per-row × and Cancel All are hidden to match, through the same _mayCancel()
helper the Clear button uses — three controls, one rule, so they cannot drift.
The server is the authority; hiding is only so nothing offers a 403.
"""

from __future__ import annotations

import pathlib

_ROOT = pathlib.Path(__file__).resolve().parents[1]

CANCEL_VIEWS = (
    "cancel_download",
    "cancel_all_downloads",
    "cancel_download_task",
    "cancel_task_v2",
)


def _web_server():
    return (_ROOT / "web_server.py").read_text(encoding="utf-8")


def _pages_extra():
    return (_ROOT / "webui" / "static" / "pages-extra.js").read_text(encoding="utf-8")


def _view_body(name):
    src = _web_server()
    return src.split("\ndef %s(" % name, 1)[1].split("\n@app.route", 1)[0]


# ── every cancel route ───────────────────────────────────────────────────────
def test_every_cancel_route_requires_download_permission():
    for name in CANCEL_VIEWS:
        body = _view_body(name)
        assert "check_download_permission()" in body, name
        assert "return dl_err" in body, name


def test_the_check_runs_before_the_cancel_does():
    """A gate after the transfer is already aborted would be decoration."""
    for name in CANCEL_VIEWS:
        body = _view_body(name)
        gate = body.index("check_download_permission()")
        work = body.index("request.get_json()") if "request.get_json()" in body else len(body)
        assert gate < work, name


def test_the_gate_sits_after_the_docstring_not_inside_it():
    """Inserting it into the docstring would silently disable all four."""
    for name in CANCEL_VIEWS:
        body = _view_body(name)
        head = body[:body.index("check_download_permission()")]
        assert head.count('"""') % 2 == 0, name


def test_cancel_task_is_called_out_for_the_wishlist_write():
    """It re-adds the cancelled track to the shared wishlist, so it was the one
    that let a member mutate state as well as stop a download."""
    body = _view_body("cancel_download_task")
    assert "wishlist" in body.lower()


# ── the buttons ──────────────────────────────────────────────────────────────
def test_the_row_cancel_is_hidden():
    js = _pages_extra()
    block = js.split("const isCancellable =", 1)[1][:200]
    assert "_mayCancel()" in block


def test_cancel_all_is_hidden():
    js = _pages_extra()
    block = js.split("const cancelAllBtn = document.getElementById('adl-cancel-all-btn');", 1)[1][:400]
    assert "_mayCancel()" in block


def test_hiding_did_not_disturb_the_in_flight_rule():
    """Per-row cancel was already limited to active/queued rows, and Cancel All to
    when there is running work. Both must still hold."""
    js = _pages_extra()
    row = js.split("const isCancellable =", 1)[1][:200]
    assert "'active'" in row and "'queued'" in row
    allb = js.split("const cancelAllBtn = document.getElementById('adl-cancel-all-btn');", 1)[1][:400]
    assert "hasRunningWork &&" in allb


def test_one_helper_drives_all_three_controls():
    """Row cancel, Cancel All and Clear Completed re-deriving the rule separately
    is how two of them end up disagreeing about who may act on the queue."""
    js = _pages_extra()
    assert "function _mayCancel()" in js
    assert js.count("_mayCancel()") >= 4   # definition + the three call sites
    assert "canDownload" in js.split("function _mayCancel()", 1)[1][:200]
