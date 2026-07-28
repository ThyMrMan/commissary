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
    db.set_aka_titles("show", 500, _AKA)
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
    stored = db.set_aka_titles("show", 500, "  One \n\n Two \n one \n, Three,, ")
    assert stored == ["One", "Two", "Three"]        # trimmed, blanks + dupes dropped
    assert db.aka_titles_for_tmdb("show", 500) == ["One", "Two", "Three"]


def test_a_list_and_a_string_are_equivalent(app_db):
    _, db, _ = app_db
    assert db.set_aka_titles("show", 500, ["A", "B"]) == ["A", "B"]
    assert db.set_aka_titles("show", 500, "A\nB") == ["A", "B"]


def test_clearing_removes_every_alias(app_db):
    _, db, _ = app_db
    db.set_aka_titles("show", 500, "Something")
    assert db.set_aka_titles("show", 500, "") == []
    assert db.aka_titles_for_tmdb("show", 500) == []


def test_an_alias_can_be_set_before_the_show_exists(app_db):
    """The whole reason for keying on the tmdb id. The releases these fix are for
    titles you do NOT own yet — you're searching to grab episode 1 — so there is
    no library row to hang an alias off. Per-row storage had nowhere to put one,
    and the Manage button that housed the field wasn't even shown."""
    _, db, _ = app_db                                  # nothing seeded
    assert db.set_aka_titles("show", 500, _AKA) == [_AKA]
    assert db.aka_titles_for_tmdb("show", 500) == [_AKA]
    assert titles_match(_TENKOSAKI, [_TMDB_NAME, *db.aka_titles_for_tmdb("show", 500)]) is True


def test_one_alias_set_serves_every_copy_of_a_title(app_db):
    """A show mirrored on two servers is two rows. Keyed by tmdb id they share
    one alias set by construction, so it can't matter which row you opened."""
    _, db, _ = app_db
    a = _show(db, server_id="s1")
    b = _show(db, server_id="s2")
    assert a != b
    db.set_aka_titles("show", 500, "Shared Name")
    assert db.aka_titles("show", a) == ["Shared Name"]
    assert db.aka_titles("show", b) == ["Shared Name"]


def test_an_alias_survives_the_library_row_being_deleted(app_db):
    """A rescan that drops and recreates the row must not silently lose it."""
    _, db, _ = app_db
    show_id = _show(db)
    db.set_aka_titles("show", 500, "Persistent")
    conn = db._get_connection()
    conn.execute("DELETE FROM shows WHERE id=?", (show_id,))
    conn.commit(); conn.close()
    assert db.aka_titles_for_tmdb("show", 500) == ["Persistent"]


def test_lookups_are_defensive(app_db):
    _, db, _ = app_db
    assert db.set_aka_titles("nonsense", 1, "x") is None
    assert db.set_aka_titles("show", None, "x") is None
    assert db.set_aka_titles("show", "not-a-number", "x") is None
    assert db.aka_titles("show", None) == []
    assert db.aka_titles_for_tmdb("show", None) == []
    assert db.aka_titles_for_tmdb("nonsense", 1) == []


def test_movies_have_it_too(app_db):
    """Foreign films hit the same problem as anime."""
    _, db, _ = app_db
    db.upsert_movie("plex", {"server_id": "m1", "tmdb_id": 77, "title": "The Movie"})
    assert db.set_aka_titles("movie", 77, "Le Film") == ["Le Film"]
    assert db.aka_titles_for_tmdb("movie", 77) == ["Le Film"]
    assert db.aka_titles_for_tmdb("show", 77) == []      # kinds don't collide


def test_the_endpoint_accepts_a_tmdb_id_for_an_unowned_title(app_db):
    """source:'tmdb' says item_id is a TMDB id, not a library row — the case the
    per-row design couldn't express at all."""
    client, db, _ = app_db
    body = client.put("/api/video/detail/show/500/aka",
                      json={"titles": _AKA, "source": "tmdb"}).get_json()
    assert body["ok"] is True and body["aka_titles"] == [_AKA]
    assert db.aka_titles_for_tmdb("show", 500) == [_AKA]


def test_akas_migrate_off_the_old_per_row_columns(tmp_path):
    """Anything typed under 1.6.12's per-row storage has to survive the move."""
    import sqlite3

    import database.video_database as vdb
    p = tmp_path / "old.db"
    VideoDatabase(database_path=str(p))                # build the current schema
    conn = sqlite3.connect(str(p))
    conn.execute("DELETE FROM video_title_overrides")
    conn.execute("INSERT INTO shows (id, tmdb_id, title, aka_titles) VALUES (1, 500, 'X', 'Legacy Name')")
    conn.execute("PRAGMA user_version = 46")           # pre-move
    conn.commit(); conn.close()

    # Schema init is guarded once-per-path for the process, so a second
    # construction would skip the migration entirely. Drop the guard to make the
    # reopen behave like a real restart on an upgraded install.
    vdb._initialized_paths.discard(str(p.resolve()))

    upgraded = VideoDatabase(database_path=str(p))
    assert upgraded.aka_titles_for_tmdb("show", 500) == ["Legacy Name"]


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


def test_aliases_are_readable_without_a_library_row(app_db):
    """The manage panel opens on titles that aren't in the library, so it needs a
    read path keyed by tmdb id — the detail payload it normally reads doesn't
    exist for those."""
    client, db, _ = app_db
    db.set_aka_titles("show", 500, _AKA)
    body = client.get("/api/video/detail/aka/show/500").get_json()
    assert body["ok"] is True and body["aka_titles"] == [_AKA]
    # unknown title is empty, not an error
    assert client.get("/api/video/detail/aka/show/424242").get_json()["aka_titles"] == []
    assert client.get("/api/video/detail/aka/banana/500").status_code == 400


def test_reading_aliases_is_open_but_writing_stays_admin(app_db):
    """Reading back titles the user typed is harmless; changing what the
    downloader matches is management."""
    client, db, persona = app_db
    db.set_aka_titles("show", 500, _AKA)
    persona.update({"profile_id": 5, "is_admin": False, "can_download": False})
    assert client.get("/api/video/detail/aka/show/500").status_code == 200
    assert client.put("/api/video/detail/show/500/aka",
                      json={"titles": "nope", "source": "tmdb"}).status_code == 403


# ── series type, settable before the show is in the library ──────────────────
def test_series_type_can_be_set_for_a_show_you_do_not_own(app_db):
    """Series type decides HOW episodes are hunted — SxxExx vs air date vs
    absolute number — so it matters most while you're still acquiring the show,
    which is exactly when set_show_series_type has no row to write to."""
    client, db, _ = app_db                              # nothing seeded
    r = client.put("/api/video/detail/show/500/series-type",
                   json={"series_type": "anime", "source": "tmdb"})
    assert r.status_code == 200 and r.get_json()["success"] is True
    assert db.series_type_for_tmdb(500) == "anime"


def test_the_drain_reads_the_override_for_an_unowned_show(app_db):
    """The payoff: a wished episode of a show you don't own yet is queried with
    anime numbering because you said so."""
    _, db, _ = app_db
    db.set_series_type_override(700, "anime")
    db.add_episodes_to_wishlist(700, "Brand New Anime",
                                [{"season_number": 1, "episode_number": 1}])
    rows = [i for i in db.episode_wishlist_to_download() if i.get("show_tmdb_id") == 700]
    assert rows and rows[0]["series_type"] == "anime"


def test_the_library_row_wins_once_the_show_exists(app_db):
    """The override is a stand-in until the real row can answer. Keeping it
    ahead of the row would silently ignore a later change made on the show."""
    _, db, _ = app_db
    show_id = _show(db, tmdb_id=700, title="Owned")
    db.set_series_type_override(700, "anime")
    db.set_show_series_type(show_id, "daily")
    db.add_episodes_to_wishlist(700, "Owned", [{"season_number": 1, "episode_number": 1}])
    rows = [i for i in db.episode_wishlist_to_download() if i.get("show_tmdb_id") == 700]
    assert rows and rows[0]["series_type"] == "daily"


def test_series_type_override_validates_and_clears(app_db):
    _, db, _ = app_db
    assert db.set_series_type_override(500, "nonsense") is None
    assert db.set_series_type_override("not-a-number", "anime") is None
    db.set_series_type_override(500, "anime")
    assert db.set_series_type_override(500, "") == ""     # cleared
    assert db.series_type_for_tmdb(500) is None
    assert db.series_type_for_tmdb(None) is None


def test_the_aka_read_endpoint_carries_the_series_type(app_db):
    """One fetch backs the whole not-in-library panel."""
    client, db, _ = app_db
    db.set_series_type_override(500, "anime")
    db.set_aka_titles("show", 500, _AKA)
    body = client.get("/api/video/detail/aka/show/500").get_json()
    assert body["series_type"] == "anime" and body["aka_titles"] == [_AKA]
    # movies have no series type
    assert client.get("/api/video/detail/aka/movie/77").get_json()["series_type"] is None


def test_setting_series_type_for_an_owned_show_is_unchanged(app_db):
    """No source → library row id, exactly as before."""
    client, db, _ = app_db
    show_id = _show(db)
    assert client.put("/api/video/detail/show/%d/series-type" % show_id,
                      json={"series_type": "daily"}).status_code == 200
    assert db.show_detail(show_id)["series_type"] == "daily"
    assert db.series_type_for_tmdb(500) is None          # no override written


# ── frontend contract ────────────────────────────────────────────────────────
def test_the_panel_offers_series_type_without_a_row():
    from pathlib import Path
    js = (Path(__file__).resolve().parent.parent / "webui" / "static" / "video"
          / "video-manage-panel.js").read_text(encoding="utf-8")
    body = js.split("function tmdbOnlyBodyHtml", 1)[1].split("function bodyHtml", 1)[0]
    assert "data-vmg-series-type" in body
    assert "source: state.tmdbOnly ? 'tmdb' : 'library'" in js


def test_manage_opens_for_a_title_that_is_not_in_the_library():
    """The reported gap: the button was gated on a library row, so the one
    control that works without one was unreachable.

    Manage is ALSO admin-only now (everything it saves — /metadata, /lock, /aka,
    /library — is admin in the video blueprint's gate). Both must hold: a library
    row is still not required, and a non-admin never gets the button. Asserted as
    two separate properties rather than one exact line, so re-ordering the
    condition doesn't fail this for cosmetic reasons."""
    from pathlib import Path
    js = (Path(__file__).resolve().parent.parent / "webui" / "static" / "video"
          / "video-detail.js").read_text(encoding="utf-8")
    cond = js.split("window.VideoManage &&", 1)[1].split(")", 1)[0]
    assert "ownLibItem || d.tmdb_id" in cond, "a tmdb-only title must still qualify"
    assert "_isAdmin &&" in js.split("window.VideoManage &&", 1)[0][-40:], \
        "and the button is admin-only"
    # ...and it opens in tmdb mode, passing the id the aliases are keyed by
    assert "source: 'tmdb', detail: data" in js


def test_the_panel_renders_only_what_applies_without_a_row():
    """Metadata, locks, quality profile, series type and Matches all need a row.
    Showing them disabled would be a panel of dead controls."""
    from pathlib import Path
    js = (Path(__file__).resolve().parent.parent / "webui" / "static" / "video"
          / "video-manage-panel.js").read_text(encoding="utf-8")
    assert "function tmdbOnlyBodyHtml" in js
    assert "if (d._tmdbOnly) return tmdbOnlyBodyHtml(d);" in js
    # the row-backed loaders must not run for it
    assert "if (!tmdbOnly) {" in js
    # and the save must tell the endpoint the id is a tmdb id
    assert "source: state.tmdbOnly ? 'tmdb' : 'library'" in js
def test_the_manage_panel_renders_and_saves_the_field():
    from pathlib import Path
    js = (Path(__file__).resolve().parent.parent / "webui" / "static" / "video"
          / "video-manage-panel.js").read_text(encoding="utf-8")
    assert "data-vmg-aka" in js and "data-vmg-aka-save" in js
    assert "'/aka'" in js or "/aka'" in js
    assert "saveAkaTitles" in js
    # echoes back the STORED list, so the box shows the truth after cleanup
    assert "d.aka_titles || []" in js
