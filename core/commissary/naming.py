"""The ``{Token}`` vocabulary: what each Sonarr/Radarr token means here.

Upstream names files from ``$token`` templates only. The old fork added the
Sonarr/Radarr ``{Token}`` scheme so a TRaSH-guide format pasted verbatim renders
the filename the guide promises - and the live settings use it, so without this
an import would name its folder ``{Movie CleanTitle} ({Movie Year})`` literally.

Three callers have to agree on a name, or the library forks:
  * the importer (``import_fields``: facts from the grab and ffprobe),
  * the renamer and Naming Conformance (``library_media_fields``: facts
    recovered for a file already on disk - the scan's own columns, then the
    current filename),
  * the settings preview (``PREVIEW_SAMPLES``).

Tokens only an import can know (bit depth, audio languages, the original
release name, the absolute number) are listed in LIBRARY_UNAVAILABLE_TOKENS, so
a rename never proposes a shorter name that silently drops them.
"""

from __future__ import annotations

import os
import re
from typing import Any, Dict, List, Optional

from core.commissary import naming_tokens
from utils.logging_config import get_logger

logger = get_logger("commissary.naming")

NUMERIC_TOKENS = ("season", "episode", "absolute", "MediaInfo VideoBitDepth")

# Deliberately NOT supported: {Preferred Words}. Sonarr's release-preference
# scoring has no counterpart here, and rendering it empty would quietly drop a
# component the scheme asked for - an unknown token stays visible instead.

# Codec labels as the naming schemes write them. ffprobe says 'hevc'/'eac3';
# blanket upper-casing gives 'HEVC'/'TRUEHD', which is not a Sonarr filename.
_VIDEO_CODEC_LABELS = {"hevc": "x265", "h265": "x265", "x265": "x265",
                       "x264": "x264", "h264": "x264", "avc": "x264",
                       "av1": "AV1", "vp9": "VP9", "mpeg2video": "MPEG2"}
_AUDIO_CODEC_LABELS = {"truehd": "TrueHD", "eac3": "EAC3", "ac3": "AC3",
                       "dts": "DTS", "dtshd": "DTS-HD", "dts-hd": "DTS-HD",
                       "aac": "AAC", "opus": "Opus", "flac": "FLAC",
                       "vorbis": "Vorbis", "mp3": "MP3", "pcm_s24le": "PCM"}


def _str(v: Any) -> str:
    return "" if v is None else str(v)


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


def _codec_label(value: Any, table: dict) -> str:
    key = _str(value).strip().lower()
    return table.get(key, key.upper()) if key else ""


# ── file facts (ffprobe) ────────────────────────────────────────────────────

_CHANNEL_LAYOUTS = {1: "1.0", 2: "2.0", 3: "2.1", 6: "5.1", 7: "6.1", 8: "7.1"}
_PIX_FMT_DEPTHS = (("12le", 12), ("12be", 12), ("10le", 10), ("10be", 10))


def _int(v: Any) -> int:
    try:
        return int(v)
    except (TypeError, ValueError):
        return 0


def audio_channel_label(channels: Any, layout: Any = None) -> Optional[str]:
    """'5.1' / '7.1' / '2.0' from a channel count (the scan stores 6, schemes write 5.1)."""
    n = _int(channels)
    if n in _CHANNEL_LAYOUTS:
        return _CHANNEL_LAYOUTS[n]
    text = str(layout or "").strip().lower()
    if text.startswith(("mono", "stereo")):
        return "1.0" if text.startswith("mono") else "2.0"
    if n > 0:
        return "%d.1" % (n - 1) if n > 2 else "%d.0" % n
    return None


def video_bit_depth(video: Any) -> Optional[int]:
    v = video if isinstance(video, dict) else {}
    declared = _int(v.get("bits_per_raw_sample"))
    if declared:
        return declared
    pix = str(v.get("pix_fmt") or "").lower()
    for suffix, depth in _PIX_FMT_DEPTHS:
        if pix.endswith(suffix):
            return depth
    return 8 if pix else None


def dynamic_range_type(video: Any) -> Optional[str]:
    """'DV' / 'HDR10+' / 'HDR10' / 'HLG' from colour metadata and side data -
    the file's own word, not the release name's claim."""
    v = video if isinstance(video, dict) else {}
    kinds = {str((s or {}).get("side_data_type") or "").lower()
             for s in v.get("side_data_list") or [] if isinstance(s, dict)}
    if any("dovi" in k or "dolby vision" in k for k in kinds):
        return "DV"
    if any("hdr dynamic metadata" in k for k in kinds):
        return "HDR10+"
    transfer = str(v.get("color_transfer") or "").lower()
    if transfer in ("smpte2084", "smpte st 2084"):
        return "HDR10"
    if transfer in ("arib-std-b67", "hlg"):
        return "HLG"
    return None


def audio_languages(streams: Any, limit: int = 3) -> Optional[str]:
    out: list = []
    for s in streams or []:
        if not isinstance(s, dict) or s.get("codec_type") != "audio":
            continue
        lang = str((s.get("tags") or {}).get("language") or "").strip().upper()
        if lang and lang != "UND" and lang not in out:
            out.append(lang)
    return "+".join(out[:limit]) if out else None


def probe_facts(data: Any) -> Dict[str, Any]:
    """The naming facts in one ffprobe JSON (the same call upstream makes)."""
    data = data if isinstance(data, dict) else {}
    streams = data.get("streams") or []
    video = next((s for s in streams if s.get("codec_type") == "video"), None)
    audio = next((s for s in streams if s.get("codec_type") == "audio"), None)
    return {
        "audio_channels": audio_channel_label((audio or {}).get("channels"),
                                              (audio or {}).get("channel_layout")),
        "audio_languages": audio_languages(streams),
        "video_bit_depth": video_bit_depth(video),
        "dynamic_range_type": dynamic_range_type(video),
    }


# ── release-name facts ──────────────────────────────────────────────────────

# A fixed list of real editions, not "any of these words": 'cut' and 'edition'
# appear in ordinary titles ('The Final Cut' film aside).
_EDITIONS = [
    (re.compile(r"\bdirector'?s?[. _-]?cut\b", re.I), "Directors Cut"),
    (re.compile(r"\bextended[. _-]?(?:edition|cut|version)\b", re.I), "Extended"),
    (re.compile(r"\bfinal[. _-]?cut\b", re.I), "Final Cut"),
    (re.compile(r"\bspecial[. _-]?edition\b", re.I), "Special Edition"),
    (re.compile(r"\bultimate[. _-]?(?:edition|cut)\b", re.I), "Ultimate"),
    (re.compile(r"\bdefinitive[. _-]?edition\b", re.I), "Definitive"),
    (re.compile(r"\bcollector'?s?[. _-]?edition\b", re.I), "Collectors"),
    (re.compile(r"\banniversary[. _-]?edition\b", re.I), "Anniversary"),
    (re.compile(r"\bcriterion\b", re.I), "Criterion"),
    (re.compile(r"\bremaster(?:ed)?\b", re.I), "Remastered"),
    (re.compile(r"\bunrated\b", re.I), "Unrated"),
    (re.compile(r"\buncut\b", re.I), "Uncut"),
    (re.compile(r"\bimax\b", re.I), "IMAX"),
    (re.compile(r"\bextended\b", re.I), "Extended"),
]


def edition_tags(release_name: Any) -> Optional[str]:
    t = str(release_name or "")
    found: list = []
    for rx, label in _EDITIONS:
        if label not in found and rx.search(t):
            found.append(label)
    return " ".join(found) if found else None


# ── the vocabulary ──────────────────────────────────────────────────────────

def _media_tokens(f: dict) -> dict:
    """Shared by movies and episodes. File facts beat name claims."""
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


def movie_tokens(f: dict) -> dict:
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


def episode_tokens(f: dict) -> dict:
    series = f.get("series") or f.get("title") or "Unknown"
    year = _str(f.get("year")) if _plausible_year(f.get("year")) else ""
    clean = naming_tokens.clean_title(series)
    # a series title that itself ends '(2019)' must not print the year twice
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


_BUILDERS = {"movie": movie_tokens, "episode": episode_tokens}


def token_set(scope: Any, fields: dict):
    """The TokenSet for a movie/episode render, or None (YouTube keeps $tokens)."""
    builder = _BUILDERS.get(str(scope or "").lower())
    if not builder:
        return None
    return naming_tokens.TokenSet(builder(fields if isinstance(fields, dict) else {}), NUMERIC_TOKENS)


def brace_token_names(scope: str) -> List[str]:
    builder = _BUILDERS.get(str(scope or "").lower())
    return sorted(builder({}).keys(), key=str.lower) if builder else []


# A value for EVERY token, awkward ones included, so a preview shows what a
# template does at its fullest.
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
        "custom_formats": "AMZN", "tmdbid": 63639, "tvdbid": 280619, "imdbid": "tt3230854",
        "original_title": "The.Expanse.S04E02.Jetsam.1080p.AMZN.WEB-DL.DDP5.1.H.264-NTb",
    },
    "youtube": {
        "channel": "Techmoan", "title": "The forgotten cassette format",
        "published_at": "2026-03-14", "youtube_id": "dQw4w9WgXcQ",
    },
}


# Tokens only an IMPORT can fill: ffprobe reading the file as it lands, or the
# grab's own release name. A rename works from the library row and can't.
# (Custom Formats is not here: formats match a NAME, and a library file has one.)
LIBRARY_UNAVAILABLE_TOKENS = (
    "MediaInfo VideoBitDepth", "MediaInfo AudioLanguages",
    "Original Title", "Original Filename", "absolute",
)


def template_uses_unavailable_tokens(template: Any) -> List[str]:
    if not naming_tokens.has_brace_tokens(template):
        return []
    folded = naming_tokens.canonical(template)
    return [n for n in LIBRARY_UNAVAILABLE_TOKENS if naming_tokens.canonical(n) in folded]


# ── the two suppliers ───────────────────────────────────────────────────────

def import_fields(dl: dict, parsed: dict, ctx: dict, name: Any) -> Dict[str, Any]:
    """The {Token} inputs an import knows: the grab's release name, ffprobe's
    facts (already copied onto ``parsed``), and the custom formats the monitor
    scored. All optional - a template that doesn't mention them is unaffected."""
    dl = dl or {}
    parsed = parsed or {}
    release = dl.get("release_title") or name
    return {
        "absolute": (ctx or {}).get("absolute"),
        "release_group": parsed.get("group"),
        "edition": parsed.get("edition") or edition_tags(release),
        "three_d": parsed.get("three_d"),
        "original_title": release,
        "original_filename": name,
        "custom_formats": dl.get("_custom_formats"),
        "audio_codec": parsed.get("audio_codec") or parsed.get("audio"),
        "audio_channels": parsed.get("audio_channels"),
        "audio_languages": parsed.get("audio_languages"),
        "video_bit_depth": parsed.get("video_bit_depth"),
        # the FILE's range when probed; the name's claim only as the coarse 'HDR'
        "dynamic_range_type": (parsed.get("dynamic_range_type")
                               or (str(parsed.get("hdr")).upper() if parsed.get("hdr") else None)),
    }


def library_custom_formats(db) -> list:
    """The custom-format definitions, loaded once per rename pass."""
    try:
        from core.video.custom_formats import load_formats
        return load_formats(db)
    except Exception:  # noqa: BLE001 - naming must never fail on a config read
        logger.exception("custom format definitions could not be loaded for renaming")
        return []


def custom_formats_for(db, dl: dict) -> Optional[str]:
    """The custom formats a grabbed release matches, for {Custom Formats}."""
    try:
        from core.video.custom_formats import matching_formats
        name = (dl or {}).get("release_title") or (dl or {}).get("filename") or ""
        names = [f.get("name") for f in matching_formats(name, library_custom_formats(db))
                 if f.get("name")]
        return " ".join(names) or None
    except Exception:  # noqa: BLE001
        logger.debug("custom formats for an import failed", exc_info=True)
        return None


# 'edition-Directors Cut' - how the {edition-{Edition Tags}} schemes write it
_EDITION_MARK = re.compile(r"\bedition-([^\[\]{}()]+?)(?=\s*[\[{(]|\s+-\s|$)", re.I)
_BRACKETED = re.compile(r"\[[^\]]*\]")


def library_edition(stem: str) -> Optional[str]:
    """The edition written in a library filename. The explicit 'edition-X'
    marker wins; otherwise the edition words outside [brackets], because a
    bracketed tag is a custom format or quality - '[IMAX]' is not an edition."""
    m = _EDITION_MARK.search(stem or "")
    if m:
        return m.group(1).strip() or None
    return edition_tags(_BRACKETED.sub(" ", stem or ""))


def library_media_fields(row: dict, filename: Any = None, custom_formats: Any = None) -> Dict[str, Any]:
    """Naming facts recoverable for a file ALREADY in the library, in order of
    trust: the scan's own columns (audio codec, channels, dynamic range - read
    from the container), then the CURRENT filename for release group, edition
    and 3D (no column holds them), then the custom formats matched against that
    same name. Lossy one way: a name already stripped of its group can't get it
    back. It recovers what is there and claims nothing more."""
    from core.video.release_parse import parse_release
    row = row if isinstance(row, dict) else {}
    raw = str(filename or row.get("relative_path") or "")
    # the basename without its extension: the group pattern anchors to the end
    stem = os.path.splitext(raw.replace("\\", "/").rstrip("/").rsplit("/", 1)[-1])[0]
    parsed = parse_release(stem)
    out = {
        "audio_codec": row.get("audio_codec"),
        "audio_channels": audio_channel_label(row.get("audio_channels")),
        "dynamic_range_type": row.get("dynamic_range") or (
            str(parsed.get("hdr")).upper() if parsed.get("hdr") else None),
        "release_group": parsed.get("group"),
        "edition": library_edition(stem),
        "three_d": parsed.get("three_d"),
    }
    if custom_formats:
        try:
            from core.video.custom_formats import matching_formats
            names = [f.get("name") for f in matching_formats(stem, custom_formats) if f.get("name")]
            if names:
                out["custom_formats"] = " ".join(names)
        except Exception:  # noqa: BLE001
            logger.debug("custom formats for a library file failed", exc_info=True)
    return out


# ── rows for a rename / conformance pass ────────────────────────────────────

_FACT_SQL = {
    "movie": ("SELECT f.id, f.audio_codec, f.audio_channels, f.dynamic_range, "
              "m.tmdb_id, m.imdb_id, NULL, NULL, "
              "COALESCE(m.year, CAST(substr(NULLIF(m.release_date, ''), 1, 4) AS INTEGER)) "
              "FROM media_files f JOIN movies m ON m.id = f.movie_id"),
    "episode": ("SELECT f.id, f.audio_codec, f.audio_channels, f.dynamic_range, "
                "s.tmdb_id, s.imdb_id, s.tvdb_id, e.air_date, "
                "COALESCE(s.year, CAST(substr(NULLIF(s.first_air_date, ''), 1, 4) AS INTEGER)) "
                "FROM media_files f JOIN episodes e ON e.id = f.episode_id "
                "JOIN shows s ON s.id = e.show_id"),
}


def _facts(db, kind) -> Dict[int, tuple]:
    try:
        conn = db._get_connection()
    except Exception:  # noqa: BLE001
        return {}
    try:
        return {r[0]: tuple(r[1:]) for r in conn.execute(_FACT_SQL[kind]).fetchall()}
    except Exception:  # noqa: BLE001 - a stub DB or an old schema: names as upstream does
        logger.debug("library naming facts unavailable", exc_info=True)
        return {}
    finally:
        conn.close()


def with_library_facts(db, rows, kind=None):
    """Attach ``_naming`` to each row: the ids, air date and premiere year the
    upstream queries leave out, plus the file facts ``library_media_fields``
    recovers. Two queries and one custom-format load for the whole pass."""
    rows = list(rows or [])
    if not rows:
        return rows
    formats = library_custom_formats(db)
    facts = {k: _facts(db, k) for k in ("movie", "episode")}
    out = []
    for r in rows:
        r = dict(r)
        scope = "movie" if str(r.get("scope") or kind or "") == "movie" else "episode"
        f = facts[scope].get(r.get("file_id"))
        merged = dict(r)
        if f:
            audio, channels, dr, tmdb, imdb, tvdb, air, year = f
            merged.update({"audio_codec": r.get("audio_codec") or audio,
                           "audio_channels": channels, "dynamic_range": dr})
            extra = {"tmdbid": tmdb, "imdbid": imdb, "tvdbid": tvdb,
                     "air_date": air, "year": year}
        else:
            extra = {}
        extra.update(library_media_fields(merged, r.get("relative_path"), formats))
        r["_naming"] = {k: v for k, v in extra.items() if v not in (None, "")}
        out.append(r)
    return out


def with_naming(row: dict, fields: dict) -> dict:
    """An upstream field dict plus the row's ``_naming`` facts (only where the
    upstream value is missing, so nothing it already knew is overridden)."""
    extra = (row or {}).get("_naming") or {}
    out = dict(fields)
    for k, v in extra.items():
        if out.get(k) in (None, ""):
            out[k] = v
    return out
