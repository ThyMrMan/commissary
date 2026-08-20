"""Every Opus download was labelled with nothing at all.

Carried over from upstream 3.2.2 (part of #1154). In the library that reported
it, 507 of 521 YouTube downloads had recorded no ``audio_quality`` and no
``bitrate`` — while the 14 M4A ones beside them labelled fine.

The cause is one attribute that does not exist:

    mutagen.oggopus.OggOpusInfo declares `length` and `channels`, and its
    __init__ never sets `bitrate`.

``get_audio_quality_string`` did ``OggOpus(path).info.bitrate // 1000``. That
raises AttributeError, the surrounding bare ``except`` swallowed it, and the
function returned the empty string — which every caller reads as "unknown file"
rather than "unreadable field". The same line existed in a second copy inside
web_server.py, so both were wrong in the same way while only one of them knew
about the other cases.

Three sources of truth are now tried in order: mutagen's header, the SOURCE's
own pre-download claim (a YouTube itag) when the file is still that codec, and
finally size ÷ duration. The claim is never copied onto a transcoded MP3 — the
original stream's bitrate says nothing about a file that was re-encoded.

Opus is always VBR, so any figure is an average; ``stream_uses_vbr_display``
marks the ones that should be shown as such rather than as a precise constant.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from core.imports import file_ops as fo


class TestThePremise:
    def test_mutagen_really_cannot_read_an_opus_bitrate(self):
        """If this ever becomes false the whole fix is unnecessary, and the
        estimate below would be silently preferring a worse number."""
        from mutagen.oggopus import OggOpusInfo
        assert not hasattr(OggOpusInfo, "bitrate")
        assert hasattr(OggOpusInfo, "length") and hasattr(OggOpusInfo, "channels")


# an OggOpusInfo look-alike: a length, no bitrate attribute at all
def _opus_info(length=174.4):
    return SimpleNamespace(length=length, channels=2)


def _sized(tmp_path, name, kbytes):
    p = tmp_path / name
    p.write_bytes(b"\x00" * (kbytes * 1024))
    return p


class TestKbpsFromStreamInfo:
    def test_mutagen_wins_when_it_has_an_answer(self, tmp_path):
        f = _sized(tmp_path, "a.mp3", 100)
        info = SimpleNamespace(bitrate=320000, length=10.0)
        assert fo._kbps_from_stream_info(info, str(f)) == 320

    def test_an_opus_info_falls_back_to_size_over_duration(self, tmp_path):
        # 1024 KB over 60s ≈ 139 kbps
        f = _sized(tmp_path, "a.opus", 1024)
        assert fo._kbps_from_stream_info(_opus_info(60.0), str(f)) == pytest.approx(139, abs=2)

    def test_estimate_can_be_refused(self, tmp_path):
        f = _sized(tmp_path, "a.opus", 1024)
        assert fo._kbps_from_stream_info(_opus_info(60.0), str(f), estimate=False) is None

    def test_no_info_at_all_is_survivable(self, tmp_path):
        f = _sized(tmp_path, "a.opus", 100)
        assert fo._kbps_from_stream_info(None, str(f), estimate=False) is None

    def test_a_zero_length_yields_nothing_rather_than_dividing_by_it(self, tmp_path):
        f = _sized(tmp_path, "a.opus", 100)
        assert fo._kbps_from_stream_info(_opus_info(0), str(f)) is None

    def test_a_missing_file_yields_nothing(self):
        assert fo._kbps_from_stream_info(_opus_info(60.0), "/nope/gone.opus") is None


class TestTheSourcesClaim:
    # The alias table is keyed by the CLAIM: a source claiming "opus" is happy
    # to find opus, ogg or webm on disk, because that one stream can be written
    # into any of those containers. The reverse does not hold.
    @pytest.mark.parametrize("disk,claim", [
        ("opus", "opus"), ("ogg", "opus"), ("webm", "opus"),
        ("opus", "ogg"), ("ogg", "ogg"),
        ("m4a", "aac"), ("aac", "m4a"), ("mp4", "aac"),
        ("mp3", "mp3"),
    ])
    def test_a_matching_codec_accepts_the_claim(self, disk, claim):
        assert fo._claim_kbps(disk, claim, 160) == 160

    @pytest.mark.parametrize("disk,claim", [
        ("mp3", "opus"),     # the transcode case, below
        ("opus", "aac"),
        ("m4a", "opus"),
        ("flac", "opus"),
    ])
    def test_a_mismatched_codec_declines_it(self, disk, claim):
        assert fo._claim_kbps(disk, claim, 160) is None

    def test_an_opus_claim_is_never_copied_onto_an_mp3(self):
        """THE trap. Re-encoding to MP3 320 discards whatever the original
        stream was; carrying its number across would report a bitrate the file
        on disk does not have."""
        assert fo._claim_kbps("mp3", "opus", 160) is None

    @pytest.mark.parametrize("bad", [None, "", 0, -5, "abc"])
    def test_a_useless_claim_is_declined(self, bad):
        assert fo._claim_kbps("opus", "opus", bad) is None

    def test_no_claim_at_all(self):
        assert fo._claim_kbps("opus", None, 160) is None


class TestTheQualityChip:
    def _as_opus(self, monkeypatch, info):
        import mutagen.oggopus as m
        monkeypatch.setattr(m, "OggOpus", lambda p: SimpleNamespace(info=info))

    def test_an_opus_file_is_no_longer_labelled_with_nothing(self, tmp_path, monkeypatch):
        """THE regression: this returned '' for every Opus file ever imported."""
        self._as_opus(monkeypatch, _opus_info(60.0))
        f = _sized(tmp_path, "a.opus", 1024)
        out = fo.get_audio_quality_string(str(f))
        assert out.startswith("OPUS-"), out

    def test_the_sources_itag_claim_is_preferred_over_an_estimate(self, tmp_path, monkeypatch):
        self._as_opus(monkeypatch, _opus_info(60.0))
        f = _sized(tmp_path, "a.opus", 1024)
        assert fo.get_audio_quality_string(
            str(f), claimed_format="opus", claimed_bitrate=160) == "OPUS-160"

    def test_a_transcoded_mp3_ignores_the_opus_claim(self, tmp_path, monkeypatch):
        import mutagen.mp3 as m
        monkeypatch.setattr(m, "MP3", lambda p: SimpleNamespace(
            info=SimpleNamespace(length=60.0, bitrate_mode=None)))
        f = _sized(tmp_path, "a.mp3", 1024)
        out = fo.get_audio_quality_string(str(f), claimed_format="opus", claimed_bitrate=160)
        assert "160" not in out, out

    def test_an_unreadable_bitrate_still_names_the_format(self, tmp_path, monkeypatch):
        """A bare 'OPUS' beats '', which reads as 'we could not identify this
        file at all'."""
        self._as_opus(monkeypatch, SimpleNamespace(length=0, channels=2))
        f = tmp_path / "a.opus"
        f.write_bytes(b"")
        assert fo.get_audio_quality_string(str(f)) == "OPUS"

    def test_an_ogg_container_holding_opus_is_not_a_dead_end(self, tmp_path, monkeypatch):
        """yt-dlp writes Opus into .ogg too; reading it as Vorbis raises."""
        import mutagen.oggvorbis as mv
        monkeypatch.setattr(mv, "OggVorbis", lambda p: (_ for _ in ()).throw(ValueError("not vorbis")))
        self._as_opus(monkeypatch, _opus_info(60.0))
        f = _sized(tmp_path, "a.ogg", 1024)
        assert fo.get_audio_quality_string(str(f)).startswith("OPUS-")

    def test_an_unknown_extension_is_still_empty(self, tmp_path):
        f = _sized(tmp_path, "a.txt", 10)
        assert fo.get_audio_quality_string(str(f)) == ""


class TestProbeAudioQuality:
    def test_probing_an_opus_file_no_longer_raises_on_a_missing_attribute(
            self, tmp_path, monkeypatch):
        """It asked OggOpusInfo for bitrate AND sample_rate; it has neither."""
        import mutagen.oggopus as m
        monkeypatch.setattr(m, "OggOpus", lambda p: SimpleNamespace(info=_opus_info(60.0)))
        f = _sized(tmp_path, "a.opus", 1024)
        aq = fo.probe_audio_quality(str(f))
        assert aq is not None and aq.format == "opus"
        assert aq.bitrate and aq.bitrate > 0
        # Opus is 48 kHz internally — a fact, not a guess
        assert aq.sample_rate == 48000


class TestEstimateBitrate:
    def test_size_over_duration(self):
        # 1 MB over 60s ≈ 139 kbps
        assert fo.estimate_bitrate_kbps(size_bytes=1024 * 1024,
                                        duration_seconds=60) == pytest.approx(139, abs=2)

    def test_milliseconds_are_accepted(self):
        a = fo.estimate_bitrate_kbps(size_bytes=1024 * 1024, duration_ms=60000)
        b = fo.estimate_bitrate_kbps(size_bytes=1024 * 1024, duration_seconds=60)
        assert a == b

    @pytest.mark.parametrize("kw", [
        {"size_bytes": 0, "duration_seconds": 60},
        {"size_bytes": 1024, "duration_seconds": 0},
        {"size_bytes": None, "duration_seconds": None},
        {"size_bytes": "x", "duration_seconds": "y"},
    ])
    def test_nothing_usable_yields_none(self, kw):
        assert fo.estimate_bitrate_kbps(**kw) is None


class TestVbrDisplay:
    @pytest.mark.parametrize("name", ["a.opus", "a.ogg", "a.m4a", "a.aac", "a.wma"])
    def test_formats_whose_bitrate_is_always_an_average(self, name):
        assert fo.stream_uses_vbr_display(name) is True

    @pytest.mark.parametrize("name", ["a.flac", "a.wav", "a.mp3", ""])
    def test_everything_else_is_not_marked_by_extension_alone(self, name):
        assert fo.stream_uses_vbr_display(name) is False

    def test_an_mp3_is_marked_only_when_mutagen_says_vbr(self):
        from mutagen.mp3 import BitrateMode
        assert fo.stream_uses_vbr_display(
            "a.mp3", SimpleNamespace(bitrate_mode=BitrateMode.VBR)) is True
        assert fo.stream_uses_vbr_display(
            "a.mp3", SimpleNamespace(bitrate_mode=BitrateMode.CBR)) is False


class TestFillMissingTrackBitrate:
    def test_a_zero_bitrate_row_gets_an_average(self):
        row = fo.fill_missing_track_bitrate(
            {"bitrate": 0, "file_size": 1024 * 1024, "duration": 60000,
             "file_path": "/lib/a.opus"})
        assert row["bitrate"] == pytest.approx(139, abs=2)
        assert row["bitrate_vbr"] == 1

    def test_a_real_bitrate_is_left_alone(self):
        row = fo.fill_missing_track_bitrate(
            {"bitrate": 320, "file_size": 1024 * 1024, "duration": 60000,
             "file_path": "/lib/a.mp3"})
        assert row["bitrate"] == 320
        assert "bitrate_vbr" not in row

    def test_a_lossless_row_is_not_marked_as_an_average(self):
        row = fo.fill_missing_track_bitrate(
            {"bitrate": 1076, "file_path": "/lib/a.flac"})
        assert "bitrate_vbr" not in row

    def test_a_row_with_nothing_to_go_on_is_returned_unchanged(self):
        row = fo.fill_missing_track_bitrate({"bitrate": None, "file_path": "/lib/a.flac"})
        assert row.get("bitrate") is None


def test_web_server_no_longer_carries_its_own_copy():
    """Source guard. Two implementations of this had already drifted — both
    wrong about Opus, only one aware of the rest."""
    from pathlib import Path
    src = (Path(__file__).resolve().parents[2] / "web_server.py").read_text(
        encoding="utf-8", errors="replace")
    assert "from core.imports.file_ops import get_audio_quality_string" in src
    assert 'return f"OPUS-{bitrate_kbps}"' not in src
