"""Track lengths and exact ids reach the album matcher, in the right unit.

1. ``_track_duration_ms`` multiplied a Discogs or MusicBrainz ``duration_ms`` by
   1000, although both clients already return milliseconds -- a 3:25 track
   "lasted" about 57 hours, and the duration gate rejected every file. Live on
   upstream's enriched download from basic search, which tags each track with
   its provider and gives the matcher the files' real lengths.
2. The Import page's album match (``build_album_import_match_payload``) never
   gave the matcher a file's duration, ISRC or MusicBrainz id, so its duration
   gate (a 0 always passes) and exact-id phase were inert.

Ported from the legacy fork (Release 2.3.5).
"""

from __future__ import annotations

import os

import pytest

import core.imports.album as import_album
from core.imports.album_matching import _track_duration_ms, default_quality_rank, match_files_to_tracks
from core.search import enriched_download


def _tags(title, track, *, duration_ms=0):
    return {"title": title, "artist": "Artist", "album": "Album",
            "track_number": track, "disc_number": 1, "duration_ms": duration_ms}


def _track(name, number, *, duration_ms=0, source="spotify", **extra):
    return {"id": "t%d" % number, "name": name, "track_number": number, "disc_number": 1,
            "duration_ms": duration_ms, "source": source,
            "artists": [{"name": "Artist"}], **extra}


# ── the unit ─────────────────────────────────────────────────────────────────
@pytest.mark.parametrize("key", ["source", "_source", "provider"])
@pytest.mark.parametrize("source", ["discogs", "musicbrainz"])
def test_a_duration_ms_from_a_seconds_source_is_already_milliseconds(source, key):
    assert _track_duration_ms({"duration_ms": 205_000, key: source}) == 205_000


def test_a_bare_seconds_duration_from_a_seconds_source_is_still_scaled():
    assert _track_duration_ms({"duration": 205, "source": "discogs"}) == 205_000


@pytest.mark.parametrize("source", ["discogs", "musicbrainz"])
def test_a_tagged_track_matches_a_file_of_the_same_length(source):
    f = "/s/01 Song.flac"
    result = match_files_to_tracks(
        [f], {f: _tags("Song", 1, duration_ms=205_000)},
        [_track("Song", 1, duration_ms=205_000, source=source)],
        target_album="Album", quality_rank=default_quality_rank)
    assert [m["file"] for m in result["matches"]] == [f]


@pytest.mark.parametrize("source", ["discogs", "musicbrainz"])
def test_an_enriched_download_matches_a_discogs_or_musicbrainz_release(source):
    """Basic search -> enriched download: soulseek rows carry ``duration`` in ms
    and the release's tracks come tagged with their provider."""
    files = [{"username": "u", "filename": "Music\\Album\\01 - Song.flac",
              "title": "Song", "track_number": 1, "duration": 205_000}]
    out = enriched_download.match_files(
        files, [_track("Song", 1, duration_ms=205_000, source=source)], "Album")
    assert out[0]["track_index"] == 0, out


# ── the Import page's payload ────────────────────────────────────────────────
def _staging_entry(path, title, track, *, duration_ms=0, isrc="", mbid=""):
    return {"filename": os.path.basename(path), "full_path": path, "title": title,
            "artist": "Artist", "albumartist": "Artist", "album": "Album",
            "track_number": track, "disc_number": 1, "duration_ms": duration_ms,
            "isrc": isrc, "mbid": mbid}


def _release(tracks, *, release_source="spotify"):
    # The builder tags tracks with the RESPONSE's source, so a test about one
    # source has to set it here or it quietly runs as spotify.
    return lambda album_id, artist_name="", album_name="", source=None: {
        "success": True,
        "album": {"id": album_id, "name": "Album", "artist": "Artist",
                  "artists": [{"name": "Artist"}], "source": release_source},
        "tracks": tracks, "source": release_source, "source_priority": [release_source],
        "resolved_album_id": album_id,
    }


def _payload(monkeypatch, entries, tracks, *, source="spotify"):
    monkeypatch.setattr(import_album, "collect_staging_files",
                        lambda file_paths=None, root=None: entries)
    monkeypatch.setattr(import_album, "get_artist_album_tracks",
                        _release(tracks, release_source=source))
    return import_album.build_album_import_match_payload(
        "alb", album_name="Album", album_artist="Artist",
        file_paths={e["full_path"] for e in entries}, source=source)


def test_the_duration_gate_applies_on_the_import_page(monkeypatch):
    """Same title, same position, a third of the length. Before durations
    reached the matcher this paired at high confidence."""
    short = os.path.join("s", "Album", "01 Song.flac")
    payload = _payload(monkeypatch, [_staging_entry(short, "Song", 1, duration_ms=60_000)],
                       [_track("Song", 1, duration_ms=200_000)])
    assert payload["matches"][0]["staging_file"] is None


@pytest.mark.parametrize("source", ["discogs", "musicbrainz"])
def test_a_discogs_or_musicbrainz_release_still_matches_on_the_import_page(monkeypatch, source):
    song = os.path.join("s", "Album", "01 Song.flac")
    payload = _payload(monkeypatch, [_staging_entry(song, "Song", 1, duration_ms=205_000)],
                       [_track("Song", 1, duration_ms=205_000, source=source)], source=source)
    assert payload["source"] == source, "the release must really be %s" % source
    matched = payload["matches"][0]["staging_file"]
    assert matched is not None and matched["full_path"] == song


def test_the_matcher_receives_each_file_s_length_and_ids(monkeypatch):
    import core.imports.album_matching as album_matching

    seen = {}
    real = album_matching.match_files_to_tracks

    def spy(audio_files, file_tags, tracks, **kwargs):
        seen.update(file_tags)
        return real(audio_files, file_tags, tracks, **kwargs)

    monkeypatch.setattr(album_matching, "match_files_to_tracks", spy)
    song = os.path.join("s", "Album", "01 Song.flac")
    _payload(monkeypatch, [_staging_entry(song, "Song", 1, duration_ms=205_000,
                                          isrc="USABC1234567", mbid="abcd")],
             [_track("Song", 1, duration_ms=205_000)])
    tags = seen[song]
    assert (tags["duration_ms"], tags["isrc"], tags["mbid"]) == (205_000, "USABC1234567", "abcd")


# ── the file scan ────────────────────────────────────────────────────────────
def test_staging_metadata_carries_duration_and_identifiers(monkeypatch):
    from core.imports.staging import read_staging_file_metadata

    class _Info:
        length = 205.4
        bitrate = 0

    class _Audio:
        info = _Info()
        _tags = {"title": ["Song"], "isrc": ["usabc1234567"],
                 "musicbrainz_trackid": ["ABCD-1234"]}

        def get(self, key):
            return self._tags.get(key)

    monkeypatch.setattr("mutagen.File", lambda path, easy=True: _Audio())
    meta = read_staging_file_metadata("/x/01 Song.flac", "01 Song.flac")
    assert meta["duration_ms"] == 205_400
    assert meta["isrc"] == "USABC1234567"
    assert meta["mbid"] == "abcd-1234"


def test_an_unreadable_file_still_yields_empty_identifiers(monkeypatch):
    from core.imports.staging import read_staging_file_metadata

    def _boom(path, easy=True):
        raise OSError("not audio")

    monkeypatch.setattr("mutagen.File", _boom)
    meta = read_staging_file_metadata("/x/01 Song.flac", "01 Song.flac")
    assert (meta["duration_ms"], meta["isrc"], meta["mbid"]) == (0, "", "")


def test_collect_staging_files_passes_the_identifiers_on(tmp_path, monkeypatch):
    import core.imports.staging as staging

    (tmp_path / "01 Song.flac").write_bytes(b"")
    monkeypatch.setattr(staging, "read_staging_file_metadata", lambda path, name: {
        "title": "Song", "duration_ms": 1000, "isrc": "USABC1234567", "mbid": "abcd"})
    (entry,) = staging.collect_staging_files(root=str(tmp_path))
    assert (entry["isrc"], entry["mbid"]) == ("USABC1234567", "abcd")
