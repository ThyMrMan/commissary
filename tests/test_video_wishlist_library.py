"""Direct a WISHED title at a specific Library, before it exists on disk.

Reported as: "currently everything ends up in All Movies or All TV. No way to
direct it to be in the correct library."

The multi-library work already resolves a grab's destination per item — but only
from the movies/shows row's ``root_folder_id``, which a title that isn't in the
library yet doesn't have. So every unattended grab for a wished title fell back
to the primary Library for its kind, and there was nowhere to say otherwise. The
choice now lives on the wishlist row the drain actually reads.

The interesting cases are the refusals and the roll-up: a Library of the wrong
kind must not be accepted (every future grab would go to the wrong tree,
silently), and a show whose episodes disagree must not report one of them as
"the" Library — a picker that looks unchanged would then move the rest.
"""

from __future__ import annotations

import pytest
from flask import Flask, g

from database.video_database import VideoDatabase


@pytest.fixture()
def db(tmp_path):
    d = VideoDatabase(database_path=str(tmp_path / "video_library.db"))
    d.save_libraries("plex",
                     [{"server_title": "Movies", "label": "Movies", "path": "/m/std"},
                      {"server_title": "Anime Films", "label": "Anime Films", "path": "/m/anime"}],
                     [{"server_title": "TV", "label": "TV Shows", "path": "/tv/std"},
                      {"server_title": "Anime", "label": "Anime", "path": "/tv/anime"}])
    return d


@pytest.fixture()
def libs(db):
    """{'movies': [id, id], 'tv': [id, id]} in configured order."""
    cur = db.list_libraries("plex")
    return {k: [r["id"] for r in v] for k, v in cur.items()}


@pytest.fixture()
def client(db, monkeypatch):
    import api.video as videoapi
    import core.video.sources as sources
    monkeypatch.setattr(sources, "resolve_video_server", lambda *a, **k: "plex")
    videoapi._video_db = db
    app = Flask(__name__)
    app.register_blueprint(videoapi.create_video_blueprint(), url_prefix="/api/video")

    @app.before_request
    def _p():
        g.profile_id = 1; g.is_admin = True; g.can_download = True; g.allowed_sides = "both"

    try:
        yield app.test_client()
    finally:
        videoapi._video_db = None


def _wish_movie(db, tmdb_id=550, **kw):
    db.add_movie_to_wishlist(tmdb_id=tmdb_id, title="Fight Club", year=1999, **kw)


def _wish_eps(db, tmdb_id=30984, seasons=((1, 1), (1, 2)), **kw):
    db.add_episodes_to_wishlist(
        tmdb_id, "Bleach",
        [{"season_number": s, "episode_number": e} for s, e in seasons], **kw)


def _movie_item(db, tmdb_id=550):
    for it in db.query_wishlist("movie")["items"]:
        if it["tmdb_id"] == tmdb_id:
            return it
    return None


def _show_item(db, tmdb_id=30984):
    for it in db.query_wishlist("show")["items"]:
        if it["tmdb_id"] == tmdb_id:
            return it
    return None


# ── the reported gap ─────────────────────────────────────────────────────────
def test_a_wished_movie_can_be_pointed_at_a_library(db, libs):
    _wish_movie(db)
    assert _movie_item(db)["root_folder_id"] is None      # the old behaviour: primary
    assert db.set_wishlist_root_folder("movie", 550, libs["movies"][1]) == 1
    assert _movie_item(db)["root_folder_id"] == libs["movies"][1]


def test_every_wished_episode_of_a_show_moves_together(db, libs):
    _wish_eps(db, seasons=((1, 1), (1, 2), (2, 1)))
    assert db.set_wishlist_root_folder("show", 30984, libs["tv"][1]) == 3
    assert _show_item(db)["root_folder_id"] == libs["tv"][1]


def test_the_drain_grabs_into_the_chosen_library(db, libs):
    """The whole point — what the unattended path resolves as the destination."""
    from core.automation.handlers.video_process_wishlist import _item_target_dir
    _wish_eps(db)
    db.set_wishlist_root_folder("show", 30984, libs["tv"][1])
    rows = db.episode_wishlist_to_download()
    assert rows and all(r["root_folder_id"] == libs["tv"][1] for r in rows)

    import api.video as videoapi
    videoapi._video_db = db
    try:
        assert _item_target_dir(rows[0], "/tv/std") == "/tv/anime"
    finally:
        videoapi._video_db = None


def test_a_movie_grab_resolves_the_chosen_library_too(db, libs):
    _wish_movie(db)
    db.set_wishlist_root_folder("movie", 550, libs["movies"][1])
    rows = db.movie_wishlist_to_download()
    assert rows and rows[0]["root_folder_id"] == libs["movies"][1]


# ── refusals ─────────────────────────────────────────────────────────────────
def test_a_library_of_the_wrong_kind_is_refused(db, libs):
    """A movie filed under a TV Library sends every future grab to the wrong
    tree, and nothing would ever say so."""
    _wish_movie(db)
    assert db.set_wishlist_root_folder("movie", 550, libs["tv"][0]) == 0
    assert _movie_item(db)["root_folder_id"] is None


def test_an_unknown_library_is_refused(db):
    _wish_movie(db)
    assert db.set_wishlist_root_folder("movie", 550, 99999) == 0
    assert _movie_item(db)["root_folder_id"] is None


def test_junk_input_is_refused(db, libs):
    _wish_movie(db)
    assert db.set_wishlist_root_folder("movie", 550, "not-a-number") == 0
    assert db.set_wishlist_root_folder("nonsense", 550, libs["movies"][0]) == 0
    assert db.set_wishlist_root_folder("movie", None, libs["movies"][0]) == 0
    assert _movie_item(db)["root_folder_id"] is None


# ── clearing back to the default ─────────────────────────────────────────────
def test_clearing_returns_the_item_to_the_default(db, libs):
    _wish_movie(db)
    db.set_wishlist_root_folder("movie", 550, libs["movies"][1])
    assert db.set_wishlist_root_folder("movie", 550, None) == 1
    assert _movie_item(db)["root_folder_id"] is None


def test_clearing_does_not_unassign_the_library_row(db, libs):
    """'Default' means 'inherit from the library row'. Wiping that row too would
    make choosing Default silently unassign a title nobody asked to touch — and
    the item would still read as assigned, from the row it just cleared."""
    mid = db.upsert_movie("plex", {"server_id": "m1", "tmdb_id": 550, "title": "Fight Club"})
    db.set_item_root_folder("movie", mid, libs["movies"][0])
    _wish_movie(db, library_id=mid)
    assert db.set_wishlist_root_folder("movie", 550, None) == 1
    assert db.root_folder_id_for_library_row("movie", mid) == libs["movies"][0]
    assert _movie_item(db)["root_folder_id"] == libs["movies"][0]   # inherited, not blank


def test_choosing_a_library_moves_the_library_row_with_it(db, libs):
    """A title already in the library must not end up split — new episodes in
    one Library, the ones it already has in another."""
    sid = db.upsert_show_tree("plex", {"server_id": "s1", "tmdb_id": 30984,
                                       "title": "Bleach", "seasons": []})
    db.set_item_root_folder("show", sid, libs["tv"][0])
    _wish_eps(db, library_id=sid)
    db.set_wishlist_root_folder("show", 30984, libs["tv"][1])
    assert db.root_folder_id_for_library_row("show", sid) == libs["tv"][1]


# ── the show roll-up ─────────────────────────────────────────────────────────
def test_a_show_whose_episodes_disagree_reports_no_library(db, libs):
    """Rows can disagree (some predate the assignment). Reporting one of them as
    'the' Library would let a picker that looks unchanged move the rest."""
    _wish_eps(db, seasons=((1, 1), (1, 2)))
    conn = db._get_connection()
    conn.execute("UPDATE video_wishlist SET root_folder_id=? "
                 "WHERE kind='episode' AND episode_number=1", (libs["tv"][1],))
    conn.commit(); conn.close()
    assert _show_item(db)["root_folder_id"] is None


def test_a_wished_show_inherits_its_library_row(db, libs):
    """No override set, but the show IS in the library — the picker should show
    where it already lives, not 'Default'."""
    sid = db.upsert_show_tree("plex", {"server_id": "s1", "tmdb_id": 30984,
                                       "title": "Bleach", "seasons": []})
    db.set_item_root_folder("show", sid, libs["tv"][1])
    _wish_eps(db, library_id=sid)
    assert _show_item(db)["root_folder_id"] == libs["tv"][1]


# ── the endpoint ─────────────────────────────────────────────────────────────
def test_the_endpoint_sets_and_clears(client, db, libs):
    _wish_movie(db)
    r = client.put("/api/video/wishlist/library",
                   json={"kind": "movie", "tmdb_id": 550, "root_folder_id": libs["movies"][1]})
    assert r.status_code == 200 and r.get_json()["updated"] == 1
    assert _movie_item(db)["root_folder_id"] == libs["movies"][1]
    r = client.put("/api/video/wishlist/library",
                   json={"kind": "movie", "tmdb_id": 550, "root_folder_id": None})
    assert r.status_code == 200 and r.get_json()["success"] is True
    assert _movie_item(db)["root_folder_id"] is None


def test_the_endpoint_rejects_a_wrong_kind_library(client, db, libs):
    _wish_movie(db)
    r = client.put("/api/video/wishlist/library",
                   json={"kind": "movie", "tmdb_id": 550, "root_folder_id": libs["tv"][0]})
    assert r.status_code == 400
    assert _movie_item(db)["root_folder_id"] is None


def test_the_endpoint_needs_a_kind_and_a_title(client, libs):
    assert client.put("/api/video/wishlist/library",
                      json={"tmdb_id": 550}).status_code == 400
    assert client.put("/api/video/wishlist/library",
                      json={"kind": "episode", "tmdb_id": 550}).status_code == 400
    assert client.put("/api/video/wishlist/library", json={"kind": "movie"}).status_code == 400


def test_a_title_no_longer_wished_is_not_an_error(client, libs):
    """Nothing to update isn't a failure — and must NOT be reported as 'no such
    Library', which is a different problem entirely."""
    r = client.put("/api/video/wishlist/library",
                   json={"kind": "movie", "tmdb_id": 550, "root_folder_id": libs["movies"][0]})
    assert r.status_code == 200 and r.get_json() == {"success": True, "updated": 0}


def test_setting_the_library_is_admin_only(client, db, libs):
    _wish_movie(db)

    @client.application.before_request
    def _member():
        g.profile_id = 7; g.is_admin = False; g.can_download = True; g.allowed_sides = "both"

    assert client.put("/api/video/wishlist/library",
                      json={"kind": "movie", "tmdb_id": 550,
                            "root_folder_id": libs["movies"][1]}).status_code == 403
    assert _movie_item(db)["root_folder_id"] is None


# ── frontend source guards (this repo has no JS runner) ──────────────────────
def _read(rel):
    import pathlib
    return (pathlib.Path(__file__).resolve().parents[1] / rel).read_text(encoding="utf-8")


def test_both_card_types_render_a_library_slot():
    js = _read("webui/static/video/video-wishlist.js")
    assert "libSlot('movie', it.tmdb_id, it.root_folder_id)" in js
    assert "libSlot('show', sh.tmdb_id, sh.root_folder_id)" in js


def test_the_picker_is_painted_from_both_ends_of_the_registry_race():
    """Cards can render before /libraries answers, or after. Filling the slots
    from only one of the two leaves an empty picker whenever the other wins."""
    js = _read("webui/static/video/video-wishlist.js")
    assert js.count("paintLibPickers(") >= 3          # definition + render + libraries load
    assert "paintLibPickers(grid);" in js


def test_the_picker_saves_through_the_endpoint():
    js = _read("webui/static/video/video-wishlist.js")
    assert "'/api/video/wishlist/library'" in js
    assert "method: 'PUT'" in js


def test_a_refused_change_does_not_stay_on_screen():
    js = _read("webui/static/video/video-wishlist.js")
    assert js.count("sel.value = was;") == 2          # server said no, and network failure


def test_using_the_picker_does_not_open_the_title():
    js = _read("webui/static/video/video-wishlist.js")
    assert "if (e.target.closest('[data-vwsh-lib-slot]')) { e.stopPropagation(); return; }" in js


def test_an_empty_slot_leaves_the_card_unchanged():
    assert ".vwsh-lib:empty { display: none; }" in _read("webui/static/video/video-side.css")
