"""A language version of a song is a different recording.

"UNDEAD" and "UNDEAD (English Version)" share a backing track and a length, and
a title comparison that drops bracketed text sees one title twice. A library
held LE SSERAFIM's "FEARLESS (Japanese Version)" inside the Korean album
"FEARLESS", and a request for either version would take the other.
"""

from __future__ import annotations

from difflib import SequenceMatcher
from types import SimpleNamespace

import pytest

import core.matching_engine as me
from core.commissary.language_versions import language_version
from core.matching.audio_verification import Decision, evaluate


@pytest.fixture
def engine():
    return me.MusicMatchingEngine()


def _title_sim(engine, a, b):
    return engine.similarity_score(engine.clean_title(a), engine.clean_title(b))


# ── recognising the marker ──────────────────────────────────────────────────
@pytest.mark.parametrize("title, language", [
    ("UNDEAD (English Version)", "en"),
    ("crossing field - English Version", "en"),
    ("ReawakeR -English TV version- (feat. Felix of Stray Kids)", "en"),
    ("Ashes of Dreams／English Version", "en"),
    ("Weight of the World／English Version(Ver1.1a)", "en"),
    ("Kick Back (English)", "en"),
    ("FEARLESS (Japanese Version)", "ja"),
    ("Blue Flame -Japanese ver.-", "ja"),
    ("Dynamite (JP ver.)", "ja"),
    ("Butter (Korean Ver.)", "ko"),
    ("Mojito (Chinese Version)", "zh"),
    ("アイドル (英語版)", "en"),
    ("Lemon 日本語ver.", "ja"),
])
def test_a_language_version_names_its_language(title, language):
    assert language_version(title) == language


@pytest.mark.parametrize("title", [
    "UNDEAD", "English Rose", "The Japanese House", "Korean Air", "Version 2",
    "Live Version", "Radio Version", "Chinese Democracy", "An Englishman in New York",
    "Revenge (Extended Version)", "Revenge Version", "", None, 42,
])
def test_an_ordinary_title_names_none(title):
    assert language_version(title) is None


# ── upstream's recording markers (MusicBrainz, Deezer, release import, wishlist) ──
@pytest.mark.parametrize("marked", ["Blue Flame -Japanese ver.-", "Blue Flame (JP ver.)",
                                    "Blue Flame 日本語ver."])
def test_upstreams_markers_see_the_spellings_its_tokens_miss(marked):
    from core.text.title_match import recording_version_markers
    assert recording_version_markers(marked) == recording_version_markers("Blue Flame (Japanese Version)")
    assert recording_version_markers(marked) != recording_version_markers("Blue Flame")


def test_upstreams_own_language_markers_are_unchanged():
    from core.text.title_match import recording_version_markers
    assert recording_version_markers("Kick Back (English)") == frozenset({"english"})
    assert recording_version_markers("Song (Live) (German Version)") >= {"live", "german"}
    assert recording_version_markers("Live Forever") == frozenset()


# ── title similarity ────────────────────────────────────────────────────────
def test_the_original_and_its_language_version_score_as_different_songs(engine):
    assert _title_sim(engine, "UNDEAD", "UNDEAD (English Version)") <= 0.30
    assert _title_sim(engine, "FEARLESS (Japanese Version)", "FEARLESS") <= 0.30


def test_two_different_language_versions_score_as_different_songs(engine):
    assert _title_sim(engine, "UNDEAD (English Version)", "UNDEAD (Japanese Version)") <= 0.30


def test_one_language_spelled_two_ways_is_still_the_same_version(engine):
    assert _title_sim(engine, "UNDEAD (English Version)", "UNDEAD -English ver.-") >= 0.80


def test_the_rule_never_raises_a_score(engine):
    """Capped, not replaced: an unrelated pair keeps its own lower ratio."""
    a = engine.clean_title("Qwxz")
    b = engine.clean_title("UNDEAD (English Version)")
    ratio = SequenceMatcher(None, a, b).ratio()
    assert ratio < 0.30
    assert engine.similarity_score(a, b) == ratio


def test_a_download_for_the_original_rejects_its_english_version(engine):
    """Below the 0.60 download validation accepts at."""
    confidence, _ = engine.score_track_match(
        "UNDEAD", ["YOASOBI"], 183000, "UNDEAD (English Version)", ["YOASOBI"], 183000)
    assert confidence < 0.60


def test_a_download_for_the_english_version_still_takes_it(engine):
    confidence, _ = engine.score_track_match(
        "UNDEAD (English Version)", ["YOASOBI"], 183000,
        "UNDEAD (English Version)", ["YOASOBI"], 183000)
    assert confidence >= 0.90


# ── the version detector and the Soulseek scorer ───────────────────────────
def test_the_version_detector_labels_a_language_version(engine):
    assert engine.detect_version_type("UNDEAD (English Version)") == ("english version", 0.0)
    assert engine.detect_version_type("Blue Flame -Japanese ver.-")[0] == "japanese version"
    assert engine.detect_version_type("UNDEAD")[0] == "original"
    assert engine.detect_version_type("UNDEAD (English Version) (Live)")[0] == "live", \
        "a performance marker still wins"


def _soulseek(engine, wanted, filename, base=0.90, album=""):
    engine.calculate_slskd_match_confidence = lambda *_a, **_k: base
    return engine.calculate_slskd_match_confidence_enhanced(
        SimpleNamespace(name=wanted, album=album, artists=["YOASOBI"]),
        SimpleNamespace(filename=filename))


def test_soulseek_rejects_the_english_version_for_the_original(engine):
    """The marker is on the folder here, not the file - the path counts."""
    assert _soulseek(engine, "UNDEAD",
                     "@@user\\Music\\YOASOBI - UNDEAD (English Version)\\01 UNDEAD.flac") \
        == (0.0, "rejected_version_mismatch")


def test_soulseek_rejects_the_original_for_the_english_version(engine):
    assert _soulseek(engine, "UNDEAD (English Version)",
                     "@@user\\Music\\YOASOBI - UNDEAD\\01 UNDEAD.flac") \
        == (0.0, "rejected_version_mismatch")


def test_soulseek_takes_the_english_version_when_asked_for_it(engine):
    confidence, version = _soulseek(engine, "UNDEAD (English Version)",
                                    "@@user\\Music\\YOASOBI\\01 UNDEAD (English Version).flac")
    assert (confidence, version) == (0.90, "english version")


def test_soulseek_reads_the_language_off_the_requested_single(engine):
    """"UNDEAD" on the single "UNDEAD (English Version)" wants the English one."""
    single = "UNDEAD (English Version)"
    english = "@@user\\Music\\YOASOBI - UNDEAD (English Version)\\01 UNDEAD.flac"
    original = "@@user\\Music\\YOASOBI - UNDEAD\\01 UNDEAD.flac"
    assert _soulseek(engine, "UNDEAD", english, album=single)[0] == 0.90
    assert _soulseek(engine, "UNDEAD", original, album=single) == (0.0, "rejected_version_mismatch")


# ── AcoustID verification ───────────────────────────────────────────────────
def _rec(title, artist="YOASOBI"):
    return {"title": title, "artist": artist}


def test_verification_fails_the_english_version_downloaded_for_the_original():
    out = evaluate("UNDEAD", "YOASOBI", [_rec("UNDEAD (English Version)")], fingerprint_score=0.97)
    assert out.decision == Decision.FAIL
    assert "english version" in out.reason


def test_verification_fails_the_original_downloaded_for_the_english_version():
    out = evaluate("UNDEAD (English Version)", "YOASOBI", [_rec("UNDEAD")], fingerprint_score=0.97)
    assert out.decision == Decision.FAIL


@pytest.mark.parametrize("listed_first", ["UNDEAD (English Version)", "UNDEAD"])
@pytest.mark.parametrize("expected", ["UNDEAD (English Version)", "UNDEAD"])
def test_one_fingerprint_listing_both_versions_passes_either_request(listed_first, expected):
    """The two versions share a backing track, so one fingerprint often lists
    both recordings - in whichever order AcoustID returns them."""
    listed_second = "UNDEAD" if listed_first != "UNDEAD" else "UNDEAD (English Version)"
    out = evaluate(expected, "YOASOBI", [_rec(listed_first), _rec(listed_second)],
                   fingerprint_score=0.97)
    assert out.decision == Decision.PASS, out.reason


# ── staging ─────────────────────────────────────────────────────────────────
class _Engine:
    normalize_string = staticmethod(lambda s: (s or "").lower().strip())
    detect_version_type = staticmethod(lambda t: me.MusicMatchingEngine().detect_version_type(t))


def _staged(tmp_path, monkeypatch, staged_file, *, name="UNDEAD", album="UNDEAD"):
    from core.downloads import staging as ds
    from core.runtime_state import download_tasks
    claimed = []
    monkeypatch.setitem(download_tasks, "t1", {"status": "searching", "track_info": {},
                                               "used_sources": set(), "download_id": None})
    deps = ds.StagingDeps(
        config_manager=SimpleNamespace(get=lambda k, d=None: str(tmp_path / "out")
                                       if k == "soulseek.transfer_path" else d),
        matching_engine=_Engine(),
        get_staging_file_cache=lambda _b: [staged_file],
        docker_resolve_path=lambda p: p,
        post_process_matched_download_with_verification=lambda *a, **k: claimed.append(a),
        get_batch_field=None)
    track = SimpleNamespace(name=name, artists=["YOASOBI"], album=album)
    return ds.try_staging_match("t1", "b1", track, deps)


def _file(tmp_path, name):
    path = tmp_path / "staging" / name
    path.parent.mkdir(exist_ok=True)
    path.write_bytes(b"audio")
    return str(path)


def test_staging_never_claims_a_file_from_the_english_single(tmp_path, monkeypatch):
    """Its title tag is bare - the album tag names the language."""
    f = _file(tmp_path, "01 UNDEAD.flac")
    assert _staged(tmp_path, monkeypatch, {"full_path": f, "title": "UNDEAD", "artist": "YOASOBI",
                                           "album": "UNDEAD (English Version)"}) is False


def test_staging_still_claims_the_same_language(tmp_path, monkeypatch):
    f = _file(tmp_path, "01 UNDEAD.flac")
    assert _staged(tmp_path, monkeypatch, {"full_path": f, "title": "UNDEAD", "artist": "YOASOBI",
                                           "album": "UNDEAD"}) is True


# ── album import ────────────────────────────────────────────────────────────
def test_album_import_never_files_the_japanese_version_into_the_korean_album():
    from core.imports.album_matching import default_quality_rank, match_files_to_tracks
    files = ["/in/01 FEARLESS.flac", "/in/02 Blue Flame.flac"]
    tags = {
        files[0]: {"title": "FEARLESS (Japanese Version)", "artist": "LE SSERAFIM",
                   "album": "FEARLESS (Japanese Version)", "track_number": 1, "duration_ms": 168000},
        files[1]: {"title": "Blue Flame", "artist": "LE SSERAFIM", "album": "FEARLESS",
                   "track_number": 2, "duration_ms": 191000},
    }
    tracks = [
        {"name": "FEARLESS", "artists": [{"name": "LE SSERAFIM"}], "track_number": 1,
         "duration_ms": 168000},
        {"name": "Blue Flame", "artists": [{"name": "LE SSERAFIM"}], "track_number": 2,
         "duration_ms": 191000},
    ]
    result = match_files_to_tracks(files, tags, tracks, target_album="FEARLESS",
                                   quality_rank=default_quality_rank)
    assert [(m["track"]["name"], m["file"]) for m in result["matches"]] == \
        [("Blue Flame", files[1])]


def test_album_import_fills_a_japanese_album_with_its_own_files():
    from core.imports.album_matching import default_quality_rank, match_files_to_tracks
    f = "/in/01 FEARLESS -Japanese ver.-.flac"
    tags = {f: {"title": "FEARLESS", "artist": "LE SSERAFIM", "album": "FEARLESS -Japanese ver.-",
                "track_number": 1, "duration_ms": 168000}}
    tracks = [{"name": "FEARLESS (Japanese Version)", "artists": [{"name": "LE SSERAFIM"}],
               "track_number": 1, "duration_ms": 168000}]
    result = match_files_to_tracks([f], tags, tracks, target_album="FEARLESS (Japanese Version)",
                                   quality_rank=default_quality_rank)
    assert [m["file"] for m in result["matches"]] == [f]
