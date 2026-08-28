"""Post-process a finished video download into the library — the Radarr/Sonarr step.

On completion the monitor hands us the located file. We:
  1. parse the release + SANITY-GATE it (reject non-video / samples / wrong episode /
     multi-file packs we can't safely place),
  2. build the canonical library path (``library_paths``),
  3. decide IMPORT vs UPGRADE-replace vs not-an-upgrade by looking at what is already
     in the destination folder — the filesystem is the source of truth (like Radarr),
     which avoids leaning on DB columns the schema doesn't have,
  4. COPY it in (renamed), carry sibling subtitles, on an upgrade delete the worse
     existing file, and remove the source unless it's a torrent (preserve seeding).

``plan_import`` is pure (directory reads injected via ``list_dir``); ``run_import``
executes the plan through an injected ``fs`` facade so orchestration is unit-tested
without touching disk. Isolated — sibling video modules + stdlib only; no music imports.
"""

from __future__ import annotations

import errno
import json
import os
import re
import shutil
import uuid
from typing import Any, Callable

from core.video import organization
from utils.logging_config import get_logger

from core.video.download_pipeline import basename_of

logger = get_logger("video.importer")
from core.video.library_paths import quality_full
from core.video.quality_eval import resolution_rank
from core.video.release_parse import parse_release

VIDEO_EXTS = frozenset({
    ".mkv", ".mp4", ".avi", ".m4v", ".mov", ".ts", ".wmv",
    ".mpg", ".mpeg", ".webm", ".flv", ".m2ts",
})
SUB_EXTS = frozenset({".srt", ".sub", ".ass", ".ssa", ".idx", ".vtt", ".smi"})

_SAMPLE_MAX_BYTES = 150 * 1024 * 1024   # a "sample"-named file under this is a sample
_SAMPLE = re.compile(r"(^|[.\-_ ])sample([.\-_ ]|$)", re.I)

# A probed runtime under this (seconds) is a sample/clip, not the real thing. Movies
# get a generous floor; episodes vary wildly (shorts/cartoons) so only the absurdly
# short get caught.
_RUNTIME_FLOOR = {"movie": 15 * 60, "episode": 90}

# Source ranking for the upgrade comparison (mirrors the quality ladder order).
_SRC_RANK = {"remux": 6, "bluray": 5, "web-dl": 4, "webrip": 3, "hdtv": 2, "dvd": 1}


def ext_of(path: Any) -> str:
    """Lower-cased extension (with dot) of a path's basename, '' if none."""
    return os.path.splitext(basename_of(path))[1].lower()


def is_video(path: Any) -> bool:
    return ext_of(path) in VIDEO_EXTS


def is_sample(name: Any, size_bytes: Any) -> bool:
    """A 'sample'-tagged file that's also small (or of unknown size) is a sample."""
    if not _SAMPLE.search(basename_of(name)):
        return False
    try:
        sz = int(size_bytes or 0)
    except (TypeError, ValueError):
        sz = 0
    return sz == 0 or sz < _SAMPLE_MAX_BYTES


def quality_score(parsed: Any) -> int:
    """A self-contained quality score (resolution dominates, source breaks ties) used
    only for the local upgrade comparison. Higher = better."""
    parsed = parsed if isinstance(parsed, dict) else {}
    return resolution_rank(parsed.get("resolution")) * 10 + _SRC_RANK.get(parsed.get("source"), 0)


def _scope_of(dl: dict) -> str:
    """The import scope from the search context, falling back to the download kind.
    Only 'movie' and 'episode' are placeable; packs/youtube are gated out upstream."""
    ctx = _search_ctx(dl)
    sc = str(ctx.get("scope") or "").lower()
    if sc in ("movie", "episode", "season", "series"):
        return sc
    k = str(dl.get("kind") or "").lower()
    if k == "movie":
        return "movie"
    if k in ("show", "tv", "episode"):
        return "episode"
    return k or "movie"


def _search_ctx(dl: dict) -> dict:
    try:
        ctx = json.loads((dl or {}).get("search_ctx") or "{}")
        return ctx if isinstance(ctx, dict) else {}
    except (ValueError, TypeError):
        return {}


def _ctx(dl: dict) -> dict:
    sc = _search_ctx(dl)
    return {
        "title": sc.get("title") or (dl or {}).get("title") or "",
        "year": sc.get("year") if sc.get("year") is not None else (dl or {}).get("year"),
        "season": sc.get("season"),
        "episode": sc.get("episode"),
        "episode_title": sc.get("episode_title"),
        "air_date": sc.get("air_date"),
    }


def _reject(reason: str, bad_release: bool = False) -> dict:
    """``bad_release=True`` marks rejects where the FILE ITSELF is junk (sample /
    corrupt / fake / not a video) — the monitor auto-blocklists those releases so
    the next search never re-picks them. Context rejects (pack, wrong episode,
    not-an-upgrade, already owned) stay untagged: the release is fine."""
    return {"action": "reject", "reason": reason, "manual": True, "bad_release": bad_release}


def _existing_match(scope: str, dest_dir: str, ctx: dict, list_dir: Callable) -> str | None:
    """The basename of a file ALREADY in the destination folder that represents this
    same item (any video for a movie; a matching SxxExx for an episode), or None.
    ``list_dir(dir)`` yields basenames; a missing dir yields nothing."""
    try:
        names = [str(n) for n in (list_dir(dest_dir) or [])]
    except Exception:   # noqa: BLE001 - a missing/denied dir simply means "nothing there"
        return None
    vids = [n for n in names if ext_of(n) in VIDEO_EXTS and not is_sample(n, None)]
    if scope == "movie":
        return max(vids, key=len) if vids else None
    if scope == "episode":
        try:
            ws, we = int(ctx.get("season")), int(ctx.get("episode"))
        except (TypeError, ValueError):
            return None
        for n in vids:
            # span-aware (P8): an existing 'S01E01-E02' file already covers E02
            p = parse_release(n)
            if p.get("season") == ws and p.get("episode") is not None \
                    and p["episode"] <= we <= (p.get("episode_end") or p["episode"]):
                return n
        return None
    return None


def plan_import(dl: dict, src_path: str, *, list_dir: Callable, probe: dict | None = None,
                settings: dict | None = None, force: bool = False,
                override: dict | None = None, library_dir: str | None = None,
                lock_reason: str | None = None, identity: dict | None = None) -> dict:
    """Decide what to do with a finished download. Returns one of:

      {"action": "import",  "dest": {...}, "quality_label": str}
      {"action": "upgrade", "dest": {...}, "replace_path": str, "quality_label": str}
      {"action": "reject",  "reason": str, "manual": True}

    Pure: all directory reads go through ``list_dir`` (injected). ``probe`` is the
    ffprobe ``mediainfo`` result (or None when ffprobe is unavailable) — when present
    we trust the FILE's real resolution over the scene name and reject corrupt /
    too-short junk. ``settings`` are the user's organisation settings (naming templates
    + replace policy); None = defaults.

    MANUAL placement: ``force=True`` with an ``override`` ({scope, title, year, season,
    episode, episode_title, target_dir, media_id}) trusts the user's chosen identity —
    it skips the auto sanity-gates (sample / wrong-episode / pack / not-an-upgrade) and
    files the file exactly where they said, replacing any worse copy. ffprobe is still
    used for the true resolution, but never to reject.

    ``identity`` is the LIBRARY's own naming facts for this title
    (``video_naming_identity`` — title, premiere year, tmdb/tvdb/imdb ids, and the
    episode list), resolved by the CALLER for the same reason ``lock_reason`` is:
    this function stays pure. It exists because naming a destination from the GRAB
    instead of from the library forked every ongoing show into two folders — see
    that method for the full account. None (an unowned title) keeps the previous
    behaviour, which is all that can be known before the first file lands.

    ``lock_reason`` is the "Lock automatic edits" verdict, resolved by the CALLER
    (this function stays pure and has no DB). A non-empty string refuses the
    placement outright — before any destination is computed, so a mis-identified
    release cannot so much as name a file inside locked content. ``force`` bypasses
    it deliberately: a manual placement IS the review the lock exists to demand."""
    dl = dl or {}
    settings = organization.normalize(settings)
    override = override or {}
    scope = str(override.get("scope") or _scope_of(dl)).lower() if force else _scope_of(dl)
    name = basename_of(src_path)
    ext = ext_of(src_path)
    parsed = parse_release(dl.get("release_title") or name)
    ctx = _ctx(dl)
    # The library's own title and year — the two naming facts the grab gets wrong,
    # its "year" being the year this EPISODE aired rather than the year the series
    # began. Deliberately nothing else: season, episode and air_date are what the
    # sanity gates below judge the release against, so those keep coming from the
    # grab and a library row cannot talk the importer into accepting a release it
    # had already decided was the wrong episode. Applied before the force overlay,
    # so a manual placement — itself a deliberate statement of identity — wins.
    _ident = identity if isinstance(identity, dict) else {}
    for _k, _v in (("title", _ident.get("title")), ("year", _ident.get("year"))):
        if _v not in (None, ""):
            ctx[_k] = _v
    if force:   # the user told us what it is — let their identity win
        for k in ("title", "year", "season", "episode", "episode_title"):
            if override.get(k) is not None:
                ctx[k] = override.get(k)
    # The episode's own title, looked up AFTER the numbering is final: a manual
    # placement happens precisely when the detected episode was wrong, and reading
    # the library at the pre-override number would name the corrected file after
    # the episode it was mistaken for. Fills a gap only — the grab seldom carries
    # a title, which is why a newly imported episode had none in its filename
    # until some later rename pass put it back.
    _ep = (_ident.get("episodes") or {}).get((ctx.get("season"), ctx.get("episode")))
    if _ep and _ep.get("episode_title") and not ctx.get("episode_title"):
        ctx["episode_title"] = _ep["episode_title"]

    if not is_video(src_path):   # can't place a non-video, even on a forced import
        return _reject("Not a video file (%s)" % (ext or "no extension"), bad_release=True)
    # The import lock, checked BEFORE anything else decides where this would go.
    # Not tagged bad_release: the release may be perfectly good and simply
    # mis-identified — blocklisting it would punish the file for the lock.
    if lock_reason and not force:
        return _reject("%s. Place it by hand once you have checked it." % lock_reason)
    if not force:
        if is_sample(name, dl.get("size_bytes")):
            return _reject("Looks like a sample, not the feature", bad_release=True)
        if scope not in ("movie", "episode"):
            return _reject("Season/complete packs need manual import")
        if scope == "episode":
            if ctx.get("season") is None or ctx.get("episode") is None:
                return _reject("Missing season/episode info")
            # If the release name itself names a DIFFERENT episode, don't mis-file it.
            # A multi-episode file (S01E01E02, P8) counts as a match for any episode
            # it spans; a date-named daily file matches on the episode's air date.
            if parsed.get("episode") is not None:
                span_end = parsed.get("episode_end") or parsed.get("episode")
                numbering_ok = (parsed.get("season") == ctx.get("season")
                                and parsed.get("episode") <= ctx.get("episode") <= span_end)
                date_ok = bool(ctx.get("air_date")
                               and parsed.get("air_date") == ctx.get("air_date"))
                if not numbering_ok and not date_ok:
                    return _reject("Release is S%02dE%02d, not the episode requested"
                                   % (parsed.get("season") or 0, parsed.get("episode") or 0))
    else:
        if scope not in ("movie", "episode"):
            return _reject("Pick a movie or an episode to place this file")
        if scope == "episode" and (ctx.get("season") is None or ctx.get("episode") is None):
            return _reject("Pick a season and episode to place this file")

    # ffprobe verification — best-effort; on a forced placement we use the real
    # resolution but never reject on it (the user has decided).
    if probe is not None:
        if not force:
            if not probe.get("ok"):
                return _reject("No readable video stream — corrupt or fake file", bad_release=True)
            dur = probe.get("duration_sec") or 0
            floor = _RUNTIME_FLOOR.get(scope)
            if floor and 0 < dur < floor:
                return _reject("Runtime is only %d min — looks like a sample/clip, not the %s"
                               % (int(dur // 60), scope), bad_release=True)
        # Trust the FILE over the (often lying) scene name: real resolution always,
        # real codec only when the name didn't carry one.
        parsed = dict(parsed)
        if probe.get("resolution"):
            parsed["resolution"] = probe["resolution"]
        if probe.get("aspect"):
            parsed["aspect"] = probe["aspect"]
        if probe.get("video_codec") and not parsed.get("codec"):
            parsed["codec"] = probe["video_codec"]
        # MediaInfo naming tokens ({Mediainfo AudioChannels} and friends). These
        # come from the container ONLY — there is no name-derived fallback for
        # them, because a filename asserting '7.1' is exactly the kind of claim
        # probing exists to check. Absent ffprobe they stay empty and their
        # template groups collapse, which is the honest outcome.
        for key in ("audio_codec", "audio_channels", "audio_languages",
                    "video_bit_depth", "dynamic_range_type"):
            if probe.get(key):
                parsed[key] = probe[key]

    root = (override.get("target_dir") if force else None) or dl.get("target_dir") or ""
    if not root:
        return _reject("No library folder configured for this type")

    media_id = override.get("media_id") if force else dl.get("media_id")
    # A FORCED placement's media_id is a TMDB id by construction — the Place
    # dialog resolves the title against TMDB and sends that id (it is the same
    # value the endpoint feeds to root_folder_id_for_tmdb). Without this a manual
    # placement of a title not yet in the library would lose the one id it does
    # know, which is the case the dialog exists to serve.
    _tmdb_sourced = bool(force) or str(dl.get("media_source") or "").lower() == "tmdb"
    quality = quality_full(parsed)
    # A multi-episode file is NAMED by its full span (S01E01-E02, the Sonarr/Plex
    # convention) even though the download row's identity is one episode of it.
    ep, ep_end = ctx.get("episode"), None
    _ov_end = override.get("episode_end") if force else None
    if _ov_end is not None:
        # A pack member's span, supplied by the caller that already parsed it.
        # Re-deriving it below fails under a season renumber, because the guard
        # there requires the parsed season to agree with the one being filed —
        # and disagreeing is the entire point of a renumber. Without this a
        # renumbered S08E01-E02 would be filed as a single S07E01.
        try:
            _end = int(_ov_end)
        except (TypeError, ValueError):
            _end = None
        if _end is not None and _end != ctx.get("episode"):
            ep_end = _end
    elif scope == "episode" and parsed.get("episode_end") \
            and parsed.get("season") == ctx.get("season"):
        ep, ep_end = parsed.get("episode"), parsed.get("episode_end")
    fields = {
        "title": ctx.get("title"), "year": ctx.get("year"),
        "series": ctx.get("title"), "season": ctx.get("season"),
        "episode": ep, "episode_end": ep_end, "episode_title": ctx.get("episode_title"),
        "air_date": ctx.get("air_date"),
        "quality": quality, "resolution": parsed.get("resolution"),
        "source": parsed.get("source"), "codec": parsed.get("codec"),
        # Ids come from the LIBRARY row, falling back to ``media_id`` only when it
        # IS a TMDB id (a tmdb-sourced grab of a title not on disk yet — the first
        # episode of a new show, which is precisely the import that used to leave
        # an empty '(tmdb-)' for the media server to guess at). ``media_id`` was
        # previously written into ``tvdbid`` for every episode: it is a TMDB id or
        # a local row id and never a TVDB one, so that asserted an id that was
        # false rather than merely missing. Only a real tvdb_id goes there now.
        "tmdbid": _ident.get("tmdbid") or (media_id if _tmdb_sourced else None),
        "tvdbid": _ident.get("tvdbid"),
        "imdbid": _ident.get("imdbid"),
        # Sonarr/Radarr {Token} inputs. Everything here is optional — a template
        # that doesn't mention them is byte-for-byte unaffected, and a token
        # whose value is missing collapses its own group.
        "absolute": ctx.get("absolute"),
        "release_group": parsed.get("group"),
        "edition": parsed.get("edition"),
        "three_d": parsed.get("three_d"),
        "original_title": dl.get("release_title") or name,
        "original_filename": name,
        # Scored by the monitor, which has the DB and the profile; plan_import
        # stays pure and just reads what it was handed.
        "custom_formats": dl.get("_custom_formats"),
        "audio_codec": parsed.get("audio_codec") or parsed.get("audio"),
        "audio_channels": parsed.get("audio_channels"),
        "audio_languages": parsed.get("audio_languages"),
        "video_bit_depth": parsed.get("video_bit_depth"),
        # The FILE's dynamic range when probed; the name's claim only as a
        # last resort, flagged by being the coarser 'HDR' rather than a type.
        "dynamic_range_type": (parsed.get("dynamic_range_type")
                               or (str(parsed.get("hdr")).upper() if parsed.get("hdr") else None)),
    }
    dest = organization.render_path(scope, root, fields, settings, ext)
    # The library already owns this item at a REAL, resolved location
    # (``library_dir`` — the server-stored path re-rooted by the video path
    # resolver): upgrades must land beside/replace THAT copy, not fork a
    # second one in the template location. Templated filename kept; a forced
    # manual placement still goes exactly where the user pointed.
    if library_dir and not force:
        dest = {"dir": library_dir, "filename": dest["filename"],
                "path": os.path.join(library_dir, dest["filename"])}
    # WHERE THIS FILE IS ABOUT TO GO, and on whose authority. The video import
    # path logged nothing at all: across eight days of a real app.log there was
    # not one line naming a destination, so "it got filed as the wrong show" could
    # only be investigated by reading the database afterwards and guessing. The
    # music side has logged `Resolved path:` on every import for years.
    #
    # The SOURCE matters as much as the path. A destination built from the grab
    # rather than from the library row is exactly how a show acquires a second
    # folder, so the line says which one it was — and names the ids it used, since
    # an absent tmdb id is what leaves a folder the media server has to guess at.
    try:
        if library_dir and not force:
            _why = "existing library copy"
        elif force:
            _why = "manual placement"
        elif _ident:
            _why = "library row (tmdb=%s tvdb=%s year=%s)" % (
                _ident.get("tmdbid"), _ident.get("tvdbid"), _ident.get("year"))
        else:
            _why = "the grab alone — this title is not in the library yet"
        logger.info("[Placement] %s %r -> %s  (naming from: %s)",
                    scope, ctx.get("title") or "?", dest["path"], _why)
    except Exception:   # noqa: BLE001 - a log line must never cost an import
        pass

    # Where poster.jpg goes: the movie folder, or the SHOW root for an episode
    # (parent of the Season folder) — so it isn't dropped per-season.
    artwork_dir = dest["dir"] if scope == "movie" else os.path.dirname(dest["dir"])

    existing = _existing_match(scope, dest["dir"], ctx, list_dir)
    if existing:
        # A forced placement replaces whatever's there (the user chose to put it here).
        if force:
            return {"action": "upgrade", "dest": dest, "quality_label": quality,
                    "replace_path": os.path.join(dest["dir"], existing), "artwork_dir": artwork_dir}
        # Idempotent re-import: the file already sits at the EXACT path we'd write. This is
        # the crash-recovery case — an import that finished the copy but died before the row
        # flipped to 'completed' (e.g. a restart), so the monitor re-drives it. The goal (this
        # item in the library, here) is already met → report it done instead of the misleading
        # "not an upgrade" failure that would otherwise leave a landed download looking failed.
        if existing == dest["filename"]:
            return {"action": "already_placed", "dest": dest, "quality_label": quality,
                    "artwork_dir": artwork_dir}
        if not settings.get("replace_existing", True):
            return _reject("Already in the library (%s) — replace is turned off" % existing)
        new_score = quality_score(parsed)
        old_score = quality_score(parse_release(existing))
        if new_score > old_score:
            return {"action": "upgrade", "dest": dest, "quality_label": quality,
                    "replace_path": os.path.join(dest["dir"], existing), "artwork_dir": artwork_dir}
        return _reject("Not an upgrade over the copy already in the library (%s)" % existing)

    return {"action": "import", "dest": dest, "quality_label": quality, "artwork_dir": artwork_dir}


def plan_subs(src_path: str, dest_path: str, list_dir: Callable) -> list:
    """Sibling subtitle files to carry alongside the video, renamed to match the
    destination stem (preserving any language suffix, e.g. '.en.srt'). Returns a list
    of (src_abs, dest_abs) pairs. ``list_dir`` lists the SOURCE directory."""
    src_dir = os.path.dirname(src_path) or "."
    v_stem = os.path.splitext(basename_of(src_path))[0]
    d_stem = os.path.splitext(basename_of(dest_path))[0]
    d_dir = os.path.dirname(dest_path)
    out = []
    try:
        names = [str(n) for n in (list_dir(src_dir) or [])]
    except Exception:   # noqa: BLE001
        return out
    for n in names:
        if ext_of(n) not in SUB_EXTS:
            continue
        stem, ext = os.path.splitext(n)
        if stem == v_stem:
            extra = ""                                  # movie.srt → <dest>.srt
        elif stem.startswith(v_stem + "."):
            extra = stem[len(v_stem):]                  # movie.en.srt → <dest>.en.srt
        else:
            continue
        out.append((os.path.join(src_dir, n), os.path.join(d_dir, d_stem + extra + ext)))
    return out


def run_import(dl: dict, src_path: str, *, fs: Any, prober: Callable | None = None,
               settings: dict | None = None, force: bool = False,
               override: dict | None = None, library_dir: str | None = None,
               recycle: Callable | None = None, lock_reason: str | None = None,
               identity: dict | None = None) -> dict:
    """Execute the import and return a DB patch dict for the download row.

    ``fs`` is an injected facade with: ``list_dir(dir)->iterable[name]``,
    ``makedirs(dir)``, ``copy(src, dst)``, ``move(src, dst)``, ``remove(path)``.
    ``prober(path)->mediainfo`` is an optional ffprobe hook (None = skip verification).
    ``recycle(path)`` (optional) replaces the upgrade-delete of the old library
    copy with a move-to-trash (core.video.recycle.discarder); None = hard remove.
    ``settings`` are the user's organisation settings (transfer mode, subtitle carry);
    None = defaults. ``force``/``override`` drive a MANUAL placement (see ``plan_import``).
    A reject becomes an ``import_failed`` row with ``dest_path`` pointing at the file's
    current (unplaced) location so the Import page can resolve it; a success becomes a
    ``completed`` row with ``dest_path`` set to its final home."""
    settings = organization.normalize(settings)
    probe_info = None
    if prober is not None:
        try:
            probe_info = prober(src_path)
        except Exception:   # noqa: BLE001 - a probe crash must not block the import
            probe_info = None
    plan = plan_import(dl, src_path, list_dir=fs.list_dir, probe=probe_info,
                       settings=settings, force=force, override=override,
                       library_dir=library_dir, lock_reason=lock_reason,
                       identity=identity)
    if plan["action"] == "reject":
        # Leave the file where it is; remember WHERE so manual import can find it.
        # _bad_release is transient (stripped by update_video_download): it tells
        # the monitor to blocklist this exact release so it's never re-picked.
        return {"status": "import_failed", "progress": 100.0, "error": plan["reason"],
                "dest_path": src_path, "_bad_release": bool(plan.get("bad_release"))}

    if plan["action"] == "already_placed":
        # The file is already at its destination (a re-driven, crash-interrupted import) —
        # nothing to copy, and it's not an upgrade. Report completed at the placed path so a
        # download whose file genuinely landed stops showing as in-progress / failed.
        return {"status": "completed", "progress": 100.0, "dest_path": plan["dest"]["path"],
                "quality_label": plan.get("quality_label") or dl.get("quality_label")}

    dest = plan["dest"]
    move_mode = settings.get("transfer_mode") == "move"
    try:
        fs.makedirs(dest["dir"])
        if move_mode:
            fs.move(src_path, dest["path"])
        else:
            fs.copy(src_path, dest["path"])
        if settings.get("carry_subtitles", True):
            for sub_src, sub_dst in plan_subs(src_path, dest["path"], fs.list_dir):
                try:
                    fs.copy(sub_src, sub_dst)
                except Exception:   # noqa: BLE001 - a subtitle that won't copy isn't fatal
                    pass
        if plan["action"] == "upgrade" and plan.get("replace_path"):
            try:
                (recycle or fs.remove)(plan["replace_path"])
            except Exception:   # noqa: BLE001 - failing to delete the old file isn't fatal
                pass
        # Copy mode reclaims the download copy UNLESS it's a torrent (keep seeding) or a
        # manually-added file (the user pointed at a file that's theirs, not a download
        # client's temp copy — copying it into the library must never also delete it);
        # move mode already relocated it.
        if not move_mode and str(dl.get("source") or "").lower() not in ("torrent", "manual"):
            try:
                fs.remove(src_path)
            except Exception:   # noqa: BLE001
                pass
    except Exception as e:   # noqa: BLE001 - any copy/mkdir failure → manual import
        return {"status": "import_failed", "progress": 100.0, "error": "Import failed: " + str(e),
                "dest_path": src_path}

    return {"status": "completed", "progress": 100.0, "dest_path": dest["path"],
            "quality_label": plan.get("quality_label") or dl.get("quality_label"),
            # transient (underscore = stripped by update_video_download): lets the
            # monitor fire the 'Quality Upgrade Landed' event trigger
            "_upgraded": plan["action"] == "upgrade"}


# ── season packs ─────────────────────────────────────────────────────────────
# A pack is a FOLDER of episodes, but everything above is built around one
# download → one file. Rather than teach plan_import about packs (and fork every
# naming / upgrade / subtitle / recycle / seeding rule in it), we fan the pack
# out: each member file is parsed on its OWN name and handed to the very same
# single-file importer as a synthetic per-episode download. Every behaviour the
# episode path already has is inherited rather than reimplemented, and a pack
# whose members are half-upgrades gets the per-episode upgrade decision for free.

def pack_members(src_dir: str, lister: Callable, *, size_of: Callable | None = None) -> list:
    """Importable episode files inside a pack, in (season, episode) order.

    ``lister(dir)`` yields full paths recursively (same injection as
    find_completed_file). Drops non-video files, samples, and anything whose name
    carries no episode number — extras, trailers, "behind the scenes" and the
    stray .nfo all fail that test, so they are never mistaken for an episode.
    Specials (season 0) are kept: they are real episodes with a real destination.
    """
    out = []
    for path in lister(src_dir) or []:
        if not is_video(path):
            continue
        name = basename_of(path)
        if is_sample(name, (size_of(path) if size_of else 0)):
            continue
        p = parse_release(name)
        if p.get("episode") is None or p.get("season") is None:
            continue
        out.append({"path": path, "season": p["season"], "episode": p["episode"],
                    "episode_end": p.get("episode_end") or p["episode"], "parsed": p})
    out.sort(key=lambda m: (m["season"], m["episode"]))
    return out


def _member_download(dl: dict, member: dict) -> dict:
    """A synthetic single-episode download row for one pack member.

    release_title becomes the MEMBER's filename: the pack's own name has no
    episode number in it, so parsing that would give every member the same (and
    wrong) identity. search_ctx is rewritten to scope='episode' with this file's
    numbers, which is exactly what plan_import's episode branch expects.
    """
    ctx = dict(_search_ctx(dl))
    ctx.update({"scope": "episode", "season": member["season"], "episode": member["episode"]})
    ctx.pop("air_date", None)      # a pack member is identified by numbering, not date
    out = dict(dl)
    out["search_ctx"] = json.dumps(ctx)
    out["release_title"] = basename_of(member["path"])
    return out


def run_season_import(dl: dict, src_dir: str, *, fs: Any, lister: Callable,
                      prober: Callable | None = None, settings: dict | None = None,
                      library_dir: str | None = None, recycle: Callable | None = None,
                      size_of: Callable | None = None, force: bool = False,
                      override: dict | None = None,
                      lock_check: Callable | None = None,
                      identity: dict | None = None) -> dict:
    """Import every episode in a season/series pack. Returns a DB patch dict.

    Partial success is SUCCESS: a pack advertised as S01 that ships 8 of 12
    episodes, or one where four episodes are already in the library at better
    quality, has still done its job. The patch reports what landed. Only a pack
    from which nothing at all could be imported is a failure — and it keeps the
    source path so the Import page can pick it up manually, same as any other
    failed import.

    ``force``/``override`` drive a MANUAL placement of a whole folder: the user
    has chosen the show, so every member is placed against that identity.

    EPISODE numbers always come from each FILE. The whole point of a pack is
    that its members differ, and stamping one episode number across all of them
    would file the entire season on top of itself.

    SEASON is not the same kind of fact. It is a property of the PACK, not of
    its members, so an override renumbers the lot — which is what makes a folder
    whose filenames say S08 importable as season 7. Release groups numbering
    differently from TMDB make that routine, and it used to be impossible: the
    number was silently replaced by the one parsed out of each filename, so the
    placement landed exactly where the user had just said it should not.

    A renumber is REFUSED across a pack spanning several seasons. One number
    cannot describe them, and applying it anyway would file S07E01 and S08E01 at
    the same path — the second overwriting the first.
    """
    settings = organization.normalize(settings)
    members = pack_members(src_dir, lister, size_of=size_of)
    if not members:
        return {"status": "import_failed", "progress": 100.0, "dest_path": src_dir,
                "error": "No episode files found in this pack"}

    # The user's season, if they gave one. Resolved before the loop because it
    # is a fact about the pack: the multi-season refusal has to be answered from
    # ALL the members, not discovered halfway through copying them.
    renumber = None
    if override and override.get("season") is not None:
        try:
            renumber = int(override["season"])
        except (TypeError, ValueError):
            renumber = None
    if renumber is not None:
        _seasons = sorted({m["season"] for m in members})
        if len(_seasons) > 1:
            return {"status": "import_failed", "progress": 100.0, "dest_path": src_dir,
                    "error": "This folder holds seasons %s. A season number can only be "
                             "reassigned to a pack that is all one season — applying one "
                             "number to several would file their episodes on top of each "
                             "other." % ", ".join("%d" % s for s in _seasons)}

    imported, upgraded, failed, dests = 0, 0, [], []
    for m in members:
        member_override = None
        if override:
            member_override = dict(override)
            member_override.update({
                "scope": "episode",
                "season": m["season"] if renumber is None else renumber,
                "episode": m["episode"],
                # The member's own span, handed over rather than re-parsed. Under
                # a renumber the filename's season and the one being filed under
                # deliberately disagree, which is exactly what the span check in
                # plan_import treats as a reason to distrust the parse.
                "episode_end": m.get("episode_end"),
            })
            # The chosen show supplies the title; only the pack itself knows the
            # per-episode title, and a stale one from the dialog would be worse
            # than none (it names the file).
            member_override.pop("episode_title", None)
        try:
            # Per MEMBER, not once for the pack: a season lock protects its own
            # season, so a pack spanning a locked and an unlocked season imports
            # the unlocked half and refuses the rest.
            member_lock = lock_check(m.get("season")) if lock_check else None
            # One identity for the whole pack — it carries every episode of the
            # show, so each member still names itself from its OWN row.
            patch = run_import(_member_download(dl, m), m["path"], fs=fs, prober=prober,
                               settings=settings, library_dir=library_dir, recycle=recycle,
                               force=force, override=member_override,
                               lock_reason=member_lock, identity=identity)
        except Exception as e:      # noqa: BLE001 - one bad member must not abort the pack
            failed.append("S%02dE%02d: %s" % (m["season"], m["episode"], e))
            continue
        if patch.get("status") == "completed":
            imported += 1
            if patch.get("_upgraded"):
                upgraded += 1
            if patch.get("dest_path"):
                dests.append(patch["dest_path"])
        else:
            failed.append("S%02dE%02d: %s" % (m["season"], m["episode"],
                                              patch.get("error") or "not imported"))

    if not imported:
        return {"status": "import_failed", "progress": 100.0, "dest_path": src_dir,
                "error": "Nothing in the pack could be imported — " + ("; ".join(failed[:3])
                                                                       or "no usable episodes")}
    return {
        "status": "completed", "progress": 100.0,
        # The season folder, not one episode — this is what the UI links to.
        "dest_path": os.path.dirname(dests[0]) if dests else src_dir,
        "quality_label": dl.get("quality_label"),
        "_upgraded": upgraded > 0,
        # transient (underscore-stripped on write): drives the completion message
        # and lets the caller reconcile the wishlist for exactly what landed.
        "_pack_imported": imported,
        "_pack_total": len(members),
        "_pack_failed": failed,
        "_pack_episodes": [(m["season"], m["episode"]) for m in members],
    }


def atomic_verified_copy(src: str, dst: str) -> None:
    """Copy ``src`` to ``dst`` without ever exposing a partial file at the
    final name, and refuse to accept a short copy.

    The library folders are watched by Plex/Jellyfin: a bare
    ``shutil.copy2(src, final_name)`` leaves the final ``.mkv`` name as a
    growing partial file for the whole multi-GB copy (minutes over SMB), so
    the server can index/analyze a truncated file — playback then skips
    like corruption even after the copy finished — and an interrupted copy
    leaves a permanently truncated file that looks complete. Same disease
    the music side cured with ``atomic_copy_to_staging``.

    The in-flight file is ``<dest>.tmp.<random>`` in the DESTINATION
    directory (so the final ``os.replace`` is a same-filesystem atomic
    rename, and the random suffix means media servers never index it).
    The byte count is verified before the rename — a silently short copy
    (SMB hiccup that didn't raise) must never be promoted to the real name.
    """
    d = str(dst)
    tmp = os.path.join(os.path.dirname(d) or ".",
                       os.path.basename(d) + ".tmp." + uuid.uuid4().hex[:8])
    try:
        shutil.copy2(src, tmp)
        src_size = os.path.getsize(src)
        tmp_size = os.path.getsize(tmp)
        if src_size != tmp_size:
            raise OSError(
                f"short copy: {tmp_size} of {src_size} bytes for {os.path.basename(d)}")
        os.replace(tmp, d)
    except BaseException:
        try:
            if os.path.exists(tmp):
                os.remove(tmp)
        except OSError:
            pass
        raise


def atomic_verified_move(src: str, dst: str) -> None:
    """Move with the same guarantees as ``atomic_verified_copy``.

    Same filesystem → one atomic ``os.replace`` (instant, nothing partial
    ever exists). Cross-device (download drive → SMB library) → verified
    copy to a temp name + atomic rename, and the source is removed only
    AFTER the destination verified — never lose the only good copy."""
    os.makedirs(os.path.dirname(str(dst)) or ".", exist_ok=True)
    try:
        os.replace(str(src), str(dst))
        return
    except OSError as e:
        if getattr(e, "errno", None) != errno.EXDEV:
            raise            # real error (perms/space/missing) — not cross-device
    atomic_verified_copy(src, dst)
    os.remove(str(src))


class _RealFS:
    """The production filesystem facade for ``run_import`` (os/shutil)."""

    @staticmethod
    def list_dir(path):
        try:
            return os.listdir(str(path or ""))
        except OSError:
            return []

    @staticmethod
    def makedirs(path):
        os.makedirs(str(path or "."), exist_ok=True)

    @staticmethod
    def copy(src, dst):
        atomic_verified_copy(src, dst)

    @staticmethod
    def move(src, dst):
        atomic_verified_move(src, dst)

    @staticmethod
    def save_url(url, dst):
        import urllib.request
        req = urllib.request.Request(url, headers={"User-Agent": "Commissary"})
        with urllib.request.urlopen(req, timeout=20) as resp, open(dst, "wb") as f:
            shutil.copyfileobj(resp, f)

    @staticmethod
    def write_text(path, content):
        with open(path, "w", encoding="utf-8") as f:
            f.write(content)

    @staticmethod
    def remove(path):
        os.remove(path)


def real_fs() -> _RealFS:
    return _RealFS()


__all__ = [
    "VIDEO_EXTS", "SUB_EXTS", "ext_of", "is_video", "is_sample", "quality_score",
    "plan_import", "plan_subs", "run_import", "real_fs",
    "pack_members", "run_season_import",
]
