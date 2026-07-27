"""Manual search understands releases that carry no SxxExx.

Reported: "✗ NOT A SINGLE EPISODE" on a file the user had verified by hand was a
single episode. The message was describing a conclusion the code never reached —
it is the fall-through when ``parse_release`` finds NO episode number at all, not
a judgement that the release is a pack.

Fansub anime uses absolute numbering ('[SubsPlease] Show - 03'); daily series are
named by air date ('The.Daily.Show.2026.07.08'). Both parse to ``episode=None``.
``_scope_ok`` has escape hatches for exactly these — ``want_absolute`` and
``want_date`` — and ``_evaluate_hits`` accepts both and passes them down. No
caller in api/video/downloads.py ever supplied them, so the hourly drain grabbed
such a release happily while searching for it by hand always failed.

Same shape as the alias gap fixed in 1.6.10: the automated path had a capability
the interactive path was never wired for.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from flask import Flask, g

from core.video.quality_eval import evaluate_release
from core.video.release_parse import parse_release
from database.video_database import VideoDatabase

_ROOT = Path(__file__).resolve().parent.parent
_DOWNLOADS_PY = (_ROOT / "api" / "video" / "downloads.py").read_text(encoding="utf-8")

_FANSUB = "[SubsPlease] Tenkosaki - 03 [Web][MKV][h264][1080p][AAC 2.0]"
_PROFILE = {"tiers": [{"key": k, "enabled": True}
                      for k in ("web-1080p", "webdl-1080p", "bluray-1080p")]}


@pytest.fixture()
def app_db(tmp_path, monkeypatch):
    import api.video as videoapi
    import core.video.sources as sources
    monkeypatch.setattr(sources, "resolve_video_server", lambda *a, **k: "plex")
    db = VideoDatabase(database_path=str(tmp_path / "video_library.db"))
    videoapi._video_db = db
    app = Flask(__name__)
    app.register_blueprint(videoapi.create_video_blueprint(), url_prefix="/api/video")

    @app.before_request
    def _p():
        g.profile_id = 1; g.is_admin = True; g.can_download = True; g.allowed_sides = "both"

    try:
        yield app.test_client(), db
    finally:
        videoapi._video_db = None


# ── what the message actually meant ──────────────────────────────────────────
def test_a_fansub_episode_has_no_parsable_episode_number():
    p = parse_release(_FANSUB)
    assert p["season"] is None and p["episode"] is None


def test_without_the_hint_it_is_rejected_and_with_it_accepted():
    """The exact before/after the user hit."""
    p = parse_release(_FANSUB)
    miss = evaluate_release(p, _PROFILE, scope="episode", want_season=1, want_episode=3)
    assert miss["accepted"] is False
    assert "No episode number" in miss["rejected"]

    hit = evaluate_release(p, _PROFILE, scope="episode", want_absolute=3)
    assert hit["accepted"] is True


def test_the_message_no_longer_claims_it_is_a_pack():
    """The old wording sent people off checking for season packs."""
    v = evaluate_release(parse_release(_FANSUB), _PROFILE, scope="episode",
                         want_season=1, want_episode=3)
    assert "Not a single episode" not in (v["rejected"] or "")
    assert v["rejected"] == "No episode number in the release name"


# ── the plumbing ─────────────────────────────────────────────────────────────
def _seed_show(db, *, tmdb_id=500, air_date="2026-07-20"):
    return db.upsert_show_tree("plex", {
        "server_id": "s1", "tmdb_id": tmdb_id, "title": "Tenkosaki",
        "seasons": [{"season_number": 1, "episodes": [
            {"season_number": 1, "episode_number": 1, "title": "E1", "air_date": "2026-07-06"},
            {"season_number": 1, "episode_number": 2, "title": "E2", "air_date": "2026-07-13"},
            {"season_number": 1, "episode_number": 3, "title": "E3", "air_date": air_date}]}]})


def test_hints_resolve_from_a_tmdb_payload(app_db):
    from api.video.downloads import _episode_hints
    _, db = app_db
    _seed_show(db)
    want_date, want_absolute = _episode_hints(
        db, {"scope": "episode", "media_id": 500, "media_source": "tmdb"}, 1, 3)
    assert want_date == "2026-07-20"
    assert want_absolute == 3


def test_hints_resolve_from_a_library_row(app_db):
    """The detail page sends a library row id, not a tmdb id."""
    from api.video.downloads import _episode_hints
    _, db = app_db
    show_id = _seed_show(db)
    want_date, want_absolute = _episode_hints(
        db, {"scope": "episode", "media_id": show_id, "media_source": "library"}, 1, 3)
    assert want_date == "2026-07-20" and want_absolute == 3


def test_hints_are_not_gated_on_series_type(app_db):
    """search_context only derives `absolute` for shows tagged series_type=anime.
    The manual path deliberately does not: both hints only ever ACCEPT, so
    computing them always is safe — and it stops a mistagged show from silently
    breaking search, which is its own common failure."""
    from api.video.downloads import _episode_hints
    _, db = app_db
    _seed_show(db)
    conn = db._get_connection()
    conn.execute("UPDATE shows SET series_type='standard' WHERE tmdb_id=500")
    conn.commit(); conn.close()
    assert _episode_hints(db, {"scope": "episode", "media_id": 500,
                               "media_source": "tmdb"}, 1, 3)[1] == 3


def test_hints_degrade_quietly(app_db):
    from api.video.downloads import _episode_hints
    _, db = app_db
    # movie scope → not an episode search
    assert _episode_hints(db, {"scope": "movie", "media_id": 1, "media_source": "tmdb"}) == (None, None)
    # nothing to resolve from
    assert _episode_hints(db, {"scope": "episode"}, 1, 3) == (None, None)
    # unknown show
    assert _episode_hints(db, {"scope": "episode", "media_id": 999999,
                               "media_source": "tmdb"}, 1, 3) == (None, None)
    # missing season/episode
    assert _episode_hints(db, {"scope": "episode", "media_id": 500,
                               "media_source": "tmdb"}, None, None) == (None, None)
    # junk id must not raise
    assert _episode_hints(db, {"scope": "episode", "media_id": "nope",
                               "media_source": "library"}, 1, 3) == (None, None)


def test_every_search_endpoint_passes_the_hints():
    """Four call sites; missing one leaves that entry point rejecting fansub and
    daily releases. _evaluate_hits already ACCEPTED both params — only the
    callers were missing, which is why this went unnoticed."""
    body = _DOWNLOADS_PY.split("def _episode_hints", 1)[1]
    assert body.count("want_date=_ep_hints[0], want_absolute=_ep_hints[1]") == 3
    assert body.count("want_date=_poll_hints[0], want_absolute=_poll_hints[1]") == 1
    assert body.count("_episode_hints(get_video_db()") == 3


def test_air_date_lookup_is_defensive(app_db):
    _, db = app_db
    _seed_show(db)
    assert db.episode_air_date(500, 1, 3) == "2026-07-20"
    assert db.episode_air_date(500, 9, 9) is None
    assert db.episode_air_date(999999, 1, 1) is None
    assert db.episode_air_date(500, "x", 1) is None
    assert db.episode_air_date(None, 1, 1) is None
