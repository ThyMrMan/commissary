"""The Library naming templates must survive leaving the page.

Reported as "Library video naming templates do not save after leaving the
page". The card autosaves on the template inputs' ``change`` event, which is
correct for TYPING — but two controls write the template with JavaScript:

  * the token chips under "show all N" (click-to-insert), and
  * the "use the TRaSH recommended scheme" preset button.

A value assigned by code raises no ``change`` event — browsers fire that only
for edits the user made — so neither ever reached the server. Measured in the
running app before the fix: after clicking a token chip the input read
``... {Custom Formats}`` while ``GET /api/video/organization`` still returned
the old template, and ``fillOrg()`` overwrote the box with the stored value on
the next page show. The preset was worse: it toasted "click away to save",
advice that could not work for the same reason.

The backend was never at fault — POST/GET round-trips any template fine — so
these pin the frontend contract instead: anything that writes into a template
box commits it, and a save that fails says so rather than toasting success.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parents[1]
_JS = (_ROOT / "webui" / "static" / "video" / "video-settings.js").read_text(encoding="utf-8")


def _card_block() -> str:
    """The Library Organization section of video-settings.js."""
    start = _JS.index("// ── Library Organization")
    return _JS[start:_JS.index("\n    function onPageShown", start)]


@pytest.fixture(scope="module")
def block() -> str:
    return _card_block()


def test_commit_helper_actually_saves(block):
    """commitTemplateEdit must POST, not just redraw the preview. The bug was
    precisely that the programmatic paths refreshed the preview and stopped."""
    edit = block[block.index("function commitTemplateEdit"):]
    edit = edit[:edit.index("\n    function ")]
    assert "renderOrgPreview" in edit, "the preview should still follow the edit"
    assert "_commitOrgNow" in edit, "the edit must be scheduled for commit"
    assert "_orgEditSeq++" in edit, "an edit must mark the box dirty against a late load"

    now = block[block.index("function _commitOrgNow"):]
    now = now[:now.index("\n    function ")]
    assert "saveOrganization" in now, "the commit must POST, not only preview"


def test_token_chip_insert_commits(block):
    """Click-to-insert writes box.value directly — it has to commit it."""
    start = block.index("data-vo-insert]")
    branch = block[start:block.index("var preset", start)]
    assert "box.value = box.value.slice" in branch, "guarding the right branch"
    assert "commitTemplateEdit()" in branch, (
        "a token inserted by clicking a chip is lost on the next page load "
        "unless the insert commits — no 'change' event fires for it"
    )


def test_trash_preset_commits(block):
    """The 1.9.12 headline feature: the preset must apply, not just display."""
    branch = block[block.index("data-vo-preset]"):]
    assert "target.value = _TRASH_PRESETS[scope]" in branch, "guarding the right branch"
    assert "commitTemplateEdit(" in branch, "the TRaSH preset must save what it loads"
    assert "click away to save" not in branch, (
        "that instruction cannot work — clicking away raises no change event "
        "for a value the code wrote"
    )


def test_no_programmatic_template_write_stops_at_the_preview(block):
    """Generalises the two cases above: every assignment into a *-template box
    must be followed by a commit before the enclosing branch ends. Catches a
    third such control being added with the same mistake."""
    writes = [m for m in re.finditer(r"^\s*(?:box|target|input)\.value\s*=", block, re.M)]
    assert writes, "expected at least the chip-insert and preset writes"
    for m in writes:
        tail = block[m.start(): m.start() + 600]
        assert "commitTemplateEdit" in tail, (
            "a template written by code at offset %d never commits — it will "
            "look applied and be gone on the next page load" % m.start()
        )


def test_a_late_load_does_not_paint_over_an_edit(block):
    """Showing this page fires ~a dozen loads at once; the organization GET was
    measured landing 924ms after the page appeared, and fillOrg() then wrote the
    STORED template back into the box. An edit made in that window disappeared
    in front of the user — the same "it didn't save" symptom, reachable by
    typing as well as by clicking a token."""
    load = block[block.index("function loadOrganization"):]
    load = load[:load.index("\n    function ")]
    assert "_orgLoadSeq" in load, "a superseded response must be droppable"
    assert "editsAtStart" in load and "_orgEditSeq !== editsAtStart" in load, (
        "the response must know whether an edit beat it here"
    )
    assert "fillOrg(" in load and "fillOrg()" not in load, (
        "loadOrganization must pass the keep-templates decision to fillOrg"
    )

    fill = block[block.index("function fillOrg"):]
    fill = fill[:fill.index("\n    function ")]
    assert "keepTemplates" in fill, "fillOrg must be able to leave the templates alone"
    for field in ("vo-movie-template", "vo-episode-template", "vo-youtube-template"):
        assert field in fill, "guarding the right fields"
    guarded = fill[fill.index("if (!keepTemplates)"):]
    for field in ("vo-movie-template", "vo-episode-template", "vo-youtube-template"):
        assert field in guarded[:guarded.index("\n        set('vo-transfer-mode'")], (
            "%s must only be written when the fill is allowed to clobber" % field
        )


def test_save_waits_for_the_form_to_be_populated(block):
    """collectOrg() reads every field, so a commit that fires before the initial
    load lands would post the markup's blank toggles over real settings."""
    now = block[block.index("function _commitOrgNow"):]
    now = now[:now.index("\n    function ")]
    assert "if (!_videoOrg)" in now, "must not save a form that was never loaded"
    assert "setTimeout" in now, "the edit is deferred, not dropped"


def test_failed_save_does_not_claim_success(block):
    """A save that the server refused used to toast 'saved' anyway. On this card
    that is the worst failure mode: the field keeps showing the new template
    while files keep being named the old way."""
    start = block.index("function saveOrganization")
    # Cut at the next top-level function so this reads the same shape before and
    # after the fix — otherwise a missing helper aborts with a lookup error
    # instead of the assertion that explains what is wrong.
    body = block[start:block.index("\n    function ", start + 10)]
    assert "Could not save library organization" in body, "a failed save must speak"
    assert body.count("Could not save library organization") >= 2, (
        "both the non-ok response and the network-error path must report"
    )
    ok_line = [ln for ln in body.splitlines() if "Library organization saved" in ln]
    assert ok_line and "!silent" in ok_line[0], "success toast stays gated on !silent"
