"""Downloads that finished but never left "Downloading".

Reported as five tracks stuck in Active, "the torrents downloaded fine". They
had: three were already imported and sitting in the library, one torrent had
completed and never been post-processed, and one had been refused at grab time.
All five presented identically as a task frozen at "Downloading".

Three faults, found in one instance's logs:

1. **The completion callback required a batch.** ``post_process_matched_download``
   ended with ``if task_id and batch_id:`` before marking the task completed. A
   manual pick from the search modal has a task and NO batch, so the block was
   skipped: across ten days that instance logged 836 "Post-processing complete"
   lines and exactly ZERO task completions. The file imported, the DB row was
   written, and the row stayed at "Downloading" for ever.

2. **Six early returns settled nothing.** Between "the file is on disk" and the
   completion callback sit six ``return``s, all of them meaning "the library
   already has this" — a success from the user's side. They returned without
   touching the task, so whatever queued it queued it again: ``17 - Penguins.flac``
   was downloaded, moved, compared and deleted 64 times over three days.

3. **qBittorrent's newer add reply was read as a refusal.** WebAPI <= 2.10 says
   ``Ok.``; newer builds answer with JSON. ``{"added_torrent_ids": [],
   "failure_count": 0, "pending_count": 1, "success_count": 0}`` means accepted
   and still fetching the URL — but anything other than ``Ok.`` was treated as a
   rejection, so the client downloaded the release happily while the app
   reported "Torrent client refused the URL" and never polled it.

And the setting the fault made necessary: a download the USER chose now
outranks the copy already on disk, because a person pressing download is a
clearer statement of intent than a quality comparison.
"""

from __future__ import annotations

import pytest

from core.torrent_clients.qbittorrent import _parse_add_response


# ── 3. qBittorrent's add reply ──────────────────────────────────────────────

def test_the_exact_reply_that_was_read_as_a_refusal():
    """Verbatim from the report. failure_count 0 and pending_count 1 mean qBit
    took the URL and is fetching it; the empty id list is 'not resolved yet',
    not 'rejected'."""
    accepted, direct = _parse_add_response(
        '{"added_torrent_ids":[],"failure_count":0,"pending_count":1,"success_count":0}')
    assert accepted is True
    assert direct is None, "the hash is discovered by confirm/poll, as for any URL add"


def test_a_resolved_id_is_used_directly():
    """When the reply names the torrent there is nothing to discover, and
    polling for it would just be a race we can lose."""
    accepted, direct = _parse_add_response(
        '{"added_torrent_ids":["AABBCCDD"],"failure_count":0,"pending_count":0,"success_count":1}')
    assert (accepted, direct) == (True, "aabbccdd")


def test_a_real_failure_is_still_a_failure():
    """The point is not to accept everything — a reply that says something
    failed must still stop the grab."""
    accepted, _ = _parse_add_response(
        '{"added_torrent_ids":[],"failure_count":2,"pending_count":0,"success_count":0}')
    assert accepted is False


def test_the_legacy_ok_body_never_reaches_the_parser():
    """`Ok.` is handled before this, and is not JSON — if it ever did arrive
    here it must not be mistaken for an acceptance by accident."""
    assert _parse_add_response("Ok.") == (False, None)


@pytest.mark.parametrize("body", ["", "Fails.", "<html>500</html>", "[]", "null",
                                  '{"unrelated": true}'])
def test_an_unrecognised_body_is_refused(body):
    """Guessing "probably fine" about a shape we do not understand would hand
    back a download nothing can track — which is the failure being fixed, in
    the other direction."""
    assert _parse_add_response(body) == (False, None)


def test_the_add_path_consults_the_parser():
    import inspect
    from core.torrent_clients import qbittorrent
    src = inspect.getsource(qbittorrent.QBittorrentAdapter._add_torrent_sync)
    assert "_parse_add_response" in src
    # The legacy fast path survives: an `Ok.` body must not start JSON parsing.
    assert "!= 'Ok.'" in src


# ── 1 + 2. the task settles, whatever happened ──────────────────────────────

def _pipeline_src() -> str:
    import inspect
    from core.imports import pipeline
    return inspect.getsource(pipeline.post_process_matched_download)


def test_the_completion_no_longer_requires_a_batch():
    """THE bug. A manual pick has a task and no batch; requiring both is why
    836 successful imports marked zero tasks completed."""
    src = _pipeline_src()
    # The old block, verbatim: status was set only inside `if task_id and batch_id`.
    assert "download_tasks[task_id]['status'] = 'completed'" not in src
    assert "Calling completion callback for task" not in src
    settle = src.split("def _settle_task(", 1)[1]
    # NOTE `if task_id and batch_id:` still appears elsewhere and is CORRECT
    # there: the integrity / audio / quality failure paths already set the
    # status under a bare `if task_id:` and gate only the notify. What the bug
    # was is the completion doing its status write inside that same condition.
    assert "if not task_id:" in settle
    assert "if batch_id:" in settle
    assert settle.index("if batch_id:") > settle.index("_mark_task_completed"), \
        "the batch notify must come after the task's own status is settled"


def test_every_terminal_return_settles_the_task():
    """Six returns sit between 'the file is on disk' and the completion
    callback. Each is a terminal outcome for the task; each used to leave it
    at 'Downloading' and let something re-queue it."""
    src = _pipeline_src()
    body = src.split("def _settle_task(", 1)[1].split("\n    def ", 1)[-1]
    # Every `return` after the settle helper, other than the guard-clause
    # returns before any file work, is preceded by a settle call.
    calls = body.count("_settle_task(")
    assert calls >= 7, "expected the six early returns plus the ordinary success, got %d" % calls


def test_the_outcomes_are_named_not_just_flagged():
    """'Downloading' forever was bad; 'Completed' with no reason would still
    leave the user wondering why nothing changed on disk."""
    src = _pipeline_src()
    for phrase in ("already in the library",
                   "the library copy is the same or better quality",
                   "the library copy already has enhanced metadata",
                   "another thread completed the transfer",
                   "the stream processor already placed it"):
        assert phrase in src, phrase


def test_a_skip_is_reported_as_success_not_failure():
    """The track IS in the library. Reporting these as failures would send the
    retry monitor after a download that has nothing left to do."""
    src = _pipeline_src()
    for phrase in ("already in the library",
                   "the library copy is the same or better quality"):
        call = src.split(phrase, 1)[0]
        assert call.rstrip().endswith("outcome=(") or "success=True" in call[-260:], phrase


def test_the_short_replacement_guard_still_fails_the_task():
    """The one terminal outcome here that is NOT a success: a truncated file
    was refused, and the user needs to see that rather than a green tick."""
    src = _pipeline_src()
    assert "success=False" in src
    guard = src.split("Replacement rejected", 1)[0]
    assert "_settle_task(success=False" in guard[-400:]


def test_a_task_without_an_id_is_simply_left_alone():
    """Auto-import has no task. Settling must be a no-op there, not a crash in
    the middle of a successful import."""
    src = _pipeline_src()
    settle = src.split("def _settle_task(", 1)[1]
    settle = settle[:settle.index("if batch_id:")]      # the helper's own body
    assert "if not task_id:" in settle
    assert settle.index("if not task_id:") < settle.index("with tasks_lock:")


# ── the setting ─────────────────────────────────────────────────────────────

@pytest.fixture()
def replaces():
    from core.imports.pipeline import _user_download_replaces
    return _user_download_replaces


def test_a_file_the_user_picked_replaces_what_is_there(replaces):
    assert replaces({"_user_manual_pick": True}) is True


def test_an_unattended_import_never_replaces(replaces):
    """The wishlist drain, auto-import and the retry monitor never set the
    manual-pick flag, and none of them may overwrite a library file this way."""
    assert replaces({}) is False
    assert replaces({"batch_id": "b1", "task_id": "t1"}) is False
    assert replaces(None) is False


def test_it_can_be_turned_off(replaces, monkeypatch):
    from core.imports import pipeline
    monkeypatch.setattr(pipeline.config_manager, "get",
                        lambda k, d=None: False if k == "import.user_download_always_replaces" else d)
    assert replaces({"_user_manual_pick": True}) is False


def test_it_defaults_on_for_an_install_that_has_never_set_it(replaces, monkeypatch):
    """Read with a True default, so the fix reaches existing installs without
    them having to find a new checkbox."""
    from core.imports import pipeline
    seen = {}

    def _get(key, default=None):
        seen[key] = default
        return default

    monkeypatch.setattr(pipeline.config_manager, "get", _get)
    assert replaces({"_user_manual_pick": True}) is True
    assert seen["import.user_download_always_replaces"] is True


def test_a_broken_config_read_does_not_block_the_import(replaces, monkeypatch):
    """A config failure must not turn into a failed import."""
    from core.imports import pipeline

    def _boom(*a, **k):
        raise RuntimeError("config gone")

    monkeypatch.setattr(pipeline.config_manager, "get", _boom)
    assert replaces({"_user_manual_pick": True}) is True


def test_it_feeds_the_same_force_replace_the_toggle_does(replaces):
    """Not a second mechanism: it ORs into the flag the Force toggle already
    sets, so both answer to one code path in the protection block."""
    src = _pipeline_src()
    assert "_batch_force_replace(context) or _user_download_replaces(context)" in src


def test_the_setting_is_reachable_in_the_ui():
    """A default-on behaviour that overwrites files needs a visible off switch."""
    import pathlib
    root = pathlib.Path(__file__).resolve().parents[2]
    html = (root / "webui" / "index.html").read_text(encoding="utf-8")
    js = (root / "webui" / "static" / "settings.js").read_text(encoding="utf-8")
    assert 'id="import-user-download-replaces"' in html
    assert "Only applies to downloads you chose" in html
    # Loaded with `!== false` so a never-saved install shows it ticked.
    assert "user_download_always_replaces !== false" in js
    assert "user_download_always_replaces:" in js
