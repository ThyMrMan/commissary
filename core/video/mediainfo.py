"""Probe a finished video file with ffprobe for its TRUE media info.

We otherwise trust the release NAME for resolution/quality, and names lie: 720p
upscales labelled 1080p, trailers/samples labelled as the feature, broken muxes.
ffprobe reads the real container — duration, dimensions → resolution, codecs — so the
importer can tag the file by its actual quality and reject corrupt / too-short junk.

The parsing (``parse_ffprobe`` / ``resolution_from_dimensions``) is pure and unit-tested
on canned JSON; the subprocess runner is injected, so nothing here needs ffmpeg to be
tested. ffmpeg is OPTIONAL — when ffprobe isn't installed, or it errors, ``probe``
returns None and the caller falls back to the scene name.

Three outcomes, deliberately distinct:
  - None              → couldn't verify (ffprobe missing / crashed / timed out) → skip
  - {"ok": False, …}  → ffprobe ran and found NO video stream → corrupt / fake
  - {"ok": True,  …}  → real media info to trust over the name

Isolated: stdlib only; no music imports.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from typing import Any, Callable

_FFPROBE = "ffprobe"


def _int(v: Any) -> int:
    try:
        return int(v)
    except (TypeError, ValueError):
        return 0


def _float(v: Any) -> float:
    try:
        return float(v)
    except (TypeError, ValueError):
        return 0.0


def resolution_from_dimensions(width: Any, height: Any) -> str | None:
    """Bucket real pixel dimensions into a resolution label. Uses the LARGER axis so
    a letterboxed 1920x800 movie reads as 1080p (not 720p by its short side)."""
    ref = max(_int(width), _int(height))
    if ref <= 0:
        return None
    if ref >= 3000:
        return "2160p"
    if ref >= 1700:
        return "1080p"
    if ref >= 1100:
        return "720p"
    return "480p"


# Channel count → the label naming schemes use ('6' reads as 5.1, not "6").
_CHANNEL_LAYOUTS = {1: "1.0", 2: "2.0", 3: "2.1", 6: "5.1", 7: "6.1", 8: "7.1"}

# pix_fmt suffixes that imply a bit depth when bits_per_raw_sample is absent
# (which is most of the time for HEVC).
_PIX_FMT_DEPTHS = (("12le", 12), ("12be", 12), ("10le", 10), ("10be", 10))


def audio_channel_label(channels: Any, layout: Any = None) -> str | None:
    """'5.1' / '7.1' / '2.0' from a stream's channel count.

    ffprobe reports a bare count; every naming scheme wants the layout. Falls
    back to the container's own channel_layout string for the odd counts not in
    the table (a '5.0' or a '9.1' stays honest rather than being rounded)."""
    n = _int(channels)
    if n in _CHANNEL_LAYOUTS:
        return _CHANNEL_LAYOUTS[n]
    text = str(layout or "").strip().lower()
    if text.startswith(("mono", "stereo")):
        return "1.0" if text.startswith("mono") else "2.0"
    if n > 0:
        # e.g. 5 → '4.1': one LFE plus the rest, the same shape as the table.
        return "%d.1" % (n - 1) if n > 2 else "%d.0" % n
    return None


def video_bit_depth(video: Any) -> int | None:
    """Bit depth of a video stream — 8/10/12 — or None when it can't be read.
    ``bits_per_raw_sample`` is authoritative but usually absent, so the pixel
    format is the fallback ('yuv420p10le' → 10)."""
    v = video if isinstance(video, dict) else {}
    declared = _int(v.get("bits_per_raw_sample"))
    if declared:
        return declared
    pix = str(v.get("pix_fmt") or "").lower()
    for suffix, depth in _PIX_FMT_DEPTHS:
        if pix.endswith(suffix):
            return depth
    return 8 if pix else None


def dynamic_range_type(video: Any) -> str | None:
    """'DV' / 'HDR10+' / 'HDR10' / 'HLG' / 'PQ', or None for plain SDR.

    Read from the stream's colour metadata and side-data list, which is what
    makes it trustworthy: a release NAME claiming HDR proves nothing about the
    file, and this is the token schemes put in the filename."""
    v = video if isinstance(video, dict) else {}
    side = v.get("side_data_list") or []
    kinds = {str((s or {}).get("side_data_type") or "").lower() for s in side if isinstance(s, dict)}
    if any("dovi" in k or "dolby vision" in k for k in kinds):
        return "DV"
    transfer = str(v.get("color_transfer") or "").lower()
    if any("hdr dynamic metadata" in k for k in kinds):
        return "HDR10+"
    if transfer in ("smpte2084", "smpte st 2084"):
        return "HDR10"
    if transfer in ("arib-std-b67", "hlg"):
        return "HLG"
    return None


def audio_languages(streams: Any, limit: int = 3) -> str | None:
    """'EN' / 'EN+JA' — the audio stream languages, deduped, order preserved.
    'und' (undefined) is dropped: it is ffprobe's "no idea", not a language."""
    out: list = []
    for s in (streams or []):
        if not isinstance(s, dict) or s.get("codec_type") != "audio":
            continue
        lang = str(((s.get("tags") or {}).get("language") or "")).strip().upper()
        if lang and lang != "UND" and lang not in out:
            out.append(lang)
    return "+".join(out[:limit]) if out else None


def _norm_codec(name: Any) -> str | None:
    s = str(name or "").strip().lower()
    if not s:
        return None
    if s in ("hevc", "h265", "x265"):
        return "hevc"
    if s in ("h264", "avc", "x264"):
        return "x264"
    if s == "av1":
        return "av1"
    return s


def _aspect_ratio_num(v: Any):
    """A numeric width/height ratio from a float, an 'a:b' string, or a decimal string."""
    if v is None or v == "":
        return None
    if isinstance(v, (int, float)):
        return float(v) if v > 0 else None
    s = str(v).strip()
    if ":" in s:
        a, _, b = s.partition(":")
        try:
            a, b = float(a), float(b)
            return (a / b) if b else None
        except ValueError:
            return None
    try:
        f = float(s)
        return f if f > 0 else None
    except ValueError:
        return None


def canonical_aspect(v: Any) -> str | None:
    """Bucket an aspect ratio (a float, an 'a:b' string, or a w/h ratio) into a
    common label. Servers report it differently (Plex 1.78, Jellyfin '16:9'), so
    we normalize once at store time and the overlay just shows the label."""
    r = _aspect_ratio_num(v)
    if r is None:
        return None
    if r < 1.4:
        return "4:3"
    if r < 1.9:
        return "16:9"
    if r < 2.1:
        return "2:1"
    return "2.40:1"


def parse_ffprobe(data: Any) -> dict:
    """Parse ffprobe's ``-show_format -show_streams`` JSON into the fields we use.
    ``ok`` is True only when a video stream is present (else: corrupt / not a video)."""
    data = data if isinstance(data, dict) else {}
    streams = data.get("streams") or []
    fmt = data.get("format") or {}
    video = next((s for s in streams if s.get("codec_type") == "video"), None)
    audio = next((s for s in streams if s.get("codec_type") == "audio"), None)
    duration = _float(fmt.get("duration")) or _float((video or {}).get("duration"))
    width = (video or {}).get("width")
    height = (video or {}).get("height")
    return {
        "ok": video is not None,
        "duration_sec": duration,
        "width": _int(width),
        "height": _int(height),
        "resolution": resolution_from_dimensions(width, height) if video else None,
        "aspect": canonical_aspect(_int(width) / _int(height)) if (video and _int(height)) else None,
        "video_codec": _norm_codec((video or {}).get("codec_name")),
        "audio_codec": str((audio or {}).get("codec_name") or "") or None,
        # Naming-scheme facts (Sonarr/Radarr MediaInfo tokens). All read from the
        # SAME ffprobe JSON we already fetch — no extra call, and every one of
        # them is a property of the file rather than a claim in its name.
        "audio_channels": audio_channel_label((audio or {}).get("channels"),
                                              (audio or {}).get("channel_layout")),
        "audio_languages": audio_languages(streams),
        "video_bit_depth": video_bit_depth(video),
        "dynamic_range_type": dynamic_range_type(video),
    }


def ffprobe_available() -> bool:
    return shutil.which(_FFPROBE) is not None


def _default_runner(path: str) -> str | None:
    """Run ffprobe and return its JSON stdout, or None on any failure (so a transient
    ffprobe error degrades to 'unverified', never to a false 'corrupt')."""
    try:
        proc = subprocess.run(
            [_FFPROBE, "-v", "quiet", "-print_format", "json",
             "-show_format", "-show_streams", str(path)],
            capture_output=True, text=True, timeout=120, check=False,
        )
    except Exception:   # noqa: BLE001 - missing binary / timeout / OS error → unverified
        return None
    if proc.returncode != 0 or not (proc.stdout or "").strip():
        return None
    return proc.stdout


def probe(path: Any, runner: Callable | None = None) -> dict | None:
    """Probe ``path`` and return parsed media info, or None when it can't be verified.
    ``runner(path)->json_str|None`` is injected (real ffprobe in prod, canned in tests).
    When no runner is given and ffprobe isn't installed, returns None (skip verify)."""
    use = runner if runner is not None else (_default_runner if ffprobe_available() else None)
    if use is None:
        return None
    try:
        raw = use(path)
    except Exception:   # noqa: BLE001
        return None
    if not raw:
        return None
    try:
        data = json.loads(raw)
    except (ValueError, TypeError):
        return None
    return parse_ffprobe(data)


__all__ = [
    "resolution_from_dimensions", "parse_ffprobe", "ffprobe_available", "probe",
]
