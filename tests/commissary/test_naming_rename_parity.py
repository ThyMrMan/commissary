"""A file named by an import must look correctly named to the renamer and to
Naming Conformance - or approving their "fix" deletes detail from the filename.

The live templates name folders with a TMDB id and files with the scan's audio
and HDR facts plus the release group. Upstream's rename and conformance passes
carried none of those, so for every such file they proposed a SHORTER name.
"""

from __future__ import annotations

import json
import os

import pytest

import core.video.mass_rename as mr
from core.commissary import naming
from core.video import organization as org
from database.video_database import VideoDatabase
from tests.commissary.test_naming_tokens import LIVE_EPISODE, LIVE_MOVIE

_TRASH_EP = (
    "{Series CleanTitleWithoutYear} {(Series Year)} - S{season:00}E{episode:00} - "
    "{Episode CleanTitle:90}{[Quality Full]}{[Mediainfo AudioCodec}"
    "{ Mediainfo AudioChannels]}{[MediaInfo VideoDynamicRangeType]}"
    "{[Mediainfo VideoCodec]}{-Release Group}")


def _imported(scope, tmpl, fields):
    key = "movie_template" if scope == "movie" else "episode_template"
    return org.render_path(scope, "/root", fields, {key: tmpl}, ".mkv")["filename"]


def _imported_episode():
    return _imported("episode", _TRASH_EP, {
        "series": "Silo", "year": 2023, "season": 3, "episode": 6,
        "episode_title": "The Getaway", "quality": "WEBDL-1080p", "audio_codec": "eac3",
        "audio_channels": "5.1", "codec": "hevc", "dynamic_range_type": "DV",
        "release_group": "NTb"})


# ── the field builders (ported from the old fork's tests) ───────────────────

def _library_row():
    return {"show_title": "Silo", "show_year": 2023, "season_number": 3,
            "episode_number": 6, "episode_title": "The Getaway", "quality": "WEBDL-1080p",
            "video_codec": "hevc", "resolution": "1080p", "release_source": "web-dl",
            "audio_codec": "eac3", "audio_channels": 6, "dynamic_range": "DV",
            "relative_path": "/tv/Silo/Season 03/" + _imported_episode()}


def _with_facts(row):
    row = dict(row)
    row["_naming"] = {k: v for k, v in naming.library_media_fields(
        row, row["relative_path"]).items() if v not in (None, "")}
    return row


def test_a_rename_reproduces_the_name_the_import_wrote():
    fields = mr._episode_fields(_with_facts(_library_row()))
    assert _imported("episode", _TRASH_EP, fields) == _imported_episode()


def test_the_conformance_job_agrees_with_the_import_too():
    from core.video.repair.naming_conformance import _fields_of
    row = _library_row()
    conf_row = _with_facts({"series": "Silo", "year": 2023, "season": 3, "episode": 6,
                            "episode_title": "The Getaway", "quality": "WEBDL-1080p",
                            "video_codec": "hevc", "audio_codec": "eac3", "audio_channels": 6,
                            "dynamic_range": "DV", "relative_path": row["relative_path"]})
    assert _imported("episode", _TRASH_EP, _fields_of(conf_row)) == _imported_episode()


def test_the_release_group_is_recovered_from_the_existing_filename():
    fields = naming.library_media_fields(
        {}, "Silo (2023) - S03E06 - The Getaway[WEBDL-1080p][x265]-NTb.mkv")
    assert fields["release_group"] == "NTb"
    assert naming.library_media_fields({}, "Some.Movie.2020.IMAX.1080p.mkv")["edition"] == "IMAX"


def test_a_bracketed_custom_format_is_not_read_as_an_edition():
    """'[IMAX]' from {[Custom Formats]} beside 'edition-Remastered' made the
    rename propose 'edition-Remastered IMAX'."""
    stem = "The Matrix (1999) (tmdb-603) edition-Remastered [IMAX][Bluray-2160p]-FraMeSToR"
    assert naming.library_edition(stem) == "Remastered"
    assert naming.library_edition("Dune.2021.IMAX.1080p") == "IMAX"
    assert naming.library_edition("Heat (1995) [IMAX][Bluray-1080p]") is None


def test_scanned_channel_counts_become_layout_labels():
    assert naming.library_media_fields({"audio_channels": 6})["audio_channels"] == "5.1"
    assert naming.library_media_fields({"audio_channels": 8})["audio_channels"] == "7.1"
    assert naming.library_media_fields({})["audio_channels"] is None


def test_import_only_tokens_are_named_so_a_rename_cannot_be_lossy():
    assert naming.template_uses_unavailable_tokens(_TRASH_EP) == []
    assert naming.template_uses_unavailable_tokens(
        _TRASH_EP + "[{MediaInfo VideoBitDepth}bit]") == ["MediaInfo VideoBitDepth"]
    assert naming.template_uses_unavailable_tokens(org.DEFAULTS["episode_template"]) == []
    assert naming.template_uses_unavailable_tokens("{Movie CleanTitle} {Custom Formats}") == []


def test_upstream_values_are_never_overridden():
    row = {"_naming": {"year": 1999, "tmdbid": 5}}
    assert naming.with_naming(row, {"year": 2001, "tmdbid": None}) == {"year": 2001, "tmdbid": 5}


# ── end to end, against a real library on disk ─────────────────────────────

@pytest.fixture()
def env(tmp_path):
    import api.video as videoapi
    db = VideoDatabase(database_path=str(tmp_path / "video_library.db"))
    videoapi._video_db = db
    movies, tv = tmp_path / "Movies", tmp_path / "TV"
    movies.mkdir()
    tv.mkdir()
    db.set_setting("movies_path", str(movies))
    db.set_setting("tv_path", str(tv))
    db.set_setting("organization", json.dumps({"movie_template": LIVE_MOVIE,
                                               "episode_template": LIVE_EPISODE}))
    yield db, movies, tv
    videoapi._video_db = None


def _seed(db, movies, tv):
    """Files named exactly as the live templates import them."""
    from core.video.custom_formats import save_format
    save_format(db, {"name": "IMAX", "include": ["IMAX"], "score": 10})
    mpath = org.render_path("movie", str(movies), {
        "title": "The Matrix", "year": 1999, "tmdbid": 603, "quality": "Bluray-2160p",
        "codec": "hevc", "audio_codec": "truehd", "audio_channels": "7.1",
        "dynamic_range_type": "HDR10", "release_group": "FraMeSToR", "edition": "Remastered",
        "custom_formats": "IMAX"}, {"movie_template": LIVE_MOVIE}, ".mkv")["path"]
    epath = org.render_path("episode", str(tv), {
        "series": "Silo", "year": 2023, "tmdbid": 125988, "season": 3, "episode": 6,
        "episode_title": "The Getaway", "quality": "WEBDL-1080p", "codec": "hevc",
        "audio_codec": "eac3", "audio_channels": "5.1", "dynamic_range_type": "DV",
        "release_group": "NTb"}, {"episode_template": LIVE_EPISODE}, ".mkv")["path"]
    for p in (mpath, epath):
        os.makedirs(os.path.dirname(p), exist_ok=True)
        with open(p, "wb") as fh:
            fh.write(b"x" * 1024)
    mid = db.upsert_movie("plex", {
        "server_id": "m1", "title": "The Matrix", "year": 1999, "tmdb_id": 603,
        "file": {"relative_path": mpath, "resolution": "2160p", "quality": "Bluray-2160p",
                 "size_bytes": 1024, "video_codec": "hevc", "audio_codec": "truehd",
                 "audio_channels": 8, "dynamic_range": "HDR10"}})
    db.upsert_show_tree("plex", {
        "server_id": "s1", "title": "Silo", "year": 2023, "tmdb_id": 125988, "seasons": [
            {"season_number": 3, "episodes": [{
                "episode_number": 6, "title": "The Getaway", "server_id": "e1",
                "file": {"relative_path": epath, "resolution": "1080p", "quality": "WEBDL-1080p",
                         "size_bytes": 1024, "video_codec": "hevc", "audio_codec": "eac3",
                         "audio_channels": 6, "dynamic_range": "DV"}}]}]})
    conn = db._get_connection()
    conn.execute("UPDATE movies SET has_file=1 WHERE id=?", (mid,))
    conn.execute("UPDATE episodes SET has_file=1")
    conn.commit()
    conn.close()
    return mpath, epath


def test_a_rename_preview_proposes_nothing_for_correctly_named_files(env):
    db, movies, tv = env
    _seed(db, movies, tv)
    assert len(db.repair_owned_movie_files()) == 1 and len(db.rename_owned_episode_files()) == 1
    res = mr.preview()
    assert res["unresolved"] == 0
    assert res["entries"] == [], [(e["current"], e["proposed"]) for e in res["entries"]]


def test_naming_conformance_finds_nothing_either(env):
    from core.video.repair.base import JobContext
    from core.video.repair.naming_conformance import NamingConformanceJob
    db, movies, tv = env
    _seed(db, movies, tv)
    found = []
    res = NamingConformanceJob().scan(JobContext(db=db, create_finding=lambda **kw: found.append(kw) or True))
    assert (res.scanned, res.skipped) == (2, 0), "both files really examined"
    assert found == [], [(f["description"]) for f in found]


def test_a_template_with_an_import_only_token_warns_on_its_findings(env):
    from core.video.repair.base import JobContext
    from core.video.repair.naming_conformance import NamingConformanceJob
    db, movies, tv = env
    mpath, _epath = _seed(db, movies, tv)
    # an off-template name, so there is a finding to look at
    odd = os.path.join(os.path.dirname(mpath), "matrix.1999.mkv")
    os.replace(mpath, odd)
    conn = db._get_connection()
    conn.execute("UPDATE media_files SET relative_path=? WHERE relative_path=?", (odd, mpath))
    conn.commit()
    conn.close()
    db.set_setting("organization", json.dumps({
        "movie_template": LIVE_MOVIE + "{ Original Title}", "episode_template": LIVE_EPISODE}))
    found = []
    NamingConformanceJob().scan(JobContext(db=db, create_finding=lambda **kw: found.append(kw) or True))
    assert [f["entity_type"] for f in found] == ["movie"]
    assert found[0]["severity"] == "warning" and "Original Title" in found[0]["description"]
    # the plain live template raises the same finding as plain info
    db.set_setting("organization", json.dumps({"movie_template": LIVE_MOVIE,
                                               "episode_template": LIVE_EPISODE}))
    found.clear()
    NamingConformanceJob().scan(JobContext(db=db, create_finding=lambda **kw: found.append(kw) or True))
    assert [f["severity"] for f in found] == ["info"]
