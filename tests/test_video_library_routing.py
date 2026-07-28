"""Multi-library ROUTING: a grab lands in the Library its title belongs to.

The conversion from a hardcoded single Movies + single TV Shows library to the
root_folders registry left the *unattended* drain fully per-item aware
(``_item_target_dir`` / ``_category_for_item``) while the manual paths still
resolved only from an explicit UI pick. With none supplied they fell through to
``primary_root_folder()`` — the lowest sort_order Library for the kind — so an
episode of a show filed under Anime was handed the standard TV Library's
category and downloaded into its folder.

Reported symptom: an Anime episode landing in ``/media/completed/watching/tv-shows/``
instead of ``.../anime/``. The category is the whole story — every ``grab()`` call
passes ``save_path=None``, so the client's per-category save folder is the only
thing choosing the directory.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from database.video_database import VideoDatabase

_ROOT = Path(__file__).resolve().parent.parent
_GRAB_JS = (_ROOT / "webui" / "static" / "video" / "video-grab.js").read_text(encoding="utf-8")
_DETAIL_JS = (_ROOT / "webui" / "static" / "video" / "video-detail.js").read_text(encoding="utf-8")
_DLVIEW_JS = (_ROOT / "webui" / "static" / "video" / "video-download-view.js").read_text(encoding="utf-8")


@pytest.fixture()
def db(tmp_path):
    return VideoDatabase(database_path=str(tmp_path / "video_library.db"))


def _add_library(db, *, path, kind="show", category=None, server="plex",
                 sort_order=0, title=None):
    conn = db._get_connection()
    cur = conn.execute(
        "INSERT INTO root_folders (path, content_kind, server, server_title, category, sort_order) "
        "VALUES (?,?,?,?,?,?)",
        (str(path), kind, server, title or path, category, sort_order))
    rid = cur.lastrowid
    conn.commit(); conn.close()
    return rid


@pytest.fixture()
def two_show_libraries(db):
    """The reported setup: a primary 'TV Shows' Library and an 'Anime' one."""
    tv = _add_library(db, path="/media/tv", category="tv-shows", sort_order=0, title="TV Shows")
    anime = _add_library(db, path="/media/anime", category="anime", sort_order=1, title="Anime")
    anime_show = db.upsert_show_tree("plex", {"server_id": "s1", "tmdb_id": 500, "title": "Anime Show"})
    std_show = db.upsert_show_tree("plex", {"server_id": "s2", "tmdb_id": 600, "title": "Regular Show"})
    conn = db._get_connection()
    conn.execute("UPDATE shows SET root_folder_id=? WHERE id=?", (anime, anime_show))
    conn.execute("UPDATE shows SET root_folder_id=? WHERE id=?", (tv, std_show))
    conn.commit(); conn.close()
    return {"tv": tv, "anime": anime, "anime_show": anime_show, "std_show": std_show}


# ── the resolver behind every manual grab ────────────────────────────────────
def _resolve(db, body):
    from api.video.downloads import _root_folder_id_for_grab
    return _root_folder_id_for_grab(db, body)


def test_grab_without_a_pick_uses_the_shows_own_library(db, two_show_libraries):
    """The regression test. The inline per-episode button sends no root_folder_id;
    before the fix this fell straight through to the primary TV Library."""
    got = _resolve(db, {"kind": "show", "media_id": two_show_libraries["anime_show"],
                        "media_source": "library"})
    assert got == two_show_libraries["anime"]


def test_two_libraries_each_resolve_to_their_own(db, two_show_libraries):
    anime = _resolve(db, {"kind": "show", "media_id": two_show_libraries["anime_show"],
                          "media_source": "library"})
    std = _resolve(db, {"kind": "show", "media_id": two_show_libraries["std_show"],
                        "media_source": "library"})
    assert anime == two_show_libraries["anime"]
    assert std == two_show_libraries["tv"]
    assert anime != std


def test_an_explicit_pick_still_wins(db, two_show_libraries):
    """The user overriding the dropdown beats the inferred Library."""
    got = _resolve(db, {"kind": "show", "media_id": two_show_libraries["anime_show"],
                        "media_source": "library", "root_folder_id": two_show_libraries["tv"]})
    assert got == two_show_libraries["tv"]


def test_a_tmdb_sourced_grab_resolves_through_the_tmdb_id(db, two_show_libraries):
    got = _resolve(db, {"kind": "show", "media_id": 500, "media_source": "tmdb"})
    assert got == two_show_libraries["anime"]


def test_an_unowned_or_bare_payload_falls_back_to_the_primary(db, two_show_libraries):
    # nothing to infer from → None, leaving the caller's primary fallback in charge
    assert _resolve(db, {"kind": "show"}) is None
    assert _resolve(db, {"kind": "show", "media_id": 999999, "media_source": "library"}) is None
    assert _resolve(db, {}) is None


def test_movie_libraries_resolve_independently_of_shows(db):
    movies = _add_library(db, path="/media/movies", kind="movie", category="movies", sort_order=0)
    anime_films = _add_library(db, path="/media/anime-films", kind="movie",
                               category="anime-films", sort_order=1)
    mid = db.upsert_movie("plex", {"server_id": "m1", "tmdb_id": 77, "title": "Anime Film"})
    conn = db._get_connection()
    conn.execute("UPDATE movies SET root_folder_id=? WHERE id=?", (anime_films, mid))
    conn.commit(); conn.close()
    assert _resolve(db, {"kind": "movie", "media_id": mid, "media_source": "library"}) == anime_films
    # a show-kind lookup must not find a movie row of the same id
    assert _resolve(db, {"kind": "show", "media_id": mid, "media_source": "library"}) != anime_films
    assert movies != anime_films


def test_root_folder_id_for_library_row_is_defensive(db, two_show_libraries):
    assert db.root_folder_id_for_library_row("show", None) is None
    assert db.root_folder_id_for_library_row("show", "not-a-number") is None
    assert db.root_folder_id_for_library_row("nonsense", 1) is None


# ── the wishlist carries its own Library now ─────────────────────────────────
def test_wishlist_remembers_the_library_for_a_title_it_does_not_own_yet(db):
    """The structural gap: video_wishlist only knew a Library indirectly, via the
    owned movies/shows row. A brand-new Anime show has no such row, so it had no
    Library at all and drained into the primary."""
    tv = _add_library(db, path="/media/tv", category="tv-shows", sort_order=0)
    anime = _add_library(db, path="/media/anime", category="anime", sort_order=1)
    db.add_episodes_to_wishlist(
        700, "Brand New Anime", [{"season_number": 1, "episode_number": 1}],
        root_folder_id=anime)
    items = db.episode_wishlist_to_download()
    rows = [i for i in items if i.get("show_tmdb_id") == 700]
    assert rows and rows[0]["root_folder_id"] == anime
    assert rows[0]["root_folder_id"] != tv


def test_wishlist_library_falls_back_to_the_owned_row_when_unset(db):
    """Existing rows (added before the column) keep working — the owned title's
    own Library still answers when the wishlist row names none."""
    anime = _add_library(db, path="/media/anime", category="anime")
    show_id = db.upsert_show_tree("plex", {"server_id": "s1", "tmdb_id": 800, "title": "Owned Anime"})
    conn = db._get_connection()
    conn.execute("UPDATE shows SET root_folder_id=? WHERE id=?", (anime, show_id))
    conn.commit(); conn.close()
    db.add_episodes_to_wishlist(800, "Owned Anime", [{"season_number": 1, "episode_number": 1}],
                                library_id=show_id)
    rows = [i for i in db.episode_wishlist_to_download() if i.get("show_tmdb_id") == 800]
    assert rows and rows[0]["root_folder_id"] == anime


def test_wishlist_tab_filter_matches_the_explicit_library(db):
    """The tab badge and the list it labels must agree — both read the same
    COALESCE(explicit, owned-row) expression."""
    tv = _add_library(db, path="/media/tv", category="tv-shows", sort_order=0)
    anime = _add_library(db, path="/media/anime", category="anime", sort_order=1)
    db.add_episodes_to_wishlist(700, "Brand New Anime",
                                [{"season_number": 1, "episode_number": 1}], root_folder_id=anime)
    titles = [i["title"] for i in db.query_wishlist("show", root_folder_id=anime)["items"]]
    assert titles == ["Brand New Anime"]
    assert db.query_wishlist("show", root_folder_id=tv)["items"] == []
    assert db.wishlist_counts()["by_library"][anime] == 1
    assert db.wishlist_counts()["by_library"][tv] == 0


# ── saving Libraries must not delete the kinds it wasn't given ───────────────
def test_saving_movies_and_tv_leaves_youtube_libraries_alone(db):
    """The Libraries settings page posts movies+tv and omits youtube. Folding that
    None into an empty list deleted every YouTube root on every save, dropping it
    from health checks, recycle and path re-rooting (all_library_rows is
    kind-agnostic and does read those rows)."""
    yt = _add_library(db, path="/media/youtube", kind="youtube", title="YouTube")
    saved = db.save_libraries("plex", [{"server_title": "Movies", "path": "/media/movies"}],
                              [{"server_title": "TV", "path": "/media/tv"}])
    assert yt in {r["id"] for r in db.all_library_rows()}
    # ...but an explicitly supplied empty list still prunes, as before. Re-post the
    # saved rows BY ID so this is an update, not a duplicate insert (path is UNIQUE).
    db.save_libraries("plex", saved["movies"], saved["tv"], [])
    assert yt not in {r["id"] for r in db.all_library_rows()}


# ── library-page facets are scoped to the tab ────────────────────────────────
def test_genre_and_resolution_facets_scope_to_one_library(db):
    movies = _add_library(db, path="/media/movies", kind="movie", sort_order=0)
    anime = _add_library(db, path="/media/anime", kind="movie", sort_order=1)
    a = db.upsert_movie("plex", {"server_id": "m1", "tmdb_id": 1, "title": "A", "genres": ["Anime"]})
    b = db.upsert_movie("plex", {"server_id": "m2", "tmdb_id": 2, "title": "B", "genres": ["Western"]})
    conn = db._get_connection()
    conn.execute("UPDATE movies SET root_folder_id=? WHERE id=?", (anime, a))
    conn.execute("UPDATE movies SET root_folder_id=? WHERE id=?", (movies, b))
    conn.commit(); conn.close()
    assert db.library_genres("movies", root_folder_id=anime) == ["Anime"]
    assert db.library_genres("movies", root_folder_id=movies) == ["Western"]
    # unscoped keeps the old union behavior
    assert sorted(db.library_genres("movies")) == ["Anime", "Western"]


# ── frontend source contracts (no JS runner in this repo) ────────────────────
def test_inline_grab_payload_carries_the_library():
    """video-grab.js drives the detail page's per-episode/per-season buttons and
    sent no Library at all."""
    assert "root_folder_id: opts.rootFolderId" in _GRAB_JS

    # season() builds its OWN grab payload now (one pack, no per-episode fan-out),
    # so the Library must appear there rather than being passed down to episode().
    assert _GRAB_JS.count("root_folder_id: opts.rootFolderId") >= 2
    assert "rootFolderId: data.root_folder_id" in _DETAIL_JS  # both call sites feed it


def test_library_picker_preselects_instead_of_defaulting_to_the_primary():
    """Without a `selected` the browser picks option 0 — the lowest sort_order,
    i.e. the primary — and that then gets stamped onto the grab EXPLICITLY,
    overriding the backend's own fallback."""
    assert "function loadLibraryPicker(container, kind, rootFolderId)" in _DLVIEW_JS
    assert "' selected'" in _DLVIEW_JS
    assert "loadLibraryPicker(container, 'show', opts.rootFolderId)" in _DLVIEW_JS
    assert "loadLibraryPicker(container, 'movie', opts.rootFolderId)" in _DLVIEW_JS
