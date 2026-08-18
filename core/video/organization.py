"""Video library-organisation settings: naming templates + post-process toggles.

Mirrors the MUSIC side's file-organisation standard — editable ``$token`` path
templates with per-component sanitisation and dangling-separator cleanup — but for
video's movie/episode shape, plus the optional post-process behaviours the user can
turn on and off.

Settings (persisted as JSON in video.db ``video_settings['organization']``):
  - ``movie_template``      : path template for movies   (folders via '/', last = file)
  - ``episode_template``    : path template for episodes
  - ``verify_with_ffprobe`` : probe the real file (true quality + reject junk)
  - ``replace_existing``    : upgrade-replace a worse copy already in the library
  - ``transfer_mode``       : 'copy' (reclaim source unless torrent) | 'move'
  - ``carry_subtitles``     : bring sibling .srt/.ass alongside the video

Template tokens
  Movies:   $title $titlefirst $year $quality $resolution $source $codec $edition
            $tmdbid $imdbid
  Episodes: $series $season $seasonraw $episode $episodetitle $year $quality
            $resolution $source $codec $tvdbid $tmdbid $imdbid $airdate
  ($season/$episode are zero-padded to 2; $seasonraw is the bare number; a
  multi-episode file renders its span through $episode — 'S01E01-E02'; $airdate
  is the episode's YYYY-MM-DD air date, for daily-show naming.)

Pure data + a pure renderer (no DB, no FS) so it's unit-tested in isolation. Isolated —
stdlib + sibling video ``library_paths`` only; nothing from the music side.
"""

from __future__ import annotations

import json
import os
import re

from utils.logging_config import get_logger

logger = get_logger("video.organization")
from typing import Any

from core.video import naming_tokens
from core.video.library_paths import sanitize, source_label

DEFAULTS = {
    "version": 1,
    "movie_template": "$title ($year)/$title ($year) $quality",
    "episode_template": "$series/Season $season/$series - S$seasonE$episode - $episodetitle $quality",
    # YouTube channels organise as a TV show Plex/Jellyfin can index WITHOUT any
    # online agent: channel = series, season = upload YEAR, and the ytdl-sub-style
    # $sxe token (s2026e0711) in the FILENAME. Plex's Series Scanner parses that
    # structurally — the old date-only naming ("... - 2026-07-11 - ...") only works
    # for shows a metadata agent can match, and YouTube channels aren't on TVDB,
    # so those folders never indexed (the "Plex isn't picking it up" report).
    "youtube_template": "$channel/Season $year/$channel - $sxe - $title",
    "verify_with_ffprobe": True,
    "replace_existing": True,
    "transfer_mode": "copy",
    "carry_subtitles": True,
    "save_artwork": True,    # nfo + artwork sidecars on by default (cheap, local) — best-in-class
    "write_nfo": True,
    "download_subtitles": False,   # opt-in: fetches from OpenSubtitles (external, rate-limited)
    "subtitle_langs": "en",
    # Recycle bin: deletes (upgrade-replaced copies, retention-cleaned YouTube
    # episodes, dismissed imports) move into an ss_recycle folder under the
    # file's library root instead of unlinking; purged after recycle_keep_days.
    "recycle_deletes": True,
    "recycle_keep_days": 7,
    "recycle_path": "",            # optional override folder; blank = auto per-library
    # YouTube downloads (ytdl-sub parity): SponsorBlock chapter handling and
    # embedded subtitles, baked into the file at download time.
    "youtube_sponsorblock": "off",     # off | mark (chapters) | remove (cut segments)
    "youtube_embed_subs": False,       # embed subs (subtitle_langs) into the container
    "min_free_disk_gb": 0,             # refuse new grabs when the target drive has less (0 = off)
    # How many recent videos following a YouTube channel backfills — and the rolling
    # "keep the last N current" net the watchlist-channels scan uses. One knob for both
    # so they stay consistent (Settings → Library). 0 = follow with no backfill (only
    # new uploads after you follow).
    "youtube_follow_count": 5,
    # Bring a title's EXISTING files up to the current naming template before a
    # new episode is imported into it. Templates used to apply only at import
    # time, so changing one forked a show across two naming eras: the new
    # episode lands in a folder named the new way while every earlier season
    # sits in the old one, and the media server sees two shows. On by default —
    # it is a no-op for a library that already matches its template, and the
    # split it prevents is tedious to unpick by hand.
    "rename_before_import": True,
}

_TRANSFER_MODES = ("copy", "move")

# The pre-$sxe default (see youtube_template above) — recognised and upgraded at
# render time so existing saved settings get the Plex-indexable naming too.
_LEGACY_YOUTUBE_TEMPLATE = "$channel/Season $year/$channel - $date - $title"


def default_settings() -> dict:
    return dict(DEFAULTS)


def normalize(raw: Any) -> dict:
    """Coerce stored/posted settings to a valid shape, filling gaps from the default.
    Blank templates fall back to the default; never raises."""
    d = default_settings()
    if not isinstance(raw, dict):
        return d
    for key in ("movie_template", "episode_template", "youtube_template"):
        v = raw.get(key)
        if isinstance(v, str) and v.strip():
            d[key] = v.strip()
    sb = str(raw.get("youtube_sponsorblock") or "").strip().lower()
    if sb in ("off", "mark", "remove"):
        d["youtube_sponsorblock"] = sb
    for key in ("verify_with_ffprobe", "replace_existing", "carry_subtitles",
                "save_artwork", "write_nfo", "download_subtitles", "recycle_deletes",
                "youtube_embed_subs", "rename_before_import"):
        if key in raw:
            d[key] = bool(raw.get(key))
    if "recycle_keep_days" in raw:
        try:
            d["recycle_keep_days"] = max(1, min(365, int(raw.get("recycle_keep_days"))))
        except (TypeError, ValueError):
            pass
    if "recycle_path" in raw:
        d["recycle_path"] = str(raw.get("recycle_path") or "").strip()
    if "min_free_disk_gb" in raw:
        try:
            d["min_free_disk_gb"] = max(0, min(10000, float(raw.get("min_free_disk_gb") or 0)))
        except (TypeError, ValueError):
            pass
    if "youtube_follow_count" in raw:
        try:
            d["youtube_follow_count"] = max(0, min(100, int(raw.get("youtube_follow_count"))))
        except (TypeError, ValueError):
            pass
    tm = str(raw.get("transfer_mode") or "").strip().lower()
    if tm in _TRANSFER_MODES:
        d["transfer_mode"] = tm
    if "subtitle_langs" in raw:
        from core.video.subtitles import parse_langs
        d["subtitle_langs"] = ",".join(parse_langs(raw.get("subtitle_langs")))
    return d


# ``min_free_disk_gb`` is SHARED with music — same concept, and it used to exist
# twice with different defaults (music soulseek.min_free_disk_gb = 5.0, video's
# organization blob = 0). It now lives on the app-wide ``settings.min_free_disk_gb``
# and is merged into/out of this payload, so every caller
# (disk_guard.has_room(target, organization.load(db))) is unchanged.
_SHARED_MIN_FREE_KEY = "settings.min_free_disk_gb"
_MIN_FREE_PROMOTION_MARKER = "min_free_disk_promoted"
_min_free_promotion_checked = False


def _shared_min_free() -> float:
    # The STORED value: this is settings data being read back for the UI and for
    # has_room's explicit-floor path, not a live guard decision, so it must not
    # pick up the guard's test override.
    from core.disk_guard import configured_floor_gb
    return configured_floor_gb()


def _promote_min_free_once(db, stored: dict) -> None:
    """One-shot: an EXPLICIT non-zero video floor is a deliberate choice, so it
    wins and becomes the shared value. A video side left at the 0 default simply
    adopts the shared floor — which means such installs newly gain music's 5 GB
    default. That only ever REFUSES a grab (never deletes anything) and is
    reversible from the one field, so it's the safe direction."""
    global _min_free_promotion_checked
    if _min_free_promotion_checked:
        return
    try:
        if str(db.get_setting(_MIN_FREE_PROMOTION_MARKER) or "") == "1":
            _min_free_promotion_checked = True
            return
        from config.settings import config_manager
        video_floor = float(stored.get("min_free_disk_gb") or 0)
        if video_floor > 0:
            config_manager.set(_SHARED_MIN_FREE_KEY, video_floor)
        db.set_setting(_MIN_FREE_PROMOTION_MARKER, "1")
        _min_free_promotion_checked = True
    except Exception:
        logger.exception("min-free-disk promotion failed (non-fatal)")


def load(db) -> dict:
    d = default_settings()
    raw = db.get_setting("organization")
    if raw:
        try:
            d = normalize(json.loads(raw))
        except (ValueError, TypeError):
            pass
    _promote_min_free_once(db, d)
    d["min_free_disk_gb"] = _shared_min_free()
    return d


def save(db, raw: Any) -> dict:
    s = normalize(raw)
    _promote_min_free_once(db, s)
    if isinstance(raw, dict) and "min_free_disk_gb" in raw:
        from config.settings import config_manager
        config_manager.set(_SHARED_MIN_FREE_KEY, s["min_free_disk_gb"])
    stored = {k: v for k, v in s.items() if k != "min_free_disk_gb"}
    db.set_setting("organization", json.dumps(stored))
    s["min_free_disk_gb"] = _shared_min_free()
    return s


# ── the template engine (the music $token standard, video tokens) ─────────────
def _str(v: Any) -> str:
    if v is None:
        return ""
    return str(v)


def _pad2(v: Any) -> str:
    try:
        return "%02d" % int(v)
    except (TypeError, ValueError):
        return _str(v)


def _plausible_year(v: Any) -> bool:
    try:
        return 1870 <= int(v) <= 2999
    except (TypeError, ValueError):
        return False


def _ext(ext: Any) -> str:
    e = str(ext or "").strip().lower()
    if not e:
        return ""
    return e if e.startswith(".") else "." + e


def _movie_values(f: dict) -> dict:
    title = f.get("title") or "Unknown"
    return {
        "title": title,
        "titlefirst": (str(title)[:1] or "U").upper(),
        "year": _str(f.get("year")) if _plausible_year(f.get("year")) else "",
        "quality": _str(f.get("quality")),
        "resolution": _str(f.get("resolution")),
        "source": source_label(f.get("source")),
        "codec": _str(f.get("codec")).upper(),
        "edition": _str(f.get("edition")),
        "tmdbid": _str(f.get("tmdbid")),
        "imdbid": _str(f.get("imdbid")),
    }


def _episode_values(f: dict) -> dict:
    series = f.get("series") or f.get("title") or "Unknown"
    # A multi-episode file renders its whole span through the ordinary $episode
    # token: episode=1 + episode_end=2 → '01-E02', so the default template's
    # 'S$seasonE$episode' comes out as 'S01E01-E02' (the Sonarr/Plex convention).
    episode = _pad2(f.get("episode"))
    if f.get("episode_end"):
        episode = "%s-E%s" % (episode, _pad2(f.get("episode_end")))
    return {
        "series": series,
        "season": _pad2(f.get("season")),
        "seasonraw": _str(f.get("season")),
        "episode": episode,
        "episodetitle": _str(f.get("episode_title")),
        # daily-show naming (P8): $airdate = the episode's YYYY-MM-DD air date
        "airdate": str(f.get("air_date") or "")[:10],
        "year": _str(f.get("year")) if _plausible_year(f.get("year")) else "",
        "quality": _str(f.get("quality")),
        "resolution": _str(f.get("resolution")),
        "source": source_label(f.get("source")),
        "codec": _str(f.get("codec")).upper(),
        # All three ids, not just tvdbid. The brace vocabulary has offered
        # {TmdbId}/{ImdbId} for episodes all along; the $token set carried only
        # $tvdbid, so the same scheme written in $tokens silently dropped the id
        # — and a folder named for an id that renders as nothing is one the media
        # server has to guess at.
        "tvdbid": _str(f.get("tvdbid")),
        "tmdbid": _str(f.get("tmdbid")),
        "imdbid": _str(f.get("imdbid")),
    }


# ── Sonarr/Radarr {Token} vocabulary ─────────────────────────────────────────
# Names follow the guides exactly (matching is case/space-insensitive, so the
# guides' own inconsistent 'Mediainfo' vs 'MediaInfo' both resolve). Tokens
# listed in _NUMERIC_TOKENS treat ':00' as zero-padding; every other token
# treats ':90' as a length cap. See core/video/naming_tokens.

_NUMERIC_TOKENS = ("season", "episode", "absolute", "MediaInfo VideoBitDepth")

# Deliberately NOT supported: {Preferred Words}. Sonarr's release-preference
# scoring has no counterpart here, and rendering it empty would quietly drop a
# component the user's scheme asked for — an unknown token is left visible
# instead, so a paste that relies on it is obvious rather than silently wrong.


# Codec labels as the naming schemes write them. Our internal forms come from
# ffprobe ('hevc', 'eac3') and the release parser ('x264'); blanket-uppercasing
# them yields 'HEVC'/'TRUEHD', which is not what a Sonarr-shaped filename looks
# like. Anything unlisted falls back to upper-case, which is right far more
# often than not for audio formats.
_VIDEO_CODEC_LABELS = {"hevc": "x265", "h265": "x265", "x265": "x265",
                       "x264": "x264", "h264": "x264", "avc": "x264",
                       "av1": "AV1", "vp9": "VP9", "mpeg2video": "MPEG2"}
_AUDIO_CODEC_LABELS = {"truehd": "TrueHD", "eac3": "EAC3", "ac3": "AC3",
                       "dts": "DTS", "dtshd": "DTS-HD", "dts-hd": "DTS-HD",
                       "aac": "AAC", "opus": "Opus", "flac": "FLAC",
                       "vorbis": "Vorbis", "mp3": "MP3", "pcm_s24le": "PCM"}


def _codec_label(value: Any, table: dict) -> str:
    key = _str(value).strip().lower()
    if not key:
        return ""
    return table.get(key, key.upper())


def _media_tokens(f: dict) -> dict:
    """The MediaInfo/quality/release tokens shared by movies and episodes.

    Every value prefers what ffprobe read from the FILE over what the release
    name claimed, because that is the whole point of probing — a name saying
    'HDR' proves nothing, and these end up in the filename."""
    return {
        "Quality Full": _str(f.get("quality")),
        "Quality Title": _str(f.get("resolution")),
        "MediaInfo VideoCodec": _codec_label(f.get("codec"), _VIDEO_CODEC_LABELS),
        "MediaInfo AudioCodec": _codec_label(f.get("audio_codec"), _AUDIO_CODEC_LABELS),
        "MediaInfo AudioChannels": _str(f.get("audio_channels")),
        "MediaInfo AudioLanguages": _str(f.get("audio_languages")),
        "MediaInfo VideoBitDepth": _str(f.get("video_bit_depth")),
        "MediaInfo VideoDynamicRange": "HDR" if f.get("dynamic_range_type") else "",
        "MediaInfo VideoDynamicRangeType": _str(f.get("dynamic_range_type")),
        "MediaInfo 3D": "3D" if f.get("three_d") else "",
        "Release Group": _str(f.get("release_group")),
        "Custom Formats": _str(f.get("custom_formats")),
        "Edition Tags": _str(f.get("edition")),
        "Original Title": _str(f.get("original_title")),
        "Original Filename": _str(f.get("original_filename")),
        "ImdbId": _str(f.get("imdbid")),
        "TmdbId": _str(f.get("tmdbid")),
        "TvdbId": _str(f.get("tvdbid")),
    }


def _movie_brace_tokens(f: dict) -> dict:
    title = f.get("title") or "Unknown"
    year = _str(f.get("year")) if _plausible_year(f.get("year")) else ""
    return {
        **_media_tokens(f),
        "Movie Title": _str(title),
        "Movie CleanTitle": naming_tokens.clean_title(title),
        "Movie TitleThe": naming_tokens.title_the(title),
        "Movie OriginalTitle": _str(f.get("original_title")) or _str(title),
        "Movie Year": year,
        "Release Year": year,
    }


def _episode_brace_tokens(f: dict) -> dict:
    series = f.get("series") or f.get("title") or "Unknown"
    year = _str(f.get("year")) if _plausible_year(f.get("year")) else ""
    clean = naming_tokens.clean_title(series)
    # 'WithoutYear' strips a trailing '(2019)' the series title may itself carry,
    # so '{Series CleanTitleWithoutYear} {(Series Year)}' can't print it twice.
    without_year = re.sub(r"\s*\((?:19|20)\d{2}\)\s*$", "", clean).strip()
    episode = _pad2(f.get("episode"))
    if f.get("episode_end"):
        episode = "%s-E%s" % (episode, _pad2(f.get("episode_end")))
    ep_title = _str(f.get("episode_title"))
    return {
        **_media_tokens(f),
        "Series Title": _str(series),
        "Series CleanTitle": clean,
        "Series CleanTitleWithoutYear": without_year,
        "Series TitleThe": naming_tokens.title_the(series),
        "Series TitleYear": ("%s (%s)" % (without_year, year)) if year else without_year,
        "Series Year": year,
        "season": _str(f.get("season")),
        "episode": episode,
        "absolute": _str(f.get("absolute")),
        "Episode Title": ep_title,
        "Episode CleanTitle": naming_tokens.clean_title(ep_title),
        "Air-Date": str(f.get("air_date") or "")[:10],
        "AirDate": str(f.get("air_date") or "")[:10].replace("-", "."),
    }


_BRACE_TOKENS = {"movie": _movie_brace_tokens, "episode": _episode_brace_tokens}


# The sample title the settings-page preview renders. Deliberately carries a
# value for EVERY token, including the awkward ones (an apostrophe for
# CleanTitle, a multi-episode span, a Dolby Vision 10-bit HEVC release), so the
# preview shows what a template does at its fullest rather than a tidy case.
PREVIEW_SAMPLES = {
    "movie": {
        "title": "The Director's Cut", "year": 1999, "quality": "Bluray-2160p",
        "resolution": "2160p", "source": "bluray", "codec": "hevc",
        "audio_codec": "truehd", "audio_channels": "7.1", "audio_languages": "EN",
        "video_bit_depth": 10, "dynamic_range_type": "DV", "three_d": False,
        "edition": "Directors Cut", "release_group": "FraMeSToR",
        "custom_formats": "IMAX", "tmdbid": 603, "imdbid": "tt0133093",
        "original_title": "The Director's Cut 1999 UHD BluRay 2160p TrueHD Atmos 7.1 DV HEVC-FraMeSToR",
    },
    "episode": {
        "series": "The Expanse", "year": 2015, "season": 4, "episode": 2,
        "episode_title": "Jetsam", "air_date": "2019-12-13", "absolute": 32,
        "quality": "WEBDL-1080p", "resolution": "1080p", "source": "web-dl",
        "codec": "x264", "audio_codec": "eac3", "audio_channels": "5.1",
        "audio_languages": "EN", "video_bit_depth": 8, "release_group": "NTb",
        "custom_formats": "AMZN", "tvdbid": 280619, "imdbid": "tt3230854",
        "original_title": "The.Expanse.S04E02.Jetsam.1080p.AMZN.WEB-DL.DDP5.1.H.264-NTb",
    },
    "youtube": {
        "channel": "Techmoan", "title": "The forgotten cassette format",
        "published_at": "2026-03-14", "youtube_id": "dQw4w9WgXcQ",
    },
}


# Tokens only the IMPORT path can fill. They come from ffprobe reading the file
# as it lands, or from the grab's own release name and quality profile — none of
# which survives into the library row a rename works from. Anything that renames
# an EXISTING file has to know this list, or it will happily compute a "correct"
# name that silently drops whatever the template asked for here.
#
# ``Custom Formats`` used to be on this list and is not any more. Custom formats
# are matched against a release NAME, and an existing file still has one — its
# own filename — so they are as computable for a library file as they were at
# import. Leaving them here blocked the TRaSH scheme this app recommends with a
# one-click button, which silently turned the Naming Conformance job off.
LIBRARY_UNAVAILABLE_TOKENS = (
    "MediaInfo VideoBitDepth", "MediaInfo AudioLanguages",
    "Original Title", "Original Filename", "absolute",
)


def library_custom_formats(db) -> list:
    """The custom-format definitions, loaded once for a whole rename pass.

    Callers hold this across every file: load_formats() parses JSON out of the
    settings table, and doing that per file turns a library scan into thousands
    of redundant parses."""
    try:
        from core.video.custom_formats import load_formats
        return load_formats(db)
    except Exception:   # noqa: BLE001 - naming must never fail on a config read
        logger.exception("custom format definitions could not be loaded for renaming")
        return []


def library_media_fields(row: dict, filename: Any = None,
                         custom_formats: Any = None) -> dict:
    """Naming fields recoverable for a file ALREADY in the library.

    Three sources, in this order of trust:
      · the scan's own facts (``media_files``) for audio codec, channel layout
        and dynamic range — measured from the container, same as at import;
      · the CURRENT filename for release group, edition and 3D, because those
        never had a column and the name is the only place they survive;
      · the custom-format definitions, matched against that same filename.
        Custom formats are name matchers, and a library file has a name — the
        one it is called right now — so they are computable here exactly as they
        were at import. Pass the definitions in (see library_custom_formats);
        omitting them leaves the token empty rather than guessing.

    The filename sources are deliberately lossy in one direction: a file whose
    name was already stripped of its group can't get it back. This recovers what
    is there and claims nothing more.
    """
    from core.video.mediainfo import audio_channel_label
    from core.video.release_parse import parse_release
    row = row if isinstance(row, dict) else {}
    # The BASENAME without its extension. The release-group pattern anchors to
    # end-of-string, so a trailing '.mkv' hides the '-NTb' that precedes it, and
    # a full path lets a parent folder's text leak into the parse.
    raw = str(filename or row.get("relative_path") or "")
    stem = os.path.splitext(raw.replace("\\", "/").rstrip("/").rsplit("/", 1)[-1])[0]
    parsed = parse_release(stem)
    out = {
        "audio_codec": row.get("audio_codec"),
        "audio_channels": audio_channel_label(row.get("audio_channels")),
        "dynamic_range_type": row.get("dynamic_range") or (
            str(parsed.get("hdr")).upper() if parsed.get("hdr") else None),
        "release_group": parsed.get("group"),
        "edition": parsed.get("edition"),
        "three_d": parsed.get("three_d"),
    }
    if custom_formats:
        from core.video.custom_formats import matching_formats
        names = [f.get("name") for f in matching_formats(stem, custom_formats)
                 if f.get("name")]
        if names:
            out["custom_formats"] = " ".join(names)
    return out


def template_uses_unavailable_tokens(template: Any) -> list:
    """Which import-only tokens a template references, if any.

    A caller that renames existing files consults this before proposing
    anything: rendering a template it cannot fully satisfy produces a shorter
    name that LOOKS canonical, so acting on it would quietly delete information
    from the filename."""
    if not naming_tokens.has_brace_tokens(template):
        return []
    folded = naming_tokens.canonical(template)
    return [name for name in LIBRARY_UNAVAILABLE_TOKENS
            if naming_tokens.canonical(name) in folded]


def brace_token_names(scope: str) -> list:
    """Every ``{Token}`` this scope understands — drives the settings-page
    reference list, so the UI can never advertise a token the renderer lacks."""
    builder = _BRACE_TOKENS.get(str(scope or "").lower())
    if not builder:
        return []
    return sorted(builder({}).keys(), key=str.lower)


def _youtube_values(f: dict) -> dict:
    """Template values for a YouTube upload — channel-as-show, season=year, date-named
    episode (Plex 'TV by date'). ``published_at``/``date`` is 'YYYY-MM-DD'."""
    channel = f.get("channel") or f.get("series") or f.get("title") or "Unknown"
    pub = str(f.get("published_at") or f.get("date") or "")[:10]
    y = m = d = ""
    if len(pub) == 10 and pub[4] == "-" and pub[7] == "-":
        y, m, d = pub[0:4], pub[5:7], pub[8:10]
    has_year = _plausible_year(y)
    return {
        "channel": channel,
        "title": _str(f.get("title")) or "Unknown",
        "year": y if has_year else "",
        "date": pub if has_year else "",     # only a trustworthy full date
        "month": m if has_year else "",
        "day": d if has_year else "",
        # ytdl-sub-style season/episode token: s<year>e<MMDD>. The one thing that
        # lets Plex's Series Scanner index a YouTube channel with no online agent.
        "sxe": ("s%se%s%s" % (y, m, d)) if (has_year and m and d) else "",
        "videoid": _str(f.get("youtube_id")),
    }


def render_template(template: Any, values: dict, brace: Any = None) -> str:
    """Substitute ``$token`` / ``${token}`` from ``values`` into ``template``. Each
    value is path-sanitised first (so a title with '/' can't spawn a folder), and
    tokens are replaced longest-name-first ($episodetitle before $episode).

    ``brace`` (a ``naming_tokens.TokenSet``) additionally enables the
    Sonarr/Radarr ``{Token}`` scheme. The two run in ONE order for a reason:
    ``{…}`` groups resolve FIRST, so a ``$token`` value that happens to contain a
    brace — an episode literally titled 'The {Redacted} Job' — is inserted after
    group parsing is done and can never be read as a group itself."""
    clean = {k: sanitize(v) for k, v in (values or {}).items()}
    out = str(template or "")
    if brace is not None and naming_tokens.has_brace_tokens(out):
        out = naming_tokens.render(out, brace, sanitize=sanitize)
    for tok in sorted(clean, key=len, reverse=True):
        out = out.replace("${" + tok + "}", clean[tok])
    for tok in sorted(clean, key=len, reverse=True):
        out = out.replace("$" + tok, clean[tok])
    return out


def _tidy_component(part: str) -> str:
    """Clean one path segment: drop a ' - ' left dangling by an empty token, remove
    empty ()/[] left by an empty $year, collapse whitespace, trim stray dashes and
    Windows-hostile trailing dots/spaces.

    Orphan brackets are swept too: the ``{[A}{ B]}`` idiom the TRaSH schemes use
    spreads one bracket pair across two groups, so when only one survives the
    segment is left lopsided ('[EAC3' / ' 5.1]')."""
    p = naming_tokens.strip_orphan_brackets(part)
    p = re.sub(r"\s+-\s+(?=(\s|$))", " ", p)      # ' - ' before an empty token
    p = re.sub(r"\(\s*\)", "", p)                 # empty ( ) from a missing $year
    p = re.sub(r"\[\s*\]", "", p)
    p = re.sub(r"\{\s*\}", "", p)                 # empty { } from a vanished group
    p = re.sub(r"\s+", " ", p).strip()
    p = p.strip("-").strip()
    return p.rstrip(". ")


def render_path(scope: Any, root: Any, fields: dict, settings: Any, ext: Any) -> dict:
    """Render the destination for a finished download from the user's templates.
    Returns ``{"dir", "filename", "path"}`` (same shape as ``library_paths.plan_path``).
    An unsupported scope falls back to a flat drop so a file is never lost."""
    settings = settings if isinstance(settings, dict) else {}
    fields = fields if isinstance(fields, dict) else {}
    root = str(root or "")
    sc = str(scope or "").lower()

    brace = None
    if sc == "movie":
        tmpl = settings.get("movie_template") or DEFAULTS["movie_template"]
        values = _movie_values(fields)
        brace = naming_tokens.TokenSet(_movie_brace_tokens(fields), _NUMERIC_TOKENS)
    elif sc == "episode":
        tmpl = settings.get("episode_template") or DEFAULTS["episode_template"]
        values = _episode_values(fields)
        brace = naming_tokens.TokenSet(_episode_brace_tokens(fields), _NUMERIC_TOKENS)
    elif sc == "youtube":
        tmpl = settings.get("youtube_template") or DEFAULTS["youtube_template"]
        # Saved settings snapshot the default, so simply changing DEFAULTS would
        # strand everyone who never customised the template on the old broken
        # naming. A stored value that IS the old default upgrades to the new one;
        # anything the user actually edited is untouched.
        if tmpl == _LEGACY_YOUTUBE_TEMPLATE:
            tmpl = DEFAULTS["youtube_template"]
        values = _youtube_values(fields)
    else:
        base = (sanitize(fields.get("title")) or "download") + _ext(ext)
        return {"dir": root, "filename": base, "path": os.path.join(root, base)}

    rendered = render_template(tmpl, values, brace)
    parts = [p for p in (_tidy_component(seg) for seg in rendered.split("/")) if p]
    if not parts:
        parts = ["download"]
    d = os.path.join(root, *parts[:-1]) if len(parts) > 1 else root
    filename = parts[-1] + _ext(ext)
    return {"dir": d, "filename": filename, "path": os.path.join(d, filename)}


__all__ = [
    "DEFAULTS", "default_settings", "normalize", "load", "save",
    "render_template", "render_path",
]
