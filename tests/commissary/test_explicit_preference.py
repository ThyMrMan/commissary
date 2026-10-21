"""'Prefer explicit versions' for the structured sources (old fork, #923 extended).

Upstream's preference only reads Soulseek filenames. Deezer, Tidal, Qobuz and
HiFi report an explicit flag per track - the authoritative answer - and it was
dropped before the candidate was built. The flag only means something relative
to what you asked for: it is compared only when the wanted track is explicit.
"""

from __future__ import annotations

import pathlib

import pytest

from core.commissary import explicit_preference as ep
from core.download_plugins.types import TrackResult
from core.spotify_client import Track

_ROOT = pathlib.Path(__file__).resolve().parents[2]


def _wanted(explicit=None, name="Song Title"):
    return Track(id="i", name=name, artists=["A"], album="Al",
                 duration_ms=200000, popularity=0, explicit=explicit)


def _candidate(explicit=None, **kw):
    base = dict(username="deezer_dl", filename="1||A - Song Title", size=0,
                bitrate=None, duration=200000, quality="flac", free_upload_slots=0,
                upload_speed=0, queue_length=0, artist="A", title="Song Title",
                _source_metadata={"source": "deezer", "explicit": explicit})
    base.update(kw)
    return TrackResult(**base)


# ── the verdict ─────────────────────────────────────────────────────────────
class TestTheFlagOnlyMeansSomethingRelatively:
    def test_a_song_with_no_explicit_cut_is_never_penalised(self):
        assert ep.verdict(_wanted(False), _candidate(False)) == ep.UNKNOWN

    def test_nor_when_nobody_said_what_was_wanted(self):
        assert ep.verdict(_wanted(None), _candidate(False)) == ep.UNKNOWN

    def test_the_censored_cut_of_an_explicit_track_is_the_case_that_counts(self):
        assert ep.verdict(_wanted(True), _candidate(False)) == ep.CENSORED

    def test_and_the_explicit_cut_is_preferred(self):
        assert ep.verdict(_wanted(True), _candidate(True)) == ep.MATCH

    def test_a_source_that_did_not_say_is_not_guessed_at(self):
        assert ep.verdict(_wanted(True), _candidate(None)) == ep.UNKNOWN


class TestSourcesWithNoFlagFallBackToTheName:
    @pytest.mark.parametrize("title,expect", [
        ("Song Title (Clean)", ep.CENSORED),
        ("Song Title [Clean]", ep.CENSORED),
        ("Song Title - Clean", ep.CENSORED),
        ("Song Title (Censored)", ep.CENSORED),
        ("Song Title (Edited Version)", ep.CENSORED),
        ("Song Title (Explicit)", ep.MATCH),
        ("Song Title", ep.UNKNOWN),
    ])
    def test_markers(self, title, expect):
        assert ep.verdict(_wanted(True), _candidate(None, title=title)) == expect

    def test_a_title_containing_the_word_clean_is_not_a_censored_cut(self):
        for title in ("Mr. Clean", "Clean Bandit - Rockabye", "Come Clean"):
            assert ep.verdict(_wanted(True), _candidate(None, title=title)) == ep.UNKNOWN


class TestItRanksAndNeverFilters:
    def test_the_ladder(self):
        assert ep.adjust(0.80, ep.MATCH, enabled=True) > 0.80
        assert ep.adjust(0.80, ep.UNKNOWN, enabled=True) == 0.80
        assert ep.adjust(0.80, ep.CENSORED, enabled=True) < 0.80

    def test_the_setting_off_changes_nothing(self):
        for v in (ep.MATCH, ep.CENSORED, ep.UNKNOWN):
            assert ep.adjust(0.80, v, enabled=False) == 0.80

    def test_it_stays_inside_zero_and_one(self):
        assert ep.adjust(0.99, ep.MATCH, enabled=True) <= 1.0
        assert ep.adjust(0.02, ep.CENSORED, enabled=True) >= 0.0

    def test_junk_confidence_does_not_raise(self):
        assert ep.adjust(None, ep.MATCH, enabled=True) == 0.0
        assert ep.adjust("nonsense", ep.CENSORED, enabled=True) == 0.0


class TestItSaysWhenEverythingIsCensored:
    def test_the_case_worth_reporting(self):
        note = ep.summarize([ep.CENSORED, ep.CENSORED])
        assert "clean cut" in note and "2 of 2" in note

    def test_a_mixed_field_just_says_it_is_ranking(self):
        assert "ranking explicit first" in ep.summarize([ep.MATCH, ep.CENSORED])

    def test_nothing_to_report_stays_silent(self):
        assert ep.summarize([ep.UNKNOWN, ep.MATCH]) == ""
        assert ep.summarize([]) == ""


# ── validation ranks by it ──────────────────────────────────────────────────
@pytest.fixture
def settings(monkeypatch):
    from core.downloads import validation
    from core.matching_engine import MusicMatchingEngine
    from core.settings import config_manager
    monkeypatch.setattr(validation, "matching_engine", MusicMatchingEngine())
    values = {"content_filter.prefer_explicit": True, "content_filter.allow_explicit": True}
    real_get = config_manager.get
    monkeypatch.setattr(config_manager, "get",
                        lambda key, default=None: values[key] if key in values else real_get(key, default))
    return values


def _ranked(wanted, *candidates):
    from core.downloads.validation import _score_streaming_candidates
    return _score_streaming_candidates(list(candidates), wanted)


def test_validation_puts_the_explicit_cut_first(settings):
    clean, explicit = _candidate(False, filename="1||clean"), _candidate(True, filename="2||explicit")
    ranked = _ranked(_wanted(True), clean, explicit)
    assert [r.filename for r in ranked] == ["2||explicit", "1||clean"]
    assert ranked[0].confidence == ranked[1].confidence, "ranking only - the score is untouched"


def test_a_lone_censored_cut_still_downloads(settings):
    assert [r.filename for r in _ranked(_wanted(True), _candidate(False, filename="1||clean"))] == \
        ["1||clean"]


def test_the_setting_off_or_explicit_blocked_keeps_upstreams_order(settings):
    for key, value in (("content_filter.prefer_explicit", False),
                       ("content_filter.allow_explicit", False)):
        settings.update({"content_filter.prefer_explicit": True, "content_filter.allow_explicit": True})
        settings[key] = value
        ranked = _ranked(_wanted(True), _candidate(False, filename="1||clean"),
                         _candidate(True, filename="2||explicit"))
        assert [r.filename for r in ranked] == ["1||clean", "2||explicit"], key


# ── the flags get there ─────────────────────────────────────────────────────
def test_the_worker_hands_the_tracks_flag_to_validation():
    src = (_ROOT / "core" / "downloads" / "task_worker.py").read_text(encoding="utf-8")
    assert "track.explicit = wanted_flag(track_data)" in src


@pytest.mark.parametrize("data, flag", [
    ({"explicit": True}, True), ({"explicit_lyrics": False}, False),
    ({"trackExplicitness": "explicit"}, True), ({"trackExplicitness": "notExplicit"}, False),
    ({}, None), (None, None),
])
def test_wanted_flag_reads_every_spelling(data, flag):
    assert ep.wanted_flag(data) is flag


def test_the_wishlist_keeps_the_flag():
    from core.wishlist.payloads import ensure_wishlist_track_format
    row = ensure_wishlist_track_format({"id": "x", "name": "S", "artists": ["A"], "explicit": True})
    assert row["explicit"] is True
    assert "explicit" not in ensure_wishlist_track_format({"id": "x", "name": "S", "artists": ["A"]})


def test_deezer_candidates_carry_the_flag():
    from core.deezer_download_client import DeezerDownloadClient
    client = object.__new__(DeezerDownloadClient)
    client.__dict__.update({"_quality": "flac", "quality": "flac"})
    tr = client._item_to_track_result({"id": 7, "title": "S", "duration": 200, "explicit_lyrics": True,
                                       "artist": {"id": 1, "name": "A"}, "album": {"id": 2, "title": "Al"}})
    assert tr._source_metadata["explicit"] is True


def test_hifi_candidates_carry_the_flag():
    from core.hifi_client import HiFiClient
    tr = HiFiClient._to_track_result(object.__new__(HiFiClient),
                                     {"id": 7, "artist": "A", "title": "S", "explicit": False}, {})
    assert tr._source_metadata["explicit"] is False


def test_qobuz_candidates_carry_the_flag():
    from core.qobuz_client import QobuzClient
    tr = QobuzClient._qobuz_to_track_result(
        object.__new__(QobuzClient),
        {"id": 7, "title": "S", "performer": {"name": "A"}, "parental_warning": True, "duration": 200},
        {}, require_streamable=False)
    assert tr._source_metadata["explicit"] is True


def test_tidal_candidates_carry_the_flag():
    from types import SimpleNamespace

    from core.tidal_download_client import TidalDownloadClient
    track = SimpleNamespace(id=7, name="S", version=None, artist=SimpleNamespace(name="A", id=1),
                            album=None, duration=200, track_num=1, isrc="", bpm=0, copyright="",
                            explicit=True)
    tr = TidalDownloadClient._tidal_to_track_result(object.__new__(TidalDownloadClient), track, {})
    assert tr._source_metadata["explicit"] is True
