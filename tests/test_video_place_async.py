"""A slow placement must not be reported as a failed one.

Reported as: "Manual Import Placement — get the error that it couldn't be
placed, but the import goes fine with no issue."

The copy ran inside the HTTP request. The importer's own comment puts that at
"minutes over SMB" for a multi-GB release, and the server does NOT stop when the
client goes away — so a proxy timeout, a dropped connection or a browser abort
surfaced as an error while the placement completed perfectly. The frontend made
it worse twice over: both failure routes printed the same generic message, and
it refreshed the list only on success, so the placed item stayed on screen
looking unplaced. Retrying then hit "Not an unplaced import" (404), because the
row was by then completed — a second, different, equally wrong error.

Placement now runs on a worker thread. The endpoint waits a grace period first,
so a small file or a same-filesystem move still answers in the original request
with exactly the response every existing caller expects; only a genuinely slow
copy switches the client to polling.
"""

from __future__ import annotations

import os
import threading
import time

import pytest
from flask import Flask

import api.video as videoapi
from core.video import manual_place, organization
from database.video_database import VideoDatabase


@pytest.fixture()
def env(tmp_path):
    db = VideoDatabase(database_path=str(tmp_path / "video_library.db"))
    movies = tmp_path / "Movies"
    movies.mkdir()
    db.set_setting("movies_path", str(movies))
    organization.save(db, {"verify_with_ffprobe": False})

    dl_dir = tmp_path / "dl"
    dl_dir.mkdir()
    src = dl_dir / "the.matrix.1999.1080p.bluray.x265.mkv"
    src.write_bytes(b"x" * 4096)

    dl_id = db.add_video_download({
        "kind": "movie", "title": "the matrix", "release_title": src.name,
        "source": "soulseek", "username": "neo", "filename": src.name,
        "size_bytes": 4096, "target_dir": str(movies), "status": "import_failed",
        "search_ctx": "{}",
    })
    db.update_video_download(dl_id, dest_path=str(src), error="Looks like a sample")

    videoapi._video_db = db
    app = Flask(__name__)
    app.register_blueprint(videoapi.create_video_blueprint(), url_prefix="/api/video")
    try:
        yield {"client": app.test_client(), "db": db, "dl_id": dl_id,
               "src": src, "movies": movies}
    finally:
        videoapi._video_db = None
        manual_place.forget(dl_id)


_BODY = {"scope": "movie", "title": "The Matrix", "year": 1999, "media_id": 603}


# ── the fast path is unchanged ───────────────────────────────────────────────
def test_a_quick_placement_still_answers_in_the_one_request(env):
    """Every existing caller and test depends on this; going async for all
    placements would have broken them for no benefit."""
    r = env["client"].post("/api/video/import/%d/place" % env["dl_id"], json=_BODY).get_json()
    assert r["success"] is True
    assert r["status"] == "completed"
    assert not r.get("running")
    assert os.path.exists(r["dest_path"])


# ── the slow path ────────────────────────────────────────────────────────────
def test_a_slow_placement_returns_running_instead_of_blocking(env, monkeypatch):
    monkeypatch.setattr(manual_place, "DEFAULT_GRACE_SECONDS", 0.2)
    gate = threading.Event()
    # The handler does `from core.video.importer import run_import` per request,
    # so the module attribute is what it binds — patching the api module would
    # silently do nothing (it did, on the first attempt at this test).
    import core.video.importer as importer
    original = importer.run_import

    def _slow(*a, **kw):
        gate.wait(5)
        return original(*a, **kw)

    monkeypatch.setattr(importer, "run_import", _slow)

    r = env["client"].post("/api/video/import/%d/place" % env["dl_id"], json=_BODY).get_json()
    assert r["running"] is True, r
    assert r["success"] is True          # started fine — not a failure

    gate.set()
    for _ in range(100):                 # let the worker finish
        s = env["client"].get("/api/video/import/%d/place/status" % env["dl_id"]).get_json()
        if not s.get("running"):
            break
        time.sleep(0.05)
    assert s["success"] is True, s
    assert s["status"] == "completed"
    assert os.path.exists(s["dest_path"])


def test_the_copy_finishes_even_though_the_request_already_returned(env, monkeypatch):
    """The property the whole fix rests on: the client walking away — by timeout
    or otherwise — must not leave a half-done placement."""
    monkeypatch.setattr(manual_place, "DEFAULT_GRACE_SECONDS", 0.05)
    env["client"].post("/api/video/import/%d/place" % env["dl_id"], json=_BODY)
    for _ in range(100):
        if not manual_place.is_running(env["dl_id"]):
            break
        time.sleep(0.05)
    row = env["db"].get_video_download(env["dl_id"])
    assert row["status"] == "completed"
    assert os.path.exists(row["dest_path"])


def test_asking_again_while_it_runs_does_not_start_a_second_copy(env, monkeypatch):
    """A retry after a timed-out request must not copy the same file twice, on
    top of itself."""
    monkeypatch.setattr(manual_place, "DEFAULT_GRACE_SECONDS", 0.05)
    calls = []
    gate = threading.Event()
    import core.video.importer as importer
    original = importer.run_import

    def _slow(*a, **kw):
        calls.append(1)
        gate.wait(5)
        return original(*a, **kw)

    monkeypatch.setattr(importer, "run_import", _slow)

    first = env["client"].post("/api/video/import/%d/place" % env["dl_id"], json=_BODY).get_json()
    second = env["client"].post("/api/video/import/%d/place" % env["dl_id"], json=_BODY).get_json()
    assert first["running"] and second["running"]
    gate.set()
    for _ in range(100):
        if not manual_place.is_running(env["dl_id"]):
            break
        time.sleep(0.05)
    assert len(calls) == 1, "started a second placement over the first"


# ── retrying after a lost response ───────────────────────────────────────────
def test_retrying_an_already_placed_import_reports_success(env):
    """The second wrong error: the row is completed by then, and the unplaced
    guard answered 404 'Not an unplaced import'."""
    env["client"].post("/api/video/import/%d/place" % env["dl_id"], json=_BODY)
    again = env["client"].post("/api/video/import/%d/place" % env["dl_id"], json=_BODY)
    assert again.status_code == 200
    body = again.get_json()
    assert body["success"] is True
    assert body["already"] is True
    assert body["status"] == "completed"


def test_status_answers_from_the_database_when_the_job_is_forgotten(env):
    """A restart, or a reaped job — the DB row is the durable record of what
    happened, so the answer must come from it rather than defaulting to
    'failed'."""
    env["client"].post("/api/video/import/%d/place" % env["dl_id"], json=_BODY)
    manual_place.forget(env["dl_id"])
    s = env["client"].get("/api/video/import/%d/place/status" % env["dl_id"]).get_json()
    assert s["success"] is True
    assert s["status"] == "completed"


def test_status_for_an_unknown_download_is_a_404(env):
    r = env["client"].get("/api/video/import/999999/place/status")
    assert r.status_code == 404


# ── validation still fails fast, in the request ──────────────────────────────
@pytest.mark.parametrize("bad", ["", "film", "series"])
def test_a_bad_scope_is_still_rejected_immediately(env, bad):
    """Validation must not be deferred to a worker — a malformed request should
    never look like a job that started."""
    r = env["client"].post("/api/video/import/%d/place" % env["dl_id"], json={"scope": bad})
    assert r.status_code == 400
    assert not manual_place.is_running(env["dl_id"])


def test_a_missing_source_file_is_still_a_410(env):
    os.remove(env["src"])
    r = env["client"].post("/api/video/import/%d/place" % env["dl_id"], json=_BODY)
    assert r.status_code == 410
    assert not manual_place.is_running(env["dl_id"])


# ── the file landed but the record did not ───────────────────────────────────
def test_a_failed_db_write_after_a_good_copy_is_not_reported_as_failure(env, monkeypatch):
    """The rarer variant with the same symptom: the file has already moved by
    the time the row is written, so a bare failure there tells the same lie the
    timeout did."""
    def _boom(*a, **kw):
        raise RuntimeError("database is locked")

    monkeypatch.setattr(env["db"], "update_video_download", _boom)
    r = env["client"].post("/api/video/import/%d/place" % env["dl_id"], json=_BODY).get_json()
    assert r["success"] is True
    assert "warning" in r and "could not be saved" in r["warning"]
    assert os.path.exists(r["dest_path"])


# ── the worker itself ────────────────────────────────────────────────────────
def test_a_raising_job_is_recorded_not_swallowed():
    def _boom():
        raise RuntimeError("nope")

    manual_place.start(4242, _boom)
    snap = manual_place.wait(4242, 5)
    assert snap["running"] is False
    assert "nope" in snap["error"]
    manual_place.forget(4242)


def test_wait_returns_the_running_snapshot_on_timeout():
    gate = threading.Event()
    manual_place.start(4243, lambda: gate.wait(5) or {"success": True})
    snap = manual_place.wait(4243, 0.2)
    assert snap["running"] is True
    gate.set()
    manual_place.wait(4243, 5)
    manual_place.forget(4243)


def test_finished_jobs_do_not_accumulate_forever():
    for i in range(manual_place._MAX_FINISHED + 20):
        manual_place.start(90000 + i, lambda: {"success": True})
        manual_place.wait(90000 + i, 5)
    manual_place.start(99999, lambda: {"success": True})
    manual_place.wait(99999, 5)
    assert len(manual_place._jobs) <= manual_place._MAX_FINISHED + 1
    for i in range(manual_place._MAX_FINISHED + 20):
        manual_place.forget(90000 + i)
    manual_place.forget(99999)
