"""Sonarr/Radarr ``{Token}`` naming — the TRaSH schemes must work copy-pasted.

Ported from the old fork's tests/video/test_trash_naming_tokens.py (1.9.12).

That is the whole acceptance test for this feature: someone reads
trash-guides.info, copies a format string, pastes it into Settings, and gets the
filename the guide promises. Everything else here exists to pin the grammar that
makes those strings work — optional groups, split brackets, padding, truncation
— and to guarantee the ``$token`` templates every existing install already has
keep rendering byte-for-byte identically.
"""

from __future__ import annotations

import pytest

from core.commissary import naming, naming_tokens
from core.commissary import naming as mediainfo          # the ffprobe fact helpers live here
from core.commissary.naming import edition_tags
from core.video import mediainfo as upstream_mediainfo
from core.video import organization as org

# Verbatim from the guides.
SONARR_STANDARD = (
    "{Series CleanTitleWithoutYear} {(Series Year)} - S{season:00}E{episode:00} - "
    "{Episode CleanTitle:90} {[Custom Formats]}{[Quality Full]}"
    "{[Mediainfo AudioCodec}{ Mediainfo AudioChannels]}"
    "{[MediaInfo VideoDynamicRangeType]}{[Mediainfo VideoCodec]}{-Release Group}")
SONARR_DAILY = (
    "{Series CleanTitleWithoutYear} {(Series Year)} - {Air-Date} - {Episode CleanTitle:90} "
    "{[Custom Formats]}{[Quality Full]}{[Mediainfo AudioCodec}{ Mediainfo AudioChannels]}"
    "{[MediaInfo VideoDynamicRangeType]}{[Mediainfo VideoCodec]}{-Release Group}")
SONARR_ANIME = (
    "{Series CleanTitleWithoutYear} {(Series Year)} - S{season:00}E{episode:00} - "
    "{absolute:000} - {Episode CleanTitle:90} {[Custom Formats]}{[Quality Full]}"
    "{[Mediainfo AudioCodec}{ Mediainfo AudioChannels]}{MediaInfo AudioLanguages}"
    "{[MediaInfo VideoDynamicRangeType]}[{Mediainfo VideoCodec }{MediaInfo VideoBitDepth}bit]"
    "{-Release Group}")
RADARR_STANDARD = (
    "{Movie CleanTitle} {(Release Year)} {edition-{Edition Tags}} {[MediaInfo 3D]}"
    "{[Custom Formats]}{[Quality Full]}{[Mediainfo AudioCodec}{ Mediainfo AudioChannels]}"
    "{[MediaInfo VideoDynamicRangeType]}{[Mediainfo VideoCodec]}{-Release Group}")

FULL_EPISODE = {
    "series": "Silo", "year": 2023, "season": 3, "episode": 6,
    "episode_title": "The Getaway", "quality": "WEBDL-1080p", "audio_codec": "eac3",
    "audio_channels": "5.1", "codec": "hevc", "dynamic_range_type": "DV",
    "release_group": "NTb", "custom_formats": "AMZN",
}
FULL_MOVIE = {
    "title": "The Matrix", "year": 1999, "quality": "Bluray-2160p", "audio_codec": "truehd",
    "audio_channels": "7.1", "codec": "hevc", "dynamic_range_type": "HDR10",
    "release_group": "FraMeSToR", "custom_formats": "IMAX", "edition": "Remastered",
}


def _name(scope, template, fields, ext=".mkv"):
    key = "movie_template" if scope == "movie" else "episode_template"
    return org.render_path(scope, "/root", fields, {key: template}, ext)["filename"]


# ── the acceptance test ──────────────────────────────────────────────────────
def test_the_sonarr_standard_scheme_renders_as_documented():
    assert _name("episode", SONARR_STANDARD, FULL_EPISODE) == (
        "Silo (2023) - S03E06 - The Getaway [AMZN][WEBDL-1080p][EAC3 5.1][DV][x265]-NTb.mkv")


def test_the_sonarr_daily_scheme_renders_as_documented():
    daily = {"series": "The Daily Show", "year": 1996, "air_date": "2026-07-08",
             "episode_title": "Guest Name", "quality": "WEBDL-1080p",
             "audio_codec": "aac", "audio_channels": "2.0", "codec": "x264"}
    assert _name("episode", SONARR_DAILY, daily) == (
        "The Daily Show (1996) - 2026-07-08 - Guest Name [WEBDL-1080p][AAC 2.0][x264].mkv")


def test_the_sonarr_anime_scheme_renders_as_documented():
    anime = {**FULL_EPISODE, "series": "Frieren", "absolute": 12, "audio_languages": "JA+EN",
             "video_bit_depth": 10, "dynamic_range_type": None, "custom_formats": ""}
    assert _name("episode", SONARR_ANIME, anime) == (
        "Frieren (2023) - S03E06 - 012 - The Getaway [WEBDL-1080p][EAC3 5.1]JA+EN"
        "[x265 10bit]-NTb.mkv")


def test_the_radarr_standard_scheme_renders_as_documented():
    assert _name("movie", RADARR_STANDARD, FULL_MOVIE) == (
        "The Matrix (1999) edition-Remastered [IMAX][Bluray-2160p][TrueHD 7.1]"
        "[HDR10][x265]-FraMeSToR.mkv")


# ── the grammar those schemes depend on ──────────────────────────────────────
def test_an_empty_token_takes_its_whole_group_with_it():
    """The reason '{[Quality Full]}' is safe: no quality means no brackets
    either, rather than a literal '[]' in the filename."""
    assert _name("episode", "{Series Title}{[Quality Full]}",
                 {"series": "Silo"}) == "Silo.mkv"


def test_a_missing_release_group_leaves_no_trailing_dash():
    assert _name("episode", "{Series Title} - S{season:00}E{episode:00}{-Release Group}",
                 {"series": "Silo", "season": 3, "episode": 6}) == "Silo - S03E06.mkv"


def test_a_split_bracket_pair_collapses_when_half_is_missing():
    """'{[Mediainfo AudioCodec}{ Mediainfo AudioChannels]}' is ONE bracket pair
    across two groups. With no channel count the survivor would be '[EAC3'."""
    tmpl = "{Series Title} {[Mediainfo AudioCodec}{ Mediainfo AudioChannels]}"
    both = _name("episode", tmpl, {"series": "Silo", "audio_codec": "eac3",
                                   "audio_channels": "5.1"})
    codec_only = _name("episode", tmpl, {"series": "Silo", "audio_codec": "eac3"})
    channels_only = _name("episode", tmpl, {"series": "Silo", "audio_channels": "5.1"})
    neither = _name("episode", tmpl, {"series": "Silo"})
    assert both == "Silo [EAC3 5.1].mkv"
    assert codec_only == "Silo EAC3.mkv"          # the unclosed '[' is swept
    assert channels_only == "Silo 5.1.mkv"        # ...and so is the unopened ']'
    assert neither == "Silo.mkv"


def test_a_nested_group_vanishes_with_its_inner_token():
    """'{edition-{Edition Tags}}' must not leave a bare 'edition-' behind."""
    assert _name("movie", "{Movie CleanTitle}{ edition-{Edition Tags}}",
                 {"title": "Dune"}) == "Dune.mkv"
    assert _name("movie", "{Movie CleanTitle}{ edition-{Edition Tags}}",
                 {"title": "Dune", "edition": "IMAX"}) == "Dune edition-IMAX.mkv"


def test_numeric_tokens_zero_pad_and_text_tokens_truncate():
    padded = _name("episode", "S{season:00}E{episode:00} {absolute:000}",
                   {"season": 3, "episode": 6, "absolute": 12})
    assert padded == "S03E06 012.mkv"
    capped = _name("episode", "{Episode CleanTitle:10}",
                   {"episode_title": "A Very Long Episode Title Indeed"})
    assert capped == "A Very Lon.mkv"


def test_token_names_ignore_case_and_spacing():
    """The guides themselves mix 'Mediainfo' and 'MediaInfo' in one line."""
    a = _name("episode", "{MediaInfo VideoCodec}", {"codec": "hevc"})
    b = _name("episode", "{mediainfo videocodec}", {"codec": "hevc"})
    assert a == b == "x265.mkv"


def test_air_date_and_airdate_are_different_tokens():
    """They differ only by a hyphen, so the name-folding must keep hyphens."""
    out = _name("episode", "{Air-Date} vs {AirDate}", {"air_date": "2026-07-08"})
    assert out == "2026-07-08 vs 2026.07.08.mkv"


def test_cleantitle_drops_filename_hostile_characters():
    assert naming_tokens.clean_title("Marvel's Daredevil: Born Again") == "Marvels Daredevil Born Again"
    assert naming_tokens.title_the("The Matrix") == "Matrix, The"


def test_cleantitlewithoutyear_does_not_print_the_year_twice():
    out = _name("episode", "{Series CleanTitleWithoutYear} {(Series Year)}",
                {"series": "Doctor Who (2005)", "year": 2005})
    assert out == "Doctor Who (2005).mkv"


def test_folder_segments_render_and_empty_ones_are_dropped():
    tmpl = "{Series CleanTitleWithoutYear}{ [tvdbid-{TvdbId}]}/Season {season:00}/{Series Title}"
    f = {"series": "Silo", "season": 3, "episode": 6, "tvdbid": 457516}
    assert org.render_path("episode", "/tv", f, {"episode_template": tmpl}, ".mkv")["dir"].replace(
        "\\", "/") == "/tv/Silo [tvdbid-457516]/Season 03"
    f2 = {k: v for k, v in f.items() if k != "tvdbid"}
    assert org.render_path("episode", "/tv", f2, {"episode_template": tmpl}, ".mkv")["dir"].replace(
        "\\", "/") == "/tv/Silo/Season 03"


# ── nothing existing may shift ───────────────────────────────────────────────
def test_the_shipped_defaults_are_unchanged():
    """The defaults stay $token and stay exactly as they were — adopting the
    TRaSH scheme is opt-in, because changing it silently would make every file
    already on disk non-conforming."""
    assert org.DEFAULTS["movie_template"] == "$title ($year)/$title ($year) $quality"
    assert org.DEFAULTS["episode_template"] == (
        "$series/Season $season/$series - S$seasonE$episode - $episodetitle $quality")


def test_a_legacy_dollar_template_renders_exactly_as_before():
    out = org.render_path("episode", "/tv", FULL_EPISODE, {}, ".mkv")["path"].replace("\\", "/")
    assert out == "/tv/Silo/Season 03/Silo - S03E06 - The Getaway WEBDL-1080p.mkv"


def test_a_brace_inside_a_value_is_not_parsed_as_a_group():
    """An episode genuinely titled 'The {Redacted} Job' must not have its title
    eaten by the group parser. $token values are substituted AFTER groups
    resolve, precisely so this can't happen."""
    out = _name("episode", "$series - $episodetitle",
                {"series": "Archer", "episode_title": "The {Redacted} Job"})
    assert out == "Archer - The {Redacted} Job.mkv"


def test_a_slash_in_a_value_cannot_spawn_a_directory():
    out = org.render_path("episode", "/tv", {"series": "AC/DC Live", "season": 1, "episode": 1},
                          {"episode_template": "{Series Title}/E{episode:00}"}, ".mkv")
    assert out["dir"].replace("\\", "/") == "/tv/ACDC Live"


def test_the_token_reference_matches_what_the_renderer_knows():
    """The settings page lists these; it must never advertise a token that
    renders as literal text."""
    for scope in ("movie", "episode"):
        for name in naming.brace_token_names(scope):
            rendered = _name(scope, "{%s}" % name, FULL_EPISODE if scope == "episode" else FULL_MOVIE)
            assert "{" not in rendered, (scope, name, rendered)


# ── the file facts the MediaInfo tokens need ─────────────────────────────────
@pytest.mark.parametrize("channels,layout,expected", [
    (6, None, "5.1"), (8, None, "7.1"), (2, None, "2.0"), (1, None, "1.0"),
    (5, None, "4.1"), (0, "stereo", "2.0"), (None, None, None),
])
def test_audio_channel_labels(channels, layout, expected):
    assert mediainfo.audio_channel_label(channels, layout) == expected


@pytest.mark.parametrize("stream,expected", [
    ({"bits_per_raw_sample": "10"}, 10),
    ({"pix_fmt": "yuv420p10le"}, 10),
    ({"pix_fmt": "yuv420p12le"}, 12),
    ({"pix_fmt": "yuv420p"}, 8),
    ({}, None),
])
def test_video_bit_depth(stream, expected):
    assert mediainfo.video_bit_depth(stream) == expected


@pytest.mark.parametrize("stream,expected", [
    ({"side_data_list": [{"side_data_type": "DOVI configuration record"}]}, "DV"),
    ({"color_transfer": "smpte2084"}, "HDR10"),
    ({"color_transfer": "arib-std-b67"}, "HLG"),
    ({"color_transfer": "bt709"}, None),
    ({}, None),
])
def test_dynamic_range_type(stream, expected):
    assert mediainfo.dynamic_range_type(stream) == expected


def test_audio_languages_skips_undefined():
    streams = [{"codec_type": "audio", "tags": {"language": "eng"}},
               {"codec_type": "audio", "tags": {"language": "und"}},
               {"codec_type": "audio", "tags": {"language": "jpn"}},
               {"codec_type": "video"}]
    assert mediainfo.audio_languages(streams) == "ENG+JPN"
    assert mediainfo.audio_languages([{"codec_type": "audio", "tags": {"language": "und"}}]) is None


def test_the_probe_carries_the_new_facts_through():
    parsed = upstream_mediainfo.parse_ffprobe({
        "format": {"duration": "3600"},
        "streams": [
            {"codec_type": "video", "codec_name": "hevc", "width": 3840, "height": 2160,
             "pix_fmt": "yuv420p10le", "color_transfer": "smpte2084"},
            {"codec_type": "audio", "codec_name": "truehd", "channels": 8,
             "tags": {"language": "eng"}},
        ]})
    assert parsed["ok"] is True
    assert parsed["audio_channels"] == "7.1"
    assert parsed["video_bit_depth"] == 10
    assert parsed["dynamic_range_type"] == "HDR10"
    assert parsed["audio_languages"] == "ENG"


# ── edition detection ────────────────────────────────────────────────────────
@pytest.mark.parametrize("release,expected", [
    ("Blade.Runner.1982.Final.Cut.2160p", "Final Cut"),
    ("Dune.2021.IMAX.Enhanced.1080p", "IMAX"),
    ("Aliens.1986.Directors.Cut.1080p", "Directors Cut"),
    ("Movie.2020.Extended.Edition.1080p", "Extended"),
    ("Movie.2020.1080p.BluRay.x264", None),
])
def test_edition_tags(release, expected):
    assert edition_tags(release) == expected


def test_a_plain_title_containing_an_edition_word_is_not_given_an_edition():
    """'cut' and 'edition' alone appear in ordinary titles; only real edition
    phrases count."""
    assert edition_tags("The.Cut.2014.1080p.BluRay") is None
    assert edition_tags("Special.2006.1080p.WEB") is None



# ── the live settings ────────────────────────────────────────────────────────
# The templates this install actually has saved: before the port, upstream
# rendered them as literal folder names ('{Movie CleanTitle} ({Movie Year})').
LIVE_MOVIE = ("{Movie CleanTitle} ({Movie Year}) /{Movie CleanTitle} {(Release Year)} "
              "(tmdb-{TmdbId}) {edition-{Edition Tags}} {[Custom Formats]}{[Quality Full]}"
              "{[MediaInfo VideoDynamicRangeType]}{[Mediainfo AudioCodec}{ Mediainfo AudioChannels]}"
              "{[Mediainfo VideoCodec]}{-Release Group}")
LIVE_EPISODE = ("{Series CleanTitleWithoutYear} {({Series Year})} (tmdb-{TmdbId}) /Season {season:00}/"
                "{Series CleanTitleWithoutYear} {(Series Year)} - S{season:00}E{episode:00} - "
                "{Episode CleanTitle:90} {[Custom Formats]}{[Quality Full]}"
                "{[Mediainfo AudioCodec}{ Mediainfo AudioChannels]}{[MediaInfo VideoDynamicRangeType]}"
                "{[Mediainfo VideoCodec]}{-Release Group}")


def test_the_live_movie_template_names_a_folder_and_a_file():
    out = org.render_path("movie", "/movies", {**FULL_MOVIE, "tmdbid": 603},
                          {"movie_template": LIVE_MOVIE}, ".mkv")
    assert out["path"].replace("\\", "/") == (
        "/movies/The Matrix (1999)/The Matrix (1999) (tmdb-603) edition-Remastered "
        "[IMAX][Bluray-2160p][HDR10][TrueHD 7.1][x265]-FraMeSToR.mkv")


def test_the_live_episode_template_names_a_folder_and_a_file():
    out = org.render_path("episode", "/tv", {**FULL_EPISODE, "tmdbid": 125988},
                          {"episode_template": LIVE_EPISODE}, ".mkv")
    assert out["path"].replace("\\", "/") == (
        "/tv/Silo (2023) (tmdb-125988)/Season 03/Silo (2023) - S03E06 - The Getaway "
        "[AMZN][WEBDL-1080p][EAC3 5.1][DV][x265]-NTb.mkv")


def test_an_import_supplies_every_fact_the_live_template_asks_for(tmp_path):
    """Through upstream's own planner: release name, probe facts and the
    monitor's custom formats all reach the filename."""
    import json
    from core.video import importer
    probe = upstream_mediainfo.parse_ffprobe({
        "format": {"duration": "3000"},
        "streams": [
            {"codec_type": "video", "codec_name": "hevc", "width": 1920, "height": 1080,
             "side_data_list": [{"side_data_type": "DOVI configuration record"}]},
            {"codec_type": "audio", "codec_name": "eac3", "channels": 6}]})
    dl = {"kind": "show", "title": "Silo", "media_id": 125988, "media_source": "tmdb",
          "release_title": "Silo.S03E06.The.Getaway.1080p.ATVP.WEB-DL.DDP5.1.DV.H.265-NTb",
          "target_dir": str(tmp_path), "_custom_formats": "ATVP",
          "search_ctx": json.dumps({"scope": "episode", "title": "Silo", "year": 2023,
                                    "season": 3, "episode": 6, "episode_title": "The Getaway"})}
    plan = importer.plan_import(dl, str(tmp_path / "x.mkv"), list_dir=lambda d: [], probe=probe,
                               settings={"episode_template": LIVE_EPISODE})
    assert plan["action"] == "import", plan
    assert plan["dest"]["filename"] == (
        "Silo (2023) - S03E06 - The Getaway [ATVP][WEBDL-1080p][EAC3 5.1][DV][x265]-NTb.mkv")


def test_the_monitor_scores_custom_formats_for_the_filename(tmp_path):
    """{Custom Formats} needs the user's format definitions, which only the
    monitor has: it scores the grab and hands the importer the names."""
    import json
    from core.video.custom_formats import save_format
    from core.video.download_monitor import _make_organizer
    from database.video_database import VideoDatabase
    db = VideoDatabase(database_path=str(tmp_path / "video_library.db"))
    db.set_setting("organization", json.dumps({
        "transfer_mode": "copy", "verify_with_ffprobe": False, "save_artwork": False,
        "write_nfo": False, "download_subtitles": False, "carry_subtitles": False,
        "movie_template": "{Movie CleanTitle} {[Custom Formats]}{[Quality Full]}"}))
    save_format(db, {"name": "IMAX", "include": ["IMAX"], "score": 10})
    save_format(db, {"name": "x265", "include": ["x265"], "score": 5})
    src = tmp_path / "dl" / "Dune.2021.IMAX.1080p.BluRay.x264-GRP.mkv"
    src.parent.mkdir()
    src.write_bytes(b"x" * 100)
    dl = {"id": 1, "kind": "movie", "title": "Dune", "year": 2021, "source": "torrent",
          "media_id": 438631, "media_source": "tmdb", "size_bytes": 100,
          "release_title": "Dune.2021.IMAX.1080p.BluRay.x264-GRP",
          "target_dir": str(tmp_path / "lib"),
          "search_ctx": json.dumps({"scope": "movie", "title": "Dune", "year": 2021})}
    patch = _make_organizer(db)(dl, str(src))
    assert patch["status"] == "completed", patch
    assert patch["dest_path"].endswith("Dune [IMAX][Bluray-1080p].mkv"), patch["dest_path"]
