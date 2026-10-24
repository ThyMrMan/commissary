"""Let a Re-identify the user asks for again through the auto-import scan.

A Re-identify (per-track, or upstream's album reassign) copies a library file
into the staging root and writes a hint naming the release the user chose.
Asking to re-identify the same track again stages the same file under the same
name, so the copy has the folder hash of the attempt before it and the worker's
``_is_already_processed`` skipped it for good. :func:`is_rematch_retry` lets it
through when a pending hint for the file was written after the file's LATEST
import-history row. A copy binds to a hint by normalised path or by bare
filename (its ``[reid-<track id>]`` suffix), the terms ``find_hint_for_file``
uses, so a Docker-mapped staging path still binds.

Ported from the legacy fork (Releases 2.3.6 and 2.3.7). The 2.3.6 half - each
copy its own candidate, never part of an album-tag group - is upstream's own
since 3.5.9 (#1567), so it was dropped here.
"""

from __future__ import annotations

import ntpath
import os
from typing import Any, Optional, Tuple

from utils.logging_config import get_logger

logger = get_logger("commissary.rematch_grouping")

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


__all__ = [
    "is_rematch_retry",
    "pending_hint_written_after",
    "staged_file_keys",
]
