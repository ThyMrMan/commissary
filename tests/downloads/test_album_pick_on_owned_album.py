"""Picking a release for an album you ALREADY OWN.

Three faults, one story. A user opened the album source picker for a record they
already had in a bad rip, chose a specific torrent, pressed go — and nothing
downloaded, with nothing in the log naming their choice.

1. **The pick was evaluated too late to matter.** ``run_full_missing_tracks_process``
   reads ``pinned_release`` at the download-phase transition, but the ownership
   analysis returns before that whenever every track is already in the library.
   The chosen release was never grabbed and never mentioned.

   Note what the fix is NOT: staging the release from that branch would be
   *worse*. ``album_bundle_dispatch.try_dispatch`` returns False on success
   precisely because the per-track workers are what import the staged files —
   and those are the workers that never run when nothing is missing. It would
   download a whole release and abandon it. So the batch says why instead, and
   the intent that actually unblocks it is captured at the pick.

2. **The pick was spent even when it was thrown away.** ``_pendingAlbumPins``
   was deleted while the request was being *built*, so a 429, a blocklist 409 or
   a dropped connection ate the choice. The natural retry — tick Force, run
   again — then ran with no pin at all while looking like it had worked.

3. **Nothing ever asked.** Replacing files the user owns is not a default, and
   the picker was the only place that knew a release had been chosen for an
   album that is already on disk.

The counts in the prompt come from the same 0.7-confidence lookup the batch
analysis uses, so the warning and the behaviour it warns about cannot disagree.
"""

from __future__ import annotations

import pathlib
import shutil
import subprocess

import pytest

from core.downloads import master as mw
from core.runtime_state import download_batches, download_tasks

_ROOT = pathlib.Path(__file__).resolve().parents[2]
_HARNESS = _ROOT / "tests" / "js" / "album_owned_prompt_harness.mjs"


def _js(*parts) -> str:
    """A webui source with line endings normalised — the repo stores these LF
    and a Windows checkout hands them back CRLF, so an anchor spanning a newline
    would pass on CI and fail only on a dev machine."""
    return _ROOT.joinpath(*parts).read_text(encoding="utf-8").replace("\r\n", "\n")


_DOWNLOADS_JS = _js("webui", "static", "downloads.js")
_SEARCH_JS = _js("webui", "static", "search.js")
_WISHLIST_JS = _js("webui", "static", "wishlist-tools.js")
_CSS = _js("webui", "static", "style.css")


def _picker_body() -> str:
    body = _DOWNLOADS_JS.split("async function openAlbumSourcePicker(", 1)[1]
    return body[:body.index("\nwindow.openAlbumSourcePicker")]


def _confirm_body() -> str:
    body = _DOWNLOADS_JS.split("function _confirmOwnedAlbumPick(", 1)[1]
    return body[:body.index("\nasync function openAlbumSourcePicker(")]


def _start_missing_body() -> str:
    """Just startMissingTracksProcess. Its wishlist twin directly above it has
    the same fetch-and-check shape, so an unscoped index() silently reads the
    wrong function and the ordering assertions below become meaningless."""
    body = _DOWNLOADS_JS.split("async function startMissingTracksProcess(playlistId) {", 1)[1]
    return body[:body.index("\nfunction updateTrackAnalysisResults(")]


def _node_available() -> bool:
    if not shutil.which("node"):
        return False
    try:
        out = subprocess.run(["node", "--version"], capture_output=True, text=True, timeout=15)
        return int(out.stdout.strip().lstrip("v").split(".")[0]) >= 18
    except Exception:
        return False


# ── what the prompt actually resolves to ─────────────────────────────────────

@pytest.mark.skipif(not _node_available(), reason="node >= 18 not available")
def test_the_prompt_behaves_under_node():
    """Source pins cannot see reachability or return values, and the answer this
    dialog resolves to is what decides whether the user's files are deleted.

    A back-out proved the gap concretely: short-circuiting the guard to
    ``if (false && ownership...)`` left every substring the text pins look for
    intact and they all still passed. This runs the thing."""
    result = subprocess.run(
        ["node", str(_HARNESS)], capture_output=True, text=True, timeout=60,
        cwd=str(_ROOT),
    )
    assert result.returncode == 0, (
        f"album owned-prompt harness failed:\n{result.stdout}\n{result.stderr}"
    )


# ── 3. the picker asks before it pins ────────────────────────────────────────

def test_picking_an_owned_album_asks_before_it_pins():
    """The load-bearing one. Without an answer the pick is a no-op, so the
    question has to be put at the moment of the pick, not left to a toggle in a
    modal that has not opened yet."""
    body = _picker_body()
    pick = body.split(".album-source-pick-btn').addEventListener(", 1)[1][:900]
    assert "await _confirmOwnedAlbumPick(ownership, c.title)" in pick
    # The WHOLE guard, not a substring of it. A back-out that short-circuits the
    # condition (`if (false && ownership && ...)`) leaves every substring a
    # looser pin looks for intact, and this test passed under exactly that.
    assert "if (ownership && ownership.owned > 0) {" in pick
    assert "await" in pick.split("_confirmOwnedAlbumPick", 1)[0][-120:], \
        "a prompt that is not awaited resolves after the pick has already gone through"


def test_cancelling_the_prompt_pins_nothing_and_keeps_the_picker_open():
    """Cancel has to be a real answer. Closing the picker or falling through to
    onPicked would both turn "no" into "yes, without replacing"."""
    body = _picker_body()
    pick = body.split(".album-source-pick-btn').addEventListener(", 1)[1][:900]
    guard = pick.split("_confirmOwnedAlbumPick", 1)[1]
    # `return` before either close() or onPicked() can run.
    assert guard.index("if (!answer) return;") < guard.index("close();")
    assert guard.index("if (!answer) return;") < guard.index("onPicked(")


def test_the_answer_travels_with_the_pin_not_beside_it():
    """One decision, one dialog, one lifetime. Split across two stores, clearing
    the pin would leave a live 'overwrite my files' flag attached to the next
    download of the same album."""
    body = _DOWNLOADS_JS.split("function setPendingAlbumPin(", 1)[1][:600]
    assert "opts && opts.replace" in body
    assert "{pin: pin || null, replace: replace}" in body
    # A cleared pick clears BOTH — the record is deleted, not half-emptied.
    assert "delete _pendingAlbumPins[virtualPlaylistId];" in body


def test_replace_rides_the_flags_that_actually_overwrite():
    """`force_download_all` alone only skips the ownership check. `force_replace`
    — which web_server derives from it — is what lets the import delete the file
    that is already there; without it the protection discards the download as a
    duplicate and the user gets their bad rip back."""
    block = _DOWNLOADS_JS.split("if (_pendingPick.replace) {", 1)[1][:700]
    assert "requestBody.force_download_all = true;" in block
    assert "requestBody.ignore_manual_matches = true;" in block


def test_choosing_automatically_is_asked_the_same_question():
    """"Let Commissary choose" hits the identical dead end — analysis finds
    nothing missing and the batch ends — so it must not be the quiet way to skip
    the prompt."""
    body = _picker_body()
    auto = body.split("#album-sources-auto').addEventListener(", 1)[1][:800]
    assert "_confirmOwnedAlbumPick" in auto
    assert "if (!answer) return;" in auto
    assert "onPicked(null, {replace: replace})" in auto
    # The whole guard — see test_picking_an_owned_album_asks_before_it_pins.
    assert "if (ownership && ownership.owned > 0) {" in auto


def test_an_album_you_do_not_own_is_never_interrupted():
    """The prompt is a cost. It is paid only when it buys something."""
    body = _picker_body()
    assert body.count("if (ownership && ownership.owned > 0) {") == 2   # both picks
    assert body.count("if (ownership && ownership.owned > 0 && ownedEl) {") == 1  # the banner


def test_the_banner_states_it_while_the_results_stream():
    """Asking at the pick is not enough on its own — by then the user has
    already spent time comparing releases they may not want."""
    body = _picker_body()
    header = body.split("if (msg.type === 'header') {", 1)[1][:600]
    assert "ownership = msg.ownership || null;" in header
    assert "ownedEl.style.display = '';" in header
    assert 'id="album-sources-owned"' in body


def test_a_failed_ownership_lookup_does_not_block_picking():
    """The server answers with zeros when it cannot tell. Treating "unknown" as
    "owned" would put a prompt in front of every pick the moment the DB hiccups."""
    body = _picker_body()
    assert "let ownership = null;" in body
    # `owned > 0` on a null/zero record is false, so the pick proceeds as before.
    assert "ownership = msg.ownership || null;" in body


def test_the_destructive_answer_does_not_look_like_the_safe_one():
    body = _confirm_body()
    assert "album-owned-replace" in body
    assert "album-owned-cancel" in body
    assert ".album-owned-replace {" in _CSS
    # Red, not accent — it deletes files.
    replace_css = _CSS.split(".album-owned-replace {", 1)[1][:200]
    assert "220, 80, 80" in replace_css


def test_a_partly_owned_album_can_still_just_fill_the_gaps():
    """Replacing is not the only reason to pick a release when some tracks are
    missing, and forcing that choice would make the prompt a trap."""
    body = _confirm_body()
    assert "album-owned-fill" in body
    assert "finish('fill')" in body
    # Offered only when something IS missing.
    assert "complete ? '' :" in body


# ── the search terms are editable ───────────────────────────────────────────
# Indexers file the same record under names the metadata provider never uses
# ("Flo.Rida-Wild.Ones-2012-FLAC"), so the auto-filled query is a starting
# point. Everything below is about that not quietly breaking the prompt above.

def test_the_search_terms_are_prefilled_and_editable():
    body = _picker_body()
    assert 'id="album-sources-album"' in body
    assert 'id="album-sources-artist"' in body
    assert 'id="album-sources-go"' in body
    assert "albumInput.value = album || '';" in body
    assert "artistInput.value = artist || '';" in body


def test_the_prefill_is_a_property_never_an_attribute():
    """escapeHtml here is textContent -> innerHTML: it escapes & < > but NOT
    quotes. Correct for a text node, an injection hole in value="". Album titles
    with a double quote are ordinary, and they come from a metadata provider."""
    body = _picker_body()
    row = body.split('<div class="album-sources-query">', 1)[1].split("</div>`", 1)[0]
    assert 'value="' not in row, "the inputs must render empty and be filled by property"
    assert "escapeHtml" not in row


def test_a_new_search_replaces_the_old_results():
    """Appending would leave releases from a query the user has already
    abandoned sitting in the list, indistinguishable from the new ones."""
    run = _picker_body().split("async function runSearch() {", 1)[1][:900]
    assert "groupsHost.innerHTML = '';" in run
    assert "found = 0;" in run


def test_a_stale_stream_cannot_write_into_the_new_results():
    """These streams are slow and Search can be pressed again mid-flight. Without
    the generation check the old reader keeps appending into a list the new
    search just cleared, interleaving two queries with nothing saying so."""
    run = _picker_body().split("async function runSearch() {", 1)[1]
    run = run[:run.index("\n    goBtn.addEventListener")]
    assert "const myGen = ++searchGen;" in run
    # Every await boundary that can resume after a newer search began: the fetch
    # itself, and each chunk read. The stale reader is cancelled, not just left.
    assert "if (myGen !== searchGen) return;" in run
    assert "if (myGen !== searchGen) { try { await reader.cancel(); } catch (_) {} return; }" in run
    # ...and the shared UI a stale run must not touch on its way out.
    assert "if (myGen === searchGen) statusEl.textContent = 'Search request failed';" in run
    assert "if (myGen === searchGen) goBtn.disabled = false;" in run


def test_enter_searches_as_well_as_the_button():
    body = _picker_body()
    assert "goBtn.addEventListener('click', () => { runSearch(); });" in body
    assert "if (e.key === 'Enter') { e.preventDefault(); runSearch(); }" in body


def test_too_short_a_name_is_refused_before_the_request():
    """Mirrors the server's own rule, so the user gets a sentence instead of a
    400 rendered as 'Search failed'."""
    run = _picker_body().split("async function runSearch() {", 1)[1][:900]
    assert "qAlbum.length < 2" in run
    assert run.index("qAlbum.length < 2") < run.index("fetch(")


def test_the_request_carries_both_the_query_and_the_album_it_is_for():
    """The two are no longer the same thing, and conflating them is what would
    let an edited search suppress the replace prompt."""
    run = _picker_body().split("async function runSearch() {", 1)[1][:3000]
    body = run.split("JSON.stringify({", 1)[1].split("}),", 1)[0]
    assert "album: qAlbum, artist: qArtist," in body
    assert "owned_album: album, owned_artist: artist," in body


def test_the_empty_result_message_points_at_the_box():
    """"No source has this album" was the whole story when the query was fixed.
    Now it is often just the wrong name."""
    body = _picker_body()
    assert "try a different name above" in body


def test_the_hint_says_the_search_text_is_not_what_gets_imported():
    """The pin carries source + token + title; the album context comes from the
    modal. That was invisible while the query always matched the album, and
    becomes a real question the moment the two can disagree."""
    body = _picker_body()
    hint = body.split('class="album-sources-query-hint"', 1)[1][:500]
    assert "searched for" in hint
    assert "imported as" in hint


def test_the_query_row_is_styled():
    assert ".album-sources-query {" in _CSS
    assert ".album-sources-query-field input:focus {" in _CSS


# ── both callers forward the answer ─────────────────────────────────────────

def test_the_search_page_forwards_the_answer():
    """Dropping `opts` here would send the user's confirmed yes into the void
    and grab nothing — the exact failure the prompt exists to end."""
    body = _SEARCH_JS.split("async function _openSourcesForAlbum(", 1)[1][:900]
    assert "(pin, opts) =>" in body
    assert "setPendingAlbumPin(virtualPlaylistId, pin, opts)" in body


def test_the_download_modal_forwards_the_answer():
    body = _WISHLIST_JS.split("async function openAlbumReleasePickerForModal(", 1)[1]
    assert "(pin, opts) =>" in body
    assert "window.setPendingAlbumPin(playlistId, pin, opts)" in body


def test_the_modal_toast_says_replacing_when_it_is_replacing():
    body = _WISHLIST_JS.split("async function openAlbumReleasePickerForModal(", 1)[1]
    assert "opts && opts.replace" in body
    assert "'Replacing' : 'Downloading'" in body


# ── 2. the pick is spent on acceptance, not on send ─────────────────────────

def test_the_pick_is_not_spent_while_the_request_is_being_built():
    """A 429, a blocklist 409 or a dropped connection used to eat the choice
    here, and the retry then ran with no pin while looking like it worked."""
    build = _start_missing_body().split("const _pendingPick = _pendingAlbumPins[playlistId];", 1)[1]
    build = build[:build.index("let response = await fetch(")]
    assert "delete _pendingAlbumPins" not in build


def test_the_pick_is_spent_once_the_server_has_the_batch():
    """Still consumed once — left in place it would silently re-pin a stale
    release the next time this album downloads."""
    after = _start_missing_body().split("process.batchId = data.batch_id;", 1)[1][:600]
    assert "delete _pendingAlbumPins[_pendingPinKey];" in after


def test_the_delete_is_downstream_of_the_failure_paths():
    """Ordering IS the fix: both the rate-limit throw and the blocklist return
    have to be able to run without the pick being spent."""
    src = _start_missing_body()
    build_at = src.index("const _pendingPick = _pendingAlbumPins[playlistId];")
    fail_at = src.index("Try closing some other download processes first.")
    spend_at = src.index("delete _pendingAlbumPins[_pendingPinKey];")
    assert build_at < fail_at < spend_at


# ── 1. a discarded pin is never silent ──────────────────────────────────────

@pytest.fixture(autouse=True)
def _reset_state():
    download_tasks.clear()
    download_batches.clear()
    yield
    download_tasks.clear()
    download_batches.clear()


class _Album:
    def __init__(self, id_, title):
        self.id = id_
        self.title = title


class _Track:
    def __init__(self, title):
        self.title = title


class _DB:
    """Every wanted track already owned, matched through the album fast path."""
    def __init__(self):
        self.album = _Album(42, "Wild Ones")
        self.album_tracks = [_Track("Whistle")]

    def check_track_exists(self, title, artist, **kw):
        return (object(), 0.95)

    def check_album_exists_with_editions(self, title, artist, **kw):
        return (self.album, 0.95)

    def get_tracks_by_album(self, album_id):
        return self.album_tracks

    def _string_similarity(self, a, b):
        return 1.0 if a == b else 0.0

    def update_sync_history_completion(self, *a, **k):
        pass

    def update_sync_history_track_results(self, *a, **k):
        pass

    def get_manual_library_match(self, *a, **k):
        return None

    def find_manual_library_match_by_source_track_id(self, *a, **k):
        return None


def _deps():
    from tests.downloads.test_downloads_master import (
        _build_deps, _FakeAlbumBundleSoulseek, _FakeConfig, _FakePluginWrapper,
    )
    plugin = _FakeAlbumBundleSoulseek()
    return _build_deps(
        config=_FakeConfig({'download_source.mode': 'torrent'}),
        soulseek=_FakePluginWrapper({'torrent': plugin}),
    ), plugin


PIN = {'source': 'torrent', 'token': 'tok-1', 'title': 'Flo Rida - Wild Ones (2012) FLAC'}


def _run(batch_id, monkeypatch, *, pinned):
    db = _DB()
    monkeypatch.setattr('database.music_database.MusicDatabase', lambda: db)
    deps, plugin = _deps()
    download_batches[batch_id] = {
        'phase': 'queued', 'queue': [], 'analysis_total': 0,
        'analysis_processed': 0, 'analysis_results': [],
        'is_album_download': True,
        'album_context': {'name': 'Wild Ones', 'total_tracks': 1},
        'artist_context': {'name': 'Flo Rida'},
        'pinned_release': pinned,
    }
    mw.run_full_missing_tracks_process(
        batch_id, 'enhanced_search_album_1',
        [{'name': 'Whistle', 'artists': ['Flo Rida'], 'track_number': 1}], deps)
    return plugin


def test_a_discarded_pin_is_recorded_on_the_batch(monkeypatch):
    """The user chose a release and got a completed batch with no download and
    no explanation. The release still must not be grabbed — see the module
    docstring — but the batch has to admit that it dropped the choice."""
    plugin = _run('BP1', monkeypatch, pinned=PIN)
    batch = download_batches['BP1']
    assert batch['phase'] == 'complete'
    assert plugin.calls == [], "staging a release nothing will import would be worse"
    assert batch['pinned_release_skipped'] is True
    assert 'Replace' in batch['pinned_release_skipped_reason']


def test_a_discarded_pin_names_the_release_in_the_log(monkeypatch, caplog):
    """'No missing tracks' alone sent the user to the wrong layer — the log has
    to name the release and the setting that unblocks it."""
    import logging
    caplog.set_level(logging.WARNING, logger='soulsync.downloads.master')
    _run('BP2', monkeypatch, pinned=PIN)
    msgs = [r.getMessage() for r in caplog.records
            if r.name == 'soulsync.downloads.master']
    hit = [m for m in msgs if 'was NOT grabbed' in m]
    assert hit, msgs
    assert 'Flo Rida - Wild Ones (2012) FLAC' in hit[0]
    assert 'torrent' in hit[0]
    assert 'Replace' in hit[0]


def test_without_a_pin_nothing_new_is_said_or_stored(monkeypatch):
    """An ordinary all-owned batch is not a problem and must not start reporting
    like one."""
    _run('BP3', monkeypatch, pinned=None)
    assert download_batches['BP3']['phase'] == 'complete'
    assert 'pinned_release_skipped' not in download_batches['BP3']


def test_replace_intent_reaches_the_pinned_release(monkeypatch):
    """The end-to-end point of the prompt: with the answer carried through,
    force_download_all skips the ownership check, the batch reaches the
    download-phase transition, and the release the user picked is finally
    claimed from the source they picked it from."""
    plugin = _run_forced('BP4', monkeypatch)
    assert plugin.calls, "the pinned release should now actually be grabbed"
    album, artist, _staging, kwargs = plugin.calls[0]
    assert (album, artist) == ('Wild Ones', 'Flo Rida')
    assert kwargs['preferred_release']['token'] == 'tok-1'


def _run_forced(batch_id, monkeypatch):
    db = _DB()
    monkeypatch.setattr('database.music_database.MusicDatabase', lambda: db)
    deps, plugin = _deps()
    download_batches[batch_id] = {
        'phase': 'queued', 'queue': [], 'analysis_total': 0,
        'analysis_processed': 0, 'analysis_results': [],
        'is_album_download': True,
        'album_context': {'name': 'Wild Ones', 'total_tracks': 1},
        'artist_context': {'name': 'Flo Rida'},
        'pinned_release': PIN,
        # What the picker's "Replace" answer produces once web_server has
        # translated it (force_download_all + not wing_it -> force_replace).
        'force_download_all': True,
        'force_replace': True,
    }
    mw.run_full_missing_tracks_process(
        batch_id, 'enhanced_search_album_1',
        [{'name': 'Whistle', 'artists': ['Flo Rida'], 'track_number': 1}], deps)
    return plugin


# ── the ownership summary the prompt is built on ────────────────────────────

@pytest.fixture(scope="module")
def summary():
    from web_server import _album_ownership_summary
    return _album_ownership_summary


class _OwnDB:
    def __init__(self, result):
        self.result = result
        self.calls = []

    def check_album_exists_with_completeness(self, title, artist, **kw):
        self.calls.append((title, artist, kw))
        return self.result


def test_an_unknown_album_reports_owning_nothing(summary, monkeypatch):
    import web_server
    monkeypatch.setattr(web_server, 'get_database',
                        lambda: _OwnDB((None, 0.0, 0, 0, False, [])))
    assert summary('Flo Rida', 'Wild Ones')['owned'] == 0


def test_a_known_album_reports_what_is_on_disk(summary, monkeypatch):
    import web_server
    monkeypatch.setattr(web_server, 'get_database',
                        lambda: _OwnDB((_Album(1, 'Wild Ones'), 0.92, 12, 18, False,
                                        ['MP3-320'])))
    out = summary('Flo Rida', 'Wild Ones')
    assert out['owned'] == 12 and out['expected'] == 18
    assert out['complete'] is False
    assert out['formats'] == ['MP3-320']       # what they have is WHY they're replacing


def test_a_weak_match_is_not_reported_as_owned(summary, monkeypatch):
    """Below the threshold the batch would not treat it as owned either, so
    warning about it would be a prompt in front of a pick that needs none."""
    import web_server
    monkeypatch.setattr(web_server, 'get_database',
                        lambda: _OwnDB((_Album(1, 'Wild One'), 0.55, 12, 12, True, [])))
    assert summary('Flo Rida', 'Wild Ones')['owned'] == 0


def test_the_threshold_matches_the_one_the_batch_uses(summary, monkeypatch):
    """If these drift, the picker warns about a replacement that will not happen
    (or stays silent before one that will)."""
    import web_server
    db = _OwnDB((None, 0.0, 0, 0, False, []))
    monkeypatch.setattr(web_server, 'get_database', lambda: db)
    summary('Flo Rida', 'Wild Ones')
    assert db.calls[0][2]['confidence_threshold'] == 0.7
    master_src = (_ROOT / "core" / "downloads" / "master.py").read_text(encoding="utf-8")
    call = master_src.split("db.check_album_exists_with_editions(", 1)[1][:300]
    assert "confidence_threshold=0.7" in call


def test_a_broken_lookup_degrades_to_owning_nothing(summary, monkeypatch):
    """Advisory only. A DB hiccup must not put a prompt in front of every pick,
    and must not break the picker it rides on."""
    import web_server

    class _Boom:
        def check_album_exists_with_completeness(self, *a, **k):
            raise RuntimeError("db gone")

    monkeypatch.setattr(web_server, 'get_database', lambda: _Boom())
    assert summary('Flo Rida', 'Wild Ones') == {
        "owned": 0, "expected": 0, "complete": False, "formats": []}


def test_a_blank_album_never_reaches_the_database(summary, monkeypatch):
    import web_server
    db = _OwnDB((None, 0.0, 0, 0, False, []))
    monkeypatch.setattr(web_server, 'get_database', lambda: db)
    assert summary('Flo Rida', '   ')['owned'] == 0
    assert db.calls == []


def test_the_header_carries_ownership_to_the_picker():
    """The picker cannot ask about something it was never told."""
    src = (_ROOT / "web_server.py").read_text(encoding="utf-8")
    route = src.split("def album_source_candidates(", 1)[1][:6000]
    assert "ownership = _album_ownership_summary(" in route
    header = route.split('"type": "header"', 1)[1][:400]
    assert '"ownership": ownership,' in header


def test_ownership_follows_the_album_not_the_edited_search_text():
    """The picker lets the search terms be retyped, to find a release an indexer
    files under some other name. The ownership question does NOT move with them:
    it is about the album the batch will run for, which has not changed.

    Bind it to the query instead and editing the search silently suppresses the
    replace prompt — the same no-op this whole module exists to end, reachable
    again through the box the user was invited to type in."""
    src = (_ROOT / "web_server.py").read_text(encoding="utf-8")
    route = src.split("def album_source_candidates(", 1)[1][:6000]
    call = route.split("ownership = _album_ownership_summary(", 1)[1][:200]
    assert "data.get('owned_artist')" in call
    assert "data.get('owned_album')" in call
    # ...and any other caller, which sends no owned_*, still gets today's answer.
    assert "or artist" in call and "or album" in call
