"""Library page: filter shows by TMDB lifecycle status (Airing / Ended / Upcoming).

The poster-corner badge already buckets TMDB's free-text ``status`` into these
three groups (video-library.js's showStatusBadge) but there was no way to
FILTER by them — just the visual badge. ``air_status`` mirrors the exact same
bucketing so the filter never disagrees with what's already on screen.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from flask import Flask

from database.video_database import VideoDatabase

_ROOT = Path(__file__).resolve().parent.parent
_LIB_JS = (_ROOT / "webui" / "static" / "video" / "video-library.js").read_text(encoding="utf-8")
_INDEX = (_ROOT / "webui" / "index.html").read_text(encoding="utf-8")


@pytest.fixture()
def db(tmp_path):
    return VideoDatabase(database_path=str(tmp_path / "video_library.db"))


def _seed(db):
    db.upsert_show_tree("plex", {"server_id": "s1", "title": "Returning Show",
                                 "status": "Returning Series", "seasons": []})
    db.upsert_show_tree("plex", {"server_id": "s2", "title": "Ended Show",
                                 "status": "Ended", "seasons": []})
    db.upsert_show_tree("plex", {"server_id": "s3", "title": "Cancelled Show",
                                 "status": "Canceled", "seasons": []})
    db.upsert_show_tree("plex", {"server_id": "s4", "title": "Pilot Show",
                                 "status": "In Production", "seasons": []})
    db.upsert_show_tree("plex", {"server_id": "s5", "title": "No Status Show",
                                 "seasons": []})
    db.upsert_movie("plex", {"server_id": "m1", "title": "A Movie", "year": 2020})


def test_air_status_airing_matches_continuing_and_returning(db):
    _seed(db)
    titles = [i["title"] for i in db.query_library("shows", air_status="airing")["items"]]
    assert titles == ["Returning Show"]


def test_air_status_ended_matches_ended_and_canceled(db):
    _seed(db)
    titles = {i["title"] for i in db.query_library("shows", air_status="ended")["items"]}
    assert titles == {"Ended Show", "Cancelled Show"}


def test_air_status_upcoming_matches_in_production(db):
    _seed(db)
    titles = [i["title"] for i in db.query_library("shows", air_status="upcoming")["items"]]
    assert titles == ["Pilot Show"]


def test_air_status_none_returns_everything(db):
    _seed(db)
    assert db.query_library("shows", air_status=None)["pagination"]["total_count"] == 5
    assert db.query_library("shows", air_status="bogus")["pagination"]["total_count"] == 5


def test_air_status_is_a_show_only_concept_movies_unaffected(db):
    _seed(db)
    # movies have no lifecycle status at all — the param is silently a no-op there
    assert [i["title"] for i in db.query_library("movies", air_status="airing")["items"]] == ["A Movie"]


def test_air_status_composes_with_other_filters(db):
    _seed(db)
    res = db.query_library("shows", air_status="ended", search="Cancelled")
    assert [i["title"] for i in res["items"]] == ["Cancelled Show"]


def test_api_passes_air_status_through(db, monkeypatch):
    import api.video as videoapi
    import core.video.sources as sources
    monkeypatch.setattr(sources, "resolve_video_server", lambda: "plex")
    videoapi._video_db = db
    try:
        _seed(db)
        app = Flask(__name__)
        app.register_blueprint(videoapi.create_video_blueprint(), url_prefix="/api/video")
        c = app.test_client()
        out = c.get("/api/video/library?kind=shows&air_status=airing").get_json()
        assert [i["title"] for i in out["items"]] == ["Returning Show"]
    finally:
        videoapi._video_db = None


# ---------------------------------------------------------------------------
# Frontend contracts
# ---------------------------------------------------------------------------

def test_js_and_html_wire_the_airing_filter():
    assert "data-video-lib-airing" in _INDEX
    assert "data-video-lib-airing" in _LIB_JS
    assert "air_status" in _LIB_JS
