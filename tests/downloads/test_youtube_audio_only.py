"""The music YouTube downloader takes audio, and can keep YouTube's own stream.

Two things, from a report that the downloader was "grabbing the music video".

It was not: 'bestaudio' compiles to ``vcodec == 'none'`` in yt-dlp, so video
formats are excluded from selection, and the extract-audio postprocessor means
an MP3 is what lands either way. But one retry branch DID fetch video —

    elif attempt >= 2:
        logger.info("... with 'best' format (video fallback)")
        download_opts['format'] = 'best'

'best' compiles to ``vcodec != 'none' and acodec != 'none'``: a muxed stream,
downloaded in full so ffmpeg can discard the video track. It was redundant as
well as wasteful, because the '/best' half of 'bestaudio/best' already is that
fallback and applies only when a video offers no audio-only format at all.

Second, the transcode. Everything was re-encoded to MP3 320 while YouTube serves
Opus at roughly 130-160kbps — lossy-to-lossy, so a bigger file that sounds
slightly worse. ``youtube.audio_format = "original"`` maps to yt-dlp's
``preferredcodec='best'``, which does not convert: an m4a/AAC stream is left
alone entirely (its extension is in FFmpegExtractAudioPP.COMMON_AUDIO_EXTS, so
the postprocessor returns early) and a webm/Opus one is rewrapped with
'-acodec copy'. Default stays "mp3", so nobody's library changes on upgrade.

That makes the output extension variable, which the old
``prepare_filename(info).with_suffix('.mp3')`` could not express.
"""

from __future__ import annotations

import inspect
import re
from pathlib import Path

import pytest

from core import youtube_client as yc
from core.youtube_client import YouTubeClient


# ── no code path ever selects a video stream ─────────────────────────────────
def _code_only(src: str) -> str:
    """Source with comments stripped.

    The comment explaining WHY the video fallback was removed necessarily
    quotes the thing it removed, so a scan over raw source finds the very
    string it is checking for. Assert about code, not prose.
    """
    out = []
    for line in src.splitlines():
        quote = None
        for i, ch in enumerate(line):
            if quote:
                if ch == quote:
                    quote = None
            elif ch in '"\'':
                quote = ch
            elif ch == '#':
                line = line[:i]
                break
        out.append(line)
    return '\n'.join(out)


def test_the_video_fallback_is_gone():
    """The literal defect. A format spec of exactly 'best' is a muxed
    video+audio stream — it must not appear in the download path."""
    code = _code_only(inspect.getsource(YouTubeClient._download_sync))
    assert "'best'" not in code.replace("'bestaudio/best'", ""), code


def test_the_comment_stripper_does_not_eat_code():
    """This file's own helper — a broken stripper would make the test above
    pass for the wrong reason."""
    assert _code_only("x = 'a#b'  # note").strip() == "x = 'a#b'"
    assert _code_only("# all comment").strip() == ""
    assert _code_only("fmt = 'best'").strip() == "fmt = 'best'"


def test_every_format_the_retry_loop_sets_is_audio_only():
    """Catches the next one: any format assigned anywhere in _download_sync has
    to start from bestaudio, whatever new retry strategy gets added."""
    src = inspect.getsource(YouTubeClient._download_sync)
    specs = re.findall(r"""\[['"]format['"]\]\s*=\s*['"]([^'"]+)['"]""", src)
    assert specs, "expected the retry loop to set a format at least once"
    for spec in specs:
        assert spec.startswith('bestaudio'), f"{spec!r} can select a video stream"


def test_the_default_options_ask_for_audio_only(monkeypatch, tmp_path):
    client = _bare_client(monkeypatch, tmp_path)
    assert client.download_opts['format'] == 'bestaudio/best'


def test_the_last_retry_still_drops_cookies_and_client_overrides():
    """Removing the video fallback must not remove the point of that retry —
    it exists to try again with nothing clever attached."""
    src = inspect.getsource(YouTubeClient._download_sync)
    tail = src.split('elif attempt >= 2:', 1)[1]
    for key in ('cookiesfrombrowser', 'cookiefile', 'extractor_args'):
        assert f"pop('{key}'" in tail, f"the last attempt no longer clears {key}"


# ── the audio_format setting ─────────────────────────────────────────────────
def _bare_client(monkeypatch, tmp_path, audio_format='mp3'):
    monkeypatch.setattr(yc, '_resolve_audio_format', lambda: audio_format)
    monkeypatch.setattr(yc, '_resolve_cookie_opts', lambda: {})
    client = YouTubeClient.__new__(YouTubeClient)
    client.download_path = tmp_path
    client.audio_format = audio_format
    client.download_opts = {
        'format': 'bestaudio/best',
        'outtmpl': str(tmp_path / '%(title)s.%(ext)s'),
        'postprocessors': yc.build_audio_postprocessors(audio_format),
    }
    return client


def test_mp3_is_the_default_and_still_transcodes():
    """Unchanged behaviour, pinned — an upgrade must not silently change the
    format of everything a user downloads."""
    pps = yc.build_audio_postprocessors('mp3')
    assert pps == [{'key': 'FFmpegExtractAudio',
                    'preferredcodec': 'mp3', 'preferredquality': '320'}]


def test_original_asks_yt_dlp_not_to_convert():
    pps = yc.build_audio_postprocessors('original')
    assert len(pps) == 1
    assert pps[0]['preferredcodec'] == 'best'


def test_original_sends_no_quality_because_nothing_is_encoded():
    """preferredquality only feeds an encoder's -q:a / -b:a. Passing it
    alongside '-acodec copy' would be meaningless."""
    assert 'preferredquality' not in yc.build_audio_postprocessors('original')[0]


def test_yt_dlp_really_treats_best_as_passthrough():
    """The assumption the whole option rests on, checked against the installed
    yt-dlp rather than its docs: 'best' means leave a common audio format alone,
    and copy the codec otherwise."""
    from yt_dlp.postprocessor.ffmpeg import FFmpegExtractAudioPP
    src = inspect.getsource(FFmpegExtractAudioPP.run)
    assert "target_format == 'best' and information['ext'] in self.COMMON_AUDIO_EXTS" in src
    assert "'copy'" in src
    for ext in ('m4a', 'opus'):
        assert ext in FFmpegExtractAudioPP.COMMON_AUDIO_EXTS


def _select(spec, formats):
    """Run yt-dlp's real selector over *formats*, sorted the way
    process_video_result sorts them before selection.

    Sorting is not optional here: 'bestaudio' takes the LAST match in the given
    order, trusting the caller to have ranked them. Handing it an unsorted list
    silently returns whatever happens to be last, which is how a first pass at
    this test 'proved' m4a was chosen when opus is what YouTube actually yields.
    """
    import yt_dlp
    from yt_dlp.utils._utils import FormatSorter
    ydl = yt_dlp.YoutubeDL({'quiet': True, 'simulate': True})
    ordered = sorted(formats, key=FormatSorter(ydl, '').calculate_preference)
    return [f['format_id'] for f in ydl._select_formats(ordered, ydl.build_format_selector(spec))]


_FORMATS = [
    {'format_id': 'video-1080', 'vcodec': 'avc1', 'acodec': 'none', 'tbr': 4000,
     'ext': 'mp4', 'protocol': 'https'},
    {'format_id': 'muxed-360', 'vcodec': 'avc1', 'acodec': 'mp4a.40.2', 'tbr': 700,
     'ext': 'mp4', 'protocol': 'https'},
    {'format_id': 'audio-m4a', 'vcodec': 'none', 'acodec': 'mp4a.40.2', 'tbr': 129,
     'abr': 129, 'ext': 'm4a', 'protocol': 'https'},
    {'format_id': 'audio-opus', 'vcodec': 'none', 'acodec': 'opus', 'tbr': 160,
     'abr': 160, 'ext': 'webm', 'protocol': 'https'},
]


def test_the_configured_spec_picks_audio_and_never_the_video():
    """The claim itself, run through the real selector rather than asserted
    about the source: given a typical YouTube format list, what gets downloaded
    is the audio-only stream."""
    assert _select('bestaudio/best', _FORMATS) == ['audio-opus']


def test_the_removed_spec_would_have_picked_a_muxed_stream():
    """And the contrast — 'best', which the old third retry forced, selects a
    video+audio stream. This is the defect, demonstrated."""
    assert _select('best', _FORMATS) == ['muxed-360']


def test_the_muxed_fallback_applies_only_when_there_is_no_audio_only_stream():
    """Which is exactly why forcing it on retry 3 was redundant: 'bestaudio/best'
    already reaches for a muxed stream, but only when it has to."""
    video_only_offering = [f for f in _FORMATS if f['vcodec'] != 'none']
    assert _select('bestaudio/best', video_only_offering) == ['muxed-360']


def test_an_unknown_setting_falls_back_to_mp3(monkeypatch):
    import config.settings as cs
    for junk in ('flac', '', None, 'MP4', 'video'):
        monkeypatch.setattr(cs.config_manager, 'get',
                            lambda k, d=None, _v=junk: _v if k == 'youtube.audio_format' else d)
        assert yc._resolve_audio_format() == 'mp3'


def test_a_valid_setting_is_read_case_insensitively(monkeypatch):
    import config.settings as cs
    monkeypatch.setattr(cs.config_manager, 'get',
                        lambda k, d=None: ' Original ' if k == 'youtube.audio_format' else d)
    assert yc._resolve_audio_format() == 'original'


def test_the_shipped_default_is_mp3():
    from config.settings import ConfigManager
    src = inspect.getsource(ConfigManager)
    block = src.split('"youtube": {', 1)[1].split('}', 1)[0]
    assert '"audio_format": "mp3"' in block


def test_reload_settings_rebuilds_rather_than_mutates(monkeypatch, tmp_path):
    """Switching back to mp3 has to DROP the passthrough codec. Mutating the
    existing dict would leave preferredcodec='best' with a quality bolted on."""
    client = _bare_client(monkeypatch, tmp_path, audio_format='original')
    assert client.download_opts['postprocessors'][0]['preferredcodec'] == 'best'

    monkeypatch.setattr(yc, '_resolve_audio_format', lambda: 'mp3')
    import config.settings as cs
    monkeypatch.setattr(cs.config_manager, 'get',
                        lambda k, d=None: str(tmp_path) if 'download_path' in k else d)
    client._download_delay = 3
    YouTubeClient.reload_settings(client)

    assert client.audio_format == 'mp3'
    assert client.download_opts['postprocessors'] == [
        {'key': 'FFmpegExtractAudio', 'preferredcodec': 'mp3', 'preferredquality': '320'}]


# ── finding the finished file ────────────────────────────────────────────────
class _FakeYDL:
    def __init__(self, prepared):
        self._prepared = prepared

    def prepare_filename(self, info):
        return self._prepared


@pytest.mark.parametrize('ext', ['.mp3', '.m4a', '.opus'])
def test_the_output_path_comes_from_the_postprocessor(tmp_path, ext):
    """yt-dlp rewrites filepath as it converts or remuxes, so that is the
    answer for mp3, m4a and opus alike."""
    landed = tmp_path / f'Song{ext}'
    landed.write_bytes(b'audio')
    info = {'requested_downloads': [{'filepath': str(landed)}]}
    got = YouTubeClient._final_download_path(_FakeYDL(str(tmp_path / 'Song.webm')), info)
    assert got == str(landed)


def test_a_stale_postprocessor_path_is_not_trusted(tmp_path):
    """filepath pointing at a file that isn't there must fall through to the
    probe rather than be handed back as a successful download."""
    real = tmp_path / 'Song.opus'
    real.write_bytes(b'audio')
    info = {'requested_downloads': [{'filepath': str(tmp_path / 'gone.mp3')}]}
    got = YouTubeClient._final_download_path(_FakeYDL(str(tmp_path / 'Song.webm')), info)
    assert got == str(real)


def test_the_probe_covers_the_containers_youtube_serves(tmp_path):
    for ext in ('.m4a', '.opus', '.mp3'):
        landed = tmp_path / f'Track{ext}'
        landed.write_bytes(b'audio')
        got = YouTubeClient._final_download_path(
            _FakeYDL(str(tmp_path / 'Track.webm')), {})
        assert got == str(landed)
        landed.unlink()


def test_nothing_on_disk_reports_failure_rather_than_a_path(tmp_path):
    """The old code returned early on a missing .mp3; the replacement must not
    hand back a path to a file that was never written."""
    assert YouTubeClient._final_download_path(
        _FakeYDL(str(tmp_path / 'Missing.webm')), {}) is None


def test_the_mp3_suffix_assumption_is_gone():
    src = inspect.getsource(YouTubeClient._download_sync)
    assert "with_suffix('.mp3')" not in src


# ── the candidate quality label ──────────────────────────────────────────────
def test_mp3_mode_labels_everything_mp3(monkeypatch, tmp_path):
    """Whatever YouTube is streaming, the file delivered IS an MP3 — so this
    stays exactly as it was, and existing quality profiles are untouched."""
    client = _bare_client(monkeypatch, tmp_path, audio_format='mp3')
    for acodec in ('opus', 'mp4a.40.2', None):
        assert client._delivered_quality_label({'acodec': acodec}) == 'mp3'


@pytest.mark.parametrize('acodec,expected', [
    ('opus', 'opus'),
    ('mp4a.40.2', 'aac'),
    ('aac', 'aac'),
    ('mp3', 'mp3'),
])
def test_original_mode_reports_the_codec_it_will_deliver(monkeypatch, tmp_path,
                                                         acodec, expected):
    client = _bare_client(monkeypatch, tmp_path, audio_format='original')
    assert client._delivered_quality_label({'acodec': acodec}) == expected


def test_an_unknown_codec_never_drops_the_candidate(monkeypatch, tmp_path):
    """A flat search carries no format list, so best_audio is None. Labelling
    that 'unknown' would score it 0.5 and could lose the candidate."""
    client = _bare_client(monkeypatch, tmp_path, audio_format='original')
    assert client._delivered_quality_label(None) == 'mp3'
    assert client._delivered_quality_label({}) == 'mp3'


def test_the_quality_model_understands_both_labels():
    """opus/aac are not invented names — the ranker already scores them, which
    is what makes an honest label safe to emit."""
    from core.quality import model
    src = inspect.getsource(model)
    for fmt in ("'opus'", "'aac'", "'mp3'"):
        assert fmt in src


# ── the settings UI ──────────────────────────────────────────────────────────
_ROOT = Path(__file__).resolve().parents[2]


def test_the_option_is_reachable_from_settings():
    """A config-only setting is unreachable without editing config.json."""
    html = (_ROOT / 'webui' / 'index.html').read_text(encoding='utf-8')
    assert 'id="youtube-audio-format"' in html
    assert 'value="original"' in html


def test_the_select_is_both_loaded_and_saved():
    """Saving without loading would silently reset it to mp3 on every visit."""
    js = (_ROOT / 'webui' / 'static' / 'settings.js').read_text(encoding='utf-8')
    assert "getElementById('youtube-audio-format').value = settings.youtube?.audio_format" in js
    assert "audio_format: document.getElementById('youtube-audio-format')" in js


def test_the_help_text_states_the_quality_profile_consequence():
    """Under 'original' a profile targeting MP3 stops matching YouTube, because
    the file genuinely is not an MP3. That must not be a surprise."""
    html = (_ROOT / 'webui' / 'index.html').read_text(encoding='utf-8')
    help_text = html.split('id="youtube-audio-format"', 1)[1] \
                    .split('setting-help-text', 1)[1].split('</div>', 1)[0].lower()
    assert 'quality' in help_text, help_text
    assert 'mp3' in help_text, help_text
    assert 'video' in help_text, "should also say neither option downloads the video"
