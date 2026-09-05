"""The Last.fm listening importer, wired into THIS fork.

Ported from upstream SoulSync 3.3.0. ``test_lastfm_import.py`` beside this file
is upstream's own suite, taken verbatim — it passing unchanged is the evidence
that the importer itself behaves the same here. This file covers the half
upstream's tests cannot: the wiring written for this codebase, and the places
where the fork had already diverged and a careless port would have trampled it.

The port is deliberately narrow. Upstream reached 3.3.0 having also lifted its
stats endpoints into ``api/stats.py``; this fork still serves them from
``web_server.py``, so the three importer routes were added inline rather than
dragging in a refactor of code that works.
"""

from __future__ import annotations

import inspect
import sqlite3

import pytest


# ── the one client method the importer needs ────────────────────────────────

def test_the_client_gained_only_the_method_the_importer_calls():
    from core.lastfm_client import LastFMClient
    assert hasattr(LastFMClient, "get_user_recent_tracks")
    src = inspect.getsource(LastFMClient.get_user_recent_tracks)
    # Paged by the IMPORTER, not the client — the client hands back one raw page.
    assert "user.getRecentTracks" in src
    assert "raise_on_transient=True" in src, (
        "the importer distinguishes a transient page failure from an empty page; "
        "swallowing the exception here would make a network blip look like the "
        "end of the user's history")


def test_the_forks_own_removal_of_get_tag_top_artists_survived_the_port():
    """This fork deleted `get_tag_top_artists` from the client. Porting by
    copying upstream's file wholesale would have silently restored it along
    with the method actually wanted."""
    from core.lastfm_client import LastFMClient
    assert not hasattr(LastFMClient, "get_tag_top_artists")


def test_the_forks_branding_survived_the_port():
    """Same trap, other direction: the client's User-Agent and auth copy were
    rebranded here, and upstream's file still says SoulSync."""
    import core.lastfm_client as mod
    src = inspect.getsource(mod)
    assert "Commissary/1.0" in src
    assert "SoulSync/1.0" not in src


# ── the automation wiring ───────────────────────────────────────────────────

def test_the_deps_carry_the_worker_and_tolerate_its_absence():
    """The importer is legitimately None when Last.fm is unconfigured or its
    boot failed, so the field is optional and the handler answers for it."""
    import dataclasses
    from core.automation.deps import AutomationDeps
    fields = {f.name: f for f in dataclasses.fields(AutomationDeps)}
    assert "lastfm_import_worker" in fields
    assert fields["lastfm_import_worker"].default is None


def test_the_handler_is_registered_and_guarded_on_its_own_run_flag():
    """Not on the pipeline flag its neighbours share: a listening-history crawl
    has no reason to block, or be blocked by, a playlist sync."""
    import core.automation.handlers.registration as reg
    src = inspect.getsource(reg)
    assert "'import_lastfm_listening'," in src
    guard = src.split("'import_lastfm_listening',", 1)[1][:300]
    assert "deps.lastfm_import_worker and deps.lastfm_import_worker.is_running()" in guard
    assert "is_pipeline_running" not in guard.split(")")[0]


def _web_server_tree():
    import ast
    import pathlib
    root = pathlib.Path(__file__).resolve().parents[2]
    return ast.parse((root / "web_server.py").read_text(encoding="utf-8"))


def _calls_named(tree, name):
    import ast
    return [n for n in ast.walk(tree)
            if isinstance(n, ast.Call) and (
                getattr(n.func, "id", None) == name
                or getattr(n.func, "attr", None) == name)]


def test_the_deps_build_site_passes_the_worker():
    """ONE build site, not three. The guard reads deps.lastfm_import_worker, so
    a construction that misses it registers a handler which always reports the
    importer unavailable -- which is exactly what 2.3.0 shipped: the worker was
    added to three OTHER calls and never to this one, so the automation could
    never run no matter how it was configured."""
    calls = _calls_named(_web_server_tree(), "AutomationDeps")
    assert len(calls) == 1, "expected a single AutomationDeps construction"
    passed = {kw.arg for kw in calls[0].keywords}
    assert "lastfm_import_worker" in passed


def test_every_enrichment_runtime_kwarg_is_one_the_builder_accepts():
    """The bug this test replaces. Its predecessor asserted that the string
    "lastfm_import_worker=lastfm_import_worker," appeared three times in
    web_server.py -- and the mistake satisfied that perfectly, because the three
    places it landed were _build_metadata_enrichment_runtime() calls, which do
    not accept the argument at all. Every one raised TypeError at runtime and
    the user's log filled with failed metadata enrichment.

    Counting text cannot tell a right call from a wrong one. Check the keywords
    against the real signature instead, which catches the next such mistake too.
    """
    import inspect

    from core.metadata.enrichment import build_metadata_enrichment_runtime
    accepted = set(inspect.signature(build_metadata_enrichment_runtime).parameters)
    calls = _calls_named(_web_server_tree(), "_build_metadata_enrichment_runtime")
    assert calls, "no _build_metadata_enrichment_runtime call sites found"
    for call in calls:
        for kw in call.keywords:
            assert kw.arg in accepted, (
                "_build_metadata_enrichment_runtime() is called with %r, which "
                "it does not accept -- this raises TypeError at runtime" % kw.arg)


def test_the_enrichment_builder_really_rejects_it():
    """The other half: proof the signature check above is testing something. If
    the builder ever grows this argument the assertion would pass vacuously."""
    import pytest as _pytest

    from core.metadata.enrichment import build_metadata_enrichment_runtime
    with _pytest.raises(TypeError):
        build_metadata_enrichment_runtime(lastfm_import_worker=None)


def test_it_is_offered_as_an_automation_block():
    from core.automation.blocks import ACTIONS
    block = next((b for b in ACTIONS
                  if b.get("type") == "import_lastfm_listening"), None)
    assert block is not None
    keys = {f["key"] for f in block.get("config_fields", [])}
    assert {"username", "full"} <= keys
    # A blank username means "the authorized account", which the placeholder
    # has to say or the field reads as required.
    uname = next(f for f in block["config_fields"] if f["key"] == "username")
    assert "authorized" in (uname.get("placeholder") or "").lower()
    assert "SoulSync" not in block["description"]


def test_it_runs_on_a_schedule_rather_than_only_by_hand():
    """Scrobbles accrue continuously; an importer you have to remember to press
    is one that is always behind."""
    import core.automation_engine as eng
    src = inspect.getsource(eng)
    block = src.split("'action_type': 'import_lastfm_listening'", 1)
    assert len(block) == 2, "no system automation registered for the importer"
    around = block[0][-400:]
    assert "'trigger_type': 'schedule'" in around
    assert "'unit': 'hours'" in around


def test_the_handler_refuses_politely_when_the_importer_is_absent():
    from core.automation.handlers.lastfm_import import auto_import_lastfm_listening

    class _Deps:
        lastfm_import_worker = None
        config_manager = None

    out = auto_import_lastfm_listening({}, _Deps())
    assert out["status"] == "error"
    assert "not available" in out["error"]


def test_a_scheduled_run_is_skipped_while_disabled_but_a_manual_one_enables_it():
    """The distinction upstream draws: the hourly automation must not start
    importing history nobody asked for, while pressing the button IS the
    request to turn it on."""
    from core.automation.handlers.lastfm_import import auto_import_lastfm_listening

    class _Cfg:
        def __init__(self):
            self.v = {"lastfm.listening_sync_enabled": False}

        def get(self, k, d=None):
            return self.v.get(k, d)

        def set(self, k, val):
            self.v[k] = val

    class _Worker:
        def __init__(self):
            self.calls = []

        def run_once(self, username=None, full=False):
            self.calls.append((username, full))
            return {"status": "complete"}

    cfg, worker = _Cfg(), _Worker()

    class _Deps:
        lastfm_import_worker = worker
        config_manager = cfg

    assert auto_import_lastfm_listening({}, _Deps())["status"] == "skipped"
    assert worker.calls == []

    assert auto_import_lastfm_listening({"_manual_run": True}, _Deps())["status"] == "complete"
    assert cfg.get("lastfm.listening_sync_enabled") is True
    assert worker.calls == [(None, False)]


# ── the schema the importer writes into ─────────────────────────────────────

def test_the_importer_writes_only_columns_this_fork_already_has(tmp_path):
    """No migration shipped with this port, so the claim that none is needed has
    to be checked rather than asserted. The media-server scrobble path fills the
    same table, and the importer joins it rather than adding a parallel one."""
    from database.music_database import MusicDatabase
    import core.listening_import.lastfm as mod

    db = MusicDatabase(str(tmp_path / "m.db"))
    conn = sqlite3.connect(str(tmp_path / "m.db"))
    have = {r[1] for r in conn.execute("PRAGMA table_info(listening_history)")}
    assert have, "listening_history is missing entirely"

    src = inspect.getsource(mod)
    insert = src.split("INSERT OR IGNORE INTO listening_history", 1)[1]
    cols = insert.split("(", 1)[1].split(")", 1)[0]
    wanted = {c.strip() for c in cols.split(",") if c.strip()}
    assert wanted <= have, "importer writes columns this fork lacks: %s" % (wanted - have)
    conn.close()


def test_imported_rows_are_labelled_so_they_can_be_told_apart(tmp_path):
    """`server_source` is what separates a Last.fm scrobble from the Plex ones
    already in the table — without it a bad import could not be unpicked."""
    import core.listening_import.lastfm as mod
    assert mod.SOURCE == "lastfm"
    ev = mod.normalize_lastfm_scrobble({
        "name": "Track", "artist": {"#text": "Artist"},
        "album": {"#text": "Album"}, "date": {"uts": "1700000000"}})
    assert ev is not None
    assert ev["server_source"] == "lastfm"


def test_a_now_playing_row_is_not_imported_as_history():
    """Last.fm returns the currently-playing track with no `date`. Storing it
    would put a row with no real timestamp into the history."""
    import core.listening_import.lastfm as mod
    assert mod.normalize_lastfm_scrobble({
        "name": "Track", "artist": {"#text": "Artist"},
        "@attr": {"nowplaying": "true"}}) is None


# ── the endpoints ───────────────────────────────────────────────────────────

@pytest.fixture(scope="module")
def client():
    import web_server
    web_server.app.config["TESTING"] = True
    with web_server.app.test_client() as c:
        yield c


def test_the_three_routes_are_registered(client):
    import web_server
    rules = {r.rule for r in web_server.app.url_map.iter_rules()}
    for path in ("/api/lastfm/listening-import/status",
                 "/api/lastfm/listening-import/run",
                 "/api/lastfm/listening-import/cancel"):
        assert path in rules, path


def test_status_answers_rather_than_erroring_when_the_importer_is_absent(client, monkeypatch):
    """A 500 here would read as a broken install to someone who simply has not
    set Last.fm up.

    The worker BOOTS in the test environment, so asserting against the live one
    exercises the happy path and proves nothing about the absent case — a
    back-out that made this branch raise passed the earlier version of this
    test. Take the worker away explicitly."""
    import web_server
    monkeypatch.setattr(web_server, "lastfm_import_worker", None)
    r = client.get("/api/lastfm/listening-import/status")
    assert r.status_code == 200, r.get_data(as_text=True)[:200]
    body = r.get_json()
    assert body["success"] is False
    assert body["enabled"] is False
    assert "unavailable" in body["error"]


def test_run_and_cancel_refuse_cleanly_when_the_importer_is_absent(client, monkeypatch):
    """These two DO fail the request — there is nothing to start or stop — but
    with a 400 and a reason, not a traceback."""
    import web_server
    monkeypatch.setattr(web_server, "lastfm_import_worker", None)
    for path in ("/api/lastfm/listening-import/run",
                 "/api/lastfm/listening-import/cancel"):
        r = client.post(path, json={})
        assert r.status_code == 400, path
        assert "unavailable" in r.get_json()["error"], path


def test_status_reports_the_live_worker_when_there_is_one(client):
    r = client.get("/api/lastfm/listening-import/status")
    assert r.status_code == 200
    body = r.get_json()
    assert "enabled" in body
    assert "api_key_configured" in body


def test_status_says_whether_a_blank_username_would_work(client):
    """The importer can ask Last.fm who we are only when the account is
    authorized. Without that a username is required, and the caller needs to
    know which case they are in before pressing run."""
    r = client.get("/api/lastfm/listening-import/status")
    body = r.get_json()
    assert "authenticated_user_available" in body
