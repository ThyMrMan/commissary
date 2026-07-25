"""Purchased-tracking: a durable purchase record, separate from the
"to be purchased" shopping list (tests/test_to_be_purchased.py, unchanged).

Covers the purchased_at migration, Database.mark_tracks_purchased /
unmark_tracks_purchased / get_purchased_albums, and the three new HTTP
endpoints (mark-purchased, unmark-purchased, purchased list) — including the
full round trip from "on the shopping list" to "purchased" to "unmarked".
"""

from __future__ import annotations

import os
import tempfile
from pathlib import Path

import pytest

from database.music_database import MusicDatabase


@pytest.fixture
def db(tmp_path: Path) -> MusicDatabase:
    return MusicDatabase(database_path=str(tmp_path / "purchased.db"))


def _insert_track(db: MusicDatabase, *, track_id: str, title: str, to_be_purchased: int = 0,
                  track_number: int = 1, album_id: str = "a1", artist_id: str = "ar1",
                  artist_name: str = "Test Artist", album_title: str = "Test Album") -> None:
    conn = db._get_connection()
    cur = conn.cursor()
    cur.execute("INSERT OR IGNORE INTO artists (id, name) VALUES (?, ?)", (artist_id, artist_name))
    cur.execute("INSERT OR IGNORE INTO albums (id, artist_id, title) VALUES (?, ?, ?)",
               (album_id, artist_id, album_title))
    cur.execute(
        "INSERT INTO tracks (id, album_id, artist_id, title, track_number, to_be_purchased) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        (track_id, album_id, artist_id, title, track_number, to_be_purchased),
    )
    conn.commit()
    conn.close()


# ── Schema migration ────────────────────────────────────────────────────────

def test_purchased_at_column_exists_after_init(db: MusicDatabase):
    conn = db._get_connection()
    cols = {row[1] for row in conn.execute("PRAGMA table_info(tracks)")}
    conn.close()
    assert "purchased_at" in cols


def test_migration_is_idempotent(tmp_path: Path):
    path = str(tmp_path / "idempotent.db")
    MusicDatabase(database_path=path)
    MusicDatabase(database_path=path)   # re-init against the same file must not raise
    db2 = MusicDatabase(database_path=path)
    conn = db2._get_connection()
    cols = [row[1] for row in conn.execute("PRAGMA table_info(tracks)")]
    conn.close()
    assert cols.count("purchased_at") == 1


def test_purchased_at_not_in_editable_fields_whitelist(db: MusicDatabase):
    """purchased_at must never be settable to an arbitrary client-supplied
    value via the generic track-update endpoint — only through
    mark_tracks_purchased (server-stamped CURRENT_TIMESTAMP)."""
    assert "purchased_at" not in db.TRACK_EDITABLE_FIELDS


# ── mark_tracks_purchased / unmark_tracks_purchased ─────────────────────────

def test_mark_single_track_purchased(db: MusicDatabase):
    _insert_track(db, track_id="t1", title="Song", to_be_purchased=1)
    result = db.mark_tracks_purchased(["t1"])
    assert result == {"success": True, "updated": 1}

    conn = db._get_connection()
    row = conn.execute("SELECT to_be_purchased, purchased_at FROM tracks WHERE id='t1'").fetchone()
    conn.close()
    assert row["to_be_purchased"] == 0
    assert row["purchased_at"] is not None


def test_mark_whole_album_purchased_including_never_flagged_track(db: MusicDatabase):
    """Album-level marking must cover EVERY track in the album, including
    ones that were never on the to-be-purchased shopping list (e.g.
    manually imported) — confirmed scope from the feature request."""
    _insert_track(db, track_id="t1", title="Flagged", to_be_purchased=1, track_number=1)
    _insert_track(db, track_id="t2", title="Never Flagged", to_be_purchased=0, track_number=2)

    result = db.mark_tracks_purchased(["t1", "t2"])
    assert result == {"success": True, "updated": 2}

    conn = db._get_connection()
    rows = {r["id"]: dict(r) for r in conn.execute("SELECT id, to_be_purchased, purchased_at FROM tracks")}
    conn.close()
    assert rows["t1"]["purchased_at"] is not None
    assert rows["t2"]["purchased_at"] is not None
    assert rows["t2"]["to_be_purchased"] == 0


def test_mark_tracks_purchased_empty_list(db: MusicDatabase):
    assert db.mark_tracks_purchased([]) == {"success": False, "error": "No tracks specified"}


def test_unmark_clears_purchased_at_without_restoring_to_be_purchased(db: MusicDatabase):
    _insert_track(db, track_id="t1", title="Song", to_be_purchased=1)
    db.mark_tracks_purchased(["t1"])

    result = db.unmark_tracks_purchased(["t1"])
    assert result == {"success": True, "updated": 1}

    conn = db._get_connection()
    row = conn.execute("SELECT to_be_purchased, purchased_at FROM tracks WHERE id='t1'").fetchone()
    conn.close()
    assert row["purchased_at"] is None
    # NOT silently re-added to the shopping list — a clean undo, not "put it back".
    assert row["to_be_purchased"] == 0


def test_unmark_tracks_purchased_empty_list(db: MusicDatabase):
    assert db.unmark_tracks_purchased([]) == {"success": False, "error": "No tracks specified"}


# ── get_purchased_albums ─────────────────────────────────────────────────────

def test_get_purchased_albums_empty(db: MusicDatabase):
    result = db.get_purchased_albums()
    assert result["albums"] == []
    assert result["pagination"]["total_count"] == 0


def test_get_purchased_albums_groups_by_album(db: MusicDatabase):
    _insert_track(db, track_id="t1", title="Song 1", track_number=1)
    _insert_track(db, track_id="t2", title="Song 2", track_number=2)
    db.mark_tracks_purchased(["t1", "t2"])

    result = db.get_purchased_albums()
    assert len(result["albums"]) == 1
    album = result["albums"][0]
    assert album["album_title"] == "Test Album"
    assert album["artist_name"] == "Test Artist"
    assert album["total_track_count"] == 2
    assert album["purchased_count"] == 2
    assert [t["id"] for t in album["tracks"]] == ["t1", "t2"]   # ordered by track_number


def test_get_purchased_albums_partial_purchase_count(db: MusicDatabase):
    """An album with only SOME tracks purchased still shows, with an
    accurate purchased_count < total_track_count."""
    _insert_track(db, track_id="t1", title="Bought", track_number=1)
    _insert_track(db, track_id="t2", title="Not Bought", track_number=2)
    db.mark_tracks_purchased(["t1"])

    result = db.get_purchased_albums()
    album = result["albums"][0]
    assert album["purchased_count"] == 1
    assert album["total_track_count"] == 2
    assert [t["id"] for t in album["tracks"]] == ["t1"]


def test_get_purchased_albums_search_matches_title_artist_or_album(db: MusicDatabase):
    _insert_track(db, track_id="t1", title="Alpha Song", artist_id="ar1", artist_name="Zeta Artist",
                  album_id="a1", album_title="Gamma Album")
    _insert_track(db, track_id="t2", title="Beta Song", artist_id="ar2", artist_name="Other Artist",
                  album_id="a2", album_title="Other Album")
    db.mark_tracks_purchased(["t1", "t2"])

    by_title = db.get_purchased_albums(search="alpha")
    assert [a["album_title"] for a in by_title["albums"]] == ["Gamma Album"]

    by_artist = db.get_purchased_albums(search="zeta")
    assert [a["album_title"] for a in by_artist["albums"]] == ["Gamma Album"]

    by_album = db.get_purchased_albums(search="gamma")
    assert [a["album_title"] for a in by_album["albums"]] == ["Gamma Album"]

    no_match = db.get_purchased_albums(search="nonexistent")
    assert no_match["albums"] == []


def test_get_purchased_albums_pagination(db: MusicDatabase):
    for i in range(5):
        _insert_track(db, track_id=f"t{i}", title=f"Song {i}", album_id=f"a{i}",
                      album_title=f"Album {i}", artist_id="ar1")
        db.mark_tracks_purchased([f"t{i}"])

    page1 = db.get_purchased_albums(page=1, limit=2)
    assert len(page1["albums"]) == 2
    assert page1["pagination"] == {
        "page": 1, "limit": 2, "total_count": 5, "total_pages": 3,
        "has_prev": False, "has_next": True,
    }

    page3 = db.get_purchased_albums(page=3, limit=2)
    assert len(page3["albums"]) == 1
    assert page3["pagination"]["has_next"] is False
    assert page3["pagination"]["has_prev"] is True


# ── HTTP endpoints ───────────────────────────────────────────────────────────

_TMP = tempfile.mkdtemp(prefix="soulsync-testdb-purchased-")
os.environ["DATABASE_PATH"] = os.path.join(_TMP, "http.db")
os.environ["SOULSYNC_TEST_DB_READY"] = "1"
web_server = pytest.importorskip("web_server")


@pytest.fixture
def client():
    return web_server.app.test_client()


def test_mark_purchased_endpoint(client):
    wdb = web_server.get_database()
    _insert_track(wdb, track_id="pwt1", title="Endpoint Song", to_be_purchased=1,
                  artist_id="pwar1", album_id="pwa1")

    r = client.post("/api/library/tracks/mark-purchased", json={"track_ids": ["pwt1"]})
    assert r.status_code == 200 and r.get_json()["success"] is True

    purchased = client.get("/api/library/purchased").get_json()
    assert any(a["album_id"] == "pwa1" for a in purchased["albums"])


def test_mark_purchased_endpoint_requires_track_ids(client):
    r = client.post("/api/library/tracks/mark-purchased", json={})
    assert r.status_code == 400 and r.get_json()["success"] is False


def test_unmark_purchased_endpoint(client):
    wdb = web_server.get_database()
    _insert_track(wdb, track_id="pwt2", title="Undo Song", to_be_purchased=1,
                  artist_id="pwar2", album_id="pwa2")
    client.post("/api/library/tracks/mark-purchased", json={"track_ids": ["pwt2"]})

    r = client.post("/api/library/tracks/unmark-purchased", json={"track_ids": ["pwt2"]})
    assert r.status_code == 200 and r.get_json()["success"] is True

    purchased = client.get("/api/library/purchased").get_json()
    assert not any(a["album_id"] == "pwa2" for a in purchased["albums"])


def test_purchased_list_endpoint_search(client):
    wdb = web_server.get_database()
    _insert_track(wdb, track_id="pwt3", title="Distinctively Named Track", to_be_purchased=1,
                  artist_id="pwar3", album_id="pwa3")
    client.post("/api/library/tracks/mark-purchased", json={"track_ids": ["pwt3"]})

    r = client.get("/api/library/purchased?search=Distinctively")
    body = r.get_json()
    assert any(a["album_id"] == "pwa3" for a in body["albums"])

    r2 = client.get("/api/library/purchased?search=DefinitelyNotPresent")
    assert r2.get_json()["albums"] == []


def test_full_round_trip_download_to_purchased_to_unmarked(client):
    """download (auto-flagged) -> mark purchased -> shows in Purchased,
    vanishes from the shopping list -> unmark -> disappears from Purchased,
    and does NOT silently reappear on the shopping list."""
    wdb = web_server.get_database()
    _insert_track(wdb, track_id="pwt4", title="Round Trip Song", to_be_purchased=1,
                  artist_id="pwar4", album_id="pwa4")

    # Starts on the shopping list.
    shopping = client.get("/api/library/to-be-purchased").get_json()
    assert any(t["id"] == "pwt4" for t in shopping["tracks"])

    # Mark purchased.
    r = client.post("/api/library/tracks/mark-purchased", json={"track_ids": ["pwt4"]})
    assert r.get_json()["success"] is True

    shopping_after = client.get("/api/library/to-be-purchased").get_json()
    assert not any(t["id"] == "pwt4" for t in shopping_after["tracks"])
    purchased_after = client.get("/api/library/purchased").get_json()
    assert any(a["album_id"] == "pwa4" for a in purchased_after["albums"])

    # Unmark.
    r2 = client.post("/api/library/tracks/unmark-purchased", json={"track_ids": ["pwt4"]})
    assert r2.get_json()["success"] is True

    purchased_gone = client.get("/api/library/purchased").get_json()
    assert not any(a["album_id"] == "pwa4" for a in purchased_gone["albums"])
    shopping_still_gone = client.get("/api/library/to-be-purchased").get_json()
    assert not any(t["id"] == "pwt4" for t in shopping_still_gone["tracks"])


# ── collapsible album cards (UI source guards) ───────────────────────────────
#
# No JS runner in this repo, so these pin the wiring the feature depends on —
# same approach as tests/test_settings_partial_save.py's source guards.

_WEBUI = Path(__file__).resolve().parent.parent / "webui"
_PURCHASED_JS = (_WEBUI / "static" / "purchased.js").read_text(encoding="utf-8")
_STYLE_CSS = (_WEBUI / "static" / "style.css").read_text(encoding="utf-8")
_INDEX_HTML = (_WEBUI / "index.html").read_text(encoding="utf-8", errors="replace")


def test_collapse_state_survives_the_post_unmark_rerender():
    """Every unmark re-fetches and re-renders the whole list, so collapse state
    has to live outside the DOM or it is lost on the action most likely to
    follow a collapse."""
    assert "collapsed: new Set()" in _PURCHASED_JS
    start = _PURCHASED_JS.index("function _purchasedAlbumCardHtml")
    body = _PURCHASED_JS[start:_PURCHASED_JS.index("\nfunction ", start + 10)]
    assert "purchasedPageState.collapsed.has(albumId)" in body, (
        "the card renderer ignores the collapse set — a re-render would spring "
        "every collapsed album back open")


def test_unmark_album_button_does_not_toggle_the_card():
    """The header is the click target for collapsing, and Unmark Album sits
    inside it — without the guard, unmarking would also collapse the card."""
    assert "button:not(.purchased-album-toggle)" in _PURCHASED_JS


def test_collapse_all_button_exists_and_is_wired():
    assert 'id="purchased-collapse-all-btn"' in _INDEX_HTML
    assert "purchased-collapse-all-btn" in _PURCHASED_JS
    assert "_togglePurchasedAll" in _PURCHASED_JS


def test_collapsed_cards_hide_their_tracks():
    assert ".purchased-album-card--collapsed .purchased-track-rows { display: none; }" in _STYLE_CSS


def test_purchased_controls_row_is_scoped_to_this_page():
    """.library-controls is shared with the Library page, which stacks its
    children. Widening it globally would relayout that page too."""
    assert ".purchased-page-container .library-controls {" in _STYLE_CSS


def test_toggle_reports_state_to_assistive_tech():
    assert "aria-expanded" in _PURCHASED_JS
    assert "aria-label" in _PURCHASED_JS


# ── unmarking is admin-only ──────────────────────────────────────────────────
#
# Recording a purchase is something any profile may do; ERASING one destroys
# history nothing else can rebuild, so standard profiles (including anyone
# signed in with Plex) can't. Gated on the SERVER — hiding the buttons alone
# would leave the endpoint open to anyone who can reach the box.


def _make_profile(is_admin: bool, name: str) -> int:
    return web_server.get_database().create_profile(name=name, is_admin=is_admin)


def test_unmark_is_refused_for_a_standard_profile(client):
    wdb = web_server.get_database()
    _insert_track(wdb, track_id="pwt9", title="Kept Song", to_be_purchased=1,
                  artist_id="pwar9", album_id="pwa9")
    client.post("/api/library/tracks/mark-purchased", json={"track_ids": ["pwt9"]})

    pid = _make_profile(False, "Standard Listener")
    with client.session_transaction() as sess:
        sess["profile_id"] = pid

    r = client.post("/api/library/tracks/unmark-purchased", json={"track_ids": ["pwt9"]})
    assert r.status_code == 403, "a standard profile erased a purchase record"

    # ...and the record really is untouched, not just the response refused.
    with client.session_transaction() as sess:
        sess.pop("profile_id", None)
    purchased = client.get("/api/library/purchased").get_json()
    assert any(a["album_id"] == "pwa9" for a in purchased["albums"])


def test_a_second_admin_profile_may_still_unmark(client):
    """Gated on is_admin, not profile_id == 1 — @admin_only would have locked
    out every admin except the first one."""
    wdb = web_server.get_database()
    _insert_track(wdb, track_id="pwt10", title="Second Admin Song", to_be_purchased=1,
                  artist_id="pwar10", album_id="pwa10")
    client.post("/api/library/tracks/mark-purchased", json={"track_ids": ["pwt10"]})

    pid = _make_profile(True, "Second Admin")
    with client.session_transaction() as sess:
        sess["profile_id"] = pid

    r = client.post("/api/library/tracks/unmark-purchased", json={"track_ids": ["pwt10"]})
    assert r.status_code == 200 and r.get_json()["success"] is True


def test_a_standard_profile_may_still_mark_purchased(client):
    """Only UNmarking is restricted — recording a purchase stays open."""
    wdb = web_server.get_database()
    _insert_track(wdb, track_id="pwt11", title="Buyable", to_be_purchased=1,
                  artist_id="pwar11", album_id="pwa11")

    pid = _make_profile(False, "Standard Buyer")
    with client.session_transaction() as sess:
        sess["profile_id"] = pid

    r = client.post("/api/library/tracks/mark-purchased", json={"track_ids": ["pwt11"]})
    assert r.status_code == 200 and r.get_json()["success"] is True


def test_unmark_buttons_are_not_rendered_for_a_standard_profile():
    """UI half. Both buttons hit the SAME endpoint, so there is no coherent
    'albums only' restriction — unmarking each track in turn is the same act."""
    assert "_purchasedCanUnmark" in _PURCHASED_JS
    for marker in ("purchased-album-unmark-btn", "purchased-track-unmark-btn"):
        idx = _PURCHASED_JS.index(marker)
        window = _PURCHASED_JS[max(0, idx - 400):idx]
        assert "canUnmark" in window, f"{marker} rendered unconditionally"


def test_readonly_rows_drop_the_unmark_column():
    assert ".purchased-album-card--readonly .purchased-track-row" in _STYLE_CSS
