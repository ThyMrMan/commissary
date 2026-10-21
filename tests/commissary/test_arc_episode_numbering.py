"""An arc's own episode numbering -- TVDB's one-season-per-arc anime.

Bleach S17E48 ships as '[SubsPlease] Bleach - Sennen Kessen Hen - 48': the arc
counts from 1, so the show's absolute number (414 -- the 366 episodes before the
arc, plus 48) is written nowhere on the release, and the arc's name after the
show's title reads as a different show. The title gate rejected every release of
the arc and the absolute check rejected anything that got past it, so a whole
season could be filled only by hand no matter how many trackers were searched.
"""

from __future__ import annotations

from core.video.quality_eval import evaluate_release
from core.video.quality_profile import default_profile
from core.commissary.release_titles import arc_episode_match, arc_episode_number
from core.video.release_parse import parse_release

SUBSPLEASE = "[SubsPlease] Bleach - Sennen Kessen Hen - 48 (1080p) [F6CC4D70].mkv"
ERAI = "[Erai-raws] Bleach - Sennen Kessen Hen - 48 [1080p][Multiple Subtitle]"
# Episode 48 of the 2004 run, in the absolute numbering the same groups use.
OLD_RUN = "[SubsPlease] Bleach - 48 (1080p) [A1B2C3D4].mkv"
SCENE = "BLEACH S17E48 1080p DSNP WEB-DL H 264-NORViNE"


def _verdict(name, **kw):
    """The verdict discovery reaches for S17E48 of Bleach, with the absolute
    number the library derives for it (414)."""
    kw.setdefault("want_title", ["Bleach"])
    kw.setdefault("want_season", 17)
    kw.setdefault("want_episode", 48)
    kw.setdefault("want_absolute", 414)
    return evaluate_release(parse_release(name), default_profile(), scope="episode", **kw)


# ── the helper's two signals ──────────────────────────────────────────────────
def test_the_arc_numbers_the_episode_its_season_does():
    assert arc_episode_match(SUBSPLEASE, ["Bleach"], 48) is True
    assert arc_episode_match(ERAI, ["Bleach"], 48) is True


def test_the_arcs_number_is_read_off_the_name():
    assert arc_episode_number(SUBSPLEASE, ["Bleach"]) == 48
    assert arc_episode_number(OLD_RUN, ["Bleach"]) is None       # no arc name to extend
    assert arc_episode_number(SCENE, ["Bleach"]) is None         # SxxExx, not arc numbering


def test_another_episode_of_the_arc_is_not_this_one():
    assert arc_episode_match(SUBSPLEASE, ["Bleach"], 47) is False


def test_the_shows_own_absolute_numbering_is_not_the_arcs():
    """'Bleach - 48' is the 2004 run's episode 48, not the arc's 48th."""
    assert arc_episode_match(OLD_RUN, ["Bleach"], 48) is False


def test_a_number_that_is_part_of_the_name_is_not_an_episode_number():
    assert arc_episode_match("Bleach - Sennen Kessen Hen Part 2 [1080p]", ["Bleach"], 2) is False


def test_another_shows_release_is_never_matched():
    assert arc_episode_match(SUBSPLEASE, ["One Piece"], 48) is False


def test_nothing_to_extend_and_no_episode_are_both_misses():
    assert arc_episode_match(SUBSPLEASE, [], 48) is False
    assert arc_episode_match(SUBSPLEASE, ["Bleach"], None) is False
    assert arc_episode_match("", ["Bleach"], 48) is False


# ── the verdict discovery actually reaches ────────────────────────────────────
def test_the_arcs_release_is_accepted_though_the_absolute_number_misses():
    assert _verdict(SUBSPLEASE)["accepted"] is True
    assert _verdict(ERAI)["accepted"] is True


def test_the_2004_runs_episode_48_stays_rejected():
    assert _verdict(OLD_RUN)["accepted"] is False


def test_a_scene_release_still_matches_by_sxxexx():
    assert _verdict(SCENE)["accepted"] is True


def test_the_wrong_episode_is_still_wrong():
    assert _verdict("BLEACH S17E47 1080p DSNP WEB-DL H 264-NORViNE")["accepted"] is False


def test_another_episode_of_the_arc_is_rejected_as_the_wrong_episode():
    """The right show, the wrong number. Called a wrong title, every episode of
    an arc greys out in the picker as though none of them were the show."""
    v = _verdict("[SubsPlease] Bleach - Sennen Kessen Hen - 49 (1080p).mkv")
    assert v["accepted"] is False
    assert "Wrong episode" in str(v["rejected"])


def test_a_pack_is_never_one_episode_of_the_arc():
    """A pack carrying the arc's name and a number is still a pack."""
    parsed = dict(parse_release(SUBSPLEASE), is_season_pack=True)
    v = evaluate_release(parsed, default_profile(), scope="episode", want_season=17,
                         want_episode=48, want_title=["Bleach"], want_absolute=414)
    assert v["accepted"] is False


def test_plain_absolute_numbering_is_unchanged():
    """A show whose releases DO carry its absolute number still matches on it."""
    v = evaluate_release(parse_release("[SubsPlease] One Piece - 1071 (1080p) [ABCD1234].mkv"),
                         default_profile(), scope="episode", want_season=21, want_episode=1071,
                         want_title=["One Piece"], want_absolute=1071)
    assert v["accepted"] is True


def test_a_different_show_entirely_is_still_the_wrong_title():
    v = _verdict("[SubsPlease] Naruto Shippuuden - 48 (1080p).mkv")
    assert v["accepted"] is False
    assert "Wrong title" in str(v["rejected"])
