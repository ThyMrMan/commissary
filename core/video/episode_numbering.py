"""Which metadata provider owns a show's EPISODE NUMBERING.

Episodes are keyed ``UNIQUE(show_id, season_number, episode_number)`` and those
numbers come from your media server. Backfilling missing episodes from a
provider that splits the show into different seasons therefore writes rows into
seasons they don't belong to — and no amount of care downstream can undo that,
because the numbers are the key.

Bleach is the case that forced this. TMDB has it as three seasons (specials, the
366-episode 2004-2012 run, then Thousand-Year Blood War). TVDB has seventeen,
which is what Plex reports. Cascading TMDB's season numbers wrote TMDB's
"season 2" (TYBW, 2022-2026) on top of the 2005 arc, and left season 17 — where
the library actually keeps TYBW — with nothing to fill it, because TMDB has no
season 17 at all.

So the provider is CHOSEN per show by comparing each one's season structure
against what the server reports, rather than assuming the primary is right.
Pure functions: no database, no network, no config — just the decision, so the
rule can be tested against real season lists.
"""

from __future__ import annotations

SOURCES = ("auto", "tmdb", "tvdb")

# A show whose structure genuinely can't be told apart keeps the historical
# behaviour (TMDB), so this never silently rearranges a library that was fine.
DEFAULT_SOURCE = "tmdb"

# How much better the challenger must score before displacing the default. The
# point is to catch a WHOLESALE structural disagreement (3 seasons vs 17), not
# to flip on a single missing special, so the gap has to be decisive.
MIN_MARGIN = 0.25


def _regular(seasons) -> set:
    """Season numbers, specials dropped. Season 0 is a dumping ground whose
    contents differ between providers for reasons that say nothing about how
    the show's real seasons are numbered."""
    out = set()
    for s in (seasons or []):
        try:
            n = int(s)
        except (TypeError, ValueError):
            continue
        if n > 0:
            out.add(n)
    return out


def structure_score(server_seasons, provider_seasons) -> float:
    """How well a provider's season structure matches the server's, 0.0-1.0.

    The fraction of the server's real seasons the provider also has. Asymmetric
    on purpose: a provider knowing about seasons the server hasn't got is normal
    (that IS the backfill's job — you don't own them yet). A provider MISSING
    seasons the server has is the fatal direction, because every episode of those
    seasons has nowhere correct to go.
    """
    have = _regular(server_seasons)
    if not have:
        return 0.0
    return len(have & _regular(provider_seasons)) / len(have)


def choose_source(server_seasons, tmdb_seasons, tvdb_seasons, override=None) -> str:
    """'tmdb' | 'tvdb' — which provider's numbering to cascade episodes from.

    ``override`` is the per-show setting; anything other than 'auto'/None is
    obeyed as-is, including a choice that scores badly. It exists precisely for
    when this heuristic is wrong, so second-guessing it would defeat it.
    """
    if override in ("tmdb", "tvdb"):
        return override
    tmdb, tvdb = (structure_score(server_seasons, tmdb_seasons),
                  structure_score(server_seasons, tvdb_seasons))
    # No TVDB data at all → nothing to weigh, keep the default.
    if not _regular(tvdb_seasons):
        return DEFAULT_SOURCE
    if tvdb - tmdb >= MIN_MARGIN:
        return "tvdb"
    if tmdb - tvdb >= MIN_MARGIN:
        return "tmdb"
    return DEFAULT_SOURCE


def explain(server_seasons, tmdb_seasons, tvdb_seasons, override=None) -> dict:
    """The decision plus the numbers behind it, for the Manage panel and logs —
    'why did it pick that?' should never require reading the source."""
    chosen = choose_source(server_seasons, tmdb_seasons, tvdb_seasons, override)
    return {
        "source": chosen,
        "override": override if override in ("tmdb", "tvdb") else None,
        "tmdb_score": round(structure_score(server_seasons, tmdb_seasons), 3),
        "tvdb_score": round(structure_score(server_seasons, tvdb_seasons), 3),
        "server_seasons": sorted(_regular(server_seasons)),
        "missing_from_tmdb": sorted(_regular(server_seasons) - _regular(tmdb_seasons)),
        "missing_from_tvdb": sorted(_regular(server_seasons) - _regular(tvdb_seasons)),
    }
