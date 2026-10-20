"""Release-title matching for fansub/anime naming, arcs and colon subtitles.

Upstream's title gate (core.video.release_parse.titles_match) compares the
text before the release year / first quality token with the wanted titles.
Three naming conventions defeat it, and each rejected every release of
otherwise matchable titles (legacy fork 1.6.3, 1.6.6, 2.0.1, 2.4.0):

1. Fansub names: '[SubsPlease] One Piece - 1071 [1080p]'. The leading group tag
   and the dash-glued absolute episode number stay in the extracted title, so
   it reads 'subsplease one piece 1071' and never equals 'one piece'.
2. Colon subtitles: a release carries the full official title
   ('... Zero Subjects: Tales of Blue Dias ...') where TMDB stores only the
   part before the colon.
3. Arcs: TVDB files a long-running anime one season per arc, and the arc's
   releases number from 1 under the arc's name ('Bleach - Sennen Kessen Hen -
   48' is S17E48); the title reads as a different show and the number is not
   the absolute one.

Everything here only ever ACCEPTS a release upstream would reject; a miss
leaves upstream's verdict standing.
"""

from __future__ import annotations

import re
from typing import Any

# A leading fansub/release-group tag ('[SubsPlease] ...', '[Erai-raws] ...').
LEADING_GROUP_TAG = re.compile(r"^\s*\[[^\]]{1,40}\]\s*")
# A bare episode number glued onto the title with a dash ('Title - 40', '- 0523v2').
TRAILING_EP_NUM = re.compile(r"[-_]\s*\d{1,4}(?:v\d+)?\s*(?=[\[(]|$)")
# The episode number glued with a dash onto the END of a title region. The dash
# is the whole signal: it tells an episode number from one that is part of the
# name ('... Part 2').
_ARC_GLUED_NUM = re.compile(r"[-_–—]\s*0*(\d{1,4})(?:v\d+)?$")

# Colon subtitles: only split a head of at least this many words ('Dune: Part
# Two', 'Star Trek: Picard' stay whole) and never before an installment marker.
_SUBTITLE_MIN_WORDS = 3
_INSTALLMENT_TAIL = re.compile(
    r"^\s*(?:part|chapter|chap|vol|volume|book|episode|ep|season|series|act|"
    r"the\s+(?:first|second|third|fourth|fifth|final|last))\b", re.I)

# How many wanted titles a rejection names before counting the rest.
_WANTED_SPELLED_OUT = 3


def title_region(release_name: Any) -> str:
    """The title portion of a release name as written, separators intact.

    Same cut as upstream's extract_title (release year or first quality/scope
    token, whichever is first), without folding separators to spaces: the fold
    destroys the dash an arc release glues its episode number onto."""
    from core.video.release_parse import _META_BOUNDARY, _YEAR_TOKEN, _spaces

    t = str(release_name or "").strip()
    if not t:
        return ""
    cut = None
    years = list(_YEAR_TOKEN.finditer(t))
    if years and _spaces(t[:years[-1].start()]):
        cut = years[-1].start()
    m = _META_BOUNDARY.search(t)
    if m and _spaces(t[:m.start()]) and (cut is None or m.start() < cut):
        cut = m.start()
    return t[:len(t) if cut is None else cut]


def fansub_candidates(cands) -> list:
    """Extra title candidates for fansub names: each candidate that has a leading
    group tag, without the tag, and without the tag and its glued episode
    number. Candidates without a tag add nothing, so ordinary names (including
    ones that end in a number, like 'Moana 2') are unaffected."""
    out = []
    for c in cands:
        tagless = LEADING_GROUP_TAG.sub("", c, count=1)
        if tagless != c:
            for extra in (tagless, TRAILING_EP_NUM.sub("", tagless)):
                if extra not in cands and extra not in out:
                    out.append(extra)
    return out


def title_variants(raw_title: Any, normalized: str) -> list:
    """A RELEASE title and, when its tail after a colon reads as a subtitle, the
    part before the colon. Release side only: on the wanted side 'Mission:
    Impossible - Fallout' would accept any 'Mission ...' release."""
    from core.video.release_parse import normalize_title

    out = [normalized]
    raw = str(raw_title or "")
    if ":" not in raw:
        return out
    parts = raw.split(":")
    for i in range(1, len(parts)):
        head = normalize_title(":".join(parts[:i]))
        tail = ":".join(parts[i:])
        if (head and head not in out and len(head.split()) >= _SUBTITLE_MIN_WORDS
                and not _INSTALLMENT_TAIL.match(tail)):
            out.append(head)
    return out


def match_pairs(raw_title, normalized, wants):
    """(release title variant, wanted title) pairs for upstream's comparison loop."""
    for got in title_variants(raw_title, normalized):
        for want in wants:
            yield got, want


def fansub_absolute_episode(release_name: Any):
    """The absolute episode number of a fansub-style release ('[Group] Show - 40
    [1080p]'), or None. Requires the group tag, so a movie's '-GRP' never counts."""
    t = str(release_name or "")
    tagless = LEADING_GROUP_TAG.sub("", t, count=1)
    if tagless == t:
        return None
    m = TRAILING_EP_NUM.search(tagless)
    digits = "".join(ch for ch in m.group(0) if ch.isdigit()) if m else ""
    return int(digits) if digits else None


def arc_episode_number(release_name: Any, want_title: Any):
    """The episode number an ARC-named release of this show carries, or None.

    Two signals must agree: the title EXTENDS an acceptable title with the arc's
    name, and a number is glued onto the end of the title region with a dash.
    That keeps the show's own episode 48 ('[SubsPlease] Bleach - 48', no arc
    name) and a name's own number ('... Part 2', no dash) out."""
    from core.video.release_parse import acceptable_titles, normalize_title

    wants = acceptable_titles(want_title)
    if not wants:
        return None
    raw = str(release_name or "")
    cands = [raw]
    tagless = LEADING_GROUP_TAG.sub("", raw, count=1)
    if tagless != raw:
        cands.append(tagless)
    for cand in cands:
        head = title_region(cand).strip().rstrip(" ([{")
        m = _ARC_GLUED_NUM.search(head)
        if not m:
            continue
        name = normalize_title(head[:m.start()])
        if any(name != w and name.startswith(w + " ") for w in wants):
            return int(m.group(1))
    return None


def arc_episode_match(release_name: Any, want_title: Any, want_episode: Any) -> bool:
    """True when a release names the show's arc and numbers THIS episode in it."""
    try:
        want = int(want_episode)
    except (TypeError, ValueError):
        return False
    n = arc_episode_number(release_name, want_title)
    return n is not None and n == want


def compared_title(release_name: Any) -> str:
    """What the title gate compared, for the rejection reason: the extraction
    without a leading group tag or a bracket a quality cut left behind. Printing
    the tag ('SubsPlease Re:ZERO') sent people adding the group to a show's
    aliases, which can never help."""
    from core.video.release_parse import extract_title

    text = extract_title(LEADING_GROUP_TAG.sub("", str(release_name or ""), count=1))
    return re.sub(r"\s+", " ", re.sub(r"[\[\]()]+", " ", text)).strip()


def wanted_text(want_title) -> str:
    """The wanted side of a title rejection: never a list repr, never thirty
    aliases; the first few are named and the rest counted."""
    if isinstance(want_title, str):
        return want_title
    names = [str(t) for t in (want_title or []) if t]
    if not names:
        return "?"
    if len(names) <= _WANTED_SPELLED_OUT:
        return " / ".join(names)
    return "%s (+%d more)" % (" / ".join(names[:_WANTED_SPELLED_OUT]),
                              len(names) - _WANTED_SPELLED_OUT)
