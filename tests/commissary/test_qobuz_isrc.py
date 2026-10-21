"""A mirrored Qobuz track is identified by its ISRC, not a title search."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from core.commissary import qobuz_isrc as qi
from core.discovery import isrc_match as im
from core.discovery.direct_match import direct_source_match

_REAL_RUNNING_QOBUZ = qi._running_qobuz


class _Qobuz:
    def __init__(self, tracks):
        self.tracks, self.calls = tracks, []

    def get_track(self, track_id):
        self.calls.append(track_id)
        return self.tracks.get(str(track_id))

    def is_authenticated(self):
        return True


EXTENDED = {"id": 101, "title": "Strobe (Extended Mix)", "isrc": "ca-6d2-09-00011",
            "performer": {"name": "deadmau5"}, "album": {"artist": {"name": "deadmau5"}}}


@pytest.fixture(autouse=True)
def fresh(monkeypatch):
    qi._cache.clear()
    qobuz = _Qobuz({"101": EXTENDED, "102": {"id": 102, "title": "No Code"}})
    monkeypatch.setattr(qi, "_running_qobuz", lambda: qobuz)
    seen = []

    def deezer(client, code):
        seen.append(code)
        return SimpleNamespace(name="Strobe (Extended Mix)", artists=["deadmau5"])

    monkeypatch.setattr(im, "_deezer_isrc", deezer)
    return qobuz, seen


def test_a_qobuz_mirror_track_is_found_by_its_isrc(fresh):
    qobuz, seen = fresh
    hit = direct_source_match("qobuz", "deezer", False, "101", fallback_client=object())
    assert hit.name == "Strobe (Extended Mix)"
    assert seen == ["CA6D20900011"], "the cleaned ISRC, looked up on the active source"


def test_the_isrc_is_read_once_per_track(fresh):
    qobuz, _seen = fresh
    for _ in range(3):
        direct_source_match("qobuz", "deezer", False, "101", fallback_client=object())
    assert qobuz.calls == ["101"]


def test_a_hit_by_another_artist_falls_back_to_search(fresh, monkeypatch):
    monkeypatch.setattr(im, "_deezer_isrc",
                        lambda client, code: SimpleNamespace(name="Strobe", artists=["Someone Else"]))
    assert direct_source_match("qobuz", "deezer", False, "101", fallback_client=object()) is None


@pytest.mark.parametrize("track_id, source", [
    ("102", "deezer"),        # Qobuz has no ISRC for it
    ("not-a-qobuz-id", "deezer"),
    ("101", "itunes"),        # iTunes has no ISRC lookup: upstream's search runs
])
def test_anything_that_doesnt_pan_out_is_the_normal_search(fresh, track_id, source):
    assert direct_source_match("qobuz", source, False, track_id, fallback_client=object()) is None


def test_qobuz_signed_out_is_the_normal_search(monkeypatch):
    qi._cache.clear()
    monkeypatch.setattr(qi, "_running_qobuz", lambda: None)
    assert direct_source_match("qobuz", "deezer", False, "101", fallback_client=object()) is None


def test_no_orchestrator_is_never_built_for_it(monkeypatch):
    import core.download_orchestrator as orchestration
    monkeypatch.setattr(orchestration, "_default_orchestrator", None)
    monkeypatch.setattr(orchestration, "DownloadOrchestrator",
                        lambda *a, **k: (_ for _ in ()).throw(AssertionError("built one")))
    assert _REAL_RUNNING_QOBUZ() is None
