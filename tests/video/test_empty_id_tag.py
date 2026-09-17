"""A folder must never claim an id it hasn't got.

The Sonarr-style naming schemes write an id tag's brackets as LITERAL text
around the token -- '(tmdb-{TmdbId})' -- so unlike '{({Series Year})}' the tag
does not collapse when the id is missing: the token vanishes and '(tmdb-)' is
left in the folder name. A media server matching that folder has nothing to go
on but guesswork, which is one way a show ends up filed twice.
"""

from __future__ import annotations

from core.video import organization

# The live template, with the id tag written the way the schemes write it.
EPISODE_TMPL = ("{Series CleanTitleWithoutYear} {({Series Year})} (tmdb-{TmdbId})"
                "/Season {season:00}/"
                "{Series CleanTitleWithoutYear} - S{season:00}E{episode:00}")


def _dir(**fields):
    base = {"title": "Bleach", "year": 2004, "season": 17, "episode": 48}
    base.update(fields)
    out = organization.render_path("episode", "/media/anime", base,
                                   {"episode_template": EPISODE_TMPL}, ".mkv")
    return out["dir"].replace("\\", "/")


def test_a_missing_id_takes_its_tag_with_it():
    d = _dir()
    assert "(tmdb-)" not in d
    assert d == "/media/anime/Bleach (2004)/Season 17"


def test_an_id_that_is_there_is_kept():
    assert _dir(tmdbid=30984) == "/media/anime/Bleach (2004) (tmdb-30984)/Season 17"


def test_every_empty_tag_shape_is_dropped():
    assert organization._tidy_component("Bleach (2004) (tmdb-)") == "Bleach (2004)"
    assert organization._tidy_component("Bleach [tvdb-]") == "Bleach"
    assert organization._tidy_component("Bleach {imdb-}") == "Bleach"
    assert organization._tidy_component("Bleach (tmdbid- )") == "Bleach"
    assert organization._tidy_component("Bleach (TVDB-)") == "Bleach"


def test_a_tag_with_an_id_in_it_is_never_touched():
    assert organization._tidy_component("Bleach (tmdb-30984)") == "Bleach (tmdb-30984)"
    assert organization._tidy_component("Bleach [tvdb-74796]") == "Bleach [tvdb-74796]"
    # A show whose NAME ends that way is not an id tag at all.
    assert organization._tidy_component("Tokyo (imdb-tt0434665)") == "Tokyo (imdb-tt0434665)"
