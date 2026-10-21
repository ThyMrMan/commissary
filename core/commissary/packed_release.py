"""Is this release a pile of archives rather than a video? (the old fork's 2.0.10)

Scene torrents are often delivered as RAR parts - ``release.part01.rar``,
``release.r00``... - with the video appearing only once something unpacks
them. Nothing on the video side unpacks anything (core/archive_pipeline is
wired only into the music plugins, and the image ships no unrar), so such a
release can never import. Upstream finds no video file at 100%, returns
"complete but not visible yet, keep polling", and the stall clock fails it
half an hour later with a message about save paths.

The question is asked of two sources:
  * the torrent client's file list WHILE downloading - the only moment the
    bandwidth can still be saved (Prowlarr results carry no file list);
  * the finished folder afterwards - which also catches a usenet job whose
    unrar failed.

Torrents only for the first, permanently: a usenet release is ALWAYS rars,
and SABnzbd/NZBGet unpack them before we look. Two guards matter more than
the detection: a playable file wins outright (film.mkv + subs.rar is a normal
release), and UNKNOWN is left alone (an empty list means no metadata yet).
"""

from __future__ import annotations

import os
import re
from typing import Iterable, List, Optional

from utils.logging_config import get_logger

logger = get_logger("commissary.packed_release")

VIDEO = "video"        # something playable is in there - nothing to refuse
PACKED = "packed"      # archives, and nothing playable - this can never import
UNKNOWN = "unknown"    # can't tell: no names yet, or nothing recognisable

_ARCHIVE_EXT = frozenset({".rar", ".zip", ".7z", ".tar", ".tgz", ".gz", ".bz2", ".xz",
                          ".arj", ".cab"})
# continuation volumes: .r00-.r999 (rar), .z01-.z999 (split zip), .001-.999 (7z,
# 'film.mkv.001'); end-anchored so nothing mid-name can match
_PART_RE = re.compile(r"\.(?:r\d{2,3}|z\d{2,3}|\d{3})$", re.I)
_KIND = {".rar": "rar", ".zip": "zip", ".7z": "7z", ".tar": "tar", ".tgz": "tar",
         ".gz": "tar", ".bz2": "tar", ".xz": "tar", ".arj": "arj", ".cab": "cab"}


def _base(name) -> str:
    # lowercase decides it: scene names are routinely 'Film.PART01.RAR'
    return os.path.basename(str(name or "").replace("\\", "/")).strip().lower()


def is_archive(name) -> bool:
    base = _base(name)
    if not base:
        return False
    return os.path.splitext(base)[1] in _ARCHIVE_EXT or bool(_PART_RE.search(base))


def is_playable(name) -> bool:
    """What the importer's own file pick would take: upstream's ``_is_video``,
    plus the 'sample' rule ``client_download._largest_video`` applies - if this
    said VIDEO where that finds nothing, the poll loop would spin again."""
    from core.video.slskd_search import _is_video
    n = str(name or "")
    return bool(_is_video(n)) and "sample" not in _base(n)


def archives_in(names: Iterable) -> List[str]:
    return [str(n) for n in (names or []) if is_archive(n)]


def classify(names: Iterable) -> str:
    names = list(names or [])
    if any(is_playable(n) for n in names):
        return VIDEO
    return PACKED if any(is_archive(n) for n in names) else UNKNOWN


def _kinds(packed: List[str]) -> str:
    out = []
    for n in packed:
        base = _base(n)
        ext = os.path.splitext(base)[1]
        word = _KIND.get(ext) or ("rar" if base and _PART_RE.search(base)
                                  and base.rsplit(".", 1)[-1][:1] == "r" else "split")
        if word not in out:
            out.append(word)
    return "/".join(sorted(out)) or "archive"


def reason(names: Iterable, *, before_finishing: bool = False) -> str:
    """The format, the count and one example: 'a rar' and 'a 47-part rar set'
    lead to the same fix but read differently, and you can check it."""
    packed = archives_in(names)
    if not packed:
        return "No video file in this release."
    n = len(packed)
    detail = "%d %s file%s and nothing playable (e.g. %s)" % (
        n, _kinds(packed), "" if n == 1 else "s", os.path.basename(str(packed[0])))
    if before_finishing:
        return ("Refused before finishing: the download client reports %s. Nothing on the "
                "video side unpacks archives, so this release could never be imported - "
                "looking for another." % detail)
    return ("Finished, but there is no video file in it - %s. Nothing unpacked them, and "
            "nothing on the video side unpacks archives." % detail)


def refusal(dl: dict, names, *, before_finishing: bool) -> dict:
    """The failed patch. ``_bad_release`` has the monitor blocklist this exact
    release before retrying: being packed is permanent, and without it the
    retry would pick it straight back off the candidate list."""
    err = reason(names, before_finishing=before_finishing)
    logger.info("video download %s (%s): %s", (dl or {}).get("id"),
                (dl or {}).get("release_title") or (dl or {}).get("title") or "?", err)
    return {"status": "failed", "_bad_release": True, "error": err}


# ── the torrent client's file list ─────────────────────────────────────────

def _adapter_files(adapter, ref) -> Optional[List[str]]:
    """Each upstream adapter's own RPC, asked for the job's files. None means
    'couldn't ask'; [] means 'no metadata yet' - both read as unknown."""
    name = type(adapter).__name__
    if name == "QBittorrentAdapter":
        resp = adapter._call("GET", "/api/v2/torrents/files", params={"hash": ref})
        if not resp or not resp.ok:
            return None
        return [str(f.get("name")) for f in (resp.json() or []) if f.get("name")]
    if name == "TransmissionAdapter":
        data = adapter._rpc("torrent-get", {"ids": [_tx_id(ref)], "fields": ["files"]})
        rows = (data or {}).get("torrents") or []
        if not rows:
            return None
        return [str(f.get("name")) for f in rows[0].get("files") or []
                if isinstance(f, dict) and f.get("name")]
    if name == "DelugeAdapter":
        result = adapter._rpc_sync("core.get_torrent_status", [ref, ["files"]])
        if not isinstance(result, dict):
            return None
        return [str(f.get("path")) for f in result.get("files") or []
                if isinstance(f, dict) and f.get("path")]
    if name == "Aria2Adapter":
        result = adapter._rpc("aria2.tellStatus", ref, ["files"])
        if not isinstance(result, dict):
            return None
        return [str(f.get("path")) for f in result.get("files") or []
                if isinstance(f, dict) and f.get("path")]
    return None


def _tx_id(ref):
    try:
        return int(ref)
    except (TypeError, ValueError):
        return ref               # a hash string is a valid Transmission id too


_files_cache: dict = {}
_FILES_CACHE_CAP = 512


def client_files(source: str, ref: str) -> Optional[List[str]]:
    """The torrent client's file list for one job, memoized once non-empty:
    the monitor asks every few seconds and the answer doesn't change once the
    metadata is in. An empty answer isn't cached - that magnet hasn't resolved
    yet, and those are exactly the ones worth asking again."""
    if str(source or "") != "torrent":
        return None
    cached = _files_cache.get(str(ref))
    if cached is not None:
        return cached
    try:
        from core.torrent_clients import get_active_adapter
        adapter = get_active_adapter()
        if adapter is None:
            return None
        names = _adapter_files(adapter, str(ref))
    except Exception:  # noqa: BLE001 - unanswerable reads as unknown, never as bad
        logger.debug("file list unavailable for %s", ref, exc_info=True)
        return None
    if names:
        if len(_files_cache) > _FILES_CACHE_CAP:
            for stale in list(_files_cache)[:len(_files_cache) - _FILES_CACHE_CAP]:
                _files_cache.pop(stale, None)
        _files_cache[str(ref)] = list(names)
    return names


def in_flight(dl: dict, ref, list_files=client_files) -> Optional[dict]:
    """Refuse a downloading TORRENT whose file list is archives and nothing else."""
    if list_files is None or str((dl or {}).get("source") or "") != "torrent":
        return None
    try:
        names = list_files("torrent", str(ref))
    except Exception:  # noqa: BLE001
        return None
    if classify(names or []) != PACKED:
        return None
    return refusal(dl, names or [], before_finishing=True)


def listing(path) -> List[str]:
    """Every file under ``path`` (or the file itself) - what DID arrive."""
    if not path:
        return []
    if os.path.isfile(path):
        return [str(path)]
    if not os.path.isdir(path):
        return []
    out = []
    for dirpath, _dirs, files in os.walk(path):
        out.extend(os.path.join(dirpath, f) for f in files)
    return out


def finished(dl: dict, path, settled=None, lister=None) -> Optional[dict]:
    """A completed job with no video found: refuse it if what arrived is
    archives and nothing playable - once it has SETTLED, since a usenet job at
    100% is often still being unrar'd by its own client, rars in plain sight."""
    names = (lister or listing)(path)
    if classify(names) != PACKED:
        return None
    if settled is not None and not settled(dl, path):
        return None
    return refusal(dl, names, before_finishing=False)
