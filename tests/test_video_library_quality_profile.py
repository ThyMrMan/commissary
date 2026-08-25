"""A quality profile for a whole Library, not one title at a time.

Profiles were assignable per TITLE only. That is fine for the odd exception and
useless for the thing people actually want to say — "everything in my 4K
Library is judged at 4K, everything in Anime is judged at 1080p" — which had to
be repeated once per title, forever, including for every show added later.

Worse, a title the library has never SEEN carried no assignment at all, so its
first grab was always judged by the global Default no matter which Library it
was explicitly headed for. The first grab is the one that decides what lands on
disk, so that was precisely the wrong moment to have no opinion.

The rule this adds, and everything below is a way of checking it:

    a title's OWN profile  >  its Library's default  >  the global Default

Per-title assignment is untouched — it still wins, which is what makes it an
override rather than a duplicate setting.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from flask import Flask

import core.video.quality_profile as qp
from database.video_database import VideoDatabase

_ROOT = Path(__file__).resolve().parent.parent


def _js(*parts) -> str:
    """Read a webui source file with line endings normalized.

    The repo stores these LF and a Windows checkout hands them back CRLF, so an
    anchor containing a newline passes on CI and fails only on a dev machine."""
    return (_ROOT.joinpath(*parts).read_text(encoding="utf-8")
            .replace("\r\n", "\n"))


_SETTINGS_JS = _js("webui", "static", "video", "video-settings.js")
_MANAGE_JS = _js("webui", "static", "video", "video-manage-panel.js")


@pytest.fixture()
def db(tmp_path):
    return VideoDatabase(database_path=str(tmp_path / "video_library.db"))


@pytest.fixture()
def client(db, monkeypatch):
    import api.video as videoapi
    import core.video.sources as sources
    monkeypatch.setattr(sources, "resolve_video_server", lambda: "plex")
    monkeypatch.setattr(sources, "list_video_libraries",
                        lambda: {"server": "plex", "movies": [], "tv": []})
    videoapi._video_db = db
    app = Flask(__name__)
    app.register_blueprint(videoapi.create_video_blueprint(), url_prefix="/api/video")
    try:
        yield app.test_client()
    finally:
        videoapi._video_db = None


def _profile(db, name, cutoff="2160p"):
    return qp.save_named(db, None, name, {"cutoff_resolution": cutoff})["id"]


def _libraries(db, kind, entries, server="plex"):
    """Configure Libraries the way the settings page does — through save_libraries,
    so the write path is exercised rather than side-stepped with raw SQL.

    ``entries`` is [(title, path, default_profile_id?), ...]. Both kinds are
    passed in one call each; passing None for the other kind leaves it alone."""
    payload = [{"server_title": e[0], "label": e[0], "path": e[1],
                "default_quality_profile_id": (e[2] if len(e) > 2 else None)}
               for e in entries]
    libs = db.save_libraries(server, payload if kind == "movies" else None,
                             payload if kind == "tv" else None, None)
    return [r["id"] for r in libs[kind]]


def _a_movie(db, tmdb_id=693134, title="Dune", root_folder_id=None):
    mid = db.upsert_movie("plex", {"server_id": "m%s" % tmdb_id, "title": title,
                                   "tmdb_id": tmdb_id})
    if root_folder_id:
        db.set_item_root_folder("movie", mid, root_folder_id)
    return mid


def _a_show(db, tmdb_id=95396, title="Severance", root_folder_id=None):
    db.upsert_show_tree("plex", {
        "server_id": "s%s" % tmdb_id, "title": title, "tmdb_id": tmdb_id,
        "seasons": [{"season_number": 1, "episodes": [{"episode_number": 1}]}]})
    sid = [s for s in db.query_library("shows")["items"] if s["tmdb_id"] == tmdb_id][0]["id"]
    if root_folder_id:
        db.set_item_root_folder("show", sid, root_folder_id)
    return sid


# ── the default reaches the paths that actually grab things ─────────────────

class TestTheLibraryDefaultReachesAcquisition:
    """The drain and the manual search read the profile off the row they are
    about to act on. If the Library default doesn't arrive there it doesn't
    exist, whatever the resolver says in isolation."""

    def test_the_movie_drain_judges_by_the_library(self, db):
        pid = _profile(db, "4K")
        [rid] = _libraries(db, "movies", [("4K Films", "/4k", pid)])
        _a_movie(db, root_folder_id=rid)
        db.add_movie_to_wishlist(693134, "Dune")
        items = db.movie_wishlist_to_download()
        assert items and items[0]["quality_profile_id"] == pid

    def test_the_episode_drain_judges_by_the_library(self, db):
        pid = _profile(db, "Anime 1080p", "1080p")
        [rid] = _libraries(db, "tv", [("Anime", "/anime", pid)])
        _a_show(db, root_folder_id=rid)
        db.add_episodes_to_wishlist(95396, "Severance",
                                    [{"season_number": 1, "episode_number": 1}])
        items = db.episode_wishlist_to_download()
        assert items and items[0]["quality_profile_id"] == pid

    def test_a_manual_search_judges_by_it_too(self, db):
        """Same question asked from the UI rather than the automation — it must
        not get a different answer, or a user 'confirms' a bug by hand."""
        pid = _profile(db, "4K")
        [rid] = _libraries(db, "movies", [("4K Films", "/4k", pid)])
        _a_movie(db, root_folder_id=rid)
        db.add_movie_to_wishlist(693134, "Dune")
        items = db.wishlist_manual_search_items("movie", 693134)
        assert items and items[0]["quality_profile_id"] == pid

    def test_a_library_with_no_default_still_means_the_global_default(self, db):
        """The behaviour of every install before this column existed."""
        [rid] = _libraries(db, "movies", [("Films", "/films")])
        _a_movie(db, root_folder_id=rid)
        db.add_movie_to_wishlist(693134, "Dune")
        assert db.movie_wishlist_to_download()[0]["quality_profile_id"] is None


# ── precedence: the per-title assignment is an override, not a duplicate ────

class TestPrecedence:
    def test_a_titles_own_profile_outranks_its_library(self, db):
        lib, own = _profile(db, "Library 4K"), _profile(db, "Hand picked", "720p")
        [rid] = _libraries(db, "movies", [("4K Films", "/4k", lib)])
        mid = _a_movie(db, root_folder_id=rid)
        db.add_movie_to_wishlist(693134, "Dune")
        db.set_title_quality_profile("movie", mid, own)
        assert db.movie_wishlist_to_download()[0]["quality_profile_id"] == own
        assert db.quality_profile_id_for("movie", tmdb_id=693134) == own

    def test_clearing_a_title_falls_back_to_its_library(self, db):
        """'Default' on the per-title picker means INHERIT, not 'the global one'
        — otherwise clearing an override would quietly opt the title out of the
        Library setting it is sitting in."""
        lib, own = _profile(db, "Library 4K"), _profile(db, "Hand picked", "720p")
        [rid] = _libraries(db, "movies", [("4K Films", "/4k", lib)])
        mid = _a_movie(db, root_folder_id=rid)
        db.add_movie_to_wishlist(693134, "Dune")
        db.set_title_quality_profile("movie", mid, own)
        db.set_title_quality_profile("movie", mid, 0)
        assert db.movie_wishlist_to_download()[0]["quality_profile_id"] == lib
        assert db.quality_profile_id_for("movie", library_id=mid) == lib

    def test_a_wishlist_rows_own_assignment_outranks_the_library(self, db):
        """Import lists stamp a profile straight onto wishlist rows for titles
        that have no library row yet. That is an explicit assignment and has to
        keep beating a Library-wide default."""
        lib, wish = _profile(db, "Library 4K"), _profile(db, "From a list", "1080p")
        [rid] = _libraries(db, "movies", [("4K Films", "/4k", lib)])
        _a_movie(db, root_folder_id=rid)
        db.add_movie_to_wishlist(693134, "Dune")
        c = db._get_connection()
        c.execute("UPDATE video_wishlist SET quality_profile_id=? WHERE tmdb_id=693134", (wish,))
        c.commit(); c.close()
        assert db.movie_wishlist_to_download()[0]["quality_profile_id"] == wish

    def test_it_follows_the_library_the_row_is_actually_filed_into(self, db):
        """The row's destination and the profile judging it must be the SAME
        Library. Judging a grab by the 4K Library's profile and then filing it
        into the 1080p one would be worse than having no Library default."""
        four_k, hd = _profile(db, "4K"), _profile(db, "1080p only", "1080p")
        rids = _libraries(db, "movies", [("4K Films", "/4k", four_k),
                                         ("Films", "/films", hd)])
        _a_movie(db, root_folder_id=rids[0])
        db.add_movie_to_wishlist(693134, "Dune")
        # the wishlist row is redirected at the OTHER Library
        c = db._get_connection()
        c.execute("UPDATE video_wishlist SET root_folder_id=? WHERE tmdb_id=693134", (rids[1],))
        c.commit(); c.close()
        row = db.movie_wishlist_to_download()[0]
        assert row["root_folder_id"] == rids[1]
        assert row["quality_profile_id"] == hd


# ── the resolver, including for titles the library has never seen ───────────

class TestTheResolver:
    def test_an_explicit_destination_answers_for_an_unknown_title(self, db):
        """The case that had no answer at all before: nothing about this title
        exists yet, and the only fact available is where it is going."""
        pid = _profile(db, "4K")
        [rid] = _libraries(db, "movies", [("4K Films", "/4k", pid)])
        assert db.quality_profile_id_for("movie", tmdb_id=999999,
                                         root_folder_id=rid) == pid

    def test_an_explicit_destination_outranks_the_titles_current_library(self, db):
        """Grabbing a 1080p-Library film into the 4K Library judges it at 4K —
        the destination is where the file ends up, so it is what counts."""
        four_k, hd = _profile(db, "4K"), _profile(db, "1080p only", "1080p")
        rids = _libraries(db, "movies", [("4K Films", "/4k", four_k),
                                         ("Films", "/films", hd)])
        _a_movie(db, root_folder_id=rids[1])
        assert db.quality_profile_id_for("movie", tmdb_id=693134) == hd
        assert db.quality_profile_id_for("movie", tmdb_id=693134,
                                         root_folder_id=rids[0]) == four_k

    def test_a_title_profile_still_wins_over_an_explicit_destination(self, db):
        pid, own = _profile(db, "4K"), _profile(db, "Hand picked", "720p")
        [rid] = _libraries(db, "movies", [("4K Films", "/4k", pid)])
        mid = _a_movie(db)
        db.set_title_quality_profile("movie", mid, own)
        assert db.quality_profile_id_for("movie", tmdb_id=693134,
                                         root_folder_id=rid) == own

    def test_an_unknown_title_and_no_destination_is_still_none(self, db):
        _profile(db, "4K")
        assert db.quality_profile_id_for("movie", tmdb_id=999999) is None

    def test_shows_resolve_the_same_way(self, db):
        pid = _profile(db, "Anime 1080p", "1080p")
        [rid] = _libraries(db, "tv", [("Anime", "/anime", pid)])
        sid = _a_show(db, root_folder_id=rid)
        assert db.quality_profile_id_for("show", library_id=sid) == pid
        assert db.quality_profile_id_for("show", tmdb_id=95396) == pid

    def test_a_junk_library_id_does_not_raise(self, db):
        assert db.quality_profile_id_for("movie", root_folder_id="not-an-id") is None


class TestTheSingleLibrarySeam:
    def test_it_reads_one_library(self, db):
        pid = _profile(db, "4K")
        [rid] = _libraries(db, "movies", [("4K Films", "/4k", pid)])
        assert db.library_quality_profile_id(rid) == pid

    def test_no_library_reads_as_none(self, db):
        assert db.library_quality_profile_id(None) is None
        assert db.library_quality_profile_id(999999) is None

    def test_it_sets_and_clears(self, db):
        pid = _profile(db, "4K")
        [rid] = _libraries(db, "movies", [("4K Films", "/4k")])
        assert db.set_library_quality_profile(rid, pid) is True
        assert db.library_quality_profile_id(rid) == pid
        assert db.set_library_quality_profile(rid, 0) is True
        assert db.library_quality_profile_id(rid) is None

    def test_an_unknown_library_is_refused(self, db):
        assert db.set_library_quality_profile(999999, 1) is False


# ── deleting a profile must not leave a Library pointing at nothing ─────────

class TestDeletingAProfile:
    def test_it_releases_the_libraries_holding_it(self, db):
        """root_folders gets this column by ALTER TABLE on an existing install,
        so it cannot carry the FK that clears movies/shows automatically."""
        pid = _profile(db, "4K")
        [rid] = _libraries(db, "movies", [("4K Films", "/4k", pid)])
        assert qp.delete_named(db, pid) is True
        assert db.library_quality_profile_id(rid) is None

    def test_and_its_titles_fall_back_to_the_global_default(self, db):
        pid = _profile(db, "4K")
        [rid] = _libraries(db, "movies", [("4K Films", "/4k", pid)])
        _a_movie(db, root_folder_id=rid)
        db.add_movie_to_wishlist(693134, "Dune")
        qp.delete_named(db, pid)
        assert db.movie_wishlist_to_download()[0]["quality_profile_id"] is None

    def test_a_different_profile_is_left_alone(self, db):
        keep, drop = _profile(db, "4K"), _profile(db, "1080p only", "1080p")
        rids = _libraries(db, "movies", [("4K Films", "/4k", keep),
                                         ("Films", "/films", drop)])
        qp.delete_named(db, drop)
        assert db.library_quality_profile_id(rids[0]) == keep
        assert db.library_quality_profile_id(rids[1]) is None


# ── the settings round trip ────────────────────────────────────────────────

class TestTheSettingsRoundTrip:
    def test_it_survives_save_and_reload(self, db):
        pid = _profile(db, "4K")
        _libraries(db, "movies", [("4K Films", "/4k", pid)])
        assert db.list_libraries("plex")["movies"][0]["default_quality_profile_id"] == pid

    def test_zero_and_blank_both_mean_no_library_default(self, db):
        pid = _profile(db, "4K")
        [rid] = _libraries(db, "movies", [("Films", "/films", pid)])
        # each falsy spelling must CLEAR a default that is already set, not just
        # fail to set one — the picker's first option is how you turn it back off
        for value in (0, "", None, "0"):
            db.set_library_quality_profile(rid, pid)
            db.save_libraries("plex", [{"id": rid, "server_title": "Films", "path": "/films",
                                        "default_quality_profile_id": value}], None, None)
            assert db.list_libraries("plex")["movies"][0]["default_quality_profile_id"] is None

    def test_garbage_is_dropped_rather_than_stored(self, db):
        db.save_libraries("plex", [{"server_title": "Films", "path": "/films",
                                    "default_quality_profile_id": "nonsense"}], None, None)
        assert db.list_libraries("plex")["movies"][0]["default_quality_profile_id"] is None

    def test_movie_libraries_get_one_too(self, db):
        """Unlike the series-type default, which is meaningless for a film. A
        Library of 4K films is the most obvious use of this setting there is."""
        pid = _profile(db, "4K")
        _libraries(db, "movies", [("4K Films", "/4k", pid)])
        assert db.list_libraries("plex")["movies"][0]["default_quality_profile_id"] == pid

    def test_it_reaches_the_api(self, client, db):
        pid = _profile(db, "4K")
        r = client.post("/api/video/libraries", json={
            "movies": [{"server_title": "4K Films", "label": "4K", "path": "/4k",
                        "default_quality_profile_id": pid}], "tv": []})
        assert r.get_json()["configured"]["movies"][0]["default_quality_profile_id"] == pid
        out = client.get("/api/video/libraries").get_json()
        assert out["configured"]["movies"][0]["default_quality_profile_id"] == pid


# ── a manual grab is judged by where it is going ───────────────────────────

class TestAGrabIsJudgedByItsDestination:
    def _pids_seen(self, monkeypatch):
        """Record every profile id the request layer resolved to."""
        import core.video.quality_profile as qpmod
        seen, real = [], qpmod.profile_by_id

        def spy(d, pid):
            seen.append(pid)
            return real(d, pid)

        monkeypatch.setattr(qpmod, "profile_by_id", spy)
        monkeypatch.setattr("core.video.slskd_search.poll_search",
                            lambda sid: {"total_files": 0, "hits": []})
        return seen

    def test_a_grab_into_a_library_uses_that_librarys_profile(self, client, db, monkeypatch):
        """The whole point of the Library default: a title nothing knows about
        yet, being sent somewhere that has an opinion."""
        seen = self._pids_seen(monkeypatch)
        pid = _profile(db, "4K")
        [rid] = _libraries(db, "movies", [("4K Films", "/4k", pid)])
        client.get("/api/video/downloads/search/poll?id=abc&scope=movie&title=Dune"
                   "&root_folder_id=%d" % rid)
        assert seen and seen[0] == pid

    def test_without_a_destination_it_is_the_global_default(self, client, db, monkeypatch):
        seen = self._pids_seen(monkeypatch)
        _profile(db, "4K")
        _libraries(db, "movies", [("4K Films", "/4k")])
        client.get("/api/video/downloads/search/poll?id=abc&scope=movie&title=Dune")
        assert seen and seen[0] is None


# ── the UI has to say which of the two settings is in force ────────────────

class TestTheSettingsEditor:
    def test_every_library_row_gets_a_profile_picker(self):
        assert "qpSel.setAttribute('data-lib-quality-profile', '')" in _SETTINGS_JS
        assert "fields.appendChild(qpWrap);" in _SETTINGS_JS

    def test_it_opens_on_the_librarys_current_choice(self):
        """Rendering it empty would read as 'no default set' on every reload,
        which is the one wrong answer a settings page must never give."""
        assert ("renderProfilePicker(qpSel, configured && configured.default_quality_profile_id)"
                in _SETTINGS_JS)

    def test_the_first_option_is_no_library_default(self):
        """Not 'Default'. Profile id 0 IS the global Default, so offering both
        would be two options that do the same thing under different names."""
        assert "no Library default" in _SETTINGS_JS

    def test_the_global_default_is_not_offered_twice(self):
        assert "list.filter(function (p) { return p.id > 0; })" in _SETTINGS_JS

    def test_the_editor_sends_the_choice_back(self):
        assert "row.querySelector('[data-lib-quality-profile]')" in _SETTINGS_JS
        assert "default_quality_profile_id: (function () {" in _SETTINGS_JS

    def test_it_is_not_trapped_inside_the_tv_only_block(self):
        """The series-type picker sits inside `if (kind === 'tv')` because a film
        has no episode numbering. This one must NOT, or exactly the Libraries the
        setting is most obviously for — the 4K film ones — silently lack it."""
        import re
        m = re.search(r"if \(kind === 'tv'\) \{(.*?)\n        \}\n", _SETTINGS_JS, re.S)
        assert m, "tv-only block missing"
        tv_only = m.group(1)
        assert "data-lib-series-type" in tv_only
        assert "data-lib-quality-profile" not in tv_only


class TestThePerTitlePicker:
    def test_default_names_what_it_inherits(self):
        """A picker reading 'Default' while the title is actually judged at 4K
        would be lying about the very setting it is showing."""
        assert "this Library uses" in _MANAGE_JS
        assert "function inheritedProfileName" in _MANAGE_JS

    def test_moving_a_title_refreshes_what_default_means(self):
        """A different Library can carry a different default."""
        _, _, after = _MANAGE_JS.partition("state.data.root_folder_id = b.root_folder_id;")
        assert after, "library setter missing"
        assert "loadQualityProfiles(state.data)" in after.split("toast(")[0]
