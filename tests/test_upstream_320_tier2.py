"""Four upstream 3.2.0 fixes, each verified as still broken here before adapting.

The other four items in that release were checked and NOT taken:

  * #1124 (Jellyfin hardcoded to localhost:8096) does not exist in this fork —
    the only occurrences are settings-input placeholders and a network-scanner
    default port.
  * #1127 (tools ignored custom templates) and #1109 (upgrades landing in the
    library root) were already fixed here — reorganize was rewritten to use the
    shared template path, and ``build_final_path_for_track`` routes through
    ``resolve_music_destination`` with the superseded file retired.
  * #1128 (manual matches forgotten between syncs) is the durable-match work,
    observed working in a real app.log.

#1130 (FLAC despite an MP3-only profile) was deliberately NOT adapted: our
fallback ignores every constraint by design when nothing matches a ranked
target, is documented as such, and has a user switch — a different thing from
upstream's accidental half-filter, and a product decision rather than a defect.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from core.imports.paths import sanitize_filename
from core.library import manual_library_match as mlm
from core.video.library_paths import sanitize as video_sanitize


# ── #1134: POST /api/wishlist/process 500'd on every call ───────────────────

class TestWishlistProcessEndpoint:
    """The route called its own runtime factory with a keyword the factory has
    never accepted. The TypeError was swallowed by the route's broad `except`
    into a 500, so the endpoint had never once worked — it failed identically
    whether or not the wishlist was busy, which is why it read as "sometimes
    conflicted" rather than "always broken"."""

    def _route_body(self):
        src = Path("web_server.py").read_text(encoding="utf-8")
        start = src.index("def process_wishlist_api():")
        return src[start:src.index("\n@app.route", start)]

    def test_the_route_does_not_pass_a_keyword_the_factory_rejects(self):
        # Match the CALL's arguments, not the whole body — the body carries a
        # comment naming the bad keyword, and a substring check would trip on
        # its own explanation.
        body = self._route_body()
        call = re.search(r"_build_wishlist_route_runtime\(([^)]*)\)", body)
        assert call, "the route no longer builds a runtime at all"
        assert "is_auto_processing_flag" not in call.group(1), \
            "the route still passes a keyword _build_wishlist_route_runtime does not accept"

    def test_every_factory_keyword_used_anywhere_is_one_the_factory_declares(self):
        """The general form of the bug. A keyword-only factory plus a broad
        `except` turns a typo into a runtime 500 that no import or lint catches,
        so pin ALL call sites rather than just the one that was wrong."""
        src = Path("web_server.py").read_text(encoding="utf-8")
        sig_start = src.index("def _build_wishlist_route_runtime(")
        sig = src[sig_start:src.index(")", sig_start)]
        declared = set(re.findall(r"(\w+)\s*=\s*None", sig))
        assert declared, "could not parse the factory signature"

        used = set()
        for m in re.finditer(r"_build_wishlist_route_runtime\(\s*([^)]*)\)", src):
            used.update(re.findall(r"(\w+)\s*=", m.group(1)))
        # Drop names that belong to lambdas inside the arguments, not the call.
        unknown = {k for k in used - declared if k not in ("lambda",)}
        assert not unknown, f"call sites pass keywords the factory does not declare: {unknown}"


# ── #1129: a leading dot made the folder invisible ──────────────────────────

class TestDotLeadingNames:
    """`rstrip('. ')` is the WINDOWS rule — it rejects names ending in a dot.
    The UNIX rule is the other end, and nobody applied it: the files imported
    perfectly into a folder that `ls`, the file manager and the media server's
    scanner all skip."""

    @pytest.mark.parametrize("name,expected", [
        ("...And Justice for All", "And Justice for All"),
        (".5 The Gray Chapter", "5 The Gray Chapter"),
        (". Leading space dot", "Leading space dot"),
    ])
    def test_music_names_no_longer_start_with_a_dot(self, name, expected):
        assert sanitize_filename(name) == expected

    @pytest.mark.parametrize("name", ["...And Justice for All", ".hidden", ". x"])
    def test_no_music_name_survives_as_hidden(self, name):
        assert not sanitize_filename(name).startswith(".")

    def test_video_titles_too(self):
        """Same gap, same cause — trailing was handled because Windows rejects
        it, leading was not because Windows does not care."""
        assert not video_sanitize("...Baby One More Time").startswith(".")
        assert video_sanitize("...Baby One More Time") == "Baby One More Time"

    def test_trailing_dots_are_still_stripped(self):
        """The Windows rule must survive the Unix one being added."""
        assert sanitize_filename("trailing...") == "trailing"
        assert sanitize_filename("both...sides...") == "both...sides"

    def test_interior_dots_are_untouched(self):
        """Only the ends are unsafe. Stripping interior dots would rename half
        the library."""
        assert sanitize_filename("Mr. Bungle") == "Mr. Bungle"
        assert sanitize_filename("S.O.S.") == "S.O.S"   # trailing dot still goes

    def test_an_all_dot_name_still_yields_a_usable_component(self):
        """Stripping both ends of '...' leaves nothing — which would be an empty
        path component, i.e. a silently wrong destination."""
        assert sanitize_filename("...") == "_"
        assert sanitize_filename(".") == "_"
        assert sanitize_filename("   ") == "_"


# ── #1138: a match row is not proof the file survived ───────────────────────

class _DB:
    """Minimal library stand-in: which ids exist, and which paths map to which."""

    def __init__(self, tracks=None, by_path=None, raise_on_id=False):
        self._tracks = tracks or {}
        self._by_path = by_path or {}
        self._raise = raise_on_id

    def get_track_by_id(self, track_id):
        if self._raise:
            raise RuntimeError("db locked")
        return self._tracks.get(str(track_id))

    def find_track_id_by_file_path(self, path):
        return self._by_path.get(path)


class TestManualMatchResolution:
    def test_a_live_match_resolves(self):
        db = _DB(tracks={"42": object()})
        match = {"library_track_id": 42, "library_file_path": "/m/a.flac"}
        assert mlm.resolve_match(db, match) is match

    def test_a_match_to_a_deleted_track_does_not(self):
        """The reported symptom. Unverified, this made the track report
        found=True forever: download skipped, wishlist entry removed, gone."""
        db = _DB(tracks={})
        assert mlm.resolve_match(db, {"library_track_id": 42}) is None

    def test_a_stale_id_with_a_live_path_is_healed_not_dropped(self):
        """Media servers re-key tracks on a metadata refresh. A dead id whose
        stored path still resolves is a stale id, not a missing file — dropping
        it would re-download something the user already owns."""
        db = _DB(tracks={}, by_path={"/m/a.flac": "99"})
        out = mlm.resolve_match(db, {"library_track_id": 42, "library_file_path": "/m/a.flac"})
        assert out is not None and out["library_track_id"] == "99"

    def test_healing_does_not_mutate_the_caller_s_row(self):
        db = _DB(tracks={}, by_path={"/m/a.flac": "99"})
        match = {"library_track_id": 42, "library_file_path": "/m/a.flac"}
        mlm.resolve_match(db, match)
        assert match["library_track_id"] == 42

    def test_an_unreadable_database_keeps_the_match(self):
        """Fail-safe direction matters: treating a DB blip as 'the file is gone'
        would re-download the user's whole library."""
        db = _DB(raise_on_id=True)
        match = {"library_track_id": 42}
        assert mlm.resolve_match(db, match) is match

    def test_a_db_with_neither_lookup_is_trusted(self):
        """A reduced db shim must behave as before rather than have every match
        read as dead."""
        class _Minimal:
            pass
        match = {"library_track_id": 42}
        assert mlm.resolve_match(_Minimal(), match) is match

    def test_no_match_stays_no_match(self):
        assert mlm.resolve_match(_DB(), None) is None

    @pytest.mark.parametrize("site,fn", [
        ("core/downloads/master.py", "the download analysis"),
        ("core/wishlist/processing.py", "the wishlist drain"),
        ("database/music_database.py", "add_to_wishlist"),
    ])
    def test_every_consumer_verifies(self, site, fn):
        """Three call sites treated the row as proof, and together they closed a
        trap: the drain removed the track as a SUCCESS, add_to_wishlist then
        refused to re-add it, and the download analysis skipped it. Fixing one
        leaves the track just as stuck."""
        src = Path(site).read_text(encoding="utf-8")
        for m in re.finditer(r"get_match_for_track\(", src):
            window = src[max(0, m.start() - 260):m.start()]
            assert "resolve_match" in window, \
                f"{fn} ({site}) uses a manual match without verifying it resolves"


# ── #1132: AcoustID picked the first result, not the best ───────────────────

class TestAcoustIDSelection:
    def test_recordings_come_back_best_first(self):
        """The client appended in API order while tracking best_score in a
        separate variable, so recordings[0] was 'whatever AcoustID listed
        first' — upstream's 'variants were listed first' exactly."""
        src = Path("core/acoustid_client.py").read_text(encoding="utf-8")
        fn = src[src.index("def lookup_with_status("):]
        fn = fn[:fn.index("\n    def ")]
        assert "recordings.sort(" in fn, "the client still returns API order"
        assert fn.index("recordings.sort(") < fn.index("return {'status': 'ok'"), \
            "the sort must happen before the result is returned"

    def test_best_recording_picks_the_highest_score(self):
        from core.acoustid_client import best_recording
        out = best_recording({"recordings": [
            {"title": "Live Version", "score": 0.42},
            {"title": "Studio", "score": 0.97},
        ]})
        assert out["title"] == "Studio"

    def test_an_uncertain_guess_is_declined(self):
        """A weak hit is not a wrong answer, it is an uncertain one — and
        writing it to the file makes it look authoritative afterwards."""
        from core.acoustid_client import best_recording
        assert best_recording({"recordings": [{"title": "Maybe", "score": 0.3}]}) is None

    def test_the_floor_is_inclusive_at_the_bar(self):
        from core.acoustid_client import MIN_IDENTIFY_SCORE, best_recording
        at_bar = [{"title": "Exactly", "score": MIN_IDENTIFY_SCORE}]
        assert best_recording({"recordings": at_bar})["title"] == "Exactly"

    def test_missing_or_empty_input_is_handled(self):
        from core.acoustid_client import best_recording
        assert best_recording(None) is None
        assert best_recording({}) is None
        assert best_recording({"recordings": []}) is None

    def test_a_scoreless_recording_does_not_crash_and_does_not_pass(self):
        """AcoustID can omit fields. A missing score must read as 0 (decline),
        never as 'no floor to apply'."""
        from core.acoustid_client import best_recording
        assert best_recording({"recordings": [{"title": "No score"}]}) is None

    @pytest.mark.parametrize("marker", ["_identify_single", "_identify_from_acoustid"])
    def test_both_consumers_gate_on_confidence(self, marker):
        src = Path("core/auto_import_worker.py").read_text(encoding="utf-8")
        start = src.index(f"def {marker}(")
        body = src[start:]
        body = body[:body.index("\n    def ", 1)]
        assert "best_recording(" in body, f"{marker} still reads recordings[0] unguarded"
        assert "['recordings'][0]" not in body

    def test_no_consumer_anywhere_still_indexes_position_zero(self):
        """The general form — a new caller reaching for [0] reintroduces the
        bug even with the sort in place, because it would still skip the floor."""
        src = Path("core/auto_import_worker.py").read_text(encoding="utf-8")
        assert "['recordings'][0]" not in src
