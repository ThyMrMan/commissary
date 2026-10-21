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

Asking to re-identify the same track again stages the same file under the same
name, so the copy has the folder hash of the attempt before it and the worker's
``_is_already_processed`` skipped it for good. :func:`is_rematch_retry` lets it
through when a pending hint for the file was written after the file's LATEST
import-history row.

Ported from the legacy fork (Releases 2.3.6 and 2.3.7).
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


def pending_hint_written_after(cursor: Any, staged_path: Optional[str], timestamp: Any) -> bool:
    """True when a PENDING hint for ``staged_path`` was written after ``timestamp``
    -- a time the database recorded, such as the ``created_at`` of the latest
    auto-import history row for the file.

    The hint an attempt used was written before that attempt's history row, so
    it never counts -- a failure does not retry itself. A hint binds on the terms
    of :func:`staged_file_keys`, without reading the file. ``julianday`` compares
    the times, so both spellings SQLite accepts order correctly, and a missing or
    unreadable time is never "after". Times have one-second resolution: a hint
    written in the same second as ``timestamp`` is not after it."""
    if not staged_path or not timestamp:
        return False
    cursor.execute(
        "SELECT staged_path FROM rematch_hints WHERE status = 'pending' "
        "AND julianday(created_at) > julianday(?)",
        (timestamp,),
    )
    path_key, name_key = staged_file_keys(staged_path)
    for row in cursor.fetchall():
        hint_path, hint_name = staged_file_keys(row[0])
        if hint_path == path_key or (name_key and hint_name == name_key):
            return True
    return False


def is_rematch_retry(database: Any, candidate: Any) -> bool:
    """True when a candidate the history calls processed is a Re-identify the
    user has asked for again since its last attempt.

    Single files only, like ``_resolve_rematch_hint``. Fail-safe the other way
    from the hint lookups: any error answers False, so a file already tried
    stays skipped rather than being tried again on every scan."""
    files = getattr(candidate, "audio_files", None) or []
    if len(files) != 1:
        return False
    try:
        conn = database._get_connection()
        try:
            cursor = conn.cursor()
            cursor.execute(
                "SELECT created_at FROM auto_import_history WHERE folder_hash = ? "
                "ORDER BY julianday(created_at) DESC, id DESC LIMIT 1",
                (candidate.folder_hash,),
            )
            row = cursor.fetchone()
            retry = row is not None and pending_hint_written_after(cursor, files[0], row[0])
        finally:
            conn.close()
    except Exception as e:
        logger.debug("[Auto-Import] re-identify retry check skipped: %s", e)
        return False
    if retry:
        logger.debug("[Auto-Import] %s was re-identified again since its last attempt",
                     os.path.basename(files[0]))
    return retry


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
    "is_rematch_retry",
    "pending_hint_written_after",
    "pending_rematch_keys",
    "pending_staged_keys",
    "split_rematch_copies",
    "staged_file_keys",
]
