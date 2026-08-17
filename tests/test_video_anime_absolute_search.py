"""Anime searches missed the show because the tracker numbers episodes, not SxxExx.

Reported against a real release, on both the automatic and the manual path:

    [SubsPlease] Oh Boy, Was I Wrong About Her - 07 [Web][MKV][h264][1080p]…
    ✗ NO EPISODE NUMBER IN THE RELEASE NAME

The matcher was never the problem. ``has_absolute_episode`` handles that exact
name, and ``_scope_ok`` accepts on it — but only when it is handed the wanted
ABSOLUTE number. Everything here is about that hint failing to arrive, which it
did in three independent ways. The release is a single evaluation away from
being accepted the whole time, which is why it looked like a parser bug.

1. ``_episode_hints`` bailed out on a missing/unresolvable tmdb id BEFORE its
   season-1 fallback — a fallback that needs only the season and episode
   numbers and never touches the database. So the one derivation that works
   without a library was gated behind a library lookup.

2. The Soulseek POLL query string carries no ``media_id``. Combined with (1)
   that meant a search which STARTED with hints delivered all of its actual
   results without them: /search/start was fixed for fansub naming, and the
   endpoint that streams the results back was not.

3. ``search_context`` (the unattended drain) asked for the absolute number only
   when the show was already tagged ``anime``, and only from the library's
   episode list. A brand-new anime has no shows row, so it is neither tagged nor
   countable — making the very first episode of a new show, the grab that decides
   which Library it lives in forever, the one grab that could never match.

The season-1 restriction is load-bearing in all three: the hint only ever
ACCEPTS, so deriving an absolute number for a later season would let a bare
'Show - 05' satisfy a request for S02E05 by handing over season one's episode 5.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from core.video.quality_eval import evaluate_release
from core.video.release_parse import has_absolute_episode, parse_release
from database.video_database import VideoDatabase

_ROOT = Path(__file__).resolve().parent.parent
_DOWNLOAD_VIEW_JS = _ROOT / "webui" / "static" / "video" / "video-download-view.js"
_SIDE_CSS = _ROOT / "webui" / "static" / "video" / "video-side.css"

# The reported release, verbatim.
_REPORTED = ("[SubsPlease] Oh Boy, Was I Wrong About Her - 07 [Web][MKV][h264]"
             "[1080p][AAC 2.0][Softsubs (SubsPlease)]")
_TITLE = "Oh Boy, Was I Wrong About Her"
_PROFILE = {"tiers": [{"key": k, "enabled": True}
                      for k in ("web-1080p", "webdl-1080p", "bluray-1080p")]}


@pytest.fixture
def db(tmp_path):
    import database.video_database as mod
    if hasattr(mod, "_initialized_paths"):
        mod._initialized_paths.clear()
    return VideoDatabase(str(tmp_path / "v.db"))


def _judge(want_episode=7, **kw):
    return evaluate_release(parse_release(_REPORTED), _PROFILE, scope="episode",
                            want_season=1, want_episode=want_episode,
                            want_title=_TITLE, **kw)


# ── the diagnosis: the matcher was always fine ──────────────────────────────

def test_the_name_carries_no_parsable_episode_number():
    """Deliberate. ' - 07' is not read as an episode number, because in a title
    it is ambiguous. That is why the absolute hint exists at all."""
    p = parse_release(_REPORTED)
    assert p["season"] is None and p["episode"] is None


def test_the_matcher_recognises_the_number_perfectly_well():
    assert has_absolute_episode(_REPORTED, 7) is True
    assert has_absolute_episode(_REPORTED, 8) is False


def test_the_hint_alone_decides_the_outcome():
    """The exact before/after the user saw — same release, same profile, same
    wanted episode. Only the hint differs."""
    assert "No episode number" in _judge(want_absolute=None)["rejected"]
    assert _judge(want_absolute=7)["accepted"] is True


def test_a_different_episode_is_still_refused():
    """The hint must not become a way to accept any numbered release."""
    assert _judge(want_episode=9, want_absolute=9)["accepted"] is False


# ── (1) the fallback was gated behind a lookup it never used ────────────────

class TestEpisodeHints:
    def test_season_one_needs_no_tmdb_id_at_all(self, db):
        from api.video.downloads import _episode_hints
        assert _episode_hints(db, {"scope": "episode"}, 1, 7) == (None, 7)

    def test_a_later_season_still_refuses_to_guess(self, db):
        """Deriving here would hand over season one's episode 7."""
        from api.video.downloads import _episode_hints
        assert _episode_hints(db, {"scope": "episode"}, 2, 7) == (None, None)

    def test_an_unresolvable_id_no_longer_costs_the_fallback(self, db):
        """A library row id for a show that isn't in the library resolves to
        nothing — which used to discard the season-1 derivation with it."""
        from api.video.downloads import _episode_hints
        got = _episode_hints(db, {"scope": "episode", "media_id": 999999,
                                  "media_source": "library"}, 1, 7)
        assert got == (None, 7)

    def test_it_stays_scoped_to_episode_searches(self, db):
        from api.video.downloads import _episode_hints
        assert _episode_hints(db, {"scope": "season"}, 1, 7) == (None, None)
        assert _episode_hints(db, {"scope": "movie"}, 1, 7) == (None, None)

    def test_end_to_end_on_the_poll_payload(self, db):
        """What the poll endpoint now receives, judged against the real release."""
        from api.video.downloads import _episode_hints
        args = {"scope": "episode", "title": _TITLE, "season": "1", "episode": "7",
                "media_id": "12345", "media_source": "tmdb"}
        want_date, want_absolute = _episode_hints(db, args, 1, 7)
        assert _judge(want_date=want_date, want_absolute=want_absolute)["accepted"] is True


# ── (2) the poll endpoint never received the identity ───────────────────────

def test_the_poll_query_string_carries_the_identity():
    """Soulseek results arrive through the POLL, not the start call. Without
    these the start call's hints are irrelevant — every result that actually
    reaches the user is judged without them."""
    js = _DOWNLOAD_VIEW_JS.read_text(encoding="utf-8")
    qs = js.split("function _pollSearch", 1)[1].split("getJSON(", 1)[0]
    assert "media_id=" in qs, "the poll drops media_id — hints can never resolve"
    assert "media_source=" in qs, "media_id without media_source is not resolvable"


def test_the_poll_endpoint_still_asks_for_hints():
    src = (_ROOT / "api" / "video" / "downloads.py").read_text(encoding="utf-8")
    poll = src.split("def video_downloads_search_poll", 1)[1][:1600]
    assert "_episode_hints(" in poll
    assert "want_absolute=_poll_hints[1]" in poll


# ── (3) the unattended drain, on a show nothing has typed yet ───────────────

class TestSearchContext:
    def _item(self, **kw):
        base = {"show_title": _TITLE, "show_tmdb_id": 999, "season_number": 1,
                "episode_number": 7, "air_date": "2026-08-15"}
        return {**base, **kw}

    def test_a_brand_new_anime_gets_the_number_without_a_library(self):
        """No shows row, no episode rows, nothing tagged — the state every show
        is in for its FIRST download, which is the one that matters most."""
        from core.automation.handlers.video_process_wishlist import search_context
        ctx = search_context(self._item(series_type="anime"), "episode")
        assert ctx["absolute"] == 7

    def test_an_untyped_show_gets_it_too(self):
        """The tag lives on the shows row, and a new show has no shows row — so
        requiring the tag meant requiring the thing that cannot exist yet."""
        from core.automation.handlers.video_process_wishlist import search_context
        assert search_context(self._item(), "episode")["absolute"] == 7

    @pytest.mark.parametrize("stype", ["standard", "daily"])
    def test_explicitly_typed_non_anime_shows_are_left_alone(self, stype):
        """Deliberately not widened to everything. For a standard show 'Show - 04'
        much more likely means season one's episode 4, and the hint only accepts."""
        from core.automation.handlers.video_process_wishlist import search_context
        assert "absolute" not in search_context(self._item(series_type=stype), "episode")

    def test_a_later_season_is_not_guessed(self):
        from core.automation.handlers.video_process_wishlist import search_context
        ctx = search_context(self._item(season_number=2, series_type="anime"), "episode")
        assert "absolute" not in ctx

    def test_the_library_answer_wins_when_there_is_one(self, db):
        """A real episode list is authoritative — season 2 episode 1 of a show
        with three episodes in season 1 is absolute 4, which no fallback could
        derive. The season-0 special must not count toward it."""
        import api.video as videoapi
        from core.automation.handlers.video_process_wishlist import search_context
        seasons = [{"season_number": s, "episodes": [
            {"episode_number": e, "title": "E%d" % e} for e in range(1, n + 1)]}
            for s, n in ((1, 3), (2, 1))]
        seasons.append({"season_number": 0,
                        "episodes": [{"episode_number": 1, "title": "Special"}]})
        db.upsert_show_tree("plex", {"server_id": "s999", "title": _TITLE,
                                     "tmdb_id": 999, "seasons": seasons})
        videoapi._video_db = db
        try:
            ctx = search_context(
                self._item(season_number=2, episode_number=1, series_type="anime"), "episode")
            assert ctx["absolute"] == 4
        finally:
            videoapi._video_db = None

    def test_an_untyped_show_does_not_get_the_library_lookup(self, db):
        """'Untyped' is not 'anime'. Season 1 is served because absolute ≡ episode
        is true of EVERY show — safe by construction. A later season's absolute
        number is only knowable from the episode list, and trusting that for a
        show nobody has called anime is how a standard show wanting S02E01 ends
        up accepting 'Show - 04' (season one's episode 4). Anime still gets it."""
        import api.video as videoapi
        from core.automation.handlers.video_process_wishlist import search_context
        seasons = [{"season_number": s, "episodes": [
            {"episode_number": e, "title": "E%d" % e} for e in range(1, n + 1)]}
            for s, n in ((1, 3), (2, 1))]
        db.upsert_show_tree("plex", {"server_id": "s999", "title": _TITLE,
                                     "tmdb_id": 999, "seasons": seasons})
        videoapi._video_db = db
        try:
            item = self._item(season_number=2, episode_number=1)
            assert "absolute" not in search_context(item, "episode")
            assert search_context({**item, "series_type": "anime"}, "episode")["absolute"] == 4
        finally:
            videoapi._video_db = None

    def test_season_packs_and_movies_carry_no_episode_number(self):
        """Both identify something other than one episode; an absolute number on
        either would make it fail its own scope gate."""
        from core.automation.handlers.video_process_wishlist import search_context
        assert "absolute" not in search_context(
            self._item(_season_pack=True, series_type="anime"), "episode")
        assert "absolute" not in search_context(
            {"title": "A Film", "tmdb_id": 42, "year": 2026}, "movie")


# ── the half the user could see: the name was unreadable ────────────────────

def test_the_full_release_name_is_available_on_hover():
    """Reported alongside the matching bug: "unable to show full name because it
    gets cut off". The link variant spent its tooltip on 'Open this release on
    <indexer>' — which the underline and the cursor already convey — so for
    indexer results the name was clipped on screen AND absent on hover. An anime
    release carries its episode number in the middle of a long name, so this hid
    the single field you open a manual search to check."""
    js = _DOWNLOAD_VIEW_JS.read_text(encoding="utf-8")
    card = js.split("function resultCardHTML", 1)[1].split("function ", 1)[0]
    # Scope to the <a> branch ALONE. The <span> fallback right after it has always
    # carried the name in its tooltip, so a window wide enough to reach it makes
    # this assertion pass no matter what the link does.
    link = card.split("vdl-r-title--link", 1)[1].split("</a>", 1)[0]
    assert "title=\"' + esc(r.title)" in link, \
        "the linked release name still has no tooltip carrying the name itself"
    assert "esc(r.title)" in link.split("title=", 1)[1].split("+ esc(r.username", 1)[0], \
        "the tooltip names the indexer instead of the release"


def test_the_name_is_allowed_more_than_one_line():
    css = _SIDE_CSS.read_text(encoding="utf-8")
    block = css.split(".vdl-r-title {", 1)[1].split("}", 1)[0]
    assert "line-clamp" in block, "the release name is still clamped to a single line"
    assert "nowrap" not in block
