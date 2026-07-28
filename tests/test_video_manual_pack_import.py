"""Manually import a whole season folder, not one episode at a time.

The automated path has fanned a pack out into per-episode imports since 1.8.0.
Manual import could only take a single FILE, so a season pack that arrived any
other way — moved in by hand, grabbed outside SoulSync, or left behind by a
failed auto-import — had to be placed twelve times, answering "which show is
this?" on every one.

A folder becomes ONE queued row and ONE identity choice. The season and episode
numbers still come from each FILE: stamping the dialog's numbers across every
member would file the whole season on top of itself, which is the one mistake
that would be worse than doing it by hand.
"""

from __future__ import annotations

import json
import os

import pytest
from flask import Flask, g

from database.video_database import VideoDatabase


@pytest.fixture()
def env(tmp_path, monkeypatch):
    import api.video as videoapi
    import core.video.sources as sources
    monkeypatch.setattr(sources, "resolve_video_server", lambda *a, **k: "plex")
    db = VideoDatabase(database_path=str(tmp_path / "video_library.db"))
    videoapi._video_db = db
    app = Flask(__name__)
    app.register_blueprint(videoapi.create_video_blueprint(), url_prefix="/api/video")

    @app.before_request
    def _p():
        g.profile_id = 1; g.is_admin = True; g.can_download = True; g.allowed_sides = "both"

    pack = tmp_path / "Show.S01.1080p.WEB-DL"
    pack.mkdir()
    for n in range(1, 5):
        (pack / ("Show.S01E%02d.1080p.WEB-DL.mkv" % n)).write_bytes(b"x" * 2048)
    # Things a pack carries that are NOT episodes.
    (pack / "sample.mkv").write_bytes(b"x" * 10)
    (pack / "Show.S01.nfo").write_text("notes", encoding="utf-8")
    (pack / "behind the scenes.mkv").write_bytes(b"x" * 2048)

    try:
        yield {"client": app.test_client(), "db": db, "pack": pack, "tmp": tmp_path}
    finally:
        videoapi._video_db = None


# ── queueing a folder ────────────────────────────────────────────────────────
def test_adding_a_folder_queues_one_row_for_the_pack(env):
    r = env["client"].post("/api/video/import/add", json={"path": str(env["pack"])}).get_json()
    assert r["success"] and r["scope"] == "season" and r["episodes"] == 4
    rows = env["db"].get_import_failed_video_downloads()
    assert len(rows) == 1
    assert rows[0]["dest_path"] == str(env["pack"])
    assert json.loads(rows[0]["search_ctx"])["scope"] == "season"


def test_a_single_season_is_prefilled(env):
    env["client"].post("/api/video/import/add", json={"path": str(env["pack"])})
    ctx = json.loads(env["db"].get_import_failed_video_downloads()[0]["search_ctx"])
    assert ctx["season"] == 1


def test_a_mixed_pack_leaves_the_season_blank(env):
    """A full-series pack spans seasons. Picking one of them to show in the
    dialog would be a guess — and the import doesn't use it anyway."""
    (env["pack"] / "Show.S02E01.1080p.mkv").write_bytes(b"x" * 2048)
    env["client"].post("/api/video/import/add", json={"path": str(env["pack"])})
    ctx = json.loads(env["db"].get_import_failed_video_downloads()[0]["search_ctx"])
    assert ctx["season"] is None


def test_extras_and_samples_are_not_counted_as_episodes(env):
    r = env["client"].post("/api/video/import/add", json={"path": str(env["pack"])}).get_json()
    assert r["episodes"] == 4          # not the sample, nfo or "behind the scenes"


def test_a_folder_with_no_numbered_episodes_is_refused(env):
    d = env["tmp"] / "Some Movie (2021)"
    d.mkdir()
    (d / "Some.Movie.2021.1080p.mkv").write_bytes(b"x" * 2048)
    r = env["client"].post("/api/video/import/add", json={"path": str(d)})
    assert r.status_code == 400
    assert "episode" in r.get_json()["error"].lower()
    assert env["db"].get_import_failed_video_downloads() == []


def test_adding_the_same_folder_twice_is_idempotent(env):
    a = env["client"].post("/api/video/import/add", json={"path": str(env["pack"])}).get_json()
    b = env["client"].post("/api/video/import/add", json={"path": str(env["pack"])}).get_json()
    assert b.get("already") is True and b["id"] == a["id"]
    assert len(env["db"].get_import_failed_video_downloads()) == 1


def test_a_single_file_still_queues_as_one_episode(env):
    """The existing behaviour must be untouched."""
    f = env["pack"] / "Show.S01E01.1080p.WEB-DL.mkv"
    r = env["client"].post("/api/video/import/add", json={"path": str(f)}).get_json()
    assert r["scope"] == "episode"


def test_a_missing_path_is_a_404(env):
    r = env["client"].post("/api/video/import/add", json={"path": str(env["tmp"] / "nope")})
    assert r.status_code == 404


# ── the preview ──────────────────────────────────────────────────────────────
def test_the_preview_lists_what_would_be_imported(env):
    r = env["client"].get("/api/video/import/pack-preview?path=%s" % env["pack"]).get_json()
    assert r["count"] == 4 and r["seasons"] == [1]
    assert [(i["season"], i["episode"]) for i in r["items"]] == [(1, n) for n in range(1, 5)]


def test_the_preview_refuses_a_non_folder(env):
    assert env["client"].get("/api/video/import/pack-preview?path=%s"
                             % (env["pack"] / "sample.mkv")).status_code == 404


# ── browse offers the folder ─────────────────────────────────────────────────
def test_browse_flags_a_folder_that_looks_like_a_pack(env):
    r = env["client"].get("/api/video/import/browse?path=%s" % env["pack"]).get_json()
    assert r["pack"]["count"] == 4 and r["pack"]["seasons"] == [1]


def test_browse_does_not_flag_a_folder_with_one_episode(env):
    """One episode is just a file — offering to import it 'as a pack' would only
    take away the per-episode identity fields."""
    d = env["tmp"] / "single"
    d.mkdir()
    (d / "Show.S01E01.mkv").write_bytes(b"x" * 2048)
    r = env["client"].get("/api/video/import/browse?path=%s" % d).get_json()
    assert r["pack"] is None


# ── placing the whole folder ─────────────────────────────────────────────────
def test_every_member_keeps_its_own_episode_number(monkeypatch, env):
    """The override carries the SHOW; the files carry the numbering. If the
    dialog's episode number were applied to all of them they would overwrite
    each other."""
    from core.video import importer

    seen = []

    def _fake_run_import(dl, src_path, **kw):
        ov = kw.get("override") or {}
        seen.append((os.path.basename(src_path), ov.get("season"), ov.get("episode"),
                     ov.get("title")))
        return {"status": "completed", "dest_path": src_path}

    monkeypatch.setattr(importer, "run_import", _fake_run_import)
    patch = importer.run_season_import(
        {"search_ctx": json.dumps({"scope": "season"}), "release_title": "Show.S01"},
        str(env["pack"]), fs=None,
        lister=lambda root: [os.path.join(b, n) for b, _d, ns in os.walk(root) for n in ns],
        force=True, override={"scope": "season", "title": "Chosen Show", "media_id": 42,
                              "season": 9, "episode": 9, "episode_title": "wrong"})
    assert patch["status"] == "completed"
    assert sorted(s[2] for s in seen) == [1, 2, 3, 4]      # from the FILES
    assert {s[1] for s in seen} == {1}
    assert {s[3] for s in seen} == {"Chosen Show"}         # from the DIALOG


def test_a_stale_episode_title_is_not_stamped_on_every_member(monkeypatch, env):
    from core.video import importer
    seen = []
    monkeypatch.setattr(importer, "run_import",
                        lambda dl, p, **kw: (seen.append(kw.get("override") or {}),
                                             {"status": "completed", "dest_path": p})[1])
    importer.run_season_import(
        {"search_ctx": "{}", "release_title": "p"}, str(env["pack"]), fs=None,
        lister=lambda root: [os.path.join(b, n) for b, _d, ns in os.walk(root) for n in ns],
        force=True, override={"title": "S", "episode_title": "Pilot"})
    assert all("episode_title" not in o for o in seen)


def test_force_is_passed_through_to_every_member(monkeypatch, env):
    """Manual placement means the user has overruled the matcher; a member that
    would fail the automatic identity check must still be placed."""
    from core.video import importer
    forced = []
    monkeypatch.setattr(importer, "run_import",
                        lambda dl, p, **kw: (forced.append(kw.get("force")),
                                             {"status": "completed", "dest_path": p})[1])
    importer.run_season_import(
        {"search_ctx": "{}", "release_title": "p"}, str(env["pack"]), fs=None,
        lister=lambda root: [os.path.join(b, n) for b, _d, ns in os.walk(root) for n in ns],
        force=True, override={"title": "S"})
    assert forced and all(forced)


def test_the_automated_path_is_unchanged_without_an_override(monkeypatch, env):
    """No override → no per-member override, exactly as before this existed."""
    from core.video import importer
    seen = []
    monkeypatch.setattr(importer, "run_import",
                        lambda dl, p, **kw: (seen.append(kw.get("override")),
                                             {"status": "completed", "dest_path": p})[1])
    importer.run_season_import(
        {"search_ctx": "{}", "release_title": "p"}, str(env["pack"]), fs=None,
        lister=lambda root: [os.path.join(b, n) for b, _d, ns in os.walk(root) for n in ns])
    assert seen and all(o is None for o in seen)
