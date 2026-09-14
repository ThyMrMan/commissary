"""The shared rules behind "Prefer deluxe editions".

Every surface the option touches — download ownership, the watchlist, the artist
page, Album Consistency, the bundle picker — asks these functions what "the same
album" and "a smaller edition" mean, so they are pinned here against the titles
from a real library where deluxe downloads were being split.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from core.edition_preference import (
    PREFER_DELUXE_KEY,
    base_album_title,
    drop_smaller_editions,
    has_deluxe_marker,
    is_smaller_edition,
    owned_row_is_smaller_edition,
    prefer_deluxe_enabled,
    same_album_family,
)


# ── what names a bigger edition ──────────────────────────────────────────────
@pytest.mark.parametrize("title", [
    "Curtain Call: The Hits (Deluxe Edition)",
    "V (Deluxe)",
    "Wild Ones (Deluxe Version)",
    "The Slim Shady LP (Expanded Edition)",
    "The Marshall Mathers LP (25th Anniversary)",
    "Brave Freak Out (Special Edition)",
    "Album [Super Deluxe]",
    "The Death of Slim Shady (Coup De Grâce): Expanded Mourner’s Edition",
    "Album - Deluxe Edition",
    "Album Deluxe",
    "Album (Bonus Track Version)",
])
def test_bigger_editions_are_recognised(title):
    assert has_deluxe_marker(title)


@pytest.mark.parametrize("title", [
    "Curtain Call: The Hits",
    "Relapse: Refill",
    "Hotel California (Remastered)",
    "Thriller - 2011 Remaster",
    "Fearless (Taylor's Version)",
    "Empty Mermaid (Limited Edition)",
    "Album (Live on Tour)",
    "Album (Explicit)",
    "Eminem Presents The Re-Up",
    "",
    None,
])
def test_same_tracklist_variants_are_not_bigger_editions(title):
    assert not has_deluxe_marker(title)


# ── what counts as the same album ────────────────────────────────────────────
@pytest.mark.parametrize("title,base", [
    ("Curtain Call: The Hits (Deluxe Edition)", "curtain call the hits"),
    ("The Death of Slim Shady (Coup De Grâce): Expanded Mourner’s Edition",
     "the death of slim shady coup de grace"),
    ("Thriller - 2011 Remaster", "thriller"),
    ("Recovery (Deluxe Edition)", "recovery"),
    ("Deluxe", ""),
])
def test_base_title_drops_every_edition_marker(title, base):
    assert base_album_title(title) == base


def test_editions_of_one_album_are_a_family():
    assert same_album_family("Recovery", "Recovery (Deluxe Edition)")
    assert same_album_family("Curtain Call - The Hits", "Curtain Call: The Hits (Deluxe Edition)")


@pytest.mark.parametrize("a,b", [
    ("The Marshall Mathers LP", "The Marshall Mathers LP2 (Expanded Edition)"),
    ("Curtain Call: The Hits", "Curtain Call 2"),
    ("Relapse", "Relapse: Refill"),
    ("Deluxe", "Deluxe"),
])
def test_different_albums_are_not_a_family(a, b):
    """98%-similar titles can be different records; an exact base is required."""
    assert not same_album_family(a, b)


# ── smaller edition ──────────────────────────────────────────────────────────
def test_the_standard_album_is_a_smaller_edition_of_the_deluxe():
    assert is_smaller_edition("Curtain Call: The Hits (Deluxe Edition)", 24,
                              "Curtain Call: The Hits", 15)


def test_the_deluxe_is_never_a_smaller_edition_of_the_standard():
    assert not is_smaller_edition("Curtain Call: The Hits", 15,
                                  "Curtain Call: The Hits (Deluxe Edition)", 24)


def test_two_spellings_of_the_deluxe_are_the_same_edition():
    """A partly-owned deluxe must not be re-downloaded under another spelling."""
    assert not is_smaller_edition("X (Deluxe)", 24, "X (Deluxe Edition)", 20)


def test_an_owned_album_already_holding_every_track_is_not_smaller():
    """A media server can merge a deluxe into a plain-titled album (27 tracks)."""
    assert not is_smaller_edition("Relapse (Deluxe Edition)", 24, "Relapse", 27)


def test_a_remaster_is_not_a_bigger_edition():
    assert not is_smaller_edition("X (Remastered)", 17, "X", 15)


def test_a_different_album_is_never_a_smaller_edition():
    assert not is_smaller_edition("The Marshall Mathers LP2 (Expanded Edition)", 27,
                                  "The Marshall Mathers LP", 18)


def test_unknown_counts_fall_back_to_the_titles():
    assert is_smaller_edition("X (Deluxe)", None, "X", None)
    assert is_smaller_edition("X (Deluxe)", 0, "X", "")


@pytest.mark.parametrize("args", [
    ("", 10, "X", 5), (None, 10, "X", 5), ("X (Deluxe)", 10, "", 5), ("X (Deluxe)", 10, None, 5),
])
def test_missing_titles_are_never_smaller(args):
    assert not is_smaller_edition(*args)


# ── one edition per album in a release list ──────────────────────────────────
def test_the_smaller_edition_is_dropped_and_order_kept():
    releases = [
        {"name": "Curtain Call: The Hits", "total_tracks": 15},
        {"name": "Encore", "total_tracks": 20},
        {"name": "Curtain Call: The Hits (Deluxe Edition)", "total_tracks": 24},
    ]
    kept = drop_smaller_editions(releases)
    assert [r["name"] for r in kept] == ["Encore", "Curtain Call: The Hits (Deluxe Edition)"]


def test_release_objects_work_too():
    standard = SimpleNamespace(name="Recovery", total_tracks=17)
    deluxe = SimpleNamespace(name="Recovery (Deluxe Edition)", total_tracks=19)
    assert drop_smaller_editions([standard, deluxe]) == [deluxe]


def test_different_albums_and_same_edition_spellings_all_stay():
    releases = [
        {"name": "The Marshall Mathers LP", "total_tracks": 18},
        {"name": "The Marshall Mathers LP2 (Expanded Edition)", "total_tracks": 27},
        {"name": "X (Deluxe)", "total_tracks": 24},
        {"name": "X (Deluxe Edition)", "total_tracks": 24},
    ]
    assert drop_smaller_editions(releases) == releases


def test_a_plain_title_with_more_tracks_than_its_deluxe_stays():
    releases = [{"name": "X", "total_tracks": 15}, {"name": "X (Deluxe)", "total_tracks": 12}]
    assert drop_smaller_editions(releases) == releases


# ── an owned track row ───────────────────────────────────────────────────────
class _DB:
    def __init__(self, albums):
        self.albums = albums          # album_id -> (title, [tracks])
        self.title_lookups = 0
        self.count_lookups = 0

    def get_album_title_year(self, album_id):
        self.title_lookups += 1
        title, _ = self.albums[album_id]
        return (title, 2005)

    def get_tracks_by_album(self, album_id):
        self.count_lookups += 1
        return self.albums[album_id][1]


def test_a_row_on_the_standard_album_is_a_smaller_edition():
    db = _DB({7: ("Curtain Call: The Hits", ["t"] * 15)})
    row = SimpleNamespace(album_id=7)
    assert owned_row_is_smaller_edition(db, "Curtain Call: The Hits (Deluxe Edition)", 24, row)


def test_a_joined_album_title_is_used_without_a_title_lookup():
    db = _DB({7: ("unused", ["t"] * 15)})
    row = {"album_id": 7, "album_title": "Curtain Call: The Hits"}
    assert owned_row_is_smaller_edition(db, "Curtain Call: The Hits (Deluxe Edition)", 24, row)
    assert db.title_lookups == 0


def test_the_count_is_not_asked_for_when_the_titles_rule_it_out():
    """Asking an album's size costs a query per owned hit, so it waits until the
    titles alone say "smaller". Counted rather than raised: the function fails
    open, so a raise inside it would be swallowed and this test could not fail."""
    db = _DB({7: ("Encore", ["t"] * 12)})
    row = SimpleNamespace(album_id=7)
    assert not owned_row_is_smaller_edition(db, "Curtain Call: The Hits (Deluxe Edition)", 24, row)
    assert db.count_lookups == 0


def test_the_count_guard_applies_to_rows():
    db = _DB({7: ("Relapse", ["t"] * 27)})
    assert not owned_row_is_smaller_edition(db, "Relapse (Deluxe Edition)", 24,
                                            SimpleNamespace(album_id=7))


def test_a_memo_answers_repeat_questions_for_one_album():
    db = _DB({7: ("Curtain Call: The Hits", ["t"] * 15)})
    memo = {}
    for _ in range(3):
        assert owned_row_is_smaller_edition(db, "Curtain Call: The Hits (Deluxe Edition)", 24,
                                            SimpleNamespace(album_id=7), memo)
    assert db.title_lookups == 1 and db.count_lookups == 1


def test_rows_fail_open():
    class Broken:
        def get_album_title_year(self, album_id):
            raise RuntimeError("db down")
    assert not owned_row_is_smaller_edition(Broken(), "X (Deluxe)", 10, SimpleNamespace(album_id=1))
    assert not owned_row_is_smaller_edition(_DB({}), "X (Deluxe)", 10, None)
    assert not owned_row_is_smaller_edition(_DB({}), "X", 10, SimpleNamespace(album_id=1))


# ── the setting ──────────────────────────────────────────────────────────────
def test_the_setting_reads_its_key_and_defaults_off():
    assert prefer_deluxe_enabled(SimpleNamespace(get=lambda k, d=None: k == PREFER_DELUXE_KEY))
    assert not prefer_deluxe_enabled(SimpleNamespace(get=lambda k, d=None: d))


def test_a_broken_setting_reads_as_off():
    def _boom(*_a, **_k):
        raise RuntimeError("config unavailable")
    assert not prefer_deluxe_enabled(SimpleNamespace(get=_boom))
