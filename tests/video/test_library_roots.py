"""Video library roots come from the Libraries REGISTRY, not just the flat
Settings → Downloads paths.

Destinations live in two places: a per-library Destination Folder in
Settings → Connections (the ``root_folders`` table) and the flat
movies_path / tv_path / youtube_path keys in Settings → Downloads. For
DOWNLOADS that is a deliberate three-tier fallback (resolve_download_root).

But four other subsystems read only the flat keys, and nothing syncs the two —
save_libraries never writes them back. So the natural setup (configure
libraries in Connections, leave Downloads blank) silently gave:

  * health checks with no library folders to check,
  * a recycle bin with no roots, so every delete was permanent,
  * a path resolver that couldn't re-root a moved file,
  * a naming-conformance job that skipped every file.

The four regression tests below are that bug, one per consumer; they fail
against the old code. The multi-library tests cover the quieter half: a single
flat path can only ever describe ONE library, so "Movies" + "Anime Movies"
left the second one unprotected however you configured it.
"""

from __future__ import annotations

import os

import pytest

from core.video import recycle
from core.video.path_resolver import library_roots, video_base_dirs
from database.video_database import VideoDatabase


@pytest.fixture()
def db(tmp_path):
    return VideoDatabase(database_path=str(tmp_path / "video_library.db"))


def _add_library(db, *, path, kind="movie", title="Movies", label=None,
                 server="plex", sort_order=0):
    conn = db._get_connection()
    conn.execute(
        "INSERT INTO root_folders (path, content_kind, server, server_title, label, sort_order) "
        "VALUES (?,?,?,?,?,?)", (str(path), kind, server, title, label, sort_order))
    conn.commit()
    conn.close()


# ── the registry query ───────────────────────────────────────────────────────

def test_all_library_paths_is_empty_without_rows(db):
    assert db.all_library_paths() == []


def test_all_library_paths_orders_movies_then_shows_then_youtube(db, tmp_path):
    _add_library(db, path=tmp_path / "YT", kind="youtube", title="YouTube")
    _add_library(db, path=tmp_path / "TV", kind="show", title="TV")
    _add_library(db, path=tmp_path / "Movies", kind="movie", title="Movies")

    assert db.all_library_paths() == [
        str(tmp_path / "Movies"), str(tmp_path / "TV"), str(tmp_path / "YT")]


def test_all_library_paths_honours_sort_order_within_a_kind(db, tmp_path):
    _add_library(db, path=tmp_path / "Anime", title="Anime Movies", sort_order=1)
    _add_library(db, path=tmp_path / "Movies", title="Movies", sort_order=0)

    assert db.all_library_paths("movie") == [
        str(tmp_path / "Movies"), str(tmp_path / "Anime")]


def test_all_library_paths_needs_no_configured_server(db, tmp_path):
    """The whole reason this query is server-agnostic. resolve_video_server()
    returns None on an install with no server set up, and a server-scoped
    query would then silently return nothing — reintroducing the bug."""
    from core.video.sources import resolve_video_server
    assert resolve_video_server(db) is None

    _add_library(db, path=tmp_path / "Movies")
    assert db.all_library_paths() == [str(tmp_path / "Movies")]


def test_all_library_paths_filters_by_kind(db, tmp_path):
    _add_library(db, path=tmp_path / "Movies", kind="movie")
    _add_library(db, path=tmp_path / "TV", kind="show", title="TV")

    assert db.all_library_paths("movie") == [str(tmp_path / "Movies")]
    assert db.all_library_paths("tv") == [str(tmp_path / "TV")]
    assert db.all_library_paths("show") == [str(tmp_path / "TV")]


# ── the union helper ─────────────────────────────────────────────────────────

def test_library_roots_unions_registry_and_legacy_paths(db, tmp_path):
    _add_library(db, path=tmp_path / "Movies")
    db.set_setting("tv_path", str(tmp_path / "TV"))

    assert library_roots(db) == [str(tmp_path / "Movies"), str(tmp_path / "TV")]


def test_library_roots_puts_the_registry_first(db, tmp_path):
    db.set_setting("movies_path", str(tmp_path / "OldMovies"))
    _add_library(db, path=tmp_path / "Movies")

    assert library_roots(db)[0] == str(tmp_path / "Movies")


def test_library_roots_dedupes_a_scalar_that_repeats_a_library(db, tmp_path):
    _add_library(db, path=tmp_path / "Movies")
    db.set_setting("movies_path", str(tmp_path / "Movies"))

    assert library_roots(db) == [str(tmp_path / "Movies")]


def test_library_roots_falls_back_when_no_libraries_exist(db, tmp_path):
    """Unchanged behaviour for every install that never opened the editor."""
    db.set_setting("movies_path", str(tmp_path / "Movies"))
    db.set_setting("tv_path", str(tmp_path / "TV"))

    assert library_roots(db) == [str(tmp_path / "Movies"), str(tmp_path / "TV")]


# ── regression: the four consumers, libraries set and scalars blank ──────────

def test_health_checks_a_library_folder(db, tmp_path):
    from core.video import health
    _add_library(db, path=tmp_path / "Missing", label="Anime Movies")

    checks = health.collect(db)["checks"]
    unreachable = [c for c in checks if c["status"] == "error"]
    assert unreachable, "a configured library on a down mount went unreported"
    assert "Anime Movies" in unreachable[0]["label"], (
        "the check should name the library so a multi-library install can tell "
        "which mount is down")


def test_recycle_recovers_a_file_under_a_library(db, tmp_path):
    from core.video import organization
    root = tmp_path / "Movies"
    (root / "The Matrix (1999)").mkdir(parents=True)
    victim = root / "The Matrix (1999)" / "matrix.mkv"
    victim.write_bytes(b"x" * 8)
    _add_library(db, path=root)

    settings = organization.normalize({**organization.default_settings(),
                                       "recycle_deletes": True})
    assert recycle.discard(str(victim), settings, db)["ok"] is True
    assert not victim.exists()
    # Trash entries are timestamp-prefixed (20260725_101500_matrix.mkv).
    assert list((root / recycle.TRASH_DIRNAME).rglob("*matrix.mkv")), (
        "the file was hard-deleted — with no roots the recycle bin is inert "
        "and every delete is unrecoverable")


def test_video_base_dirs_includes_library_folders(db, tmp_path):
    _add_library(db, path=tmp_path / "Movies")
    assert str(tmp_path / "Movies") in video_base_dirs(db)


def test_naming_conformance_does_not_skip_library_files(db, tmp_path):
    from core.video.repair.worker import VideoRepairWorker
    root = tmp_path / "Movies"
    mid = db.upsert_movie("plex", {"server_id": "sv-1", "title": "The Matrix",
                                   "year": 1999, "tmdb_id": 603})
    real = root / "wrong name.mkv"
    real.parent.mkdir(parents=True, exist_ok=True)
    real.write_bytes(b"x" * 32)
    conn = db._get_connection()
    conn.execute("INSERT INTO media_files (movie_id, relative_path, size_bytes, quality) "
                 "VALUES (?,?,?,?)", (mid, str(real), 32, "1080p"))
    conn.execute("UPDATE movies SET has_file=1 WHERE id=?", (mid,))
    conn.commit(); conn.close()

    _add_library(db, path=root)          # registry only — no movies_path
    VideoRepairWorker(db)._run_job("naming_conformance", forced=True)

    findings = [f for f in db.repair_get_findings(status="pending")["items"]
                if f["finding_type"] == "naming_mismatch"]
    assert findings, ("every file was skipped as 'that library has no "
                      "configured folder' despite a configured library")


# ── multi-library: one flat path can only ever describe one library ──────────

def test_recycle_routes_to_the_library_that_holds_the_file(db, tmp_path):
    from core.video import organization
    movies, anime = tmp_path / "Movies", tmp_path / "Anime"
    for d in (movies, anime):
        d.mkdir()
    _add_library(db, path=movies, title="Movies", sort_order=0)
    _add_library(db, path=anime, title="Anime Movies", sort_order=1)

    victim = anime / "akira.mkv"
    victim.write_bytes(b"x" * 8)
    settings = organization.normalize({**organization.default_settings(),
                                       "recycle_deletes": True})
    assert recycle.discard(str(victim), settings, db)["ok"] is True

    assert list((anime / recycle.TRASH_DIRNAME).rglob("*akira.mkv")), (
        "a file in the SECOND library was not recycled into its own root")
    assert not (movies / recycle.TRASH_DIRNAME).exists()


def test_naming_conformance_renders_against_the_containing_library(db, tmp_path):
    from core.video.repair.worker import VideoRepairWorker
    movies, anime = tmp_path / "Movies", tmp_path / "Anime"
    _add_library(db, path=movies, title="Movies", sort_order=0)
    _add_library(db, path=anime, title="Anime Movies", sort_order=1)

    mid = db.upsert_movie("plex", {"server_id": "sv-2", "title": "Akira",
                                   "year": 1988, "tmdb_id": 149})
    real = anime / "akira raw.mkv"
    real.parent.mkdir(parents=True, exist_ok=True)
    real.write_bytes(b"x" * 32)
    conn = db._get_connection()
    conn.execute("INSERT INTO media_files (movie_id, relative_path, size_bytes, quality) "
                 "VALUES (?,?,?,?)", (mid, str(real), 32, "1080p"))
    conn.execute("UPDATE movies SET has_file=1 WHERE id=?", (mid,))
    conn.commit(); conn.close()

    VideoRepairWorker(db)._run_job("naming_conformance", forced=True)
    findings = [f for f in db.repair_get_findings(status="pending")["items"]
                if f["finding_type"] == "naming_mismatch"]
    assert findings, "the file in the second library was not examined"

    expected = findings[0]["details"]["expected_path"]
    assert os.path.abspath(str(anime)) in os.path.abspath(expected), (
        "the rename target was built from the PRIMARY library — a file in the "
        f"second one would be moved out of it (got {expected})")
