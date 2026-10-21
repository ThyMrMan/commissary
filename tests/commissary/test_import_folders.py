"""Import from any of your download folders, not only the Import folder."""

from __future__ import annotations

import os
from pathlib import Path

import pytest
from flask import Flask, g

from core.commissary import import_folders as imf

_ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture
def folders(tmp_path, monkeypatch):
    staging = tmp_path / "Staging"
    torrents = tmp_path / "downloads"
    usenet = tmp_path / "usenet"
    sibling = tmp_path / "downloads-elsewhere"
    for d in (staging, torrents / "complete" / "Music" / "Album X", usenet, sibling):
        d.mkdir(parents=True)
    (torrents / "complete" / "Music" / "Album X" / "01 Song.flac").write_bytes(b"x")
    (torrents / "complete" / "Music" / "Album X" / "cover.jpg").write_bytes(b"x")
    values = {"download_source.torrent_download_path": f"{torrents}; {tmp_path / 'missing'}",
              "download_source.usenet_download_path": str(usenet)}
    from core.settings import config_manager
    real_get = config_manager.get
    monkeypatch.setattr(config_manager, "get",
                        lambda key, default=None: values[key] if key in values else (
                            "" if key.startswith(("soulseek.", "usenet_client.")) else real_get(key, default)))
    monkeypatch.setattr("core.imports.staging.get_staging_path", lambda: str(staging))
    monkeypatch.setattr("core.imports.routes._get_staging_path", lambda: str(staging))
    return {"staging": staging, "torrents": torrents, "usenet": usenet, "sibling": sibling,
            "album": torrents / "complete" / "Music" / "Album X"}


def _norm(p):
    return os.path.normcase(os.path.realpath(str(p)))


def test_the_roots_are_the_import_folder_then_every_download_folder(folders):
    labels = [(label, _norm(path)) for label, path in imf.roots()]
    assert labels == [("Import folder", _norm(folders["staging"])),
                      ("Torrents", _norm(folders["torrents"])),
                      ("Usenet", _norm(folders["usenet"]))], "a missing folder is skipped"


def test_only_folders_inside_a_root_are_accepted(folders):
    assert _norm(imf.within(folders["album"])) == _norm(folders["album"])
    assert imf.within(folders["sibling"]) is None, "a lookalike sibling is not inside"
    assert imf.within(folders["torrents"] / ".." / "downloads-elsewhere") is None
    assert imf.within(folders["torrents"] / "nope") is None


def test_a_symlink_out_of_a_root_is_refused(folders, tmp_path):
    outside = tmp_path / "outside"
    outside.mkdir()
    link = folders["torrents"] / "link"
    try:
        os.symlink(outside, link, target_is_directory=True)
    except (OSError, NotImplementedError):
        pytest.skip("no symlinks here")
    assert imf.within(link) is None


def test_upstreams_import_check_accepts_the_download_folders(folders):
    from core.imports import routes
    roots = [_norm(p) for p in routes._get_allowed_import_roots()]
    assert _norm(folders["torrents"]) in roots and _norm(folders["staging"]) in roots


# ── the request override ────────────────────────────────────────────────────
def _request(path, admin=True):
    app = Flask(__name__)
    ctx = app.test_request_context("/api/import/inbox", query_string={"scan_path": str(path)})
    ctx.push()
    g.is_admin = admin
    return ctx


def test_an_admin_scans_the_chosen_folder(folders, monkeypatch):
    monkeypatch.setattr(imf, "_is_admin", lambda: True)
    ctx = _request(folders["album"])
    try:
        assert _norm(imf.runtime_overrides()["get_staging_path"]()) == _norm(folders["album"])
    finally:
        ctx.pop()


@pytest.mark.parametrize("admin, which", [(False, "album"), (True, "sibling")])
def test_otherwise_the_import_folder_as_always(folders, monkeypatch, admin, which):
    monkeypatch.setattr(imf, "_is_admin", lambda: admin)
    ctx = _request(folders[which])
    try:
        assert imf.runtime_overrides() == {}
    finally:
        ctx.pop()


def test_outside_a_request_nothing_changes():
    assert imf.runtime_overrides() == {}


# ── browse ──────────────────────────────────────────────────────────────────
def test_browse_with_no_path_lists_the_roots(folders):
    payload, status = imf.browse(None)
    assert status == 200 and [s["label"] for s in payload["shortcuts"]] == ["Import folder", "Torrents", "Usenet"]


def test_browse_lists_subfolders_and_counts_audio_below(folders):
    payload, status = imf.browse(str(folders["torrents"] / "complete"))
    assert status == 200
    assert [f["name"] for f in payload["folders"]] == ["Music"]
    assert payload["total_audio"] == 1 and payload["audio_files"] == 0
    assert _norm(payload["parent"]) == _norm(folders["torrents"])


def test_up_stops_at_a_root(folders):
    assert imf.browse(str(folders["torrents"]))[0]["parent"] is None


def test_browse_refuses_outside_and_says_missing(folders, tmp_path):
    assert imf.browse(str(folders["sibling"]))[1] == 403
    assert imf.browse(str(tmp_path / "gone"))[1] == 404


# ── through the real Import endpoints ───────────────────────────────────────
def test_the_import_page_scans_the_chosen_folder(folders, monkeypatch):
    web_server = pytest.importorskip("web_server")
    from core.imports import routes
    routes.invalidate_staging_scan_cache()
    monkeypatch.setattr(imf, "_is_admin", lambda: True)
    web_server.app.config["TESTING"] = True
    with web_server.app.test_client() as client:
        data = client.get("/api/import/staging/files",
                          query_string={"scan_path": str(folders["album"])}).get_json()
        for _ in range(50):
            if data.get("files") or not data.get("scanning"):
                break
            import time
            time.sleep(0.1)
            data = client.get("/api/import/staging/files",
                              query_string={"scan_path": str(folders["album"])}).get_json()
        browse = client.get("/api/commissary/import/browse").get_json()
    assert _norm(data["staging_path"]) == _norm(folders["album"])
    assert [os.path.basename(f.get("full_path") or f.get("path") or f.get("filename", "")) for f in data["files"]] \
        == ["01 Song.flac"]
    assert browse["shortcuts"]
    routes.invalidate_staging_scan_cache()


def test_the_import_strip_offers_the_picker():
    inbox = (_ROOT / "webui" / "src" / "routes" / "import" / "-ui" / "inbox.tsx").read_text(encoding="utf-8")
    assert "<CommissaryImportFolder />" in inbox
    client = (_ROOT / "webui" / "src" / "app" / "api-client.ts").read_text(encoding="utf-8")
    assert "hooks: { beforeRequest: [commissaryImportFolderHook] }" in client
