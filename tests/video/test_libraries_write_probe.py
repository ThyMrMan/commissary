"""The video Libraries write probe.

Same failure the music side hit: a Library the server cannot write into is
indistinguishable from one nothing has been grabbed into yet — the grab
succeeds, the import fails, and the folder just stays empty. The probe makes
that state visible in Settings.

The gate matters as much as the probe. ``/api/video/libraries`` is admin-only
on WRITE only, because its GET is the Library tab bar and the download
destination picker, and non-admins are deliberately served a payload with the
filesystem paths stripped out. A probe endpoint hanging off that prefix would
inherit the GET's openness and hand back exactly the paths that payload
withholds, so it is named in the always-admin list.
"""

from __future__ import annotations

import pytest
from flask import Flask

from database.video_database import VideoDatabase

PROBE = "/api/video/libraries/probe"


@pytest.fixture()
def client(tmp_path):
    import api.video as videoapi
    videoapi._video_db = VideoDatabase(database_path=str(tmp_path / "video_library.db"))
    app = Flask(__name__)
    app.register_blueprint(videoapi.create_video_blueprint(), url_prefix="/api/video")

    @app.before_request
    def _fake_profile():
        from flask import g, request
        g.profile_id = int(request.headers.get("X-Test-Profile", 1))
        g.is_admin = g.profile_id == 1 or request.headers.get("X-Test-Admin") == "1"
        g.can_download = True
        g.profile_name = "T"

    try:
        yield app.test_client()
    finally:
        videoapi._video_db = None


def test_the_probe_is_admin_only(client):
    """It returns filesystem paths, which the members' /libraries payload
    deliberately strips."""
    assert client.get(PROBE, headers={"X-Test-Profile": "2"}).status_code == 403
    assert client.get(PROBE).status_code == 200                       # profile 1
    assert client.get(PROBE, headers={"X-Test-Profile": "7", "X-Test-Admin": "1"}
                      ).status_code == 200                            # secondary admin


def test_gating_the_probe_did_not_gate_the_libraries_list(client):
    """The tab bar and the destination picker are non-admin surfaces that read
    GET /libraries. Adding a prefix to the admin list is exactly the kind of
    change that could catch them, so pin it."""
    assert client.get("/api/video/libraries",
                      headers={"X-Test-Profile": "2"}).status_code == 200


def test_probe_reports_each_configured_library(client, tmp_path, monkeypatch):
    import api.video as videoapi
    good = tmp_path / "movies"
    good.mkdir()
    missing = tmp_path / "not-mounted"

    import api.video.libraries as libs_mod
    monkeypatch.setattr("core.video.sources.resolve_video_server", lambda: "plex",
                        raising=False)
    monkeypatch.setattr(videoapi._video_db, "list_libraries",
                        lambda server: {
                            "movies": [{"id": 1, "label": "Movies", "server_title": "Movies",
                                        "path": str(good)}],
                            "tv": [{"id": 2, "label": "TV", "server_title": "TV",
                                    "path": str(missing)}],
                        })
    assert libs_mod is not None

    body = client.get(PROBE).get_json()
    assert body["success"] is True
    movies = body["configured"]["movies"][0]
    tv = body["configured"]["tv"][0]
    assert movies["writable"] is True and movies["status"] == "ok"
    assert tv["writable"] is False and tv["status"] == "missing"
    # The row has to be identifiable in the UI.
    assert movies["id"] == 1 and movies["label"] == "Movies"


def test_a_shared_root_is_probed_once(client, tmp_path, monkeypatch):
    """Two Libraries may legitimately point at the same root. Probing it per
    entry would create and remove a folder in it twice for one page load."""
    import api.video as videoapi
    shared = tmp_path / "media"
    shared.mkdir()

    calls = []

    def _counting(path):
        calls.append(path)
        return {"status": "ok", "writable": True, "detail": "Writable"}

    monkeypatch.setattr("core.destination_probe.probe_destination_writable", _counting)
    monkeypatch.setattr("core.video.sources.resolve_video_server", lambda: "plex",
                        raising=False)
    monkeypatch.setattr(videoapi._video_db, "list_libraries",
                        lambda server: {
                            "movies": [{"id": 1, "label": "A", "path": str(shared)}],
                            "tv": [{"id": 2, "label": "B", "path": str(shared)}],
                        })

    body = client.get(PROBE).get_json()

    assert len(calls) == 1
    assert body["configured"]["movies"][0]["writable"] is True
    assert body["configured"]["tv"][0]["writable"] is True


def test_no_server_configured_is_not_an_error(client, monkeypatch):
    """A fresh install has no video server yet; the Settings page still loads
    and must not show an error banner because of the probe."""
    monkeypatch.setattr("core.video.sources.resolve_video_server", lambda: None,
                        raising=False)
    resp = client.get(PROBE)
    assert resp.status_code == 200
    assert resp.get_json()["success"] is True
