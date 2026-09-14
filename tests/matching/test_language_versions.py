"""A language version of a song is a different recording.

"UNDEAD" and "UNDEAD (English Version)" share a backing track and a length, and
a title comparison that drops bracketed text sees one title twice. No matcher
knew the difference: a library held LE SSERAFIM's "FEARLESS (Japanese Version)"
inside the Korean album "FEARLESS", and a request for either version would take
the other.
"""

from __future__ import annotations

from difflib import SequenceMatcher
from types import SimpleNamespace

import pytest

import core.matching_engine as me
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
    from core.text.language_version import language_version
    assert language_version(title) == language


@pytest.mark.parametrize("title", [
    "UNDEAD", "English Rose", "The Japanese House", "Korean Air", "Version 2",
    "Live Version", "Radio Version", "Chinese Democracy", "An Englishman in New York",
    "Revenge (Extended Version)", "Revenge Version", "", None,
])
def test_an_ordinary_title_names_none(title):
    from core.text.language_version import language_version
    assert language_version(title) is None


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


def _soulseek(engine, wanted, filename, base=0.90, album=""):
    engine.calculate_slskd_match_confidence = lambda *_a, **_k: base
    return engine.calculate_slskd_match_confidence_enhanced(
        SimpleNamespace(name=wanted, album=album), SimpleNamespace(filename=filename))


def test_soulseek_rejects_the_english_version_for_the_original(engine):
    """The marker is on the folder here, not the file — the path counts."""
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
    both recordings — in whichever order AcoustID returns them."""
    listed_second = "UNDEAD" if listed_first != "UNDEAD" else "UNDEAD (English Version)"
    out = evaluate(expected, "YOASOBI", [_rec(listed_first), _rec(listed_second)],
                   fingerprint_score=0.97)
    assert out.decision == Decision.PASS, out.reason


def test_the_same_fingerprint_preference_is_for_language_versions_only():
    """An instrumental listed first keeps failing a vocal request, as it always did."""
    out = evaluate("In My Feelings", "Drake",
                   [_rec("In My Feelings (Instrumental)", "Drake"), _rec("In My Feelings", "Drake")],
                   fingerprint_score=0.97)
    assert out.decision == Decision.FAIL


# ── library ownership ───────────────────────────────────────────────────────
@pytest.mark.parametrize("qualifier", ["(english ver)", "(japanese ver.)", "(english)", "(korean)"])
def test_ownership_never_strips_a_language_qualifier_as_a_subtitle(qualifier):
    from core.text.title_match import strip_subtitle_qualifiers
    title = "undead " + qualifier
    assert strip_subtitle_qualifiers(title, "undead") == title
