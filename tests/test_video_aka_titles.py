"""Per-show "also known as" — the deterministic fix for release-title mismatches.

TMDB's alias coverage is patchy, most visibly for anime: a show is released by
fansub groups under a translation of its ORIGINAL title while TMDB lists a
different localised name, and there is no automatic bridge between the two. The
reported case:

    release: [SubsPlease] Tenkosaki: The Neat and Pretty Girl at My New School
             Is a Childhood Friend of Mine Who I Thought Was a Boy - 03
    wanted:  Oh Boy, Was I Wrong About Her

1.6.10 widened the automatic alias sources (manual search gained the alias set
at all; original titles joined it). Neither is guaranteed to cover a given show,
because both depend on what TMDB happens to hold. This is the override that
does not: the user types the name releases actually use, once.

Local only — deliberately NOT part of /metadata, which pushes edits to
Plex/Jellyfin and locks the field there. The media server has no concept of
this; it only widens what the release-title gate accepts.
"""

from __future__ import annotations

import pytest
from flask import Flask, g

from core.video.release_parse import titles_match
from database.video_database import VideoDatabase

_TENKOSAKI = ("[SubsPlease] Tenkosaki: The Neat and Pretty Girl at My New School Is a "
              "Childhood Friend of Mine Who I Thought Was a Boy - 03 [Web][MKV][h264]"
              "[1080p][AAC 2.0][Softsubs (SubsPlease)][Episode 3]")
_TMDB_NAME = "Oh Boy, Was I Wrong About Her"
_AKA = ("Tenkosaki: The Neat and Pretty Girl at My New School Is a Childhood Friend "
        "of Mine Who I Thought Was a Boy")


@pytest.fixture()
def app_db(tmp_path, monkeypatch):
    import api.video as videoapi
    import core.video.sources as sources
    monkeypatch.setattr(sources, "resolve_video_server", lambda *a, **k: "plex")
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


def _show(db, tmdb_id=500, title=_TMDB_NAME, server_id="s1"):
    return db.upsert_show_tree("plex", {"server_id": server_id, "tmdb_id": tmdb_id,
                                        "title": title})


# ── the reported case, end to end ────────────────────────────────────────────
def test_the_override_makes_the_reported_release_match(app_db, monkeypatch):
    """The whole point: no TMDB involvement, no guessing — it just matches."""
    client, db, _ = app_db
    show_id = _show(db)
    assert titles_match(_TENKOSAKI, _TMDB_NAME) is False        # before

    r = client.put("/api/video/detail/show/%d/aka" % show_id, json={"titles": _AKA})
    assert r.status_code == 200

    # no TMDB configured — the alias set is the user's AKA alone
    monkeypatch.setattr("core.video.enrichment.engine.get_video_enrichment_engine",
                        lambda: (_ for _ in ()).throw(RuntimeError("no TMDB")))
    from api.video.downloads import _want_titles
    wanted = _want_titles(db, {"scope": "episode", "title": _TMDB_NAME,
                               "media_id": show_id, "media_source": "library"})
    assert _AKA in wanted
    assert titles_match(_TENKOSAKI, wanted) is True             # after


def test_the_automated_drain_sees_it_too(app_db, monkeypatch):
    """Both alias resolvers must fold it in — fixing only the manual path would
    leave the hourly drain still rejecting the release."""
    _, db, _ = app_db
    _show(db)
    db.set_aka_titles("show", 1, _AKA)
    monkeypatch.setattr("core.video.enrichment.engine.get_video_enrichment_engine",
                        lambda: (_ for _ in ()).throw(RuntimeError("no TMDB")))
    import core.automation.handlers.video_process_wishlist as vpw
    titles = vpw._acceptable_titles(_TMDB_NAME, "show", 500)
    assert titles[0] == _TMDB_NAME          # primary stays first (it's the display name)
    assert _AKA in titles
    assert titles_match(_TENKOSAKI, titles) is True


# ── storage ──────────────────────────────────────────────────────────────────
def test_titles_are_cleaned_and_deduped(app_db):
    _, db, _ = app_db
    _show(db)
    stored = db.set_aka_titles("show", 1, "  One \n\n Two \n one \n, Three,, ")
    assert stored == ["One", "Two", "Three"]        # trimmed, blanks + dupes dropped
    assert db.aka_titles("show", 1) == ["One", "Two", "Three"]


def test_a_list_and_a_string_are_equivalent(app_db):
    _, db, _ = app_db
    _show(db)
    assert db.set_aka_titles("show", 1, ["A", "B"]) == ["A", "B"]
    assert db.set_aka_titles("show", 1, "A\nB") == ["A", "B"]


def test_clearing_removes_every_alias(app_db):
    _, db, _ = app_db
    _show(db)
    db.set_aka_titles("show", 1, "Something")
    assert db.set_aka_titles("show", 1, "") == []
    assert db.aka_titles("show", 1) == []
    assert db.aka_titles_for_tmdb("show", 500) == []


def test_akas_union_across_rows_for_one_tmdb_id(app_db):
    """The same show mirrored on two servers is two rows; an AKA typed on either
    has to count, or which row you happened to open decides whether it works."""
    _, db, _ = app_db
    a = _show(db, server_id="s1")
    b = _show(db, server_id="s2")
    assert a != b
    db.set_aka_titles("show", a, "From Row A")
    db.set_aka_titles("show", b, "From Row B")
    got = db.aka_titles_for_tmdb("show", 500)
    assert "From Row A" in got and "From Row B" in got


def test_lookups_are_defensive(app_db):
    _, db, _ = app_db
    assert db.set_aka_titles("show", 999999, "x") is None    # unknown row
    assert db.set_aka_titles("nonsense", 1, "x") is None
    assert db.aka_titles("show", None) == []
    assert db.aka_titles_for_tmdb("show", None) == []
    assert db.aka_titles_for_tmdb("nonsense", 1) == []


def test_movies_have_it_too(app_db):
    """Foreign films hit the same problem as anime."""
    _, db, _ = app_db
    mid = db.upsert_movie("plex", {"server_id": "m1", "tmdb_id": 77, "title": "The Movie"})
    assert db.set_aka_titles("movie", mid, "Le Film") == ["Le Film"]
    assert db.aka_titles_for_tmdb("movie", 77) == ["Le Film"]


# ── API ──────────────────────────────────────────────────────────────────────
def test_the_endpoint_round_trips_and_reports_what_was_stored(app_db):
    client, db, _ = app_db
    show_id = _show(db)
    body = client.put("/api/video/detail/show/%d/aka" % show_id,
                      json={"titles": " Alpha \n Alpha \n Beta "}).get_json()
    assert body["ok"] is True and body["aka_titles"] == ["Alpha", "Beta"]
    # ...and the detail payload carries it back for the panel to render
    assert db.show_detail(show_id)["aka_titles"] == ["Alpha", "Beta"]


def test_unknown_item_and_bad_kind_are_rejected(app_db):
    client, _, _ = app_db
    assert client.put("/api/video/detail/show/999999/aka", json={"titles": "x"}).status_code == 404
    assert client.put("/api/video/detail/banana/1/aka", json={"titles": "x"}).status_code == 400


def test_editing_aka_titles_is_admin_only(app_db):
    """Per-title management, like the quality profile and series type beside it."""
    client, db, persona = app_db
    show_id = _show(db)
    persona.update({"profile_id": 5, "is_admin": False, "can_download": False})
    assert client.put("/api/video/detail/show/%d/aka" % show_id,
                      json={"titles": "sneaky"}).status_code == 403
    assert db.aka_titles("show", show_id) == []


# ── frontend contract ────────────────────────────────────────────────────────
def test_the_manage_panel_renders_and_saves_the_field():
    from pathlib import Path
    js = (Path(__file__).resolve().parent.parent / "webui" / "static" / "video"
          / "video-manage-panel.js").read_text(encoding="utf-8")
    assert "data-vmg-aka" in js and "data-vmg-aka-save" in js
    assert "'/aka'" in js or "/aka'" in js
    assert "saveAkaTitles" in js
    # echoes back the STORED list, so the box shows the truth after cleanup
    assert "d.aka_titles || []" in js
