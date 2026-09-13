"""The Import page matches an album against THAT album's files.

Reported: selecting an album "often seems to get confused, auto matching things
that are clearly outside of that album's folder in the downloads folder".

Three causes compounded, and each is pinned here:

1. SCOPE. With no explicit file list the matcher was handed every audio file
   under the Import folder, and the match route never learned which folder the
   page was showing -- it always read the configured Import folder.
2. NO FOLDER AFFINITY. Each track took its best-scoring file from anywhere. The
   position dedup made it worse: it keeps ONE file per (disc, track) across the
   whole pool, so an album's own "01 Intro.mp3" could be discarded in favour of
   another folder's "01 Intro.flac" before scoring even ran.
3. INERT SAFETY NETS. The page never gave the matcher file durations or IDs, so
   the +/-10 second gate passed everything and the MBID/ISRC phase never ran.

Fixing (3) exposed a latent unit bug: ``_track_duration_ms`` multiplied Discogs
and MusicBrainz ``duration_ms`` by 1000 although both already arrive in ms. Only a
track tagged with its source takes that branch, and ``_normalize_match_track``
tags every track this page matches -- so once file durations arrived, every file
would have been rejected against a Discogs or MusicBrainz release. It was never
live on auto-import: the worker matches raw ``get_album`` tracks, which carry no
source tag and take the magnitude heuristic instead.
"""

from __future__ import annotations

import os
from unittest.mock import patch
from urllib.parse import quote

import pytest

import core.imports.album as import_album
from core.imports.album_matching import (
    _track_duration_ms,
    album_folder_of,
    default_quality_rank,
    match_files_to_tracks,
    match_files_to_tracks_by_folder,
)


def _tags(title, track, *, album="Album", disc=1, duration_ms=0, artist="Artist"):
    return {"title": title, "artist": artist, "album": album,
            "track_number": track, "disc_number": disc, "duration_ms": duration_ms}


def _track(name, number, *, disc=1, duration_ms=0, source="spotify"):
    return {"name": name, "track_number": number, "disc_number": disc,
            "duration_ms": duration_ms, "source": source,
            "artists": [{"name": "Artist"}]}


# ── the unit bug the duration gate would have tripped on ─────────────────────
def test_a_discogs_duration_ms_is_already_milliseconds():
    """The Discogs client converts "3:25" to 205000 before returning."""
    assert _track_duration_ms({"duration_ms": 205_000, "_source": "discogs"}) == 205_000


def test_a_musicbrainz_duration_ms_is_already_milliseconds():
    """musicbrainz_search sets duration_ms from ``length``, which is ms."""
    assert _track_duration_ms({"duration_ms": 205_000, "source": "musicbrainz"}) == 205_000


def test_a_bare_seconds_duration_from_a_seconds_source_is_still_scaled():
    assert _track_duration_ms({"duration": 205, "source": "discogs"}) == 205_000


def test_a_discogs_track_matches_a_file_of_the_same_length():
    """End to end: before the fix the track "lasted" 57 hours and this file was
    rejected by the duration gate."""
    f = os.path.join("dl", "Album", "01 Song.flac")
    result = match_files_to_tracks(
        [f], {f: _tags("Song", 1, duration_ms=205_000)},
        [_track("Song", 1, duration_ms=205_000, source="discogs")],
        target_album="Album", quality_rank=default_quality_rank)
    assert len(result["matches"]) == 1


# ── what counts as one album's folder ────────────────────────────────────────
def test_a_disc_subfolder_belongs_to_the_album_above_it():
    assert album_folder_of(os.path.join("dl", "Album", "CD2", "01 B.flac")) == \
        os.path.join("dl", "Album")
    assert album_folder_of(os.path.join("dl", "Album", "Disc 1", "01 A.flac")) == \
        os.path.join("dl", "Album")


def test_an_ordinary_folder_is_its_own_album():
    assert album_folder_of(os.path.join("dl", "Album", "01 A.flac")) == \
        os.path.join("dl", "Album")


def test_the_auto_import_worker_uses_the_same_disc_folder_rule():
    """One rule, not two copies that drift apart.

    Checked in the SOURCE, not by object identity. ``re.compile`` caches
    compiled patterns, so two modules compiling the same string get the very
    same object -- an ``is`` assertion passes even when each module keeps its
    own copy. That is what this test first asserted, and backing the shared
    import out did not fail it."""
    import ast
    from pathlib import Path

    worker_py = Path(__file__).resolve().parents[2] / "core" / "auto_import_worker.py"
    tree = ast.parse(worker_py.read_text(encoding="utf-8"))
    imported = any(
        isinstance(node, ast.ImportFrom)
        and node.module == "core.imports.album_matching"
        and any(alias.name == "DISC_FOLDER_RE" for alias in node.names)
        for node in tree.body
    )
    redefined = any(
        isinstance(node, ast.Assign)
        and any(isinstance(target, ast.Name) and target.id == "DISC_FOLDER_RE"
                for target in node.targets)
        for node in ast.walk(tree)
    )
    assert imported, "the worker must import DISC_FOLDER_RE from core.imports.album_matching"
    assert not redefined, "the worker defines its own DISC_FOLDER_RE again"


# ── folder affinity ──────────────────────────────────────────────────────────
def _stray_intro_scenario():
    """The album's own Intro is an MP3; another folder holds a FLAC "Intro" at
    the same position. Sorted so the stray's folder comes FIRST."""
    album_intro = os.path.join("dl", "Album", "01 Intro.mp3")
    album_song = os.path.join("dl", "Album", "02 Song.mp3")
    stray_intro = os.path.join("dl", "Aaa Other", "01 Intro.flac")
    files = [album_intro, album_song, stray_intro]
    tags = {
        album_intro: _tags("Intro", 1, duration_ms=60_000),
        album_song: _tags("Song", 2, duration_ms=200_000),
        stray_intro: _tags("Intro", 1, album="Other", duration_ms=60_000),
    }
    tracks = [_track("Intro", 1, duration_ms=60_000), _track("Song", 2, duration_ms=200_000)]
    return files, tags, tracks, album_intro, stray_intro


def test_unscoped_matching_lets_another_folder_take_the_slot():
    """The mechanism, shown directly -- otherwise the next test proves nothing."""
    files, tags, tracks, _album_intro, stray_intro = _stray_intro_scenario()
    result = match_files_to_tracks(files, tags, tracks, target_album="Album",
                                   quality_rank=default_quality_rank)
    intro = next(m for m in result["matches"] if m["track"]["name"] == "Intro")
    assert intro["file"] == stray_intro


def test_a_file_in_another_folder_cannot_take_a_slot():
    files, tags, tracks, album_intro, stray_intro = _stray_intro_scenario()
    result = match_files_to_tracks_by_folder(files, tags, tracks, target_album="Album",
                                             quality_rank=default_quality_rank)
    intro = next(m for m in result["matches"] if m["track"]["name"] == "Intro")
    assert intro["file"] == album_intro
    assert result["folder"] == os.path.join("dl", "Album")
    assert result["folders_considered"] == 2
    assert stray_intro not in result["candidate_files"]


def test_a_two_disc_album_is_matched_as_one_folder():
    """Without the disc rule, CD1 and CD2 are two folders and only one disc
    could ever be matched."""
    a = os.path.join("dl", "Album", "CD1", "01 A.flac")
    b = os.path.join("dl", "Album", "CD2", "01 B.flac")
    tags = {a: _tags("A", 1, disc=1), b: _tags("B", 1, disc=2)}
    tracks = [_track("A", 1, disc=1), _track("B", 1, disc=2)]
    result = match_files_to_tracks_by_folder([a, b], tags, tracks, target_album="Album",
                                             quality_rank=default_quality_rank)
    assert len(result["matches"]) == 2
    assert result["folder"] == os.path.join("dl", "Album")


def test_when_no_folder_matches_every_file_stays_available():
    """Never worse than before: nothing fits anywhere, so nothing is hidden."""
    x = os.path.join("dl", "One", "01 X.flac")
    y = os.path.join("dl", "Two", "01 Y.flac")
    tags = {x: _tags("X", 1, duration_ms=30_000), y: _tags("Y", 1, duration_ms=30_000)}
    tracks = [_track("Nothing Alike", 7, duration_ms=400_000)]
    result = match_files_to_tracks_by_folder([x, y], tags, tracks, target_album="Album",
                                             quality_rank=default_quality_rank)
    assert result["matches"] == []
    assert result["folder"] is None
    assert sorted(result["candidate_files"]) == sorted([x, y])


# ── the payload the page receives ────────────────────────────────────────────
def _staging_entry(path, title, track, *, album="Album", duration_ms=0):
    return {"filename": os.path.basename(path), "full_path": path, "title": title,
            "artist": "Artist", "albumartist": "Artist", "album": album,
            "track_number": track, "disc_number": 1, "duration_ms": duration_ms,
            "isrc": "", "mbid": ""}


def _release(tracks, *, release_source="spotify"):
    # The builder tags tracks with the RESPONSE's source, not the request's, so a
    # test about one source has to set it here or it quietly runs as spotify.
    return lambda album_id, artist_name="", album_name="", source=None: {
        "success": True,
        "album": {"id": album_id, "name": "Album", "artist": "Artist",
                  "artists": [{"name": "Artist"}], "source": release_source},
        "tracks": tracks, "source": release_source, "source_priority": [release_source],
        "resolved_album_id": album_id,
    }


@pytest.fixture
def root(tmp_path, monkeypatch):
    monkeypatch.setattr(import_album, "get_staging_path", lambda: str(tmp_path))
    return str(tmp_path)


def test_an_auto_detected_album_is_matched_against_exactly_its_files(root, monkeypatch):
    seen = []
    a = os.path.join(root, "Album", "01 Song.flac")

    def fake_collect(file_paths=None, staging_path=None):
        seen.append((set(file_paths or ()), staging_path))
        return [_staging_entry(a, "Song", 1)]

    monkeypatch.setattr(import_album, "collect_staging_files", fake_collect)
    monkeypatch.setattr(import_album, "get_artist_album_tracks", _release([_track("Song", 1)]))

    payload = import_album.build_album_import_match_payload(
        "alb", album_name="Album", album_artist="Artist", file_paths={a},
        source="spotify", staging_path=root)

    assert payload["match_scope"]["mode"] == "files"
    assert seen == [({a}, root)], "the scan did not receive the folder on screen"
    assert payload["candidate_paths"] == [a]


def test_without_a_file_list_the_match_is_confined_to_one_folder(root, monkeypatch):
    album_intro = os.path.join(root, "Album", "01 Intro.mp3")
    album_song = os.path.join(root, "Album", "02 Song.mp3")
    stray = os.path.join(root, "Aaa Other", "01 Intro.flac")
    monkeypatch.setattr(import_album, "collect_staging_files", lambda file_paths=None, staging_path=None: [
        _staging_entry(album_intro, "Intro", 1, duration_ms=60_000),
        _staging_entry(album_song, "Song", 2, duration_ms=200_000),
        _staging_entry(stray, "Intro", 1, album="Other", duration_ms=60_000),
    ])
    monkeypatch.setattr(import_album, "get_artist_album_tracks", _release(
        [_track("Intro", 1, duration_ms=60_000), _track("Song", 2, duration_ms=200_000)]))

    payload = import_album.build_album_import_match_payload(
        "alb", album_name="Album", album_artist="Artist", source="spotify", staging_path=root)

    scope = payload["match_scope"]
    assert scope["mode"] == "folder"
    assert scope["folder_label"] == "Album"
    assert scope["folders_considered"] == 2
    assert sorted(payload["candidate_paths"]) == sorted([album_intro, album_song])
    intro = next(m for m in payload["matches"] if m["track"]["name"] == "Intro")
    assert intro["staging_file"]["full_path"] == album_intro
    assert all(f["full_path"] != stray for f in payload["unmatched_files"])


def test_the_duration_gate_now_applies_on_the_import_page(root, monkeypatch):
    """Same title, same position, a third of the length. Before durations
    reached the matcher this paired at high confidence."""
    short = os.path.join(root, "Album", "01 Song.flac")
    monkeypatch.setattr(import_album, "collect_staging_files", lambda file_paths=None, staging_path=None: [
        _staging_entry(short, "Song", 1, duration_ms=60_000)])
    monkeypatch.setattr(import_album, "get_artist_album_tracks", _release(
        [_track("Song", 1, duration_ms=200_000)]))

    payload = import_album.build_album_import_match_payload(
        "alb", album_name="Album", album_artist="Artist", file_paths={short},
        source="spotify", staging_path=root)

    assert payload["matches"][0]["staging_file"] is None


@pytest.mark.parametrize("source", ["discogs", "musicbrainz"])
def test_a_discogs_or_musicbrainz_release_still_matches_on_the_import_page(root, monkeypatch, source):
    """What the unit fix protects. The page tags every track with its source,
    and these two took the seconds branch: with file durations now reaching the
    matcher, the right file at the right length read as ~57 hours out."""
    song = os.path.join(root, "Album", "01 Song.flac")
    monkeypatch.setattr(import_album, "collect_staging_files", lambda file_paths=None, staging_path=None: [
        _staging_entry(song, "Song", 1, duration_ms=205_000)])
    monkeypatch.setattr(import_album, "get_artist_album_tracks", _release(
        [_track("Song", 1, duration_ms=205_000, source=source)], release_source=source))

    payload = import_album.build_album_import_match_payload(
        "alb", album_name="Album", album_artist="Artist", file_paths={song},
        source=source, staging_path=root)

    assert payload["source"] == source, "the release must really be %s" % source
    matched = payload["matches"][0]["staging_file"]
    assert matched is not None and matched["full_path"] == song


# ── the file scan and the tags it reads ──────────────────────────────────────
def test_collect_staging_files_walks_the_folder_it_is_given(tmp_path, monkeypatch):
    import core.imports.staging as staging
    configured = tmp_path / "Configured"
    browsed = tmp_path / "Browsed" / "Album"
    configured.mkdir()
    browsed.mkdir(parents=True)
    (configured / "elsewhere.flac").write_bytes(b"")
    (browsed / "01 Track.flac").write_bytes(b"")
    monkeypatch.setattr(staging, "get_staging_path", lambda: str(configured))

    given = staging.collect_staging_files(staging_path=str(tmp_path / "Browsed"))
    assert [f["filename"] for f in given] == ["01 Track.flac"]
    # Omitted, it is still the configured Import folder, as before.
    assert [f["filename"] for f in staging.collect_staging_files()] == ["elsewhere.flac"]


def test_staging_metadata_carries_duration_and_identifiers(monkeypatch):
    from core.imports.staging import read_staging_file_metadata

    class _Info:
        length = 205.4

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


# ── the route reads the folder on screen ─────────────────────────────────────
@pytest.fixture
def web_client():
    with patch("web_server.add_activity_item"):
        with patch("web_server.SpotifyClient"):
            with patch("core.tidal_client.TidalClient"):
                import web_server
                web_server.app.config["TESTING"] = True
                yield web_server, web_server.app.test_client()


def test_the_match_route_reads_the_folder_on_screen(web_client, tmp_path, monkeypatch):
    web_server, client = web_client
    browsed = str(tmp_path)
    monkeypatch.setattr(web_server, "_resolve_import_scan_path",
                        lambda raw: (browsed, None) if raw else ("", None))
    seen = {}

    def fake_builder(album_id, **kwargs):
        seen.update(kwargs)
        return {"success": True, "matches": []}

    monkeypatch.setattr(web_server, "build_album_import_match_payload", fake_builder)
    r = client.post("/api/import/album/match?path=" + quote(browsed),
                    json={"album_id": "a1", "source": "spotify"})
    assert r.status_code == 200
    assert seen.get("staging_path") == browsed


def test_the_match_route_refuses_a_folder_outside_the_allowlist(web_client, tmp_path, monkeypatch):
    web_server, client = web_client
    called = []
    monkeypatch.setattr(web_server, "build_album_import_match_payload",
                        lambda *a, **k: called.append(1) or {"success": True})
    r = client.post("/api/import/album/match?path=" + quote(str(tmp_path / "not-allowed")),
                    json={"album_id": "a1", "source": "spotify"})
    assert r.status_code in (403, 404)
    assert not called
