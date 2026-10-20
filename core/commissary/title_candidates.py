"""Title spellings the album matcher scores for a file, including
``<Album> <NN> <Title>`` filenames.

When a file has no title tag the matcher scores the filename stem, and it
only stripped a track number at the START of that stem. Untagged rips are
often named ``<Album> <NN> <Title>``: with the album name in front of the
number nothing was stripped, so "Blue Blood 01 Blue Blood" was scored against
the track "Blue Blood" -- 0.34 against a 0.40 threshold, and a correctly named
album matched nothing at all.

Deliberately not looser: the prefix is dropped only when there is no title tag
(a real tag is authoritative and used alone), only when the album name ends on
a token boundary, and never when removing it would leave nothing.

Ported from the legacy fork (Release 1.9.7).
"""

from __future__ import annotations

import os
import re
from typing import Any, Dict, List

# Separators a ripper puts between the album name and what follows it.
_PREFIX_SEPARATORS = " -_.–—|/\\"


def strip_album_prefix(stem: str, album: str) -> str:
    """``'Blue Blood 01 Blue Blood'`` + album ``'Blue Blood'`` -> ``'Blue Blood'``.

    Returns the stem unchanged unless removing the prefix leaves something
    real: a track genuinely titled after its album must not be reduced to
    nothing."""
    from core.imports.paths import strip_leading_track_number
    s = (stem or "").strip()
    a = (album or "").strip()
    if not s or not a or not s.casefold().startswith(a.casefold()):
        return s
    rest_raw = s[len(a):]
    # The album name has to end on a token boundary, or album "Blue Blood"
    # would cut "Blue Bloodhound Blues" down to "hound Blues".
    if rest_raw and rest_raw[0] not in _PREFIX_SEPARATORS:
        return s
    rest = strip_leading_track_number(rest_raw.lstrip(_PREFIX_SEPARATORS))
    if rest and not rest.isdigit() and re.search(r"[^\W_]", rest):
        return rest
    return s


def title_candidates(file_path: str, file_tags: Dict[str, Any], target_album: str) -> List[str]:
    """The title spellings worth scoring for this file, best-known first.

    A real title tag is used alone. The filename is a guess, so when that is
    all there is, both readings of it are offered and the caller keeps
    whichever agrees better."""
    from core.imports.paths import strip_leading_track_number
    tag_title = (file_tags.get('title') or '').strip() if file_tags else ''
    if tag_title:
        return [strip_leading_track_number(tag_title)]

    stem = os.path.splitext(os.path.basename(file_path))[0]
    base = strip_leading_track_number(stem)
    candidates = [base]
    without_album = strip_album_prefix(base, target_album)
    if without_album != base:
        candidates.append(without_album)
    return candidates


__all__ = ["strip_album_prefix", "title_candidates"]
