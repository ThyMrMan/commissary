"""Staged album files a track can't claim by name, claimed by place and length.

An album bundle (torrent / usenet) stages the whole release, and each missing
track of the batch then claims its file by title. A release titled in Japanese
claims nothing against an English tracklist -- and a two-disc release numbered
straight through puts disc 2, track 1 at file 41. The batch holds only the
missing tracks, so that offset can't be counted from the album: it is read off
the staged files and accepted only when other tracks of the batch agree on it
by length.
"""

from __future__ import annotations

import pytest

from core.downloads import staging as ds
from core.runtime_state import download_tasks, matched_downloads_context
from tests.downloads.test_downloads_staging import _Track, _build_deps, _seed_task

ALBUM = "DEATH UNTO DAWN: FINAL FANTASY XIV Original Soundtrack"
ARTIST = "Masayoshi Soken"
DISC_TWO = [(1, "The Isle of Endless Summer", 55), (2, "Where All Roads Lead", 276),
            (3, "Echoes in the Dark", 94)]


@pytest.fixture(autouse=True)
def clean_state():
    download_tasks.clear()
    matched_downloads_context.clear()
    yield
    download_tasks.clear()
    matched_downloads_context.clear()


def _stage(tmp_path, entries):
    """entries: (filename, title, number, disc, seconds)."""
    folder = tmp_path / "staging"
    folder.mkdir(exist_ok=True)
    files = []
    for filename, title, number, disc, seconds in entries:
        path = folder / filename
        path.touch()
        files.append({"full_path": str(path), "title": title, "artist": "", "album": "",
                      "track_number": number, "disc_number": disc,
                      "duration_ms": seconds * 1000})
    return files


STRAIGHT_THROUGH = [
    ("01 ローカス.flac", "ローカス", 1, 1, 201),
    ("02 メタル.flac", "メタル", 2, 1, 262),
    ("03 ライズ.flac", "ライズ", 3, 1, 318),
    ("04 ワッツハンマー・ガレージ.flac", "ワッツハンマー・ガレージ", 4, 1, 147),
    ("05 常夏の島.flac", "常夏の島", 5, 1, 55),
    ("06 すべての道が至る場所.flac", "すべての道が至る場所", 6, 1, 276),
    ("07 暗がりに響く音.flac", "暗がりに響く音", 7, 1, 94),
]


def _seed_disc_two(numbers=(1, 2, 3)):
    queue = []
    for number, name, seconds in DISC_TWO:
        if number not in numbers:
            continue
        task_id = "task-2-%d" % number
        _seed_task(task_id, track_info={
            "name": name, "track_number": number, "disc_number": 2,
            "duration_ms": seconds * 1000,
            "_is_explicit_album_download": True,
            "_explicit_album_context": {"id": "dud", "name": ALBUM, "total_tracks": 7},
            "_explicit_artist_context": {"id": "soken", "name": ARTIST},
        })
        queue.append(task_id)
    return queue


def _batch_fields(queue):
    fields = {"queue": queue, "album_bundle_private_staging": True, "album_bundle_source": "torrent"}
    return lambda _batch_id, field: fields.get(field)


def _claim(tmp_path, files, queue, number):
    name = dict((n, t) for n, t, _s in DISC_TWO)[number]
    deps = _build_deps(transfer_path=str(tmp_path / "transfer"), staging_files=files,
                       get_batch_field=_batch_fields(queue))
    claimed = ds.try_staging_match("task-2-%d" % number, "b1",
                                   _Track(name=name, artists=[ARTIST], album=ALBUM), deps)
    return claimed, matched_downloads_context.get("staging_task-2-%d" % number)


def test_a_track_claims_the_file_at_its_place_in_straight_through_numbering(tmp_path):
    files = _stage(tmp_path, STRAIGHT_THROUGH)
    claimed, context = _claim(tmp_path, files, _seed_disc_two(), 1)

    assert claimed is True
    assert context["original_search_result"]["filename"].endswith("05 常夏の島.flac")
    # Its place comes from the track, not the file's straight-through number.
    assert (context["track_info"]["disc_number"], context["track_info"]["track_number"]) == (2, 1)


def test_with_no_other_track_to_confirm_the_numbering_nothing_is_claimed(tmp_path):
    files = _stage(tmp_path, STRAIGHT_THROUGH)
    claimed, _ = _claim(tmp_path, files, _seed_disc_two(numbers=(1,)), 1)
    assert claimed is False


def test_a_file_whose_length_disagrees_is_not_claimed(tmp_path):
    entries = list(STRAIGHT_THROUGH)
    entries[4] = ("05 常夏の島.flac", "常夏の島", 5, 1, 80)
    files = _stage(tmp_path, entries)
    queue = _seed_disc_two()

    assert _claim(tmp_path, files, queue, 1)[0] is False
    claimed, context = _claim(tmp_path, files, queue, 3)
    assert claimed is True
    assert context["original_search_result"]["filename"].endswith("07 暗がりに響く音.flac")


def test_files_numbered_per_disc_are_claimed_through_their_disc_tags(tmp_path):
    entries = list(STRAIGHT_THROUGH[:4]) + [
        ("2-01 常夏の島.flac", "常夏の島", 1, 2, 55),
        ("2-02 すべての道が至る場所.flac", "すべての道が至る場所", 2, 2, 276),
        ("2-03 暗がりに響く音.flac", "暗がりに響く音", 3, 2, 94),
    ]
    files = _stage(tmp_path, entries)
    claimed, context = _claim(tmp_path, files, _seed_disc_two(), 2)

    assert claimed is True
    assert context["original_search_result"]["filename"].endswith("2-02 すべての道が至る場所.flac")


def test_place_and_length_never_claim_a_different_language_version(tmp_path):
    entries = list(STRAIGHT_THROUGH[:4]) + [
        ("05 Summer (English Version).flac", "Summer (English Version)", 5, 1, 55),
        ("06 Roads (English Version).flac", "Roads (English Version)", 6, 1, 276),
        ("07 Echoes (English Version).flac", "Echoes (English Version)", 7, 1, 94),
    ]
    files = _stage(tmp_path, entries)
    assert _claim(tmp_path, files, _seed_disc_two(), 1)[0] is False


def test_a_staged_english_version_never_claims_the_original_by_title(tmp_path):
    """A long title makes the two read 83% alike — enough to claim before."""
    title = "Shine in the Cruel Night of the Endless Summer"
    source = tmp_path / "staging" / "01 Shine (English Version).flac"
    source.parent.mkdir()
    source.touch()
    deps = _build_deps(transfer_path=str(tmp_path / "transfer"), staging_files=[{
        "full_path": str(source), "title": title + " (English Version)", "artist": "LiSA",
        "album": "", "track_number": 1, "disc_number": 1, "duration_ms": 0,
    }])
    _seed_task("t-lang", track_info={"name": title})

    assert ds.try_staging_match("t-lang", "b1", _Track(name=title, artists=["LiSA"]), deps) is False
