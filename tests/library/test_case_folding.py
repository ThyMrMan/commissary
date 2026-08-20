"""Metadata casing that disagrees with disk made a SECOND album folder (#1091).

Ported from upstream SoulSync 3.2.2. Reorganize and download both build every
destination folder from metadata, so if the metadata's casing differs from the
folder already on disk you get two folders for one album — and it breaks
differently on each kind of filesystem, both badly:

  · case-SENSITIVE (Linux, which is what Docker runs): two real directories, so
    the album appears twice in Plex/Jellyfin and neither half knows about the
    other;
  · case-INSENSITIVE (Windows, default macOS): the write lands in the FIRST
    folder, but the path recorded is not how the directory is spelled — so every
    later exact-path lookup misses.

The resolver reads from the directory LISTING rather than ``os.path.isdir``,
because isdir is precisely the check that cannot answer this: on a
case-insensitive filesystem it returns True for a name spelled differently, so
trusting it fixes only the case-sensitive half while appearing to fix both.

The FILENAME is never folded. Two tracks differing only in case are two files,
and folding would overwrite one with the other and lose audio.
"""

from __future__ import annotations

import os

import pytest

from core.library import case_folding as cf


@pytest.fixture(autouse=True)
def _clear_cache():
    cf._LISTING_CACHE.clear()
    yield
    cf._LISTING_CACHE.clear()


class TestReusingAnExistingFolder:
    def test_a_differently_cased_folder_is_reused(self, tmp_path):
        """THE regression: the album is already on disk as 'Artist', and the
        metadata says 'artist'."""
        (tmp_path / "Kanaria" / "Kanaria - Dec").mkdir(parents=True)
        out = cf.resolve_existing_case_dir(str(tmp_path), "kanaria/kanaria - dec")
        assert out == os.path.join(str(tmp_path), "Kanaria", "Kanaria - Dec")

    def test_an_exact_match_is_returned_unchanged(self, tmp_path):
        (tmp_path / "Kanaria").mkdir()
        out = cf.resolve_existing_case_dir(str(tmp_path), "Kanaria")
        assert out == os.path.join(str(tmp_path), "Kanaria")

    def test_a_genuinely_new_folder_keeps_the_callers_casing(self, tmp_path):
        out = cf.resolve_existing_case_dir(str(tmp_path), "Brand New/Brand New - X")
        assert out == os.path.join(str(tmp_path), "Brand New", "Brand New - X")

    def test_resolution_stops_at_the_first_missing_component(self, tmp_path):
        """Nothing below a missing directory can exist either, so the rest keeps
        the caller's casing and gets created as asked."""
        (tmp_path / "Kanaria").mkdir()
        out = cf.resolve_existing_case_dir(str(tmp_path), "kanaria/NEW ALBUM/Disc 1")
        assert out == os.path.join(str(tmp_path), "Kanaria", "NEW ALBUM", "Disc 1")

    def test_the_root_is_never_rewritten(self, tmp_path):
        """It can never walk out of the managed tree or rename a library root."""
        root = tmp_path / "MUSIC"
        root.mkdir()
        out = cf.resolve_existing_case_dir(str(root), "x")
        assert out.startswith(str(root))

    def test_an_empty_relative_path_returns_the_root_itself(self, tmp_path):
        """join(root, '') would append a separator, which then compares unequal
        to every other spelling of the same directory."""
        assert cf.resolve_existing_case_dir(str(tmp_path), "") == str(tmp_path)

    def test_a_file_is_ignored_when_looking_for_a_directory(self, tmp_path):
        (tmp_path / "kanaria").write_bytes(b"")      # a FILE with that name
        out = cf.resolve_existing_case_dir(str(tmp_path), "Kanaria")
        assert out == os.path.join(str(tmp_path), "Kanaria")

    def test_backslashes_are_accepted_as_separators(self, tmp_path):
        (tmp_path / "Kanaria" / "Dec").mkdir(parents=True)
        out = cf.resolve_existing_case_dir(str(tmp_path), "kanaria\\dec")
        assert out == os.path.join(str(tmp_path), "Kanaria", "Dec")


class TestTheFilenameIsNeverFolded:
    def test_only_the_directories_are_resolved(self, tmp_path):
        """Two tracks differing only in case are two files; folding would
        overwrite one with the other and lose audio."""
        (tmp_path / "Kanaria").mkdir()
        out = cf.resolve_existing_case_path(str(tmp_path), "kanaria/DEC.flac")
        assert out == os.path.join(str(tmp_path), "Kanaria", "DEC.flac")

    def test_a_bare_filename_is_joined_to_the_root(self, tmp_path):
        out = cf.resolve_existing_case_path(str(tmp_path), "Dec.flac")
        assert out == os.path.join(str(tmp_path), "Dec.flac")

    def test_an_empty_path_returns_the_root(self, tmp_path):
        assert cf.resolve_existing_case_path(str(tmp_path), "") == str(tmp_path)


class TestAnAlreadySplitLibrary:
    def test_several_matches_resolve_deterministically(self, tmp_path):
        """Only possible on a case-sensitive filesystem that already split. The
        answer must be stable, or a reorganize would move files back and forth
        on every pass."""
        for name in ("Kanaria", "kanaria", "KANARIA"):
            try:
                (tmp_path / name).mkdir()
            except FileExistsError:
                pass                              # case-insensitive host
        a = cf.resolve_existing_case_dir(str(tmp_path), "kAnArIa")
        cf._LISTING_CACHE.clear()
        b = cf.resolve_existing_case_dir(str(tmp_path), "kAnArIa")
        assert a == b


class TestTheListingCache:
    def test_a_hit_is_served_from_cache(self, tmp_path):
        (tmp_path / "Kanaria").mkdir()
        cf.resolve_existing_case_dir(str(tmp_path), "kanaria")
        assert str(tmp_path) in cf._LISTING_CACHE

    def test_a_miss_forces_a_fresh_read(self, tmp_path):
        """A stale MISS is the answer that creates a duplicate folder, and mtime
        is too coarse to trust for that — a directory created inside the same
        granularity window leaves it unchanged."""
        cf.resolve_existing_case_dir(str(tmp_path), "kanaria")     # seeds a miss
        (tmp_path / "Kanaria").mkdir()                             # appears after
        out = cf.resolve_existing_case_dir(str(tmp_path), "kanaria")
        assert out == os.path.join(str(tmp_path), "Kanaria")

    def test_an_unreadable_parent_is_survivable(self, tmp_path):
        out = cf.resolve_existing_case_dir(str(tmp_path / "nope"), "a/b")
        assert out.endswith(os.path.join("a", "b"))


def test_the_path_builder_actually_uses_it():
    """Source guard: the resolver is inert unless build_final_path_for_track
    routes through it, and BOTH branches must — the templated one and the
    fallback — or preview and apply disagree about where an album lives."""
    from pathlib import Path
    src = (Path(__file__).resolve().parents[2] / "core" / "imports"
           / "paths.py").read_text(encoding="utf-8")
    assert "from core.library.case_folding import resolve_existing_case_dir" in src
    assert src.count("resolve_existing_case_dir(") >= 3    # import + both branches
