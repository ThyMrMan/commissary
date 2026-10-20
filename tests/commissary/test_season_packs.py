"""Season packs: an 'only' mode, packs from the RSS feed, one claimant per file."""

from __future__ import annotations

import time

import pytest

import core.video.rss_sync as rss
from core.automation.handlers import video_process_wishlist as vpw
from core.commissary import season_packs as sp
from core.commissary import title_overrides as to
from database.video_database import VideoDatabase

AIRED = ["2025-01-0%d" % e for e in (1, 2, 3, 4)]


@pytest.fixture()
def db(tmp_path, monkeypatch):
    import api.video as videoapi
    from core.video import download_events
    download_events._reset_for_tests()
    deadline = time.monotonic() + 5.0
    while rss.is_running() and time.monotonic() < deadline:
        time.sleep(0.05)
    d = VideoDatabase(database_path=str(tmp_path / "video_library.db"))
    videoapi._video_db = d
    monkeypatch.setattr(vpw, "_default_db", lambda: d)
    yield d
    videoapi._video_db = None


def _wish(db, tmdb=9, title="Silo", season=2, dates=AIRED):
    db.add_episodes_to_wishlist(tmdb, title, [
        {"season_number": season, "episode_number": i + 1, "air_date": d}
        for i, d in enumerate(dates)])


def _rows(db, tmdb=9):
    return [r for r in db.episode_wishlist_to_download(due_only=False)
            if r["show_tmdb_id"] == tmdb]


def _cand(fn, source="torrent"):
    return {"filename": fn, "title": fn, "username": "u", "size_bytes": 1000,
            "quality_label": "WEBDL-1080p", "accepted": True, "score": 10, "source": source}


class _Deps:
    def __init__(self):
        self.progress = []

    def update_progress(self, automation_id, **kw):
        self.progress.append(kw)


def _drain(items, searches, *, active=()):
    enq, seen, notes = [], [], []
    deps = _Deps()
    res = vpw.auto_video_process_wishlist(
        {"_automation_id": "a", "max_concurrent": 1}, deps, media_type="episode",
        fetch_items=lambda mt: items, active_keys=lambda mt: set(active),
        target_dir=lambda mt: "/tv",
        search=lambda it, mt: seen.append(vpw.item_key(it, mt)) or searches.get(vpw.item_key(it, mt), []),
        enqueue=lambda it, best, cands, mt, root: enq.append(vpw.item_key(it, mt)) or True,
        record_note=lambda it, mt, note, q=None: notes.append((vpw.item_key(it, mt), note)))
    return res, enq, seen, notes, deps


# ── is the season over? ──────────────────────────────────────────────────────

def test_a_season_long_finished_is_finished(db):
    _wish(db)
    assert sp.season_finished(db, _rows(db), today="2026-01-01")


def test_a_recent_episode_means_still_airing(db):
    _wish(db, dates=["2025-12-20", "2025-12-27", "2026-01-03"])
    assert not sp.season_finished(db, _rows(db), today="2026-01-05")


def test_an_undated_episode_is_never_read_as_finished(db):
    _wish(db, dates=AIRED[:3])
    db.add_episodes_to_wishlist(9, "Silo", [{"season_number": 2, "episode_number": 9}])
    assert not sp.season_finished(db, _rows(db), today="2026-01-01")


def test_a_later_episode_not_yet_due_keeps_it_airing(db):
    """The drain's rows stop at air date + 7 days; the season doesn't."""
    _wish(db, dates=AIRED[:3] + ["2026-03-01"])
    rows = [r for r in _rows(db) if r["air_date"] < "2026-01-01"]
    assert len(rows) == 3
    assert not sp.season_finished(db, rows, today="2026-01-01")


# ── the drain ────────────────────────────────────────────────────────────────

def test_only_holds_a_finished_season_when_no_pack_is_found(db):
    _wish(db)
    to.set_values(db, "show", 9, pack_preference="only")
    res, enq, seen, notes, deps = _drain(_rows(db), {})
    assert seen == [("season", "9", 2)], "no episode searched one by one"
    assert enq == [] and len(notes) == 4 and notes[0][1] == sp.HOLD_NOTE
    assert any("holding its 4 episode(s)" in (p.get("log_line") or "") for p in deps.progress)


def test_only_grabs_the_pack_when_there_is_one(db):
    _wish(db)
    to.set_values(db, "show", 9, pack_preference="only")
    res, enq, seen, notes, _ = _drain(_rows(db), {("season", "9", 2): [_cand("Silo.S02.1080p")]})
    assert enq == [("season", "9", 2)] and notes == []


def test_only_reaches_for_a_pack_even_with_packs_switched_off(db):
    _wish(db)
    to.set_values(db, "show", 9, pack_preference="only")
    enq, seen = [], []
    vpw.auto_video_process_wishlist(
        {"_automation_id": "a", "max_concurrent": 1, "season_pack_first": False}, _Deps(),
        media_type="episode", fetch_items=lambda mt: _rows(db), active_keys=lambda mt: set(),
        target_dir=lambda mt: "/tv", search=lambda it, mt: seen.append(vpw.item_key(it, mt)) or [],
        enqueue=lambda *a: True, record_note=lambda *a, **k: None)
    assert seen == [("season", "9", 2)]


def test_prefer_still_falls_back_to_episodes(db):
    _wish(db)
    to.set_values(db, "show", 9, pack_preference="prefer")
    _res, _enq, seen, notes, _ = _drain(_rows(db), {})
    assert seen[0] == ("season", "9", 2) and len(seen) == 5 and notes == []


def test_only_never_holds_a_season_whose_end_is_unknown(db):
    _wish(db, dates=AIRED[:3])
    db.add_episodes_to_wishlist(9, "Silo", [{"season_number": 2, "episode_number": 9}])
    to.set_values(db, "show", 9, pack_preference="only")
    _res, _enq, seen, notes, _ = _drain(_rows(db), {})
    assert len(seen) > 1 and notes == [], "falls back like prefer"


def test_episodes_of_a_downloading_pack_are_not_grabbed_alone(db):
    _wish(db)
    res, enq, seen, _notes, _ = _drain(_rows(db), {}, active={("season", "9", 2)})
    assert seen == [] and enq == []


# ── RSS ──────────────────────────────────────────────────────────────────────

def _feed_hit(title):
    return {"title": title, "size_bytes": 20_000_000_000, "seeders": 50, "peers": 5,
            "username": "indexer", "availability": 50, "filename": title, "files": [],
            "file_count": 0, "folder_size_bytes": 20_000_000_000,
            "download_url": "http://idx/dl/" + title, "protocol": "torrent",
            "indexer_id": 1, "guid": "g-" + title}


@pytest.fixture()
def grabs(db, monkeypatch):
    from core.video.download_config import save
    save(db, {"download_mode": "torrent"})
    out = []
    monkeypatch.setattr(vpw, "_default_target_dir", lambda mt: "/media/" + mt)
    monkeypatch.setattr(vpw, "_default_active_keys", lambda mt: set())
    monkeypatch.setattr(vpw, "_default_enqueue",
                        lambda item, best, cands, mt, root: out.append(
                            (vpw.item_key(item, mt), best["title"])) or True)
    return out


def test_rss_grabs_a_season_pack_from_the_feed(db, grabs):
    _wish(db)
    feed = [_feed_hit("Silo S02 1080p WEB h264-NTb"), _feed_hit("Silo S02E01 1080p WEB h264-NTb")]
    out = rss.rss_pass(fetch=lambda: feed, extto=False)
    assert grabs == [(("season", "9", 2), "Silo S02 1080p WEB h264-NTb")], grabs
    assert out["grabbed"] == 1


def test_rss_without_a_pack_still_grabs_the_episode(db, grabs):
    _wish(db)
    rss.rss_pass(fetch=lambda: [_feed_hit("Silo S02E01 1080p WEB h264-NTb")], extto=False)
    assert grabs == [(("episode", "9", 2, 1), "Silo S02E01 1080p WEB h264-NTb")]


def test_rss_holds_an_only_shows_finished_season(db, grabs):
    _wish(db)
    to.set_values(db, "show", 9, pack_preference="only")
    rss.rss_pass(fetch=lambda: [_feed_hit("Silo S02E01 1080p WEB h264-NTb")], extto=False)
    assert grabs == []


def test_rss_never_grabs_an_episode_while_its_pack_downloads(db, grabs, monkeypatch):
    _wish(db, dates=["2025-12-30"])           # one episode: no pack of its own
    monkeypatch.setattr(vpw, "_default_active_keys", lambda mt: {("season", "9", 2)})
    rss.rss_pass(fetch=lambda: [_feed_hit("Silo S02E01 1080p WEB h264-NTb")], extto=False)
    assert grabs == []


# ── the setting ─────────────────────────────────────────────────────────────

def test_only_is_a_valid_per_show_setting(db):
    db.upsert_show_tree("plex", {"server_id": "s1", "title": "Silo", "tmdb_id": 9, "seasons": []})
    import sqlite3
    conn = sqlite3.connect(str(db.database_path))
    rid = conn.execute("SELECT id FROM shows WHERE tmdb_id=9").fetchone()[0]
    conn.close()
    assert db.set_title_overrides("show", rid, pack_preference="only")
    conn = sqlite3.connect(str(db.database_path))
    stored = conn.execute("SELECT pack_preference FROM shows WHERE id=?", (rid,)).fetchone()[0]
    conn.close()
    assert stored == "only", "upstream's own row keeps it, not just the tmdb-keyed copy"
    assert db.title_overrides("show", rid)["pack_preference"] == "only"
    assert to.get(db, "show", 9)["pack_preference"] == "only", "mirrored to the tmdb id"


def test_the_manage_panels_offer_it():
    from pathlib import Path
    root = Path(__file__).resolve().parents[2]
    for rel in ("webui/static/video/video-manage-panel.js", "webui/static/commissary-title-setup.js"):
        assert "['only', 'Season packs only" in (root / rel).read_text(encoding="utf-8"), rel


def test_both_override_endpoints_accept_only(db):
    from flask import Flask
    import api.video as videoapi
    db.upsert_show_tree("plex", {"server_id": "s1", "title": "Silo", "tmdb_id": 9, "seasons": []})
    import sqlite3
    conn = sqlite3.connect(str(db.database_path))
    rid = conn.execute("SELECT id FROM shows WHERE tmdb_id=9").fetchone()[0]
    conn.close()
    app = Flask(__name__)
    app.register_blueprint(videoapi.create_video_blueprint(), url_prefix="/api/video")
    c = app.test_client()
    r = c.put("/api/video/detail/show/%d/overrides" % rid, json={"pack_preference": "only"})
    assert r.status_code == 200 and r.get_json()["pack_preference"] == "only"
    r = c.put("/api/video/commissary/title/show/77/overrides", json={"pack_preference": "only"})
    assert r.status_code == 200 and r.get_json()["pack_preference"] == "only"
