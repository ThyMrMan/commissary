"""Album Consistency keeps a deluxe album's identity under "Prefer deluxe editions".

After an album download, Album Consistency picks one MusicBrainz release and writes
its title into every file that matches it. MusicBrainz often titles a deluxe release
without "(Deluxe Edition)", and the release it reused was keyed by the album name
with edition words stripped — shared with the standard edition. Either way, deluxe
files could be filed under the standard album by the media server. With the option
on, a deluxe keeps its own pinned release and its edition name.
"""

from __future__ import annotations

import struct
from pathlib import Path
from types import SimpleNamespace

import pytest
from mutagen.flac import FLAC

import core.album_consistency as ac

_SONGS = ["Cold Wind Blows", "Talkin' 2 Myself", "On Fire"]
_MB = SimpleNamespace(mb_client=SimpleNamespace(get_release=lambda *a, **k: None))


def _make_flac(path: Path, tags: dict) -> None:
    """A real, minimal FLAC, so the true mutagen write path runs."""
    streaminfo = bytearray(34)
    streaminfo[0:2] = struct.pack('>H', 4096)
    streaminfo[2:4] = struct.pack('>H', 4096)
    streaminfo[10] = 0x0A
    streaminfo[12] = 0x70
    path.write_bytes(b'fLaC' + bytes([0x80, 0x00, 0x00, 0x22]) + bytes(streaminfo))
    audio = FLAC(str(path))
    for key, value in tags.items():
        audio[key] = [value]
    audio.save()


def _release(title):
    return {"id": "REL-1", "title": title, "media": [{"position": 1, "tracks": [
        {"position": number, "id": "TRK-%d" % number, "recording": {"title": song}}
        for number, song in enumerate(_SONGS, start=1)]}]}


@pytest.fixture()
def pins(monkeypatch):
    """Dict-backed stand-in for the persistent album -> release pin store."""
    store = {}
    monkeypatch.setattr("core.metadata.album_mbid_cache.lookup", lambda a, ar: store.get((a, ar)))

    def _record(a, ar, mbid):
        store[(a, ar)] = mbid
        return True

    monkeypatch.setattr("core.metadata.album_mbid_cache.record", _record)
    return store


def _deluxe_files(tmp_path):
    folder = tmp_path / "Eminem" / "Recovery (Deluxe Edition)"
    folder.mkdir(parents=True)
    infos = []
    for number, song in enumerate(_SONGS, start=1):
        path = folder / ("%02d.flac" % number)
        _make_flac(path, {"TITLE": song, "ALBUM": "Recovery (Deluxe Edition)"})
        infos.append({"path": str(path), "track_number": number, "disc_number": 1, "title": song})
    return infos


def test_a_deluxe_keeps_its_edition_name_in_the_album_tag(tmp_path, monkeypatch, pins):
    monkeypatch.setattr(ac, "_find_best_release", lambda *a, **k: _release("Recovery"))
    infos = _deluxe_files(tmp_path)

    result = ac.run_album_consistency(infos, "Recovery (Deluxe Edition)", "Eminem",
                                      mb_service=_MB, prefer_deluxe=True)

    assert result["success"] and result["tags_written"] == 3
    assert [FLAC(info["path"])["ALBUM"] for info in infos] == [["Recovery (Deluxe Edition)"]] * 3
    assert FLAC(infos[0]["path"])["MUSICBRAINZ_RELEASE_ID"] == ["REL-1"]   # the release still applies


def test_without_the_option_the_musicbrainz_title_is_written(tmp_path, monkeypatch, pins):
    """Today's behaviour, pinned: the deluxe files are re-titled to the standard album."""
    monkeypatch.setattr(ac, "_find_best_release", lambda *a, **k: _release("Recovery"))
    infos = _deluxe_files(tmp_path)

    ac.run_album_consistency(infos, "Recovery (Deluxe Edition)", "Eminem",
                             mb_service=_MB, prefer_deluxe=False)

    assert [FLAC(info["path"])["ALBUM"] for info in infos] == [["Recovery"]] * 3


def test_a_different_album_from_musicbrainz_is_still_written(tmp_path, monkeypatch, pins):
    """The option keeps an EDITION's name — not any name that disagrees with MusicBrainz."""
    monkeypatch.setattr(ac, "_find_best_release", lambda *a, **k: _release("Recovery Live"))
    infos = _deluxe_files(tmp_path)

    ac.run_album_consistency(infos, "Recovery (Deluxe Edition)", "Eminem",
                             mb_service=_MB, prefer_deluxe=True)

    assert FLAC(infos[0]["path"])["ALBUM"] == ["Recovery Live"]


def test_a_deluxe_gets_its_own_pinned_release(monkeypatch, pins):
    picks = iter([{"id": "REL-STANDARD"}, {"id": "REL-DELUXE"}])
    monkeypatch.setattr(ac, "_find_best_release", lambda *a, **k: next(picks))

    standard = ac._resolve_album_release("Nevermind", "Nirvana", 12, _MB)
    deluxe = ac._resolve_album_release("Nevermind (Deluxe Edition)", "Nirvana", 20, _MB,
                                       keep_edition=True)

    assert standard["id"] == "REL-STANDARD" and deluxe["id"] == "REL-DELUXE"
    assert pins[("nevermind", "nirvana")] == "REL-STANDARD"
    assert pins[("nevermind (deluxe edition)", "nirvana")] == "REL-DELUXE"


def test_the_setting_is_read_when_the_caller_does_not_pass_it(tmp_path, monkeypatch, pins):
    monkeypatch.setattr(ac, "prefer_deluxe_enabled", lambda: True)
    monkeypatch.setattr(ac, "_find_best_release", lambda *a, **k: _release("Recovery"))
    infos = _deluxe_files(tmp_path)

    ac.run_album_consistency(infos, "Recovery (Deluxe Edition)", "Eminem", mb_service=_MB)

    assert FLAC(infos[0]["path"])["ALBUM"] == ["Recovery (Deluxe Edition)"]
