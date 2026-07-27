"""Per-Library "Preferred trackers" — reported as "doesn't seem to save".

The storage layer was fine the whole time: the field round-trips through
save_libraries → list_libraries → the HTTP API correctly, for numeric input.

What actually happened is that the setting stores comma-separated PROWLARR
INDEXER IDS, and nothing in the app ever showed a user what those ids were.
Faced with a text box labelled "Preferred trackers", the natural thing to type
is a tracker's name — and ``_norm_indexer_ids`` keeps only ``.isdigit()``
tokens and silently drops the rest to None. So the field came back blank after
every save, which is indistinguishable from "it doesn't save".

The fix is to stop asking for a number the app won't tell you: a picker built
from Prowlarr's real indexer list. The stored value is still ids.
"""

from __future__ import annotations

import os
import tempfile
from pathlib import Path

import pytest
from flask import Flask, g

from database.video_database import VideoDatabase, _norm_indexer_ids

_ROOT = Path(__file__).resolve().parent.parent
_SETTINGS_JS = (_ROOT / "webui" / "static" / "video" / "video-settings.js").read_text(encoding="utf-8")
_CSS = (_ROOT / "webui" / "static" / "video" / "video-side.css").read_text(encoding="utf-8")


class _IX:
    def __init__(self, i, n, p="torrent", e=True):
        self.id, self.name, self.protocol, self.enable, self.privacy = i, n, p, e, "public"


@pytest.fixture()
def app_db(tmp_path, monkeypatch):
    import api.video as videoapi
    import core.video.sources as sources
    monkeypatch.setattr(sources, "resolve_video_server", lambda *a, **k: "plex")
    monkeypatch.setattr(sources, "list_video_libraries", lambda *a, **k: {
        "server": "plex", "movies": [{"title": "Movies"}], "tv": [{"title": "TV"}]})
    db = VideoDatabase(database_path=str(tmp_path / "video_library.db"))
    videoapi._video_db = db
    app = Flask(__name__)
    app.register_blueprint(videoapi.create_video_blueprint(), url_prefix="/api/video")
    persona = {"profile_id": 1, "is_admin": True, "can_download": True,
               "profile_name": "Admin", "allowed_sides": "both"}

    @app.before_request
    def _p():
        for k, v in persona.items():
            setattr(g, k, v)

    try:
        yield app.test_client(), db, persona
    finally:
        videoapi._video_db = None


@pytest.fixture()
def prowlarr(monkeypatch):
    import core.video.prowlarr_search as ps

    class _C:
        def is_configured(self):
            return True

        def _get_indexers_sync(self):
            return [_IX(1, "Nyaa"), _IX(7, "AnimeTosho"), _IX(9, "OldUsenet", "usenet", False)]

    monkeypatch.setattr(ps, "_client", lambda: _C())
    return ps


# ── the reported symptom, and its real cause ─────────────────────────────────
def test_numeric_ids_always_did_save(app_db):
    """Pins that storage was never the problem — so a future 'doesn't save'
    report points somewhere else."""
    client, db, _ = app_db
    client.post("/api/video/libraries", json={"movies": [], "tv": [
        {"server_title": "TV", "path": "/media/anime", "preferred_indexer_ids": "1,7"}]})
    got = client.get("/api/video/libraries").get_json()["configured"]["tv"]
    assert [e["preferred_indexer_ids"] for e in got] == ["1,7"]


def test_a_typed_tracker_name_is_silently_discarded(app_db):
    """The actual bug. A name normalises to None, the row saves blank, and the
    UI redraws an empty box — identical to the setting not persisting."""
    assert _norm_indexer_ids("Nyaa") is None
    assert _norm_indexer_ids("Nyaa,AnimeTosho") is None
    assert _norm_indexer_ids("nyaa (7)") is None        # even with the id present
    assert _norm_indexer_ids("#7") is None
    # tolerant of spacing, which was never the issue
    assert _norm_indexer_ids("1, 3") == "1,3"

    client, _, _ = app_db
    client.post("/api/video/libraries", json={"movies": [], "tv": [
        {"server_title": "TV", "path": "/media/anime", "preferred_indexer_ids": "Nyaa"}]})
    got = client.get("/api/video/libraries").get_json()["configured"]["tv"]
    assert got[0]["preferred_indexer_ids"] is None       # what the user saw as "didn't save"


# ── the fix: the app can now tell you what the trackers are ──────────────────
def test_the_indexer_list_is_exposed(app_db, prowlarr):
    client, _, _ = app_db
    body = client.get("/api/video/downloads/indexers").get_json()
    assert body["configured"] is True
    assert [(i["id"], i["name"]) for i in body["indexers"]] == \
        [(1, "Nyaa"), (7, "AnimeTosho"), (9, "OldUsenet")]


def test_the_indexer_list_never_leaks_urls_or_keys(app_db, prowlarr):
    """Indexer URLs leaking to the browser was a real security bug once; only
    identity fields may cross this boundary."""
    client, _, _ = app_db
    for ix in client.get("/api/video/downloads/indexers").get_json()["indexers"]:
        assert set(ix) == {"id", "name", "protocol", "enable", "privacy"}


def test_the_indexer_list_is_admin_only(app_db, prowlarr):
    client, _, persona = app_db
    persona.update({"profile_id": 5, "is_admin": False, "can_download": False})
    assert client.get("/api/video/downloads/indexers").status_code == 403


def test_an_unconfigured_prowlarr_reports_itself_instead_of_erroring(app_db, monkeypatch):
    """The picker degrades to the old id text box rather than showing nothing."""
    import core.video.prowlarr_search as ps

    class _Off:
        def is_configured(self):
            return False

        def _get_indexers_sync(self):
            raise AssertionError("must not be called when unconfigured")

    monkeypatch.setattr(ps, "_client", lambda: _Off())
    body = app_db[0].get("/api/video/downloads/indexers").get_json()
    assert body == {"configured": False, "indexers": []}


def test_an_unreachable_prowlarr_does_not_raise(app_db, monkeypatch):
    import core.video.prowlarr_search as ps

    class _Boom:
        def is_configured(self):
            return True

        def _get_indexers_sync(self):
            raise RuntimeError("connection refused")

    monkeypatch.setattr(ps, "_client", lambda: _Boom())
    body = app_db[0].get("/api/video/downloads/indexers").get_json()
    assert body["indexers"] == []


def test_ids_chosen_in_the_picker_still_round_trip(app_db, prowlarr):
    """End to end: the picker submits ids, and they come back selected."""
    client, _, _ = app_db
    client.post("/api/video/libraries", json={"movies": [], "tv": [
        {"server_title": "TV", "path": "/media/anime", "preferred_indexer_ids": "1,7"}]})
    tv = client.get("/api/video/libraries").get_json()["configured"]["tv"][0]
    assert tv["preferred_indexer_ids"] == "1,7"


# ── frontend contract ────────────────────────────────────────────────────────
def test_the_settings_page_renders_a_picker_and_keeps_a_fallback():
    assert "/api/video/downloads/indexers" in _SETTINGS_JS
    assert "data-lib-tracker" in _SETTINGS_JS
    # still submits through the same field the backend already understood
    assert "data-lib-indexer-ids" in _SETTINGS_JS
    assert "preferred_indexer_ids: indexerIdsInput" in _SETTINGS_JS
    # no Prowlarr → the id box stays usable rather than the setting vanishing
    assert "Connect Prowlarr to pick trackers" in _SETTINGS_JS
    assert ".library-tracker" in _CSS


def test_the_indexer_fetch_is_deduped_across_library_rows():
    """Every Library row builds a picker at once; without this each one fires
    its own request to Prowlarr."""
    assert "_indexersPromise" in _SETTINGS_JS
