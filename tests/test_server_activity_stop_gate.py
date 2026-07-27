"""Only an admin can end someone else's stream.

/api/server-activity/stop has always checked g.is_admin, but the Stop button in
the Server Activity drawer was rendered for every profile — its only condition
was that the session had a session_key. A Plex or standard user saw the control,
clicked it, and got a 403 toast. The permission was right; the UI disagreed with
it, which reads as a broken button rather than a boundary.

Plex sign-ins matter most here: PLEX_PROFILE_DEFAULTS sets is_admin False, so
every Plex-linked user landed in exactly that state.
"""

from __future__ import annotations

from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parent.parent
_JS = (_ROOT / "webui" / "static" / "server-activity.js").read_text(encoding="utf-8")


# ── the server side: the gate itself ─────────────────────────────────────────
@pytest.fixture()
def client():
    import web_server
    web_server.app.config["TESTING"] = True
    with web_server.app.test_client() as c:
        yield c


def _as(client, monkeypatch, *, profile_id, is_admin):
    """Sign in as a profile. Profile 1 is admin unconditionally; anyone else is
    resolved from the DB, so the non-admin arm needs get_profile stubbed.

    Via monkeypatch, never a bare assignment: web_server.get_database is a module
    global shared by the whole session, and a hand-rolled restore that a failing
    assert skips would poison every later test in the run."""
    import web_server
    with client.session_transaction() as s:
        s["profile_id"] = profile_id
    if profile_id != 1:
        monkeypatch.setattr(web_server, "get_database", lambda: type("_D", (), {
            "get_profile": staticmethod(lambda pid: {
                "id": pid, "name": "Plex User", "is_admin": is_admin,
                "can_download": False, "allowed_sides": "both"})})())


def test_a_non_admin_cannot_stop_a_stream(client, monkeypatch):
    called = {"n": 0}
    monkeypatch.setattr("core.server_activity.stop_session",
                        lambda *a, **k: called.__setitem__("n", called["n"] + 1))
    _as(client, monkeypatch, profile_id=7, is_admin=False)
    r = client.post("/api/server-activity/stop",
                    json={"session_key": "42", "message": "go to bed"})
    assert r.status_code == 403
    # the exact refusal, so an incidental 403 from somewhere else in the
    # stack can't stand in for the gate actually being there
    assert r.get_json() == {"ok": False, "error": "Admin only."}
    # the refusal must happen BEFORE the Plex call, not after
    assert called["n"] == 0


def test_an_admin_still_can(client, monkeypatch):
    seen = {}
    monkeypatch.setattr("core.server_activity.stop_session",
                        lambda key, msg: seen.update(key=key, msg=msg) or {"ok": True})
    with client.session_transaction() as s:
        s["profile_id"] = 1
    r = client.post("/api/server-activity/stop",
                    json={"session_key": "42", "message": "go to bed"})
    assert r.status_code == 200 and r.get_json()["ok"] is True
    assert seen == {"key": "42", "msg": "go to bed"}


def test_the_read_only_views_stay_open_to_everyone(client, monkeypatch):
    """Watching who is streaming is not the same permission as ending it — the
    drawer itself must keep working for a standard user."""
    _as(client, monkeypatch, profile_id=7, is_admin=False)
    for path in ("/api/server-activity", "/api/server-activity/history",
                 "/api/server-activity/stats"):
        assert client.get(path).status_code == 200, path


# ── the client side: the button must agree with the gate ─────────────────────
def test_the_stop_button_is_not_rendered_without_admin():
    card = _JS.split("var stop =", 1)[1].split("// a live equalizer", 1)[0]
    assert "canStopStreams()" in card
    # the old condition was session_key ALONE
    assert "s.session_key\n" not in card


def test_the_action_rechecks_at_click_time():
    """Profiles switch without a page reload, so a card painted for an admin can
    outlive them in an open drawer."""
    body = _JS.split("function openStop", 1)[1].split("document.body.appendChild", 1)[0]
    assert "canStopStreams()" in body


def test_the_check_matches_the_established_idiom():
    """Same shape as video-side.js sideAllowed() / video-downloads-page.js
    canImport(): a typeof guard so script load order can't throw, profile 1 and
    is_admin allowed, and no-profile left permissive because the SERVER is the
    real gate — a UI check that fails closed on a boot race would just hide the
    button from the admin."""
    fn = _JS.split("function canStopStreams", 1)[1].split("}", 1)[0]
    assert "typeof currentProfile !== 'undefined'" in fn
    assert "cp.is_admin" in fn and "cp.id === 1" in fn
    assert "!cp ||" in fn


def test_stopping_is_the_only_mutation_in_the_drawer():
    """If another write endpoint is ever added here it needs its own gate; this
    fails loudly rather than letting one slip in ungated."""
    import re
    posts = set(re.findall(r"fetch\(\s*'(/api/[^']+)'[^)]*method:\s*'POST'", _JS))
    assert posts == {"/api/server-activity/stop"}
