"""The "Wrong title" reason has to name the title the matcher actually compared.

Reported: ✗ WRONG TITLE (SUBSPLEASE RE:ZERO — WANTED RE:ZERO -STARTING LIFE IN
ANOTHER WORLD-), and "for some reason it is grabbing the group name as a part of
the name". It was: the reason ran ``extract_title`` over the RAW release name,
tag and all, while ``titles_match`` had already stripped that leading group tag
before it compared anything. So the message reported a title nothing was ever
judged against — and it reads as an instruction to add the group name to the
show's AKA titles, which cannot help, because the tag never reaches the
comparison in the first place.

The other half is the wanted side: it is a whole alias set (the primary title
plus every TMDB alternative title), and joining thirty names in a dozen scripts
buried the one the user would recognise.
"""

from __future__ import annotations

from core.video.quality_eval import _wanted_text, evaluate_release
from core.video.release_parse import compared_title, parse_release

ANIMEBYTES = ("[SubsPlease] Re:ZERO Season 4 S04E82 - 82 "
              "[Web][MKV][h264][1080p][AAC 2.0][Softsubs (SubsPlease)][Episode 82]")
_PROFILE = {"tiers": [{"key": k, "enabled": True}
                      for k in ("web-1080p", "webdl-1080p", "bluray-1080p")]}


# ── the title the matcher compared ───────────────────────────────────────────
def test_the_group_tag_is_not_part_of_the_title():
    assert compared_title(ANIMEBYTES) == "Re:ZERO"


def test_a_name_without_a_group_tag_is_unchanged():
    assert compared_title("Re ZERO Starting Life in Another World S04E16 1080p WEB") \
        == "Re ZERO Starting Life in Another World"


def test_only_a_LEADING_tag_is_dropped():
    """A bracketed tag anywhere else is part of the name as written."""
    assert compared_title("Some Show [Uncensored] 1080p WEB") == "Some Show Uncensored"


def test_an_unreadable_name_still_answers():
    assert compared_title("") == ""
    assert compared_title(None) == ""


# ── the reason a user reads ──────────────────────────────────────────────────
def _reason(want_title):
    v = evaluate_release(parse_release(ANIMEBYTES), _PROFILE, scope="episode",
                         want_season=4, want_episode=16, want_title=want_title)
    assert v["accepted"] is False
    return v["rejected"]


def test_the_reason_names_the_title_that_was_compared():
    r = _reason("Re:ZERO -Starting Life in Another World-")
    assert r == "Wrong title (Re:ZERO — wanted Re:ZERO -Starting Life in Another World-)"
    assert "SubsPlease" not in r


# Aliases none of which this release carries — the point here is how the wanted
# side READS, so every one of them has to actually miss.
_KARA = "Re Zero kara Hajimeru Isekai Seikatsu"


def test_a_few_aliases_are_spelled_out():
    """Seeing which alias was tried often answers the question by itself."""
    assert _reason(["Re:ZERO -Starting Life in Another World-", _KARA]).endswith(
        "wanted Re:ZERO -Starting Life in Another World- / %s)" % _KARA)


def test_a_long_alias_set_stops_and_counts():
    """A well-travelled show's set is thirty names in a dozen scripts; all of
    them joined buries the one the user would recognise."""
    r = _reason(["Re:ZERO -Starting Life in Another World-", _KARA, "ReZero kara Hajimeru",
                 "リゼロ", "Re Zero Empezar de cero"])
    assert r.endswith("wanted Re:ZERO -Starting Life in Another World- / %s "
                      "/ ReZero kara Hajimeru (+2 more))" % _KARA)


def test_the_wanted_side_never_leaks_a_list_repr():
    assert "[" not in _reason(["Re:ZERO -Starting Life in Another World-", "Other"])


def test_the_edges_of_the_wanted_text():
    assert _wanted_text(["A", "B"]) == "A / B"
    assert _wanted_text(["A", "B", "C"]) == "A / B / C"          # exactly the limit
    assert _wanted_text(["A", "B", "C", "D"]) == "A / B / C (+1 more)"
    assert _wanted_text(["A"]) == "A"
    assert _wanted_text("A") == "A"
    assert _wanted_text([]) == "?"
