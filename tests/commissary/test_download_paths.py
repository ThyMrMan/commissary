"""Several completed-download folders where upstream stores one string.

A legacy Commissary config stores ``download_source.torrent_download_path`` as
a LIST (one folder per client category). Upstream did ``str(value)`` on it and
searched a nonexistent ``"['/a', '/b']"`` folder, so finished downloads in any
category folder were never found by the basename fallback.
"""

import os

from core.commissary.paths import split_paths
from core.download_plugins.album_bundle import (
    _candidate_download_roots,
    resolve_reported_save_path,
)


def _cfg(values):
    return lambda key, default=None: values.get(key, default)


def test_split_paths_accepts_the_legacy_list_and_the_settings_string():
    assert split_paths(["/a", " /b ", "", None, "/a"]) == ["/a", "/b"]
    assert split_paths("/a; /b\n/c") == ["/a", "/b", "/c"]
    assert split_paths("/only") == ["/only"]
    assert split_paths("") == [] and split_paths(None) == []


def test_every_listed_folder_becomes_a_download_root():
    roots = _candidate_download_roots(_cfg({
        "download_source.torrent_download_path": ["/dl/Movies", "/dl/Music"],
        "download_source.usenet_download_path": "/nz/a; /nz/b",
    }))
    assert roots == ["/nz/a", "/nz/b", "/dl/Movies", "/dl/Music"]
    assert not any("[" in r for r in roots)


def test_a_download_in_the_second_category_folder_is_found(tmp_path):
    movies = tmp_path / "Movies"
    music = tmp_path / "Music"
    movies.mkdir()
    (music / "Artist - Album").mkdir(parents=True)
    (music / "Artist - Album" / "01.flac").write_bytes(b"x")

    got = resolve_reported_save_path(
        "/client/side/path/Artist - Album",
        _cfg({"download_source.torrent_download_path": [str(movies), str(music)]}),
    )
    assert os.path.normpath(got) == os.path.normpath(str(music / "Artist - Album"))


def test_the_settings_box_shows_a_legacy_list_joined_not_comma_mangled():
    """Assigning an array to an <input> stringifies it as 'a,b', and saving
    that back would write a single bogus path."""
    from pathlib import Path

    js = (Path(__file__).resolve().parents[2] / "webui" / "static" / "settings.js").read_text(encoding="utf-8")
    assert "[].concat(settings.download_source?.torrent_download_path || []).join('; ')" in js
    assert "[].concat(settings.download_source?.usenet_download_path || []).join('; ')" in js
