"""Wishlist Audit runs, and things you already own leave the wishlist.

Reported as: the tool runs but never removes shows that have already been
downloaded and imported. Three separate causes, all of which end in a scan
that reports nothing:

  1. DEDUP BLINDED IT PERMANENTLY. repair_create_finding dedups against
     findings in ANY status, including 'resolved'. Approving a stale_wishlist
     finding DELETES the row it names — so the same title landing on the
     wishlist again is a genuinely new row, but the job could never flag it a
     second time. One approval retired that episode from the audit for good.
     Fixed with ignore_resolved; 'dismissed' still suppresses, because "leave
     this one alone" has to stick.

  2. OWNED COPIES WERE ONLY FOUND BY tmdb_id. A library row that never got
     TMDB-matched is invisible to that lookup — which is precisely the row
     someone has downloaded, imported, and can see on their server while the
     audit reports nothing to clean. The wishlist row's own library_id already
     points straight at it (movies.id / shows.id, the same meaning the
     to_download queries give it), so it is now matched on either.

  3. BELOW-CUTOFF ROWS WERE SKIPPED IN SILENCE. Upgrade-until deliberately
     keeps a 720p grab under a 1080p cutoff — and keeps EVERYTHING under an
     empty 'always chase the best' cutoff. Correct, but the scan then said
     "0 new findings" against a wishlist full of owned titles, which reads as
     broken. Those skips are now counted and reported, and
     include_below_cutoff opts into flagging them.
"""

from __future__ import annotations

import pytest

from core.video.repair.worker import VideoRepairWorker
from database.video_database import VideoDatabase


@pytest.fixture()
def db(tmp_path):
    return VideoDatabase(database_path=str(tmp_path / "video_library.db"))


@pytest.fixture()
def worker(db):
    return VideoRepairWorker(db)


def _seed_show(db, *, tmdb, title="Show", resolution="1080p", sid="s1"):
    """A show whose S01E01 is downloaded and imported (has_file + a media file)."""
    return db.upsert_show_tree("plex", {
        "server_id": sid, "title": title, "tmdb_id": tmdb,
        "seasons": [{"season_number": 1, "episodes": [
            {"episode_number": 1, "title": "Pilot", "server_id": sid + "e1",
             "file": {"path": "/tv/%s.mkv" % sid, "resolution": resolution}}]}]})


def _wish_ep(db, tmdb, title="Show", **kw):
    db.add_episodes_to_wishlist(tmdb, title,
                                [{"season_number": 1, "episode_number": 1}], **kw)


def _pending(db):
    return db.repair_get_findings(status="pending")["items"]


def _wishlist(db):
    conn = db._get_connection()
    try:
        return [dict(r) for r in conn.execute(
            "SELECT kind, tmdb_id, season_number, episode_number FROM video_wishlist")]
    finally:
        conn.close()


def _log(worker):
    return " | ".join(e["text"] for e in worker._states["wishlist_audit"]["log"])


# ── 1. the same title can be cleaned more than once ──────────────────────────
def test_a_re_wishlisted_episode_is_flagged_again_after_a_previous_approval(db, worker):
    """The reported bug, at its sharpest: clean it once and the audit went
    permanently blind to that episode."""
    _seed_show(db, tmdb=77)
    _wish_ep(db, 77)

    worker._run_job("wishlist_audit", forced=True)
    first = _pending(db)
    assert len(first) == 1
    assert worker.fix_finding(first[0]["id"])["success"]
    assert _wishlist(db) == []

    _wish_ep(db, 77)                      # it comes back — re-wished, re-added, a failed grab
    worker._run_job("wishlist_audit", forced=True)
    assert len(_pending(db)) == 1, "a NEW wishlist row must be flagged like any other"


def test_the_second_finding_can_actually_be_approved_too(db, worker):
    """Flagging it again is only useful if the fix still lands."""
    _seed_show(db, tmdb=77)
    _wish_ep(db, 77)
    worker._run_job("wishlist_audit", forced=True)
    worker.fix_finding(_pending(db)[0]["id"])

    _wish_ep(db, 77)
    worker._run_job("wishlist_audit", forced=True)
    res = worker.fix_finding(_pending(db)[0]["id"])
    assert res["success"] and res["action"] == "removed"
    assert _wishlist(db) == []


def test_a_dismissed_finding_is_never_raised_again(db, worker):
    """The other half of the contract: only RESOLVED drops out of the dedup
    basis. Dismiss means 'leave this row alone', and a rescan must respect it
    rather than nag on every hourly pass."""
    _seed_show(db, tmdb=66)
    _wish_ep(db, 66)
    worker._run_job("wishlist_audit", forced=True)
    assert worker.dismiss_finding(_pending(db)[0]["id"])

    worker._run_job("wishlist_audit", forced=True)
    assert _pending(db) == []


def test_a_still_pending_finding_is_not_duplicated(db, worker):
    """Hourly re-scans must not stack copies of a finding nobody has handled."""
    _seed_show(db, tmdb=55)
    _wish_ep(db, 55)
    worker._run_job("wishlist_audit", forced=True)
    worker._run_job("wishlist_audit", forced=True)
    assert len(_pending(db)) == 1


def test_other_jobs_keep_the_strict_dedup(db):
    """ignore_resolved is opt-in — the shared helper's default must not change,
    or every job starts re-raising work the user already did."""
    kw = dict(finding_type="broken_file", entity_type="movie", entity_id="m:1",
              title="x")
    assert db.repair_create_finding("broken_files", **kw) is True
    fid = _pending(db)[0]["id"]
    db.repair_set_finding_status(fid, "resolved", action="fixed")
    assert db.repair_create_finding("broken_files", **kw) is False
    assert db.repair_create_finding("broken_files", ignore_resolved=True, **kw) is True


# ── 2. owned copies that were never TMDB-matched ─────────────────────────────
def test_an_episode_owned_under_an_unmatched_show_row_is_found(db, worker):
    """The show sits in the library with a file but no tmdb_id, so the tmdb-only
    lookup never saw it. The wishlist row's library_id points at it."""
    show_id = _seed_show(db, tmdb=None, title="Unmatched", sid="sx")
    _wish_ep(db, 99, title="Unmatched", library_id=show_id)

    worker._run_job("wishlist_audit", forced=True)
    assert len(_pending(db)) == 1


def test_a_movie_owned_under_an_unmatched_row_is_found(db, worker):
    """Same gap on the movie side."""
    mid = db.upsert_movie("plex", {"server_id": "m1", "title": "Unmatched Film",
                                   "tmdb_id": None, "file": {"path": "/m1.mkv"}})
    conn = db._get_connection()
    conn.execute("INSERT INTO media_files (movie_id, relative_path, resolution) "
                 "VALUES (?,?,?)", (mid, "/m1.mkv", "1080p"))
    conn.commit(); conn.close()
    db.add_movie_to_wishlist(1234, "Unmatched Film", year=2010, library_id=mid)

    worker._run_job("wishlist_audit", forced=True)
    assert len(_pending(db)) == 1


def test_a_wishlist_row_for_something_genuinely_missing_is_left_alone(db, worker):
    """The widened match must not start flagging rows with nothing behind them —
    a library_id of NULL can't accidentally match a library row."""
    _seed_show(db, tmdb=77)
    _wish_ep(db, 77)                       # owned
    _wish_ep(db, 4242, title="Not Owned")  # genuinely still wanted
    db.add_movie_to_wishlist(4243, "Also Not Owned", year=2020)

    worker._run_job("wishlist_audit", forced=True)
    assert [f["details"]["tmdb_id"] for f in _pending(db)] == [77]


# ── 3. below-cutoff rows: reported, and optionally flagged ───────────────────
def test_below_cutoff_rows_are_reported_as_skipped_not_silently_dropped(db, worker):
    """Upgrade-until is correct, but a bare '0 new findings' against a wishlist
    of owned titles is what made the tool look broken."""
    _seed_show(db, tmdb=88, resolution="720p")     # cutoff defaults to 1080p
    _wish_ep(db, 88)

    worker._run_job("wishlist_audit", forced=True)
    assert _pending(db) == []
    assert "1 deliberately left alone" in _log(worker)


def test_an_empty_cutoff_skips_everything_and_says_so(db, worker, monkeypatch):
    """'Always chase the best' means NO owned row ever meets the cutoff — the
    regime in which the job flags nothing at all, forever."""
    monkeypatch.setattr("core.video.quality_profile.load",
                        lambda _db: {"cutoff_resolution": ""})
    _seed_show(db, tmdb=88, resolution="2160p")
    _wish_ep(db, 88)

    worker._run_job("wishlist_audit", forced=True)
    assert _pending(db) == []
    assert "1 deliberately left alone" in _log(worker)


def test_include_below_cutoff_flags_owned_rows_whatever_the_quality(db, worker):
    """The opt-in for people who read 'downloaded and imported' as 'done'."""
    _seed_show(db, tmdb=88, resolution="720p")
    _wish_ep(db, 88)
    worker.set_job_config("wishlist_audit", settings={"include_below_cutoff": True})

    worker._run_job("wishlist_audit", forced=True)
    found = _pending(db)
    assert len(found) == 1
    assert found[0]["details"]["reason"] == "already downloaded and imported"
    assert worker.fix_finding(found[0]["id"])["success"]
    assert _wishlist(db) == []


def test_include_below_cutoff_defaults_off(db, worker):
    """Turning it on ends the upgrade hunt for those titles, so it must be a
    deliberate choice rather than something an upgrade switches on."""
    from core.video.repair import get_all_jobs
    cls = get_all_jobs()["wishlist_audit"]
    assert cls.default_settings["include_below_cutoff"] is False
    assert worker.job_config("wishlist_audit")["settings"]["include_below_cutoff"] is False


def test_the_setting_is_offered_in_the_ui(db, worker):
    """setting_options is what the Tools page renders the control from — a
    setting missing from it is unreachable without editing the database."""
    from core.video.repair import get_all_jobs
    assert get_all_jobs()["wishlist_audit"].setting_options["include_below_cutoff"] == [False, True]


def test_the_finding_links_to_the_show_that_owns_the_episode(db, worker):
    """details.library_id is what the 'View show →' button in the finding uses.
    It was only ever the wishlist row's own value, so an episode matched by
    tmdb_id alone offered no way through to the thing you already own."""
    show_id = _seed_show(db, tmdb=77)
    _wish_ep(db, 77)                       # no library_id on the row

    worker._run_job("wishlist_audit", forced=True)
    assert _pending(db)[0]["details"]["library_id"] == show_id


def test_the_detail_panel_explains_a_below_cutoff_removal_honestly(db, worker):
    """The panel's stock copy says the engine "never re-grabs owned items", which
    is untrue of a row include_below_cutoff caught — that one IS being chased.
    The renderer branches on details.reason, so the two strings must agree."""
    import pathlib
    js = (pathlib.Path(__file__).resolve().parents[2] / "webui" / "static" / "video"
          / "video-repair.js").read_text(encoding="utf-8")

    _seed_show(db, tmdb=88, resolution="720p")
    _wish_ep(db, 88)
    worker.set_job_config("wishlist_audit", settings={"include_below_cutoff": True})
    worker._run_job("wishlist_audit", forced=True)
    reason = _pending(db)[0]["details"]["reason"]

    branch = js.split("function staleDetailHTML(", 1)[1].split("\n    }", 1)[0]
    assert "d.reason === '%s'" % reason in branch, \
        "the renderer branches on a reason string the job no longer emits"
    assert "ends " in branch and "the hunt for a better copy" in branch


def test_quality_unreadable_rows_are_still_flagged_by_default(db, worker):
    """Unchanged behaviour, pinned: no media_files row means the upgrader can
    never judge it, so it is dead weight regardless of the cutoff."""
    db.upsert_show_tree("plex", {
        "server_id": "sd", "title": "No Probe", "tmdb_id": 111,
        "seasons": [{"season_number": 1, "episodes": [
            {"episode_number": 1, "title": "Pilot", "server_id": "sde1",
             "file": {"path": "/tv/d.mkv"}}]}]})
    _wish_ep(db, 111, title="No Probe")

    worker._run_job("wishlist_audit", forced=True)
    found = _pending(db)
    assert len(found) == 1
    assert "can't be read" in found[0]["details"]["reason"]
