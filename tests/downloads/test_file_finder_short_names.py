"""A short track title made its own finished download invisible.

Reported as "Dec. never finishes downloading". The FLAC landed in the download
folder and then nothing happened — no post-processing, no library row, the file
just sat there.

The fuzzy tier compared a bare title against a filename that still carried its
extension:

    target (Deezer hands over ``id||Artist - Title``):  'kanaria - dec'
    the file sitting on disk:                           'kanaria - dec flac'
                                                         0.839  vs threshold 0.85

Nothing about the match was wrong except a constant ``' flac'`` the target could
not possibly contain. Being a fixed number of characters, it is only ever fatal
to SHORT names — a long title dilutes the same five characters to nothing, which
is why this looked arbitrary rather than systematic. Anything normalising to 14
characters or fewer could never be found.

The extension now comes off BOTH sides. Both matters: Soulseek reports a real
remote path whose basename does carry an extension, so stripping only the
on-disk side would hand every Soulseek transfer the penalty this removes.
"""

from __future__ import annotations

from difflib import SequenceMatcher
from pathlib import Path

import pytest

from core.downloads.file_finder import (
    _FUZZY_THRESHOLD,
    _normalize_for_finding,
    _strip_audio_ext,
    find_completed_audio_file,
)


def _touch(path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"\x00\x00")
    return path


def _score(target: str, on_disk: str) -> float:
    return SequenceMatcher(None, _normalize_for_finding(_strip_audio_ext(target)),
                           _normalize_for_finding(_strip_audio_ext(on_disk))).ratio()


class TestTheReportedFailure:
    def test_a_short_title_finds_its_own_file(self, tmp_path):
        """THE regression. Scored 0.839 against a 0.85 threshold and was
        reported missing while sitting in the download folder."""
        _touch(tmp_path / "Kanaria - Dec.flac")
        found, where = find_completed_audio_file(str(tmp_path),
                                                 "2741706671||Kanaria - Dec.")
        assert found is not None and Path(found).name == "Kanaria - Dec.flac"
        assert where == "downloads"

    @pytest.mark.parametrize("title", [
        "A - B", "Kanaria - Dec.", "Sia - Cheap", "AJR - Bang!", "Zedd - Stay",
    ])
    def test_short_titles_in_general(self, tmp_path, title):
        """It was never about this one track: every one of these normalises to
        under 15 characters and so could never be found."""
        _touch(tmp_path / (title + ".flac"))
        found, _ = find_completed_audio_file(str(tmp_path), "123||" + title)
        assert found is not None, "%r scored %.3f" % (title, _score(title, title + ".flac"))

    def test_the_old_scoring_really_did_fail(self):
        """Guards the diagnosis itself: WITHOUT the strip, the reported case is
        below threshold. If this ever stops being true the fix above is no
        longer testing what it claims to."""
        unstripped = SequenceMatcher(
            None, _normalize_for_finding("Kanaria - Dec."),
            _normalize_for_finding("Kanaria - Dec.flac")).ratio()
        assert unstripped < _FUZZY_THRESHOLD


class TestBothSidesAreStripped:
    def test_a_soulseek_target_carrying_an_extension_still_matches(self, tmp_path):
        """Soulseek's api_filename is a real remote PATH — its basename has an
        extension. Strip only the on-disk side and this breaks."""
        _touch(tmp_path / "03 - Song Title.mp3")
        found, _ = find_completed_audio_file(str(tmp_path), "music/album/03 - Song Title.mp3")
        assert found is not None

    def test_a_source_that_promised_mp3_and_delivered_flac_matches(self, tmp_path):
        _touch(tmp_path / "03 - Song Title.flac")
        found, _ = find_completed_audio_file(str(tmp_path), "music/album/03 - Song Title.mp3")
        assert found is not None


class TestStripAudioExtIsNarrow:
    """``os.path.splitext`` splits on the LAST dot wherever it falls, so it is
    not safe to point at a title. Only a real audio extension comes off."""

    @pytest.mark.parametrize("name,expected", [
        ("Kanaria - Dec.flac", "Kanaria - Dec"),
        ("Song.MP3", "Song"),
        # a title whose own punctuation would confuse a blind splitext
        ("The Killers - Mr. Brightside", "The Killers - Mr. Brightside"),
        ("Kanaria - Dec.", "Kanaria - Dec."),
        ("Some Band - 8.5", "Some Band - 8.5"),
        ("No Dots Here", "No Dots Here"),
        ("", ""),
    ])
    def test_only_audio_extensions_are_removed(self, name, expected):
        assert _strip_audio_ext(name) == expected

    def test_a_title_with_an_internal_dot_is_not_truncated(self, tmp_path):
        _touch(tmp_path / "The Killers - Mr. Brightside.flac")
        found, _ = find_completed_audio_file(str(tmp_path),
                                             "1||The Killers - Mr. Brightside")
        assert found is not None


class TestItStillRefusesTheWrongFile:
    def test_an_unrelated_file_is_not_matched(self, tmp_path):
        """Loosening the comparison must not turn the finder into a shrug."""
        _touch(tmp_path / "Some Other Artist - Whatever.flac")
        found, where = find_completed_audio_file(str(tmp_path), "1||Kanaria - Dec.")
        assert found is None and where is None

    def test_a_different_short_track_is_not_matched(self, tmp_path):
        _touch(tmp_path / "Kanaria - Kyu.flac")
        found, _ = find_completed_audio_file(str(tmp_path), "1||Kanaria - Dec.")
        assert found is None

    def test_a_non_audio_file_is_never_returned(self, tmp_path):
        _touch(tmp_path / "Kanaria - Dec.txt")
        _touch(tmp_path / "Kanaria - Dec.jpg")
        found, _ = find_completed_audio_file(str(tmp_path), "1||Kanaria - Dec.")
        assert found is None

    def test_the_right_file_wins_when_a_near_miss_sits_beside_it(self, tmp_path):
        _touch(tmp_path / "Kanaria - Kyu.flac")
        _touch(tmp_path / "Kanaria - Dec.flac")
        found, _ = find_completed_audio_file(str(tmp_path), "1||Kanaria - Dec.")
        assert Path(found).name == "Kanaria - Dec.flac"
