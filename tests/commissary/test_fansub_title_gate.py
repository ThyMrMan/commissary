"""Fansub tags, colon subtitles and readable rejections in the title gate
(the legacy fork's additions to upstream's Radarr/Sonarr-parity gate).

Original notes:

A text search for "Paradox (2017)" was accepting "The.Cloverfield.Paradox.2018..." —
the release title is only a SUBSTRING and the year is one off, yet nothing checked
the title (movies passed on year alone). These lock in a real title match: the
release's parsed title must equal the wanted film/show (tolerating only trailing
edition words), so a different film is rejected, while legit releases still pass.
"""

from __future__ import annotations

from core.video.release_parse import (
    extract_title, normalize_title, titles_match, parse_release)
from core.video.quality_eval import evaluate_release

# A permissive profile so ONLY the title/year gate decides accept vs reject.
_PROFILE = {"tiers": [{"key": t, "enabled": True} for t in
                      ("webdl-1080p", "bluray-1080p", "webrip-1080p", "bluray-2160p")]}



def test_fansub_leading_group_tag_and_glued_episode_number_dont_poison_the_title():
    # reported bug: '[SubsPlease] DIGIMON BEATBREAK - 40 [...]' was rejected as
    # 'Wrong title (subsplease digimon beatbreak 40 — wanted digimon beatbreak)' —
    # the bracketed group tag and the dash-glued episode number both leaked into
    # the extracted title.
    name = ("[SubsPlease] DIGIMON BEATBREAK - 40 [Web][MKV][h264][1080p][AAC 2.0]"
            "[Softsubs (SubsPlease)][Episode 40]")
    assert titles_match(name, "DIGIMON BEATBREAK") is True
    # a real mismatch behind the same tag convention must still reject
    assert titles_match("[SubsPlease] Naruto - 40 [1080p]", "One Piece") is False
    # non-anime releases (no leading bracket tag) are completely unaffected —
    # a number that's actually part of the title still isn't stripped
    assert titles_match("Moana.2.2024.1080p.WEBRip", "Moana 2") is True
    assert titles_match("Moana.2016.1080p.BluRay", "Moana 2") is False


# ── colon subtitles (release carries the FULL official title, TMDB only the head) ─
# Reported bug: '[SubsPlease] The Frontier Lord Begins with Zero Subjects: Tales of
# Blue Dias and the Onikin Alna - 04 [...]' was rejected against TMDB's shorter
# 'The Frontier Lord Begins with Zero Subjects'. The fansub tag and glued episode
# number were already handled; what remained was the ': <subtitle>' tail, which
# normalize_title flattens to a space so nothing downstream could tell it apart
# from a different show's extra words.
_FRONTIER = ("[SubsPlease] The Frontier Lord Begins with Zero Subjects: Tales of Blue "
             "Dias and the Onikin Alna - 04 [Web][MKV][h264][1080p][AAC 2.0]"
             "[Softsubs (SubsPlease)][Episode 4]")
# Loosely-named episode releases land on the 'web-1080p' tier (tier_key assumes web
# when the resolution is known but the source isn't), which _PROFILE doesn't enable —
# without it the tier check rejects first and the title verdict never surfaces.
_EP_PROFILE = {"tiers": _PROFILE["tiers"] + [{"key": "web-1080p", "enabled": True}]}


def test_the_reported_bug_colon_subtitle_is_accepted():
    assert titles_match(_FRONTIER, "The Frontier Lord Begins with Zero Subjects") is True


def test_reported_bug_accepted_end_to_end_as_an_absolute_numbered_episode():
    parsed = parse_release(_FRONTIER)
    v = evaluate_release(parsed, _EP_PROFILE, scope="episode", want_absolute=4,
                         want_title="The Frontier Lord Begins with Zero Subjects")
    assert v["accepted"] is True
    assert v.get("rejected") is None


def test_colon_subtitles_match_across_naming_conventions():
    assert titles_match(
        "[SubsPlease] That Time I Got Reincarnated as a Slime: Coleus' Dream - 03 [1080p]",
        "That Time I Got Reincarnated as a Slime") is True
    # scene TV naming, not just fansub
    assert titles_match("Delicious.in.Dungeon: Senshi.no.Kanshoku.S01E05.1080p.WEB-DL",
                        "Delicious in Dungeon") is True
    # exactly three words after article-stripping — pins the floor from below
    assert titles_match("[Erai-raws] The Eminence in Shadow: Second Season - 05 [1080p]",
                        "The Eminence in Shadow") is True


def test_colon_split_never_drops_a_franchise_installment():
    """The head must be long enough to identify the work on its own, and the tail
    must not be an installment marker — otherwise 'Dune: Part Two' would satisfy a
    search for 'Dune'. A sequel number always sits LEFT of the colon, so it is never
    dropped either."""
    assert titles_match("Dune: Part Two 2024 1080p BluRay x265", "Dune") is False
    assert titles_match("Alien: Romulus 2024 1080p WEB-DL", "Alien") is False
    assert titles_match("Star Wars: Episode IV - A New Hope 1977 1080p", "Star Wars") is False
    assert titles_match("John Wick: Chapter 4 2023 1080p", "John Wick") is False
    assert titles_match("Kill Bill: Vol. 1 2003 1080p BluRay", "Kill Bill") is False
    assert titles_match("The Hunger Games: Catching Fire 2013 1080p", "The Hunger Games") is False
    assert titles_match("Moana 2: The Return 2024 1080p", "Moana") is False


def test_colon_split_floor_is_three_words_because_episodes_have_no_year_backstop():
    """_scope_ok's episode branch runs no year check, so a two-word head like
    'Star Trek' has nothing behind it if the title gate lets it through."""
    assert titles_match("Star Trek: Discovery S01E01 1080p WEB-DL", "Star Trek") is False


def test_the_wanted_title_is_never_colon_split():
    """Splitting the WANT side would make 'mission' an acceptable title for
    'Mission: Impossible - Fallout' and let any 'Mission: ...' release through."""
    assert titles_match("Mission: Impossible - Fallout 2018 1080p", "Mission") is False
    # ...while a wanted title that legitimately contains a colon still matches
    assert titles_match("Mission: Impossible - Fallout 2018 1080p",
                        "Mission: Impossible - Fallout") is True


def test_colon_path_does_not_weaken_the_existing_fansub_reject():
    assert titles_match("[SubsPlease] Naruto: Shippuden - 40 [1080p]", "One Piece") is False


def test_brackets_do_not_leak_into_the_rejection_message():
    """The cut at a quality token lands inside the bracket introducing it, so the
    orphaned '[' used to reach the user-facing 'Wrong title (...)' text. Upstream's
    extract_title is left as it is; the rejection message is cleaned instead."""
    v = evaluate_release(parse_release("[SubsPlease] Foo - 40 [Web][1080p]"), _EP_PROFILE,
                         scope="episode", want_season=1, want_episode=40, want_title="Bar")
    assert v["rejected"] == "Wrong title (Foo 40 — wanted Bar)"
    assert "[" not in v["rejected"] and "SubsPlease" not in v["rejected"]
    # the absolute-episode probe reads the same helper and is unaffected
    from core.video.release_parse import has_absolute_episode
    assert has_absolute_episode("[Erai-raws] Some Anime - 1071 [1080p]", 1071) is True


def test_rejection_message_renders_an_alias_set_readably():
    parsed = parse_release("Some.Other.Show.S02E03.1080p")
    v = evaluate_release(parsed, _EP_PROFILE, scope="episode", want_season=2, want_episode=3,
                         want_title=["The Wire", "Sur Ecoute"])
    assert v["accepted"] is False
    assert "wanted The Wire / Sur Ecoute" in (v.get("rejected") or "")


def test_fansub_absolute_episode_is_extractable_on_its_own():
    """The manual-import queue needs the same signal to tell a fansub EPISODE
    apart from a movie — there's no SxxExx and no season anywhere in the name."""
    from core.commissary.release_titles import fansub_absolute_episode
    assert fansub_absolute_episode("[SubsPlease] DIGIMON BEATBREAK - 40 [1080p][AAC]") == 40
    assert fansub_absolute_episode("[Erai-raws] Some Anime - 1071 [1080p]") == 1071
    # no leading group tag → not the fansub convention, whatever the trailing dash
    assert fansub_absolute_episode("The.Matrix.1999.1080p.BluRay.x265-GRP") is None
    assert fansub_absolute_episode("Moana 2 2024 1080p WEBRip") is None
    assert fansub_absolute_episode("") is None
