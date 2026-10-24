"""Search every source for any track in the download window, and take one file."""

from __future__ import annotations

from pathlib import Path

import pytest

from core.commissary.track_inspector import track_info

_ROOT = Path(__file__).resolve().parents[2]


def test_a_search_page_single_becomes_a_pipeline_track():
    info = track_info({"id": "sp1", "name": "Idol", "artists": ["YOASOBI"], "album": "",
                       "duration_ms": 213000, "explicit": True, "track_number": 1})
    assert info["artists"] == [{"name": "YOASOBI"}]
    assert info["album"]["name"] == "Idol", "a single's album is the track"
    assert (info["explicit"], info["track_number"], info["duration_ms"]) == (True, 1, 213000)


def test_an_album_track_keeps_its_album():
    info = track_info({"name": "Idol", "artists": [{"name": "YOASOBI"}],
                       "album": {"name": "THE BOOK 3", "id": "al1"}})
    assert info["album"] == {"name": "THE BOOK 3", "id": "al1"}


def test_a_track_whose_artists_are_dicts_still_gets_an_album_dict():
    info = track_info({"name": "Idol", "artists": [{"name": "YOASOBI"}], "album": "THE BOOK 3"})
    assert info["album"] == {"name": "THE BOOK 3"}


@pytest.mark.parametrize("track", [None, {}, {"name": "  "}, "Idol"])
def test_no_name_is_nothing_to_search(track):
    assert track_info(track) is None


# ── the routes ──────────────────────────────────────────────────────────────
@pytest.fixture
def server(monkeypatch):
    web_server = pytest.importorskip("web_server")
    web_server.app.config["TESTING"] = True
    seen = {}

    def fake_stream(track_obj, profile_id, **kwargs):
        seen["track"] = track_obj
        return web_server.jsonify({"streamed": True})

    monkeypatch.setattr(web_server, "_inspect_sources_stream", fake_stream)
    monkeypatch.setattr(web_server, "check_download_permission", lambda: None)
    monkeypatch.setattr(web_server._pinned_batch, "dispatch_pinned_batch",
                        lambda batch_id, task_ids, deps: seen.setdefault("dispatched", (batch_id, task_ids)))
    return web_server, seen


TRACK = {"name": "Idol", "artists": [{"name": "YOASOBI"}], "album": {"name": "THE BOOK 3"},
         "duration_ms": 213000}


def test_inspect_streams_upstreams_verdicts_for_the_posted_track(server):
    web_server, seen = server
    with web_server.app.test_client() as client:
        assert client.post("/api/commissary/inspect", json={"track": TRACK}).get_json() == {"streamed": True}
        assert client.post("/api/commissary/inspect", json={}).status_code == 400
    assert seen["track"].name == "Idol"


def test_a_pick_starts_that_exact_file_pinned(server):
    web_server, seen = server
    candidate = {"username": "peer", "filename": "Music/YOASOBI/01 Idol.flac", "size": 30_000_000}
    with web_server.app.test_client() as client:
        data = client.post("/api/commissary/inspect/download",
                           json={"track": TRACK, "candidate": candidate}).get_json()
    assert data["success"] is True
    task = web_server.download_tasks[data["task_id"]]
    try:
        assert task["_pinned_candidate"]["filename"] == candidate["filename"]
        assert task["track_info"]["album"]["name"] == "THE BOOK 3"
        assert seen["dispatched"][1] == [data["task_id"]]
    finally:
        web_server.download_tasks.pop(data["task_id"], None)
        web_server.download_batches.pop(data["batch_id"], None)


def test_a_whole_release_cannot_be_picked_for_one_track(server):
    web_server, _seen = server
    with web_server.app.test_client() as client:
        resp = client.post("/api/commissary/inspect/download",
                           json={"track": TRACK, "candidate": {"username": "torrent", "filename": "x"}})
    assert resp.status_code == 400 and "whole releases" in resp.get_json()["error"]


def test_a_profile_without_download_rights_is_refused(server, monkeypatch):
    web_server, _seen = server
    monkeypatch.setattr(web_server, "check_download_permission",
                        lambda: (web_server.jsonify({"error": "no"}), 403))
    with web_server.app.test_client() as client:
        resp = client.post("/api/commissary/inspect/download",
                           json={"track": TRACK, "candidate": {"username": "peer", "filename": "x"}})
    assert resp.status_code == 403


def test_every_download_window_row_offers_it():
    downloads = (_ROOT / "webui" / "static" / "downloads.js").read_text(encoding="utf-8")
    assert "globalThis.commissaryInspectButton(playlistId, trackIndex)" in downloads
    index = (_ROOT / "webui" / "index.html").read_text(encoding="utf-8")
    assert "filename='commissary-track-inspector.js'" in index
