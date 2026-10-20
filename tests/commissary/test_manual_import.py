"""Import any file or folder, not only a failed download."""

from __future__ import annotations

import json
import os

import pytest
from flask import Flask

from core.commissary import manual_import as mi
from database.video_database import VideoDatabase


@pytest.fixture(autouse=True)
def small_files_count(monkeypatch):
    # upstream skips anything under 32 MB as 'too small to be an episode'
    monkeypatch.setattr("core.video.season_pack.MIN_EPISODE_BYTES", 1)


@pytest.fixture()
def env(tmp_path):
    import api.video as videoapi
    db = VideoDatabase(database_path=str(tmp_path / "video_library.db"))
    videoapi._video_db = db
    tv = tmp_path / "TV"
    tv.mkdir()
    db.set_setting("tv_path", str(tv))
    db.set_setting("organization", json.dumps({
        "episode_template": "$series/Season $season/$series - S$seasonE$episode",
        "transfer_mode": "copy", "verify_with_ffprobe": False, "save_artwork": False,
        "write_nfo": False, "download_subtitles": False, "carry_subtitles": False}))
    yield db, tmp_path, tv
    videoapi._video_db = None


def _touch(path, size=2048):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "wb") as fh:
        fh.write(b"x" * size)


def _folder(root, names):
    for n in names:
        _touch(os.path.join(str(root), n))
    return str(root)


# ── what a folder holds ─────────────────────────────────────────────────────

def test_members_read_sxxexx_spans_and_fansub_numbers(tmp_path):
    f = _folder(tmp_path / "pack", [
        "Show.S08E01.1080p.mkv", "Show.S08E02E03.1080p.mkv", "Show.S08E01.sample.mkv",
        "notes.txt", "[GHOST] Nana - 04 [BD 1080p].mkv"])
    got = {(m["season"], m["episode"], m["episode_end"], m["absolute"]) for m in mi.members(f)}
    assert got == {(8, 1, None, None), (8, 2, 3, None), (None, 4, None, 4)}


def test_a_typed_season_renumbers_and_keeps_each_files_episode():
    found = [{"path": "a", "season": 8, "episode": 1, "absolute": None},
             {"path": "b", "season": 8, "episode": 2, "absolute": None}]
    plan, err = mi.resolve(found, 7)
    assert err is None
    assert [(p["to_season"], p["to_episode"], p["renumbered_from"]) for p in plan] == \
        [(7, 1, 8), (7, 2, 8)]


def test_a_folder_spanning_seasons_refuses_one_number():
    found = [{"path": "a", "season": 1, "episode": 1, "absolute": None},
             {"path": "b", "season": 2, "episode": 1, "absolute": None}]
    plan, err = mi.resolve(found, 3)
    assert plan == [] and "seasons 1, 2" in err
    plan, err = mi.resolve(found, None)
    assert err is None and [p["to_season"] for p in plan] == [1, 2]


def test_absolute_numbers_follow_the_shows_episode_list_then_the_typed_season():
    found = [{"path": "a", "season": None, "episode": 3, "absolute": 3},
             {"path": "b", "season": None, "episode": 14, "absolute": 14}]
    plan, _ = mi.resolve(found, None, {3: (1, 3), 14: (2, 2)})
    assert [(p["to_season"], p["to_episode"]) for p in plan] == [(1, 3), (2, 2)]
    plan, _ = mi.resolve(found, 2, {})
    assert [(p["to_season"], p["to_episode"]) for p in plan] == [(2, 3), (2, 14)]
    plan, _ = mi.resolve(found, None, {})
    assert [p["to_season"] for p in plan] == [1, 1], "season 1: absolute and episode agree"


def test_two_files_becoming_one_episode_is_refused():
    found = [{"path": "/x/a.mkv", "season": 1, "episode": 3, "absolute": None},
             {"path": "/x/b.mkv", "season": None, "episode": 3, "absolute": 3}]
    plan, err = mi.resolve(found, None, {})
    assert plan == [] and "S01E03" in err


def test_the_absolute_lookup_is_the_nth_aired_episode(env):
    db, _tmp, _tv = env
    db.upsert_show_tree("plex", {"server_id": "n", "title": "Nana", "tmdb_id": 31, "seasons": [
        {"season_number": 0, "episodes": [{"episode_number": 1}]},
        {"season_number": 1, "episodes": [{"episode_number": e} for e in (1, 2)]},
        {"season_number": 2, "episodes": [{"episode_number": e} for e in (1, 2)]}]})
    assert mi.absolute_lookup(db, 31) == {1: (1, 1), 2: (1, 2), 3: (2, 1), 4: (2, 2)}


# ── browse and add ──────────────────────────────────────────────────────────

@pytest.fixture()
def client(env):
    import api.video as videoapi
    app = Flask(__name__)
    app.register_blueprint(videoapi.create_video_blueprint(), url_prefix="/api/video")
    return app.test_client()


def test_browse_lists_folders_and_videos_filtered_before_the_cap(client, env):
    _db, tmp, _tv = env
    root = tmp / "dl"
    _folder(root, ["Bleach - Sennen Kessen Hen - 48.mkv", "Other.Show.S01E01.mkv", "readme.nfo"])
    (root / "Bleach Season 17").mkdir()
    d = client.get("/api/video/import/browse?path=%s" % root).get_json()
    assert [x["name"] for x in d["dirs"]] == ["Bleach Season 17"]
    assert sorted(x["name"] for x in d["files"]) == ["Bleach - Sennen Kessen Hen - 48.mkv",
                                                    "Other.Show.S01E01.mkv"]
    d = client.get("/api/video/import/browse?path=%s&q=BLEACH 48" % root).get_json()
    assert [x["name"] for x in d["files"]] == ["Bleach - Sennen Kessen Hen - 48.mkv"]
    assert d["dirs"] == [] and d["total"] == 3 and d["matched"] == 1


def test_browse_offers_a_folder_from_two_numbered_episodes(client, env):
    _db, tmp, _tv = env
    root = _folder(tmp / "nana", ["[GHOST] Nana - 01 [BD].mkv", "[GHOST] Nana - 02 [BD].mkv"])
    d = client.get("/api/video/import/browse?path=%s&q=zzz" % root).get_json()
    assert d["pack"] == {"count": 2, "seasons": []}, "counted from the folder, not the filter"


def test_the_offer_counts_episodes_the_way_the_import_will(client, env, monkeypatch):
    """A sample is not an episode, and a two-episode file is two."""
    _db, tmp, _tv = env
    monkeypatch.setattr("core.video.season_pack.MIN_EPISODE_BYTES", 1000)
    root = tmp / "s08"
    _folder(root, ["Show.S08E01.mkv", "Show.S08E02E03.mkv"])
    _touch(str(root / "Show.S08E01.sample.mkv"), size=10)
    d = client.get("/api/video/import/browse?path=%s" % root).get_json()
    assert d["pack"] == {"count": 3, "seasons": [8]}
    _touch(str(tmp / "one" / "Show.S08E09.mkv"))
    assert client.get("/api/video/import/browse?path=%s" % (tmp / "one")).get_json()["pack"] is None


def test_add_queues_a_file_with_its_guessed_scope_and_a_folder_as_one_row(client, env):
    db, tmp, _tv = env
    f = tmp / "loose" / "[SubsPlease] Frieren - 12 (1080p).mkv"
    _touch(str(f))
    r = client.post("/api/video/import/add", json={"path": str(f)}).get_json()
    assert r["success"] and r["scope"] == "episode"
    pack = _folder(tmp / "pack", ["Silo.S02E01.mkv", "Silo.S02E02.mkv"])
    r2 = client.post("/api/video/import/add", json={"path": pack}).get_json()
    assert r2["scope"] == "season" and r2["episodes"] == 2
    again = client.post("/api/video/import/add", json={"path": pack}).get_json()
    assert again["already"] is True and again["id"] == r2["id"]
    rows = {r["id"]: r for r in db.get_import_failed_video_downloads()}
    assert json.loads(rows[r2["id"]]["search_ctx"])["season"] == 2
    assert client.post("/api/video/import/add", json={"path": str(tmp / "nope")}).status_code == 404


def test_the_import_tools_are_admin_only(client):
    from flask import g

    @client.application.before_request
    def _member():
        g.is_admin = False

    assert client.get("/api/video/import/browse").status_code == 403
    assert client.post("/api/video/import/add", json={"path": "x"}).status_code == 403


# ── placing a folder ────────────────────────────────────────────────────────

def test_a_folder_placed_as_another_season_keeps_each_files_span(client, env):
    db, tmp, tv = env
    pack = _folder(tmp / "Show.S08", ["Show.S08E01.1080p.mkv", "Show.S08E02E03.1080p.mkv"])
    dl_id = client.post("/api/video/import/add", json={"path": pack}).get_json()["id"]
    pre = client.get("/api/video/import/pack-preview?path=%s&season=7" % pack).get_json()
    assert [(i["to_season"], i["to_episode"]) for i in pre["items"]] == [(7, 1), (7, 2)]
    r = client.post("/api/video/import/%d/place-folder" % dl_id,
                    json={"media_id": 77, "title": "Show", "year": 2010, "season": 7}).get_json()
    assert r["success"] and r["imported"] == 2, r
    season = os.path.join(str(tv), "Show", "Season 07")
    assert sorted(os.listdir(season)) == ["Show - S07E01.mkv", "Show - S07E02-E03.mkv"]
    assert db.get_video_download(dl_id)["status"] == "completed"
    s = client.get("/api/video/import/%d/place/status" % dl_id).get_json()
    assert s["running"] is False and s["imported"] == 2


def test_a_fansub_folder_lands_by_the_chosen_shows_episode_list(client, env):
    db, tmp, tv = env
    db.upsert_show_tree("plex", {"server_id": "n", "title": "Nana", "tmdb_id": 31, "seasons": [
        {"season_number": 1, "episodes": [{"episode_number": e} for e in (1, 2)]},
        {"season_number": 2, "episodes": [{"episode_number": e} for e in (1, 2)]}]})
    pack = _folder(tmp / "nana", ["[GHOST] Nana - %02d [BD 1080p].mkv" % n for n in (2, 3)])
    dl_id = client.post("/api/video/import/add", json={"path": pack}).get_json()["id"]
    r = client.post("/api/video/import/%d/place-folder" % dl_id,
                    json={"media_id": 31, "title": "Nana", "year": 2006}).get_json()
    assert r["imported"] == 2, r
    assert os.path.exists(os.path.join(str(tv), "Nana", "Season 01", "Nana - S01E02.mkv"))
    assert os.path.exists(os.path.join(str(tv), "Nana", "Season 02", "Nana - S02E01.mkv"))


def test_a_multi_season_folder_with_one_season_is_refused_before_copying(client, env):
    _db, tmp, tv = env
    pack = _folder(tmp / "box", ["Show.S01E01.mkv", "Show.S02E01.mkv"])
    dl_id = client.post("/api/video/import/add", json={"path": pack}).get_json()["id"]
    r = client.post("/api/video/import/%d/place-folder" % dl_id,
                    json={"media_id": 77, "title": "Show", "season": 3}).get_json()
    assert r["success"] is False and "seasons 1, 2" in r["error"]
    assert os.listdir(str(tv)) == []


def test_place_needs_a_show_and_an_unplaced_folder(client, env):
    _db, tmp, _tv = env
    pack = _folder(tmp / "p", ["A.S01E01.mkv", "A.S01E02.mkv"])
    dl_id = client.post("/api/video/import/add", json={"path": pack}).get_json()["id"]
    assert client.post("/api/video/import/%d/place-folder" % dl_id, json={}).status_code == 400
    assert client.post("/api/video/import/999/place-folder", json={"media_id": 1}).status_code == 404


def test_a_slow_placement_answers_running_and_its_status_follows(monkeypatch):
    import threading
    gate = threading.Event()
    snap = mi.start(4242, lambda: (gate.wait(5), {"success": True, "imported": 3})[1], grace=0.05)
    assert snap["running"] is True and mi.is_running(4242)
    gate.set()
    for _ in range(100):
        if not mi.is_running(4242):
            break
        threading.Event().wait(0.02)
    assert mi.job_state(4242)["result"] == {"success": True, "imported": 3}


def test_the_page_script_is_loaded_and_never_claims_upstreams_modal():
    """upstream's handlers and ensureModal() key off [data-vimp-modal]: a fork
    dialog carrying it would stop upstream's own Place dialog from opening."""
    import shutil
    import subprocess
    from pathlib import Path
    root = Path(__file__).resolve().parents[2]
    assert "filename='commissary-import.js'" in (root / "webui/index.html").read_text(encoding="utf-8")
    js = (root / "webui/static/commissary-import.js").read_text(encoding="utf-8")
    assert "data-vimp-modal" not in js.replace("[data-vimp-modal]", "")
    assert "setAttribute('data-vimp-modal'" not in js
    node = shutil.which("node") or shutil.which("node.exe")
    if node:
        res = subprocess.run([node, "--check", "webui/static/commissary-import.js"], cwd=str(root),
                             capture_output=True, text=True)
        assert res.returncode == 0, res.stderr
