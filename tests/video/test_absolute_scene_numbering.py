"""An indexer's SxxExx that carries the show's ABSOLUTE number.

Anime trackers synthesise a SxxExx from the series entry's own continuous
numbering, so Re:ZERO S4E16 arrives as '[SubsPlease] Re:ZERO -Starting Life in
Another World- Season 4 S04E82 - 82' — 82 being the 66 episodes before season 4
plus 16. The season is right and only the counter differs.

Because a SxxExx IS present, the absolute-number escape hatch never applied: it
lives in the branch for releases the parser finds no episode number on at all.
So the search turned away the very episode that was asked for, and a copy
grabbed by hand anyway was turned away a second time at import —
"Release is S04E81, not the episode requested".
"""

from __future__ import annotations

import json

from core.video import importer
from core.video.quality_eval import evaluate_release
from core.video.release_parse import parse_release

ANIMEBYTES = ("[SubsPlease] Re:ZERO -Starting Life in Another World- Season 4 S04E82 - 82 "
              "[Web][MKV][h264][1080p][AAC 2.0][Softsubs (SubsPlease)][Episode 82]")
SCENE = "Re ZERO Starting Life in Another World S04E16 1080p WEB H264-SENPAI"
_PROFILE = {"tiers": [{"key": k, "enabled": True}
                      for k in ("web-1080p", "webdl-1080p", "bluray-1080p")]}


def _verdict(name, **kw):
    """The verdict for Re:ZERO S4E16, whose absolute number the library puts at 82."""
    kw.setdefault("want_title", ["Re:ZERO -Starting Life in Another World-"])
    kw.setdefault("want_season", 4)
    kw.setdefault("want_episode", 16)
    kw.setdefault("want_absolute", 82)
    return evaluate_release(parse_release(name), _PROFILE, scope="episode", **kw)


# ── what the release says about itself ───────────────────────────────────────
def test_the_release_numbers_itself_by_the_absolute():
    p = parse_release(ANIMEBYTES)
    assert (p["season"], p["episode"]) == (4, 82)


# ── the search gate ──────────────────────────────────────────────────────────
def test_the_search_accepts_it_once_the_absolute_agrees():
    """The reported failure, the right way round."""
    assert _verdict(ANIMEBYTES)["accepted"] is True


def test_without_a_known_absolute_it_stays_the_wrong_episode():
    """Nothing is guessed. With no absolute number for the episode — a show the
    library doesn't hold past season 1 — 82 is just a number that isn't 16."""
    v = _verdict(ANIMEBYTES, want_absolute=None)
    assert v["accepted"] is False and v["rejected"] == "Wrong episode"


def test_a_number_that_is_not_the_absolute_is_still_refused():
    """The next episode's release, while episode 16 is what was asked for."""
    v = _verdict(ANIMEBYTES, want_absolute=83)
    assert v["accepted"] is False and v["rejected"] == "Wrong episode"


def test_the_season_still_has_to_agree():
    """Season numbering is the one thing both sides do state plainly, so an
    absolute that matches cannot talk a different season's release through."""
    v = _verdict(ANIMEBYTES.replace("S04E82", "S03E82"), want_absolute=82)
    assert v["accepted"] is False and v["rejected"] == "Wrong season"


def test_the_boundary_this_rule_cannot_see():
    """The honest limit, pinned so it is visible rather than surprising.

    Both signals are satisfied by a release that really IS its season's episode
    N when N happens to equal the absolute number of the episode wanted — a
    25-episode first season followed by a 50-episode second, wanting S02E05
    (absolute 30), against a genuine S02E30. Nothing in either name can tell
    those apart; Sonarr needs a scene-mapping table for it. The shape is rare
    (the season has to be longer than everything before it plus the episode),
    and it costs a wrong file rather than a wrong SHOW."""
    v = _verdict("Some Anime S02E30 1080p WEB", want_season=2, want_episode=5,
                 want_absolute=30, want_title=["Some Anime"])
    assert v["accepted"] is True


def test_ordinary_numbering_is_untouched():
    assert _verdict(SCENE)["accepted"] is True
    wrong = _verdict(SCENE.replace("S04E16", "S04E17"))
    assert wrong["accepted"] is False and wrong["rejected"] == "Wrong episode"


# ── the import gate ──────────────────────────────────────────────────────────
def _dl(release, *, season=4, episode=16, absolute=None):
    ctx = {"scope": "episode", "title": "Re:ZERO -Starting Life in Another World-",
           "season": season, "episode": episode}
    if absolute is not None:
        ctx["absolute"] = absolute
    return {"kind": "show", "title": "Re:ZERO -Starting Life in Another World-",
            "source": "torrent", "release_title": release, "size_bytes": 1_400_000_000,
            "target_dir": "/lib/anime", "search_ctx": json.dumps(ctx)}


def test_the_import_files_an_absolute_numbered_release():
    p = importer.plan_import(_dl(ANIMEBYTES, absolute=82),
                             "/dl/x/rezero.s04e82.mkv", list_dir=lambda d: [])
    assert p["action"] != "reject", p.get("reason")
    # filed as the episode the library knows, not as the number on the release
    assert "S04E16" in p["dest"]["filename"]


def test_the_import_refuses_it_without_the_absolute():
    """What the user's three grabs hit: the search context a picker grab wrote
    carried no absolute number, so the file arrived looking like episode 82."""
    p = importer.plan_import(_dl(ANIMEBYTES), "/dl/x/rezero.s04e82.mkv", list_dir=lambda d: [])
    assert p["action"] == "reject"
    assert p["reason"] == "Release is S04E82, not the episode requested"


def test_the_import_still_refuses_a_genuinely_different_episode():
    """The absolute is an exact key, not a licence to accept any number."""
    p = importer.plan_import(_dl(ANIMEBYTES, absolute=83),
                             "/dl/x/rezero.s04e82.mkv", list_dir=lambda d: [])
    assert p["action"] == "reject"
    assert p["reason"] == "Release is S04E82, not the episode requested"


def test_the_import_keeps_the_season_check():
    """An absolute that agrees is not a licence to file another season's release."""
    p = importer.plan_import(_dl(ANIMEBYTES.replace("S04E82", "S03E82"), absolute=82),
                             "/dl/x/rezero.s03e82.mkv", list_dir=lambda d: [])
    assert p["action"] == "reject"
    assert p["reason"] == "Release is S03E82, not the episode requested"


def test_the_context_hands_the_absolute_to_the_importer():
    """``_ctx`` is what plan_import judges against AND what fills the {absolute}
    naming token; it never copied the number across, so both read None."""
    assert importer._ctx(_dl(ANIMEBYTES, absolute=82))["absolute"] == 82
    assert importer._ctx(_dl(ANIMEBYTES))["absolute"] is None
