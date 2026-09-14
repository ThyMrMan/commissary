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
    from core.imports.album_matching import MATCH_THRESHOLD, POSITION_DURATION_CONFIDENCE
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
