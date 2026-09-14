"""Prefer deluxe editions — the ``wishlist.prefer_deluxe_editions`` option (default off).

Many albums exist as a standard edition and a bigger one: "(Deluxe Edition)",
"(Expanded)", "(25th Anniversary Edition)", a super deluxe box. Commissary treats
those as the SAME album in several places on purpose — right for "do I already
have this album?", wrong for someone who wants the bigger edition:

- owning the standard edition counted as owning every song the deluxe shares with
  it, so downloading the deluxe fetched only its bonus tracks, into a folder of
  their own, and the album ended up split across two folders;
- the artist page merged same-year editions into the plain-titled card;
- the watchlist wishlisted both editions of one album;
- Album Consistency tagged deluxe files with the standard release's title.

With the option on, each of those prefers the bigger edition. The rules live here
so every surface agrees on what "the same album" and "a smaller edition" mean.
Everything is pure except ``prefer_deluxe_enabled`` (reads the setting) and
``owned_row_is_smaller_edition``, which asks a duck-typed library DB for an owned
album's title and track count.
"""

from __future__ import annotations

import re
import unicodedata
from typing import Any, Callable, Iterable, List, Optional

PREFER_DELUXE_KEY = "wishlist.prefer_deluxe_editions"

# Words that mark a BIGGER edition. "Remastered", "Limited", "Explicit" or
# "(Taylor's Version)" do not: those carry the standard album's tracklist, or are
# a different recording of it. Generic words like "special" only count as part of
# "... Edition" so a live or remix album never reads as a deluxe of the studio one.
_DELUXE_WORDS = (
    r"super\s+deluxe|deluxe|expanded|anniversary|bonus|"
    r"(?:special|complete|platinum|collector'?s|extended)\s+(?:edition|version)"
)
# Words that mark any edition at all — stripped to find an album's base title.
_EDITION_WORDS = _DELUXE_WORDS + r"|edition|version|remaster(?:ed)?|reissue|explicit|clean"

# A bracketed group containing one of the words: "(Deluxe Edition)", "[Expanded]".
_GROUP = r"\s*[\(\[][^\(\)\[\]]*\b(?:%s)\b[^\(\)\[\]]*[\)\]]"
# A trailing dash/colon clause containing one: " - Deluxe Edition",
# ": Expanded Mourner's Edition", " - 2011 Remaster". A hyphen inside a word
# ("Re-Up") has no space after it, so it never matches.
_DASH = r"\s*[-–—:]\s+[^-–—:\(\)\[\]]*\b(?:%s)\b[^-–—:\(\)\[\]]*$"

_DELUXE_GROUP_RE = re.compile(_GROUP % _DELUXE_WORDS, re.IGNORECASE)
_DELUXE_DASH_RE = re.compile(_DASH % _DELUXE_WORDS, re.IGNORECASE)
_ANY_GROUP_RE = re.compile(_GROUP % _EDITION_WORDS, re.IGNORECASE)
_ANY_DASH_RE = re.compile(_DASH % _EDITION_WORDS, re.IGNORECASE)
# A bare trailing "Deluxe" / "Deluxe Edition" with no brackets.
_BARE_DELUXE_RE = re.compile(r"\s*\b(?:super\s+)?deluxe(?:\s+(?:edition|version))?\s*$", re.IGNORECASE)


def prefer_deluxe_enabled(config: Any = None) -> bool:
    """Whether the option is on. Any error reads as off — the existing behaviour."""
    try:
        if config is None:
            from config.settings import config_manager as config
        return bool(config.get(PREFER_DELUXE_KEY, False))
    except Exception:   # noqa: BLE001 - a settings hiccup must never change behaviour
        return False


def _normalize(text: str) -> str:
    decomposed = unicodedata.normalize("NFKD", text)
    unmarked = "".join(c for c in decomposed if not unicodedata.combining(c))
    return re.sub(r"[^a-z0-9]+", " ", unmarked.lower()).strip()


def has_deluxe_marker(title: Optional[str]) -> bool:
    """True when a title names a bigger-than-standard edition."""
    text = str(title or "")
    return bool(_DELUXE_GROUP_RE.search(text) or _DELUXE_DASH_RE.search(text)
                or _BARE_DELUXE_RE.search(text))


def base_album_title(title: Optional[str]) -> str:
    """The album's title with every edition marker removed, normalized for
    comparison (lowercase, no accents or punctuation). Empty when nothing is left
    — an album literally titled "Deluxe" has no base to compare."""
    text = str(title or "")
    while True:
        stripped = _BARE_DELUXE_RE.sub("", _ANY_DASH_RE.sub("", _ANY_GROUP_RE.sub("", text)))
        if stripped == text:
            break
        text = stripped
    return _normalize(text)


def same_album_family(a: Optional[str], b: Optional[str]) -> bool:
    """True when two titles are editions of one album.

    Exact equality of the base titles, deliberately not a fuzzy ratio: "The
    Marshall Mathers LP" and "The Marshall Mathers LP2" are 98% similar and are
    different albums. A false "same album" here would skip or re-download a
    genuinely different record."""
    base = base_album_title(a)
    return bool(base) and base == base_album_title(b)


def _positive_int(value: Any) -> Optional[int]:
    try:
        number = int(value)
    except (TypeError, ValueError):
        return None
    return number if number > 0 else None


def is_smaller_edition(requested_title: Optional[str], requested_count: Any,
                       owned_title: Optional[str], owned_count: Any) -> bool:
    """True when ``owned`` is a SMALLER edition of ``requested``: same album, the
    requested title names a bigger edition and the owned one doesn't.

    Counts only guard, never decide on their own: when both are known and the
    owned album already holds at least as many tracks, it is not smaller (a media
    server may have merged a deluxe into a plain-titled album). A library's count
    is how many tracks you HAVE, not the edition's size, so a partly-owned album
    of the same edition must not read as "smaller" by count alone."""
    if not requested_title or not owned_title:
        return False
    if not has_deluxe_marker(requested_title) or has_deluxe_marker(owned_title):
        return False
    if not same_album_family(requested_title, owned_title):
        return False
    requested, owned = _positive_int(requested_count), _positive_int(owned_count)
    if requested is not None and owned is not None and owned >= requested:
        return False
    return True


def _field(item: Any, name: str) -> Any:
    if isinstance(item, dict):
        return item.get(name)
    return getattr(item, name, None)


def _release_title(release: Any) -> str:
    return _field(release, "name") or _field(release, "title") or ""


def _release_track_count(release: Any) -> Any:
    count = _field(release, "total_tracks")
    return count if count is not None else _field(release, "track_count")


def drop_smaller_editions(releases: Iterable[Any], *,
                          title_of: Optional[Callable[[Any], str]] = None,
                          count_of: Optional[Callable[[Any], Any]] = None) -> List[Any]:
    """The releases, minus any that has a bigger edition in the same list — "X"
    goes when "X (Deluxe Edition)" is present. Order is preserved; releases with no
    bigger sibling (including two deluxe spellings of one album) all stay."""
    items = list(releases or [])
    title_of = title_of or _release_title
    count_of = count_of or _release_track_count
    described = [(title_of(item) or "", count_of(item)) for item in items]
    kept: List[Any] = []
    for index, item in enumerate(items):
        title, count = described[index]
        if any(other_index != index and is_smaller_edition(other_title, other_count, title, count)
               for other_index, (other_title, other_count) in enumerate(described)):
            continue
        kept.append(item)
    return kept


def owned_row_is_smaller_edition(db: Any, requested_title: Optional[str], requested_count: Any,
                                 row: Any, memo: Optional[dict] = None) -> bool:
    """Whether a library ownership hit — a track row — sits on a SMALLER edition of
    the requested album, so it must not count as owning that song for this edition.

    ``db`` is duck-typed: ``get_album_title_year(album_id)`` supplies the owned
    album's title when the row carries no ``album_title``, and
    ``get_tracks_by_album(album_id)`` its track count — asked only once the titles
    alone say "smaller". ``memo`` (a dict) caches that per album id across calls
    for one requested album. Fail-open: a missing piece or any error returns
    False, and the hit counts as owned exactly as it did before."""
    try:
        if row is None or not has_deluxe_marker(requested_title):
            return False
        album_id = _field(row, "album_id")
        if memo is not None and album_id is not None and album_id in memo:
            owned_title, owned_count = memo[album_id]
        else:
            owned_title = _field(row, "album_title")
            if not owned_title and album_id is not None:
                meta = db.get_album_title_year(album_id)
                owned_title = meta[0] if meta else None
            owned_count = None
            if (owned_title and album_id is not None
                    and is_smaller_edition(requested_title, None, owned_title, None)):
                owned_count = len(db.get_tracks_by_album(album_id) or [])
            if memo is not None and album_id is not None:
                memo[album_id] = (owned_title, owned_count)
        if not owned_title:
            return False
        return is_smaller_edition(requested_title, requested_count, owned_title, owned_count)
    except Exception:   # noqa: BLE001 - the gate must never break an ownership check
        return False


__all__ = [
    "PREFER_DELUXE_KEY",
    "base_album_title",
    "drop_smaller_editions",
    "has_deluxe_marker",
    "is_smaller_edition",
    "owned_row_is_smaller_edition",
    "prefer_deluxe_enabled",
    "same_album_family",
]
