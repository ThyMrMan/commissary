"""Untagged rips named ``<Album> <NN> <Title>`` have to match.

Reported from a live install: an album of ``Blue Blood 01 Blue Blood.flac``
files matched NOTHING automatically, and the user had to drag every track into
place by hand.

The title fallback only stripped a track number at the START of the stem, so
with the album name sitting in front of the number nothing was stripped and
the whole string was scored against the track title. Measured before the fix:
0.340 against a 0.400 threshold — close enough that the naming looked
supported and just silently wasn't.
"""

from __future__ import annotations

import os

from core.imports.album_matching import (
    MATCH_THRESHOLD,
    _FORMAT_QUALITY_RANK,
    match_files_to_tracks,
    score_file_against_track,
    strip_album_prefix,
)

TRACK = {"name": "Blue Blood", "track_number": 2}


def _rank(path):
    return _FORMAT_QUALITY_RANK.get(os.path.splitext(path)[1].lower(), 0)


# ── the reported case ────────────────────────────────────────────────────────
def test_the_reported_filename_now_matches():
    score = score_file_against_track(
        "/stage/Blue Blood 01 Blue Blood.flac", {}, TRACK, target_album="Blue Blood")
    assert score >= MATCH_THRESHOLD


def test_it_scores_the_same_as_the_form_that_always_worked():
    """`01 Blue Blood.flac` scored 0.525 and matched. The album-prefixed name
    is the same information, so it should reach the same score — not merely
    scrape over the threshold."""
    plain = score_file_against_track(
        "/stage/01 Blue Blood.flac", {}, TRACK, target_album="Blue Blood")
    prefixed = score_file_against_track(
        "/stage/Blue Blood 01 Blue Blood.flac", {}, TRACK, target_album="Blue Blood")
    assert prefixed == plain


def test_a_whole_untagged_album_matches_end_to_end():
    tracks = [{"name": "Prologue", "track_number": 1},
              {"name": "Blue Blood", "track_number": 2},
              {"name": "Week End", "track_number": 3}]
    files = ["/s/Blue Blood 01 Prologue.flac",
             "/s/Blue Blood 02 Blue Blood.flac",
             "/s/Blue Blood 03 Week End.flac"]
    result = match_files_to_tracks(files, {f: {} for f in files}, tracks,
                                   target_album="Blue Blood", quality_rank=_rank)
    assert result["unmatched_files"] == []
    paired = {m["track"]["name"]: os.path.basename(m["file"]) for m in result["matches"]}
    assert paired == {
        "Prologue": "Blue Blood 01 Prologue.flac",
        "Blue Blood": "Blue Blood 02 Blue Blood.flac",
        "Week End": "Blue Blood 03 Week End.flac",
    }


# ── the prefix stripper itself ───────────────────────────────────────────────
def test_strip_album_prefix_handles_the_common_separators():
    for stem in ("Blue Blood 01 Blue Blood",
                 "Blue Blood - 01 - Blue Blood",
                 "Blue Blood_01_Blue Blood"):
        assert strip_album_prefix(stem, "Blue Blood") == "Blue Blood", stem


def test_the_album_name_is_matched_case_insensitively():
    assert strip_album_prefix("BLUE BLOOD 01 Week End", "Blue Blood") == "Week End"


def test_a_track_named_after_its_album_is_not_reduced_to_nothing():
    """The failure mode this guard exists for: stripping "Blue Blood" from
    "Blue Blood" would leave an empty title that matches everything."""
    assert strip_album_prefix("Blue Blood", "Blue Blood") == "Blue Blood"


def test_a_stem_that_is_only_the_album_and_a_number_is_left_alone():
    assert strip_album_prefix("Blue Blood 01", "Blue Blood") == "Blue Blood 01"


def test_an_unrelated_stem_is_untouched():
    assert strip_album_prefix("Week End", "Blue Blood") == "Week End"


def test_the_album_name_must_end_on_a_token_boundary():
    """A plain string prefix is not enough: album "Blue Blood" matching the
    start of "Blue Bloodhound Blues" would strip it to "hound Blues", which
    is a longer word being cut in half rather than a prefix being removed."""
    assert strip_album_prefix("Blue Bloodhound Blues", "Blue Blood") == "Blue Bloodhound Blues"


def test_no_album_name_is_a_no_op():
    assert strip_album_prefix("Blue Blood 01 Week End", "") == "Blue Blood 01 Week End"


# ── it must not loosen matching ──────────────────────────────────────────────
def test_a_real_title_tag_still_wins():
    """A tagged file is authoritative — rewriting its title risks mangling a
    track legitimately named after its album."""
    tagged = score_file_against_track(
        "/stage/Blue Blood 01 Blue Blood.flac",
        {"title": "Blue Blood", "track_number": 2, "artist": "X Japan",
         "album": "Blue Blood"},
        TRACK, target_album="Blue Blood")
    assert tagged > 0.9


def test_a_wrong_track_still_does_not_match():
    """Best-of-two readings must not become "matches anything": the prefix
    strip only ever offers the title, never widens what counts as agreement."""
    other = {"name": "Rose Of Pain", "track_number": 7}
    score = score_file_against_track(
        "/stage/Blue Blood 01 Blue Blood.flac", {}, other, target_album="Blue Blood")
    assert score < MATCH_THRESHOLD
