"""A member's wish is asked for, not fetched, until an admin approves it.

Reported as: have members' wishlist adds require approval as well (the watchlist
already worked this way).

video_wishlist gained approved / requested_by / requested_by_name, mirroring
video_watchlist. A wish from a profile WITHOUT download rights lands approved=0:
it appears on the wishlist straight away — so the requester can see they asked —
but every ACQUISITION path skips it until an admin releases it.

The whole value of this is in that word EVERY. A single un-gated feed means the
hourly drain quietly downloads something nobody approved, which is the exact
behaviour this is meant to stop. Every acquisition consumer in the codebase funnels
through four DB methods, so those four are the chokepoint:

    movie_wishlist_to_download      the drain + RSS + Search all
    episode_wishlist_to_download    same, for episodes
    wishlist_manual_search_items    'Search now' / the manual picker
    youtube_wishlist_to_download    the YouTube fulfilment worker

approved defaults to 1, so admin wishes, automation rows and every row written
before this column stay live. Nothing that was being fetched stops being fetched.
"""

from __future__ import annotations

import pathlib

import pytest
from flask import Flask, g

from database.video_database import VideoDatabase

ADMIN, ALICE = 1, 7


@pytest.fixture()
def db(tmp_path):
    return VideoDatabase(database_path=str(tmp_path / "video_library.db"))


@pytest.fixture()
def env(tmp_path, monkeypatch):
    import api.video as videoapi
    import core.video.sources as sources
    monkeypatch.setattr(sources, "resolve_video_server", lambda *a, **k: "plex")
    database = VideoDatabase(database_path=str(tmp_path / "video_library.db"))
    videoapi._video_db = database
    app = Flask(__name__)
    app.register_blueprint(videoapi.create_video_blueprint(), url_prefix="/api/video")
    app.config["_perm"] = {"profile_id": ALICE, "is_admin": False,
                           "can_download": False, "name": "Member"}

    @app.before_request
    def _p():
        perm = app.config["_perm"]
        g.profile_id = perm["profile_id"]
        g.is_admin = perm["is_admin"]
        g.can_download = perm["can_download"]
        g.profile_name = perm["name"]
        g.allowed_sides = "both"

    try:
        yield app.test_client(), app, database
    finally:
        videoapi._video_db = None


def _as(app, profile_id, *, is_admin=False, can_download=False, name="Member"):
    app.config["_perm"].update(profile_id=profile_id, is_admin=is_admin,
                               can_download=can_download, name=name)


def _add_movie(c, tmdb_id=550, title="Fight Club"):
    return c.post("/api/video/wishlist/add",
                  json={"movie": {"tmdb_id": tmdb_id, "title": title, "year": 1999}})


# ── EVERY acquisition path skips a pending wish ──────────────────────────────
def test_the_drain_does_not_see_a_pending_movie(db):
    db.add_movie_to_wishlist(550, "Fight Club", approved=False, requested_by=ALICE)
    assert db.movie_wishlist_to_download() == []


def test_the_drain_does_not_see_a_pending_episode(db):
    db.add_episodes_to_wishlist(1396, "Breaking Bad",
                                [{"season_number": 1, "episode_number": 1}],
                                approved=False, requested_by=ALICE)
    assert db.episode_wishlist_to_download() == []


def test_search_now_does_not_see_a_pending_wish(db):
    """The manual path matters as much as the automatic one — otherwise a pending
    wish could be fetched just by pressing a different button."""
    db.add_movie_to_wishlist(550, "Fight Club", approved=False, requested_by=ALICE)
    db.add_episodes_to_wishlist(1396, "Breaking Bad",
                                [{"season_number": 1, "episode_number": 1}],
                                approved=False, requested_by=ALICE)
    assert db.wishlist_manual_search_items("movie", 550) == []
    assert db.wishlist_manual_search_items("show", 1396) == []


def test_the_youtube_worker_does_not_see_a_pending_video(db):
    db.add_videos_to_wishlist({"youtube_id": "c1", "title": "Chan"},
                              [{"youtube_id": "v1", "title": "Vid"}],
                              approved=False, requested_by=ALICE)
    assert db.youtube_wishlist_to_download() == []


def test_approving_releases_it_to_the_drain(db):
    db.add_movie_to_wishlist(550, "Fight Club", approved=False, requested_by=ALICE)
    assert db.approve_wishlist("movie", tmdb_id=550) == 1
    assert [r["tmdb_id"] for r in db.movie_wishlist_to_download()] == [550]


# ── but it is VISIBLE the whole time ─────────────────────────────────────────
def test_a_pending_wish_still_shows_on_the_wishlist(db):
    """The requester has to be able to see that they asked, or the button looks
    broken and they ask again."""
    db.add_movie_to_wishlist(550, "Fight Club", approved=False,
                             requested_by=ALICE, requested_by_name="Member")
    items = db.query_wishlist("movie")["items"]
    assert [i["tmdb_id"] for i in items] == [550]
    assert items[0]["approved"] == 0
    assert items[0]["requested_by_name"] == "Member"
    assert db.wishlist_counts()["movie"] == 1


def test_episode_rows_carry_the_pending_state_too(db):
    db.add_episodes_to_wishlist(1396, "Breaking Bad",
                                [{"season_number": 1, "episode_number": 1}],
                                approved=False, requested_by=ALICE, requested_by_name="Member")
    ep = db.query_wishlist("show")["items"][0]["seasons"][0]["episodes"][0]
    assert ep["approved"] == 0 and ep["requested_by_name"] == "Member"


# ── the default keeps everything that worked, working ────────────────────────
def test_approved_defaults_to_one(db):
    """Admin wishes, automation rows and every row predating the column stay live.
    If this ever flips, a working install silently stops downloading."""
    db.add_movie_to_wishlist(550, "Fight Club")
    db.add_episodes_to_wishlist(1396, "BB", [{"season_number": 1, "episode_number": 1}])
    db.add_videos_to_wishlist({"youtube_id": "c1", "title": "C"}, [{"youtube_id": "v1"}])
    assert len(db.movie_wishlist_to_download()) == 1
    assert len(db.episode_wishlist_to_download()) == 1
    assert len(db.youtube_wishlist_to_download()) == 1


def test_a_pending_re_add_cannot_downgrade_an_approved_wish(db):
    """MAX(approved) on the upsert. Otherwise a member re-wishing something live
    would quietly park it back in the queue."""
    db.add_movie_to_wishlist(550, "Fight Club")
    db.add_movie_to_wishlist(550, "Fight Club", approved=False, requested_by=ALICE)
    assert [r["tmdb_id"] for r in db.movie_wishlist_to_download()] == [550]


def test_approving_twice_is_a_no_op(db):
    db.add_movie_to_wishlist(550, "Fight Club", approved=False, requested_by=ALICE)
    assert db.approve_wishlist("movie", tmdb_id=550) == 1
    assert db.approve_wishlist("movie", tmdb_id=550) == 0


def test_deny_never_touches_a_live_wish(db):
    """A deny aimed at the wrong thing must not delete something already approved."""
    db.add_movie_to_wishlist(550, "Fight Club")          # live
    assert db.deny_wishlist("movie", tmdb_id=550) == 0
    assert db.count_wishlist_rows("movie", tmdb_id=550) == 1


def test_show_scope_approves_every_pending_episode_under_it(db):
    db.add_episodes_to_wishlist(1396, "BB", [{"season_number": 1, "episode_number": n}
                                             for n in (1, 2, 3)],
                                approved=False, requested_by=ALICE)
    assert db.approve_wishlist("show", tmdb_id=1396) == 3
    assert len(db.episode_wishlist_to_download()) == 3


# ── the API ──────────────────────────────────────────────────────────────────
def test_a_member_add_lands_pending_and_an_admin_add_does_not(env):
    c, app, database = env
    _as(app, ALICE)
    assert _add_movie(c, 550).status_code == 200
    assert database.movie_wishlist_to_download() == []

    _as(app, ADMIN, is_admin=True, can_download=True, name="Admin")
    assert _add_movie(c, 603, "The Matrix").status_code == 200
    assert [r["tmdb_id"] for r in database.movie_wishlist_to_download()] == [603]


def test_can_download_is_what_decides_not_is_admin(env):
    """_may_acquire keys on can_download ALONE — a download-disabled admin is
    refused /downloads/grab, so letting is_admin approve here would have been a
    way around that same gate."""
    c, app, database = env
    _as(app, 9, is_admin=False, can_download=True, name="Trusted")
    _add_movie(c, 550)
    assert [r["tmdb_id"] for r in database.movie_wishlist_to_download()] == [550]


def test_a_member_cannot_approve_or_deny(env):
    c, app, database = env
    _as(app, ALICE)
    _add_movie(c, 550)
    assert c.post("/api/video/wishlist/approve",
                  json={"scope": "movie", "tmdb_id": 550}).status_code == 403
    assert c.post("/api/video/wishlist/deny",
                  json={"scope": "movie", "tmdb_id": 550}).status_code == 403
    assert database.movie_wishlist_to_download() == []


def test_an_admin_approves_and_the_drain_picks_it_up(env):
    c, app, database = env
    _as(app, ALICE)
    _add_movie(c, 550)
    _as(app, ADMIN, is_admin=True, can_download=True, name="Admin")
    r = c.post("/api/video/wishlist/approve", json={"scope": "movie", "tmdb_id": 550})
    assert r.status_code == 200 and r.get_json()["approved"] == 1
    assert [x["tmdb_id"] for x in database.movie_wishlist_to_download()] == [550]


def test_deny_removes_the_request(env):
    c, app, database = env
    _as(app, ALICE)
    _add_movie(c, 550)
    _as(app, ADMIN, is_admin=True, can_download=True, name="Admin")
    assert c.post("/api/video/wishlist/deny",
                  json={"scope": "movie", "tmdb_id": 550}).get_json()["removed"] == 1
    assert database.query_wishlist("movie")["items"] == []


def test_approving_nothing_pending_is_a_404_not_a_silent_success(env):
    c, app, database = env
    _as(app, ADMIN, is_admin=True, can_download=True, name="Admin")
    _add_movie(c, 550)                       # already live
    assert c.post("/api/video/wishlist/approve",
                  json={"scope": "movie", "tmdb_id": 550}).status_code == 404


def test_a_member_sees_only_their_own_pending_queue(env):
    """The queue is other people's requests — not a member's business — but their
    own asks are."""
    c, app, database = env
    _as(app, ALICE)
    _add_movie(c, 550)
    database.add_movie_to_wishlist(603, "The Matrix", approved=False, requested_by=99)

    assert c.get("/api/video/wishlist/pending").get_json()["count"] == 1
    _as(app, ADMIN, is_admin=True, can_download=True, name="Admin")
    assert c.get("/api/video/wishlist/pending").get_json()["count"] == 2


def test_a_member_may_still_remove_their_own_pending_wish(env):
    """Asking and then thinking better of it must not need an admin."""
    c, app, database = env
    _as(app, ALICE)
    _add_movie(c, 550)
    r = c.post("/api/video/wishlist/remove", json={"scope": "movie", "tmdb_id": 550})
    assert r.status_code == 200 and r.get_json()["removed"] == 1


# ── the page shows it ────────────────────────────────────────────────────────
_ROOT = pathlib.Path(__file__).resolve().parents[1]


def _js():
    return (_ROOT / "webui" / "static" / "video" / "video-wishlist.js").read_text(encoding="utf-8")


def test_the_wishlist_renders_the_pending_badge():
    js = _js()
    assert "function pendingChrome(" in js
    assert "Awaiting approval" in js


def test_it_reuses_the_watchlist_classes_rather_than_inventing_its_own():
    """The two queues should look the same; a second set of styles is how they
    drift apart."""
    js = _js()
    assert "vwlp-pending-badge" in js and "vwlp-pending-actions" in js


def test_only_an_admin_gets_approve_and_decline():
    js = _js()
    body = js.split("function pendingChrome(", 1)[1].split("\n    }", 1)[0]
    assert "if (!isAdmin()) return { badge: badge, actions: '' };" in body


def test_both_buttons_disable_together():
    """Approve and Decline on the same row is exactly the double-click a pending
    queue invites."""
    js = _js()
    body = js.split("function doApproval(", 1)[1][:900]
    assert "b.disabled = true" in body
