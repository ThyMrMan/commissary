"""Upgrading a track's FORMAT never replaced anything — it added a second copy.

Reported as "downloads never overwrite what already exists". Every replace and
upgrade decision in the import pipeline hung off ``os.path.exists(final_path)``,
an exact path match with the extension in it. But an upgrade is precisely a
change of container: replacing a 130 kbps ``.opus`` with a FLAC computes
``Track.flac``, does not find ``Track.opus``, and moves the new file in beside
the old one. Both then live in the library and the media server lists the track
twice.

The reported library had nine such pairs. Two of them isolate the cause
perfectly, because the naming is not even in question:

    01 - Courage.opus   +   01 - Courage.flac
    01 - STAY.opus      +   01 - STAY.flac

Byte-identical stems. The extension alone kept them apart.

Two things this must NOT do, both load-bearing:

  · It looks in ONE directory — the folder the incoming file is going into. A
    track downloaded as a single templates into its own album folder, so it can
    never reach into an album's folder and replace that album's copy. The user
    asked for exactly that behaviour, and here it is a structural property
    rather than a rule someone has to remember to apply.

  · A lossy file the lossy-copy feature maintains on purpose is a companion,
    not a stale original, and is never a replacement candidate.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from core.imports.pipeline import _existing_library_copy, _resolve_replace_target


def _touch(p: Path) -> Path:
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(b"\x00\x00")
    return p


NO_LOSSY = {"lossy_copy_enabled": False}


class TestTheReportedFailure:
    def test_an_opus_is_found_when_the_flac_is_computed(self, tmp_path):
        """THE regression, in the form the library proves it: same stem, and
        only the extension differs."""
        old = _touch(tmp_path / "01 - STAY.opus")
        got = _existing_library_copy(str(tmp_path / "01 - STAY.flac"), 1, NO_LOSSY)
        assert got == str(old)

    def test_nothing_is_reported_when_the_track_is_genuinely_new(self, tmp_path):
        _touch(tmp_path / "02 - Something Else.opus")
        assert _existing_library_copy(str(tmp_path / "01 - STAY.flac"), 1, NO_LOSSY) is None

    def test_an_empty_folder_reports_nothing(self, tmp_path):
        assert _existing_library_copy(str(tmp_path / "X.flac"), 1, NO_LOSSY) is None

    def test_a_folder_that_does_not_exist_reports_nothing(self, tmp_path):
        assert _existing_library_copy(str(tmp_path / "nope" / "X.flac"), 1, NO_LOSSY) is None

    @pytest.mark.parametrize("ext", [".opus", ".mp3", ".m4a", ".ogg", ".wav", ".aac"])
    def test_any_audio_container_counts_as_the_existing_copy(self, tmp_path, ext):
        old = _touch(tmp_path / ("Courage" + ext))
        assert _existing_library_copy(str(tmp_path / "Courage.flac"), 1, NO_LOSSY) == str(old)

    def test_sidecars_and_artwork_are_never_mistaken_for_the_track(self, tmp_path):
        for name in ("Courage.lrc", "Courage.nfo", "Courage.jpg", "cover.jpg"):
            _touch(tmp_path / name)
        assert _existing_library_copy(str(tmp_path / "Courage.flac"), 1, NO_LOSSY) is None


class TestTrackNumberPrefixes:
    """60% of the reported library is named ``NN - Title`` while the current
    template writes a bare title, so the prefix has to be tolerated — but only
    when it is provably the same track."""

    def test_a_prefixed_existing_name_matches_a_bare_template(self, tmp_path):
        old = _touch(tmp_path / "01 - Monsters (feat. Demi Lovato & blackbear).opus")
        got = _existing_library_copy(
            str(tmp_path / "Monsters (feat. Demi Lovato & blackbear).flac"), 1, NO_LOSSY)
        assert got == str(old)

    def test_a_different_track_number_is_refused(self, tmp_path):
        """The trap this guards: importing track 5, also called 'Intro', must
        not delete track 1's file just because the titles agree."""
        _touch(tmp_path / "01 - Intro.opus")
        assert _existing_library_copy(str(tmp_path / "Intro.flac"), 5, NO_LOSSY) is None

    def test_the_matching_track_number_is_accepted(self, tmp_path):
        old = _touch(tmp_path / "05 - Intro.opus")
        assert _existing_library_copy(str(tmp_path / "Intro.flac"), 5, NO_LOSSY) == str(old)

    def test_an_unknown_incoming_number_refuses_a_prefixed_name(self, tmp_path):
        """With no number to check against there is nothing to verify. A
        duplicate is recoverable; deleting the wrong track is not."""
        _touch(tmp_path / "01 - Intro.opus")
        assert _existing_library_copy(str(tmp_path / "Intro.flac"), None, NO_LOSSY) is None

    def test_an_unnumbered_existing_name_needs_no_number(self, tmp_path):
        old = _touch(tmp_path / "Intro.opus")
        assert _existing_library_copy(str(tmp_path / "Intro.flac"), None, NO_LOSSY) == str(old)

    def test_both_sides_numbered_still_matches(self, tmp_path):
        old = _touch(tmp_path / "01 - Courage.opus")
        assert _existing_library_copy(str(tmp_path / "01 - Courage.flac"), 1, NO_LOSSY) == str(old)

    def test_a_title_that_merely_starts_with_digits_is_not_stripped(self, tmp_path):
        """``1-800-273-8255`` is a title, not a track number."""
        old = _touch(tmp_path / "1-800-273-8255.opus")
        assert _existing_library_copy(str(tmp_path / "1-800-273-8255.flac"), 1, NO_LOSSY) == str(old)


class TestItNeverReachesOutOfTheFolder:
    """The user's explicit instruction: a single must not replace an album track.
    A single templates into its own album folder, so confining the search to the
    destination directory makes that impossible by construction."""

    def test_an_album_copy_in_another_folder_is_invisible(self, tmp_path):
        album = tmp_path / "Imagine Dragons - LOOM"
        single = tmp_path / "Imagine Dragons - Take Me to the Beach"
        _touch(album / "04 - Take Me to the Beach.opus")
        single.mkdir(parents=True, exist_ok=True)
        got = _existing_library_copy(str(single / "Take Me to the Beach.flac"), 1, NO_LOSSY)
        assert got is None

    def test_it_does_not_descend_into_subfolders(self, tmp_path):
        _touch(tmp_path / "Disc 2" / "Courage.opus")
        assert _existing_library_copy(str(tmp_path / "Courage.flac"), 1, NO_LOSSY) is None


class TestLossyCompanions:
    def test_a_deliberate_lossy_copy_is_not_a_replacement_candidate(self, tmp_path):
        _touch(tmp_path / "Courage.opus")
        profile = {"lossy_copy_enabled": True, "lossy_copy_codec": "opus"}
        assert _existing_library_copy(str(tmp_path / "Courage.flac"), 1, profile) is None

    def test_a_different_codec_than_the_configured_one_is_still_replaceable(self, tmp_path):
        old = _touch(tmp_path / "Courage.opus")
        profile = {"lossy_copy_enabled": True, "lossy_copy_codec": "mp3"}
        assert _existing_library_copy(str(tmp_path / "Courage.flac"), 1, profile) == str(old)

    def test_with_the_feature_off_the_same_file_is_replaceable(self, tmp_path):
        old = _touch(tmp_path / "Courage.opus")
        assert _existing_library_copy(str(tmp_path / "Courage.flac"), 1, NO_LOSSY) == str(old)

    def test_companions_only_apply_when_the_incoming_file_is_flac(self, tmp_path):
        old = _touch(tmp_path / "Courage.opus")
        profile = {"lossy_copy_enabled": True, "lossy_copy_codec": "opus"}
        assert _existing_library_copy(str(tmp_path / "Courage.m4a"), 1, profile) == str(old)


class TestAmbiguityIsRefused:
    def test_two_possible_copies_means_none(self, tmp_path):
        """Guessing which of two files to delete is not a decision code should
        make on its own."""
        _touch(tmp_path / "01 - Courage.opus")
        _touch(tmp_path / "Courage.m4a")
        assert _existing_library_copy(str(tmp_path / "Courage.flac"), 1, NO_LOSSY) is None

    def test_the_destination_itself_is_never_returned(self, tmp_path):
        """Otherwise the caller would be told to delete the file it is about to
        write, and the exact-path branch already owns that case."""
        _touch(tmp_path / "Courage.flac")
        assert _existing_library_copy(str(tmp_path / "Courage.flac"), 1, NO_LOSSY) is None


class TestWhatTheImportActuallyAsksFor:
    """``_resolve_replace_target`` is the seam the move block reads, so these
    are the answers that decide whether a file is replaced or duplicated."""

    def test_a_file_already_at_the_destination_still_wins(self, tmp_path):
        """The pre-existing behaviour, unchanged and still first."""
        dest = _touch(tmp_path / "Courage.flac")
        src = _touch(tmp_path / "incoming.flac")
        assert _resolve_replace_target(str(dest), str(src), 1, NO_LOSSY) == str(dest)

    def test_an_older_container_is_found_when_the_destination_is_free(self, tmp_path):
        """THE fix, at the level the pipeline reads it."""
        old = _touch(tmp_path / "01 - STAY.opus")
        src = _touch(tmp_path / "incoming.flac")
        got = _resolve_replace_target(str(tmp_path / "01 - STAY.flac"), str(src), 1, NO_LOSSY)
        assert got == str(old)

    def test_a_vanished_source_replaces_nothing(self, tmp_path):
        """Nothing is being placed, so nothing may be deleted — the pre-move
        recovery downstream owns this and must see it exactly as before."""
        _touch(tmp_path / "01 - STAY.opus")
        got = _resolve_replace_target(str(tmp_path / "01 - STAY.flac"),
                                      str(tmp_path / "gone.flac"), 1, NO_LOSSY)
        assert got is None

    def test_a_genuinely_new_track_replaces_nothing(self, tmp_path):
        src = _touch(tmp_path / "incoming.flac")
        assert _resolve_replace_target(str(tmp_path / "Brand New.flac"),
                                       str(src), 1, NO_LOSSY) is None

    def test_a_single_does_not_reach_into_an_album_folder(self, tmp_path):
        """The user's explicit instruction, asserted where it takes effect."""
        album = tmp_path / "Imagine Dragons - LOOM"
        single = tmp_path / "Imagine Dragons - Take Me to the Beach"
        _touch(album / "04 - Take Me to the Beach.opus")
        src = _touch(tmp_path / "incoming.flac")
        single.mkdir(parents=True, exist_ok=True)
        got = _resolve_replace_target(str(single / "Take Me to the Beach.flac"),
                                      str(src), 1, NO_LOSSY)
        assert got is None


def test_the_fix_can_never_turn_a_duplicate_into_a_deletion():
    """The invariant that makes this change safe to ship.

    Making the ``.opus`` visible also makes a previously-unreachable branch
    reachable: with ``replace_lower_quality`` off, "existing file already has
    metadata - skipping overwrite" DELETES the incoming download as redundant.
    Reached cross-container that would discard a FLAC because a 130 kbps copy
    exists — a brand new way to lose a file, where the old behaviour merely made
    a duplicate. So that branch stays exclusive to a same-path collision, and the
    cross-container case falls back to placing the file exactly as it did before.

    A source guard rather than a behavioural test: the branch sits inside
    ``post_process_matched_download``, which needs the whole import runtime to
    drive. The rule it encodes is small enough to read, and pinning it is worth
    more than leaving it unpinned.
    """
    src = Path(__file__).resolve().parents[2] / "core" / "imports" / "pipeline.py"
    text = src.read_text(encoding="utf-8")
    assert "elif existing_path != final_path:" in text
    guarded = text.split("elif existing_path != final_path:", 1)[1]
    # the cross-container branch must disarm the replace, not delete the download
    assert "existing_path = None" in guarded.split("else:", 1)[0]
    assert "os.remove(file_path)" not in guarded.split("else:", 1)[0]


def test_the_move_block_reads_the_seam():
    """Source guard, kept alongside the behavioural tests above because those
    exercise the seam rather than the caller."""
    src = Path(__file__).resolve().parents[2] / "core" / "imports" / "pipeline.py"
    text = src.read_text(encoding="utf-8")
    assert "existing_path = _resolve_replace_target(" in text
    assert "if existing_path:" in text
