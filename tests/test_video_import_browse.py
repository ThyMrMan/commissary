"""Manual import: walk the filesystem instead of typing the file's full path.

The "Add a file to import" modal used to be a bare text input — every import
meant typing an absolute path by hand. This adds a Sonarr-style folder browser:
shortcuts to the configured download and library folders, a folder listing, and
click-to-pick on the video files inside.

The endpoint enumerates directories on the host, so it is admin-only. It gets
that from the blueprint's ``/api/video/import`` prefix gate, which is admin for
EVERY method — pinned below, because the gate living somewhere else is exactly
how such a thing quietly stops applying.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from flask import Flask, g

from database.video_database import VideoDatabase

_ROOT = Path(__file__).resolve().parent.parent
_IMPORT_JS = (_ROOT / "webui" / "static" / "video" / "video-import.js").read_text(encoding="utf-8")
_CSS = (_ROOT / "webui" / "static" / "video" / "video-side.css").read_text(encoding="utf-8")


@pytest.fixture()
def app_db(tmp_path, monkeypatch):
    import api.video as videoapi
    import core.video.sources as sources
    monkeypatch.setattr(sources, "resolve_video_server", lambda: "plex")
    d = VideoDatabase(database_path=str(tmp_path / "video_library.db"))
    videoapi._video_db = d
    app = Flask(__name__)
    app.register_blueprint(videoapi.create_video_blueprint(), url_prefix="/api/video")
    persona = {"profile_id": 1, "is_admin": True, "can_download": True,
               "profile_name": "Admin", "allowed_sides": "both"}

    @app.before_request
    def _persona():
        for k, v in persona.items():
            setattr(g, k, v)

    try:
        yield app.test_client(), d, persona
    finally:
        videoapi._video_db = None


@pytest.fixture()
def tree(tmp_path, monkeypatch):
    """A download folder with a subfolder, two videos, and noise to filter out."""
    dl = tmp_path / "downloads"
    (dl / "Some.Show.S01").mkdir(parents=True)
    (dl / "movie.mkv").write_bytes(b"x" * 2048)
    (dl / "Another.mp4").write_bytes(b"y" * 10)
    (dl / "notes.txt").write_text("not a video")
    (dl / ".hidden").mkdir()
    (dl / "Some.Show.S01" / "ep1.mkv").write_bytes(b"z" * 5)

    from config.settings import config_manager
    real = config_manager.get

    def fake_get(key, default=None):
        if key == "soulseek.download_path":
            return str(dl)
        if key in ("soulseek.torrent_download_path", "soulseek.usenet_download_path"):
            return []
        return real(key, default)

    monkeypatch.setattr(config_manager, "get", fake_get)
    return dl


def _browse(client, path=None):
    q = "?path=%s" % path if path else ""
    return client.get("/api/video/import/browse" + q).get_json()


# ── listing ──────────────────────────────────────────────────────────────────
def test_browse_opens_on_the_download_folder_with_no_path(app_db, tree):
    """The point of the feature: opening the dialog needs no typing at all."""
    client, _, _ = app_db
    d = _browse(client)
    assert d["success"] is True
    assert Path(d["path"]) == tree
    assert [s["label"] for s in d["shortcuts"]][0] == "Downloads"


def test_browse_lists_folders_and_video_files_only(app_db, tree):
    client, _, _ = app_db
    d = _browse(client, str(tree))
    assert [x["name"] for x in d["dirs"]] == ["Some.Show.S01"]      # .hidden filtered
    assert [x["name"] for x in d["files"]] == ["Another.mp4", "movie.mkv"]   # notes.txt filtered
    sizes = {x["name"]: x["size"] for x in d["files"]}
    assert sizes["movie.mkv"] == 2048


def test_browse_sorts_case_insensitively(app_db, tree):
    client, _, _ = app_db
    (tree / "aaa.mkv").write_bytes(b"a")
    (tree / "ZZZ.mkv").write_bytes(b"z")
    names = [x["name"] for x in _browse(client, str(tree))["files"]]
    assert names == sorted(names, key=str.lower)


def test_browse_walks_into_a_subfolder_and_back_out(app_db, tree):
    client, _, _ = app_db
    sub = _browse(client, str(tree / "Some.Show.S01"))
    assert [x["name"] for x in sub["files"]] == ["ep1.mkv"]
    assert Path(sub["parent"]) == tree                  # the "Parent folder" row
    back = _browse(client, sub["parent"])
    assert Path(back["path"]) == tree


def test_every_offered_file_would_be_accepted_by_the_import(app_db, tree):
    """The browser must not offer anything /import/add would then refuse."""
    client, _, _ = app_db
    from core.video.importer import is_video
    for f in _browse(client, str(tree))["files"]:
        assert is_video(f["name"]), f


# ── failure modes ────────────────────────────────────────────────────────────
def test_a_missing_folder_404s_but_still_returns_the_shortcuts(app_db, tree):
    """A wrong path must leave the user somewhere to click, not a dead dialog."""
    client, _, _ = app_db
    r = client.get("/api/video/import/browse?path=/definitely/not/here")
    assert r.status_code == 404
    body = r.get_json()
    assert body["success"] is False and body["error"]
    assert [s["label"] for s in body["shortcuts"]] == ["Downloads"]


def test_a_file_path_is_not_a_folder(app_db, tree):
    client, _, _ = app_db
    assert client.get("/api/video/import/browse?path=%s"
                      % (tree / "movie.mkv")).status_code == 404


def test_no_configured_folders_is_reported_not_crashed(app_db, monkeypatch):
    client, _, _ = app_db
    from config.settings import config_manager
    monkeypatch.setattr(config_manager, "get",
                        lambda k, d=None: [] if "download_path" in k else (
                            "" if k == "soulseek.download_path" else d))
    d = _browse(client)
    assert d["success"] is True and d["shortcuts"] == [] and d["error"]


def test_shortcuts_skip_folders_that_do_not_exist(app_db, tmp_path, monkeypatch):
    """A shortcut to an unmounted path is worse than no shortcut."""
    client, _, _ = app_db
    from config.settings import config_manager
    real = config_manager.get
    monkeypatch.setattr(config_manager, "get", lambda k, d=None: (
        "/nope/not/mounted" if k == "soulseek.download_path"
        else ([] if "download_path" in k else real(k, d))))
    assert _browse(client)["shortcuts"] == []


def test_library_roots_appear_as_shortcuts(app_db, tmp_path, tree):
    client, d, _ = app_db
    lib = tmp_path / "anime"
    lib.mkdir()
    conn = d._get_connection()
    conn.execute("INSERT INTO root_folders (path, content_kind, server, server_title, label) "
                 "VALUES (?,?,?,?,?)", (str(lib), "show", "plex", "Anime", "Anime"))
    conn.commit(); conn.close()
    labels = [s["label"] for s in _browse(client)["shortcuts"]]
    assert labels == ["Downloads", "Anime"]     # downloads first — the likely target


def test_shortcuts_are_deduped(app_db, tmp_path, monkeypatch):
    """The download folder doubling as a library root must list once."""
    client, d, _ = app_db
    shared = tmp_path / "shared"
    shared.mkdir()
    from config.settings import config_manager
    real = config_manager.get
    monkeypatch.setattr(config_manager, "get", lambda k, dd=None: (
        str(shared) if k == "soulseek.download_path"
        else ([] if "download_path" in k else real(k, dd))))
    conn = d._get_connection()
    conn.execute("INSERT INTO root_folders (path, content_kind, server, server_title, label) "
                 "VALUES (?,?,?,?,?)", (str(shared), "movie", "plex", "Movies", "Movies"))
    conn.commit(); conn.close()
    sc = _browse(client)["shortcuts"]
    assert [s["label"] for s in sc] == ["Downloads"]    # first label wins


# ── the admin gate ───────────────────────────────────────────────────────────
def test_browsing_the_filesystem_is_admin_only(app_db, tree):
    """It enumerates directories on the host. The gate lives on the blueprint's
    /api/video/import prefix, so this pins that it still reaches here."""
    client, _, persona = app_db
    persona.update({"profile_id": 5, "is_admin": False, "can_download": False})
    assert client.get("/api/video/import/browse").status_code == 403
    assert client.get("/api/video/import/browse?path=%s" % tree).status_code == 403


# ── frontend contract ────────────────────────────────────────────────────────
def test_the_modal_browses_and_still_accepts_a_typed_path():
    assert "/api/video/import/browse" in _IMPORT_JS
    assert "data-vimp-go" in _IMPORT_JS and "data-vimp-pick" in _IMPORT_JS
    # opens on the backend's default (the download folder) — no path argument
    assert "browseTo('')" in _IMPORT_JS
    # the text field survives: pasting a path is still the fastest route
    assert "data-vimp-add-path" in _IMPORT_JS
    assert "syncAddConfirm" in _IMPORT_JS
    assert ".vimp-browse-list" in _CSS and ".vimp-chip" in _CSS
