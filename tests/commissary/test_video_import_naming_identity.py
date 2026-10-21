"""An import names a file the way the LIBRARY names it, not the way the grab does.

Fork 2.0.3 / 2.4.0 / 1.9.22. The importer built its destination from the grab:
the episode's AIR year instead of the series premiere year, no tmdb id for
episodes (an empty '(tmdb-)'), and ``media_id`` — a TMDB id or a local row id —
written as the TVDB id. Every ongoing show forked into a second folder the media
server had to guess the identity of.
"""

from __future__ import annotations

import json

import pytest

from core.commissary.naming_identity import library_identity, naming_identity
from core.video import importer, organization
from database.video_database import VideoDatabase

TEMPLATE = ("$series ($year) (tmdb-$tmdbid)/Season $season/"
            "$series ($year) - S$seasonE$episode - $episodetitle $quality")
SETTINGS = organization.normalize({"episode_template": TEMPLATE, "verify_with_ffprobe": False})
ROOT = "/tv"
SRC = "/downloads/Kitchen Nightmares US S09E06 1080p WEB h264-EDITH.mkv"
IDENTITY = {"title": "Kitchen Nightmares (US)", "year": 2007, "tmdbid": 235884,
            "tvdbid": 80552, "imdbid": "tt0983514",
            "episodes": {(9, 6): {"episode_title": "Boodles Restaurant",
                                  "air_date": "2026-08-18"}}}


def _dl(**over):
    ctx = {"scope": "episode", "title": "Kitchen Nightmares (US)", "season": 9,
           "episode": 6, "year": "2026", "air_date": "2026-08-18"}
    ctx.update(over.pop("ctx", {}))
    row = {"id": 138, "kind": "episode", "title": "Kitchen Nightmares (US)",
           "release_title": "Kitchen Nightmares US S09E06 1080p WEB h264-EDITH",
           "media_id": "235884", "media_source": "tmdb", "target_dir": ROOT,
           "search_ctx": json.dumps(ctx)}
    row.update(over)
    return row


def _plan(identity=IDENTITY, dl=None, settings=SETTINGS, **kw):
    return importer.plan_import(dl or _dl(), SRC, list_dir=lambda d: [],
                                settings=settings, identity=identity, **kw)


def _dir(plan):
    return plan["dest"]["dir"].replace("\\", "/")


def _tmpl(t):
    return organization.normalize({"episode_template": t, "verify_with_ffprobe": False})


# ── the folder matches the library ───────────────────────────────────────────
def test_the_year_is_the_series_premiere_not_the_air_year():
    assert _dir(_plan()) == "/tv/Kitchen Nightmares (US) (2007) (tmdb-235884)/Season 09"


def test_without_the_library_it_falls_back_to_the_grab_but_keeps_its_tmdb_id():
    assert _dir(_plan(identity=None)) == \
        "/tv/Kitchen Nightmares (US) (2026) (tmdb-235884)/Season 09"


def test_the_episode_title_comes_from_the_library():
    assert "Boodles Restaurant" in _plan()["dest"]["filename"]


def test_media_id_is_never_written_as_a_tvdb_id():
    d = _dir(_plan(settings=_tmpl("$series ($year) (tvdb-$tvdbid)/x")))
    assert "tvdb-80552" in d and "235884" not in d
    d = _dir(_plan(identity=None, settings=_tmpl("$series (tvdb-$tvdbid)/x")))
    assert "235884" not in d


def test_a_library_sourced_regrab_does_not_leak_its_row_id():
    """media_source 'library' means media_id is the shows ROW id."""
    d = _dir(_plan(identity=None, dl=_dl(media_id="200", media_source="library"),
                   settings=_tmpl("$series (tvdb-$tvdbid) (tmdb-$tmdbid)/x")))
    assert "200" not in d


@pytest.mark.parametrize("token,expected", [
    ("$tmdbid", "235884"), ("$imdbid", "tt0983514"), ("$tvdbid", "80552"),
])
def test_every_id_token_renders_for_an_episode(token, expected):
    assert "[%s]" % expected in _dir(_plan(settings=_tmpl("$series [%s]/x" % token)))


def test_the_importer_agrees_with_the_renamer():
    """Different code from different rows — every disagreement is a second show."""
    from core.video.mass_rename import _episode_fields
    s = _tmpl("$series ($year)/Season $season/$series - S$seasonE$episode - $episodetitle")
    rename_dir = organization.render_path("episode", ROOT, _episode_fields({
        "show_title": "Kitchen Nightmares (US)", "show_year": 2007,
        "season_number": 9, "episode_number": 6, "episode_title": "Boodles Restaurant",
    }), s, ".mkv")
    plan = _plan(settings=s)
    assert plan["dest"]["dir"] == rename_dir["dir"]
    assert plan["dest"]["filename"] == rename_dir["filename"]


# ── what must NOT change ────────────────────────────────────────────────────
def test_a_wrong_episode_release_is_still_refused():
    dl = _dl(release_title="Kitchen Nightmares US S10E05 1080p WEB h264-EDITH")
    out = importer.plan_import(dl, "/downloads/Kitchen.Nightmares.US.S10E05.mkv",
                               list_dir=lambda p: [], settings=SETTINGS, identity=IDENTITY)
    assert out["action"] == "reject" and "S10E05" in out["reason"]


def test_a_library_air_date_cannot_override_the_grabs():
    ident = {**IDENTITY, "episodes": {(9, 6): {"episode_title": "X", "air_date": "1999-01-01"}}}
    assert _plan(identity=ident)["action"] == "import"


def test_a_manual_placement_still_wins():
    out = _dir(_plan(force=True, override={"scope": "episode", "title": "Renamed By Hand",
                                           "year": 1988, "season": 9, "episode": 6,
                                           "target_dir": ROOT}))
    assert "Renamed By Hand (1988)" in out


def test_correcting_the_episode_number_by_hand_renames_to_the_right_episode():
    ident = {**IDENTITY, "episodes": {
        (9, 6): {"episode_title": "Boodles Restaurant", "air_date": "2026-08-18"},
        (9, 7): {"episode_title": "The Right One", "air_date": "2026-08-25"}}}
    out = _plan(identity=ident, force=True,
                override={"scope": "episode", "season": 9, "episode": 7,
                          "target_dir": ROOT})["dest"]["filename"]
    assert "The Right One" in out and "Boodles" not in out


def test_a_manual_placement_of_an_unowned_title_keeps_its_tmdb_id():
    out = _dir(_plan(identity=None, dl=_dl(media_source=""), force=True,
                     override={"scope": "episode", "title": "New Show", "season": 9,
                               "episode": 6, "media_id": "4242", "target_dir": ROOT}))
    assert "(tmdb-4242)" in out


def test_movies_without_identity_keep_their_tmdb_id():
    dl = {"id": 1, "kind": "movie", "title": "Fight Club", "media_id": "550",
          "media_source": "tmdb", "target_dir": "/movies",
          "release_title": "Fight Club 1999 1080p BluRay x264",
          "search_ctx": json.dumps({"scope": "movie", "title": "Fight Club", "year": 1999})}
    s = organization.normalize({"movie_template": "$title ($year) (tmdb-$tmdbid)/$title"})
    d = importer.plan_import(dl, "/downloads/Fight Club 1999 1080p BluRay x264.mkv",
                             list_dir=lambda p: [], settings=s, identity=None)["dest"]["dir"]
    assert d.replace("\\", "/") == "/movies/Fight Club (1999) (tmdb-550)"


# ── empty id tags and hidden folders (2.4.0, 1.9.22) ─────────────────────────
@pytest.mark.parametrize("raw,want", [
    ("Bleach (tmdb-)", "Bleach"),
    ("Bleach [tvdb-] (2004)", "Bleach (2004)"),
    ("Bleach {imdb-}", "Bleach"),
    ("Bleach (tmdbid-)", "Bleach"),
    ("Bleach (tmdb-30984)", "Bleach (tmdb-30984)"),
    ("...And Justice for All", "And Justice for All"),
    (".hack (2002)", "hack (2002)"),
    ("Mr. Bungle", "Mr. Bungle"),
])
def test_tidy_component(raw, want):
    assert organization._tidy_component(raw) == want


def test_an_episode_grabbed_before_its_show_has_an_id_gets_no_empty_tag():
    d = _dir(_plan(identity=None, dl=_dl(media_source="library", media_id="7")))
    assert d == "/tv/Kitchen Nightmares (US) (2026)/Season 09"


# ── the DB lookup ───────────────────────────────────────────────────────────
@pytest.fixture
def db(tmp_path):
    return VideoDatabase(database_path=str(tmp_path / "v.db"))


def _show(db, **cols):
    cols.setdefault("server_source", "plex")
    cols.setdefault("server_id", "s%s" % cols.get("tmdb_id"))
    c = db._get_connection()
    c.execute("INSERT INTO shows (%s) VALUES (%s)"
              % (", ".join(cols), ", ".join("?" * len(cols))), tuple(cols.values()))
    c.commit()
    c.close()


def test_it_reports_the_library_facts(db):
    _show(db, tmdb_id=235884, title="Kitchen Nightmares (US)", year=2007,
          tvdb_id=80552, imdb_id="tt0983514")
    out = naming_identity(db, "show", 235884)
    assert (out["title"], out["year"], out["tmdbid"], out["tvdbid"], out["imdbid"]) == \
        ("Kitchen Nightmares (US)", 2007, 235884, 80552, "tt0983514")


def test_a_missing_year_heals_from_the_premiere_date(db):
    _show(db, tmdb_id=1, title="X", first_air_date="2013-06-19")
    assert naming_identity(db, "show", 1)["year"] == 2013


def test_it_carries_the_whole_episode_list(db):
    _show(db, tmdb_id=1, title="X", year=2020)
    c = db._get_connection()
    sid = c.execute("SELECT id FROM shows WHERE tmdb_id=1").fetchone()[0]
    c.execute("INSERT INTO seasons (show_id, season_number) VALUES (?,1)", (sid,))
    sn_id = c.execute("SELECT id FROM seasons WHERE show_id=?", (sid,)).fetchone()[0]
    c.execute("INSERT INTO episodes (show_id, season_id, season_number, episode_number, "
              "title, air_date) VALUES (?,?,1,1,'Pilot','2020-01-01')", (sid, sn_id))
    c.commit()
    c.close()
    assert naming_identity(db, "show", 1)["episodes"][(1, 1)] == \
        {"episode_title": "Pilot", "air_date": "2020-01-01"}


def test_movies_report_their_own_shape(db):
    c = db._get_connection()
    c.execute("INSERT INTO movies (tmdb_id, title, year, imdb_id, server_source, "
              "server_id) VALUES (550,'Fight Club',1999,'tt0137523','plex','m550')")
    c.commit()
    c.close()
    out = naming_identity(db, "movie", 550)
    assert out["tmdbid"] == 550 and out["imdbid"] == "tt0137523"
    assert out["tvdbid"] is None and "episodes" not in out


@pytest.mark.parametrize("kind,tid", [("show", None), ("show", "nope"), ("bogus", 1),
                                      ("show", 999999)])
def test_nonsense_or_unowned_is_declined_not_raised(db, kind, tid):
    assert naming_identity(db, kind, tid) is None


def test_library_identity_resolves_a_download_row(db):
    _show(db, tmdb_id=235884, title="Kitchen Nightmares (US)", year=2007)
    assert library_identity(db, _dl())["year"] == 2007
    assert library_identity(db, {"kind": "youtube", "media_id": "abc"}) is None


# ── the production wiring ────────────────────────────────────────────────────
def test_the_automatic_organizer_passes_the_library_identity(monkeypatch, tmp_path):
    from core.video import download_monitor as monitor
    import core.commissary.naming_identity as ni
    seen = {}

    def fake_plan(dl, src, **kw):
        seen.update(kw)
        return {"action": "reject", "reason": "stop here", "manual": True}

    monkeypatch.setattr(importer, "plan_import", fake_plan)
    monkeypatch.setattr(importer, "run_import", lambda *a, **k: {"status": "import_failed"})
    monkeypatch.setattr(ni, "library_identity", lambda db, dl: {"title": "From Library"})
    monkeypatch.setattr(monitor, "_owned_library_dir", lambda db, dl: None)
    monkeypatch.setattr(monitor, "_expected_duration_sec", lambda db, dl: None)

    class _DB:
        def update_video_download(self, *a, **k):
            pass

        def get_setting(self, *a, **k):
            return None

    organize = monitor._make_organizer(_DB())
    organize({"id": 1, "source": "torrent"}, str(tmp_path / "x.mkv"))
    assert seen.get("identity") == {"title": "From Library"}


def test_manual_placement_passes_the_library_identity(tmp_path, monkeypatch):
    from flask import Flask

    import api.video as videoapi
    vdb = VideoDatabase(database_path=str(tmp_path / "video_library.db"))
    monkeypatch.setattr(videoapi, "_video_db", vdb)
    _show(vdb, tmdb_id=235884, title="Kitchen Nightmares (US)", year=2007, tvdb_id=80552)
    src = tmp_path / "file.mkv"
    src.write_bytes(b"x")
    dl_id = vdb.add_video_download({"kind": "episode", "title": "KN", "source": "torrent",
                                    "status": "import_failed", "filename": "file.mkv"})
    vdb.update_video_download(dl_id, status="import_failed", dest_path=str(src))
    seen = {}

    def fake_plan(dl, path, **kw):
        seen.update(kw)
        return {"action": "reject", "reason": "stop here", "manual": True}

    monkeypatch.setattr(importer, "plan_import", fake_plan)
    app = Flask(__name__)
    app.register_blueprint(videoapi.create_video_blueprint(), url_prefix="/api/video")

    @app.before_request
    def _admin():
        from flask import g
        g.profile_id, g.is_admin, g.can_download = 1, True, True

    app.test_client().post("/api/video/import/%d/place" % dl_id, json={
        "scope": "episode", "title": "KN", "season": 9, "episode": 6, "media_id": 235884})
    assert (seen.get("identity") or {}).get("tvdbid") == 80552
