"""Clearing completed music downloads is a downloader's action.

Reported as: hide the Clear Completed button on Music Downloads from
standard/Plex users.

Hiding it alone would not have been enough. /api/downloads/clear-completed was
behind NO permission check, and what it does is not scoped to the caller:

    DELETE FROM library_history WHERE event_type = 'download'

library_history has no profile column, so one click empties the download history
for EVERY profile — and takes the verification review queue with it, since the
unverified / force_imported rows live in that same table. Any signed-in profile
could wipe the admin's history.

So the button is hidden AND the endpoint now runs check_download_permission(),
the music side's existing can_download gate. The server is the authority; hiding
the button only stops the page offering an action that would 403.
"""

from __future__ import annotations

import pathlib
import re

_ROOT = pathlib.Path(__file__).resolve().parents[1]


def _web_server():
    return (_ROOT / "web_server.py").read_text(encoding="utf-8")


def _route_body(name):
    """The source of one Flask view, up to the next decorator."""
    src = _web_server()
    body = src.split("def %s(" % name, 1)[1]
    return body.split("\n@app.route", 1)[0]


# ── the server check ─────────────────────────────────────────────────────────
def test_clear_completed_requires_download_permission():
    body = _route_body("clear_completed_downloads")
    assert "check_download_permission()" in body
    assert "if dl_err:" in body and "return dl_err" in body


def test_the_check_runs_before_anything_is_deleted():
    """A gate after the delete would be decoration."""
    body = _route_body("clear_completed_downloads")
    gate = body.index("check_download_permission()")
    wipe = body.index("clear_completed_download_history()")
    assert gate < wipe


def test_the_gate_is_the_music_sides_existing_one():
    """Not a second, divergent rule — check_download_permission lets profile 1
    through and otherwise reads can_download off the profile."""
    src = _web_server()
    fn = src.split("def check_download_permission():", 1)[1].split("\ndef ", 1)[0]
    assert "get_current_profile_id()" in fn
    assert "can_download" in fn
    assert "403" in fn


# ── why it matters: the delete is not profile-scoped ─────────────────────────
def test_clearing_history_really_is_everyones():
    """If this ever becomes profile-scoped the gate could be relaxed. Until then
    it is a shared-state wipe and the test says so out loud."""
    db = (_ROOT / "database" / "music_database.py").read_text(encoding="utf-8")
    fn = db.split("def clear_completed_download_history(", 1)[1].split("\n    def ", 1)[0]
    stmt = re.search(r"DELETE FROM library_history[^\"']*", fn)
    assert stmt, "expected a DELETE against library_history"
    assert "profile_id" not in stmt.group(0)


# ── the page stops offering it ───────────────────────────────────────────────
def test_the_downloads_page_hides_the_button():
    js = (_ROOT / "webui" / "static" / "pages-extra.js").read_text(encoding="utf-8")
    block = js.split("const clearBtn = document.getElementById('adl-clear-btn');", 1)[1][:400]
    assert "_mayCancel()" in block


def test_hiding_it_did_not_disturb_the_empty_list_rule():
    """It was already hidden with nothing to clear; that must still hold, so a
    downloader doesn't get a button that clears zero rows."""
    js = (_ROOT / "webui" / "static" / "pages-extra.js").read_text(encoding="utf-8")
    block = js.split("const clearBtn = document.getElementById('adl-clear-btn');", 1)[1][:400]
    assert "completedN > 0 && _mayCancel()" in block
