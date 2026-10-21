"""Only the provider that owns a show's numbering creates its episodes.

A Bleach-shaped show: the server (and TVDB) have seasons 1-3; TMDB folds the
first two into its season 1 and calls the revival season 2. Upstream cascades
TMDB's numbers into the library and hands the same numbers to TVDB, so TMDB's
season 2 lands on top of the server's, and season 3 can never fill.
"""

from __future__ import annotations

import sqlite3

import pytest
from flask import Flask

from core.commissary import episode_numbering as en
from core.video.enrichment.engine import VideoEnrichmentEngine
from core.video.enrichment.worker import VideoEnrichmentWorker
from database.video_database import VideoDatabase

TMDB = {1: [1, 2, 3, 4, 5], 2: [1, 2, 3]}          # TMDB's split
TVDB = {1: [1, 2], 2: [1, 2, 4], 3: [1, 2, 3]}      # the server's split


class Tmdb:
    enabled = True

    def __init__(self):
        self.season_calls = []

    def match(self, kind, title, year, known_id=None, **kw):
        return {"id": 30984, "metadata": {"seasons": [{"season_number": s} for s in TMDB]}}

    def season_episodes(self, tv_id, sn):
        self.season_calls.append(sn)
        return {"episodes": [{"episode_number": e, "title": "TMDB S%dE%d" % (sn, e),
                              "air_date": "2020-01-%02d" % e} for e in TMDB.get(sn, [])]}


class Tvdb:
    enabled = True
    api_key = "k"

    def __init__(self, page_size=100):
        self.page_size = page_size
        self.calls = []

    def match(self, *a, **k):
        return None

    def _authed_get(self, path, params):
        self.calls.append(path)
        if path.endswith("/extended"):
            return {"data": {"seasons": [{"number": s, "type": {"type": "official"}} for s in TVDB]
                             + [{"number": 9, "type": {"type": "dvd"}}]}}
        eps = [{"seasonNumber": s, "number": e, "name": "TVDB S%dE%d" % (s, e),
                "aired": "2021-0%d-%02d" % (s, e)} for s in TVDB for e in TVDB[s]]
        page = params.get("page", 0)
        chunk = eps[page * self.page_size:(page + 1) * self.page_size]
        more = (page + 1) * self.page_size < len(eps)
        # the v4 API's own paging links
        return {"data": {"episodes": chunk},
                "links": {"next": "https://api4/x?page=%d" % (page + 1) if more else None}}

    def season_episodes(self, series_id, season_number):
        return en.tvdb_season_episodes(self, series_id, season_number)


@pytest.fixture()
def db(tmp_path):
    db = VideoDatabase(database_path=str(tmp_path / "video_library.db"))
    db.upsert_show_tree("plex", {"server_id": "s1", "title": "Bleach", "tmdb_id": 30984, "seasons": [
        {"season_number": s, "episodes": [{"episode_number": 1, "server_id": "e%d" % s}]}
        for s in (1, 2, 3)]})
    conn = sqlite3.connect(str(db.database_path))
    conn.execute("UPDATE shows SET tvdb_id=74796")
    conn.commit()
    conn.close()
    return db


def _sid(db):
    conn = sqlite3.connect(str(db.database_path))
    try:
        return conn.execute("SELECT id FROM shows").fetchone()[0]
    finally:
        conn.close()


def _episodes(db):
    conn = sqlite3.connect(str(db.database_path))
    try:
        return {(s, e): t for s, e, t in conn.execute(
            "SELECT season_number, episode_number, title FROM episodes")}
    finally:
        conn.close()


# ── the decision ────────────────────────────────────────────────────────────

def test_a_wholesale_disagreement_picks_the_provider_the_server_agrees_with():
    assert en.choose_source([1, 2, 3], [0, 1, 2], [0, 1, 2, 3]) == "tvdb"
    assert en.choose_source(range(1, 18), [0, 1, 2], range(0, 18)) == "tvdb"


def test_agreement_or_no_tvdb_keeps_tmdb():
    assert en.choose_source([1, 2], [1, 2], [1, 2, 3]) == "tmdb"
    assert en.choose_source([1, 2, 3], [1], []) == "tmdb"
    assert en.choose_source(range(1, 11), range(1, 10), range(1, 11)) == "tmdb",         "one missing season of ten isn't decisive"


def test_an_explicit_choice_is_obeyed():
    assert en.choose_source([1, 2], [1, 2], [], override="tvdb") == "tvdb"
    assert en.choose_source([1, 2, 3], [1], [1, 2, 3], override="tmdb") == "tmdb"


# ── TVDB ────────────────────────────────────────────────────────────────────

def test_tvdb_reads_every_page():
    """Page 0 alone left the later seasons empty on a long show."""
    client = Tvdb(page_size=2)
    got = en.tvdb_season_episodes(client, 74796, 3)
    assert [e["episode_number"] for e in got] == [1, 2, 3]


def test_the_upstream_client_now_pages_too():
    from core.video.enrichment.clients import TVDBClient
    c = TVDBClient("k")
    fake = Tvdb(page_size=2)
    c._authed_get = fake._authed_get
    assert [e["episode_number"] for e in c.season_episodes(74796, 3)] == [1, 2, 3]


def test_tvdb_season_list_is_the_aired_order():
    assert en.tvdb_season_numbers(Tvdb(), 74796) == [1, 2, 3]


# ── refresh_show_art ────────────────────────────────────────────────────────

def _engine(db):
    return VideoEnrichmentEngine(db, {"tmdb": Tmdb(), "tvdb": Tvdb()})


def test_auto_lets_tvdb_build_the_list_and_tmdb_write_nothing(db):
    sid = _sid(db)
    assert _engine(db).refresh_show_art(sid, with_ratings=False)["ok"]
    eps = _episodes(db)
    assert (3, 3) in eps and eps[(3, 3)] == "TVDB S3E3", "season 3 fills at last"
    assert (1, 5) not in eps and (2, 3) not in eps, "nothing filed under TMDB's numbers"


def test_tmdb_owned_show_never_takes_tvdb_inserts(db):
    sid = _sid(db)
    en.set_override(db, sid, "tmdb")
    _engine(db).refresh_show_art(sid, with_ratings=False)
    eps = _episodes(db)
    assert (1, 5) in eps and (2, 3) in eps
    assert (2, 4) not in eps, "TVDB may fill in, never invent under TMDB's season 2"


def test_the_recent_refresh_stays_scoped_for_a_tvdb_show(db):
    sid = _sid(db)
    _engine(db).refresh_show_art(sid, with_ratings=False, recent_seasons_only=True)
    eps = _episodes(db)
    assert (3, 3) in eps and (1, 2) not in eps, "only the latest season(s) re-pulled"


# ── the unattended worker passes ────────────────────────────────────────────

def test_the_match_cascade_skips_a_show_tmdb_doesnt_own(db):
    sid = _sid(db)
    en.set_override(db, sid, "tvdb")
    client = Tmdb()
    w = VideoEnrichmentWorker(db, "tmdb", client)
    assert w.process_one() is True
    assert client.season_calls == [] and (1, 5) not in _episodes(db)


def test_the_episode_sync_queue_skips_it_and_moves_on(db):
    sid = _sid(db)
    db.enrichment_apply("tmdb", "show", sid, matched=True, external_id=30984)
    en.set_override(db, sid, "tvdb")
    w = VideoEnrichmentWorker(db, "tmdb", Tmdb())
    assert w._sync_episodes_once() is True
    assert (1, 5) not in _episodes(db)
    assert db.episode_sync_next() is None, "marked synced, never handed back"


def test_a_tmdb_owned_show_still_cascades(db):
    w = VideoEnrichmentWorker(db, "tmdb", Tmdb())
    en.set_override(db, _sid(db), "tmdb")
    w.process_one()
    assert (1, 5) in _episodes(db)


# ── scene numbering ─────────────────────────────────────────────────────────

def test_scene_mapping_skips_a_tvdb_numbered_show(db):
    from core.video import numbering
    en.set_override(db, _sid(db), "tvdb")
    client = Tvdb()
    item = {"show_tmdb_id": 30984, "tvdb_id": 74796, "season_number": 3, "episode_number": 2,
            "air_date": "2021-03-02", "show_title": "Bleach"}
    assert numbering.scene_info(item, db=db, client=client) is None
    assert client.calls == [], "not even asked"


# ── out of place ────────────────────────────────────────────────────────────

def _pollute(db):
    """What upstream's cascade wrote: TMDB's season 1 and 2 numbers."""
    sid = _sid(db)
    db.backfill_episodes(sid, 1, [{"episode_number": e} for e in (2, 3, 4, 5)])
    db.backfill_episodes(sid, 2, [{"episode_number": e} for e in (2, 3)])
    return sid


def test_out_of_place_finds_and_removes_only_rows_nothing_owns(db):
    sid = _pollute(db)
    eng = _engine(db)
    res = en.out_of_place(eng, sid)
    assert sorted((e["season_number"], e["episode_number"]) for e in res["episodes"]) \
        == [(1, 3), (1, 4), (1, 5), (2, 3)]
    assert res["source"] == "tvdb" and res["removed"] == 0
    assert en.out_of_place(eng, sid, remove=True)["removed"] == 4
    eps = _episodes(db)
    assert (1, 1) in eps and (1, 2) in eps and (2, 2) in eps and (1, 5) not in eps


def test_a_season_the_provider_returned_nothing_for_is_left_alone(db):
    sid = _pollute(db)
    assert en.unlisted_rows(db, sid, 1, []) == []
    assert en.delete_unlisted(db, sid, 1, []) == 0


# ── the endpoints ───────────────────────────────────────────────────────────

@pytest.fixture()
def client(db, monkeypatch):
    import api.video as videoapi
    import core.video.enrichment.engine as engmod
    videoapi._video_db = db
    eng = _engine(db)
    monkeypatch.setattr(engmod, "get_video_enrichment_engine", lambda: eng)
    app = Flask(__name__)
    app.register_blueprint(videoapi.create_video_blueprint(), url_prefix="/api/video")
    try:
        yield app.test_client()
    finally:
        videoapi._video_db = None


def test_numbering_says_which_provider_and_why(client, db):
    d = client.get("/api/video/libraries/show/%d/numbering" % _sid(db)).get_json()
    assert (d["source"], d["setting"], d["missing_from_tmdb"]) == ("tvdb", "auto", [3])
    r = client.put("/api/video/libraries/show/%d/numbering" % _sid(db), json={"episode_source": "tmdb"})
    assert r.status_code == 200
    d = client.get("/api/video/libraries/show/%d/numbering" % _sid(db)).get_json()
    assert (d["source"], d["override"]) == ("tmdb", "tmdb")
    assert client.put("/api/video/libraries/show/%d/numbering" % _sid(db),
                      json={"episode_source": "imdb"}).status_code == 400


def test_rescan_reports_what_landed(client, db):
    d = client.post("/api/video/libraries/show/%d/rescan-episodes" % _sid(db)).get_json()
    assert d["success"] and d["before"] == 3 and d["added"] == 5 and d["total"] == 8


def test_out_of_place_endpoint_previews_then_removes(client, db):
    sid = _pollute(db)
    d = client.get("/api/video/libraries/show/%d/out-of-place" % sid).get_json()
    assert len(d["episodes"]) == 4 and d["removed"] == 0
    d = client.post("/api/video/libraries/show/%d/out-of-place" % sid).get_json()
    assert d["removed"] == 4


def test_the_tools_are_admin_only(client, db):
    from flask import g

    @client.application.before_request
    def _member():
        g.is_admin = False

    sid = _sid(db)
    assert client.get("/api/video/libraries/show/%d/numbering" % sid).status_code == 403
    assert client.post("/api/video/libraries/show/%d/out-of-place" % sid).status_code == 403


def test_the_manage_panel_loads_it():
    from pathlib import Path
    root = Path(__file__).resolve().parents[2]
    html = (root / "webui/index.html").read_text(encoding="utf-8")
    assert "filename='commissary-episode-numbering.js'" in html


def test_the_note_names_the_provider_and_why():
    import shutil
    import subprocess
    from pathlib import Path
    node = shutil.which("node") or shutil.which("node.exe")
    if node is None:
        pytest.skip("node not available")
    root = Path(__file__).resolve().parents[2]
    res = subprocess.run([node, "tests/js/commissary_episode_numbering_harness.mjs"], cwd=str(root),
                         capture_output=True, text=True, timeout=120)
    assert res.returncode == 0 and "all assertions passed" in res.stdout, res.stdout + res.stderr
