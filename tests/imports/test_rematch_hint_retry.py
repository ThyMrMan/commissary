"""Re-identifying a track again after an attempt that did not file it.

The per-track Re-identify copies the library file into the staging root as
``<name> [reid-<track id>]<ext>`` and writes a hint naming the release the user
chose. A hint is consumed only when its import succeeds, so an attempt that
fails leaves the hint pending and the copy in staging.

Asking again stages the same file under the same name. The auto-import worker
keys a candidate on its files' names and sizes, so the new copy had the folder
hash of the attempt before it -- which the import history already records, and
``_is_already_processed`` skipped it for good. The new hint stayed pending and
nothing happened. The same went for re-identifying a track a second time after
a first Re-identify that completed without changing the file's name or size.

These run the real scanner, the real history and hint tables and real tagged
FLACs; only identification and matching -- the network edges -- are stubbed.
"""

from __future__ import annotations

import os

import pytest

from core.auto_import_worker import AutoImportWorker
from core.imports.rematch_hints import RematchHint, create_hint, quick_file_signature
from database.music_database import MusicDatabase
from tests.imports.test_rematch_hint_grouping import _Config, _InlineExecutor, _stage_copy


class _AutoImport:
    """The worker as it runs, with the network edges stubbed.

    Identification would read the copy's tags, which name the album it is
    leaving; matching fetches whichever release the identification names, and a
    release in ``unmatchable`` has no track this file matches."""

    def __init__(self, tmp_path, monkeypatch):
        self.root = str(tmp_path)
        self.db = MusicDatabase(os.path.join(self.root, "music.db"))
        self.staging = os.path.join(self.root, "Staging")
        self.library = os.path.join(self.root, "Library", "Old Album")
        os.makedirs(self.staging)
        os.makedirs(self.library)
        self.filed = []           # (staged file, release) for every file filed
        self.matched = []         # every release matching was asked for
        self.unmatchable = set()
        self.filing_fails = False
        self._tracks = {}
        self.worker = AutoImportWorker(database=self.db, staging_path=self.staging,
                                       process_callback=self._post_process,
                                       config_manager=_Config())
        self.worker._executor = _InlineExecutor()
        monkeypatch.setattr("core.imports.side_effects.is_active_media_server_ready",
                            lambda: (True, ""))
        monkeypatch.setattr(self.worker, "_identify_folder", self._identify_from_tags)
        monkeypatch.setattr(self.worker, "_match_tracks", self._match_tracks)

    # ── what the user, and the clock, do ──
    def reidentify(self, track_id, number, title, release):
        """What /api/reidentify/apply does: copy the library file into staging,
        then write the hint naming the chosen release."""
        staged = _stage_copy(self.staging, self.library, track_id, number, title)
        self._tracks[staged] = (number, title)
        conn = self.db._get_connection()
        try:
            hint_id = create_hint(conn.cursor(), RematchHint(
                staged_path=staged, content_hash=quick_file_signature(staged),
                source="spotify", album_id=release, album_name=release.title(),
                artist_id="artist-1", artist_name="Artist", album_type="album",
                track_id="%s-%d" % (release, number), track_title=title,
                track_number=number, disc_number=1,
            ))
            conn.commit()
        finally:
            conn.close()
        return staged, hint_id

    def an_hour_passes(self):
        """Move everything written so far an hour into the past. Timestamps have
        one-second resolution and a test runs inside one second, where in use a
        person sees an attempt fail before asking again."""
        for table in ("auto_import_history", "rematch_hints"):
            self.execute("UPDATE %s SET created_at = datetime(created_at, '-1 hour')" % table)

    def scan(self, times=2):
        """Scan cycles. The first sight of a file only records it; it is imported
        once it has held still for a cycle."""
        for _ in range(times):
            self.worker._scan_and_submit()

    # ── reading back ──
    def execute(self, sql, params=()):
        conn = self.db._get_connection()
        try:
            rows = [dict(r) for r in conn.execute(sql, params).fetchall()]
            conn.commit()
            return rows
        finally:
            conn.close()

    def history(self):
        return [r["status"] for r in self.execute("SELECT status FROM auto_import_history ORDER BY id")]

    def hint_status(self, hint_id):
        return self.execute("SELECT status FROM rematch_hints WHERE id = ?", (hint_id,))[0]["status"]

    # ── the stubbed edges ──
    def _post_process(self, context_key, context, file_path):
        if self.filing_fails:
            raise OSError("the library disk is full")
        album = context["spotify_album"]
        self.filed.append((file_path, album["id"]))
        context["_final_processed_path"] = os.path.join(
            self.root, "Library", album["name"], os.path.basename(file_path))

    @staticmethod
    def _identify_from_tags(candidate):
        return {"album_id": "old-album", "album_name": "Old Album", "artist_name": "Artist",
                "source": "spotify", "method": "tags"}

    def _match_tracks(self, candidate, identification):
        release = identification["album_id"]
        self.matched.append(release)
        if release in self.unmatchable:
            return None
        matches = []
        for f in candidate.audio_files:
            number, title = self._tracks[f]
            matches.append({"file": f, "confidence": 1.0, "track": {
                "id": "%s-%d" % (release, number), "name": title,
                "track_number": number, "disc_number": 1}})
        return {"matches": matches, "unmatched_files": [], "confidence": 1.0,
                "matched_count": len(matches), "total_tracks": 12,
                "album_data": {"id": release, "name": identification["album_name"],
                               "total_tracks": 12}}


@pytest.fixture
def imports(tmp_path, monkeypatch):
    return _AutoImport(tmp_path, monkeypatch)


@pytest.mark.parametrize("first_attempt", ["needs_identification", "failed", "completed"])
def test_asking_again_files_the_track_under_the_new_choice(imports, first_attempt):
    """``needs_identification``: the chosen release has no track this file
    matches. ``failed``: it matched, but filing it failed. ``completed``: it was
    filed, and the track's file kept its name and size -- as re-filing a track to
    the release it is already in does."""
    staged, _first = imports.reidentify(101, 1, "Intro", "first-choice")
    if first_attempt == "needs_identification":
        imports.unmatchable.add("first-choice")
    imports.filing_fails = first_attempt == "failed"
    imports.scan()
    assert imports.history() == [first_attempt]
    imports.filing_fails = False
    imports.an_hour_passes()

    again, second = imports.reidentify(101, 1, "Intro", "second-choice")
    imports.scan()

    assert again == staged
    assert imports.filed[-1:] == [(staged, "second-choice")], imports.history()
    assert imports.hint_status(second) == "consumed"
    assert imports.history() == [first_attempt, "completed"]
    hashes = {r["folder_hash"] for r in imports.execute("SELECT folder_hash FROM auto_import_history")}
    assert len(hashes) == 1, "the retry is expected to share the first attempt's folder hash"

    filed = list(imports.filed)
    imports.scan(3)
    assert imports.filed == filed, "the track was filed again with nobody asking"
    assert imports.history() == [first_attempt, "completed"]


def test_an_attempt_that_failed_is_not_tried_again_until_the_user_asks(imports):
    """The hint an attempt used is older than the history row that attempt
    wrote, so a failure never retries itself, scan after scan."""
    imports.reidentify(101, 1, "Intro", "first-choice")
    imports.unmatchable.add("first-choice")
    imports.an_hour_passes()          # the hint is strictly older than the attempt

    imports.scan(5)

    assert imports.matched == ["first-choice"]
    assert imports.history() == ["needs_identification"]


def test_a_retry_that_fails_too_is_not_tried_again_on_its_own(imports):
    """The LATEST attempt is the one a hint must be newer than. The retry's own
    hint is newer than the first attempt, so measured against that one it would
    be tried again, scan after scan, for good."""
    imports.reidentify(101, 1, "Intro", "first-choice")
    imports.unmatchable.update({"first-choice", "second-choice"})
    imports.scan()
    imports.an_hour_passes()
    imports.reidentify(101, 1, "Intro", "second-choice")

    imports.scan(5)

    assert imports.matched == ["first-choice", "second-choice"]
    assert imports.history() == ["needs_identification", "needs_identification"]


def test_asking_again_for_another_track_leaves_a_failed_one_alone(imports):
    intro, _ = imports.reidentify(101, 1, "Intro", "first-choice")
    imports.unmatchable.add("first-choice")
    imports.scan()
    imports.an_hour_passes()

    song, _ = imports.reidentify(102, 2, "Song", "second-choice")
    imports.scan(3)

    assert imports.filed == [(song, "second-choice")]
    assert imports.matched.count("first-choice") == 1, "the failed Intro was tried again"


def test_a_retry_check_that_cannot_run_leaves_the_file_as_tried(imports):
    """Fail-safe the other way from the hint lookups: an error must not turn a
    file already tried into a retry on every scan."""
    imports.reidentify(101, 1, "Intro", "first-choice")
    imports.unmatchable.add("first-choice")
    imports.scan()
    imports.an_hour_passes()
    imports.reidentify(101, 1, "Intro", "second-choice")
    (candidate,) = imports.worker._enumerate_folders(imports.staging)
    assert imports.worker._is_rematch_retry(candidate), "precondition: this is a retry"

    imports.execute("DROP TABLE rematch_hints")

    assert imports.worker._is_rematch_retry(candidate) is False
