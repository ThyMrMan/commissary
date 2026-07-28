"""Pick the provider whose season structure matches the media server.

Episodes are keyed UNIQUE(show_id, season_number, episode_number) and those
numbers come from the server. A provider that splits the show differently can
only write rows into seasons they don't belong to.

Bleach, from the diagnostic: Plex reports 17 seasons. TVDB has 17. TMDB has
three — specials, the 366-episode original run, and Thousand-Year Blood War. The
damage correlated exactly with TMDB's season list: seasons 0, 1 and 2 carried
extra rows, seasons 3-16 were pristine, and season 17 could never fill because
TMDB has no season 17 to cascade.
"""

from __future__ import annotations

import pytest

from core.video.episode_numbering import (DEFAULT_SOURCE, choose_source, explain,
                                          structure_score)

# The real shapes, from the diagnostic and the user's reading of both sites.
PLEX_BLEACH = list(range(1, 18))            # 17 seasons
TVDB_BLEACH = [0] + list(range(1, 18))      # matches
TMDB_BLEACH = [0, 1, 2]                     # specials + original run + TYBW


# ── the reported case ────────────────────────────────────────────────────────
def test_bleach_picks_tvdb(db_unused=None):
    assert choose_source(PLEX_BLEACH, TMDB_BLEACH, TVDB_BLEACH) == "tvdb"


def test_the_scores_show_why(db_unused=None):
    assert structure_score(PLEX_BLEACH, TVDB_BLEACH) == 1.0
    assert structure_score(PLEX_BLEACH, TMDB_BLEACH) == pytest.approx(2 / 17)


def test_it_names_the_seasons_tmdb_cannot_serve():
    """Seasons 3-17 are exactly the ones with no extra rows in the diagnostic —
    and season 17, where the library keeps TYBW, is exactly what never filled."""
    e = explain(PLEX_BLEACH, TMDB_BLEACH, TVDB_BLEACH)
    assert e["source"] == "tvdb"
    assert e["missing_from_tmdb"] == list(range(3, 18))
    assert e["missing_from_tvdb"] == []


# ── the ordinary show must not move ──────────────────────────────────────────
def test_a_show_both_providers_agree_on_keeps_tmdb():
    assert choose_source([1, 2, 3], [0, 1, 2, 3], [0, 1, 2, 3]) == DEFAULT_SOURCE == "tmdb"


def test_a_show_with_no_tvdb_data_keeps_tmdb():
    assert choose_source([1, 2, 3], [1, 2, 3], []) == "tmdb"
    assert choose_source([1, 2, 3], [1, 2, 3], None) == "tmdb"


def test_a_single_missing_special_does_not_flip_the_source():
    """Season 0 is a dumping ground that differs between providers for reasons
    that say nothing about how the real seasons are numbered."""
    assert choose_source([1, 2, 3], [1, 2, 3], [0, 1, 2, 3]) == "tmdb"


def test_a_one_season_difference_is_not_decisive():
    """The rule targets wholesale disagreement (3 vs 17), not a provider being
    a bit behind on the newest season."""
    assert choose_source(list(range(1, 11)), list(range(1, 10)),
                         list(range(1, 11))) == "tmdb"


def test_tmdb_wins_when_it_is_the_one_that_matches():
    """Symmetric — nothing here is TVDB-favouring, it's structure-favouring."""
    assert choose_source(list(range(1, 18)), list(range(1, 18)), [1, 2]) == "tmdb"


# ── the override is obeyed, including when it disagrees ──────────────────────
def test_an_override_wins_over_the_heuristic():
    assert choose_source(PLEX_BLEACH, TMDB_BLEACH, TVDB_BLEACH, override="tmdb") == "tmdb"
    assert choose_source([1, 2, 3], [1, 2, 3], [1, 2, 3], override="tvdb") == "tvdb"


def test_auto_and_junk_overrides_fall_through_to_detection():
    for bad in ("auto", None, "", "netflix", 7):
        assert choose_source(PLEX_BLEACH, TMDB_BLEACH, TVDB_BLEACH, override=bad) == "tvdb"


def test_explain_reports_the_override_it_used():
    assert explain(PLEX_BLEACH, TMDB_BLEACH, TVDB_BLEACH, override="tmdb")["override"] == "tmdb"
    assert explain(PLEX_BLEACH, TMDB_BLEACH, TVDB_BLEACH)["override"] is None


# ── degenerate input ─────────────────────────────────────────────────────────
def test_a_show_with_no_server_seasons_scores_zero_and_keeps_the_default():
    assert structure_score([], [1, 2]) == 0.0
    assert choose_source([], [1, 2], [1, 2, 3]) == DEFAULT_SOURCE


def test_junk_season_numbers_are_ignored_rather_than_crashing():
    assert structure_score([1, 2, None, "x"], [1, 2]) == 1.0
    assert choose_source(["1", 2], [1, 2], [1, 2]) == DEFAULT_SOURCE


def test_specials_alone_never_decide():
    assert structure_score([0], [0]) == 0.0
