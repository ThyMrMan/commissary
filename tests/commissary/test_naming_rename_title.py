"""Rename one title's files, and rename a title's files before an import.

A template change used to fork a show across two naming eras: the next
episode landed in a folder named the new way while every earlier season sat
in the old one, and the media server saw two shows.
"""

from __future__ import annotations

import json
import os

import pytest
from flask import Flask

import core.video.mass_rename as mr
from core.commissary import naming
from core.video import organization as org
from database.video_database import VideoDatabase

OLD_EP = "$series/Season $season/$series - S$seasonE$episode"
NEW_EP = "{Series Title} {(Series Year)}/Season {season:00}/{Series Title} - S{season:00}E{episode:00}"
OLD_MOVIE = "$title ($year)/$title"


@pytest.fixture()
def env(tmp_path):
    import api.video as videoapi
    db = VideoDatabase(database_path=str(tmp_path / "video_library.db"))
    videoapi._video_db = db
    movies, tv = tmp_path / "Movies", tmp_path / "TV"
    movies.mkdir()
    tv.mkdir()
    db.set_setting("movies_path", str(movies))
    db.set_setting("tv_path", str(tv))
    db.set_setting("organization", json.dumps({
        "movie_template": OLD_MOVIE, "episode_template": OLD_EP, "transfer_mode": "copy",
        "verify_with_ffprobe": False, "save_artwork": False, "write_nfo": False,
        "download_subtitles": False, "carry_subtitles": False}))
    yield db, movies, tv
    videoapi._video_db = None


def _place(path):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "wb") as fh:
        fh.write(b"x" * 1024)


def _seed(db, movies, tv):
    """A show with two episodes and a movie, all named by the OLD templates."""
    eps = []
    for e in (1, 2):
        p = org.render_path("episode", str(tv), {"series": "Silo", "season": 1, "episode": e},
                            {"episode_template": OLD_EP}, ".mkv")["path"]
        _place(p)
        eps.append(p)
    mpath = org.render_path("movie", str(movies), {"title": "Heat", "year": 1995},
                            {"movie_template": OLD_MOVIE}, ".mkv")["path"]
    _place(mpath)
    db.upsert_show_tree("plex", {"server_id": "s1", "title": "Silo", "year": 2023, "tmdb_id": 125988,
                                 "seasons": [{"season_number": 1, "episodes": [
                                     {"episode_number": e, "server_id": "e%d" % e,
                                      "file": {"relative_path": eps[e - 1], "size_bytes": 1024}}
                                     for e in (1, 2)]}]})
    mid = db.upsert_movie("plex", {"server_id": "m1", "title": "Heat", "year": 1995, "tmdb_id": 949,
                                   "file": {"relative_path": mpath, "size_bytes": 1024}})
    conn = db._get_connection()
    conn.execute("UPDATE episodes SET has_file=1")
    conn.execute("UPDATE movies SET has_file=1 WHERE id=?", (mid,))
    show_id = conn.execute("SELECT id FROM shows").fetchone()[0]
    conn.commit()
    conn.close()
    return show_id, mid, eps, mpath


def _new_template(db):
    s = json.loads(db.get_setting("organization"))
    s["episode_template"] = NEW_EP
    db.set_setting("organization", json.dumps(s))


# ── scope and a one-off template ────────────────────────────────────────────

def test_a_scoped_preview_sees_only_that_title(env):
    db, movies, tv = env
    show_id, mid, _eps, _m = _seed(db, movies, tv)
    _new_template(db)
    res = mr.preview(scope=("show", show_id))
    assert len(res["entries"]) == 2 and all(e["kind"] == "episode" for e in res["entries"])
    assert mr.preview(scope=("movie", mid))["entries"] == []        # still on its template
    assert len(mr.preview()["entries"]) == 2                          # the library-wide pass


def test_a_typed_template_previews_without_being_saved(env):
    db, movies, tv = env
    show_id, _mid, _eps, _m = _seed(db, movies, tv)
    res = mr.preview(scope=("show", show_id), template=NEW_EP)
    assert len(res["entries"]) == 2
    assert json.loads(db.get_setting("organization"))["episode_template"] == OLD_EP


def test_a_scoped_apply_renames_only_what_was_previewed(env):
    db, movies, tv = env
    show_id, _mid, eps, mpath = _seed(db, movies, tv)
    res = mr.apply(scope=("show", show_id), template=NEW_EP)
    assert res["renamed"] == 2 and not res["failures"]
    assert not os.path.exists(eps[0]) and os.path.exists(mpath)
    new = os.path.join(str(tv), "Silo (2023)", "Season 01", "Silo - S01E01.mkv")
    assert os.path.exists(new)


# ── rename before import ────────────────────────────────────────────────────

def _episode_dl(tmp_path, tv):
    src = tmp_path / "dl" / "Silo.S01E03.1080p.WEB-DL-NTb.mkv"
    src.parent.mkdir(exist_ok=True)
    src.write_bytes(b"x" * 1024)
    return {"id": 9, "kind": "show", "title": "Silo", "source": "torrent", "media_id": 125988,
            "media_source": "tmdb", "release_title": "Silo.S01E03.1080p.WEB-DL-NTb",
            "size_bytes": 1024, "target_dir": str(tv),
            "search_ctx": json.dumps({"scope": "episode", "title": "Silo", "year": 2023,
                                      "season": 1, "episode": 3})}, src


def test_an_import_renames_the_shows_old_files_first(env, tmp_path):
    from core.video.download_monitor import _make_organizer
    db, movies, tv = env
    _seed(db, movies, tv)
    _new_template(db)
    dl, src = _episode_dl(tmp_path, tv)
    patch = _make_organizer(db)(dl, str(src))
    assert patch["status"] == "completed", patch
    season = os.path.join(str(tv), "Silo (2023)", "Season 01")
    assert sorted(os.listdir(season)) == ["Silo - S01E01.mkv", "Silo - S01E02.mkv", "Silo - S01E03.mkv"]
    assert not os.path.exists(os.path.join(str(tv), "Silo")), "no second, old-style show folder"


def test_switched_off_it_leaves_them(env, tmp_path):
    from core.video.download_monitor import _make_organizer
    db, movies, tv = env
    _seed(db, movies, tv)
    _new_template(db)
    naming.set_rename_before_import(db, False)
    dl, src = _episode_dl(tmp_path, tv)
    _make_organizer(db)(dl, str(src))
    assert os.path.exists(os.path.join(str(tv), "Silo", "Season 01", "Silo - S01E01.mkv"))


def test_a_locked_show_is_not_renamed(env, tmp_path):
    from core.commissary import import_lock
    from core.video.download_monitor import _make_organizer
    db, movies, tv = env
    show_id, _mid, eps, _m = _seed(db, movies, tv)
    _new_template(db)
    import_lock.set_lock(db, "show", show_id, True)
    dl, src = _episode_dl(tmp_path, tv)
    patch = _make_organizer(db)(dl, str(src))
    assert patch["status"] == "import_failed" and all(os.path.exists(p) for p in eps)


def test_the_setting_reads_the_old_forks_value_until_set():
    class DB:
        def __init__(self):
            self.s = {"organization": json.dumps({"rename_before_import": False})}

        def get_setting(self, k):
            return self.s.get(k)

        def set_setting(self, k, v):
            self.s[k] = v

    db = DB()
    assert naming.rename_before_import_enabled(db) is False
    naming.set_rename_before_import(db, True)
    assert naming.rename_before_import_enabled(db) is True
    assert naming.rename_before_import_enabled(type("E", (), {"get_setting": lambda s, k: None})())


# ── the endpoints ───────────────────────────────────────────────────────────

@pytest.fixture()
def client(env):
    import api.video as videoapi
    app = Flask(__name__)
    app.register_blueprint(videoapi.create_video_blueprint(), url_prefix="/api/video")
    return app.test_client()


def test_preview_and_apply_through_the_endpoints(client, env):
    db, movies, tv = env
    show_id, _mid, _eps, _m = _seed(db, movies, tv)
    d = client.get("/api/video/libraries/rename/show/%d?template=%s" % (show_id, NEW_EP)).get_json()
    assert len(d["entries"]) == 2 and d["template"] == OLD_EP and d["rendered_with"] == NEW_EP
    toks = {t["token"]: t for t in d["tokens"]}
    assert toks["{Series Title}"]["example"] == "Silo" and toks["$series"]["example"] == "Silo"
    assert toks["{MediaInfo VideoBitDepth}"]["import_only"] is True
    one = d["entries"][0]["key"]
    r = client.post("/api/video/libraries/rename/show/%d" % show_id,
                    json={"keys": [one], "template": NEW_EP}).get_json()
    assert r["renamed"] == 1
    assert client.get("/api/video/libraries/naming/rename-first").get_json() == {"enabled": True}
    assert client.post("/api/video/libraries/naming/rename-first",
                       json={"enabled": False}).get_json() == {"enabled": False}


def test_the_rename_endpoints_are_admin_only(client, env):
    from flask import g

    @client.application.before_request
    def _member():
        g.is_admin = False

    assert client.get("/api/video/libraries/rename/show/1").status_code == 403
    assert client.post("/api/video/libraries/rename/show/1", json={}).status_code == 403


def test_the_sweep_never_removes_a_library_root(env):
    db, movies, tv = env
    show = tv / "Old Name" / "Season 01"
    show.mkdir(parents=True)
    naming.sweep_empty_parents(db, str(show))
    assert not (tv / "Old Name").exists() and tv.exists(), "emptied folders go, the root stays"
    naming.sweep_empty_parents(db, str(movies))
    assert movies.exists()


def test_the_panel_harness_passes_and_the_page_wires_it():
    import shutil
    import subprocess
    from pathlib import Path
    root = Path(__file__).resolve().parents[2]
    node = shutil.which("node") or shutil.which("node.exe")
    if node is None:
        pytest.skip("node not available")
    res = subprocess.run([node, "tests/js/commissary_rename_panel_harness.mjs"], cwd=str(root),
                         capture_output=True, text=True, timeout=120)
    assert res.returncode == 0 and "all assertions passed" in res.stdout, res.stdout + res.stderr
    detail = (root / "webui/static/video/video-detail.js").read_text(encoding="utf-8")
    assert "CommissaryRename.applies(d)" in detail
    assert "which === 'commissary-rename') CommissaryRename.open(data)" in detail
    assert "filename='commissary-rename-panel.js'" in (root / "webui/index.html").read_text(encoding="utf-8")
