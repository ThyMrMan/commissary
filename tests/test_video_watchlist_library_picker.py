"""The watchlist card's Library picker — the last mile of the Anime siloing fix.

1.9.23 shipped everything needed to say "this show belongs in Anime" except a
way to say it. The column (``video_watchlist.root_folder_id``), the endpoint
(``POST /api/video/watchlist/library``), the validation, and the cascade that
drags queued episodes and the show row along with the choice all landed — and
no control anywhere could reach any of it.

That is the same shape as the Discover block button one release earlier: every
layer complete, the feature unreachable, and no test noticing because each piece
was individually fine. It is worse here, because the window in which the choice
matters is exactly the window in which nothing could express it — a show's
Library is decided by its FIRST download (the import lands in whichever section
the grab was filed under, and the next scan stamps that onto the show row), and
before that first download there is no show row and no detail page to correct.

Two couplings had to be added, and both are pinned here:

  * ``query_watchlist`` didn't return ``root_folder_id``, so a picker could be
    saved to but never rendered in its current state — a control that always
    reads "Default" no matter what you set.
  * ``cardHTML`` had to actually emit the thing.

Behavioural assertions (escaping, the gating, the request shape) live in
``tests/js/video_watchlist_library_picker_harness.mjs`` and run under Node; this
module shells out to that and additionally pins the couplings a JS harness
cannot see — the live SELECT, and the JS↔Flask agreement on the endpoint.
"""

from __future__ import annotations

import re
import shutil
import subprocess
from pathlib import Path

import pytest
from flask import Flask, g

from database.video_database import VideoDatabase

_REPO_ROOT = Path(__file__).resolve().parents[1]
_HARNESS = _REPO_ROOT / "tests" / "js" / "video_watchlist_library_picker_harness.mjs"
_WATCHLIST_JS = _REPO_ROOT / "webui" / "static" / "video" / "video-watchlist.js"
_WATCHLIST_API = _REPO_ROOT / "api" / "video" / "watchlist.py"


def _node_available() -> bool:
    if not shutil.which("node"):
        return False
    try:
        out = subprocess.run(["node", "--version"], capture_output=True, text=True, timeout=15)
        return int(out.stdout.strip().lstrip("v").split(".")[0]) >= 18
    except Exception:
        return False


@pytest.mark.skipif(not _node_available(), reason="node >= 18 not available")
def test_library_picker_behaviour_harness():
    """Who gets the control, what it renders, and the request it sends."""
    result = subprocess.run(
        ["node", str(_HARNESS)], capture_output=True, text=True, timeout=60,
        cwd=str(_REPO_ROOT),
    )
    assert result.returncode == 0, (
        f"watchlist library-picker harness failed:\n{result.stdout}\n{result.stderr}"
    )


# ── the payload the control renders from ────────────────────────────────────

@pytest.fixture
def db(tmp_path):
    import database.video_database as mod
    # Process-level cache: a second VideoDatabase(path) is a no-op without this,
    # so each test would silently share the first test's schema state.
    if hasattr(mod, "_initialized_paths"):
        mod._initialized_paths.clear()
    d = VideoDatabase(str(tmp_path / "v.db"))
    conn = d._get_connection()
    conn.execute("INSERT INTO root_folders (id, path, content_kind, server, server_title, label, sort_order) "
                 "VALUES (2, '/tv', 'show', 'plex', 'TV Shows', 'TV-Shows', 0)")
    conn.execute("INSERT INTO root_folders (id, path, content_kind, server, server_title, label, sort_order, category) "
                 "VALUES (5, '/anime', 'show', 'plex', 'Anime', 'Anime', 1, 'Anime')")
    conn.commit()
    conn.close()
    return d


def _follow(db, tmdb_id, title):
    c = db._get_connection()
    c.execute("INSERT INTO video_watchlist (kind, tmdb_id, title, state) VALUES ('show', ?, ?, 'follow')",
              (tmdb_id, title))
    c.commit(); c.close()


def _own(db, tmdb_id, title, rfid, status="Returning Series"):
    c = db._get_connection()
    c.execute("INSERT INTO shows (tmdb_id, title, root_folder_id, status, server_source, server_id) "
              "VALUES (?, ?, ?, ?, 'plex', ?)", (tmdb_id, title, rfid, status, f"s{tmdb_id}"))
    c.commit(); c.close()


def _one_show(db, tmdb_id):
    items = db.query_watchlist("show")["items"]
    match = [it for it in items if it.get("tmdb_id") == tmdb_id]
    assert match, f"show {tmdb_id} is not on the watchlist at all"
    return match[0]


class TestTheCardCanSeeTheChoice:
    def test_a_chosen_library_reaches_the_card(self, db):
        """The whole point. Without this the picker could be saved to and never
        show what it was set to — every card would read 'Default' forever, which
        is indistinguishable from the save silently failing."""
        _follow(db, 1, "Brand New Anime")
        assert db.set_watchlist_root_folder(1, 5) == 1
        assert _one_show(db, 1)["root_folder_id"] == 5

    def test_no_choice_is_none_not_a_missing_key(self, db):
        """The card reads ``it.root_folder_id``; an absent key and a null one
        both render as 'Default', but only one of them survives a JSON round
        trip predictably. Keep the shape stable."""
        _follow(db, 1, "Undecided")
        item = _one_show(db, 1)
        assert "root_folder_id" in item
        assert item["root_folder_id"] is None

    def test_the_auto_added_airing_shows_carry_the_key_too(self, db):
        """Shows reach this list by two routes — explicit follows and the
        actively-airing library default — built by two separate queries. Only
        one of them had a watchlist row to read from, so the other arm must
        still emit the field or half the cards read ``undefined``."""
        _own(db, 77, "Airing Library Show", 2)
        item = _one_show(db, 77)
        assert "root_folder_id" in item
        assert item["root_folder_id"] is None
        # ...and it is one of the cards the picker deliberately skips: it is
        # already filed, and its detail page reassigns it for real.
        assert item["library_id"] is not None

    def test_clearing_the_choice_reaches_the_card_as_well(self, db):
        """'Default' is a real choice, not just the absence of one — the card
        has to be able to show that it was made."""
        _follow(db, 1, "X")
        db.set_watchlist_root_folder(1, 5)
        db.set_watchlist_root_folder(1, None)
        assert _one_show(db, 1)["root_folder_id"] is None

    def test_a_refused_library_never_shows_as_chosen(self, db):
        """A movie Library is rejected by the setter. The card must not then
        display it as this show's destination."""
        _follow(db, 1, "X")
        c = db._get_connection()
        c.execute("INSERT INTO root_folders (id, path, content_kind, server, server_title, label) "
                  "VALUES (3, '/movies', 'movie', 'plex', 'Movies', 'Movies')")
        c.commit(); c.close()
        assert db.set_watchlist_root_folder(1, 3) == 0
        assert _one_show(db, 1)["root_folder_id"] is None

    def test_the_search_and_paging_path_carries_it(self, db):
        """query_watchlist filters and slices what list_watchlist built; the
        page always goes through it, so the field has to survive that trip."""
        _follow(db, 1, "Zebra Show")
        _follow(db, 2, "Another Show")
        db.set_watchlist_root_folder(1, 5)
        res = db.query_watchlist("show", search="zebra")
        assert [it["title"] for it in res["items"]] == ["Zebra Show"]
        assert res["items"][0]["root_folder_id"] == 5

    def test_people_are_deliberately_left_out(self, db):
        """Only shows have a destination — ``set_watchlist_root_folder`` writes
        ``WHERE kind='show'`` and the card only offers the control for shows.
        A person row carrying the field would advertise a setting that can
        never be set."""
        c = db._get_connection()
        c.execute("INSERT INTO video_watchlist (kind, tmdb_id, title, state) "
                  "VALUES ('person', 500, 'Tom Hanks', 'follow')")
        c.commit(); c.close()
        assert "root_folder_id" not in db.query_watchlist("person")["items"][0]


# ── the wiring a JS harness cannot see ──────────────────────────────────────

def _js() -> str:
    return _WATCHLIST_JS.read_text(encoding="utf-8")


def _fn_body(src: str, name: str) -> str:
    """One indented function out of the page IIFE (closing brace at column 4)."""
    start = src.index(f"    function {name}(")
    end = src.index("\n    }", start)
    return src[start:end]


def test_the_card_actually_emits_the_picker():
    """The regression this file exists for, in one line: everything else can be
    perfect and the feature is still unreachable if nothing renders it."""
    assert "libSlot(it, kind)" in _fn_body(_js(), "cardHTML"), \
        "cardHTML does not emit the Library slot — the picker is unreachable again"


def test_the_slot_is_painted_after_every_render():
    """Slots are emitted empty because the Library registry loads async. If
    render() doesn't fill them, a card only ever gets a cog when the registry
    happens to land second — which on a warm page it never does."""
    assert "paintLibCogs(" in _fn_body(_js(), "render"), \
        "render() never fills the slots it just emitted"


def test_the_registry_is_actually_loaded():
    """``_showLibs`` starts empty and libCogHTML renders nothing below two
    Libraries — so without this call EVERY card silently has no cog, which
    looks exactly like 'this user has one Library'."""
    assert "loadShowLibraries()" in _fn_body(_js(), "init"), \
        "the Library registry is never fetched — no card can ever show a cog"


def test_the_cog_click_does_not_navigate():
    """The cog sits inside the card's ``<a href>``. Without both calls the click
    opens the show's detail page and the picker never appears — the failure
    mode looks like a dead button, not a missing preventDefault."""
    body = _fn_body(_js(), "onGridClick")
    handler = body[body.index("data-vwlp-lib]"):]
    handler = handler[:handler.index("return;")]
    assert "e.preventDefault()" in handler and "e.stopPropagation()" in handler


def test_the_cog_is_handled_before_the_card_open():
    """Same delegated listener handles both. The generic card handler matches
    ANY click inside the card, so it has to come second."""
    body = _fn_body(_js(), "onGridClick")
    assert body.index("data-vwlp-lib]") < body.index("data-vwlp-open]"), \
        "the card-open handler runs first and swallows the cog click"


def test_the_js_and_the_route_agree_on_the_method():
    """The wishlist's equivalent picker PUTs to ``/api/video/wishlist/library``;
    this route is registered POST-only. Copying the neighbouring idiom would
    405 every save, and the JS would report a generic failure."""
    js = _js()
    call = js[js.index("'/api/video/watchlist/library'"):][:400]
    assert "method: 'POST'" in call, "the JS does not POST to /api/video/watchlist/library"

    api = _WATCHLIST_API.read_text(encoding="utf-8")
    route = re.search(r'@bp\.route\("/watchlist/library",\s*methods=\[([^\]]+)\]\)', api)
    assert route, "the /watchlist/library route is gone"
    assert "POST" in route.group(1), f"route accepts {route.group(1)}, the JS sends POST"


def _api_client(tmp_path, monkeypatch, *, is_admin, can_download):
    """The video blueprint with a profile context this test controls."""
    import api.video as videoapi
    import core.video.sources as sources
    import database.video_database as mod
    monkeypatch.setattr(sources, "resolve_video_server", lambda *a, **k: "plex")
    if hasattr(mod, "_initialized_paths"):
        mod._initialized_paths.clear()
    vdb = VideoDatabase(database_path=str(tmp_path / "v.db"))
    videoapi._video_db = vdb
    app = Flask(__name__)
    app.register_blueprint(videoapi.create_video_blueprint(), url_prefix="/api/video")

    @app.before_request
    def _profile():
        g.profile_id = 7
        g.is_admin = is_admin
        g.can_download = can_download
        g.allowed_sides = "both"

    return app.test_client(), vdb, videoapi


@pytest.mark.parametrize("is_admin,can_dl,expect", [
    (True, True, 200),
    # The interesting one. This profile is exactly who the route's own
    # ``_may_acquire()`` check was written to admit.
    (False, True, 403),
    (False, False, 403),
])
def test_who_the_server_actually_lets_save(tmp_path, monkeypatch, is_admin, can_dl, expect):
    """``POST /api/video/watchlist/library`` READS as can_download-gated: its body
    checks ``_may_acquire()`` and refuses with a message about download
    permission. It is not. The blueprint's gate runs first and admin-gates every
    WRITE whose path ends in ``/library`` — a suffix that exists for the Manage
    panel's real library reassignment, and which this path ends in by
    coincidence. A non-admin never reaches the route at all; they get
    "Admin only."

    Pinned functionally rather than by reading the gate, because the UI hides the
    control based on this answer. If the gate is ever relaxed to match the
    route's stated intent, this fails and says so — and the picker should then be
    shown to members with download rights.
    """
    client, vdb, videoapi = _api_client(tmp_path, monkeypatch,
                                        is_admin=is_admin, can_download=can_dl)
    try:
        vdb.save_libraries("plex", [], [
            {"server_title": "TV", "label": "TV-Shows", "path": "/tv"},
            {"server_title": "Anime", "label": "Anime", "path": "/anime"}])
        rid = [r for r in vdb.all_library_rows() if r["content_kind"] == "show"][1]["id"]
        _follow(vdb, 1399, "Brand New Anime")
        res = client.post("/api/video/watchlist/library",
                          json={"tmdb_id": 1399, "root_folder_id": rid})
        assert res.status_code == expect, res.get_json()
        if expect == 403:
            assert res.get_json().get("error") == "Admin only."
    finally:
        videoapi._video_db = None


def test_the_control_is_hidden_from_profiles_the_server_refuses():
    """So the button is never one that fails on every press — the dead end
    ``test_video_detail_manage_is_admin_only`` was written for. Gating on
    download rights ALONE would have shipped exactly that for any member who
    can download."""
    body = _fn_body(_js(), "libSlot")
    assert "mayChooseLibrary()" in body, "libSlot no longer checks who may save"
    chooser = _fn_body(_js(), "mayChooseLibrary")
    assert "isAdmin()" in chooser and "mayGrab()" in chooser, \
        "the picker must be gated on BOTH admin and download rights"


def test_the_picker_guards_its_own_entry_point():
    """Hiding the cog is not the check — the same defence-in-depth the Manage
    panel and the poster modal already have (see
    ``test_video_detail_manage_is_admin_only``). The slot's attributes are plain
    DOM; a stale card, a replayed handler or a console call must not open a
    picker whose every save is refused."""
    assert "if (!mayChooseLibrary()) return;" in _fn_body(_js(), "openLibraryPicker"), \
        "openLibraryPicker opens for anyone who can reach it"


def test_the_select_still_carries_the_column():
    """The card renders its current state from this one column in one SELECT.
    Dropping it from the projection breaks the picker's display without
    breaking a single save — the silent half of the failure."""
    src = (_REPO_ROOT / "database" / "video_database.py").read_text(encoding="utf-8")
    body = src[src.index("def _effective_shows("):]
    body = body[:body.index("\n    def ")]
    assert "w.root_folder_id" in body, \
        "_effective_shows stopped selecting the follow's Library"
