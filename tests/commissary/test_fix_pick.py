"""A Fix pick from another source is completed from its own source before it is saved."""

from __future__ import annotations

from pathlib import Path

import pytest

from core.metadata.types import Track

ARTIST = "Daft Punk"
ITUNES_PICK = {"id": "1440857786", "name": "HBFS zqfp", "artists": [ARTIST], "album": "Discovery",
               "duration_ms": 224000, "image_url": "https://img/hbfs", "source": "itunes"}
ITUNES_DETAILS = {"id": "1440857786", "name": "HBFS zqfp", "track_number": 4, "disc_number": 1,
                  "album": {"id": "1440857781", "name": "Discovery", "total_tracks": 14,
                            "release_date": "2001-03-07T08:00:00Z", "album_type": "album"}}
DEEZER_PICK = dict(ITUNES_PICK, id="3135556", source="deezer")
DEEZER_DETAILS = {"id": "3135556", "name": "HBFS zqfp", "track_number": 4, "disc_number": 1,
                  "album": {"id": "302127", "name": "Discovery", "total_tracks": 0, "release_date": "2001-03-07"}}
DEEZER_ALBUM = {"id": 302127, "nb_tracks": 14, "release_date": "2001-03-07"}
DETAILS = ("track_number", "disc_number", "album_id", "release_date", "total_tracks")
COMPLETE = (4, "2001-03-07", 14)


class _LookupClient:
    """A metadata client that answers track and album lookups."""

    def __init__(self, details=None, album=None, fail=False):
        self.details, self.album, self.fail = details, album, fail
        self.lookups = []

    def get_track_details(self, track_id):
        self.lookups.append(("track", track_id))
        if self.fail:
            raise RuntimeError("the source is down")
        return self.details

    def get_album_raw(self, album_id):
        self.lookups.append(("album", album_id))
        return self.album


def _complete(pick, client):
    from core.commissary.fix_pick import complete_fix_pick
    asked = []
    return complete_fix_pick(pick, lambda source: asked.append(source) or client), asked


def _completed(pick=ITUNES_PICK, details=ITUNES_DETAILS):
    return _complete(dict(pick), _LookupClient(details=details, album=DEEZER_ALBUM))[0]


def _saved_details(saved):
    return saved.get("track_number"), saved["album"].get("release_date"), saved["album"].get("total_tracks")


# ── completing a pick ───────────────────────────────────────────────────────
def test_an_itunes_pick_is_completed_from_itunes():
    pick = dict(ITUNES_PICK)
    completed, asked = _complete(pick, _LookupClient(details=ITUNES_DETAILS))

    assert asked == ["itunes"]
    assert {key: completed[key] for key in DETAILS} == {
        "track_number": 4, "disc_number": 1, "album_id": "1440857781", "release_date": "2001-03-07",
        "total_tracks": 14}
    assert pick == ITUNES_PICK


def test_a_deezer_pick_takes_its_track_count_from_its_album():
    client = _LookupClient(details=DEEZER_DETAILS, album=DEEZER_ALBUM)
    completed, asked = _complete(dict(DEEZER_PICK), client)

    assert asked == ["deezer"]
    assert client.lookups == [("track", "3135556"), ("album", "302127")]
    assert {key: completed[key] for key in DETAILS} == {
        "track_number": 4, "disc_number": 1, "album_id": "302127", "release_date": "2001-03-07", "total_tracks": 14}


def test_a_lookup_fills_in_only_what_the_pick_lacks():
    completed, _ = _complete(dict(ITUNES_PICK, track_number=7, release_date="2001-03-12"),
                             _LookupClient(details=ITUNES_DETAILS))

    assert (completed["track_number"], completed["release_date"], completed["total_tracks"]) == (7, "2001-03-12", 14)


def test_an_answer_for_another_track_is_not_used():
    completed, _ = _complete(dict(ITUNES_PICK), _LookupClient(details=dict(ITUNES_DETAILS, id="999")))
    assert completed == ITUNES_PICK


def test_a_failed_lookup_or_a_source_that_isnt_connected_saves_the_pick_as_it_came():
    assert _complete(dict(ITUNES_PICK), _LookupClient(fail=True))[0] == ITUNES_PICK
    assert _complete(dict(ITUNES_PICK), None)[0] == ITUNES_PICK


def test_a_complete_pick_and_a_spotify_or_musicbrainz_pick_look_nothing_up():
    client = _LookupClient(details=ITUNES_DETAILS)
    complete = dict(ITUNES_PICK, track_number=4, release_date="2001-03-07", total_tracks=14)
    for pick in (complete, dict(ITUNES_PICK, source="spotify"), dict(ITUNES_PICK, source="musicbrainz"),
                 dict(ITUNES_PICK, source="")):
        assert _complete(pick, client) == (pick, [])
    assert client.lookups == []

# ── what the page is sent back ──────────────────────────────────────────────
def test_the_saved_pick_carries_its_album_and_numbers():
    from core.commissary.fix_pick import completed_for_save
    out = completed_for_save(dict(DEEZER_PICK), lambda _s: _LookupClient(details=DEEZER_DETAILS, album=DEEZER_ALBUM))
    assert (out["track_number"], out["disc_number"], out["release_date"]) == (4, 1, "2001-03-07")
    assert out["album"] == {"name": "Discovery", "release_date": "2001-03-07", "total_tracks": 14,
                            "id": "302127", "image_url": "https://img/hbfs",
                            "images": [{"url": "https://img/hbfs"}]}


def test_a_lean_album_keeps_no_id_from_another_source():
    from core.commissary.fix_pick import completed_for_save
    lean = dict(ITUNES_DETAILS, album=dict(ITUNES_DETAILS["album"], total_tracks=0))
    out = completed_for_save(dict(ITUNES_PICK), lambda _s: _LookupClient(details=lean))
    assert "id" not in out["album"]


def test_the_endpoint_completes_a_posted_pick(monkeypatch):
    web_server = pytest.importorskip("web_server")
    monkeypatch.setattr("core.metadata.registry.get_client_for_source",
                        lambda source, **_k: _LookupClient(details=ITUNES_DETAILS))
    with web_server.app.test_client() as client:
        data = client.post("/api/commissary/fix-pick/complete", json={"track": ITUNES_PICK}).get_json()
        assert client.post("/api/commissary/fix-pick/complete", json={}).status_code == 400
    assert data["track"]["track_number"] == 4 and data["track"]["album"]["id"] == "1440857781"


def test_the_fix_dialog_completes_a_pick_before_saving_it():
    modal = (Path(__file__).resolve().parents[2] / "webui" / "src" / "routes" / "sync" / "-ui"
             / "fix-modal.tsx").read_text(encoding="utf-8")
    assert "const picked = await completePick(track);" in modal
    assert "buildUpdateMatchBody(sourceId, row.index, row.yt_track, row.yt_artist, picked)" in modal
