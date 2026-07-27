"""Watchlist posters sometimes stayed blank.

Reported: "posters on the watchlist do not always load — will sometimes just
stay the default blank image". Three separate causes, all of which produce the
same symptom on a card:

1. A follow stores whatever art the surface that added it happened to hold. The
   detail page proxies TMDB art through /api/video/img; the search/discover
   cards pass image.tmdb.org straight through. Those load from the BROWSER, so
   they die on any client whose DNS/blocklist eats that host — and they skip the
   disk cache entirely. Same show, different behaviour per click origin.
2. w.poster_url is a SNAPSHOT. A follow added from an owned show saves
   '/api/video/poster/show/<row id>', and show rows are deleted and re-inserted
   when Plex re-keys an item (core/video/show_sync.py) — so the saved id 404s.
   A follow added before the show was ever scanned has no art at all, and
   nothing backfills it once the show arrives.
3. When the <img> did fail, onerror hid it (display:none) instead of restoring
   the placeholder, turning "no art" into a blank tile.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from database.video_database import VideoDatabase, _proxied_art

_ROOT = Path(__file__).resolve().parent.parent
_TMDB = "https://image.tmdb.org/t/p/w342/abc.jpg"


@pytest.fixture()
def db(tmp_path):
    return VideoDatabase(database_path=str(tmp_path / "video_library.db"))


def _seed_show(db, *, tmdb_id=500, title="Tenkosaki", poster="/library/metadata/9/thumb"):
    return db.upsert_show_tree("plex", {
        "server_id": "s1", "tmdb_id": tmdb_id, "title": title, "status": "Returning Series",
        "poster_url": poster,
        "seasons": [{"season_number": 1, "episodes": [
            {"season_number": 1, "episode_number": 1, "title": "E1"}]}]})


def _follow(db, tmdb_id=500):
    return next(it for it in db.list_watchlist("show") if it["tmdb_id"] == tmdb_id)


# ── 1. external art is routed through the app's own proxy ────────────────────
def test_tmdb_art_is_proxied_and_everything_else_is_left_alone():
    assert _proxied_art(_TMDB) == "/api/video/img?u=" + \
        "https%3A%2F%2Fimage.tmdb.org%2Ft%2Fp%2Fw342%2Fabc.jpg"
    # already-proxied, library proxy paths and non-TMDB hosts pass through
    for keep in ("/api/video/img?u=x", "/api/video/poster/show/3",
                 "https://jellyfin.local/Items/1/Images/Primary"):
        assert _proxied_art(keep) == keep
    for empty in (None, "", "   "):
        assert _proxied_art(empty) is None


def test_a_raw_tmdb_poster_is_normalised_on_the_way_in(db):
    db.add_to_watchlist("person", 77, "Some Director", poster_url=_TMDB)
    stored = next(p for p in db.list_watchlist("person") if p["tmdb_id"] == 77)
    assert stored["poster_url"].startswith("/api/video/img?u=")


def test_rows_written_before_the_fix_heal_on_read(db):
    """No migration: the read path normalises, so existing follows are fixed the
    next time the page loads."""
    db.add_to_watchlist("studio", 88, "A24")
    conn = db._get_connection()
    conn.execute("UPDATE video_watchlist SET poster_url=? WHERE tmdb_id=88", (_TMDB,))
    conn.commit(); conn.close()
    got = next(s for s in db.list_watchlist("studio") if s["tmdb_id"] == 88)
    assert got["poster_url"].startswith("/api/video/img?u=")


# ── 2. an owned show's art comes from the live library row ───────────────────
def test_an_owned_show_serves_library_art_not_the_saved_snapshot(db):
    show_id = _seed_show(db)
    db.add_to_watchlist("show", 500, "Tenkosaki", poster_url=_TMDB, library_id=show_id)
    assert _follow(db)["poster_url"] == "/api/video/poster/show/%d" % show_id


def test_a_stale_library_id_is_healed_by_tmdb_id(db):
    """The re-key case. The saved id points at a row that no longer exists, so the
    old JOIN produced NULL art, NULL status and 0/0 episodes."""
    db.add_to_watchlist("show", 500, "Tenkosaki", library_id=4242)   # never existed
    assert _follow(db)["poster_url"] is None      # nothing to serve yet
    show_id = _seed_show(db)                       # ... now it is scanned in
    it = _follow(db)
    assert it["poster_url"] == "/api/video/poster/show/%d" % show_id
    assert it["library_id"] == show_id             # the id is corrected too
    assert it["status"] == "Returning Series"
    assert it["episode_count"] == 1                # counts follow the healed row


def test_a_saved_library_path_with_no_live_row_is_dropped(db):
    """Caught in the browser: the id healed to None but the SAVED art was still
    '/api/video/poster/show/<dead id>' — a request the card cannot win. Serving
    None paints the placeholder immediately instead of after a failed round-trip."""
    db.add_to_watchlist("show", 500, "Tenkosaki", library_id=99999)
    conn = db._get_connection()
    conn.execute("UPDATE video_watchlist SET poster_url='/api/video/poster/show/99999' "
                 "WHERE tmdb_id=500")
    conn.commit(); conn.close()
    assert _follow(db)["poster_url"] is None


def test_a_scanned_show_with_no_art_does_not_serve_a_dead_library_path(db):
    """Same shape, live row: the row resolves but carries no poster, so the saved
    path for that very row is equally dead."""
    show_id = _seed_show(db, poster=None)
    db.add_to_watchlist("show", 500, "Tenkosaki",
                        poster_url="/api/video/poster/show/%d" % show_id, library_id=show_id)
    assert _follow(db)["poster_url"] is None


def test_a_follow_added_before_the_scan_picks_up_art_afterwards(db):
    """Following an un-owned show stores no library_id at all."""
    db.add_to_watchlist("show", 500, "Tenkosaki")
    assert _follow(db)["poster_url"] is None
    show_id = _seed_show(db)
    assert _follow(db)["poster_url"] == "/api/video/poster/show/%d" % show_id


def test_the_saved_poster_is_used_when_the_library_row_has_no_art(db):
    """A scanned show whose own poster_url is blank must not produce a dead
    /api/video/poster/show/<id> — that 404s. Fall back to what we stored."""
    show_id = _seed_show(db, poster=None)
    db.add_to_watchlist("show", 500, "Tenkosaki", poster_url=_TMDB, library_id=show_id)
    assert _follow(db)["poster_url"].startswith("/api/video/img?u=")


def test_an_unowned_follow_keeps_its_own_art(db):
    db.add_to_watchlist("show", 501, "Not Owned", poster_url=_TMDB)
    it = next(i for i in db.list_watchlist("show") if i["tmdb_id"] == 501)
    assert it["poster_url"].startswith("/api/video/img?u=")
    assert it["library_id"] is None


def test_no_art_anywhere_is_none_not_a_broken_url(db):
    db.add_to_watchlist("show", 502, "Bare")
    assert next(i for i in db.list_watchlist("show") if i["tmdb_id"] == 502)["poster_url"] is None


def test_the_heal_respects_server_source(db):
    """A tmdb_id can exist on more than one configured server; the healed row must
    come from the one being listed, matching the airing-shows arm."""
    show_id = _seed_show(db)
    db.add_to_watchlist("show", 500, "Tenkosaki", library_id=9999)
    assert _follow(db)["library_id"] == show_id
    listed = db.list_watchlist("show", server_source="jellyfin")
    assert next(i for i in listed if i["tmdb_id"] == 500)["library_id"] is None


def test_pending_follows_still_carry_their_flags(db):
    """The rewritten SELECT must not drop the approval columns it also carries."""
    db.add_to_watchlist("show", 503, "Requested", approved=False,
                        requested_by=7, requested_by_name="ana", monitor="all")
    it = next(i for i in db.list_watchlist("show") if i["tmdb_id"] == 503)
    assert it["approved"] is False and it["requested_by"] == 7
    assert it["requested_by_name"] == "ana" and it["monitor"] == "all"
    assert not [i for i in db.list_watchlist("show", approved_only=True)
                if i["tmdb_id"] == 503]


def test_the_airing_default_arm_is_unaffected(db):
    """Actively-airing library shows are added with no follow row at all."""
    show_id = _seed_show(db)
    it = next(i for i in db.list_watchlist("show") if i["tmdb_id"] == 500)
    assert it["poster_url"] == "/api/video/poster/show/%d" % show_id
    assert it.get("auto") is True


# ── 3. the frontend fallback ─────────────────────────────────────────────────
def test_a_failed_poster_falls_back_to_the_placeholder():
    js = (_ROOT / "webui" / "static" / "video" / "video-watchlist.js").read_text(encoding="utf-8")
    card = js.split("function cardHTML", 1)[1].split("function ", 1)[0]
    assert "vwlp-card-ph" in card.split("onerror", 1)[1].split(">", 1)[0]
    assert "this.style.display=\\'none\\'" not in card


def test_tmdb_art_in_the_img_proxy_uses_the_disk_cache():
    py = (_ROOT / "api" / "video" / "poster.py").read_text(encoding="utf-8")
    proxy = py.split("def video_img_proxy", 1)[1]
    assert '_serve_cached(url)' in proxy.split("User-Agent", 1)[0]
    # the Google hosts must stay on the live path — they need the UA header
    assert 'host == "image.tmdb.org"' in proxy
