"""Watchlist follows that wait for admin approval.

A profile without download rights may follow a show — the entry appears on the
watchlist immediately so the requester can see it — but it is filed
``approved=0`` and NOTHING acquires it until an admin approves. This is the
middle ground between the two states that existed before: full download rights,
or no watchlist access at all.

There are exactly three routes by which a follow becomes a download, and all
three are pinned here:
  1. the monitor-policy expansion at follow time (/watchlist/add)
  2. calendar_upcoming(watchlist_only=True) → the daily auto-wishlist airing job
  3. list_watchlist() → the people/studio scan handlers

Also covers the access widening that made this reachable: Plex sign-ins now get
allowed_sides='both', so endpoints that were previously unreachable for a
non-download profile had to be gated.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from flask import Flask, g

from database.video_database import VideoDatabase

_ROOT = Path(__file__).resolve().parent.parent
_WL_JS = (_ROOT / "webui" / "static" / "video" / "video-watchlist.js").read_text(encoding="utf-8")
_VIDEO_INIT = (_ROOT / "api" / "video" / "__init__.py").read_text(encoding="utf-8")
_CSS = (_ROOT / "webui" / "static" / "video" / "video-side.css").read_text(encoding="utf-8")


@pytest.fixture()
def db(tmp_path):
    return VideoDatabase(database_path=str(tmp_path / "video_library.db"))


@pytest.fixture()
def app_db(tmp_path, monkeypatch):
    import api.video as videoapi
    import core.video.sources as sources
    monkeypatch.setattr(sources, "resolve_video_server", lambda: "plex")
    d = VideoDatabase(database_path=str(tmp_path / "video_library.db"))
    videoapi._video_db = d
    app = Flask(__name__)
    app.register_blueprint(videoapi.create_video_blueprint(), url_prefix="/api/video")
    persona = {"profile_id": 1, "is_admin": True, "can_download": True,
               "profile_name": "Admin", "allowed_sides": "both"}

    @app.before_request
    def _persona():
        for k, v in persona.items():
            setattr(g, k, v)

    try:
        yield app.test_client(), d, persona
    finally:
        videoapi._video_db = None


def _as_member(persona, pid=5, name="Kid"):
    persona.update({"profile_id": pid, "is_admin": False, "can_download": False,
                    "profile_name": name, "allowed_sides": "both"})


# ── DB layer ─────────────────────────────────────────────────────────────────
def test_a_follow_defaults_to_approved(db):
    """Admin follows, and every row predating the column, stay live."""
    db.add_to_watchlist("show", 10, "Admin Follow")
    assert db.list_watchlist("show")[0]["approved"] is True
    assert db.pending_watchlist_count() == 0


def test_a_pending_follow_records_its_requester_and_monitor(db):
    db.add_to_watchlist("show", 11, "Kid Follow", approved=False, requested_by=5,
                        requested_by_name="Kid", monitor="all")
    row = db.pending_watchlist_entries()[0]
    assert row["requested_by"] == 5 and row["requested_by_name"] == "Kid"
    assert row["monitor"] == "all"
    assert db.pending_watchlist_count() == 1
    assert db.pending_watchlist_count(requested_by=6) == 0   # someone else's ask


def test_a_pending_readd_cannot_downgrade_an_approved_follow(db):
    """MAX() on approved: a member re-adding a show an admin already approved
    must not knock it back into the pending state (a de-facto un-approve)."""
    db.add_to_watchlist("show", 12, "Live Show")
    db.add_to_watchlist("show", 12, "Live Show", approved=False, requested_by=5)
    assert db.list_watchlist("show")[0]["approved"] is True
    assert db.pending_watchlist_count() == 0


def test_approving_returns_the_requested_monitor_and_is_idempotent(db):
    db.add_to_watchlist("show", 13, "Pending Show", approved=False,
                        requested_by=5, monitor="first_season")
    row = db.approve_watchlist_entry("show", 13)
    assert row and row["monitor"] == "first_season"
    assert db.list_watchlist("show")[0]["approved"] is True
    # a second approve is a no-op, so it can't fire a second round of wishlist writes
    assert db.approve_watchlist_entry("show", 13) is None


def test_watchlist_entry_requester_distinguishes_owner_and_state(db):
    db.add_to_watchlist("show", 14, "Mine", approved=False, requested_by=5)
    db.add_to_watchlist("show", 15, "Admins")
    assert db.watchlist_entry_requester("show", 14) == (0, 5)
    assert db.watchlist_entry_requester("show", 15)[0] == 1
    assert db.watchlist_entry_requester("show", 999) is None


def test_upgrading_a_pre_approval_database_works_and_keeps_follows_live(tmp_path):
    """Regression: the index on `approved` initially lived in schema.sql, whose
    executescript runs BEFORE the additive ALTERs — so on any existing install
    startup died with "no such column: approved". Fresh-DB tests can't catch
    that; this builds the OLD table shape first. It also pins the upgrade
    semantics: every follow that predates the column stays approved."""
    import sqlite3
    p = tmp_path / "old.db"
    conn = sqlite3.connect(str(p))
    conn.executescript("""
        CREATE TABLE video_watchlist (
          id INTEGER PRIMARY KEY AUTOINCREMENT, kind TEXT NOT NULL, tmdb_id INTEGER NOT NULL,
          title TEXT NOT NULL, poster_url TEXT, library_id INTEGER,
          source TEXT NOT NULL DEFAULT 'tmdb', source_id TEXT,
          state TEXT NOT NULL DEFAULT 'follow',
          date_added TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP, UNIQUE(kind, tmdb_id));
        INSERT INTO video_watchlist (kind, tmdb_id, title) VALUES ('show', 777, 'Legacy Follow');
    """)
    conn.commit(); conn.close()

    upgraded = VideoDatabase(database_path=str(p))       # raised before the fix
    rows = upgraded.list_watchlist("show")
    assert [(r["title"], r["approved"]) for r in rows] == [("Legacy Follow", True)]
    assert upgraded.pending_watchlist_count() == 0


# ── acquisition path 2: the daily auto-wishlist airing job ───────────────────
def test_an_owned_airing_show_is_unaffected_by_the_approval_gate(db):
    """No-regression pin, NOT a gate test. calendar_upcoming has two arms: the
    explicit follow, and 'an owned show that is still airing'. Only the first is
    gated. An owned airing show keeps flowing to the daily job whatever its
    follow row says — so adding approval can't stall an existing install."""
    show_id = db.upsert_show_tree("plex", {
        "server_id": "s1", "tmdb_id": 20, "title": "Airing Anime",
        "status": "Returning Series",
        "seasons": [{"season_number": 1, "episodes": [
            {"season_number": 1, "episode_number": 1, "title": "Ep1", "air_date": "2026-07-27"}]}]})
    assert show_id
    db.add_to_watchlist("show", 20, "Airing Anime", approved=False, requested_by=5)
    ids = lambda: [r["show_tmdb_id"] for r in db.calendar_upcoming(   # noqa: E731
        "2026-07-01", "2026-08-31", server_source="plex", watchlist_only=True)]
    assert ids() == [20]          # via the airing-default arm, despite being pending
    db.approve_watchlist_entry("show", 20)
    assert ids() == [20]          # and still there once approved


def test_the_explicit_follow_arm_requires_approval(db):
    """An ENDED show is excluded by the airing-default arm, so it can only reach
    the calendar through the explicit-follow arm — which is exactly the arm the
    approval gate guards."""
    db.upsert_show_tree("plex", {
        "server_id": "s2", "tmdb_id": 21, "title": "Ended Show", "status": "Ended",
        "seasons": [{"season_number": 1, "episodes": [
            {"season_number": 1, "episode_number": 1, "title": "Ep1", "air_date": "2026-07-27"}]}]})
    db.add_to_watchlist("show", 21, "Ended Show", approved=False, requested_by=5)
    pending = db.calendar_upcoming("2026-07-01", "2026-08-31", server_source="plex",
                                   watchlist_only=True)
    assert [r["show_tmdb_id"] for r in pending] == []      # gated

    db.approve_watchlist_entry("show", 21)
    live = db.calendar_upcoming("2026-07-01", "2026-08-31", server_source="plex",
                                watchlist_only=True)
    assert [r["show_tmdb_id"] for r in live] == [21]       # flows once approved


# ── acquisition path 3: the people / studio scans ────────────────────────────
def test_people_and_studio_scans_only_see_approved_follows(db):
    db.add_to_watchlist("person", 30, "Pending Actor", approved=False, requested_by=5)
    db.add_to_watchlist("person", 31, "Approved Actor")
    db.add_to_watchlist("studio", 40, "Pending Studio", approved=False, requested_by=5)
    db.add_to_watchlist("studio", 41, "Approved Studio")

    assert [p["tmdb_id"] for p in db.list_watchlist("person", approved_only=True)] == [31]
    assert [s["tmdb_id"] for s in db.list_watchlist("studio", approved_only=True)] == [41]
    # the PAGE still shows both, flagged, so the requester can see their own ask
    assert len(db.list_watchlist("person")) == 2
    assert {p["approved"] for p in db.list_watchlist("person")} == {True, False}


def test_the_scan_handlers_actually_pass_approved_only():
    """Source guard — the gate is only real if the handlers opt in."""
    people = (_ROOT / "core" / "automation" / "handlers"
              / "video_scan_watchlist_people.py").read_text(encoding="utf-8")
    studios = (_ROOT / "core" / "automation" / "handlers"
               / "video_scan_watchlist_studios.py").read_text(encoding="utf-8")
    assert "list_watchlist('person', approved_only=True)" in people
    assert "list_watchlist('studio', approved_only=True)" in studios


# ── acquisition path 1: the API, and the permission edges ────────────────────
def test_a_member_follow_is_filed_pending_and_wishes_nothing(app_db):
    """The headline behaviour: a member CAN follow (they used to get a 403), the
    show lands on the watchlist, and monitor='all' expands nothing yet."""
    client, d, persona = app_db
    _as_member(persona)
    r = client.post("/api/video/watchlist/add",
                    json={"kind": "show", "tmdb_id": 50, "title": "Kid Show", "monitor": "all"})
    body = r.get_json()
    assert r.status_code == 200 and body["success"] is True and body["pending"] is True
    assert body["wished"] == 0
    assert d.wishlist_counts()["episode"] == 0          # nothing acquired
    row = d.pending_watchlist_entries()[0]
    assert row["title"] == "Kid Show" and row["requested_by"] == 5
    assert row["monitor"] == "all"                      # stored for approval, not applied


def test_an_admin_follow_still_acquires_immediately(app_db):
    client, d, _ = app_db
    r = client.post("/api/video/watchlist/add",
                    json={"kind": "show", "tmdb_id": 51, "title": "Admin Show"})
    assert r.get_json()["success"] is True
    assert d.pending_watchlist_count() == 0
    assert d.list_watchlist("show")[0]["approved"] is True


def test_approve_is_admin_only(app_db):
    client, d, persona = app_db
    d.add_to_watchlist("show", 52, "Pending", approved=False, requested_by=5)
    _as_member(persona)
    assert client.post("/api/video/watchlist/approve",
                       json={"kind": "show", "tmdb_id": 52}).status_code == 403
    assert client.post("/api/video/watchlist/deny",
                       json={"kind": "show", "tmdb_id": 52}).status_code == 403
    assert d.pending_watchlist_count() == 1             # untouched


def test_a_member_may_withdraw_only_their_own_pending_follow(app_db):
    """Giving Plex profiles video access made /watchlist/remove reachable for the
    first time; without this a member could un-follow the shared watchlist."""
    client, d, persona = app_db
    d.add_to_watchlist("show", 60, "Admins Live Show")
    d.add_to_watchlist("show", 61, "Someone Elses Ask", approved=False, requested_by=99)
    d.add_to_watchlist("show", 62, "My Own Ask", approved=False, requested_by=5)
    _as_member(persona, pid=5)

    assert client.post("/api/video/watchlist/remove",
                       json={"kind": "show", "tmdb_id": 60}).status_code == 403
    assert client.post("/api/video/watchlist/remove",
                       json={"kind": "show", "tmdb_id": 61}).status_code == 403
    assert client.post("/api/video/watchlist/remove",
                       json={"kind": "show", "tmdb_id": 62}).status_code == 200


def test_members_see_only_their_own_pending_admins_see_all(app_db):
    client, d, persona = app_db
    d.add_to_watchlist("show", 70, "Mine", approved=False, requested_by=5)
    d.add_to_watchlist("show", 71, "Theirs", approved=False, requested_by=99)
    assert client.get("/api/video/watchlist/pending").get_json()["count"] == 2
    _as_member(persona, pid=5)
    body = client.get("/api/video/watchlist/pending").get_json()
    assert body["count"] == 1 and body["pending"][0]["title"] == "Mine"


def test_approving_through_the_api_expands_the_stored_policy(app_db, monkeypatch):
    client, d, _ = app_db
    d.add_to_watchlist("show", 80, "Pending Show", approved=False, requested_by=5, monitor="all")

    import core.video.monitor_policy as mp
    monkeypatch.setattr(mp, "episodes_for_policy",
                        lambda eng, tid, policy, today: [
                            {"season_number": 1, "episode_number": 1, "title": "Ep1"}])
    monkeypatch.setattr("core.video.enrichment.engine.get_video_enrichment_engine",
                        lambda: object())
    body = client.post("/api/video/watchlist/approve",
                       json={"kind": "show", "tmdb_id": 80}).get_json()
    assert body["success"] is True and body["wished"] == 1
    assert d.list_watchlist("show")[0]["approved"] is True


def test_denying_removes_the_pending_follow(app_db):
    client, d, _ = app_db
    d.add_to_watchlist("show", 81, "Unwanted", approved=False, requested_by=5)
    assert client.post("/api/video/watchlist/deny",
                       json={"kind": "show", "tmdb_id": 81}).get_json()["success"] is True
    assert d.pending_watchlist_count() == 0
    # and an admin's approved follow can't be denied out from under them
    d.add_to_watchlist("show", 82, "Live")
    assert client.post("/api/video/watchlist/deny",
                       json={"kind": "show", "tmdb_id": 82}).status_code == 404


# ── the endpoints the Plex access widening newly exposed ─────────────────────
def test_download_and_destructive_endpoints_stay_behind_can_download(app_db):
    """allowed_sides='both' for Plex sign-ins makes the whole video blueprint
    reachable, so anything that acquires or destroys must be gated on
    can_download, not on side access."""
    client, _, persona = app_db
    _as_member(persona)
    for path, payload in (
            ("/api/video/downloads/grab", {"source": "torrent"}),
            ("/api/video/downloads/grab-pack", {}),      # covered by the 'grab' prefix
            ("/api/video/downloads/cancel", {}),
            ("/api/video/downloads/history/clear", {}),
            ("/api/video/wishlist/remove", {}),
            ("/api/video/wishlist/clear", {}),
            ("/api/video/watchlist/approve", {"kind": "show", "tmdb_id": 1}),
            # 'Search now' / 'Search all' start REAL grabs from the shared
            # wishlist and were behind no gate at all.
            ("/api/video/wishlist/search", {"scope": "movie", "tmdb_id": 1}),
            ("/api/video/wishlist/search-all", {}),
    ):
        assert client.post(path, json=payload).status_code == 403, path


def test_a_member_may_still_add_to_the_wishlist(app_db):
    """The counterpart to the list above. Asking is not acquiring — and this was
    blocked, so members could not ask for anything."""
    client, _, persona = app_db
    _as_member(persona)
    r = client.post("/api/video/wishlist/add",
                    json={"movie": {"tmdb_id": 550, "title": "Fight Club", "year": 1999}})
    assert r.status_code == 200 and r.get_json()["success"] is True


def test_the_two_asking_endpoints_are_deliberately_not_in_the_gate():
    """A member may ASK for something; only a downloader may fetch it.

    /watchlist/add is filed pending approval by the route itself.
    /wishlist/add puts a title on the shared wishlist — the ADMIN's automation
    decides whether it is ever acquired. It used to be gated, which left members
    with no way to ask for anything at all.

    Everything that ACQUIRES or destroys must stay in the gate; adding either of
    these back removes the only thing members can do."""
    gate = _VIDEO_INIT.split('can_download", True) and _p(')[1].split("):")[0]
    assert "/api/video/watchlist/add" not in gate
    assert "/api/video/wishlist/add" not in gate
    # …while every acquisition/destructive route stays behind it.
    for guarded in ("/api/video/watchlist/approve", "/api/video/downloads/grab",
                    "/api/video/downloads/retry", "/api/video/downloads/cancel",
                    "/api/video/wishlist/remove", "/api/video/wishlist/clear",
                    # 'Search now' / 'Search all' START GRABS and had no gate at all.
                    "/api/video/wishlist/search"):
        assert guarded in gate, guarded


def test_the_wishlist_search_gate_covers_search_all_by_prefix():
    """_p() is a startswith match, so one entry guards both — but only while the
    paths keep that shared prefix."""
    gate = _VIDEO_INIT.split('can_download", True) and _p(')[1].split("):")[0]
    assert "/api/video/wishlist/search" in gate
    assert "/wishlist/search-all".startswith("/wishlist/search")


def test_plex_defaults_grant_video_but_never_downloads():
    from core.plex_user_auth import PLEX_PROFILE_DEFAULTS
    assert PLEX_PROFILE_DEFAULTS["allowed_sides"] == "both"
    assert PLEX_PROFILE_DEFAULTS["can_download"] is False
    assert PLEX_PROFILE_DEFAULTS["is_admin"] is False


# ── frontend contract ────────────────────────────────────────────────────────
def test_the_watchlist_page_renders_and_wires_the_approval_controls():
    assert "vwlp-pending-badge" in _WL_JS and "Awaiting approval" in _WL_JS
    assert "/api/video/watchlist/' + (deny ? 'deny' : 'approve')" in _WL_JS
    # the buttons live inside the card's <a> — clicking must not navigate
    assert "e.preventDefault(); e.stopPropagation();" in _WL_JS
    # non-admins get the badge but no Approve/Decline pair
    assert "if (!isAdmin()) return { badge: badge, actions: '' };" in _WL_JS
    assert ".vwlp-pending-badge" in _CSS and ".vwlp-approve" in _CSS
