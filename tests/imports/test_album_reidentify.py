"""Re-identifying a whole library album under a different release.

Asked for as "if all I wanted to do was rematch a downloaded album to update it".
An album goes through the Import page's album path rather than the per-track
Re-identify repeated per track: the user confirms every pairing before anything
moves, each track reports its own result, and nothing waits on the auto-import
worker. One confirmed track at a time, with the original removed only after its
copy has landed.

These run against the REAL MusicDatabase schema, so a wrong column name fails
here rather than on a user's library.
"""

from __future__ import annotations

import os

import pytest

import core.imports.album as album_mod
import core.imports.album_reidentify as reid
import core.imports.pipeline as pipeline
from database.music_database import MusicDatabase

ALBUM_ID = 10


@pytest.fixture
def db(tmp_path):
    return MusicDatabase(str(tmp_path / "m.db"))


def _seed(db, tracks):
    conn = db._get_connection()
    try:
        cur = conn.cursor()
        cur.execute("INSERT INTO artists (id, name) VALUES (1, 'Artist')")
        cur.execute("INSERT INTO albums (id, artist_id, title) VALUES (?, 1, 'Old Album')", (ALBUM_ID,))
        for t in tracks:
            cur.execute(
                "INSERT INTO tracks (id, album_id, artist_id, title, track_number, disc_number,"
                " duration, file_path, isrc, musicbrainz_recording_id)"
                " VALUES (?, ?, 1, ?, ?, ?, ?, ?, ?, ?)",
                (t["id"], ALBUM_ID, t["title"], t["track_number"], t.get("disc_number", 1),
                 t["duration"], t.get("file_path", ""), t.get("isrc"), t.get("mbid")))
        conn.commit()
    finally:
        conn.close()


def _row_count(db, track_id):
    conn = db._get_connection()
    try:
        return conn.execute("SELECT COUNT(*) FROM tracks WHERE id = ?", (track_id,)).fetchone()[0]
    finally:
        conn.close()


def _release_track(name, number, duration_ms, *, disc=1, track_id=None):
    t = {"name": name, "track_number": number, "disc_number": disc,
         "duration_ms": duration_ms, "artists": [{"name": "Artist"}]}
    if track_id is not None:
        t["id"] = track_id
    return t


def _release(tracks, *, source="spotify"):
    return {
        "success": True,
        "album": {"id": "rel-1", "name": "New Album", "artist": "Artist",
                  "artists": [{"name": "Artist"}], "source": source},
        "tracks": [album_mod._normalize_match_track(t, source, {"name": "New Album"}) for t in tracks],
        "source": source,
    }


# ── reading the library album ────────────────────────────────────────────────
def test_the_library_album_carries_what_the_matcher_needs(db):
    _seed(db, [
        {"id": 2, "title": "Song", "track_number": 2, "duration": 200_000, "isrc": "usabc1234567"},
        {"id": 1, "title": "Intro", "track_number": 1, "duration": 60_000, "mbid": "ABCD-1"},
    ])
    album = reid.load_library_album(db, ALBUM_ID)

    assert album["title"] == "Old Album" and album["artist_name"] == "Artist"
    # Ids are compared as stored: the schema keeps them as strings.
    assert [str(t["id"]) for t in album["tracks"]] == ["1", "2"], "tracks come back in album order"
    intro, song = album["tracks"]
    assert intro["duration_ms"] == 60_000                  # stored in ms, read as ms
    assert intro["mbid"] == "abcd-1" and song["isrc"] == "USABC1234567"
    assert song["disc_number"] == 1


def test_an_unknown_album_is_none(db):
    assert reid.load_library_album(db, 999) is None
    assert reid.load_library_album(db, "not-a-number") is None
    assert reid.load_library_album(db, None) is None
    assert reid.load_library_album(db, "  ") is None


# ── the release ──────────────────────────────────────────────────────────────
def test_a_release_track_is_addressable_without_a_provider_id():
    """Discogs returns tracks with no id; a confirmed pairing still needs a key."""
    assert reid.release_track_key({"track_number": 3}) == "1-3"
    assert reid.release_track_key({"track_number": 3, "disc_number": 2}) == "2-3"


def test_a_release_lookup_is_cached_but_a_failure_is_retried(monkeypatch):
    reid.clear_release_cache()
    calls = []
    answers = iter([
        {"success": False, "error": "timeout"},
        {"success": True, "album": {"id": "r", "name": "New Album"},
         "tracks": [_release_track("Song", 1, 200_000)], "source": "spotify"},
    ])

    def fake(album_id, artist_name="", album_name="", source=None):
        calls.append(album_id)
        return next(answers)

    monkeypatch.setattr(album_mod, "get_artist_album_tracks", fake)

    assert reid.fetch_release("r", source="spotify", now=0)["success"] is False
    assert reid.fetch_release("r", source="spotify", now=1)["success"] is True
    assert reid.fetch_release("r", source="spotify", now=2)["success"] is True
    assert len(calls) == 2, "the success should have been served from the cache"
    reid.clear_release_cache()


# ── the preview ──────────────────────────────────────────────────────────────
def test_the_preview_pairs_by_song_not_by_position(db):
    """The new release lists these two in the opposite order. Position alone
    would swap them; title and the duration gate keep them straight."""
    _seed(db, [
        {"id": 1, "title": "Intro", "track_number": 1, "duration": 60_000},
        {"id": 2, "title": "Song", "track_number": 2, "duration": 200_000},
    ])
    preview = reid.build_preview(
        reid.load_library_album(db, ALBUM_ID),
        _release([_release_track("Song", 1, 200_000), _release_track("Intro", 2, 60_000)]))

    paired = {p["release_track"]["name"]: str(p["library_track"]["id"]) for p in preview["pairs"]}
    assert paired == {"Song": "2", "Intro": "1"}


def test_the_preview_names_library_tracks_the_release_does_not_have(db):
    _seed(db, [
        {"id": 1, "title": "Song", "track_number": 1, "duration": 200_000},
        {"id": 2, "title": "Bonus Demo", "track_number": 2, "duration": 150_000},
    ])
    preview = reid.build_preview(
        reid.load_library_album(db, ALBUM_ID), _release([_release_track("Song", 1, 200_000)]))

    assert [str(t["id"]) for t in preview["unmatched_library_tracks"]] == ["2"]


@pytest.mark.parametrize("source", ["discogs", "musicbrainz"])
def test_the_preview_pairs_a_discogs_or_musicbrainz_release(db, source):
    """A release normalized for the preview is tagged with its source, and for
    these two that sent its durations through the seconds branch: every library
    track read as ~57 hours away from its release track, and nothing paired."""
    _seed(db, [{"id": 1, "title": "Song", "track_number": 1, "duration": 205_000}])
    release = _release([_release_track("Song", 1, 205_000)], source=source)
    assert release["tracks"][0]["source"] == source, "the release must really be %s" % source

    preview = reid.build_preview(reid.load_library_album(db, ALBUM_ID), release)

    paired = preview["pairs"][0]["library_track"]
    assert paired is not None and str(paired["id"]) == "1"


# ── validating a confirmed pairing ───────────────────────────────────────────
def test_a_pairing_must_name_this_albums_track_and_this_releases_position(db):
    _seed(db, [{"id": 1, "title": "Song", "track_number": 1, "duration": 200_000}])
    album = reid.load_library_album(db, ALBUM_ID)
    release = _release([_release_track("Song", 1, 200_000)])

    with pytest.raises(reid.PairError):
        reid.resolve_pair(album, release, 12345, "1-1")        # another album's track
    with pytest.raises(reid.PairError):
        reid.resolve_pair(album, release, 1, "1-9")            # not on the release
    doubled = _release([_release_track("A", 1, 1000), _release_track("B", 1, 1000)])
    with pytest.raises(reid.PairError):
        reid.resolve_pair(album, doubled, 1, "1-1")            # ambiguous position


# ── applying one track ───────────────────────────────────────────────────────
@pytest.fixture
def library_file(tmp_path, db):
    old = tmp_path / "Library" / "Old Album" / "01 Song.flac"
    old.parent.mkdir(parents=True)
    old.write_bytes(b"audio")
    _seed(db, [{"id": 1, "title": "Song", "track_number": 1, "duration": 200_000,
                "file_path": str(old)}])
    return old


@pytest.fixture
def quiet_pipeline(monkeypatch):
    monkeypatch.setattr(album_mod, "resolve_album_artist_context",
                        lambda album, source="": {"id": "a1", "name": "Artist", "genres": []})
    monkeypatch.setattr(pipeline, "import_rejection_reason", lambda context: None)


def _apply(db, tmp_path, post_process, **overrides):
    kwargs = dict(
        database=db,
        library_album=reid.load_library_album(db, ALBUM_ID),
        release=_release([_release_track("Song", 1, 200_000)]),
        library_track_id=1,
        release_key="1-1",
        replace=True,
        resolve_file=lambda p: p,
        post_process=post_process,
        is_media_server_ready=lambda: (True, ""),
    )
    kwargs.update(overrides)
    return reid.apply_track(**kwargs)


def _mover(tmp_path, seen):
    def post_process(key, context, staged_path):
        seen.update(key=key, context=context, staged=staged_path)
        new = tmp_path / "Library" / "New Album" / "01 Song.flac"
        new.parent.mkdir(parents=True, exist_ok=True)
        os.replace(staged_path, new)
        context["_final_processed_path"] = str(new)
    return post_process


def test_a_copy_is_imported_and_the_original_goes_only_after_it_lands(
        db, tmp_path, library_file, quiet_pipeline):
    seen = {}
    payload, status = _apply(db, tmp_path, _mover(tmp_path, seen))

    assert status == 200 and payload["success"] is True
    assert seen["staged"] != str(library_file), "the pipeline must be handed a COPY"
    assert not os.path.exists(os.path.dirname(seen["staged"])), "the temp copy dir is cleaned up"
    ctx = seen["context"]
    assert ctx["_user_manual_pick"] is True, "the chosen file must outrank the library copy"
    assert ctx["is_local_import"] is True
    assert ctx["_skip_quarantine_check"] == ["quality", "bit_depth"]
    assert not library_file.exists() and _row_count(db, 1) == 0
    assert payload["removed_original"] == str(library_file)


def test_a_rejected_import_keeps_the_original(db, tmp_path, library_file, quiet_pipeline, monkeypatch):
    monkeypatch.setattr(pipeline, "import_rejection_reason", lambda context: "AcoustID mismatch")
    payload, status = _apply(db, tmp_path, _mover(tmp_path, {}))

    assert status == 422 and payload["original_kept"] is True
    assert library_file.exists() and _row_count(db, 1) == 1


def test_an_import_that_cannot_say_where_it_landed_keeps_the_original(
        db, tmp_path, library_file, quiet_pipeline):
    """Without the landing path the same-home guard cannot run, and deleting
    blind could remove the very file the import just wrote."""
    payload, status = _apply(db, tmp_path, lambda key, context, staged: None)

    assert status == 200 and payload["original_kept"] is True
    assert library_file.exists() and _row_count(db, 1) == 1


def test_a_track_re_filed_to_its_own_location_is_not_deleted(db, tmp_path, library_file, quiet_pipeline):
    def lands_in_place(key, context, staged):
        os.replace(staged, library_file)
        context["_final_processed_path"] = str(library_file)

    payload, status = _apply(db, tmp_path, lands_in_place)

    assert status == 200 and payload["removed_original"] is None
    assert library_file.exists() and _row_count(db, 1) == 1


def test_nothing_is_copied_when_the_media_server_is_down(db, tmp_path, library_file, quiet_pipeline):
    copies = []
    payload, status = _apply(db, tmp_path, _mover(tmp_path, {}),
                             is_media_server_ready=lambda: (False, "Plex is not connected"),
                             copy_fn=lambda a, b: copies.append((a, b)))

    assert status == 503 and payload["error_code"] == "media_server_not_connected"
    assert copies == []


def test_an_invalid_pairing_is_refused_before_anything_moves(db, tmp_path, library_file, quiet_pipeline):
    copies = []
    payload, status = _apply(db, tmp_path, _mover(tmp_path, {}), release_key="1-9",
                             copy_fn=lambda a, b: copies.append((a, b)))

    assert status == 400 and copies == []
    assert library_file.exists()


def test_a_missing_file_is_reported_and_nothing_is_imported(db, tmp_path, library_file, quiet_pipeline):
    imported = []
    payload, status = _apply(db, tmp_path, lambda *a: imported.append(a),
                             resolve_file=lambda p: None)

    assert status == 404 and imported == []


def test_a_library_with_string_ids_is_not_treated_as_missing(db):
    """Jellyfin and Navidrome ids are not numbers. The loader once coerced the
    album id to int, which would have made every such album read as "not found"
    -- and the library button was gated on the id looking numeric."""
    conn = db._get_connection()
    try:
        cur = conn.cursor()
        cur.execute("INSERT INTO artists (id, name) VALUES ('art-guid', 'Artist')")
        cur.execute("INSERT INTO albums (id, artist_id, title) VALUES ('4f1c9a2e-album', 'art-guid', 'Guid Album')")
        cur.execute(
            "INSERT INTO tracks (id, album_id, artist_id, title, track_number, disc_number, duration, file_path)"
            " VALUES ('9b7e-track', '4f1c9a2e-album', 'art-guid', 'Song', 1, 1, 200000, '')")
        conn.commit()
    finally:
        conn.close()

    album = reid.load_library_album(db, "4f1c9a2e-album")
    assert album is not None and album["title"] == "Guid Album"
    assert album["artist_name"] == "Artist"
    assert [t["id"] for t in album["tracks"]] == ["9b7e-track"]
