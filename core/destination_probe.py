"""Can the server actually write into a destination folder?

Lives here rather than under ``core/imports`` because both sides need it and
neither owns it: music files albums into Music Libraries, video files titles
into video Libraries, and the failure mode is identical on both.

Written after a live install imported an entire album into a folder the
container had no permission to write to. Every track raised ``PermissionError``
on the artist folder and nothing surfaced in the UI — an unwritable destination
looks exactly like one nothing has been sent to yet.
"""

from __future__ import annotations

import os
import tempfile

from utils.logging_config import get_logger

logger = get_logger("destination_probe")


def probe_destination_writable(path) -> dict:
    """``{status, writable, detail}`` for ``path``.

    It CREATES AND REMOVES A DIRECTORY rather than calling ``os.access``.
    ``os.access`` answers from the permission bits alone, which is the wrong
    answer under exactly the conditions that break filing — container UID
    remapping, NFS root-squash, ACLs, read-only mounts. And a directory is the
    right probe, not a file: the failure being caught is ``mkdir`` of the
    artist/title folder, and a share can permit file creation while denying it.

    ``status`` distinguishes the causes because they have different fixes:
    ``missing`` is a typo or an unmapped volume, ``unwritable`` is ownership.
    """
    p = str(path or '').strip()
    if not p:
        return {"status": "unset", "writable": False, "detail": "No path configured"}
    try:
        if not os.path.exists(p):
            return {"status": "missing", "writable": False,
                    "detail": "Folder does not exist on the server"}
        if not os.path.isdir(p):
            return {"status": "not_a_directory", "writable": False,
                    "detail": "Path exists but is not a folder"}
    except OSError as e:
        return {"status": "unknown", "writable": False, "detail": str(e)}

    probe = None
    try:
        probe = tempfile.mkdtemp(prefix=".soulsync-write-test-", dir=p)
        return {"status": "ok", "writable": True, "detail": "Writable"}
    except PermissionError:
        return {"status": "unwritable", "writable": False,
                "detail": "Permission denied — the server cannot create folders here. "
                          "Check the folder's owner against the user Commissary runs as "
                          "(PUID/PGID)."}
    except OSError as e:
        return {"status": "unwritable", "writable": False, "detail": str(e)}
    finally:
        # Never leave the probe behind; a stray dot-folder in someone's library
        # is a bug report of its own.
        if probe:
            try:
                os.rmdir(probe)
            except OSError as e:     # noqa: BLE001
                logger.warning("Could not remove the write probe %s: %s", probe, e)


__all__ = ["probe_destination_writable"]
