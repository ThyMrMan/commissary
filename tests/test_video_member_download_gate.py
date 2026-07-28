"""A member can ASK for a title. Only a downloader can fetch or cancel one.

Reported as: standard/Plex users could download shows from the wishlist and
cancel downloads, when all they should be able to do is add to the wishlist for
the admin to fetch.

Two separate problems were behind that:

  * /wishlist/search and /wishlist/search-all were behind NO gate at all. Their
    own docstrings say they grab ("the downloads page / badge shows what it
    grabs"), so any signed-in profile with video access could start real
    downloads from the shared wishlist.
  * can_download defaulted ON for admin-created profiles, so a "standard" user
    inherited the right to cancel the admin's downloads. Only Plex-provisioned
    profiles started with it off.

And the inverse: /wishlist/add was BLOCKED for those profiles, so a member had
no way to ask for anything — the opposite of the intent.

The server is the authority here. The frontend also hides these controls, but
that is a courtesy so nothing offers an action that 403s; it is never the check.
"""

from __future__ import annotations

import pathlib

import pytest
from flask import Flask, g

from database.video_database import VideoDatabase


@pytest.fixture()
def client(tmp_path, monkeypatch):
    import api.video as videoapi
    import core.video.sources as sources
    monkeypatch.setattr(sources, "resolve_video_server", lambda *a, **k: "plex")
    videoapi._video_db = VideoDatabase(database_path=str(tmp_path / "video_library.db"))
    app = Flask(__name__)
    app.register_blueprint(videoapi.create_video_blueprint(), url_prefix="/api/video")
    app.config["_perm"] = {"is_admin": False, "can_download": False}

    @app.before_request
    def _p():
        perm = app.config["_perm"]
        g.profile_id = 1 if perm["is_admin"] else 7
        g.is_admin = perm["is_admin"]
        g.can_download = perm["can_download"]
        g.allowed_sides = "both"

    try:
        yield app.test_client(), app
    finally:
        videoapi._video_db = None


def _as(app, **perm):
    app.config["_perm"].update(perm)


# ── what a member must NOT be able to do ─────────────────────────────────────
MEMBER_BLOCKED = [
    ("/api/video/wishlist/search", {"scope": "movie", "tmdb_id": 550}),
    ("/api/video/wishlist/search-all", {}),
    ("/api/video/downloads/cancel", {"id": 1}),
    ("/api/video/downloads/grab", {"title": "X"}),
    ("/api/video/downloads/retry", {"id": 1}),
    ("/api/video/wishlist/remove", {"scope": "movie", "tmdb_id": 550}),
    ("/api/video/wishlist/clear", {"kind": "movie"}),
]


@pytest.mark.parametrize("path,body", MEMBER_BLOCKED)
def test_a_member_cannot_acquire_or_cancel(client, path, body):
    c, app = client
    _as(app, is_admin=False, can_download=False)
    assert c.post(path, json=body).status_code == 403, path


def test_searching_the_wishlist_is_the_one_that_was_open(client):
    """Pinned on its own: these two had no gate at all, which is how a member
    could download from the wishlist despite every other route being covered."""
    c, app = client
    _as(app, is_admin=False, can_download=False)
    assert c.post("/api/video/wishlist/search",
                  json={"scope": "movie", "tmdb_id": 550}).status_code == 403
    assert c.post("/api/video/wishlist/search-all", json={}).status_code == 403


# ── what a member MUST still be able to do ───────────────────────────────────
def test_a_member_can_add_to_the_wishlist(client):
    """The whole point: asking is not acquiring. The admin's automation decides
    whether it is actually fetched."""
    c, app = client
    _as(app, is_admin=False, can_download=False)
    r = c.post("/api/video/wishlist/add",
               json={"movie": {"tmdb_id": 550, "title": "Fight Club", "year": 1999}})
    assert r.status_code == 200 and r.get_json()["success"] is True


def test_a_member_can_still_read_the_wishlist_and_downloads(client):
    c, app = client
    _as(app, is_admin=False, can_download=False)
    assert c.get("/api/video/wishlist?kind=movie").status_code == 200
    assert c.get("/api/video/wishlist/counts").status_code == 200


# ── a granted member, and an admin, are unaffected ───────────────────────────
@pytest.mark.parametrize("path,body", MEMBER_BLOCKED)
def test_granting_can_download_restores_everything(client, path, body):
    """can_download stays a real per-profile switch — this must not have
    quietly become admin-only."""
    c, app = client
    _as(app, is_admin=False, can_download=True)
    assert c.post(path, json=body).status_code != 403, path


def test_an_admin_is_never_blocked(client):
    c, app = client
    _as(app, is_admin=True, can_download=True)
    assert c.post("/api/video/wishlist/search-all", json={}).status_code != 403


# ── the default for a NEW profile ────────────────────────────────────────────
def test_a_new_standard_profile_cannot_download_by_default():
    """It used to default ON, so the permission had to be noticed and switched
    off to be safe. Granting is the deliberate act now."""
    import inspect
    from database.music_database import MusicDatabase
    sig = inspect.signature(MusicDatabase.create_profile)
    assert sig.parameters["can_download"].default is None
    src = inspect.getsource(MusicDatabase.create_profile)
    assert "can_download = bool(is_admin)" in src


def test_an_explicit_choice_is_still_honoured():
    """Plex provisioning passes False explicitly; a caller that means True must
    still get True."""
    import inspect
    from database.music_database import MusicDatabase
    src = inspect.getsource(MusicDatabase.create_profile)
    assert "if can_download is None:" in src


def test_plex_provisioning_still_starts_locked_down():
    from core.plex_user_auth import PLEX_PROFILE_DEFAULTS
    assert PLEX_PROFILE_DEFAULTS["can_download"] is False
    assert PLEX_PROFILE_DEFAULTS["is_admin"] is False


# ── the UI stops offering what the server refuses ────────────────────────────
_ROOT = pathlib.Path(__file__).resolve().parents[1]


def _js(rel):
    return (_ROOT / "webui" / "static" / "video" / rel).read_text(encoding="utf-8")


def test_the_wishlist_hides_every_grab_control():
    js = _js("video-wishlist.js")
    assert "function mayGrab()" in js
    # Search now, manual pick, per-season hunt, and Search-all.
    assert js.count("mayGrab()") >= 5


def test_the_downloads_page_hides_cancel():
    js = _js("video-downloads-page.js")
    assert "function mayGrab()" in js
    assert js.count("mayGrab()") >= 5


def test_the_ui_defers_to_the_shared_helper_not_its_own_rule():
    """Re-deriving the rule per page is how two pages end up disagreeing about
    who may download."""
    for rel in ("video-wishlist.js", "video-downloads-page.js"):
        js = _js(rel)
        assert "canDownload" in js
        assert "is_admin" not in js.split("function mayGrab()")[1][:200]
