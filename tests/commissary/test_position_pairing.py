"""Files an album's titles can't pair, paired by their place in it and their length.

From a real import of DEATH UNTO DAWN: FINAL FANTASY XIV Original Soundtrack, a
40 + 44 track release whose files were numbered 01-84 straight through and titled
in Japanese. Disc 1 paired on its track numbers; of disc 2 only the files with
English words in their names matched, and the 19 tracks the wishlist then kept
searching for were all among the unmatched files -- each at file number 40 plus
its disc-2 track number, with the matching length.

And a language version is a different recording, whatever else agrees: the same
library held LE SSERAFIM's "FEARLESS (Japanese Version)" in the Korean album.

Titles in these fixtures share no characters with the tracklist on purpose, so
the title scorer can't pair anything by coincidence.
"""

from __future__ import annotations

from core.imports.album_matching import default_quality_rank, match_files_to_tracks

ARTIST = "Masayoshi Soken"
ALBUM = "DEATH UNTO DAWN: FINAL FANTASY XIV Original Soundtrack"
KANJI = "壱弐参肆伍陸漆"


def _track(disc, number, name, seconds, artist=ARTIST):
    return {"id": "t%d-%d" % (disc, number), "name": name, "track_number": number,
            "disc_number": disc, "duration_ms": seconds * 1000, "artists": [{"name": artist}]}


def _tags(title, seconds, *, number=0, disc=1, artist=ARTIST, album=""):
    return {"title": title, "artist": artist, "album": album, "track_number": number,
            "disc_number": disc, "duration_ms": seconds * 1000, "isrc": "", "mbid": ""}


def _match(files, tracks, album=ALBUM):
    return match_files_to_tracks(list(files), files, tracks,
                                 target_album=album, quality_rank=default_quality_rank)


def _by_track(result):
    return {m["track"]["id"]: m for m in result["matches"]}


TRACKS = [
    _track(1, 1, "Locus (Duality)", 201),
    _track(1, 2, "Metal - Brute Justice Mode (Journeys)", 262),
    _track(1, 3, "Rise (Journeys)", 318),
    _track(1, 4, "Watts's Anvil", 147),
    _track(2, 1, "The Isle of Endless Summer", 55),
    _track(2, 2, "Where All Roads Lead", 276),
    _track(2, 3, "Echoes in the Dark", 94),
]

# Numbered straight through, titled in Japanese, tagged with the artist.
FILES = {
    "/dl/DUD/01 ローカス.flac": _tags("ローカス", 201, number=1),
    "/dl/DUD/02 メタル：ブルートジャスティスモード.flac": _tags("メタル：ブルートジャスティスモード", 262, number=2),
    "/dl/DUD/03 ライズ.flac": _tags("ライズ", 318, number=3),
    "/dl/DUD/04 ワッツハンマー・ガレージ.flac": _tags("ワッツハンマー・ガレージ", 147, number=4),
    "/dl/DUD/05 常夏の島.flac": _tags("常夏の島", 55, number=5),
    "/dl/DUD/06 すべての道が至る場所.flac": _tags("すべての道が至る場所", 276, number=6),
    "/dl/DUD/07 暗がりに響く音.flac": _tags("暗がりに響く音", 94, number=7),
}


def test_files_numbered_straight_through_pair_with_disc_two_by_place_and_length():
    result = _match(FILES, TRACKS)

    matched = _by_track(result)
    assert set(matched) == {t["id"] for t in TRACKS}, result["unmatched_files"]
    assert matched["t2-1"]["file"].endswith("05 常夏の島.flac")
    assert matched["t2-2"]["file"].endswith("06 すべての道が至る場所.flac")
    assert matched["t2-3"]["file"].endswith("07 暗がりに響く音.flac")
    assert matched["t2-1"]["match_type"] == "position_duration"
    assert result["unmatched_files"] == []


def test_a_pair_by_place_and_length_is_recorded_below_a_strong_match():
    """Above the matcher's threshold, below the 0.8 at which one auto-import
    match can carry an album on its own."""
    from core.commissary.position_pairing import POSITION_DURATION_CONFIDENCE
    from core.imports.album_matching import MATCH_THRESHOLD
    confidence = _by_track(_match(FILES, TRACKS))["t2-1"]["confidence"]
    assert confidence == POSITION_DURATION_CONFIDENCE
    assert MATCH_THRESHOLD < confidence < 0.8


def test_a_place_whose_length_disagrees_is_left_unmatched():
    files = dict(FILES)
    wrong = "/dl/DUD/06 すべての道が至る場所.flac"
    files[wrong] = dict(files[wrong], duration_ms=301_000)

    result = _match(files, TRACKS)

    assert wrong in result["unmatched_files"]
    assert set(_by_track(result)) == {t["id"] for t in TRACKS} - {"t2-2"}


def test_a_lone_pair_with_nothing_to_confirm_the_numbering_is_not_trusted():
    """One file, one track, the same place and length — a length can coincide."""
    tracks = [_track(1, 1, "Echoes in the Dark", 94)]
    files = {"/dl/x/01 暗がりに響く音.flac": _tags("暗がりに響く音", 94, number=1, artist="")}
    assert _match(files, tracks)["matches"] == []


def test_places_that_mostly_disagree_on_length_pair_nothing():
    """Two of five places agree: the numbering isn't this album's."""
    names = ["Alpha", "Bravo", "Charlie", "Delta", "Echo"]
    tracks = [_track(1, n, names[n - 1], 100 + 30 * n) for n in range(1, 6)]
    lengths = {1: 130, 2: 160, 3: 400, 4: 410, 5: 420}
    files = {"/dl/y/%02d %s.flac" % (n, KANJI[n - 1]): _tags(KANJI[n - 1], lengths[n], number=n, artist="")
             for n in range(1, 6)}
    assert _match(files, tracks)["matches"] == []


def test_files_numbered_per_disc_pair_through_their_disc_tags():
    files = {}
    for i, track in enumerate(TRACKS):
        path = "/dl/z/%d-%02d %s.flac" % (track["disc_number"], track["track_number"], KANJI[i])
        files[path] = _tags(KANJI[i], track["duration_ms"] // 1000, number=track["track_number"],
                            disc=track["disc_number"], artist="")
    assert set(_by_track(_match(files, TRACKS))) == {t["id"] for t in TRACKS}


def test_untagged_files_use_the_number_their_filename_starts_with():
    """"05 常夏の島" — a number and a space, which the strict filename reader skips."""
    files = {path: dict(tags, track_number=0, artist="") for path, tags in FILES.items()}
    matched = _by_track(_match(files, TRACKS))
    assert set(matched) == {t["id"] for t in TRACKS}
    assert matched["t2-1"]["file"].endswith("05 常夏の島.flac")


def test_a_disc_folder_stands_in_for_a_missing_disc_tag():
    tracks = [_track(1, 1, "Alpha", 200), _track(1, 2, "Bravo", 210),
              _track(2, 1, "Charlie", 220), _track(2, 2, "Delta", 230)]
    files = {
        "/dl/w/CD1/01 壱.flac": _tags("壱", 200, number=1, artist=""),
        "/dl/w/CD1/02 弐.flac": _tags("弐", 210, number=2, artist=""),
        "/dl/w/CD2/01 参.flac": _tags("参", 220, number=1, artist=""),
        "/dl/w/CD2/02 肆.flac": _tags("肆", 230, number=2, artist=""),
    }
    matched = _by_track(_match(files, tracks))
    assert matched["t2-1"]["file"].endswith("CD2/01 参.flac")
    assert set(matched) == {t["id"] for t in tracks}


# ── language versions ───────────────────────────────────────────────────────
def test_a_japanese_version_is_not_filed_into_the_korean_album():
    """The library's own case: position, artist and album tag used to carry it."""
    tracks = [_track(1, 1, "The World Is My Oyster", 86, "LE SSERAFIM"),
              _track(1, 2, "FEARLESS", 168, "LE SSERAFIM"),
              _track(1, 3, "Blue Flame", 203, "LE SSERAFIM"),
              _track(1, 4, "The Great Mermaid", 193, "LE SSERAFIM"),
              _track(1, 5, "Sour Grapes", 176, "LE SSERAFIM")]
    files = {
        "/dl/FEARLESS JP/01 - FEARLESS (Japanese Version).flac":
            _tags("FEARLESS (Japanese Version)", 168, number=1, artist="LE SSERAFIM", album="FEARLESS"),
        "/dl/FEARLESS JP/02 - Blue Flame (Japanese Version).flac":
            _tags("Blue Flame (Japanese Version)", 202, number=2, artist="LE SSERAFIM", album="FEARLESS"),
    }
    assert _match(files, tracks, album="FEARLESS")["matches"] == []


def test_place_and_length_never_pair_different_language_versions():
    tracks = [_track(1, 1, "UNDEAD", 183, "YOASOBI"), _track(1, 2, "Biri-Biri", 187, "YOASOBI")]
    files = {
        "/dl/v/01 UNDEAD (English Version).flac": _tags("UNDEAD (English Version)", 183, number=1, artist=""),
        "/dl/v/02 Biri-Biri (English Version).flac": _tags("Biri-Biri (English Version)", 187, number=2, artist=""),
    }
    assert _match(files, tracks, album="YOASOBI Singles")["matches"] == []


def test_an_english_single_whose_track_name_lacks_the_marker_still_matches():
    """The single names the language; its one track doesn't."""
    tracks = [_track(1, 1, "UNDEAD", 183, "YOASOBI")]
    files = {"/dl/u/01 UNDEAD (English Version).flac":
             _tags("UNDEAD (English Version)", 183, number=1, artist="YOASOBI",
                   album="UNDEAD (English Version)")}
    assert len(_match(files, tracks, album="UNDEAD (English Version)")["matches"]) == 1


# ── staging: a batch's missing track claims its staged file by place ─────────
# An album bundle (torrent / usenet) stages the whole release, and each missing
# track of the batch then claims its file by title. A release titled in Japanese
# claims nothing against an English tracklist. The batch holds only the missing
# tracks, so a straight-through offset is read off the staged files and accepted
# only when other tracks of the batch agree on it by length.
import pytest  # noqa: E402

from core.downloads import staging as ds  # noqa: E402
from core.runtime_state import download_tasks, matched_downloads_context  # noqa: E402
from tests.downloads.test_downloads_staging import _Track, _build_deps, _seed_task  # noqa: E402

DISC_TWO = [(1, "The Isle of Endless Summer", 55), (2, "Where All Roads Lead", 276),
            (3, "Echoes in the Dark", 94)]


@pytest.fixture()
def clean_state():
    download_tasks.clear()
    matched_downloads_context.clear()
    yield
    download_tasks.clear()
    matched_downloads_context.clear()


def _stage_files(tmp_path, entries):
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


def test_a_track_claims_the_file_at_its_place_in_straight_through_numbering(tmp_path, clean_state):
    files = _stage_files(tmp_path, STRAIGHT_THROUGH)
    claimed, context = _claim(tmp_path, files, _seed_disc_two(), 1)

    assert claimed is True
    assert context["original_search_result"]["filename"].endswith("05 常夏の島.flac")
    # Its place comes from the track, not the file's straight-through number.
    assert (context["track_info"]["disc_number"], context["track_info"]["track_number"]) == (2, 1)


def test_with_no_other_track_to_confirm_the_numbering_nothing_is_claimed(tmp_path, clean_state):
    files = _stage_files(tmp_path, STRAIGHT_THROUGH)
    claimed, _ = _claim(tmp_path, files, _seed_disc_two(numbers=(1,)), 1)
    assert claimed is False


def test_a_file_whose_length_disagrees_is_not_claimed(tmp_path, clean_state):
    entries = list(STRAIGHT_THROUGH)
    entries[4] = ("05 常夏の島.flac", "常夏の島", 5, 1, 80)
    files = _stage_files(tmp_path, entries)
    queue = _seed_disc_two()

    assert _claim(tmp_path, files, queue, 1)[0] is False
    claimed, context = _claim(tmp_path, files, queue, 3)
    assert claimed is True
    assert context["original_search_result"]["filename"].endswith("07 暗がりに響く音.flac")


def test_files_numbered_per_disc_are_claimed_through_their_disc_tags(tmp_path, clean_state):
    entries = list(STRAIGHT_THROUGH[:4]) + [
        ("2-01 常夏の島.flac", "常夏の島", 1, 2, 55),
        ("2-02 すべての道が至る場所.flac", "すべての道が至る場所", 2, 2, 276),
        ("2-03 暗がりに響く音.flac", "暗がりに響く音", 3, 2, 94),
    ]
    files = _stage_files(tmp_path, entries)
    claimed, context = _claim(tmp_path, files, _seed_disc_two(), 2)

    assert claimed is True
    assert context["original_search_result"]["filename"].endswith("2-02 すべての道が至る場所.flac")


def test_place_and_length_never_claim_a_different_language_version(tmp_path, clean_state):
    entries = list(STRAIGHT_THROUGH[:4]) + [
        ("05 Summer (English Version).flac", "Summer (English Version)", 5, 1, 55),
        ("06 Roads (English Version).flac", "Roads (English Version)", 6, 1, 276),
        ("07 Echoes (English Version).flac", "Echoes (English Version)", 7, 1, 94),
    ]
    files = _stage_files(tmp_path, entries)
    assert _claim(tmp_path, files, _seed_disc_two(), 1)[0] is False


def test_a_staged_english_version_never_claims_the_original_by_title(tmp_path, clean_state):
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


def test_the_staging_scan_carries_each_files_length():
    from pathlib import Path
    src = (Path(__file__).resolve().parents[2] / "web_server.py").read_text(encoding="utf-8")
    assert "'duration_ms': meta.get('duration_ms') or 0,  # Commissary" in src
