"""Radarr/Sonarr-parity title gate for the video downloader.

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


# ── extraction ────────────────────────────────────────────────────────────────
def test_extract_title_cuts_at_release_year():
    assert extract_title("The.Cloverfield.Paradox.2018.1080p.WEBRip.x265-PS") == "The Cloverfield Paradox"
    assert extract_title("Paradox.2017.1080p.BluRay.x264-GROUP") == "Paradox"
    assert extract_title("Spider-Man.No.Way.Home.2021.2160p.UHD.BluRay") == "Spider Man No Way Home"


def test_extract_title_keeps_a_year_that_is_part_of_the_title():
    # the LAST year is the release year; 2049 stays in the title
    assert extract_title("Blade.Runner.2049.2017.1080p.BluRay.x264") == "Blade Runner 2049"


def test_extract_title_falls_back_to_quality_token_when_no_release_year():
    assert extract_title("Paradox.1080p.WEB-DL.x264") == "Paradox"
    assert extract_title("The.Wire.S02.1080p.BluRay.x265") == "The Wire"


def test_extract_title_recovers_a_numeric_title_via_the_quality_boundary():
    # '2012' has no separate release year, but cutting at the quality token still isolates it
    assert extract_title("2012.1080p.BluRay") == "2012"
    assert extract_title("") == ""


# ── normalization ─────────────────────────────────────────────────────────────
def test_normalize_folds_articles_punctuation_accents_and_ampersand():
    assert normalize_title("The Dark Knight") == "dark knight"
    assert normalize_title("dark.knight") == "dark knight"
    assert normalize_title("Fast & Furious") == "fast and furious"
    assert normalize_title("Amélie") == "amelie"
    assert normalize_title("Mission: Impossible") == "mission impossible"


# ── the match ─────────────────────────────────────────────────────────────────
def test_the_reported_bug_cloverfield_paradox_is_rejected_for_paradox():
    assert titles_match("The.Cloverfield.Paradox.2018.1080p.WEBRip.x265-PS", "Paradox") is False


def test_exact_and_separator_variants_match():
    assert titles_match("Paradox.2017.1080p.BluRay.x264", "Paradox") is True
    assert titles_match("The.Dark.Knight.2008.1080p.BluRay", "The Dark Knight") is True
    assert titles_match("Spider-Man.No.Way.Home.2021.2160p", "Spider-Man: No Way Home") is True


def test_trailing_edition_words_are_tolerated_but_extra_real_words_are_not():
    assert titles_match("Paradox.Extended.2017.1080p", "Paradox") is True     # edition of same film
    assert titles_match("The.Paradox.Effect.2023.1080p", "Paradox") is False  # a different film


def test_sequels_and_numbers_do_not_collapse():
    assert titles_match("Moana.2.2024.1080p.WEBRip", "Moana 2") is True
    assert titles_match("Moana.2016.1080p.BluRay", "Moana 2") is False   # the original, not the sequel


def test_numeric_or_unknown_title_passes_so_it_is_never_falsely_rejected():
    # can't isolate a numeric title → don't block; the YEAR gate still guards it
    assert titles_match("2012.1080p.BluRay.x264", "2012") is True
    assert titles_match("anything", None) is True
    assert titles_match("anything", "") is True


def test_episode_release_matches_on_the_show_name():
    assert titles_match("The.Wire.S02E03.1080p.BluRay.x265", "The Wire") is True
    assert titles_match("Some.Other.Show.S02E03.1080p", "The Wire") is False


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


def test_alias_set_beats_false_negatives():
    # the 'beat Radarr' win: a release named by a KNOWN alias still matches
    # ('God Particle' is TMDB's alternative title for 'The Cloverfield Paradox').
    aliases = ["The Cloverfield Paradox", "God Particle"]
    assert titles_match("God.Particle.2018.1080p.WEBRip.x264", aliases) is True
    assert titles_match("The.Cloverfield.Paradox.2018.1080p", aliases) is True
    # an unrelated film is still rejected against the WHOLE alias set
    assert titles_match("Paradox.2017.1080p.BluRay", aliases) is False
    # a single string still works (back-compat)
    assert titles_match("Paradox.2017.1080p", "Paradox") is True


def test_evaluate_release_accepts_a_matching_alias_end_to_end():
    parsed = parse_release("God.Particle.2018.1080p.WEBRip.x264")
    v = evaluate_release(parsed, _PROFILE, scope="movie", want_year=2018,
                         want_title=["The Cloverfield Paradox", "God Particle"])
    assert v["accepted"] is True


# ── alias plumbing (fetch → cache → search context) ───────────────────────────
def test_engine_alt_titles_for_caches_and_is_best_effort():
    from core.video.enrichment.engine import VideoEnrichmentEngine
    from core.video.enrichment.cache import TTLCache
    calls = {"n": 0}

    class _Client:
        def alternative_titles(self, kind, tmdb_id):
            calls["n"] += 1
            return ["God Particle"]

    eng = VideoEnrichmentEngine.__new__(VideoEnrichmentEngine)
    eng._cache = TTLCache(maxsize=16, ttl=60)
    eng.workers = {"tmdb": type("W", (), {"enabled": True, "client": _Client()})()}
    assert eng.alt_titles_for("movie", 42) == ["God Particle"]
    assert eng.alt_titles_for("movie", 42) == ["God Particle"]   # served from cache
    assert calls["n"] == 1                                        # only one TMDB hit
    assert eng.alt_titles_for("movie", None) == []               # no id → no call


def test_search_context_carries_the_alias_set(monkeypatch):
    import core.automation.handlers.video_process_wishlist as mod

    class _Eng:
        def alt_titles_for(self, kind, tmdb_id):
            return ["God Particle"] if str(tmdb_id) == "42" else []

    monkeypatch.setattr(
        "core.video.enrichment.engine.get_video_enrichment_engine", lambda: _Eng())
    ctx = mod.search_context({"title": "The Cloverfield Paradox", "year": 2018, "tmdb_id": 42}, "movie")
    assert ctx["title"] == "The Cloverfield Paradox"
    assert ctx["titles"] == ["The Cloverfield Paradox", "God Particle"]
    # no aliases → ctx keeps its old shape (no 'titles' key)
    assert "titles" not in mod.search_context({"title": "Paradox", "year": 2017, "tmdb_id": 99}, "movie")


# ── evaluate_release integration (the actual gate the downloader uses) ─────────
def test_evaluate_release_rejects_the_wrong_film_end_to_end():
    parsed = parse_release("The.Cloverfield.Paradox.2018.1080p.WEBRip.x265-PS")
    v = evaluate_release(parsed, _PROFILE, scope="movie", want_year=2017, want_title="Paradox")
    assert v["accepted"] is False
    assert "Wrong title" in (v.get("rejected") or "")


def test_evaluate_release_accepts_the_right_film():
    parsed = parse_release("Paradox.2017.1080p.BluRay.x264-GROUP")
    v = evaluate_release(parsed, _PROFILE, scope="movie", want_year=2017, want_title="Paradox")
    assert v["accepted"] is True


def test_evaluate_release_without_want_title_keeps_old_behavior():
    # back-compat: no wanted title supplied → the title gate is skipped (year still applies)
    parsed = parse_release("The.Cloverfield.Paradox.2018.1080p.WEBRip.x265-PS")
    v = evaluate_release(parsed, _PROFILE, scope="movie", want_year=2017)
    assert v["accepted"] is True     # only the year gate ran (2018 within 2017..2018)


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
    orphaned '[' used to reach the user-facing 'Wrong title (...)' text."""
    assert extract_title("[SubsPlease] Foo - 40 [Web][1080p]") == "SubsPlease Foo 40"
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
    from core.video.release_parse import fansub_absolute_episode
    assert fansub_absolute_episode("[SubsPlease] DIGIMON BEATBREAK - 40 [1080p][AAC]") == 40
    assert fansub_absolute_episode("[Erai-raws] Some Anime - 1071 [1080p]") == 1071
    # no leading group tag → not the fansub convention, whatever the trailing dash
    assert fansub_absolute_episode("The.Matrix.1999.1080p.BluRay.x265-GRP") is None
    assert fansub_absolute_episode("Moana 2 2024 1080p WEBRip") is None
    assert fansub_absolute_episode("") is None
