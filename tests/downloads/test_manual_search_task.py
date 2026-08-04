"""Open the multi-source picker for something nothing has tried to download yet.

Reported as: the music download process relies on adding things to the wishlist
and hoping they download; can a button search each available source and let me
download from there.

Most of that already existed. ``/api/downloads/task/<id>/manual-search`` searches
EVERY configured source and streams per-source results — but only for a download
that has already run and failed, because it reads its track metadata off an
existing task. So the picker was unreachable for a track you simply wanted now,
and the wishlist really was the only route in.

This adds the missing entry point and nothing else: a task parked in
'not_found' (the state /download-candidate accepts) with no automatic search
run. The client then drives the existing manual-search and download-candidate
endpoints unchanged.

That reuse is the design, not an implementation detail. /download-candidate
mutates the task it is given — resets retry counters, sets _user_manual_pick so
the auto-retry monitor stops second-guessing the choice, clears the pick from
used_sources — and routes the download through the shared path that applies the
AcoustID check and the quality quarantine. A separate "just download this"
endpoint would have had to duplicate all of it, and would have drifted.
"""

from __future__ import annotations

import pytest

pytest.importorskip("web_server")
import web_server  # noqa: E402
from core.runtime_state import download_tasks  # noqa: E402


@pytest.fixture
def client():
    return web_server.app.test_client()


@pytest.fixture(autouse=True)
def _clean_tasks():
    made = set(download_tasks)
    yield
    for key in set(download_tasks) - made:
        download_tasks.pop(key, None)


_SPOTIFY_SHAPED = {
    "id": "6anwyDGQmsg45JKiVKpKGA",
    "name": "Airbag",
    "artists": [{"name": "Radiohead"}],
    "album": {"name": "OK Computer"},
    "duration_ms": 284000,
}


def _post(client, body):
    return client.post("/api/downloads/manual-search/task", json=body)


# ── it creates a task the existing picker can use ────────────────────────────
def test_it_returns_a_task_id_and_a_query(client):
    r = _post(client, {"track_info": _SPOTIFY_SHAPED}).get_json()
    assert r["success"] is True
    assert r["task_id"]
    assert r["query"] == "Radiohead Airbag"


def test_the_task_is_parked_in_the_state_download_candidate_accepts(client):
    """/download-candidate refuses anything not in ('not_found', 'failed') — a
    task in any other state would make the picker's Download button 400."""
    task_id = _post(client, {"track_info": _SPOTIFY_SHAPED}).get_json()["task_id"]
    assert download_tasks[task_id]["status"] in ("not_found", "failed")


def test_it_carries_the_metadata_download_candidate_reconstructs_from(client):
    """That endpoint rebuilds a Track from task['track_info'] — a task without
    these keys would download the file and then tag it as Unknown."""
    task_id = _post(client, {"track_info": _SPOTIFY_SHAPED}).get_json()["task_id"]
    info = download_tasks[task_id]["track_info"]
    assert info["name"] == "Airbag"
    assert info["artists"] == [{"name": "Radiohead"}]
    assert info["album"]["name"] == "OK Computer"
    assert info["id"] == _SPOTIFY_SHAPED["id"]
    assert info["duration_ms"] == 284000


def test_it_carries_the_keys_the_candidate_flow_mutates(client):
    """used_sources is .discard()ed and batch_id is read; missing either would
    raise inside the pick rather than at creation."""
    task_id = _post(client, {"track_info": _SPOTIFY_SHAPED}).get_json()["task_id"]
    task = download_tasks[task_id]
    assert isinstance(task["used_sources"], set)
    assert task["batch_id"] is None          # every batch branch is guarded on this


def test_no_search_is_run_at_creation(client, monkeypatch):
    """The whole point is to hand the choice to the user. Kicking off the
    automatic search would race the manual pick and could download something
    else first."""
    called = []
    for name in ("_list_available_download_sources",):
        if hasattr(web_server, name):
            monkeypatch.setattr(web_server, name,
                                lambda *a, **k: called.append(name) or ("soulseek", []))
    _post(client, {"track_info": _SPOTIFY_SHAPED})
    assert called == []


# ── it accepts what the callers actually hold ────────────────────────────────
def test_a_flat_search_result_shape_works(client):
    """Search rows and wishlist rows carry {name/title, artist, album} strings,
    not Spotify's nested objects."""
    r = _post(client, {"name": "Airbag", "artist": "Radiohead",
                       "album": "OK Computer"}).get_json()
    info = download_tasks[r["task_id"]]["track_info"]
    assert info["artists"] == [{"name": "Radiohead"}]
    assert info["album"] == {"name": "OK Computer"}


def test_a_title_key_is_accepted_as_the_name(client):
    r = _post(client, {"title": "Airbag", "artist": "Radiohead"}).get_json()
    assert r["success"] and download_tasks[r["task_id"]]["track_info"]["name"] == "Airbag"


def test_plain_string_artists_are_normalised(client):
    r = _post(client, {"name": "Airbag", "artists": ["Radiohead", "Thom Yorke"]}).get_json()
    assert download_tasks[r["task_id"]]["track_info"]["artists"] == [
        {"name": "Radiohead"}, {"name": "Thom Yorke"}]


def test_a_track_with_no_artist_still_opens(client):
    """A bare filename is a legitimate thing to want to search for."""
    r = _post(client, {"name": "Some Rip"}).get_json()
    assert r["success"] is True
    assert r["query"] == "Some Rip"


def test_a_nameless_request_is_refused(client):
    r = _post(client, {"artist": "Radiohead"})
    assert r.status_code == 400
    assert "name" in r.get_json()["error"].lower()


# ── it is gated like every other download entry point ────────────────────────
def test_it_checks_download_permission(client, monkeypatch):
    """It starts a download, so a profile without download rights must not
    reach it — the same gate /download-candidate and /manual-search use."""
    import inspect
    src = inspect.getsource(web_server.create_manual_search_task)
    assert "check_download_permission()" in src
    assert src.index("check_download_permission") < src.index("get_json"), \
        "the permission gate must run before any work"


# ── the reused endpoints are untouched ───────────────────────────────────────
def test_the_streaming_search_still_reads_its_track_from_the_task():
    """If this entry point had changed manual-search's contract, every existing
    caller (the candidates panel on a failed download) would break."""
    import inspect
    src = inspect.getsource(web_server.manual_search_for_task)
    assert "download_tasks.get(task_id)" in src
    assert "track_info" in src


def test_the_pick_still_goes_through_download_candidate():
    """The safety nets live on that path. A parallel download route would
    bypass the AcoustID check and the quality quarantine."""
    import inspect
    src = inspect.getsource(web_server.download_selected_candidate)
    assert "_user_manual_pick" in src
    assert "check_download_permission()" in src


def test_the_new_task_is_marked_as_one(client):
    """So the Downloads page and any cleanup can tell a picker-created task from
    a real batch member."""
    task_id = _post(client, {"track_info": _SPOTIFY_SHAPED}).get_json()["task_id"]
    assert download_tasks[task_id]["_manual_search_task"] is True
