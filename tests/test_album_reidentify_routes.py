"""The album Re-identify routes, driven through the real app.

The module tests pin the logic; these pin the WIRING -- that each route reaches
the library, the release lookup, the pipeline and the admin gate it should.
A source pin cannot tell a route that calls the right function from one that
names it.
"""

from __future__ import annotations

import os
from unittest.mock import patch

import pytest

import core.imports.album as album_mod
import core.imports.album_reidentify as reid
import core.imports.pipeline as pipeline
import core.imports.side_effects as side_effects

ARTIST_ID = 930001
ALBUM_ID = 930010
TRACK_ID = 930100
OTHER_ALBUM_TRACK_ID = 930999


@pytest.fixture
def app_client():
    with patch("web_server.add_activity_item"):
        with patch("web_server.SpotifyClient"):
            with patch("core.tidal_client.TidalClient"):
                import web_server
                web_server.app.config["TESTING"] = True
                yield web_server, web_server.app.test_client()


@pytest.fixture
def seeded(app_client, tmp_path):
    web_server, _client = app_client
    db = web_server.get_database()
    old = tmp_path / "Library" / "Old Album" / "01 Song.flac"
    old.parent.mkdir(parents=True)
    old.write_bytes(b"audio")
    conn = db._get_connection()
    try:
        cur = conn.cursor()
        cur.execute("INSERT INTO artists (id, name) VALUES (?, 'Route Artist')", (ARTIST_ID,))
        cur.execute("INSERT INTO albums (id, artist_id, title) VALUES (?, ?, 'Old Album')",
                    (ALBUM_ID, ARTIST_ID))
        cur.execute(
            "INSERT INTO tracks (id, album_id, artist_id, title, track_number, disc_number,"
            " duration, file_path) VALUES (?, ?, ?, 'Song', 1, 1, 200000, ?)",
            (TRACK_ID, ALBUM_ID, ARTIST_ID, str(old)))
        conn.commit()
    finally:
        conn.close()
    reid.clear_release_cache()
    yield db, old
    reid.clear_release_cache()
    conn = db._get_connection()
    try:
        conn.execute("DELETE FROM tracks WHERE album_id = ?", (ALBUM_ID,))
        conn.execute("DELETE FROM albums WHERE id = ?", (ALBUM_ID,))
        conn.execute("DELETE FROM artists WHERE id = ?", (ARTIST_ID,))
        conn.commit()
    finally:
        conn.close()


@pytest.fixture
def release(monkeypatch):
    def fake(album_id, artist_name="", album_name="", source=None):
        return {"success": True,
                "album": {"id": album_id, "name": "New Album", "artist": "Route Artist",
                          "artists": [{"name": "Route Artist"}], "source": "spotify"},
                "tracks": [{"id": "t1", "name": "Song", "track_number": 1, "disc_number": 1,
                            "duration_ms": 200000, "artists": [{"name": "Route Artist"}]}],
                "source": "spotify"}
    monkeypatch.setattr(album_mod, "get_artist_album_tracks", fake)
    monkeypatch.setattr(album_mod, "resolve_album_artist_context",
                        lambda album, source="": {"id": "a1", "name": "Route Artist", "genres": []})


def _body(**extra):
    body = {"library_album_id": ALBUM_ID, "source": "spotify", "release_album_id": "rel-1",
            "release_album_name": "New Album", "release_album_artist": "Route Artist"}
    body.update(extra)
    return body


def test_the_preview_route_pairs_the_library_album_with_the_release(app_client, seeded, release):
    _web_server, client = app_client
    r = client.post("/api/reidentify/album/preview", json=_body())
    data = r.get_json()

    assert r.status_code == 200 and data["success"] is True
    assert str(data["pairs"][0]["library_track"]["id"]) == str(TRACK_ID)
    assert data["pairs"][0]["release_track"]["key"] == "1-1"


def test_the_apply_route_refuses_a_track_from_another_album(app_client, seeded, release, monkeypatch):
    web_server, client = app_client
    monkeypatch.setattr(side_effects, "is_active_media_server_ready", lambda: (True, ""))
    moved = []
    monkeypatch.setattr(web_server, "_post_process_matched_download", lambda *a: moved.append(a))

    r = client.post("/api/reidentify/album/apply-track",
                    json=_body(library_track_id=OTHER_ALBUM_TRACK_ID, release_track_key="1-1"))

    assert r.status_code == 400 and moved == []


def test_the_apply_route_imports_a_copy_and_replaces_the_original(
        app_client, seeded, release, monkeypatch, tmp_path):
    web_server, client = app_client
    db, old = seeded
    monkeypatch.setattr(side_effects, "is_active_media_server_ready", lambda: (True, ""))
    monkeypatch.setattr(web_server, "_resolve_library_file_path", lambda p: p)
    monkeypatch.setattr(pipeline, "import_rejection_reason", lambda context: None)
    seen = {}

    def fake_pipeline(key, context, staged):
        new = tmp_path / "Library" / "New Album" / "01 Song.flac"
        new.parent.mkdir(parents=True, exist_ok=True)
        os.replace(staged, new)
        context["_final_processed_path"] = str(new)
        seen["context"] = context

    monkeypatch.setattr(web_server, "_post_process_matched_download", fake_pipeline)

    r = client.post("/api/reidentify/album/apply-track",
                    json=_body(library_track_id=TRACK_ID, release_track_key="1-1", replace=True))

    assert r.status_code == 200, r.get_json()
    assert seen["context"]["_user_manual_pick"] is True
    assert not old.exists()
    conn = db._get_connection()
    try:
        assert conn.execute("SELECT COUNT(*) FROM tracks WHERE id = ?", (TRACK_ID,)).fetchone()[0] == 0
    finally:
        conn.close()


def test_the_album_search_route_uses_the_import_search(app_client, monkeypatch):
    _web_server, client = app_client
    import core.imports.staging as staging
    seen = {}

    def fake_search(query, limit=12, source_override=None):
        seen.update(query=query, source=source_override)
        return [{"id": "rel-1", "name": "New Album", "artist": "Route Artist", "source": "deezer"}]

    monkeypatch.setattr(staging, "search_import_albums", fake_search)
    r = client.get("/api/reidentify/album/search?q=New+Album&source=deezer")

    assert r.status_code == 200
    assert r.get_json()["albums"][0]["id"] == "rel-1"
    assert seen == {"query": "New Album", "source": "deezer"}


@pytest.mark.parametrize("method,path", [
    ("get", "/api/reidentify/album/search?q=x"),
    ("post", "/api/reidentify/album/preview"),
    ("post", "/api/reidentify/album/apply-track"),
])
def test_every_album_route_refuses_a_non_admin_profile(app_client, monkeypatch, method, path):
    web_server, client = app_client
    monkeypatch.setattr(web_server, "get_current_profile_id", lambda: 987654)
    r = getattr(client, method)(path, json=_body()) if method == "post" else client.get(path)
    assert r.status_code == 403
