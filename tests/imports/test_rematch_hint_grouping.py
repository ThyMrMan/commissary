"""Per-track Re-identify when copies from one album are staged together.

The per-track Re-identify copies a library file into the staging root and
writes a hint naming the release the user chose. The copy still carries the
album tag of the album it is LEAVING, and the scanner groups loose files by
album tag -- so two copies from one album, or a copy beside a fresh download of
that album, became ONE multi-file candidate. ``_resolve_rematch_hint`` honours
only a single file, so every hint in such a group was ignored: the files were
identified from their old tags, filed back where they came from, and the hints
stayed pending for good.

These run the real scanner over real tagged FLACs and the real hint table, and
drive the real scan -> process path with only identification and matching --
the network edges -- stubbed.
"""

from __future__ import annotations

import os
import shutil
import struct

import pytest

from core.auto_import_worker import AutoImportWorker
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


def _stage_copy(staging, library, track_id, number, title, album="Old Album"):
    """What /api/reidentify/apply leaves in staging: a copy of the library file,
    still tagged with the album it is leaving, named by staged_destination."""
    original = os.path.join(library, "%02d - %s.flac" % (number, title))
    _write_flac(original, album=album, track=number, title=title)
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
def test_two_copies_from_one_album_become_two_candidates(db, staging, library):
    intro = _stage_copy(staging, library, 101, 1, "Intro")
    song = _stage_copy(staging, library, 102, 2, "Song")
    _add_hint(db, intro, 1, "Intro")
    _add_hint(db, song, 2, "Song")
    worker = AutoImportWorker(database=db, staging_path=staging)

    candidates = worker._enumerate_folders(staging)

    assert sorted(c.audio_files for c in candidates) == [[intro], [song]]
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
    represented exactly as a lone copy always was -- the shape the hint path has
    always handled. The baseline is taken BEFORE any hint exists, so it comes
    from the scan as it was; taken with a hint, both sides would come from the
    new code and a wrong shape would simply agree with itself."""
    intro = _stage_copy(staging, library, 101, 1, "Intro")
    worker = AutoImportWorker(database=db, staging_path=staging)
    (alone,) = worker._enumerate_folders(staging)

    _add_hint(db, intro, 1, "Intro")
    song = _stage_copy(staging, library, 102, 2, "Song")
    _add_hint(db, song, 2, "Song")
    beside = next(c for c in worker._enumerate_folders(staging) if c.audio_files == [intro])

    assert vars(beside) == vars(alone)


def test_only_a_pending_hint_splits_a_file_out(db, staging, library):
    """A consumed hint is history: the files it once named group as usual, which
    is also exactly the scan there was before hints were considered here."""
    intro = _stage_copy(staging, library, 101, 1, "Intro")
    song = _stage_copy(staging, library, 102, 2, "Song")
    hint_id = _add_hint(db, intro, 1, "Intro")
    conn = db._get_connection()
    try:
        consume_hint(conn.cursor(), hint_id)
        conn.commit()
    finally:
        conn.close()
    worker = AutoImportWorker(database=db, staging_path=staging)

    candidates = worker._enumerate_folders(staging)

    assert [sorted(c.audio_files) for c in candidates] == [sorted([intro, song])]


def test_a_hint_recorded_under_another_spelling_of_the_staging_folder_still_splits(
        db, staging, library):
    """The apply route and the worker resolve the staging folder separately (a
    Docker-mapped path, say), so a hint's path can be spelled differently from
    the one the scanner lists. find_hint_for_file falls back to the filename,
    which carries the track id; the scanner must bind on the same terms, or a
    hint the worker would honour is still grouped away from it."""
    intro = _stage_copy(staging, library, 101, 1, "Intro")
    song = _stage_copy(staging, library, 102, 2, "Song")
    _add_hint(db, intro, 1, "Intro", recorded_path="/app/Staging/" + os.path.basename(intro))
    _add_hint(db, song, 2, "Song", recorded_path="/app/Staging/" + os.path.basename(song))
    worker = AutoImportWorker(database=db, staging_path=staging)

    candidates = worker._enumerate_folders(staging)

    assert sorted(c.audio_files for c in candidates) == [[intro], [song]]
    assert all(worker._resolve_rematch_hint(c)[0] is not None for c in candidates)


def test_pending_hints_are_read_once_per_scan(db, staging, monkeypatch):
    """Not once per folder -- a staging tree can hold hundreds of them."""
    for name in ("A", "B", "C"):
        folder = os.path.join(staging, name)
        os.mkdir(folder)
        _write_flac(os.path.join(folder, "01.flac"), album=name, track=1)
    worker = AutoImportWorker(database=db, staging_path=staging)
    calls = []
    real = worker._pending_rematch_keys
    monkeypatch.setattr(worker, "_pending_rematch_keys", lambda: calls.append(1) or real())

    worker._enumerate_folders(staging)

    assert len(calls) == 1


# ── what the user sees: scan, then process ───────────────────────────────────
def test_both_copies_are_filed_under_the_release_the_user_chose(
        db, staging, library, tmp_path, monkeypatch):
    intro = _stage_copy(staging, library, 101, 1, "Intro")
    song = _stage_copy(staging, library, 102, 2, "Song")
    _add_hint(db, intro, 1, "Intro")
    _add_hint(db, song, 2, "Song")
    tracks = {intro: (1, "Intro"), song: (2, "Song")}

    filed = []

    def post_process(context_key, context, file_path):
        filed.append((file_path, context["spotify_album"]["id"]))
        context["_final_processed_path"] = os.path.join(
            str(tmp_path), "Library", "New Album", os.path.basename(file_path))

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

    assert sorted(filed) == [(intro, "new-album"), (song, "new-album")], _history(db)
    assert guessed == [], "tag identification ran instead of the user's choice"
    assert _pending(db, intro) is None and _pending(db, song) is None, "a hint was left pending"
