"""Re-identify copies staged together must each keep their own hint.

Upstream's album reassign (``apply_album_reassign`` behind
``/api/reassign/apply``) stages one copy per track into the staging ROOT and
writes a hint for each. Every copy still carries the album tag of the album it
is LEAVING, and the auto-import scanner groups loose files by album tag -- so a
reassign of two or more tracks became ONE multi-file candidate.
``_resolve_rematch_hint`` honours only a single file, so every hint in the group
was ignored: the files were identified from their old tags and filed back where
they came from, and the hints stayed pending for good.

Ported from the legacy fork (Release 2.3.6). Upstream 3.5.9 (#1567) now splits
every ``[reid-...]`` copy out itself, so the fork's own split was dropped; these
tests stay as a check that upstream's covers the same cases. These run the real reassign
staging, the real scanner over real tagged FLACs and the real hint table, and
drive the real scan -> process path with only identification and matching --
the network edges -- stubbed.
"""

from __future__ import annotations

import os
import shutil
import struct

import pytest

from core.auto_import_worker import AutoImportWorker
from core.imports.reassign_album import apply_album_reassign, build_reassign_plan
from core.imports.rematch_apply import staged_destination
from core.imports.rematch_hints import (
    RematchHint,
    consume_hint,
    create_hint,
    find_hint_for_file,
    quick_file_signature,
)
from database.music_database import MusicDatabase


def _write_flac(path, *, album="", track=0, title="Test"):
    """A real, minimal FLAC with tags -- the scanner reads them with mutagen."""
    from mutagen.flac import FLAC

    streaminfo = bytearray(34)
    streaminfo[0:2] = struct.pack(">H", 4096)
    streaminfo[2:4] = struct.pack(">H", 4096)
    streaminfo[10] = 0x0A
    streaminfo[12] = 0x70
    with open(path, "wb") as f:
        f.write(b"fLaC" + bytes([0x80, 0x00, 0x00, 0x22]) + bytes(streaminfo))
    audio = FLAC(path)
    if album:
        audio["ALBUM"] = album
    if track:
        audio["TRACKNUMBER"] = str(track)
    audio["DISCNUMBER"] = "1"
    audio["TITLE"] = title
    audio.save()


class _Config:
    """Settings as the worker reads them: ``config_manager.get(key, default)``."""

    def get(self, key, default=None):
        return default


class _InlineExecutor:
    """Runs submitted work at once, so a scan has finished processing by the
    time ``_scan_and_submit`` returns."""

    def submit(self, fn, *args, **kwargs):
        fn(*args, **kwargs)


@pytest.fixture
def db(tmp_path):
    return MusicDatabase(str(tmp_path / "music.db"))


@pytest.fixture
def staging(tmp_path):
    path = tmp_path / "Staging"
    path.mkdir()
    return str(path)


@pytest.fixture
def library(tmp_path):
    path = tmp_path / "Library" / "Old Album"
    path.mkdir(parents=True)
    return str(path)


def _library_track(library, track_id, number, title, album="Old Album"):
    path = os.path.join(library, "%02d - %s.flac" % (number, title))
    _write_flac(path, album=album, track=number, title=title)
    return {"id": track_id, "title": title, "track_number": number, "file_path": path}


def _reassign(db, staging, local_tracks):
    """What /api/reassign/apply does: map the album, stage every copy, write hints."""
    targets = [{"id": "new-track-%d" % t["track_number"], "name": t["title"],
                "track_number": t["track_number"], "disc_number": 1} for t in local_tracks]
    plan = build_reassign_plan(local_tracks, targets)
    conn = db._get_connection()
    try:
        result = apply_album_reassign(
            plan, source="spotify", album_id="new-album", album_name="New Album",
            artist_id="artist-1", artist_name="Artist", album_type="album",
            staging_dir=staging, cursor=conn.cursor(),
        )
        conn.commit()
    finally:
        conn.close()
    assert not result["failed"] and not result["skipped"], result
    return [s["staged_path"] for s in result["staged"]]


def _stage_copy(staging, library, track_id, number, title, album="Old Album"):
    """A per-track Re-identify copy, named by staged_destination."""
    original = _library_track(library, track_id, number, title, album)["file_path"]
    staged = staged_destination(staging, original, track_id)
    shutil.copy2(original, staged)
    return staged


def _add_hint(db, staged_path, number, title, *, recorded_path=None):
    conn = db._get_connection()
    try:
        hint_id = create_hint(conn.cursor(), RematchHint(
            staged_path=recorded_path or staged_path,
            content_hash=quick_file_signature(staged_path),
            source="spotify", album_id="new-album", album_name="New Album",
            artist_id="artist-1", artist_name="Artist", album_type="album",
            track_id="new-track-%d" % number, track_title=title,
            track_number=number, disc_number=1,
        ))
        conn.commit()
    finally:
        conn.close()
    return hint_id


def _pending(db, staged_path):
    conn = db._get_connection()
    try:
        return find_hint_for_file(conn.cursor(), staged_path)
    finally:
        conn.close()


def _history(db):
    conn = db._get_connection()
    try:
        return [dict(r) for r in conn.execute("SELECT * FROM auto_import_history").fetchall()]
    finally:
        conn.close()


# ── the scanner ──────────────────────────────────────────────────────────────
def test_an_album_reassign_gives_every_copy_its_own_candidate(db, staging, library):
    staged = _reassign(db, staging, [
        _library_track(library, 101, 1, "Intro"),
        _library_track(library, 102, 2, "Song"),
        _library_track(library, 103, 3, "Outro"),
    ])
    worker = AutoImportWorker(database=db, staging_path=staging)

    candidates = worker._enumerate_folders(staging)

    assert sorted(c.audio_files for c in candidates) == sorted([p] for p in staged)
    for candidate in candidates:
        hint, identification = worker._resolve_rematch_hint(candidate)
        assert hint is not None, "the hint for %s was ignored" % candidate.audio_files[0]
        assert identification["album_id"] == "new-album"


def test_a_copy_never_joins_a_download_of_the_album_it_is_leaving(db, staging, library):
    copy = _stage_copy(staging, library, 101, 1, "Intro")
    _add_hint(db, copy, 1, "Intro")
    download = []
    for number in (1, 2, 3):
        path = os.path.join(staging, "Old Album %d.flac" % number)
        _write_flac(path, album="Old Album", track=number, title="Track %d" % number)
        download.append(path)
    worker = AutoImportWorker(database=db, staging_path=staging)

    candidates = worker._enumerate_folders(staging)

    assert sorted(c.audio_files for c in candidates) == sorted([[copy], sorted(download)])


def test_a_copy_gets_the_candidate_it_would_have_had_alone(db, staging, library):
    """Not a new kind of candidate: beside another copy, a hinted copy is
    represented exactly as a lone copy always was. The baseline is taken BEFORE
    any hint exists, so it comes from the scan as it was."""
    intro = _stage_copy(staging, library, 101, 1, "Intro")
    worker = AutoImportWorker(database=db, staging_path=staging)
    (alone,) = worker._enumerate_folders(staging)

    _add_hint(db, intro, 1, "Intro")
    song = _stage_copy(staging, library, 102, 2, "Song")
    _add_hint(db, song, 2, "Song")
    beside = next(c for c in worker._enumerate_folders(staging) if c.audio_files == [intro])

    assert vars(beside) == vars(alone)


def test_a_hint_recorded_under_another_spelling_of_the_staging_folder_still_splits(
        db, staging, library):
    """The apply route and the worker resolve the staging folder separately (a
    Docker-mapped path, say). find_hint_for_file falls back to the filename,
    which carries the track id; the scanner binds on the same terms."""
    intro = _stage_copy(staging, library, 101, 1, "Intro")
    song = _stage_copy(staging, library, 102, 2, "Song")
    _add_hint(db, intro, 1, "Intro", recorded_path="/app/Staging/" + os.path.basename(intro))
    _add_hint(db, song, 2, "Song", recorded_path="/app/Staging/" + os.path.basename(song))
    worker = AutoImportWorker(database=db, staging_path=staging)

    candidates = worker._enumerate_folders(staging)

    assert sorted(c.audio_files for c in candidates) == [[intro], [song]]
    assert all(worker._resolve_rematch_hint(c)[0] is not None for c in candidates)


# ── what the user sees: reassign, scan, then process ─────────────────────────
def test_a_reassigned_album_is_filed_under_the_release_the_user_chose(
        db, staging, library, tmp_path, monkeypatch):
    staged = _reassign(db, staging, [
        _library_track(library, 101, 1, "Intro"),
        _library_track(library, 102, 2, "Song"),
    ])
    tracks = {staged[0]: (1, "Intro"), staged[1]: (2, "Song")}

    filed = []

    def post_process(context_key, context, file_path):
        filed.append((file_path, context["spotify_album"]["id"]))
        # What the real pipeline leaves on a successful import: the landed
        # file and its success flag.
        landed = os.path.join(str(tmp_path), "Library", "New Album", os.path.basename(file_path))
        os.makedirs(os.path.dirname(landed), exist_ok=True)
        shutil.copy2(file_path, landed)
        context["_final_processed_path"] = landed
        context["_pipeline_import_succeeded"] = True

    worker = AutoImportWorker(database=db, staging_path=staging,
                              process_callback=post_process, config_manager=_Config())
    worker._executor = _InlineExecutor()
    monkeypatch.setattr("core.imports.side_effects.is_active_media_server_ready",
                        lambda: (True, ""))

    guessed = []

    def identify_from_tags(candidate):
        # What tag identification finds for these files: the album they are leaving.
        guessed.append(list(candidate.audio_files))
        return {"album_id": "old-album", "album_name": "Old Album", "artist_name": "Artist",
                "source": "spotify", "method": "tags"}

    def match_tracks(candidate, identification):
        matches = [{"file": f, "confidence": 1.0,
                    "track": {"id": "t%d" % tracks[f][0], "name": tracks[f][1],
                              "track_number": tracks[f][0], "disc_number": 1}}
                   for f in candidate.audio_files]
        return {"matches": matches, "unmatched_files": [], "confidence": 1.0,
                "matched_count": len(matches), "total_tracks": 12,
                "album_data": {"id": identification["album_id"],
                               "name": identification["album_name"], "total_tracks": 12}}

    monkeypatch.setattr(worker, "_identify_folder", identify_from_tags)
    monkeypatch.setattr(worker, "_match_tracks", match_tracks)

    worker._scan_and_submit()   # first sight: files must hold still for one cycle
    worker._scan_and_submit()   # stable: identify, match, import

    assert sorted(filed) == sorted((p, "new-album") for p in staged), _history(db)
    assert guessed == [], "tag identification ran instead of the user's choice"
    assert all(_pending(db, p) is None for p in staged), "a hint was left pending"
