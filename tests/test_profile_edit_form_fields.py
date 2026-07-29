"""Manage Profiles must show a profile's real settings, and be able to promote.

Two reports:

  * Side Access always opened on "Music only", whatever the profile actually had.
  * There was no way to make someone an admin from the UI.

The Side Access bug was not in the radio logic — that reads:

    selectedSides = (allowed_sides === 'video' || allowed_sides === 'both')
        ? allowed_sides : 'music'

which is correct. The value simply never arrived: loadProfileManageList stashes
each profile on its edit button as data-* attributes, and allowed_sides was not
among them, so the form read `undefined` and the ternary fell through to 'music'
every time. get_all_profiles resolves allowed_sides to music|video|both and never
empty, so the data was there all along — it was dropped in transit.
"""

from __future__ import annotations

import pathlib
import re

_ROOT = pathlib.Path(__file__).resolve().parents[1]


def _init_js():
    return (_ROOT / "webui" / "static" / "init.js").read_text(encoding="utf-8")


def _edit_form():
    js = _init_js()
    return js.split("function showProfileEditForm(", 1)[1].split("\nfunction ", 1)[0]


# ── Side Access reflects the profile ─────────────────────────────────────────
def test_allowed_sides_is_stashed_on_the_edit_button():
    """The actual bug: every other field was carried across and this one wasn't."""
    js = _init_js()
    assert "editBtn.dataset.allowedSides" in js


def test_allowed_sides_is_read_back_into_the_form():
    """Stashing it and not reading it would look fixed and behave identically."""
    js = _init_js()
    handler = js.split("list.querySelectorAll('.profile-edit-btn')", 1)[1][:600]
    assert "allowed_sides:" in handler
    assert "dataset.allowedSides" in handler


def test_every_field_the_form_reads_is_also_stashed():
    """The class of bug, not just this instance: anything showProfileEditForm
    reads off profileSettings has to be put on the button, or it silently
    defaults. Catches the next one."""
    js = _init_js()
    handler = js.split("list.querySelectorAll('.profile-edit-btn')", 1)[1][:600]
    passed = set(re.findall(r"(\w+):\s*btn\.dataset", handler))
    used = set(re.findall(r"profileSettings\.(\w+)", _edit_form()))
    missing = used - passed
    assert not missing, "read from profileSettings but never stashed: %s" % sorted(missing)


def test_the_radio_still_falls_back_safely():
    """An unknown/absent value must land on the most restrictive option, not
    crash or silently grant video."""
    form = _edit_form()
    # The ternary wraps across lines, so match on the statement, not a line.
    stmt = form.split("selectedSides = (", 1)[1].split(";", 1)[0]
    assert "?" in stmt, "expected the fallback ternary"
    assert stmt.rstrip().endswith("'music'"), "the fallback must be the most restrictive side"


# ── the admin checkbox ───────────────────────────────────────────────────────
def test_the_form_offers_an_administrator_checkbox():
    form = _edit_form()
    assert "isAdminCheckbox" in form
    assert "Administrator" in form


def test_it_only_appears_when_an_admin_edits_someone_else():
    """Inside the `isAdmin && !isEditingAdmin` block, so you cannot demote
    yourself out of the screen you are standing on."""
    form = _edit_form()
    guarded = form.split("if (isAdmin && !isEditingAdmin) {", 1)[1]
    assert "isAdminCheckbox = document.createElement" in guarded


def test_is_admin_is_only_sent_when_it_changed():
    """The server refuses to remove the last admin. Posting is_admin unchanged on
    every save would turn that guard into an error on an unrelated edit."""
    form = _edit_form()
    assert "isAdminCheckbox.checked !== (profileSettings.is_admin === true)" in form


def test_promoting_explains_that_the_other_controls_stop_applying():
    """_profile_sides forces 'both' for admins and every page is allowed, so the
    Side Access and page controls above become inert the moment it is ticked."""
    form = _edit_form()
    assert "adminNote" in form
    assert "stop applying" in form


def test_the_checkbox_variables_are_declared():
    """This file's own near-miss: both were used before being declared, which
    node --check does not catch."""
    form = _edit_form()
    assert "let isAdminCheckbox = null;" in form
    assert "let adminNote = null;" in form


# ── the server side already enforces it ──────────────────────────────────────
def test_the_server_requires_an_admin_and_protects_the_last_one():
    """The checkbox is convenience; these are the actual rules."""
    src = (_ROOT / "web_server.py").read_text(encoding="utf-8")
    body = src.split("def update_profile(profile_id):", 1)[1].split("\n@app.route", 1)[0]
    branch = body.split("if 'is_admin' in data", 1)[1][:600]
    assert "current['is_admin']" in body.split("if 'is_admin' in data", 1)[0][-200:] \
        or "current['is_admin']" in "if 'is_admin' in data and current['is_admin']"
    assert "Cannot remove the last admin" in branch
