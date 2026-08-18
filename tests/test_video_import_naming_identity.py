"""An import must name a file the way the LIBRARY names it, not the way the grab does.

Diagnosed against a real 66MB video_library.db. Every ongoing show in that install
was forked in two: 36 of the last 37 episode grabs wrote to a folder that was not
the folder the library used. The one that agreed is the tell — that show LIVES in
the malformed folder, because it has only ever been acquired this way.

The importer built its destination from the GRAB and every other part of the app
built it from the library row, so for one show they disagreed like this:

    import  ->  Kitchen Nightmares (US) (2026) (tmdb-)
    rename  ->  Kitchen Nightmares (US) (2007) (tmdb-235884)

Three separate mistakes, all in the episode branch:

  · YEAR came from the episode's air date. A series template's year is the
    PREMIERE year — Futurama (1999), not Futurama (2026).
  · TMDBID was hardcoded None for episodes, so a template asking for it rendered
    an empty '(tmdb-)' even though the id was sitting in the download row.
  · TVDBID was filled with ``media_id``, which is a TMDB id or a local row id and
    never a TVDB one. That asserts an id that is FALSE rather than missing, which
    is worse, because the media server believes it.

The consequence is not a cosmetic name. The server scans the new folder as a
brand-new series and has to guess what it is; the library scan then reads that
phantom back. In the reported install the guesses had already produced a second
'Its Always Sunny in Philadelphia' (2026, no ids, apostrophe dropped by
CleanTitle) holding two episodes, three separate folders for one anime, and a
Futurama episode filed under 'The Invisible Man and His Soon-to-Be Wife'.

``plan_import`` is pure and has no DB, so the facts arrive as ``identity`` —
resolved by the caller, exactly like ``library_dir`` beside it.
"""

from __future__ import annotations

import json

import pytest

from core.video import importer, organization
from database.video_database import VideoDatabase

# The user's actual template — the TRaSH scheme, which is what the app's own
# one-click button installs. Note '(tmdb-' and ')' are LITERAL text either side
# of the {TmdbId} group, so an empty id leaves '(tmdb-)' behind rather than
# collapsing. That is the scheme people really run.
TRASH = ('{Series CleanTitleWithoutYear} {(Series Year)} (tmdb-{TmdbId}) /Season {season:00}/'
         '{Series CleanTitleWithoutYear} {(Series Year)} - S{season:00}E{episode:00} - '
         '{Episode CleanTitle:90} {[Quality Full]}{-Release Group}')
SETTINGS = organization.normalize({"episode_template": TRASH, "verify_with_ffprobe": False})
ROOT = "/tv"

SRC = "/downloads/Kitchen Nightmares US S09E06 1080p WEB h264-EDITH.mkv"

IDENTITY = {"title": "Kitchen Nightmares (US)", "year": 2007, "tmdbid": 235884,
            "tvdbid": 80552, "imdbid": "tt0983514",
            "episodes": {(9, 6): {"episode_title": "Boodles Restaurant",
                                  "air_date": "2026-08-18"}}}


def _dl(**over):
    """The download row for Kitchen Nightmares S09E06, as the real one was."""
    ctx = {"scope": "episode", "title": "Kitchen Nightmares (US)", "season": 9,
           "episode": 6, "year": "2026", "air_date": "2026-08-18"}
    ctx.update(over.pop("ctx", {}))
    row = {"id": 138, "kind": "episode", "title": "Kitchen Nightmares (US)",
           "release_title": "Kitchen Nightmares US S09E06 1080p WEB h264-EDITH",
           "media_id": "235884", "media_source": "tmdb", "target_dir": ROOT,
           "search_ctx": json.dumps(ctx)}
    row.update(over)
    return row


def _plan(identity=IDENTITY, dl=None, **kw):
    return importer.plan_import(dl or _dl(), SRC, list_dir=lambda d: [],
                                settings=SETTINGS, identity=identity, **kw)


def _dir(plan):
    return plan["dest"]["dir"].replace("\\", "/")


# ── the fork itself ─────────────────────────────────────────────────────────

class TestTheFolderMatchesTheLibrary:
    def test_the_year_is_the_series_premiere_not_the_air_year(self):
        """The whole reported bug in one assertion: this episode aired in 2026
        and the show began in 2007, and it is 2007 that names the folder."""
        assert _dir(_plan()) == "/tv/Kitchen Nightmares (US) (2007) (tmdb-235884)/Season 09"

    def test_without_the_library_it_falls_back_to_what_the_grab_knew(self):
        """An unowned title is the one case where nothing better exists. It must
        keep working, and it must still carry the id it does have."""
        assert _dir(_plan(identity=None)) == \
            "/tv/Kitchen Nightmares (US) (2026) (tmdb-235884)/Season 09"

    def test_a_tmdb_grab_of_a_show_you_do_not_own_still_gets_a_real_id(self):
        """The first episode of a new show is the grab that CREATES the folder
        the server will identify the show by. Leaving '(tmdb-)' there is what
        let the server invent a phantom series in the first place."""
        assert "(tmdb-235884)" in _dir(_plan(identity=None))

    def test_the_episode_title_comes_from_the_library(self):
        """Imported without it, every new episode is named without its title
        until some later rename pass puts it back."""
        assert "Boodles Restaurant" in _plan()["dest"]["filename"]


class TestTheIdsAreTrue:
    def test_media_id_is_never_written_as_a_tvdb_id(self):
        """media_id here is TMDB 235884; the show's real TVDB id is 80552.
        Writing the former into a (tvdb-) folder asserted a false identity."""
        s = organization.normalize({"episode_template": "$series ($year) (tvdb-$tvdbid)/x"})
        d = importer.plan_import(_dl(), SRC, list_dir=lambda p: [], settings=s,
                                 identity=IDENTITY)["dest"]["dir"]
        assert "tvdb-80552" in d and "235884" not in d

    def test_an_unowned_show_gets_no_tvdb_id_rather_than_a_wrong_one(self):
        s = organization.normalize({"episode_template": "$series (tvdb-$tvdbid)/x"})
        d = importer.plan_import(_dl(), SRC, list_dir=lambda p: [], settings=s,
                                 identity=None)["dest"]["dir"]
        assert "235884" not in d

    def test_a_library_sourced_regrab_does_not_leak_its_row_id(self):
        """media_source 'library' means media_id is the shows ROW id. It named
        folders like 'Bleach (2004) (tvdb-200)' — 200 being row 200."""
        s = organization.normalize({"episode_template": "$series (tvdb-$tvdbid)/x"})
        dl = _dl(media_id="200", media_source="library")
        d = importer.plan_import(dl, SRC, list_dir=lambda p: [], settings=s,
                                 identity=None)["dest"]["dir"]
        assert "200" not in d

    @pytest.mark.parametrize("token,expected", [
        ("$tmdbid", "235884"), ("$imdbid", "tt0983514"), ("$tvdbid", "80552"),
    ])
    def test_every_id_token_renders_for_an_episode(self, token, expected):
        """$tmdbid and $imdbid were absent from the episode $token vocabulary
        altogether, so the same scheme written in $tokens silently dropped them."""
        s = organization.normalize({"episode_template": "$series [%s]/x" % token})
        d = importer.plan_import(_dl(), SRC, list_dir=lambda p: [], settings=s,
                                 identity=IDENTITY)["dest"]["dir"]
        assert "[%s]" % expected in d


class TestTheImporterAgreesWithTheRenamer:
    def test_both_paths_compute_the_same_folder(self):
        """THE invariant. These two are computed by different code from different
        rows, and every time they disagree the media server sees two shows.
        ``rename_before_import`` exists to prevent exactly that split and could
        never win, because it fixed the old files while the import kept creating
        a new folder next to them."""
        from core.video.mass_rename import _episode_fields
        rename_dir = organization.render_path("episode", ROOT, _episode_fields({
            "show_title": "Kitchen Nightmares (US)", "show_year": 2007,
            "season_number": 9, "episode_number": 6,
            "episode_title": "Boodles Restaurant", "air_date": "2026-08-18",
            "tmdb_id": 235884, "tvdb_id": 80552, "imdb_id": "tt0983514",
            "quality": "WEBDL-1080p", "resolution": "1080p",
        }), SETTINGS, ".mkv")["dir"]
        assert _plan()["dest"]["dir"] == rename_dir


# ── what must NOT change ────────────────────────────────────────────────────

class TestTheGatesAreUntouched:
    """Identity supplies naming facts only. Season, episode and air date are what
    the sanity gates judge a release against, and those still come from the grab —
    otherwise a library row could talk the importer into accepting a release it
    had already decided was the wrong episode."""

    def test_a_wrong_episode_release_is_still_refused(self):
        dl = _dl(release_title="Kitchen Nightmares US S10E05 1080p WEB h264-EDITH")
        out = importer.plan_import(dl, "/downloads/Kitchen.Nightmares.US.S10E05.mkv",
                                   list_dir=lambda p: [], settings=SETTINGS,
                                   identity=IDENTITY)
        assert out["action"] == "reject" and "S10E05" in out["reason"]

    def test_a_library_air_date_cannot_override_the_grabs(self):
        """The date gate is how daily shows match. Identity carries an air_date
        per episode and must not be allowed near it."""
        ident = {**IDENTITY, "episodes": {(9, 6): {"episode_title": "X",
                                                   "air_date": "1999-01-01"}}}
        out = importer.plan_import(_dl(), SRC, list_dir=lambda p: [],
                                   settings=SETTINGS, identity=ident)
        assert out["action"] == "import"

    def test_a_manual_placement_still_wins(self):
        """force/override IS a deliberate statement of identity — the fallback
        people reach for when naming has gone wrong. It outranks the library."""
        out = importer.plan_import(
            _dl(), SRC, list_dir=lambda p: [], settings=SETTINGS, identity=IDENTITY,
            force=True, override={"scope": "episode", "title": "Renamed By Hand",
                                  "year": 1988, "season": 9, "episode": 6,
                                  "target_dir": ROOT})["dest"]["dir"]
        assert "Renamed By Hand (1988)" in out

    def test_correcting_the_episode_number_by_hand_renames_to_the_RIGHT_episode(self):
        """A manual placement happens precisely when the detected episode was
        wrong, so the library must be read at the CORRECTED number. Looked up
        any earlier and the fixed file gets named after the episode it was
        mistaken for — which is the same class of wrongness being fixed here."""
        ident = {**IDENTITY, "episodes": {
            (9, 6): {"episode_title": "Boodles Restaurant", "air_date": "2026-08-18"},
            (9, 7): {"episode_title": "The Right One", "air_date": "2026-08-25"}}}
        out = importer.plan_import(
            _dl(), SRC, list_dir=lambda p: [], settings=SETTINGS, identity=ident,
            force=True, override={"scope": "episode", "season": 9, "episode": 7,
                                  "target_dir": ROOT})["dest"]["filename"]
        assert "The Right One" in out and "Boodles" not in out

    def test_a_manual_placement_of_an_unowned_title_keeps_its_tmdb_id(self):
        """The Place dialog resolves the title against TMDB and sends that id;
        it is the only id such a placement has."""
        out = importer.plan_import(
            _dl(media_source=""), SRC, list_dir=lambda p: [], settings=SETTINGS,
            identity=None, force=True,
            override={"scope": "episode", "title": "New Show", "season": 9,
                      "episode": 6, "media_id": "4242",
                      "target_dir": ROOT})["dest"]["dir"]
        assert "(tmdb-4242)" in out

    def test_movies_are_unaffected_when_nothing_is_known(self):
        dl = {"id": 1, "kind": "movie", "title": "Fight Club", "media_id": "550",
              "media_source": "tmdb", "target_dir": "/movies",
              "release_title": "Fight Club 1999 1080p BluRay x264",
              "search_ctx": json.dumps({"scope": "movie", "title": "Fight Club",
                                        "year": 1999})}
        s = organization.normalize({"movie_template": "$title ($year) (tmdb-$tmdbid)/$title"})
        d = importer.plan_import(dl, "/downloads/Fight Club 1999 1080p BluRay x264.mkv",
                                 list_dir=lambda p: [], settings=s,
                                 identity=None)["dest"]["dir"]
        assert d.replace("\\", "/") == "/movies/Fight Club (1999) (tmdb-550)"


# ── the DB layer ────────────────────────────────────────────────────────────

@pytest.fixture
def db(tmp_path):
    import database.video_database as mod
    # Process-level cache: a second VideoDatabase(path) is a no-op without this.
    if hasattr(mod, "_initialized_paths"):
        mod._initialized_paths.clear()
    return VideoDatabase(str(tmp_path / "v.db"))


def _show(db, **cols):
    cols.setdefault("server_source", "plex")
    cols.setdefault("server_id", "s%s" % cols.get("tmdb_id"))
    c = db._get_connection()
    c.execute("INSERT INTO shows (%s) VALUES (%s)"
              % (", ".join(cols), ", ".join("?" * len(cols))), tuple(cols.values()))
    c.commit(); c.close()


class TestNamingIdentity:
    def test_it_reports_the_library_facts(self, db):
        _show(db, tmdb_id=235884, title="Kitchen Nightmares (US)", year=2007,
              tvdb_id=80552, imdb_id="tt0983514")
        out = db.video_naming_identity("show", 235884)
        assert (out["title"], out["year"], out["tmdbid"], out["tvdbid"], out["imdbid"]) == \
            ("Kitchen Nightmares (US)", 2007, 235884, 80552, "tt0983514")

    def test_a_missing_year_heals_from_the_premiere_date(self, db):
        """Enrichment fills first_air_date even when the media server omits the
        year, and the rename query already reads it — the two must agree on what
        the year IS or they are back to computing different folders."""
        _show(db, tmdb_id=1, title="X", first_air_date="2013-06-19")
        assert db.video_naming_identity("show", 1)["year"] == 2013

    def test_it_carries_the_whole_episode_list(self, db):
        """One query for the show, so a season PACK names every member from the
        library too instead of one lookup per file."""
        _show(db, tmdb_id=1, title="X", year=2020)
        c = db._get_connection()
        sid = c.execute("SELECT id FROM shows WHERE tmdb_id=1").fetchone()[0]
        # episodes.season_id is NOT NULL — an episode cannot exist without its season.
        c.execute("INSERT INTO seasons (show_id, season_number) VALUES (?,1)", (sid,))
        sn_id = c.execute("SELECT id FROM seasons WHERE show_id=?", (sid,)).fetchone()[0]
        c.execute("INSERT INTO episodes (show_id, season_id, season_number, episode_number, "
                  "title, air_date) VALUES (?,?,1,1,'Pilot','2020-01-01')", (sid, sn_id))
        c.commit(); c.close()
        assert db.video_naming_identity("show", 1)["episodes"][(1, 1)] == \
            {"episode_title": "Pilot", "air_date": "2020-01-01"}

    def test_a_title_you_do_not_own_reports_nothing(self, db):
        assert db.video_naming_identity("show", 999999) is None

    def test_movies_report_their_own_shape(self, db):
        c = db._get_connection()
        c.execute("INSERT INTO movies (tmdb_id, title, year, imdb_id, server_source, "
                  "server_id) VALUES (550,'Fight Club',1999,'tt0137523','plex','m550')")
        c.commit(); c.close()
        out = db.video_naming_identity("movie", 550)
        assert out["tmdbid"] == 550 and out["imdbid"] == "tt0137523"
        assert out["tvdbid"] is None and "episodes" not in out

    @pytest.mark.parametrize("kind,tid", [("show", None), ("show", "nope"), ("bogus", 1)])
    def test_nonsense_is_declined_not_raised(self, db, kind, tid):
        assert db.video_naming_identity(kind, tid) is None
