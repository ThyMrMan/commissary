"""Is this release a pile of archives rather than a video?

Scene torrents are very often delivered as a set of RAR parts —
``release.part01.rar``, ``release.r00``, ``release.r01`` … — with the video only
appearing once something unpacks them. Commissary unpacks nothing on the video
side: ``core/archive_pipeline`` is real, but its entry point is
``collect_audio_after_extraction`` and it is wired only into the music
torrent/usenet plugins. So such a release produces no importable file, ever.

That used to fail in the worst available way. ``client_download`` walks the
finished folder for a video extension, finds none, and returns a patch with
progress but no status — which the monitor reads as "complete but the file isn't
visible yet, keep polling". Since 2.0.7 the stall clock ends that after thirty
minutes, but with the at-completion message about save paths, which sends you
hunting a path-mapping bug that was never there.

The same question gets asked of two different sources, which is why this is its
own module rather than a helper inside either caller:

  * the torrent client's file list, WHILE it is still downloading — "what is
    this going to be?" This is the one that can still save the bandwidth.
  * the finished folder on disk, afterwards — "what actually arrived?" This one
    also covers a usenet job whose unrar failed, which the file list cannot:
    a usenet release is ALWAYS rars, and SABnzbd/NZBGet unpack them server-side
    before Commissary ever looks. Judging a usenet job by its file list would
    refuse every single one.

Pure: it takes names and answers. No filesystem, no client, no config.
"""

from __future__ import annotations

import os
import re
from typing import Iterable, List

from core.video.slskd_search import _is_video

VIDEO = "video"        # something playable is in there — nothing to refuse
PACKED = "packed"      # archives, and nothing playable — this can never import
UNKNOWN = "unknown"    # can't tell: no names yet, or nothing recognisable

# Whole-name extensions. `.gz`/`.bz2`/`.xz` also cover `.tar.gz` — the leading
# `.tar` adds nothing, and a bare `.gz` is just as unimportable.
_ARCHIVE_EXT = frozenset({
    ".rar", ".zip", ".7z", ".tar", ".tgz", ".gz", ".bz2", ".xz", ".arj", ".cab",
})

# The CONTINUATION volumes, which carry no recognisable extension of their own:
#   .r00 … .r999   old-style RAR volumes (the ones after .rar)
#   .z01 … .z999   split ZIP volumes
#   .001 … .999    generic split volumes (7z, and `film.mkv.001`)
# Anchored to the end, so `Show.S01E01.1080p.mkv` cannot match on anything in
# the middle of the name.
_PART_RE = re.compile(r"\.(?:r\d{2,3}|z\d{2,3}|\d{3})$", re.I)

# Which word to use in the message. Keyed by the extension that proved it.
_KIND = {".rar": "rar", ".zip": "zip", ".7z": "7z", ".tar": "tar", ".tgz": "tar",
         ".gz": "tar", ".bz2": "tar", ".xz": "tar", ".arj": "arj", ".cab": "cab"}


def _base(name) -> str:
    """The bare filename, lowercased.

    ``.lower()`` is the half that decides anything: scene releases are routinely
    named ``Film.PART01.RAR``, and an extension set is case-sensitive. The
    basename and separator normalising are belt-and-braces — ``os.path.splitext``
    is already separator-aware and the volume pattern is end-anchored, so no
    input I can construct reaches a different verdict without them. They stay
    because they make what ``is_archive`` is asking obvious at a glance."""
    return os.path.basename(str(name or "").replace("\\", "/")).strip().lower()


def is_archive(name) -> bool:
    base = _base(name)
    if not base:
        return False
    _, ext = os.path.splitext(base)
    return ext in _ARCHIVE_EXT or bool(_PART_RE.search(base))


def is_playable(name) -> bool:
    """A video file the importer would actually take.

    Delegates wholesale to ``_is_video``, which already does both halves: it
    reads the BASENAME of a path, and it refuses anything with 'sample' in it —
    the very rule ``client_download._largest_video`` applies when choosing the
    file to import. Re-implementing either half here is how the two drift apart,
    and if this ever answered VIDEO where the locator finds nothing, the poll
    loop would go straight back into the silent spin this module exists to end.
    A folder of RAR parts plus a ``sample.mkv`` therefore holds nothing playable,
    which is the correct answer and not a special case."""
    return bool(_is_video(str(name or "")))


def archives_in(names: Iterable) -> List[str]:
    """Every archive among ``names``, in the order given."""
    return [str(n) for n in (names or []) if is_archive(n)]


def classify(names: Iterable) -> str:
    """``VIDEO`` | ``PACKED`` | ``UNKNOWN``.

    A playable file wins outright — a release carrying both a video and a
    ``subs.rar`` is a perfectly normal release, and refusing it would be a far
    worse bug than the one being fixed. ``UNKNOWN`` covers both "the client
    hasn't fetched the metadata yet" (an empty list) and "this is something I
    don't recognise", and both must be left alone: refusing what you cannot read
    is how a working release gets thrown away."""
    names = list(names or [])
    if any(is_playable(n) for n in names):
        return VIDEO
    return PACKED if any(is_archive(n) for n in names) else UNKNOWN


def _kinds(packed: List[str]) -> str:
    out = []
    for n in packed:
        base = _base(n)
        _, ext = os.path.splitext(base)
        word = _KIND.get(ext) or ("rar" if base and _PART_RE.search(base)
                                  and base.rsplit(".", 1)[-1][:1] == "r" else "split")
        if word not in out:
            out.append(word)
    return "/".join(sorted(out)) or "archive"


def reason(names: Iterable, *, before_finishing: bool = False) -> str:
    """What to write on the download row.

    It names the format, the count and one example on purpose. "It's a rar" and
    "it's a 47-part rar set" lead to the same fix but read completely
    differently, and a message that doesn't say what it found is a message you
    cannot check against the folder."""
    packed = archives_in(names)
    if not packed:
        return "No video file in this release."
    n = len(packed)
    detail = "%d %s file%s and nothing playable (e.g. %s)" % (
        n, _kinds(packed), "" if n == 1 else "s", os.path.basename(str(packed[0])))
    if before_finishing:
        return ("Refused before finishing: the download client reports %s. "
                "Commissary does not unpack archives on the video side, so this "
                "release could never be imported — looking for another." % detail)
    return ("Finished, but there is no video file in it — %s. Nothing unpacked "
            "them, and Commissary does not unpack archives on the video side."
            % detail)


__all__ = ["VIDEO", "PACKED", "UNKNOWN", "is_archive", "is_playable",
           "archives_in", "classify", "reason"]
