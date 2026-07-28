"""You may take back what you asked for. Nothing else.

Reported as: standard/Plex users should only be able to remove wishlisted media
they added themselves, not media other users wished for.

The wishlist is a single shared list — one row per movie, one per (show, season,
episode) — so "whose is it" has to be recorded at add time. video_wishlist gained
added_by_profile_id for that. It is written on INSERT only: re-adding a title
someone else already wished must not hand their row to the re-adder, which would
otherwise be a two-click way to delete another member's request.

NULL means nobody's personal wish — added by automation (the watchlist scan,
collections, RSS, a subscription import) or before the column existed. Those are
a downloader's to clear, not a member's.

Admins and any profile with can_download are unrestricted: the shared wishlist is
theirs to manage. Clearing a whole tab stays theirs alone — it empties everyone's
requests at once, so no amount of ownership makes it a member's action.
"""

from __future__ import annotations

import pathlib

import pytest
from flask import Flask, g

from database.video_database import VideoDatabase

ADMIN, ALICE, BOB = 1, 7, 9


@pytest.fixture()
def env(tmp_path, monkeypatch):
    import api.video as videoapi
    import core.video.sources as sources
    monkeypatch.setattr(sources, "resolve_video_server", lambda *a, **k: "plex")
    db = VideoDatabase(database_path=str(tmp_path / "video_library.db"))
    videoapi._video_db = db
    app = Flask(__name__)
    app.register_blueprint(videoapi.create_video_blueprint(), url_prefix="/api/video")
    app.config["_perm"] = {"profile_id": ALICE, "is_admin": False, "can_download": False}

    @app.before_request
    def _p():
        perm = app.config["_perm"]
        g.profile_id = perm["profile_id"]
        g.is_admin = perm["is_admin"]
        g.can_download = perm["can_download"]
        g.allowed_sides = "both"

    try:
        yield app.test_client(), app, db
    finally:
        videoapi._video_db = None


def _as(app, profile_id, *, is_admin=False, can_download=False):
    app.config["_perm"].update(profile_id=profile_id, is_admin=is_admin,
                               can_download=can_download)


def _add_movie(c, tmdb_id=550, title="Fight Club"):
    return c.post("/api/video/wishlist/add",
                  json={"movie": {"tmdb_id": tmdb_id, "title": title, "year": 1999}})


def _add_eps(c, tmdb_id=1396, title="Breaking Bad", eps=((1, 1), (1, 2))):
    return c.post("/api/video/wishlist/add", json={
        "show": {"tmdb_id": tmdb_id, "title": title},
        "episodes": [{"season_number": s, "episode_number": e} for s, e in eps]})


def _owner(db, tmdb_id, kind="movie"):
    conn = db._get_connection()
    try:
        rows = conn.execute(
            "SELECT added_by_profile_id a FROM video_wishlist WHERE kind=? AND tmdb_id=?",
            (kind, tmdb_id)).fetchall()
        return [r["a"] for r in rows]
    finally:
        conn.close()


# ── the add records who asked ────────────────────────────────────────────────
def test_an_add_stamps_the_profile_that_made_it(env):
    c, app, db = env
    _as(app, ALICE)
    assert _add_movie(c).status_code == 200
    assert _owner(db, 550) == [ALICE]


def test_automation_adds_belong_to_nobody(env):
    """The drain, collections and the watchlist scan call the DB directly with no
    request context — those rows must stay unowned, not fall to profile 1."""
    c, app, db = env
    db.add_movie_to_wishlist(603, "The Matrix", year=1999)
    assert _owner(db, 603) == [None]


# ── a member removes their own, and only their own ───────────────────────────
def test_a_member_can_remove_what_they_added(env):
    c, app, db = env
    _as(app, ALICE)
    _add_movie(c)
    r = c.post("/api/video/wishlist/remove", json={"scope": "movie", "tmdb_id": 550})
    assert r.status_code == 200 and r.get_json()["removed"] == 1
    assert _owner(db, 550) == []


def test_a_member_cannot_remove_someone_elses(env):
    c, app, db = env
    _as(app, ALICE)
    _add_movie(c)
    _as(app, BOB)
    r = c.post("/api/video/wishlist/remove", json={"scope": "movie", "tmdb_id": 550})
    assert r.status_code == 403
    assert "added yourself" in r.get_json()["error"]
    assert _owner(db, 550) == [ALICE], "Bob's refused remove must leave the row alone"


def test_a_member_cannot_remove_an_unowned_row(env):
    """Automation's rows aren't up for grabs just because nobody claimed them."""
    c, app, db = env
    db.add_movie_to_wishlist(603, "The Matrix", year=1999)
    _as(app, ALICE)
    r = c.post("/api/video/wishlist/remove", json={"scope": "movie", "tmdb_id": 603})
    assert r.status_code == 403
    assert _owner(db, 603) == [None]


def test_re_adding_does_not_steal_ownership(env):
    """The escalation this guards: wish for a title someone else already wished,
    then delete 'your' row. Ownership is set on INSERT only, so the upsert leaves
    Alice's name on it and Bob's remove is still refused."""
    c, app, db = env
    _as(app, ALICE)
    _add_movie(c)
    _as(app, BOB)
    assert _add_movie(c).status_code == 200          # idempotent upsert, not a new row
    assert _owner(db, 550) == [ALICE]
    assert c.post("/api/video/wishlist/remove",
                  json={"scope": "movie", "tmdb_id": 550}).status_code == 403


def test_removing_something_that_is_not_there_is_not_a_permission_error(env):
    """An ownership-scoped delete reports 0 rows for both 'not yours' and
    'nothing there'. Only the first is a 403."""
    c, app, db = env
    _as(app, ALICE)
    r = c.post("/api/video/wishlist/remove", json={"scope": "movie", "tmdb_id": 999999})
    assert r.status_code == 200 and r.get_json()["removed"] == 0


# ── episode scopes ───────────────────────────────────────────────────────────
def test_a_member_removes_only_their_own_episodes_of_a_shared_show(env):
    c, app, db = env
    _as(app, ALICE)
    _add_eps(c, eps=((1, 1), (1, 2)))
    _as(app, BOB)
    _add_eps(c, eps=((1, 3),))
    _as(app, ALICE)
    r = c.post("/api/video/wishlist/remove", json={"scope": "show", "tmdb_id": 1396})
    assert r.status_code == 200 and r.get_json()["removed"] == 2
    assert _owner(db, 1396, "episode") == [BOB], "Bob's episode must survive Alice's remove"


def test_season_scope_is_ownership_scoped_too(env):
    c, app, db = env
    _as(app, ALICE)
    _add_eps(c, eps=((1, 1),))
    _as(app, BOB)
    _add_eps(c, eps=((2, 1),))
    _as(app, ALICE)
    r = c.post("/api/video/wishlist/remove",
               json={"scope": "season", "tmdb_id": 1396, "season_number": 2})
    assert r.status_code == 403
    assert _owner(db, 1396, "episode") == [ALICE, BOB]


# ── the unrestricted profiles ────────────────────────────────────────────────
def test_an_admin_may_remove_anyone_s_wish(env):
    c, app, db = env
    _as(app, ALICE)
    _add_movie(c)
    _as(app, ADMIN, is_admin=True, can_download=True)
    assert c.post("/api/video/wishlist/remove",
                  json={"scope": "movie", "tmdb_id": 550}).status_code == 200
    assert _owner(db, 550) == []


def test_can_download_is_what_grants_it_not_admin(env):
    """A non-admin WITH download rights manages the shared wishlist — the switch
    is can_download, so granting it doesn't require making someone an admin."""
    c, app, db = env
    _as(app, ALICE)
    _add_movie(c)
    _as(app, BOB, can_download=True)
    assert c.post("/api/video/wishlist/remove",
                  json={"scope": "movie", "tmdb_id": 550}).status_code == 200


def test_clear_all_clears_only_the_members_own(env):
    """'Clear all' means 'clear all of mine' for a member. A bulk button must not
    be a way around the per-item ownership check — otherwise the × on someone
    else's row is refused while one click above it wipes the same row."""
    c, app, db = env
    _as(app, ALICE)
    _add_movie(c, 550, "Fight Club")
    _as(app, BOB)
    _add_movie(c, 603, "The Matrix")
    db.add_movie_to_wishlist(680, "Pulp Fiction")      # automation's, owned by nobody

    _as(app, ALICE)
    r = c.post("/api/video/wishlist/clear", json={"kind": "movie"})
    assert r.status_code == 200
    body = r.get_json()
    assert body["removed"] == 1 and body["scoped"] is True
    assert body["left"] == 2, "Bob's and automation's rows must survive"
    assert _owner(db, 550) == [] and _owner(db, 603) == [BOB] and _owner(db, 680) == [None]


def test_clear_all_still_empties_the_tab_for_a_manager(env):
    c, app, db = env
    _as(app, ALICE)
    _add_movie(c, 550, "Fight Club")
    db.add_movie_to_wishlist(680, "Pulp Fiction")
    _as(app, ADMIN, is_admin=True, can_download=True)
    body = c.post("/api/video/wishlist/clear", json={"kind": "movie"}).get_json()
    assert body["removed"] == 2 and body["scoped"] is False
    assert db.wishlist_counts()["movie"] == 0


def test_clear_reports_when_none_of_it_was_yours(env):
    """Reporting success over a list that still has every item in it reads as a
    broken button — the page needs to know it cleared nothing of theirs."""
    c, app, db = env
    db.add_movie_to_wishlist(680, "Pulp Fiction")
    _as(app, ALICE)
    body = c.post("/api/video/wishlist/clear", json={"kind": "movie"}).get_json()
    assert body["removed"] == 0 and body["scoped"] is True and body["left"] == 1


def test_clear_scopes_episodes_and_youtube_the_same_way(env):
    c, app, db = env
    _as(app, ALICE)
    _add_eps(c, eps=((1, 1),))
    _add_video(c, "vid1")
    _as(app, BOB)
    _add_eps(c, eps=((1, 2),))
    _add_video(c, "vid2")

    _as(app, ALICE)
    assert c.post("/api/video/wishlist/clear", json={"kind": "show"}).get_json()["removed"] == 1
    assert c.post("/api/video/wishlist/clear", json={"kind": "youtube"}).get_json()["removed"] == 1
    assert _owner(db, 1396, "episode") == [BOB]
    conn = db._get_connection()
    try:
        left = [x["source_id"] for x in conn.execute(
            "SELECT source_id FROM video_wishlist WHERE kind='video'")]
    finally:
        conn.close()
    assert left == ["vid2"]


def test_clear_wishlist_is_unrestricted_by_default(tmp_path):
    """Automation and the repair passes call it with no profile — they must keep
    emptying the tab outright."""
    db = VideoDatabase(database_path=str(tmp_path / "v.db"))
    db.add_movie_to_wishlist(550, "Fight Club", added_by_profile_id=ALICE)
    db.add_movie_to_wishlist(603, "The Matrix")
    assert db.clear_wishlist("movie") == 2


# ── YouTube rides the same table and the same rule ───────────────────────────
def _add_video(c, vid="vid1", cid="chan1"):
    return c.post("/api/video/youtube/wishlist/add", json={
        "channel": {"youtube_id": cid, "title": "A Channel"},
        "videos": [{"youtube_id": vid, "title": "A Video"}]})


def test_a_member_can_remove_their_own_wished_video(env):
    c, app, db = env
    _as(app, ALICE)
    assert _add_video(c).status_code == 200
    r = c.post("/api/video/youtube/wishlist/remove",
               json={"scope": "video", "source_id": "vid1"})
    assert r.status_code == 200 and r.get_json()["removed"] == 1


def test_a_member_cannot_remove_someone_elses_wished_video(env):
    c, app, db = env
    _as(app, ALICE)
    _add_video(c)
    _as(app, BOB)
    r = c.post("/api/video/youtube/wishlist/remove",
               json={"scope": "video", "source_id": "vid1"})
    assert r.status_code == 403 and "added yourself" in r.get_json()["error"]


def test_channel_scope_removes_only_the_members_own_videos(env):
    c, app, db = env
    _as(app, ALICE)
    _add_video(c, "vid1")
    _as(app, BOB)
    _add_video(c, "vid2")
    _as(app, ALICE)
    r = c.post("/api/video/youtube/wishlist/remove",
               json={"scope": "channel", "source_id": "chan1"})
    assert r.status_code == 200 and r.get_json()["removed"] == 1
    conn = db._get_connection()
    try:
        left = [x["source_id"] for x in conn.execute(
            "SELECT source_id FROM video_wishlist WHERE kind='video'")]
    finally:
        conn.close()
    assert left == ["vid2"]


# ── the DB layer on its own ──────────────────────────────────────────────────
def test_only_profile_id_none_means_no_restriction(tmp_path):
    db = VideoDatabase(database_path=str(tmp_path / "v.db"))
    db.add_movie_to_wishlist(550, "Fight Club", added_by_profile_id=ALICE)
    assert db.remove_from_wishlist("movie", tmdb_id=550, only_profile_id=BOB) == 0
    assert db.remove_from_wishlist("movie", tmdb_id=550, only_profile_id=None) == 1


def test_count_ignores_ownership(tmp_path):
    """It's what lets the API say 'not yours' instead of a silent no-op."""
    db = VideoDatabase(database_path=str(tmp_path / "v.db"))
    db.add_movie_to_wishlist(550, "Fight Club", added_by_profile_id=ALICE)
    assert db.count_wishlist_rows("movie", tmdb_id=550) == 1
    assert db.count_wishlist_rows("movie", tmdb_id=551) == 0


def test_a_bad_scope_removes_nothing(tmp_path):
    db = VideoDatabase(database_path=str(tmp_path / "v.db"))
    db.add_movie_to_wishlist(550, "Fight Club", added_by_profile_id=ALICE)
    assert db.remove_from_wishlist("everything", tmdb_id=550) == 0
    assert db.remove_from_wishlist("season", tmdb_id=550, season_number=None) == 0
    assert db.count_wishlist_rows("movie", tmdb_id=550) == 1


# ── the list carries ownership so the page can hide the × ────────────────────
def test_the_wishlist_query_reports_who_added_each_row(tmp_path):
    db = VideoDatabase(database_path=str(tmp_path / "v.db"))
    db.add_movie_to_wishlist(550, "Fight Club", added_by_profile_id=ALICE)
    db.add_movie_to_wishlist(603, "The Matrix")
    got = {i["tmdb_id"]: i["added_by_profile_id"] for i in db.query_wishlist("movie")["items"]}
    assert got == {550: ALICE, 603: None}


def test_episode_rows_carry_it_too(tmp_path):
    db = VideoDatabase(database_path=str(tmp_path / "v.db"))
    db.add_episodes_to_wishlist(1396, "Breaking Bad", [{"season_number": 1, "episode_number": 1}],
                                added_by_profile_id=BOB)
    show = db.query_wishlist("show")["items"][0]
    assert show["seasons"][0]["episodes"][0]["added_by_profile_id"] == BOB


def test_youtube_rows_carry_it_too(tmp_path):
    db = VideoDatabase(database_path=str(tmp_path / "v.db"))
    db.add_videos_to_wishlist({"youtube_id": "c1", "title": "Chan"},
                              [{"youtube_id": "v1", "title": "Vid", "published_at": "2024-01-01"}],
                              added_by_profile_id=ALICE)
    ch = db.query_youtube_wishlist()["items"][0]
    assert ch["seasons"][0]["episodes"][0]["added_by_profile_id"] == ALICE


# ── the page stops offering what the server refuses ──────────────────────────
_ROOT = pathlib.Path(__file__).resolve().parents[1]


def _js(rel):
    return (_ROOT / "webui" / "static" / "video" / rel).read_text(encoding="utf-8")


def test_the_wishlist_gates_every_remove_control_on_ownership():
    js = _js("video-wishlist.js")
    assert "function mayRemove(" in js and "function mayRemoveAll(" in js
    # movie ×, episode ×, season ×, show/channel ×
    assert js.count("mayRemove(") + js.count("mayRemoveAll(") >= 6


def test_a_bulk_remove_only_appears_when_every_row_under_it_is_the_viewers():
    """A season/show × that would silently remove only some of what it covers is
    worse than no button — the per-item × still handles the viewer's own rows."""
    js = _js("video-wishlist.js")
    body = js.split("function mayRemoveAll(")[1][:300]
    assert ".every(mine)" in body


def test_search_all_stays_hidden_for_a_member():
    """updateClearBtn runs on every load — it used to re-show Search all after
    the wire-time gate had hidden it. Clear-all is NOT gated here: it is
    ownership-scoped server-side, so it stays available and clears the viewer's
    own titles."""
    js = _js("video-wishlist.js")
    body = js.split("function updateClearBtn()")[1].split("\n    }")[0]
    assert "sa.hidden" in body and "!mayGrab()" in body.split("sa.hidden")[1]
    assert "btn.hidden = !has;" in body, "Clear-all must not be hidden from members"


def test_the_clear_prompt_says_what_it_will_actually_do():
    """A member reading 'Remove ALL movies' would think they were about to wipe
    everyone's requests. The confirm and the toast both have to reflect scope."""
    js = _js("video-wishlist.js")
    body = js.split("function clearAll()")[1].split("\n    // ── remove")[0]
    assert "var scoped = !mayGrab();" in body
    assert "YOU added" in body and "Clear mine" in body
    assert "res.left" in body, "the toast must say what was left in place"


def test_the_failing_hub_is_hidden_for_a_member():
    """The ⚠ Failing chip filters the list down to what keeps failing so you can
    re-search, manually pick or drop each one. A member has none of those, so the
    filter leads to a view with nothing to act on. It is client-side only, so it
    never 403s — it just goes nowhere, which is why it needs its own gate."""
    js = _js("video-wishlist.js")
    body = js.split("function _updateFailingChip(")[1].split("\n    }")[0]
    assert "!mayGrab()" in body


def test_the_failing_filter_cannot_be_left_stuck_on():
    """With the toggle hidden, a filter still set would be a filtered list with no
    way back out."""
    js = _js("video-wishlist.js")
    assert "if (!mayGrab()) state.failingOnly = false;" in js


def test_the_per_episode_search_now_is_gated():
    """It is built inline rather than through huntBtn(), so it needed its own
    mayGrab() — it was the last Search-now control a member could still click."""
    js = _js("video-wishlist.js")
    assert "st === 'downloading' || !mayGrab()" in js
