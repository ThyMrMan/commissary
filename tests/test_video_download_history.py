"""Permanent video download history — the archive that powers the History modal
and the smart post-download scan. video_downloads is the transient queue; this
table survives the cleanup, so it's snapshotted at terminal status."""

from __future__ import annotations

import json

import pytest

from database.video_database import VideoDatabase

from pathlib import Path as _P

_ROOT = _P(__file__).resolve().parent.parent
_VDH_JS = (_ROOT / "webui" / "static" / "video" / "video-download-history.js").read_text(encoding="utf-8")


@pytest.fixture()
def db(tmp_path):
    return VideoDatabase(database_path=str(tmp_path / "video_library.db"))


def _movie(**over):
    row = {"id": 1, "kind": "movie", "title": "Dune", "year": 2024, "status": "completed",
           "release_title": "Dune.2024.2160p.UHD.BluRay.x265-GRP", "source": "soulseek",
           "username": "bob", "filename": "Dune.2024.2160p.x265.mkv",
           "dest_path": "/movies/Dune (2024)/Dune (2024).mkv", "size_bytes": 9_000_000_000,
           "quality_label": "2160p", "media_id": "55", "media_source": "library",
           "poster_url": "/p/dune.jpg", "created_at": "2026-06-20 10:00:00",
           "completed_at": "2026-06-20 10:30:00"}
    row.update(over)
    return row


def _episode(**over):
    row = {"id": 2, "kind": "show", "title": "Severance", "year": 2025, "status": "completed",
           "release_title": "Severance.S02E05.1080p.WEB.h264", "source": "soulseek",
           "dest_path": "/tv/Severance/Season 02/Severance - S02E05.mkv", "size_bytes": 2_000_000_000,
           "search_ctx": json.dumps({"scope": "episode", "title": "Severance", "season": 2, "episode": 5}),
           "media_id": "9", "media_source": "library", "completed_at": "2026-06-21 02:00:00"}
    row.update(over)
    return row


def _youtube(**over):
    row = {"id": 3, "kind": "youtube", "title": "Some Video", "status": "completed",
           "source": "youtube", "media_source": "youtube", "username": None,
           "release_title": "Some Video", "completed_at": "2026-06-22 00:00:00"}
    row.update(over)
    return row


def test_history_tabs_classify_real_episode_and_youtube_kinds(db):
    # Production stores TV grabs as kind='episode' (not 'show') and YouTube as
    # source/kind='youtube'. The tabs must classify BOTH — the bug was the TV tab
    # filtering kind='show' and the counts summing only movie+show (TV + YT vanished).
    db.record_download_history(_movie(id=1))
    db.record_download_history(_episode(id=2, kind="episode"))
    db.record_download_history(_youtube(id=3))

    counts = db.download_history_counts()
    assert {k: counts[k] for k in ("movie", "show", "youtube", "total")} == {
        "movie": 1, "show": 1, "youtube": 1, "total": 3}
    assert counts["by_library"] == {}      # no Libraries configured in this test

    def kinds(tab):
        return sorted(r["kind"] for r in db.query_download_history(kind=tab)["items"])
    assert kinds("movie") == ["movie"]
    assert kinds("show") == ["episode"]      # TV tab now catches kind='episode'
    assert kinds("youtube") == ["youtube"]   # YouTube gets its own tab
    assert len(db.query_download_history(kind=None)["items"]) == 3   # 'All'


def _add_library(db, *, path, kind="show", category=None, server="plex"):
    conn = db._get_connection()
    cur = conn.execute(
        "INSERT INTO root_folders (path, content_kind, server, category) VALUES (?,?,?,?)",
        (str(path), kind, server, category))
    rid = cur.lastrowid
    conn.commit(); conn.close()
    return rid


def test_root_folder_id_filter_scopes_to_one_configured_library(db, tmp_path):
    # the reported gap: the History filter only knew Movies/TV/YouTube, never a
    # SPECIFIC configured Library (e.g. an Anime library that's also kind='show')
    anime_lib = _add_library(db, path=str(tmp_path / "anime"))
    tv_lib = _add_library(db, path=str(tmp_path / "tv"))
    db.record_download_history(_episode(id=2, kind="episode",
                                        dest_path=str(tmp_path / "anime" / "Show" / "S01E01.mkv")))
    db.record_download_history(_episode(id=3, kind="episode", title="Other Show",
                                        dest_path=str(tmp_path / "tv" / "Other" / "S01E01.mkv")))
    anime_items = db.query_download_history(root_folder_id=anime_lib)["items"]
    assert [i["title"] for i in anime_items] == ["Severance"]
    tv_items = db.query_download_history(root_folder_id=tv_lib)["items"]
    assert [i["title"] for i in tv_items] == ["Other Show"]


def test_root_folder_id_composes_with_kind_and_search(db, tmp_path):
    anime_lib = _add_library(db, path=str(tmp_path / "anime"))
    db.record_download_history(_movie(id=1, dest_path=str(tmp_path / "anime" / "movie.mkv")))
    db.record_download_history(_episode(id=2, kind="episode",
                                        dest_path=str(tmp_path / "anime" / "Show" / "S01E01.mkv")))
    scoped_shows = db.query_download_history(kind="show", root_folder_id=anime_lib)["items"]
    assert [i["kind"] for i in scoped_shows] == ["episode"]
    scoped_search = db.query_download_history(root_folder_id=anime_lib, search="Severance")["items"]
    assert len(scoped_search) == 1


def test_root_folder_id_for_unknown_library_matches_nothing(db, tmp_path):
    db.record_download_history(_movie())
    assert db.query_download_history(root_folder_id=999999)["items"] == []


def test_root_folder_id_none_is_a_no_op(db):
    db.record_download_history(_movie())
    db.record_download_history(_episode(kind="episode"))
    assert len(db.query_download_history(root_folder_id=None)["items"]) == 2


def test_api_passes_root_folder_id_through(tmp_path):
    import api.video as videoapi
    from flask import Flask
    db = VideoDatabase(database_path=str(tmp_path / "video_library.db"))
    anime_lib = _add_library(db, path=str(tmp_path / "anime"))
    db.record_download_history(_episode(dest_path=str(tmp_path / "anime" / "Show" / "S01E01.mkv")))
    db.record_download_history(_episode(id=3, title="Other", dest_path=str(tmp_path / "other" / "x.mkv")))
    videoapi._video_db = db
    try:
        app = Flask(__name__)
        app.register_blueprint(videoapi.create_video_blueprint(), url_prefix="/api/video")
        c = app.test_client()
        out = c.get("/api/video/downloads/history?root_folder_id=%d" % anime_lib).get_json()
        assert [i["title"] for i in out["items"]] == ["Severance"]
    finally:
        videoapi._video_db = None


def _func(name: str) -> str:
    i = _VDH_JS.index("function " + name + "(")
    nxt = _VDH_JS.find("\n    function ", i + 1)
    return _VDH_JS[i:nxt if nxt != -1 else len(_VDH_JS)]


def test_library_filter_lives_in_the_tab_strip_not_a_dropdown():
    """The filter sits next to the existing All/Movies/TV/YouTube tabs. The old
    standalone <select> is gone from the toolbar."""
    assert "vdh-lib-select" not in _VDH_JS
    body = _func("renderLibraryTabs")
    assert ".vdh-tabs" in body
    assert "data-vdh-lib-tab" in body
    assert "kind + ':' + l.id" in body


def test_a_library_tab_splits_into_kind_plus_root_folder_id():
    set_tab = _func("setTab")
    assert "state.rootFolderId = parts.length > 1 ? parts[1] : ''" in set_tab
    # the kind sent to the API is the bare kind, never 'show:3'
    load = _func("load")
    assert "params.set('kind', String(state.tab).split(':')[0])" in load
    assert "root_folder_id" in load


def test_libraries_come_from_the_configured_registry_so_members_see_them():
    """d.configured is readable by every profile; d.movies/d.tv are the
    admin-only server-section discovery list (empty for members, and carrying
    no root_folder id to filter by) — which is why this only worked for admins."""
    body = _func("loadLibraries")
    assert "/api/video/libraries" in body
    assert "d.configured" in body
    assert "d.movies" not in body and "d.tv" not in body


def test_records_a_completed_movie_with_parsed_quality(db):
    hid = db.record_download_history(_movie())
    assert hid > 0
    d = db.download_history_detail(hid)
    assert d["title"] == "Dune" and d["outcome"] == "completed" and d["media_type"] == "movie"
    assert d["resolution"] == "2160p" and d["video_codec"] == "x265"   # sniffed from the release name
    assert d["size_bytes"] == 9_000_000_000 and d["dest_path"].endswith("Dune (2024).mkv")


def test_records_an_episode_with_season_episode_from_search_ctx(db):
    hid = db.record_download_history(_episode())
    d = db.download_history_detail(hid)
    assert (d["kind"], d["media_type"]) == ("show", "show")
    assert d["season_number"] == 2 and d["episode_number"] == 5
    assert d["resolution"] == "1080p" and d["video_codec"] == "x264"


def test_history_is_idempotent_per_terminal_download(db):
    first = db.record_download_history(_movie())
    again = db.record_download_history(_movie())     # same download_id/outcome/dest_path
    assert first > 0 and again == 0                  # INSERT OR IGNORE → no dupe
    assert db.download_history_counts()["movie"] == 1


def test_query_filters_by_kind_and_search(db):
    db.record_download_history(_movie())
    db.record_download_history(_episode())
    assert db.query_download_history(kind="movie")["pagination"]["total_count"] == 1
    assert db.query_download_history(kind="show")["items"][0]["title"] == "Severance"
    hits = db.query_download_history(search="dune")["items"]
    assert len(hits) == 1 and hits[0]["title"] == "Dune"


def test_counts_only_count_completed(db):
    db.record_download_history(_movie())
    db.record_download_history(_movie(id=3, status="failed", dest_path=None,
                                      error="no release found"))
    c = db.download_history_counts()
    assert {k: c[k] for k in ("movie", "show", "youtube", "total")} == {
        "movie": 1, "show": 0, "youtube": 0, "total": 1}   # the failed one isn't counted


def test_latest_completed_download_is_the_probe_target(db):
    db.record_download_history(_movie(id=1, completed_at="2026-06-20 10:30:00"))
    db.record_download_history(_movie(id=4, title="Wicked",
                                      dest_path="/movies/Wicked (2024)/Wicked.mkv",
                                      completed_at="2026-06-22 09:00:00"))
    db.record_download_history(_episode())
    assert db.latest_completed_download("movie")["title"] == "Wicked"   # newest movie
    assert db.latest_completed_download("show")["title"] == "Severance"
    assert db.latest_completed_download("all")["title"] == "Wicked"     # newest overall


def test_newest_first_ordering_in_the_feed(db):
    db.record_download_history(_movie(id=1, title="Old", dest_path="/m/old.mkv",
                                      completed_at="2026-01-01 00:00:00"))
    db.record_download_history(_movie(id=2, title="New", dest_path="/m/new.mkv",
                                      completed_at="2026-06-01 00:00:00"))
    titles = [i["title"] for i in db.query_download_history()["items"]]
    assert titles == ["New", "Old"]


# ── per-Library tab badges (a new Library showed no count at all) ─────────────

def test_history_counts_break_down_per_library(db, tmp_path):
    anime = _add_library(db, path=str(tmp_path / "Anime"), kind="show")
    tv = _add_library(db, path=str(tmp_path / "TV"), kind="show")
    db.record_download_history(_episode(
        id=1, title="Anime Show", dest_path=str(tmp_path / "Anime" / "Anime Show" / "s01e01.mkv")))
    db.record_download_history(_episode(
        id=2, title="Other Show", dest_path=str(tmp_path / "TV" / "Other Show" / "s01e01.mkv")))
    by_lib = db.download_history_counts()["by_library"]
    assert by_lib[anime] == 1
    assert by_lib[tv] == 1


def test_history_count_badges_agree_with_the_filtered_list(db, tmp_path):
    """Badge and list share _history_library_clause, so they can't disagree."""
    anime = _add_library(db, path=str(tmp_path / "Anime"), kind="show")
    db.record_download_history(_episode(
        id=1, title="Anime Show", dest_path=str(tmp_path / "Anime" / "Anime Show" / "s01e01.mkv")))
    db.record_download_history(_movie(id=2, title="Elsewhere", dest_path="/somewhere/else/x.mkv"))
    assert db.download_history_counts()["by_library"][anime] == 1
    assert len(db.query_download_history(root_folder_id=anime)["items"]) == 1


def test_history_counts_for_a_library_with_no_path_are_zero_not_everything(db, tmp_path):
    """An unset Library path must match nothing — the same guard the filter has,
    so a blank path can't make a badge claim the whole history."""
    blank = _add_library(db, path="", kind="show")
    db.record_download_history(_movie(id=1, title="Anything", dest_path="/x/y.mkv"))
    assert db.download_history_counts()["by_library"][blank] == 0


def test_kind_tab_is_relabelled_when_its_libraries_are_listed_beside_it():
    body = _func("renderLibraryTabs")
    assert "KIND_ALL_LABEL" in body
    assert "'All Movies'" in _VDH_JS and "'All TV'" in _VDH_JS


def test_library_tabs_carry_a_count_badge():
    body = _func("renderLibraryTabs")
    assert "data-vdh-c-lib" in body
    assert "vdh-tab-n" in body
    counts = _func("setCounts")
    assert "by_library" in counts
    assert "data-vdh-c-lib" in counts
