"""A manual video search judges releases by the title's full identity.

Upstream's manual search compared releases with the bare title only, so an
alias, an air-date name or an anime absolute number ('[SubsPlease] Show - 07')
was rejected exactly when a person searched by hand, while the unattended drain
accepted the same release.
"""

import json
import sqlite3

import pytest
from flask import Flask

from core.commissary import search_identity as si
from database.video_database import VideoDatabase

ANIME = "[SubsPlease] Kusuriya no Hitorigoto - 03 (1080p) [ABCD1234].mkv"


@pytest.fixture()
def db(tmp_path):
    db = VideoDatabase(database_path=str(tmp_path / "video_library.db"))
    seasons = [{"season_number": s, "episodes": [
        {"episode_number": e, "title": "E%d" % e, "air_date": "2026-0%d-1%d" % (s, e)}
        for e in range(1, n + 1)]} for s, n in ((1, 3), (2, 2))]
    db.upsert_show_tree("plex", {"server_id": "s1", "title": "The Apothecary Diaries",
                                 "tmdb_id": 220542, "seasons": seasons})
    return db


def _row_id(db):
    conn = sqlite3.connect(str(db.database_path))
    try:
        return conn.execute("SELECT id FROM shows WHERE tmdb_id=220542").fetchone()[0]
    finally:
        conn.close()


# ── resolving the identity ───────────────────────────────────────────────────

def test_the_id_pair_resolves_either_way(db):
    assert si.tmdb_for(db, {"media_id": 220542, "media_source": "tmdb", "scope": "episode"}) == 220542
    assert si.tmdb_for(db, {"media_id": _row_id(db), "media_source": "library",
                            "scope": "episode"}) == 220542
    assert si.tmdb_for(db, {"media_id": 999999, "media_source": "library", "scope": "episode"}) is None
    assert si.tmdb_for(db, {}) is None


def test_a_later_season_gets_its_absolute_number_from_the_library(db):
    body = {"media_id": 220542, "media_source": "tmdb", "scope": "episode"}
    assert si.episode_hints(db, body, 2, 1) == ("2026-02-11", 4)


def test_season_one_needs_no_library_at_all(db):
    """A show you don't own yet is exactly when you search by hand."""
    assert si.episode_hints(db, {"scope": "episode"}, 1, 3) == (None, 3)


def test_a_later_season_is_never_guessed(db):
    """The hint only accepts: guessing would let 'Show - 05' satisfy S02E05."""
    assert si.episode_hints(db, {"media_id": 1, "media_source": "tmdb", "scope": "episode"}, 2, 5) \
        == (None, None)


def test_packs_and_movies_carry_no_episode_hints(db):
    assert si.episode_hints(db, {"scope": "season"}, 1, None) == (None, None)
    assert si.episode_hints(db, {"scope": "movie"}, None, None) == (None, None)


def test_manual_aliases_join_the_accepted_titles(db):
    import api.video as videoapi
    db.set_title_overrides("show", _row_id(db), manual_aliases=["Kusuriya no Hitorigoto"])
    videoapi._video_db = db          # the drain's alias builder reads the app's handle
    try:
        titles = si.want_titles(db, {"title": "The Apothecary Diaries", "media_id": 220542,
                                     "media_source": "tmdb", "scope": "episode"})
    finally:
        videoapi._video_db = None
    assert titles[0] == "The Apothecary Diaries" and "Kusuriya no Hitorigoto" in titles


# ── the endpoints ────────────────────────────────────────────────────────────

@pytest.fixture()
def client(db, monkeypatch):
    import api.video as videoapi
    videoapi._video_db = db
    db.set_title_overrides("show", _row_id(db), manual_aliases=["Kusuriya no Hitorigoto"])
    app = Flask(__name__)
    app.register_blueprint(videoapi.create_video_blueprint(), url_prefix="/api/video")
    try:
        yield app.test_client()
    finally:
        videoapi._video_db = None


def _accepted(rows):
    return [r for r in rows if r.get("accepted")]


def test_a_torrent_search_accepts_the_anime_release_by_alias_and_number(client, monkeypatch):
    seen = {}

    def fake_prowlarr(*a, **kw):
        seen.update(kw)
        return {"configured": True, "hits": [{"title": ANIME, "size_bytes": 1_400_000_000,
                                              "protocol": "torrent", "seeders": 50}]}

    monkeypatch.setattr("core.video.prowlarr_search.prowlarr_search", fake_prowlarr)
    r = client.post("/api/video/downloads/search/start", json={
        "scope": "episode", "title": "The Apothecary Diaries", "season": 1, "episode": 3,
        "source": "usenet", "media_id": 220542, "media_source": "tmdb"})
    rows = r.get_json()["results"]
    assert _accepted(rows), rows
    assert seen.get("absolute") == 3, "the query carries the absolute number too"


def test_without_the_identity_the_same_release_is_still_rejected(client, monkeypatch):
    """The alias is what makes the title match; a bare title can't."""
    monkeypatch.setattr("core.video.prowlarr_search.prowlarr_search", lambda *a, **k: {
        "configured": True, "hits": [{"title": ANIME, "size_bytes": 1_400_000_000,
                                      "protocol": "torrent", "seeders": 50}]})
    r = client.post("/api/video/downloads/search/start", json={
        "scope": "episode", "title": "The Apothecary Diaries", "season": 1, "episode": 3,
        "source": "usenet"})
    assert not _accepted(r.get_json()["results"])


def test_the_soulseek_poll_judges_by_what_the_search_started_for(client, monkeypatch):
    monkeypatch.setattr("core.video.slskd_search.start_search",
                        lambda *a, **k: {"configured": True, "id": "abc-123"})
    monkeypatch.setattr("core.video.slskd_search.poll_search", lambda sid: {
        "total_files": 1, "hits": [{"title": ANIME, "filename": "@@peer\\anime\\" + ANIME,
                                    "username": "peer", "size_bytes": 1_400_000_000}]})
    client.post("/api/video/downloads/search/start", json={
        "scope": "episode", "title": "The Apothecary Diaries", "season": 1, "episode": 3,
        "source": "soulseek", "media_id": 220542, "media_source": "tmdb"})
    # the poll's own query string carries no identity, as upstream's client sends it
    r = client.get("/api/video/downloads/search/poll?id=abc-123&scope=episode"
                   "&title=The%20Apothecary%20Diaries&season=1&episode=3")
    assert _accepted(r.get_json()["results"])


def test_a_grab_records_the_facts_the_search_judged_by(client, db, monkeypatch):
    monkeypatch.setattr("core.video.client_grab.grab", lambda *a, **k: {"ok": True, "ref": "h"})
    monkeypatch.setattr("core.video.download_monitor.ensure_started", lambda *a, **k: None)
    monkeypatch.setattr("core.video.disk_guard.free_gb", lambda p: 10000.0)
    db.set_setting("tv_path", "/tv")
    r = client.post("/api/video/downloads/grab", json={
        "kind": "show", "title": "The Apothecary Diaries", "source": "torrent",
        "download_url": "magnet:?xt=urn:btih:x", "media_id": 220542, "media_source": "tmdb",
        "search_ctx": {"scope": "episode", "season": 2, "episode": 1}})
    assert r.status_code == 200, r.get_json()
    conn = sqlite3.connect(str(db.database_path))
    ctx = json.loads(conn.execute("SELECT search_ctx FROM video_downloads").fetchone()[0])
    conn.close()
    assert (ctx["absolute"], ctx["air_date"]) == (4, "2026-02-11")


# ── the unattended drain ─────────────────────────────────────────────────────

def _ep(**kw):
    item = {"kind": "episode", "show_title": "New Anime", "show_tmdb_id": 1,
            "season_number": 1, "episode_number": 3, "series_type": None}
    item.update(kw)
    return item


def test_a_brand_new_untyped_show_gets_the_season_one_absolute():
    from core.automation.handlers.video_process_wishlist import search_context
    assert search_context(_ep(), "episode")["absolute"] == 3


def test_but_not_a_later_season_or_a_standard_show():
    from core.automation.handlers.video_process_wishlist import search_context
    assert search_context(_ep(season_number=2), "episode").get("absolute") is None
    assert search_context(_ep(series_type="standard"), "episode").get("absolute") is None
