"""A finished download nested below a category folder still imports (old fork 1.8.18).

Reported as: a movie that landed as ``Movie A/Movie A/Movie A.mkv`` never
imported after the download finished.

The nesting inside the release folder was a red herring — the video importer
walks a release recursively and finds a file at any depth. The failure is one
step earlier, in ``resolve_reported_save_path``: translating the path the
download client reports (from inside ITS container) into one this process can
read. When the reported path isn't directly readable it falls back to finding a
same-named folder under a known download root, and that scan went exactly ONE
level down.

Which could not reach the layout the code's own comment named:

    # Clients sort finished downloads into CATEGORY folders —
    # '<root>/complete/Movies/<release>'

``<root>/complete/Movies/<release>`` is TWO levels below the root, so a client
sorting into category folders resolved to nothing, and the download sat there
never importing. The scan now descends ``download_source.import_search_depth``
levels (default 3).

Depth is the OUTER loop, so shallower still wins: a release directly under one
root must beat a same-named folder buried under another, or a stale copy in an
old category folder could shadow the real one.

Shared by music and video — both sides resolve client paths through here.
"""

from __future__ import annotations

import os

import pytest

from core.commissary import download_depth as dd
from core.download_plugins import album_bundle as ab


@pytest.fixture()
def root(tmp_path):
    return tmp_path


def _cfg(root, **extra):
    values = {"soulseek.download_path": str(root)}
    values.update(extra)
    return lambda k, d=None: values.get(k, d)


def _release(root, *parts, filename="movie.mkv"):
    """Create <root>/<parts…>/ containing one file, and return the folder."""
    folder = os.path.join(str(root), *parts)
    os.makedirs(folder, exist_ok=True)
    with open(os.path.join(folder, filename), "wb") as fh:
        fh.write(b"x" * 128)
    return folder


# ── the reported failure ─────────────────────────────────────────────────────
def test_a_release_under_a_category_folder_resolves(root):
    """THE regression test: two levels down, the layout the old comment named."""
    want = _release(root, "complete", "Movies", "Movie A", "Movie A")
    got = ab.resolve_reported_save_path("/downloads/Movie A", _cfg(root))
    assert got == os.path.dirname(want), got


def test_one_level_still_resolves(root):
    """The case that always worked must keep working."""
    want = _release(root, "Movies", "Movie B")
    assert ab.resolve_reported_save_path("/downloads/Movie B", _cfg(root)) == want


def test_directly_under_the_root_still_resolves(root):
    want = _release(root, "Movie C")
    assert ab.resolve_reported_save_path("/downloads/Movie C", _cfg(root)) == want


def test_a_bare_file_in_a_category_folder_resolves(root):
    """A single-file release with no folder of its own."""
    folder = os.path.join(str(root), "complete", "TV")
    os.makedirs(folder)
    path = os.path.join(folder, "Ep.mkv")
    with open(path, "wb") as fh:
        fh.write(b"x" * 128)
    assert ab.resolve_reported_save_path("/downloads/Ep.mkv", _cfg(root)) == path


def test_beyond_the_depth_limit_is_left_unresolved(root):
    """Not a silent success: the caller's own 'no video found' error should
    surface with both paths logged, rather than a wrong folder being picked."""
    _release(root, "a", "b", "c", "d", "e", "Deep Movie")
    reported = "/downloads/Deep Movie"
    assert ab.resolve_reported_save_path(reported, _cfg(root, **{
        "download_source.import_search_depth": 2})) == reported


# ── shallower always wins ────────────────────────────────────────────────────
def test_a_shallow_match_beats_a_deeper_one(root):
    """Depth is the outer loop for exactly this: an old copy in a category
    folder must not shadow the release sitting at the root."""
    shallow = _release(root, "Movie D")
    _release(root, "complete", "Movies", "Movie D")
    assert ab.resolve_reported_save_path("/downloads/Movie D", _cfg(root)) == shallow


def test_a_shallow_match_under_a_later_root_beats_a_deep_one_under_an_earlier(tmp_path):
    """Across roots too — the ordering has to be global, not per-root."""
    first = tmp_path / "first"
    second = tmp_path / "second"
    first.mkdir(); second.mkdir()
    _release(first, "complete", "Movies", "Movie E")
    shallow = _release(second, "Movie E")
    cfg = lambda k, d=None: {                                     # noqa: E731
        "download_source.torrent_download_path": [str(first)],
        "soulseek.download_path": str(second),
    }.get(k, d)
    assert ab.resolve_reported_save_path("/downloads/Movie E", cfg) == shallow


# ── the guards that must survive ─────────────────────────────────────────────
def test_the_content_check_still_rejects_a_wrong_match(root):
    """A same-named folder in the wrong category must not be accepted just
    because the search now reaches further."""
    _release(root, "complete", "Movies", "Movie F")
    reported = "/downloads/Movie F"
    assert ab.resolve_reported_save_path(reported, _cfg(root),
                                         expect_name="not-in-there.mkv") == reported


def test_a_readable_reported_path_short_circuits(root):
    """Deeper scanning must not override mounts that already line up."""
    direct = _release(root, "Movie G")
    assert ab.resolve_reported_save_path(direct, _cfg(root)) == direct


def test_an_explicit_mapping_still_wins_over_the_scan(root):
    """The remote-path-mapping escape hatch is tried before the fallback."""
    mapped = _release(root, "elsewhere", "Movie H")
    cfg = lambda k, d=None: {                                     # noqa: E731
        "soulseek.download_path": str(root),
        "download_source.path_mappings": [
            {"from": "/client/dl", "to": os.path.join(str(root), "elsewhere")}],
    }.get(k, d)
    assert ab.resolve_reported_save_path("/client/dl/Movie H", cfg) == mapped


def test_nothing_found_returns_the_reported_path_unchanged(root):
    reported = "/downloads/Not Here At All"
    assert ab.resolve_reported_save_path(reported, _cfg(root)) == reported


# ── the descent itself ───────────────────────────────────────────────────────
def test_the_descent_is_breadth_first(root):
    """Shallowest first is the contract the caller relies on; a depth-first
    walk would return the deep folder before the shallow one."""
    os.makedirs(os.path.join(str(root), "a", "b", "c"))
    os.makedirs(os.path.join(str(root), "z"))
    depths = [d for d, _p in dd.descendant_dirs(root, 3, 100)]
    assert depths == sorted(depths), depths


def test_the_descent_respects_its_budget(root):
    for i in range(30):
        os.makedirs(os.path.join(str(root), "d%02d" % i, "inner"))
    assert len(dd.descendant_dirs(root, 3, 10)) <= 10


def test_the_descent_survives_an_unreadable_branch(root, monkeypatch):
    os.makedirs(os.path.join(str(root), "ok"))
    real = dd.Path.iterdir

    def _boom(self):
        if self.name == "boom":
            raise OSError("permission denied")
        return real(self)

    os.makedirs(os.path.join(str(root), "boom"))
    monkeypatch.setattr(dd.Path, "iterdir", _boom)
    names = {p.name for _d, p in dd.descendant_dirs(root, 3, 100)}
    assert "ok" in names          # the readable branch still came back


def test_a_missing_root_is_not_fatal(tmp_path):
    assert dd.descendant_dirs(tmp_path / "nope", 3, 100) == []


# ── the depth setting ────────────────────────────────────────────────────────
def test_the_default_depth_reaches_a_category_layout():
    """Two levels is the reported case, so the default has to exceed it."""
    assert dd.DEFAULT_SEARCH_DEPTH >= 2


def test_the_depth_is_configurable_and_clamped():
    assert dd.search_depth(lambda k, d=None: 2) == 2
    assert dd.search_depth(lambda k, d=None: 0) == 1            # floor
    assert dd.search_depth(lambda k, d=None: 99) == dd.MAX_SEARCH_DEPTH
    assert dd.search_depth(lambda k, d=None: "junk") == dd.DEFAULT_SEARCH_DEPTH
    assert dd.search_depth(lambda k, d=None: None) == dd.DEFAULT_SEARCH_DEPTH


# ── from the old fork's multi-folder release ────────────────────────────────
def test_the_client_reporting_its_save_dir_finds_the_category_folder(tmp_path):
    """Torrent clients report the save DIRECTORY; the release lives under
    <root>/<category>/."""
    category = tmp_path / "complete" / "Movies"
    (category / "Some.Movie.2024").mkdir(parents=True)
    cfg = lambda k, d=None: {"download_source.torrent_download_path": [str(tmp_path / "complete")]}.get(k, d)  # noqa: E731
    assert ab.resolve_reported_save_path("/downloads", cfg, expect_name="Some.Movie.2024") == str(category)


def test_a_single_file_release_in_a_category_folder_is_handed_back_itself(tmp_path):
    """Never the folder around it: that would donate every other download in
    the category to this job (upstream's rule for a root)."""
    category = tmp_path / "complete" / "Audiobooks"
    category.mkdir(parents=True)
    (category / "Book.m4b").write_bytes(b"x")
    (category / "Other.m4b").write_bytes(b"x")
    cfg = lambda k, d=None: {"download_source.torrent_download_path": str(tmp_path / "complete")}.get(k, d)  # noqa: E731
    assert ab.resolve_reported_save_path("/downloads", cfg, expect_name="Book.m4b") == str(category / "Book.m4b")


def test_a_semicolon_list_of_roots_reaches_its_category_folders(tmp_path):
    music = tmp_path / "music"
    (music / "complete" / "flac" / "Album X").mkdir(parents=True)
    cfg = lambda k, d=None: {"download_source.usenet_download_path": f"{tmp_path / 'tv'}; {music}"}.get(k, d)  # noqa: E731
    assert ab.resolve_reported_save_path("/sab/complete/Album X", cfg) == str(music / "complete" / "flac" / "Album X")


def test_within_one_level_the_newest_copy_wins(tmp_path):
    """Two category folders hold the name: the download that just finished,
    not a stale copy from an earlier grab - upstream's rule across roots."""
    old = tmp_path / "a" / "Rel"
    new = tmp_path / "b" / "Rel"
    old.mkdir(parents=True)
    new.mkdir(parents=True)
    os.utime(old, (1_000_000, 1_000_000))
    cfg = lambda k, d=None: {"soulseek.download_path": str(tmp_path)}.get(k, d)  # noqa: E731
    assert ab.resolve_reported_save_path("/downloads/Rel", cfg) == str(new)
