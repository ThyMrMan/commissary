"""Manual / failed-import resolution endpoints — list the unplaced queue, force-place
a file to a chosen identity, and dismiss. Uses real temp files (the place flow runs
the real importer against disk)."""

from __future__ import annotations

import os

import pytest
from flask import Flask

import api.video as videoapi
from core.video import organization
from database.video_database import VideoDatabase


@pytest.fixture()
def env(tmp_path):
    db = VideoDatabase(database_path=str(tmp_path / "video_library.db"))
    movies = tmp_path / "Movies"
    movies.mkdir()
    db.set_setting("movies_path", str(movies))
    db.set_setting("tv_path", str(tmp_path / "TV"))
    organization.save(db, {"verify_with_ffprobe": False})   # don't depend on ffprobe in CI

    dl_dir = tmp_path / "dl"
    dl_dir.mkdir()
    src = dl_dir / "the.matrix.1999.1080p.bluray.x265.mkv"
    src.write_bytes(b"x" * 4096)

    dl_id = db.add_video_download({
        "kind": "movie", "title": "the matrix", "release_title": src.name,
        "source": "soulseek", "username": "neo", "filename": src.name,
        "size_bytes": 4096, "target_dir": str(movies), "status": "import_failed",
        "search_ctx": "{}",
    })
    db.update_video_download(dl_id, dest_path=str(src), error="Looks like a sample, not the feature")

    videoapi._video_db = db
    app = Flask(__name__)
    app.register_blueprint(videoapi.create_video_blueprint(), url_prefix="/api/video")
    client = app.test_client()
    try:
        yield {"client": client, "db": db, "dl_id": dl_id, "src": src, "movies": movies}
    finally:
        videoapi._video_db = None


def test_failed_list_surfaces_unplaced_downloads(env):
    items = env["client"].get("/api/video/import/failed").get_json()["items"]
    assert len(items) == 1
    it = items[0]
    assert it["id"] == env["dl_id"]
    assert it["file"] == str(env["src"])               # points at the unplaced file
    assert "sample" in it["reason"].lower()


def test_place_force_imports_to_chosen_identity(env):
    r = env["client"].post("/api/video/import/%d/place" % env["dl_id"],
                           json={"scope": "movie", "title": "The Matrix", "year": 1999}).get_json()
    assert r["success"] and r["status"] == "completed"
    final = env["movies"] / "The Matrix (1999)" / "The Matrix (1999) Bluray-1080p.mkv"
    assert final.exists()                              # filed under the standard layout
    assert not env["src"].exists()                     # source reclaimed (copy mode, non-torrent)
    # the row is no longer in the failed queue
    assert env["client"].get("/api/video/import/failed").get_json()["items"] == []


def test_place_triggers_a_library_refresh(env):
    # a successful manual place fires the same batch-complete refresh the auto path
    # uses, so the title shows up without waiting for a scheduled scan.
    from core.video import download_events
    fired = []
    download_events.register_event_forwarder(lambda t, d: fired.append(d))
    try:
        env["client"].post("/api/video/import/%d/place" % env["dl_id"],
                           json={"scope": "movie", "title": "The Matrix", "year": 1999})
        assert fired and fired[-1].get("manual") is True
    finally:
        download_events._reset_for_tests()


# ── /import/add: manual placement isn't gated on a prior failed download ──────

def test_add_queues_an_arbitrary_file_with_no_prior_download(env, tmp_path):
    stray = tmp_path / "some.other.movie.2020.1080p.mkv"
    stray.write_bytes(b"y" * 2048)
    r = env["client"].post("/api/video/import/add", json={"path": str(stray)}).get_json()
    assert r["success"] and r["id"]
    items = env["client"].get("/api/video/import/failed").get_json()["items"]
    assert {i["file"] for i in items} == {str(env["src"]), str(stray)}
    added = next(i for i in items if i["file"] == str(stray))
    assert "manual" in added["reason"].lower() or "manual" in (added.get("source") or "")


def test_add_is_idempotent_for_the_same_path(env, tmp_path):
    stray = tmp_path / "dupe.2020.1080p.mkv"
    stray.write_bytes(b"z" * 1024)
    r1 = env["client"].post("/api/video/import/add", json={"path": str(stray)}).get_json()
    r2 = env["client"].post("/api/video/import/add", json={"path": str(stray)}).get_json()
    assert r1["id"] == r2["id"] and r2.get("already") is True
    items = env["client"].get("/api/video/import/failed").get_json()["items"]
    assert len([i for i in items if i["file"] == str(stray)]) == 1


def test_add_rejects_missing_or_non_video_paths(env, tmp_path):
    r = env["client"].post("/api/video/import/add", json={"path": str(tmp_path / "nope.mkv")})
    assert r.status_code == 404
    txt = tmp_path / "notes.txt"
    txt.write_text("hi")
    r2 = env["client"].post("/api/video/import/add", json={"path": str(txt)})
    assert r2.status_code == 400
    r3 = env["client"].post("/api/video/import/add", json={})
    assert r3.status_code == 400


def test_added_file_places_the_same_way_and_never_deletes_the_users_original(env, tmp_path):
    stray = tmp_path / "manual.pickup.2020.1080p.mkv"
    stray.write_bytes(b"w" * 8192)
    r = env["client"].post("/api/video/import/add", json={"path": str(stray)}).get_json()
    place = env["client"].post("/api/video/import/%d/place" % r["id"],
                               json={"scope": "movie", "title": "Manual Pickup", "year": 2020}).get_json()
    assert place["success"] and place["status"] == "completed"
    final_dir = env["movies"] / "Manual Pickup (2020)"
    assert final_dir.is_dir() and any(final_dir.iterdir())
    assert stray.exists()          # the user's own file — copy mode must not reclaim it


# ── /import/add guesses the kind; /place honours a chosen Library ─────────────
# Everything added by hand used to be filed as kind='movie', so the Place dialog
# opened on the Movie tab and — with no Library picker at all — an episode from a
# separate Anime library landed in the primary MOVIE destination.

@pytest.mark.parametrize("name, scope", [
    ("Severance.S02E03.1080p.WEB.x264.mkv", "episode"),
    ("Some.Show.Season.2.1080p.mkv", "episode"),
    ("[SubsPlease] Digimon Beatbreak - 40 [1080p][AAC].mkv", "episode"),   # fansub absolute numbering
    ("The.Matrix.1999.1080p.BluRay.x265.mkv", "movie"),
    ("Blade Runner 2049 (2017) 2160p.mkv", "movie"),
])
def test_add_guesses_movie_vs_episode_from_the_filename(env, tmp_path, name, scope):
    f = tmp_path / name
    f.write_bytes(b"q" * 1024)
    r = env["client"].post("/api/video/import/add", json={"path": str(f)}).get_json()
    item = next(i for i in env["client"].get("/api/video/import/failed").get_json()["items"]
                if i["id"] == r["id"])
    assert item["scope"] == scope
    assert item["kind"] == ("show" if scope == "episode" else "movie")


def test_fansub_absolute_numbering_keeps_the_episode_but_leaves_season_blank(env, tmp_path):
    """'[SubsPlease] Show - 40' carries no season at all — guessing one would be
    a lie, so the user still fills it in; only the TAB is pre-picked."""
    f = tmp_path / "[Erai-raws] Some Anime - 12 [1080p][Multiple Subtitle].mkv"
    f.write_bytes(b"q" * 1024)
    r = env["client"].post("/api/video/import/add", json={"path": str(f)}).get_json()
    item = next(i for i in env["client"].get("/api/video/import/failed").get_json()["items"]
                if i["id"] == r["id"])
    assert item["scope"] == "episode"
    assert item["episode"] == 12
    assert item["season"] is None


def test_place_files_into_the_chosen_library_not_the_primary(env, tmp_path):
    """The reported failure: a show belonging to a separate Anime library was
    placed into the primary destination because nothing could say otherwise."""
    db = env["db"]
    anime = tmp_path / "Anime"
    anime.mkdir()
    standard = tmp_path / "TVStandard"
    standard.mkdir()
    conn = db._get_connection()
    conn.execute("INSERT INTO root_folders (path, content_kind, server, sort_order) VALUES (?,?,?,?)",
                 (str(standard), "show", "plex", 0))
    conn.execute("INSERT INTO root_folders (path, content_kind, server, sort_order) VALUES (?,?,?,?)",
                 (str(anime), "show", "plex", 1))
    anime_id = conn.execute("SELECT id FROM root_folders WHERE path=?", (str(anime),)).fetchone()[0]
    conn.commit(); conn.close()

    f = tmp_path / "Anime.Show.S01E05.1080p.WEB.x264.mkv"
    f.write_bytes(b"a" * 4096)
    added = env["client"].post("/api/video/import/add", json={"path": str(f)}).get_json()
    r = env["client"].post("/api/video/import/%d/place" % added["id"],
                           json={"scope": "episode", "title": "Anime Show", "year": 2024,
                                 "season": 1, "episode": 5,
                                 "root_folder_id": anime_id}).get_json()
    assert r["success"] and r["status"] == "completed"
    assert any(anime.rglob("*.mkv"))          # landed in the Anime library
    assert not any(standard.rglob("*.mkv"))   # NOT the primary show library


def test_media_ids_resolves_tmdb_and_library_regrabs():
    from core.video.download_monitor import _media_ids
    # grabbed straight from TMDB → media_id is the tmdb id
    assert _media_ids(None, {"media_source": "tmdb", "media_id": "603"}) == (603, None)

    # owned re-grab → media_id is the LIBRARY id; resolve via the library row
    class _DB:
        def media_tmdb_id(self, kind, mid):
            assert kind == "movie" and mid == "5107"
            return (936075, "tt11378946")
    assert _media_ids(_DB(), {"media_source": "library", "media_id": "5107", "kind": "movie"}) \
        == (936075, "tt11378946")

    assert _media_ids(None, {}) == (None, None)            # unresolvable → no sidecars


def test_dismiss_does_not_trigger_a_refresh(env):
    from core.video import download_events
    fired = []
    download_events.register_event_forwarder(lambda t, d: fired.append(d))
    try:
        env["client"].post("/api/video/import/%d/dismiss" % env["dl_id"], json={})
        assert fired == []                             # nothing landed → no scan
    finally:
        download_events._reset_for_tests()


def test_place_rejects_bad_scope(env):
    """'season' used to stand in for an unknown scope here. It is now a real one
    (whole-folder import), so the refusal has to be tested with something that
    genuinely isn't a scope."""
    for bad in ("", "series", "album", None):
        r = env["client"].post("/api/video/import/%d/place" % env["dl_id"], json={"scope": bad})
        assert r.status_code == 400, bad


def test_place_as_a_season_needs_an_actual_folder(env):
    """The row here points at a single FILE. Asking to import it as a pack is a
    different failure from an unknown scope, and must not be silently treated as
    one — run_season_import would list a file's 'contents' and find nothing."""
    r = env["client"].post("/api/video/import/%d/place" % env["dl_id"],
                           json={"scope": "season", "title": "X", "media_id": 1})
    assert r.status_code == 410
    assert "folder" in (r.get_json().get("error") or "").lower()


def test_dismiss_drops_row_and_can_delete_file(env):
    r = env["client"].post("/api/video/import/%d/dismiss" % env["dl_id"],
                           json={"delete_file": True}).get_json()
    assert r["success"]
    assert not env["src"].exists()                     # file removed
    assert env["client"].get("/api/video/import/failed").get_json()["items"] == []


def test_failed_view_carries_drawer_facts(env):
    """The card's expand drawer needs grab provenance + on-disk truth: quality,
    source/user, attempts, grabbed date, and the REAL file size (file_exists
    False + size None once the file vanishes — the drawer says so instead of
    a stale number)."""
    it = env["client"].get("/api/video/import/failed").get_json()["items"][0]
    for key in ("quality_label", "size_bytes", "source", "username", "attempts",
                "grabbed_at", "file_exists", "file_size", "poster_url"):
        assert key in it, key
    assert it["file_exists"] is True
    assert it["file_size"] == env["src"].stat().st_size

    import os
    os.remove(env["src"])
    it2 = env["client"].get("/api/video/import/failed").get_json()["items"][0]
    assert it2["file_exists"] is False and it2["file_size"] is None


# ── the Place dialog's identity picker reads the right kind field ─────────────

def test_search_results_carry_kind_not_media_type():
    """/api/video/search returns {kind:'movie'|'show'|'person'} — no media_type,
    no type, no first_air_date. The picker's normResults() must key off `kind`;
    reading the others made every row fall through to the 'movie' default, so a
    show could never appear under the Episode tab."""
    from pathlib import Path
    src = (Path(__file__).resolve().parent.parent / "core" / "video" / "enrichment"
           / "clients.py").read_text(encoding="utf-8")
    # Anchored on the method NAME, not its full signature — search() gained a
    # `pages` argument when it went multi-page, and pinning the exact parameter
    # list made this fail on a change it has no opinion about.
    body = src[src.index("    def search(self, query"):]
    body = body[:body.index("\n    def ", 10)]
    assert '"kind": "movie"' in body and '"kind": "show"' in body
    assert '"media_type"' not in body.split("out.append")[1]   # not on the OUTPUT rows

    js = (Path(__file__).resolve().parent.parent / "webui" / "static" / "video"
          / "video-import.js").read_text(encoding="utf-8")
    norm = js[js.index("function normResults("):]
    norm = norm[:norm.index("\n    function ", 10)]
    assert "it.kind ||" in norm                       # kind is consulted FIRST
    assert norm.index("it.kind") < norm.index("it.media_type")
    assert "'show'" in norm                           # and 'show' is an accepted episode kind
