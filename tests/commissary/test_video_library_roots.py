"""Every video Library folder is a library root: health checks, the recycle
bin, path re-rooting, naming conformance and the temp-file sweep see them.

On the live install upstream's movies_path / tv_path are empty, so without
this none of those features had a movie or TV folder to look at."""

import os
import time

import pytest

from core.commissary import video_libraries as vl
from database.video_database import VideoDatabase


@pytest.fixture()
def db(tmp_path):
    db = VideoDatabase(database_path=str(tmp_path / "video_library.db"))
    for name in ("movies", "anime-movies", "tv", "anime"):
        (tmp_path / name).mkdir()
    vl.save_libraries(db, "plex",
                      movies=[{"server_title": "Movies", "path": str(tmp_path / "movies")},
                              {"server_title": "Anime-Movies", "path": str(tmp_path / "anime-movies")}],
                      tv=[{"server_title": "TV Shows", "path": str(tmp_path / "tv")},
                          {"server_title": "Anime", "path": str(tmp_path / "anime")}])
    return db


def test_library_roots_include_each_kinds_libraries(db, tmp_path):
    from core.video.library_roots import library_roots

    norm = lambda paths: {os.path.normcase(os.path.normpath(p)) for p in paths}  # noqa: E731
    assert norm(library_roots(db, "movie")) == norm([tmp_path / "movies", tmp_path / "anime-movies"])
    assert norm(library_roots(db, "episode")) == norm([tmp_path / "tv", tmp_path / "anime"])
    assert len(library_roots(db)) == 4


def test_path_resolution_finds_files_in_any_library(db, tmp_path):
    from core.video.path_resolver import video_base_dirs

    assert any("anime-movies" in str(p) for p in video_base_dirs(db))


def test_health_reports_an_unreachable_library(db, tmp_path):
    from core.video import health

    os.rmdir(tmp_path / "anime")
    checks = health.collect(db)["checks"]
    bad = [c for c in checks if c["status"] == "error" and "unreachable" in c["detail"]]
    assert [c["label"] for c in bad] == ["Anime library"]


def test_the_temp_sweep_cleans_library_folders(db, tmp_path):
    from core.video.download_monitor import _sweep_import_temps

    stale = tmp_path / "anime" / "Bleach - S01E01.mkv.tmp.ab12cd34"
    stale.write_bytes(b"x")
    old = time.time() - 7200
    os.utime(stale, (old, old))
    assert _sweep_import_temps(db) >= 1
    assert not stale.exists()


def test_without_libraries_roots_are_upstreams(tmp_path):
    from core.video.library_roots import library_roots

    empty = VideoDatabase(database_path=str(tmp_path / "empty.db"))
    empty.set_setting("movies_path", str(tmp_path))
    assert library_roots(empty, "movie") == [str(tmp_path)]
