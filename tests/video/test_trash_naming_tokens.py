"""Sonarr/Radarr ``{Token}`` naming — the TRaSH schemes must work copy-pasted.

That is the whole acceptance test for this feature: someone reads
trash-guides.info, copies a format string, pastes it into Settings, and gets the
filename the guide promises. Everything else here exists to pin the grammar that
makes those strings work — optional groups, split brackets, padding, truncation
— and to guarantee the ``$token`` templates every existing install already has
keep rendering byte-for-byte identically.
"""

from __future__ import annotations

import pytest

from core.video import mediainfo, naming_tokens, organization as org
from core.video.release_parse import edition_tags, parse_release

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
        for name in org.brace_token_names(scope):
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
    parsed = mediainfo.parse_ffprobe({
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
    assert parse_release(release)["edition"] == expected


def test_a_plain_title_containing_an_edition_word_is_not_given_an_edition():
    """'cut' and 'edition' alone appear in ordinary titles; only real edition
    phrases count."""
    assert edition_tags("The.Cut.2014.1080p.BluRay") is None
    assert edition_tags("Special.2006.1080p.WEB") is None


# ── renaming an existing file must reproduce what the import wrote ───────────
# Reported as "the Rename Files picker doesn't show the new variables". The
# short list was the visible symptom of something worse: only the IMPORT path
# supplied the new fields, so for the same file the rename preview and the
# naming-conformance job computed a name WITHOUT its audio, dynamic range and
# release group. Conformance would therefore flag correctly-named files as
# wrong, and approving the fix would strip that information off the filename.

_TRASH_EP = (
    "{Series CleanTitleWithoutYear} {(Series Year)} - S{season:00}E{episode:00} - "
    "{Episode CleanTitle:90}{[Quality Full]}{[Mediainfo AudioCodec}"
    "{ Mediainfo AudioChannels]}{[MediaInfo VideoDynamicRangeType]}"
    "{[Mediainfo VideoCodec]}{-Release Group}")


def _imported_name():
    return _name("episode", _TRASH_EP, {
        "series": "Silo", "year": 2023, "season": 3, "episode": 6,
        "episode_title": "The Getaway", "quality": "WEBDL-1080p", "audio_codec": "eac3",
        "audio_channels": "5.1", "codec": "hevc", "dynamic_range_type": "DV",
        "release_group": "NTb"})


def _library_row():
    """The row a scan leaves behind for that file, plus its own filename."""
    return {"show_title": "Silo", "show_year": 2023, "season_number": 3,
            "episode_number": 6, "episode_title": "The Getaway", "quality": "WEBDL-1080p",
            "video_codec": "hevc", "resolution": "1080p", "release_source": "web-dl",
            "audio_codec": "eac3", "audio_channels": 6, "dynamic_range": "DV",
            "relative_path": "/tv/Silo/Season 03/" + _imported_name()}


def test_a_rename_reproduces_the_name_the_import_wrote():
    from core.video.mass_rename import _episode_fields
    rendered = _name("episode", _TRASH_EP, _episode_fields(_library_row()))
    assert rendered == _imported_name()


def test_the_conformance_job_agrees_with_the_import_too():
    from core.video.repair.naming_conformance import _fields_of
    row = _library_row()
    conf_row = {"series": "Silo", "year": 2023, "season": 3, "episode": 6,
                "episode_title": "The Getaway", "quality": "WEBDL-1080p",
                "video_codec": "hevc", "audio_codec": "eac3", "audio_channels": 6,
                "dynamic_range": "DV", "relative_path": row["relative_path"]}
    assert _name("episode", _TRASH_EP, _fields_of(conf_row)) == _imported_name()


def test_the_release_group_is_recovered_from_the_existing_filename():
    """It has no column — the current name is the only place it survives, and
    the group pattern anchors to end-of-string, so the extension has to go."""
    fields = org.library_media_fields(
        {}, "Silo (2023) - S03E06 - The Getaway[WEBDL-1080p][x265]-NTb.mkv")
    assert fields["release_group"] == "NTb"
    assert org.library_media_fields({}, "Some.Movie.2020.IMAX.1080p.mkv")["edition"] == "IMAX"


def test_scanned_channel_counts_become_layout_labels():
    assert org.library_media_fields({"audio_channels": 6})["audio_channels"] == "5.1"
    assert org.library_media_fields({"audio_channels": 8})["audio_channels"] == "7.1"
    assert org.library_media_fields({})["audio_channels"] is None


def test_import_only_tokens_are_named_so_a_rename_cannot_be_lossy():
    """A template asking for something a library row cannot supply renders a
    SHORTER name that looks canonical. Callers that rename existing files are
    told which tokens those are, so they can warn instead of pretending."""
    assert org.template_uses_unavailable_tokens(_TRASH_EP) == []
    assert org.template_uses_unavailable_tokens(
        _TRASH_EP + "[{MediaInfo VideoBitDepth}bit]") == ["MediaInfo VideoBitDepth"]
    # a $token template can always be reproduced — never flagged
    assert org.template_uses_unavailable_tokens(org.DEFAULTS["episode_template"]) == []
    # Custom formats are matched against a release NAME and a library file has
    # one, so they are computable here — see library_media_fields.
    assert org.template_uses_unavailable_tokens("{Movie CleanTitle} {Custom Formats}") == []


def test_the_conformance_job_examines_files_and_warns_instead_of_standing_down():
    """It used to refuse: a template naming ANY import-only token made the job
    skip every file of that scope, silently. The user saw "0 findings", which is
    exactly what a fully-conforming library looks like — so the tool appeared
    broken, and the app's own recommended TRaSH scheme triggered it.

    The findings ARE a preview and nothing renames without approval, so the job
    now looks, reports, and says on the finding what it could not reproduce.
    """
    from core.video.repair.naming_conformance import NamingConformanceJob
    from core.video.repair.base import JobContext

    looked = []

    class _DB:
        def set_setting(self, *a):
            return None

        def get_setting(self, key):
            import json
            return json.dumps({"episode_template": _TRASH_EP + "{MediaInfo AudioLanguages}",
                               "movie_template": "{Movie CleanTitle}"}) \
                if key == "organization" else None

        def all_library_paths(self, kind=None):
            return ["/tv"]

        def repair_library_files(self):
            looked.append(True)
            return []

        def repair_dismiss_absent(self, *a):
            return None

    NamingConformanceJob().scan(JobContext(db=_DB()))
    assert looked, "the job must examine the library, not stand down on it"


def test_the_rename_picker_offers_the_same_vocabulary_as_settings():
    """The reported bug: Settings introduced tokens the rename picker never
    listed, so they looked unavailable where you actually rename files."""
    from core.video.mass_rename import tokens_for
    for kind, scope in (("show", "episode"), ("movie", "movie")):
        offered = {t["token"] for t in tokens_for(kind, _library_row())}
        for name in org.brace_token_names(scope):
            assert "{%s}" % name in offered, (kind, name)


def test_the_picker_marks_tokens_it_cannot_fill_here():
    from core.video.mass_rename import tokens_for
    by_token = {t["token"]: t for t in tokens_for("show", _library_row())}
    bitdepth = by_token["{MediaInfo VideoBitDepth}"]
    assert bitdepth["example"] == ""
    assert "only available at import" in bitdepth["description"]
    # ...while one it CAN fill shows the real value for this title
    # the picker emits the canonical spelling; the guides' 'Mediainfo' also
    # renders, because token matching ignores case
    assert by_token["{MediaInfo AudioChannels}"]["example"] == "5.1"
    assert by_token["{Release Group}"]["example"] == "NTb"
