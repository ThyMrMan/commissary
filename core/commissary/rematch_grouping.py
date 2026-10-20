"""Keep every staged Re-identify copy out of the scanner's album-tag groups.

A Re-identify (per-track, or upstream's album reassign) copies a library file
into the staging root and writes a hint naming the release the user chose. The
copy keeps the album tag of the album it is LEAVING, and the auto-import scanner
groups loose staging files by album tag -- so two copies from one album, or a
copy beside a fresh download of that album, became ONE candidate.
``AutoImportWorker._resolve_rematch_hint`` honours only single-file candidates,
so every hint in the group was ignored: the files were identified from their old
tags and filed back where they came from, and the hints stayed pending for good.

The scanner reads the pending hints once per scan and splits each hinted loose
file out as its own candidate -- exactly the candidate a lone copy has always
had. A copy binds by normalised path or by bare filename (its
``[reid-<track id>]`` suffix), the terms ``find_hint_for_file`` uses, so a
Docker-mapped staging path still binds.

Ported from the legacy fork (Release 2.3.6).
"""

from __future__ import annotations

import ntpath
import os
from typing import Any, List, Optional, Tuple

from utils.logging_config import get_logger

logger = get_logger("commissary.rematch_grouping")

NO_KEYS: Tuple[frozenset, frozenset] = (frozenset(), frozenset())


def staged_file_keys(path: Optional[str]) -> Tuple[str, str]:
    """``(path_key, name_key)`` binding a staged file to a hint without a query
    per file: the normalised full path, and the bare filename.

    The filename is the fallback ``find_hint_for_file`` takes as well. It carries
    ``[reid-<track id>]``, so it is unique in practice, and it still matches when
    the path a hint was written with is spelled differently from the one the
    scanner lists. ``ntpath`` splits on both separators, so a path recorded in
    either form yields its name."""
    if not path:
        return "", ""
    return os.path.normcase(os.path.normpath(path)), os.path.normcase(ntpath.basename(path))


def pending_staged_keys(cursor: Any) -> Tuple[frozenset, frozenset]:
    """The keys (see :func:`staged_file_keys`) of every PENDING hint, in one
    query: ``(path_keys, name_keys)``."""
    cursor.execute("SELECT staged_path FROM rematch_hints WHERE status = 'pending'")
    path_keys, name_keys = set(), set()
    for row in cursor.fetchall():
        path_key, name_key = staged_file_keys(row[0])
        if path_key:
            path_keys.add(path_key)
        if name_key:
            name_keys.add(name_key)
    return frozenset(path_keys), frozenset(name_keys)


def pending_rematch_keys(database: Any) -> Tuple[frozenset, frozenset]:
    """:func:`pending_staged_keys` read through the worker's database. Fail-safe
    like the hint lookup itself: any error (no database, no table, a DB hiccup)
    yields no keys, and the scan is exactly upstream's."""
    try:
        conn = database._get_connection()
        try:
            return pending_staged_keys(conn.cursor())
        finally:
            conn.close()
    except Exception as e:
        logger.debug("[Auto-Import] pending re-identify lookup skipped: %s", e)
        return NO_KEYS


def split_rematch_copies(files: List[str], keys) -> Tuple[List[str], List[str]]:
    """Separate staged Re-identify copies from the other loose files.

    Returns ``(others, copies)``, each in its original order."""
    path_keys, name_keys = keys or NO_KEYS
    if not path_keys and not name_keys:
        return files, []
    others: List[str] = []
    copies: List[str] = []
    for f in files:
        path_key, name_key = staged_file_keys(f)
        if path_key in path_keys or name_key in name_keys:
            copies.append(f)
        else:
            others.append(f)
    return others, copies


__all__ = [
    "pending_rematch_keys",
    "pending_staged_keys",
    "split_rematch_copies",
    "staged_file_keys",
]
