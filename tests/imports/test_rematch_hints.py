"""#889 Phase 1: the re-identify hint store — create / find / consume.

The hint is the single-use, user-designated "which release" answer the import
flow reads at the top of matching. These lock down: a hint round-trips, it's
found by staged path, found by content_hash when the path missed (rename-proof),
found by basename when the dir changed, consumed exactly once, and that a
consumed hint is never handed back.
"""

from __future__ import annotations

import sqlite3

import pytest

from core.imports.rematch_hints import (
    RematchHint,
    consume_hint,
    create_hint,
    find_hint_for_file,
    list_pending_hints,
    pending_hint_written_after,
    quick_file_signature,
)

# The slice of the real schema this module touches (kept in sync with
# database/music_database.py's rematch_hints CREATE).
_SCHEMA = """
CREATE TABLE rematch_hints (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    staged_path TEXT NOT NULL,
    content_hash TEXT,
    source TEXT NOT NULL,
    isrc TEXT,
    track_id TEXT,
    album_id TEXT,
    artist_id TEXT,
    track_title TEXT,
    album_name TEXT,
    artist_name TEXT,
    album_type TEXT,
    track_number INTEGER,
    disc_number INTEGER,
    replace_track_id INTEGER,
    exempt_dedup INTEGER NOT NULL DEFAULT 1,
    status TEXT NOT NULL DEFAULT 'pending',
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    consumed_at TIMESTAMP
)
"""


@pytest.fixture
def cur():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript(_SCHEMA)
    yield conn.cursor()
    conn.close()


def _hint(**kw):
    base = dict(
        staged_path="/staging/Song.flac",
        source="spotify",
        isrc="USABC1234567",
        track_id="trk_1",
        album_id="alb_album1",
        artist_id="art_1",
        track_title="Song",
        album_name="Album1",
        artist_name="Artist",
        album_type="album",
        track_number=5,
        disc_number=1,
        replace_track_id=42,
    )
    base.update(kw)
    return RematchHint(**base)


def test_create_and_find_by_path_roundtrips(cur):
    new_id = create_hint(cur, _hint())
    assert new_id > 0
    got = find_hint_for_file(cur, "/staging/Song.flac")
    assert got is not None
    assert got.id == new_id
    assert got.album_id == "alb_album1" and got.album_type == "album"
    assert got.isrc == "USABC1234567"
    assert got.track_number == 5 and got.disc_number == 1
    assert got.replace_track_id == 42
    assert got.exempt_dedup is True            # always set for a user-designated re-identify
    assert got.status == "pending"


def test_find_by_content_hash_when_path_missed(cur):
    create_hint(cur, _hint(content_hash="deadbeef"))
    # Watcher renamed/moved the file → path lookup misses, hash rescues it.
    assert find_hint_for_file(cur, "/totally/different.flac") is None
    got = find_hint_for_file(cur, "/totally/different.flac", content_hash="deadbeef")
    assert got is not None and got.album_name == "Album1"


def test_find_by_basename_when_dir_changed(cur):
    create_hint(cur, _hint(staged_path="/staging/in/Song.flac"))
    # Same filename, different directory (watcher moved it deeper).
    got = find_hint_for_file(cur, "/staging/processing/Song.flac")
    assert got is not None and got.track_id == "trk_1"


def test_consume_is_single_use(cur):
    new_id = create_hint(cur, _hint())
    assert find_hint_for_file(cur, "/staging/Song.flac") is not None
    consume_hint(cur, new_id)
    # Consumed → never handed back, by path or by hash.
    assert find_hint_for_file(cur, "/staging/Song.flac") is None
    assert find_hint_for_file(cur, "/x", content_hash=None) is None


def test_list_pending_excludes_consumed(cur):
    a = create_hint(cur, _hint(staged_path="/staging/A.flac"))
    create_hint(cur, _hint(staged_path="/staging/B.flac"))
    assert len(list_pending_hints(cur)) == 2
    consume_hint(cur, a)
    pend = list_pending_hints(cur)
    assert len(pend) == 1 and pend[0].staged_path == "/staging/B.flac"


def test_newest_pending_wins_on_duplicate_path(cur):
    create_hint(cur, _hint(album_id="alb_old"))
    create_hint(cur, _hint(album_id="alb_new"))    # user re-picked for the same file
    got = find_hint_for_file(cur, "/staging/Song.flac")
    assert got.album_id == "alb_new"


def test_exempt_dedup_false_roundtrips(cur):
    create_hint(cur, _hint(staged_path="/staging/Keep.flac", exempt_dedup=False))
    got = find_hint_for_file(cur, "/staging/Keep.flac")
    assert got.exempt_dedup is False


# ── asking again: a hint written after an attempt ─────────────────────────────
_REID = "/staging/Song [reid-42].flac"
_ATTEMPT = "2026-09-13 10:00:00"


def _written_at(cur, hint_id, when):
    cur.execute("UPDATE rematch_hints SET created_at = ? WHERE id = ?", (when, hint_id))


@pytest.mark.parametrize("written, expected", [
    ("2026-09-13 10:00:01", True),     # after the attempt: the user asked again
    ("2026-09-13 10:00:00", False),    # the same second is not after it
    ("2026-09-13 09:59:59", False),    # before it: the hint that attempt used
])
def test_pending_hint_written_after_compares_with_the_attempt(cur, written, expected):
    _written_at(cur, create_hint(cur, _hint(staged_path=_REID)), written)
    assert pending_hint_written_after(cur, _REID, _ATTEMPT) is expected


def test_pending_hint_written_after_binds_by_filename_under_another_spelling(cur):
    # The apply route and the worker can spell the staging folder differently
    # (a Docker-mapped path); the filename carries the track id.
    _written_at(cur, create_hint(cur, _hint(staged_path="/app/Staging/Song [reid-42].flac")),
                "2026-09-13 11:00:00")
    assert pending_hint_written_after(cur, r"C:\Staging\Song [reid-42].flac", _ATTEMPT) is True


def test_pending_hint_written_after_ignores_consumed_hints_and_other_files(cur):
    consumed = create_hint(cur, _hint(staged_path=_REID))
    consume_hint(cur, consumed)
    other = create_hint(cur, _hint(staged_path="/staging/Other [reid-43].flac"))
    for hint_id in (consumed, other):
        _written_at(cur, hint_id, "2026-09-13 11:00:00")
    assert pending_hint_written_after(cur, _REID, _ATTEMPT) is False


@pytest.mark.parametrize("attempt", [None, "", "not a time"])
def test_pending_hint_written_after_without_a_usable_time_is_false(cur, attempt):
    _written_at(cur, create_hint(cur, _hint(staged_path=_REID)), "2026-09-13 11:00:00")
    assert pending_hint_written_after(cur, _REID, attempt) is False


def test_pending_hint_written_after_reads_either_spelling_of_a_time(cur):
    # ' ' sorts before 'T', so comparing the strings would put 10:00:01 first.
    _written_at(cur, create_hint(cur, _hint(staged_path=_REID)), "2026-09-13 10:00:01")
    assert pending_hint_written_after(cur, _REID, "2026-09-13T10:00:00") is True
    assert pending_hint_written_after(cur, _REID, "2026-09-13T10:00:02") is False


# ── content fingerprint ───────────────────────────────────────────────────────
def test_quick_file_signature_stable_and_distinct(tmp_path):
    a = tmp_path / "a.bin"
    b = tmp_path / "b.bin"
    a.write_bytes(b"hello world" * 1000)
    b.write_bytes(b"goodbye moon" * 1000)
    sig_a1 = quick_file_signature(str(a))
    sig_a2 = quick_file_signature(str(a))
    sig_b = quick_file_signature(str(b))
    assert sig_a1 and sig_a1 == sig_a2          # stable
    assert sig_a1 != sig_b                       # distinct content → distinct sig


def test_quick_file_signature_missing_file_is_none():
    assert quick_file_signature("/no/such/file.flac") is None
