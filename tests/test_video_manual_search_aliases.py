"""The interactive search gates on the same alias set the automation does.

Reported: a SubsPlease release of "Tenkosaki: The Neat and Pretty Girl at My New
School..." rejected as WRONG TITLE against a wanted title of "Oh Boy, Was I
Wrong About Her". Those are two official English names for one show — no amount
of parsing bridges them, only an alias set can.

The alias machinery already existed and was already used by the unattended paths
(``video_process_wishlist``, ``rss_sync``, ``download_monitor`` all pass
``ctx['titles']``). The interactive search in ``api/video/downloads.py`` passed a
bare ``body['title']`` — so a release named by an AKA matched fine when the
hourly drain found it and was rejected when a human searched for it, which is
exactly backwards.

Second gap: the alias set came only from TMDB's /alternative_titles, which does
NOT include the ORIGINAL title. Fansub groups romanise from the original, so an
anime whose TMDB display name is a localised rewrite had no bridge at all.
"""

from __future__ import annotations

import pytest
from flask import Flask, g

from database.video_database import VideoDatabase

_TENKOSAKI = ("[SubsPlease] Tenkosaki: The Neat and Pretty Girl at My New School Is a "
              "Childhood Friend of Mine Who I Thought Was a Boy - 03 [Web][MKV][h264]"
              "[1080p][AAC 2.0][Softsubs (SubsPlease)][Episode 3]")
_TMDB_NAME = "Oh Boy, Was I Wrong About Her"
_ORIGINAL = "Tenkosaki: The Neat and Pretty Girl at My New School Is a Childhood Friend of Mine Who I Thought Was a Boy"


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


def _aliases(monkeypatch, titles):
    class _Eng:
        def alt_titles_for(self, kind, tmdb_id):
            return titles
    monkeypatch.setattr("core.video.enrichment.engine.get_video_enrichment_engine",
                        lambda: _Eng())


# ── the reported rejection ───────────────────────────────────────────────────
def test_the_two_titles_genuinely_do_not_match_without_aliases():
    """Pins WHY this needed aliases rather than another parser tweak — and that
    the gate was right to reject on the information it had."""
    from core.video.release_parse import titles_match
    assert titles_match(_TENKOSAKI, _TMDB_NAME) is False
    # the 1.6.6 colon rule can't help either: the head is one word
    assert titles_match(_TENKOSAKI, "Tenkosaki") is False


def test_the_release_matches_once_the_alias_set_carries_the_original():
    from core.video.release_parse import titles_match
    assert titles_match(_TENKOSAKI, [_TMDB_NAME, _ORIGINAL]) is True


# ── the plumbing gap ─────────────────────────────────────────────────────────
def test_manual_search_now_resolves_aliases_for_a_tmdb_title(app_db, monkeypatch):
    from api.video.downloads import _want_titles
    _aliases(monkeypatch, [_ORIGINAL])
    _, db = app_db
    got = _want_titles(db, {"scope": "episode", "title": _TMDB_NAME,
                            "media_id": 12345, "media_source": "tmdb"})
    assert got[0] == _TMDB_NAME          # primary stays first
    assert _ORIGINAL in got


def test_manual_search_resolves_aliases_from_a_library_row(app_db, monkeypatch):
    """The detail page sends a library row id, not a tmdb id."""
    _, db = app_db
    show_id = db.upsert_show_tree("plex", {"server_id": "s1", "tmdb_id": 999,
                                           "title": _TMDB_NAME})
    seen = {}

    class _Eng:
        def alt_titles_for(self, kind, tmdb_id):
            seen["tmdb_id"] = tmdb_id
            return [_ORIGINAL]
    monkeypatch.setattr("core.video.enrichment.engine.get_video_enrichment_engine",
                        lambda: _Eng())
    from api.video.downloads import _want_titles
    got = _want_titles(db, {"scope": "episode", "title": _TMDB_NAME,
                            "media_id": show_id, "media_source": "library"})
    assert seen["tmdb_id"] == 999        # walked row id -> tmdb id
    assert _ORIGINAL in got


def test_it_never_returns_less_than_before(app_db, monkeypatch):
    """Every failure mode must degrade to the bare primary title, never to none —
    an empty want_title would disable the gate entirely and accept anything."""
    from api.video.downloads import _want_titles
    _, db = app_db

    class _Boom:
        def alt_titles_for(self, kind, tmdb_id):
            raise RuntimeError("TMDB down")
    monkeypatch.setattr("core.video.enrichment.engine.get_video_enrichment_engine",
                        lambda: _Boom())
    assert _want_titles(db, {"title": "Solo Title", "media_id": 1, "media_source": "tmdb"}) \
        == ["Solo Title"]
    # no media id at all → primary only
    assert _want_titles(db, {"title": "Solo Title"}) == ["Solo Title"]
    # unknown library row → primary only
    assert _want_titles(db, {"title": "Solo Title", "media_id": 777,
                             "media_source": "library"}) == ["Solo Title"]


def test_a_bogus_media_id_does_not_raise(app_db):
    from api.video.downloads import _want_titles
    _, db = app_db
    assert _want_titles(db, {"title": "T", "media_id": "not-a-number",
                             "media_source": "library"}) == ["T"]


def test_every_search_endpoint_uses_the_alias_resolver():
    """Source guard: four call sites, and missing one leaves that entry point
    matching on the bare title."""
    from pathlib import Path
    src = (Path(__file__).resolve().parent.parent / "api" / "video"
           / "downloads.py").read_text(encoding="utf-8")
    body = src.split("def _want_titles", 1)[1]
    assert body.count("_want_titles(get_video_db()") == 4
    assert 'want_title=body.get("title")' not in body
    assert 'want_title=request.args.get("title")' not in body


# ── the alias set now includes the original title ────────────────────────────
def test_alternative_titles_folds_in_the_original(monkeypatch):
    from core.video.enrichment.clients import TMDBClient
    c = TMDBClient.__new__(TMDBClient)
    c.api_key = "k"

    class _R:
        status_code = 200
        def raise_for_status(self): pass
        def __init__(self, payload): self._p = payload
        def json(self): return self._p

    def fake_get(url, **kw):
        if url.endswith("/alternative_titles"):
            return _R({"results": [{"title": "Some AKA"}]})
        return _R({"name": _TMDB_NAME, "original_name": _ORIGINAL})

    monkeypatch.setattr("requests.get", fake_get)
    out = c.alternative_titles("show", 1)
    assert "Some AKA" in out
    assert _ORIGINAL in out              # the bridge the reported case needed
    assert len(out) == len(set(t.lower() for t in out)), "no duplicates"


def test_a_failed_original_lookup_still_returns_the_akas(monkeypatch):
    from core.video.enrichment.clients import TMDBClient
    c = TMDBClient.__new__(TMDBClient)
    c.api_key = "k"

    class _R:
        def raise_for_status(self): pass
        def json(self): return {"results": [{"title": "Some AKA"}]}

    def fake_get(url, **kw):
        if url.endswith("/alternative_titles"):
            return _R()
        raise RuntimeError("detail endpoint down")

    monkeypatch.setattr("requests.get", fake_get)
    assert c.alternative_titles("show", 1) == ["Some AKA"]
