"""Lock automatic edits — an unattended import must not be able to damage
content you have already curated.

Reported as: "a automatic download that has the wrong name or season cannot
screw up existing content, and instead error out pending manual checking."

The danger is real and not hypothetical. ``plan_import`` answers "upgrade" when
a release scores better than the copy already on disk, and an upgrade DELETES
that copy. It takes the identity to file under from the download row
(``search_ctx``), and its wrong-episode guard can only fire when the release
NAME carries an SxxExx to disagree with — so a release whose name says nothing
(fansub absolute numbering, a bare title, a mis-titled scene release) is filed
wherever the row claims, and a wrong season number there is enough to replace a
good file with a different episode.

The lock is deliberately blunt: a locked show, season or movie refuses EVERY
unattended placement, a brand-new episode included. Half-measures do not help
here — a release that mis-identified itself is exactly the one whose "this is a
new episode" claim cannot be trusted either.

Three properties carry the design:

  * it fails CLOSED for imports but OPEN for lookups. A locked item refuses the
    import; a DB error while resolving the lock lets the import proceed, because
    a lock that silently swallowed downloads on a transient error would be worse
    than one that missed a protection it could not prove was wanted.
  * ``force`` bypasses it. A manual placement IS the "pending manual checking"
    the lock exists to demand; blocking it too would leave no way to act on the
    file at all.
  * the release is not condemned. A lock reject is not tagged ``bad_release``,
    so it is never blocklisted — it may be perfectly good and merely pointed at
    protected content.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from core.video.importer import plan_import, run_import, run_season_import
from database.video_database import VideoDatabase

_ROOT = Path(__file__).resolve().parent.parent


@pytest.fixture
def db(tmp_path):
    import database.video_database as mod
    if hasattr(mod, "_initialized_paths"):
        mod._initialized_paths.clear()
    d = VideoDatabase(str(tmp_path / "v.db"))
    seasons = [{"season_number": s,
                "episodes": [{"episode_number": e, "title": "E%d" % e} for e in range(1, 4)]}
               for s in (1, 2)]
    d._show_id = d.upsert_show_tree("plex", {"server_id": "s1", "title": "My Show",
                                             "tmdb_id": 500, "seasons": seasons})
    d._movie_id = d.upsert_movie("plex", {"server_id": "m1", "tmdb_id": 603,
                                          "title": "The Matrix", "year": 1999})
    return d


# ── what the lock means, at the DB layer ────────────────────────────────────

class TestLockSemantics:
    def test_nothing_is_locked_by_default(self, db):
        """Every existing install upgrades into this feature switched off."""
        assert db.import_lock_reason("show", 500, 1) is None
        assert db.import_lock_reason("movie", 603) is None

    def test_a_season_lock_is_narrow(self, db):
        """The whole reason a season lock exists alongside the show lock: seal a
        finished season while the show keeps acquiring the airing one."""
        assert db.set_season_import_lock(db._show_id, 1, True) is True
        assert "season 1" in db.import_lock_reason("show", 500, 1)
        assert db.import_lock_reason("show", 500, 2) is None
        assert db.import_lock_reason("show", 500) is None

    def test_a_show_lock_covers_every_season(self, db):
        assert db.set_import_lock("show", db._show_id, True) is True
        for season in (1, 2, 99, None):
            assert db.import_lock_reason("show", 500, season)

    def test_a_movie_lock(self, db):
        assert db.set_import_lock("movie", db._movie_id, True) is True
        assert "Matrix" in db.import_lock_reason("movie", 603)

    def test_a_title_not_in_the_library_is_never_locked(self, db):
        """Nothing there to protect, and refusing it would block every FIRST
        download — the one case where no lock can possibly exist yet."""
        assert db.import_lock_reason("show", 999999, 1) is None
        assert db.import_lock_reason("movie", 999999) is None

    def test_unlocking_puts_it_back(self, db):
        db.set_import_lock("show", db._show_id, True)
        db.set_import_lock("show", db._show_id, False)
        assert db.import_lock_reason("show", 500, 1) is None

    def test_the_reason_names_the_title_and_the_season(self, db):
        """It becomes the import_failed error the user reads, so it has to say
        which lock stopped it — a bare 'locked' on a busy queue is useless."""
        db.set_season_import_lock(db._show_id, 2, True)
        reason = db.import_lock_reason("show", 500, 2)
        assert "My Show" in reason and "season 2" in reason

    def test_bad_input_never_raises(self, db):
        assert db.import_lock_reason("show", None) is None
        assert db.import_lock_reason("show", "nonsense") is None
        assert db.set_import_lock("nonsense", 1, True) is False
        assert db.set_import_lock("show", 999999, True) is False
        assert db.set_season_import_lock(db._show_id, 99, True) is False

    def test_locked_seasons_lists_them(self, db):
        db.set_season_import_lock(db._show_id, 2, True)
        assert db.locked_seasons(db._show_id) == [2]

    def test_the_detail_payloads_carry_it(self, db):
        """The toggles paint their state from these."""
        db.set_import_lock("show", db._show_id, True)
        db.set_season_import_lock(db._show_id, 1, True)
        d = db.show_detail(db._show_id)
        assert d["import_locked"] is True
        assert {s["season_number"]: s["import_locked"] for s in d["seasons"]} == {1: True, 2: False}
        assert db.movie_detail(db._movie_id)["import_locked"] is False


# ── the import itself ───────────────────────────────────────────────────────

def _dl(season=1, episode=2, release="My.Show.S01E02.2160p.BluRay.x265"):
    return {"kind": "episode", "media_id": "500", "media_source": "tmdb",
            "target_dir": "/tv", "release_title": release, "size_bytes": 8_000_000_000,
            "search_ctx": json.dumps({"scope": "episode", "title": "My Show",
                                      "season": season, "episode": episode})}


# The copy already on disk — worse than the release above, so the importer's
# normal answer is "upgrade", which deletes it.
_OWNED = ["My Show - S01E02 - [WEBDL-720p].mkv"]
_SRC = "/downloads/My.Show.S01E02.2160p.BluRay.x265.mkv"


class TestTheGate:
    def test_without_a_lock_it_deletes_the_existing_file(self):
        """The behaviour being protected against — established first, or the
        rest of this file proves nothing."""
        plan = plan_import(_dl(), _SRC, list_dir=lambda d: _OWNED)
        assert plan["action"] == "upgrade"
        assert "WEBDL-720p" in plan["replace_path"]

    def test_a_lock_refuses_it(self):
        plan = plan_import(_dl(), _SRC, list_dir=lambda d: _OWNED,
                           lock_reason="My Show season 1 is locked")
        assert plan["action"] == "reject"
        assert "My Show season 1 is locked" in plan["reason"]
        assert "by hand" in plan["reason"], "the reject must say how to proceed"

    def test_it_refuses_a_brand_new_episode_too(self):
        """Not only replacements. A release that mis-identified itself cannot be
        trusted when it claims to be something you do not have, either."""
        plan = plan_import(_dl(), _SRC, list_dir=lambda d: [], lock_reason="locked")
        assert plan["action"] == "reject"

    def test_it_decides_before_a_destination_exists(self):
        """Nothing about where the file would go is computed for a locked item —
        no folder is named, so nothing can be created or touched by accident."""
        plan = plan_import(_dl(), _SRC, list_dir=lambda d: _OWNED, lock_reason="locked")
        assert "dest" not in plan and "replace_path" not in plan

    def test_it_does_not_condemn_the_release(self):
        """A lock reject must not blocklist: the release may be fine and merely
        pointed at protected content, and blocklisting would stop it being picked
        again — including for the title it actually belongs to."""
        plan = plan_import(_dl(), _SRC, list_dir=lambda d: _OWNED, lock_reason="locked")
        assert plan["bad_release"] is False

    def test_a_manual_placement_goes_through(self):
        """The "pending manual checking" half of the report. With force blocked
        too there would be no way to act on the file at all."""
        plan = plan_import(_dl(), _SRC, list_dir=lambda d: _OWNED, lock_reason="locked",
                           force=True,
                           override={"scope": "episode", "title": "My Show", "season": 1,
                                     "episode": 2, "target_dir": "/tv"})
        assert plan["action"] == "upgrade"

    def test_an_empty_reason_is_not_a_lock(self):
        """The resolver returns None (or '') for 'no lock'; neither may read as
        one, or every import everywhere would stop."""
        for falsy in (None, ""):
            assert plan_import(_dl(), _SRC, list_dir=lambda d: [],
                               lock_reason=falsy)["action"] == "import"

    def test_the_wrong_season_case_from_the_report(self):
        """The release name carries no SxxExx, so the importer's wrong-episode
        guard cannot fire and the row's (wrong) season is used unchallenged.
        Unlocked, that replaces a good file in a season the release never
        mentioned. This is the scenario the lock exists for."""
        dl = _dl(season=2, episode=2, release="My.Show.2160p.BluRay.x265")
        owned = ["My Show - S02E02 - [WEBDL-720p].mkv"]
        loose = plan_import(dl, "/downloads/My.Show.2160p.BluRay.x265.mkv",
                            list_dir=lambda d: owned)
        assert loose["action"] == "upgrade" and "S02E02" in loose["replace_path"]
        locked = plan_import(dl, "/downloads/My.Show.2160p.BluRay.x265.mkv",
                             list_dir=lambda d: owned, lock_reason="season 2 is locked")
        assert locked["action"] == "reject"


class _FS:
    def __init__(self, existing=None):
        self.existing = existing or []
        self.copied, self.moved, self.removed, self.made = [], [], [], []

    def list_dir(self, path):
        return list(self.existing)

    def makedirs(self, path):
        self.made.append(str(path))

    def copy(self, src, dst):
        self.copied.append((src, dst))

    def move(self, src, dst):
        self.moved.append((src, dst))

    def remove(self, path):
        self.removed.append(path)


def test_run_import_forwards_the_lock_and_writes_nothing():
    """The reject has to reach the FILESYSTEM layer, not just the plan."""
    fs = _FS(_OWNED)
    patch = run_import(_dl(), _SRC, fs=fs, lock_reason="locked")
    assert patch["status"] == "import_failed"
    assert "locked" in patch["error"]
    assert fs.copied == [] and fs.removed == [] and fs.moved == []
    # the file is left where it is, so manual import can find it
    assert patch["dest_path"] == _SRC


def test_a_season_pack_is_judged_per_season():
    """A pack spanning a locked and an unlocked season imports the unlocked half
    and refuses the rest — which is why the check is a callable taking the season
    rather than one verdict for the whole pack."""
    files = ["/dl/pack/My.Show.S01E01.1080p.WEB.mkv", "/dl/pack/My.Show.S02E01.1080p.WEB.mkv"]
    dl = {"kind": "episode", "media_id": "500", "media_source": "tmdb",
          "target_dir": "/tv", "release_title": "My.Show.S01-S02.1080p.WEB",
          "size_bytes": 20_000_000_000,
          "search_ctx": json.dumps({"scope": "season", "title": "My Show", "season": 1})}
    fs = _FS([])
    patch = run_season_import(dl, "/dl/pack", fs=fs, lister=lambda d: files,
                              lock_check=lambda sn: "season 1 is locked" if sn == 1 else None)
    copied = " ".join(dst for _src, dst in fs.copied)
    assert "S02E01" in copied, "the unlocked season should still import"
    assert "S01E01" not in copied, "the locked season must not be written"


# ── the wiring a unit test cannot see ───────────────────────────────────────

def _src(rel):
    return (_ROOT / rel).read_text(encoding="utf-8")


def test_the_monitor_asks_for_the_lock_on_both_import_paths():
    """Single-file and pack. Missing either leaves that path unprotected, which
    is indistinguishable from not having built this at all."""
    body = _src("core/video/download_monitor.py")
    assert "lock_reason=_import_lock(db, dl)" in body
    assert "lock_check=lambda sn: _import_lock(db, dl, season=sn)" in body


def test_the_lock_lookup_fails_open():
    """A DB hiccup must not silently swallow downloads. Failing open means the
    worst case is the protection not applying, never imports vanishing."""
    body = _src("core/video/download_monitor.py")
    fn = body[body.index("def _import_lock("):]
    fn = fn[:fn.index("\ndef ", 1)]
    assert "return None" in fn.split("except")[1]


def test_the_routes_are_admin_gated():
    """Locking decides whether the unattended importer may write to a title —
    library management, the same class as the other detail writes. And the path
    does NOT end with '/lock', so the existing suffix does not cover it."""
    gate = _src("api/video/__init__.py")
    admin_block = gate.split("if admin and not is_admin:")[0]
    assert '"/import-lock"' in admin_block
    assert not "/detail/movie/1/import-lock".endswith("/lock"), \
        "if this ever became true the explicit entry above could be dropped"


@pytest.mark.parametrize("route", [
    '@bp.route("/detail/<kind>/<int:item_id>/import-lock", methods=["POST"])',
    '@bp.route("/detail/show/<int:show_id>/season/<int:season_number>/import-lock",',
])
def test_both_endpoints_exist(route):
    assert route in _src("api/video/detail.py")


def test_the_manage_panel_has_the_toggle():
    js = _src("webui/static/video/video-manage-panel.js")
    assert "data-vmg-import-lock" in js
    assert "d.import_locked" in js, "the toggle must paint its stored state"
    assert "toggleImportLock(lk)" in js, "the toggle is not wired to anything"
    assert "/import-lock" in js


def test_the_season_toggle_is_rendered_and_wired():
    js = _src("webui/static/video/video-detail.js")
    assert "seasonLockHTML(season) + seasonBar" in js, "the season lock is never rendered"
    assert "data-vd-season-lock" in js
    assert "toggleSeasonLock(seasonLock)" in js
    assert "season/' + sn + '/import-lock'" in js


def test_the_season_toggle_is_hidden_where_it_cannot_work():
    """A TMDB preview has no season row to lock, YouTube has no seasons, and the
    endpoint refuses non-admins — a control that always fails is worse than none
    (the dead end tests/test_video_detail_manage_is_admin_only.py exists for)."""
    js = _src("webui/static/video/video-detail.js")
    fn = js[js.index("function seasonLockHTML("):]
    fn = fn[:fn.index("\n    }")]
    assert "data.source === 'tmdb'" in fn
    assert "data.source === 'youtube'" in fn
    assert "isAdminUser()" in fn


# ── the endpoints, exercised for real ───────────────────────────────────────

def _api(tmp_path, monkeypatch, *, is_admin=True):
    import api.video as videoapi
    import core.video.sources as sources
    import database.video_database as mod
    from flask import Flask, g
    monkeypatch.setattr(sources, "resolve_video_server", lambda *a, **k: "plex")
    if hasattr(mod, "_initialized_paths"):
        mod._initialized_paths.clear()
    vdb = VideoDatabase(database_path=str(tmp_path / "api.db"))
    videoapi._video_db = vdb
    app = Flask(__name__)
    app.register_blueprint(videoapi.create_video_blueprint(), url_prefix="/api/video")

    @app.before_request
    def _profile():
        g.profile_id = 1 if is_admin else 7
        g.is_admin = is_admin
        g.can_download = True
        g.allowed_sides = "both"

    seasons = [{"season_number": s, "episodes": [{"episode_number": 1, "title": "E1"}]}
               for s in (1, 2)]
    show_id = vdb.upsert_show_tree("plex", {"server_id": "s1", "title": "My Show",
                                            "tmdb_id": 500, "seasons": seasons})
    return app.test_client(), vdb, show_id, videoapi


class TestEndpoints:
    def test_locking_and_unlocking_a_show_round_trips(self, tmp_path, monkeypatch):
        client, vdb, show_id, videoapi = _api(tmp_path, monkeypatch)
        try:
            res = client.post("/api/video/detail/show/%d/import-lock" % show_id,
                              json={"locked": True})
            assert res.status_code == 200 and res.get_json()["import_locked"] is True
            assert vdb.show_detail(show_id)["import_locked"] is True
            client.post("/api/video/detail/show/%d/import-lock" % show_id, json={"locked": False})
            assert vdb.show_detail(show_id)["import_locked"] is False
        finally:
            videoapi._video_db = None

    def test_locking_one_season_leaves_the_others_alone(self, tmp_path, monkeypatch):
        client, vdb, show_id, videoapi = _api(tmp_path, monkeypatch)
        try:
            res = client.post("/api/video/detail/show/%d/season/2/import-lock" % show_id,
                              json={"locked": True})
            assert res.status_code == 200 and res.get_json()["season"] == 2
            assert vdb.import_lock_reason("show", 500, 1) is None
            assert vdb.import_lock_reason("show", 500, 2)
        finally:
            videoapi._video_db = None

    @pytest.mark.parametrize("url,expect", [
        ("/api/video/detail/show/%d/season/99/import-lock", 404),
        ("/api/video/detail/nonsense/1/import-lock", 400),
    ])
    def test_bad_targets_are_refused_clearly(self, tmp_path, monkeypatch, url, expect):
        client, _vdb, show_id, videoapi = _api(tmp_path, monkeypatch)
        try:
            got = client.post(url % show_id if "%d" in url else url, json={"locked": True})
            assert got.status_code == expect
        finally:
            videoapi._video_db = None

    @pytest.mark.parametrize("url", [
        "/api/video/detail/show/%d/import-lock",
        "/api/video/detail/show/%d/season/1/import-lock",
    ])
    def test_a_non_admin_cannot_lock_or_unlock(self, tmp_path, monkeypatch, url):
        """The gate is the authority, not the hidden toggle. Someone who could
        flip these could also UNLOCK content an admin sealed, which is the more
        interesting direction."""
        client, vdb, show_id, videoapi = _api(tmp_path, monkeypatch, is_admin=False)
        try:
            res = client.post(url % show_id, json={"locked": True})
            assert res.status_code == 403
            assert res.get_json()["error"] == "Admin only."
            assert vdb.show_detail(show_id)["import_locked"] is False
        finally:
            videoapi._video_db = None
