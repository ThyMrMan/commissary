"""Rename a title's existing files before importing into it.

Reported as: "a show won't have an episode get imported into a brand new folder
with the new naming scheme, while the original seasons sit in a differently
named folder because it hadn't been renamed to match the new scheme."

``core/video/mass_rename.py`` opens by naming this exact hazard — templates
"only ever applied at import time, so a template change forked your library into
two naming eras" — and closes it with a MANUAL preview/apply. That still leaves
the fork open between changing a template and remembering to run the rename,
and the thing most likely to happen in that window is an import: an airing show
gets an episode weekly, rendered with today's template, landing in a folder
named today's way while every earlier season keeps the old name. The media
server then sees two shows.

So the rename runs on the way in, scoped to the one title being imported into.

Deliberate shape:

  * scoped to the title, never the library. An import is not the moment to start
    a library-wide job, and the only fork that matters here is this show's.
  * a title not in the library is skipped — there is nothing to rename, and that
    is the case for every first-ever download.
  * failures are IGNORED and the import proceeds. mass_rename.apply is
    collision-safe and reports what it skipped; a rename that could not happen
    leaves exactly the split this avoids, which is no worse than not having
    tried, whereas refusing the import would strand a good download.
  * on by default. It is a no-op for a library already matching its template,
    and the split it prevents is tedious to unpick by hand.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from core.video import download_monitor as dm
from core.video import organization
from database.video_database import VideoDatabase

_ROOT = Path(__file__).resolve().parent.parent


@pytest.fixture
def db(tmp_path):
    import database.video_database as mod
    if hasattr(mod, "_initialized_paths"):
        mod._initialized_paths.clear()
    d = VideoDatabase(str(tmp_path / "v.db"))
    seasons = [{"season_number": 1, "episodes": [{"episode_number": 1, "title": "E1"}]}]
    d._show_id = d.upsert_show_tree("plex", {"server_id": "s1", "title": "My Show",
                                             "tmdb_id": 500, "seasons": seasons})
    d._movie_id = d.upsert_movie("plex", {"server_id": "m1", "tmdb_id": 603,
                                          "title": "The Matrix", "year": 1999})
    return d


@pytest.fixture
def calls(monkeypatch):
    """Record every mass_rename.apply the hook makes."""
    from core.video import mass_rename
    seen = []

    def _apply(keys=None, *, scope=None, template=None):
        seen.append({"keys": keys, "scope": scope, "template": template})
        return {"status": "completed", "renamed": 1, "skipped": 0, "failures": []}

    monkeypatch.setattr(mass_rename, "apply", _apply)
    return seen


def _episode_dl(tmdb_id=500):
    return {"kind": "episode", "media_id": str(tmdb_id), "media_source": "tmdb",
            "title": "My Show",
            "search_ctx": json.dumps({"scope": "episode", "season": 1, "episode": 2})}


ON = {"rename_before_import": True}


# ── the setting ─────────────────────────────────────────────────────────────

class TestTheSetting:
    def test_it_defaults_to_on(self):
        """Asked for explicitly. It is a no-op on a tidy library, so the cost of
        being wrong in this direction is nil."""
        assert organization.default_settings()["rename_before_import"] is True
        assert organization.normalize({})["rename_before_import"] is True

    def test_it_can_be_turned_off(self):
        assert organization.normalize({"rename_before_import": False})["rename_before_import"] is False

    def test_it_survives_a_settings_round_trip(self, db):
        """The Settings page posts the whole object back; a key that normalize
        drops would silently re-enable itself on every save."""
        organization.save(db, {**organization.default_settings(),
                               "rename_before_import": False})
        assert organization.load(db)["rename_before_import"] is False


# ── when it runs ────────────────────────────────────────────────────────────

class TestWhenItRuns:
    def test_it_renames_the_title_being_imported_into(self, db, calls):
        dm._rename_before_import(db, _episode_dl(), ON)
        assert [c["scope"] for c in calls] == [("show", db._show_id)]

    def test_it_is_scoped_to_that_title_only(self, db, calls):
        """A library-wide rename at import time would be a long job kicked off by
        an unrelated download — and could rename titles the user never touched."""
        dm._rename_before_import(db, _episode_dl(), ON)
        assert calls[0]["scope"] is not None, "an unscoped apply renames the whole library"
        assert calls[0]["keys"] is None, "the whole scoped plan is what needs applying"
        assert calls[0]["template"] is None, "must use the SAVED template, not an override"

    def test_a_movie_scopes_as_a_movie(self, db, calls):
        dm._rename_before_import(db, {"kind": "movie", "media_id": "603",
                                      "media_source": "tmdb", "title": "The Matrix",
                                      "search_ctx": json.dumps({"scope": "movie"})}, ON)
        assert calls[0]["scope"] == ("movie", db._movie_id)

    def test_it_does_nothing_when_switched_off(self, db, calls):
        dm._rename_before_import(db, _episode_dl(), {"rename_before_import": False})
        assert calls == []

    def test_absent_settings_still_mean_on(self, db, calls):
        """The monitor loads settings best-effort and can hand over defaults."""
        dm._rename_before_import(db, _episode_dl(), {})
        assert len(calls) == 1

    def test_a_title_not_in_the_library_is_skipped(self, db, calls):
        """Nothing on disk to rename — and this is every first-ever download, so
        it must not cost a scan."""
        dm._rename_before_import(db, _episode_dl(tmdb_id=999999), ON)
        assert calls == []

    def test_a_download_with_no_identity_is_skipped(self, db, calls):
        dm._rename_before_import(db, {"kind": "episode"}, ON)
        assert calls == []


# ── failures never cost the import ──────────────────────────────────────────

class TestFailuresAreIgnored:
    def test_a_crash_is_swallowed(self, db, monkeypatch):
        from core.video import mass_rename

        def _boom(*a, **k):
            raise OSError("disk went away")

        monkeypatch.setattr(mass_rename, "apply", _boom)
        dm._rename_before_import(db, _episode_dl(), ON)   # must simply return

    def test_a_manual_rename_holding_the_lock_is_not_an_error(self, db, monkeypatch):
        """mass_rename has one global lock. If the user is running the full
        library rename right now, that pass covers this title too."""
        from core.video import mass_rename
        monkeypatch.setattr(mass_rename, "apply",
                            lambda *a, **k: {"status": "skipped", "reason": "already_running"})
        dm._rename_before_import(db, _episode_dl(), ON)

    def test_a_partial_failure_is_reported_but_not_raised(self, db, monkeypatch, caplog):
        """The user asked for this explicitly: if the rename fails, ignore it and
        import anyway. It is still said out loud, because a silent half-rename is
        how you end up with the split this feature exists to prevent."""
        import logging
        from core.video import mass_rename
        monkeypatch.setattr(mass_rename, "apply", lambda *a, **k: {
            "status": "completed", "renamed": 1, "skipped": 1,
            "failures": [{"key": "e:1", "reason": "destination already exists"}]})
        caplog.set_level(logging.WARNING, logger="soulsync.video.download_monitor")
        dm._rename_before_import(db, _episode_dl(), ON)
        msgs = [r.getMessage() for r in caplog.records
                if r.name == "soulsync.video.download_monitor"]
        assert any("importing anyway" in m for m in msgs), msgs

    def test_a_None_result_is_survivable(self, db, monkeypatch):
        from core.video import mass_rename
        monkeypatch.setattr(mass_rename, "apply", lambda *a, **k: None)
        dm._rename_before_import(db, _episode_dl(), ON)


# ── the wiring ──────────────────────────────────────────────────────────────

def _src(rel):
    return (_ROOT / rel).read_text(encoding="utf-8")


def test_it_runs_BEFORE_the_import():
    """The entire point. Renaming after the file has landed would leave the new
    episode in the new-style folder and rename the old seasons around it — the
    same split, just reached from the other side."""
    body = _src("core/video/download_monitor.py")
    org = body[body.index("def organize(dl, src):"):]
    org = org[:org.index("return patch")]
    assert "_rename_before_import(db, dl, settings)" in org, "the hook is never called"
    assert org.index("_rename_before_import") < org.index("run_import("), \
        "the rename must happen before the file is placed"
    assert org.index("_rename_before_import") < org.index("run_season_import("), \
        "a season pack must be renamed around too"


def test_the_settings_page_exposes_it():
    """A default-on behaviour that silently moves files needs to be visible and
    switchable, or it is indistinguishable from the app doing it on a whim."""
    html = _src("webui/index.html")
    assert 'id="vo-rename-first"' in html
    js = _src("webui/static/video/video-settings.js")
    assert "chk('vo-rename-first', _videoOrg.rename_before_import);" in js, \
        "the checkbox never shows its stored state"
    assert "rename_before_import: on('vo-rename-first')," in js, \
        "the checkbox is never saved"
