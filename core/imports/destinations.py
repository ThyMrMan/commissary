"""Which Music Library a file is filed into, and under what naming template.

Music had exactly one destination — ``soulseek.transfer_path`` — since its
Soulseek-era design. This resolves the destination per file instead, from the
``music_root_folders`` table, while keeping the single-destination behaviour
byte-identical for an install that never configures a second library.

There is one resolver rather than a lookup at each call site because the whole
point is that every write path agrees: a download, a manual import and a
reorganize must all compute the same destination for the same context, or files
of one album end up split across libraries.

Precedence, most specific first:

1. ``context['_music_root_id']`` — an explicit choice (a picked library, a
   wishlist row's ``root_folder_id``, an import into a named library).
2. The lowest-``sort_order`` library — the user's default.
3. ``soulseek.transfer_path`` — the pre-libraries destination, and still the
   answer on an install whose table is empty.

Every step degrades to the next rather than failing. A wishlist row naming a
library the user has since deleted resolves to the default and downloads;
refusing would strand items on a setting change they may not connect to the
symptom.
"""

from __future__ import annotations

import os
from typing import Any, Optional, Tuple

from utils.logging_config import get_logger

logger = get_logger("imports.destinations")


def _config_transfer_path(config_get=None) -> str:
    try:
        if config_get is None:
            from config.settings import config_manager
            config_get = config_manager.get
        return str(config_get("soulseek.transfer_path", "./Transfer") or "./Transfer")
    except Exception:   # noqa: BLE001 - config unavailable (early init / tests)
        return "./Transfer"


def _libraries(libraries=None) -> list:
    if libraries is not None:
        return list(libraries)
    try:
        from database.music_database import get_database
        return get_database().list_music_libraries() or []
    except Exception as e:   # noqa: BLE001 - a DB hiccup must not break an import
        logger.debug("music library lookup failed, using the configured path: %s", e)
        return []


def _library_containing(path, libs) -> Optional[dict]:
    """The library ``path`` lives under, or None.

    Deepest match wins: with a library at ``/music`` and another at
    ``/music/archive``, a file in the latter belongs to the latter. Ordering by
    path length rather than sort_order is what makes nesting behave.
    """
    if not path or not isinstance(path, str):
        return None
    try:
        target = os.path.normcase(os.path.abspath(path))
    except Exception:   # noqa: BLE001
        return None
    best = None
    best_len = -1
    for lib in libs:
        root = lib.get("path")
        if not root:
            continue
        try:
            root_abs = os.path.normcase(os.path.abspath(root))
        except Exception:   # noqa: BLE001
            continue
        # commonpath, not startswith: "/music-old" is not inside "/music".
        try:
            if target == root_abs or os.path.commonpath([target, root_abs]) == root_abs:
                if len(root_abs) > best_len:
                    best, best_len = lib, len(root_abs)
        except ValueError:
            continue    # different drives on Windows
    return best


def resolve_music_library(context: Optional[dict], libraries=None) -> Optional[dict]:
    """The library row this context targets, or None to mean "not from the
    table" — in which case callers use ``soulseek.transfer_path``.

    Kept separate from :func:`resolve_music_destination` so callers that want
    the library's OTHER settings (naming template, quality profile) don't have
    to re-query. ``libraries`` lets a caller supply the rows it already read
    (or an empty list to mean "none configured") instead of hitting the DB.
    """
    libs = _libraries(libraries)
    if not libs:
        return None

    root_id = None
    if isinstance(context, dict):
        # An explicit choice for THIS operation (a picked library, an import
        # into a named library) outranks the item's stored destination — the
        # user is looking at the screen when they set it.
        root_id = context.get("_music_root_id")
        if root_id in (None, ""):
            # The item's own destination, carried on track_info exactly the
            # way `quality_profile_id` is, so a wishlist row needs no extra
            # plumbing to be filed where it was asked to go.
            track_info = context.get("track_info")
            if isinstance(track_info, dict):
                root_id = track_info.get("root_folder_id")
        if root_id in (None, ""):
            # Re-filing a file that already exists (reorganize, a rename) must
            # keep it in the library it's IN. Without this, every reorganize of
            # a file outside the default library would compute a destination
            # inside the default one and quietly consolidate the user's
            # libraries into one — a data move they never asked for, dressed up
            # as a rename. Same rule the video side uses when picking a root.
            current = context.get("_current_file_path")
            containing = _library_containing(current, libs)
            if containing is not None:
                return containing
    if root_id not in (None, ""):
        try:
            wanted = int(root_id)
        except (TypeError, ValueError):
            wanted = None
        if wanted is not None:
            for lib in libs:
                if lib.get("id") == wanted:
                    return lib
            # Named a library that no longer exists. Fall through to the
            # default rather than failing — see the module docstring.
            logger.info("Music library %s is gone; filing into the default library", root_id)

    return libs[0]


def resolve_music_destination(context: Optional[dict], libraries=None,
                              config_get=None) -> Tuple[str, Optional[str]]:
    """``(root_path, naming_template)`` for this context.

    ``naming_template`` is None when the library doesn't override it, which
    means "use the configured global template" — NOT "no template".

    ``libraries`` / ``config_get`` are injection points so a caller that
    already resolves its own config (``core.imports.paths``) keeps ONE source
    of truth rather than this module reaching around it.
    """
    lib = resolve_music_library(context, libraries)
    if not lib or not lib.get("path"):
        return _config_transfer_path(config_get), None
    return lib["path"], (lib.get("naming_template") or None)


def apply_library_quality_profile(context: Optional[dict], libraries=None) -> None:
    """Stamp the destination library's quality profile onto ``track_info`` when
    the item doesn't already carry one.

    Deliberately reuses the EXISTING per-item mechanism
    (``track_info.quality_profile_id`` → ``pipeline._resolve_context_quality_profile``)
    rather than adding a second one. That mechanism already governs the whole
    pipeline — quality gate, AcoustID strictness, deep verify, replace-lower,
    downsample, lossy copy — so a library profile inherits all of it for free,
    and there's no second place for the two to disagree.

    An item that already names a profile wins: a wishlist row's own profile is
    a more specific statement than the library's default.
    """
    if not isinstance(context, dict):
        return
    track_info = context.get("track_info")
    if not isinstance(track_info, dict):
        return
    if track_info.get("quality_profile_id") not in (None, ""):
        return
    lib = resolve_music_library(context, libraries)
    if lib and lib.get("quality_profile_id") not in (None, ""):
        track_info["quality_profile_id"] = lib["quality_profile_id"]


__all__ = [
    "apply_library_quality_profile",
    "resolve_music_destination",
    "resolve_music_library",
]
