"""'Prefer explicit versions' matching preference (#923).

Two pieces:
  * detect_version_type grows a 'clean' class — bare "(Clean)" / "Censored" /
    "Edited Version" markers used to be invisible (only "clean edit"/"radio
    edit" were), so a clean rip scored like the original.
  * an opt-in scoring nudge (content_filter.prefer_explicit, gated on
    allow_explicit): explicit-marked files rank up, clean/censored/radio-edit
    files rank down, unmarked untouched. Pure ORDERING — a clean edit still
    matches when it's all that exists (the requester's explicit → unmarked →
    clean ladder, never a skip).
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

import core.matching_engine as me


@pytest.fixture
def engine():
    return me.MusicMatchingEngine()


class _Config:
    def __init__(self, values):
        self.values = values

    def get(self, key, default=None):
        return self.values.get(key, default)


def _set_config(monkeypatch, **values):
    monkeypatch.setattr(me, 'config_manager', _Config(values))


def _score(engine, filename, base=0.80):
    """Adjusted confidence for a candidate with a fixed base confidence, so
    only the version handling is under test."""
    src = SimpleNamespace(name='Song Title')
    cand = SimpleNamespace(filename=filename)
    engine.calculate_slskd_match_confidence = lambda *_a, **_k: base
    conf, version = engine.calculate_slskd_match_confidence_enhanced(src, cand)
    return conf, version


# ── the new 'clean' version class ────────────────────────────────────────────

def test_clean_markers_are_detected(engine):
    for name in ('01 - Song Title (Clean).flac',
                 'Song Title [clean].mp3',
                 'Artist - Song Title - Clean.flac',
                 'Song Title (Clean Version).flac',
                 'Song Title (Censored).mp3',
                 'Song Title (Edited Version).flac'):
        vt, penalty = engine.detect_version_type(name)
        assert vt == 'clean', name
        assert penalty > 0


def test_clean_never_matches_inside_real_titles(engine):
    # "clean" as part of a song/artist name is not a version marker
    for name in ('Mr. Clean - Song Title.flac',
                 'Cleaner Days.mp3',
                 'Clean Bandit - Rather Be.flac'):
        vt, _ = engine.detect_version_type(name)
        assert vt == 'original', name


def test_explicit_markers_unchanged(engine):
    assert engine.detect_version_type('Song Title (Explicit).flac')[0] == 'explicit'
    assert engine.detect_version_type('Song Title (Uncensored).flac')[0] == 'explicit'


# ── preference OFF: today's behavior, byte-stable ────────────────────────────

def test_default_scoring_unchanged(engine, monkeypatch):
    _set_config(monkeypatch)  # empty config = defaults (prefer off)
    explicit, _ = _score(engine, 'Song Title (Explicit).flac')
    original, _ = _score(engine, 'Song Title.flac')
    clean, _ = _score(engine, 'Song Title (Clean).flac')
    assert explicit == pytest.approx(0.80 - 0.02 * 0.5)   # the historical -2%
    assert original == pytest.approx(0.80)
    assert clean == pytest.approx(0.80 - 0.08 * 0.5)      # like a radio edit


# ── preference ON: the fallback ladder through ordering ──────────────────────

def test_preference_orders_explicit_over_unmarked_over_clean(engine, monkeypatch):
    _set_config(monkeypatch, **{'content_filter.prefer_explicit': True,
                                'content_filter.allow_explicit': True})
    explicit, vt_e = _score(engine, 'Song Title (Explicit).flac')
    original, _ = _score(engine, 'Song Title.flac')
    clean, vt_c = _score(engine, 'Song Title (Clean).flac')
    radio, _ = _score(engine, 'Song Title (Radio Version).flac')

    assert vt_e == 'explicit' and vt_c == 'clean'
    assert explicit > original > clean          # the requested ladder
    assert original > radio
    assert clean > 0.5                          # never skipped — still a live candidate
    assert explicit <= 1.0


def test_preference_boost_caps_at_one(engine, monkeypatch):
    _set_config(monkeypatch, **{'content_filter.prefer_explicit': True,
                                'content_filter.allow_explicit': True})
    conf, _ = _score(engine, 'Song Title (Explicit).flac', base=0.99)
    assert conf == 1.0


def test_preference_is_inert_when_explicit_content_blocked(engine, monkeypatch):
    """The parent filter wins: preferring explicit while blocking it is a
    contradiction, so the sub-setting is ignored (UI greys it out too)."""
    _set_config(monkeypatch, **{'content_filter.prefer_explicit': True,
                                'content_filter.allow_explicit': False})
    explicit, _ = _score(engine, 'Song Title (Explicit).flac')
    clean, _ = _score(engine, 'Song Title (Clean).flac')
    assert explicit == pytest.approx(0.80 - 0.02 * 0.5)   # default penalties
    assert clean == pytest.approx(0.80 - 0.08 * 0.5)


def test_config_errors_mean_feature_off(engine, monkeypatch):
    class _Boom:
        def get(self, *_a, **_k):
            raise RuntimeError('config unavailable')
    monkeypatch.setattr(me, 'config_manager', _Boom())
    conf, _ = _score(engine, 'Song Title (Explicit).flac')
    assert conf == pytest.approx(0.80 - 0.02 * 0.5)


# ── the settings UI contract ─────────────────────────────────────────────────

def test_settings_ui_wires_the_sub_toggle():
    from pathlib import Path
    root = Path(__file__).resolve().parent.parent
    index = (root / 'webui' / 'index.html').read_text(encoding='utf-8', errors='replace')
    settings_js = (root / 'webui' / 'static' / 'settings.js').read_text(encoding='utf-8')

    assert 'id="prefer-explicit"' in index
    assert 'syncPreferExplicitState' in index               # parent onchange hook
    assert 'function syncPreferExplicitState' in settings_js
    assert "prefer_explicit: document.getElementById('prefer-explicit').checked" in settings_js
    assert "settings.content_filter?.prefer_explicit === true" in settings_js


# ── slskd reality: full remote paths, markers on FOLDER names ────────────────

def _slskd_result(path):
    from core.download_plugins.types import TrackResult
    return TrackResult(username='someuser', filename=path, size=30_000_000,
                       bitrate=999, duration=210000, quality='flac',
                       free_upload_slots=1, upload_speed=500, queue_length=0)


def test_full_remote_paths_rank_end_to_end(engine, monkeypatch):
    """slskd filenames are whole remote paths ('Music\\Artist\\Album (Clean)\\01
    - Song.flac'). Version markers on ALBUM FOLDERS classify the files inside
    them — that's how peers actually label clean/explicit rips — and the
    ladder holds through the real ranking entry point with real TrackResults."""
    from core.spotify_client import Track
    _set_config(monkeypatch, **{'content_filter.prefer_explicit': True,
                                'content_filter.allow_explicit': True})
    src = Track(id='x', name='Godzilla', artists=['Eminem'],
                album='Music To Be Murdered By', duration_ms=210000, popularity=80)
    explicit_file = _slskd_result(r"Music\Eminem\MTBMB\04 - Godzilla (Explicit).flac")
    unmarked = _slskd_result(r"Music\Eminem\MTBMB\04 - Godzilla.flac")
    clean_folder = _slskd_result(r"Rap - Clean Versions\Eminem\04 - Godzilla.flac")
    clean_file = _slskd_result(r"Music\Eminem\MTBMB\04 - Godzilla (Clean).flac")

    ranked = engine.find_best_slskd_matches_enhanced(
        src, [clean_file, unmarked, clean_folder, explicit_file])
    assert len(ranked) == 4                       # ladder = ordering, never a skip
    assert ranked[0] is explicit_file
    assert ranked[1] is unmarked
    # TrackResult is unhashable — compare by identity, not set()
    assert all(any(r is c for c in (clean_file, clean_folder)) for r in ranked[2:])
    assert all(r.confidence > 0.6 for r in ranked)   # clean survives validation's floor


def test_folder_guards_hold_on_full_paths(engine):
    # band/title names containing 'clean' anywhere in the remote path
    for path in (r"Music\Clean Bandit\Rather Be\01 - Rather Be.flac",
                 r"Music\Mr. Clean OST\01 - Theme.flac",
                 r"Music\DJ Clean - Mixtape\01 - Intro.flac"):
        assert engine.detect_version_type(path)[0] == 'original', path


# ── the same preference, for sources with no filename to read ───────────────
# Deezer/Tidal/Qobuz/HiFi/Amazon never reach detect_version_type at all —
# validation.py says so outright ("structured metadata; don't fall back to
# filename matching"), so everything above did nothing for them. They return
# the AUTHORITATIVE answer instead, and it was discarded before the candidate
# was even built.

import pathlib                                               # noqa: E402

from core.download_plugins.types import TrackResult          # noqa: E402
from core.downloads import explicit_preference as ep         # noqa: E402
from core.spotify_client import Track                        # noqa: E402

_SRC = pathlib.Path(__file__).resolve().parents[1]


def _wanted(explicit=None, name="Song Title"):
    return Track(id="i", name=name, artists=["A"], album="Al",
                 duration_ms=1000, popularity=0, explicit=explicit)


def _candidate(**kw):
    base = dict(username="deezer_dl", filename="1||A - Song Title", size=0,
                bitrate=None, duration=None, quality="flac", free_upload_slots=0,
                upload_speed=0, queue_length=0, artist="A", title="Song Title")
    base.update(kw)
    return TrackResult(**base)


class TestTheFlagOnlyMeansSomethingRelatively:
    """The decision the whole module turns on, and the one that would do real
    damage if it were wrong."""

    def test_a_song_with_no_explicit_cut_is_never_penalised(self):
        """`explicit=False` overwhelmingly means "this song has no explicit
        content", not "this is the censored cut". Sinking every False would
        break the correct result for most music ever recorded."""
        assert ep.verdict(_wanted(False), _candidate(explicit=False)) == ep.UNKNOWN

    def test_nor_when_nobody_said_what_was_wanted(self):
        assert ep.verdict(_wanted(None), _candidate(explicit=False)) == ep.UNKNOWN

    def test_the_censored_cut_of_an_explicit_track_is_the_case_that_counts(self):
        assert ep.verdict(_wanted(True), _candidate(explicit=False)) == ep.CENSORED

    def test_and_the_explicit_cut_is_preferred(self):
        assert ep.verdict(_wanted(True), _candidate(explicit=True)) == ep.MATCH

    def test_a_source_that_did_not_say_is_not_guessed_at(self):
        """None and False are different answers. Collapsing them is the misread
        this module exists to avoid."""
        assert ep.verdict(_wanted(True), _candidate(explicit=None)) == ep.UNKNOWN


class TestSourcesWithNoFlagFallBackToTheName:
    """Torrent, usenet and YouTube have a release title and nothing else — this
    is what brings them into the preference at all."""

    @pytest.mark.parametrize("title,expect", [
        ("Song Title (Clean)", ep.CENSORED),
        ("Song Title [Clean]", ep.CENSORED),
        ("Song Title - Clean", ep.CENSORED),
        ("Song Title (Censored)", ep.CENSORED),
        ("Song Title (Edited Version)", ep.CENSORED),
        ("Song Title (Explicit)", ep.MATCH),
        ("Song Title", ep.UNKNOWN),
    ])
    def test_markers(self, title, expect):
        assert ep.verdict(_wanted(True), _candidate(explicit=None, title=title)) == expect

    def test_a_title_containing_the_word_clean_is_not_a_censored_cut(self):
        """Same discipline detect_version_type uses: bracket- and dash-bound
        only. 'Mr. Clean' and 'Clean Bandit' must survive."""
        for title in ("Mr. Clean", "Clean Bandit - Rockabye", "Come Clean"):
            assert ep.verdict(_wanted(True),
                              _candidate(explicit=None, title=title)) == ep.UNKNOWN


class TestItRanksAndNeverFilters:
    def test_the_ladder(self):
        assert ep.adjust(0.80, ep.MATCH, enabled=True) > 0.80
        assert ep.adjust(0.80, ep.UNKNOWN, enabled=True) == 0.80
        assert ep.adjust(0.80, ep.CENSORED, enabled=True) < 0.80

    def test_the_setting_off_changes_nothing(self):
        for v in (ep.MATCH, ep.CENSORED, ep.UNKNOWN):
            assert ep.adjust(0.80, v, enabled=False) == 0.80

    def test_it_stays_inside_zero_and_one(self):
        assert ep.adjust(0.99, ep.MATCH, enabled=True) <= 1.0
        assert ep.adjust(0.02, ep.CENSORED, enabled=True) >= 0.0

    def test_junk_confidence_does_not_raise(self):
        assert ep.adjust(None, ep.MATCH, enabled=True) == 0.0
        assert ep.adjust("nonsense", ep.CENSORED, enabled=True) == 0.0

    def test_the_acceptance_gate_is_never_what_gets_adjusted(self):
        """The promise from #923 — a clean cut still downloads when it is all
        that exists. validation.py must keep gating on the true confidence and
        apply the preference only to the sort, or a penalty could push a lone
        candidate under 0.60 and leave the user with nothing at all."""
        src = (_SRC / "core" / "downloads" / "validation.py").read_text(encoding="utf-8")
        block = src.split("r.explicit_verdict = explicit_preference.verdict", 1)[1]
        gate = block.split("scored.append(r)", 1)[0]
        assert "confidence >= 0.60" in gate
        assert "explicit_preference.adjust" not in gate, \
            "the acceptance gate must read the true confidence"
        # ...and the sort key must BE the adjusted confidence, not merely mention
        # it somewhere nearby: a reverted `key=lambda x: x.confidence` with the
        # old call left dangling below would satisfy a substring check.
        after_key = src.split("scored.sort(key=", 1)[1]
        assert after_key.startswith("lambda x: explicit_preference.adjust("),             "the sort key must be the preference-adjusted confidence"

    def test_it_is_gated_on_both_settings(self):
        """Preferring explicit while the content filter blocks explicit is a
        contradiction — the parent toggle wins, same as the Soulseek path."""
        src = (_SRC / "core" / "downloads" / "validation.py").read_text(encoding="utf-8")
        block = src.split("if scored:", 1)[1][:900]
        assert "content_filter.prefer_explicit" in block
        assert "content_filter.allow_explicit" in block


class TestTheSourcesCarryTheFlag:
    def test_spotify_stops_discarding_it(self):
        """It was reported on every track and dropped in from_spotify_track,
        which left nothing to compare a candidate against."""
        t = Track.from_spotify_track({
            "id": "x", "name": "S", "artists": [{"name": "A"}],
            "album": {"name": "Al", "images": []}, "duration_ms": 1,
            "explicit": True})
        assert t.explicit is True

    def test_an_absent_flag_stays_none_not_false(self):
        t = Track.from_spotify_track({
            "id": "x", "name": "S", "artists": [{"name": "A"}],
            "album": {"name": "Al", "images": []}, "duration_ms": 1})
        assert t.explicit is None

    def test_deezer_carries_its_own(self):
        src = (_SRC / "core" / "deezer_download_client.py").read_text(encoding="utf-8")
        assert "explicit=item.get('explicit_lyrics')" in src

    def test_the_candidate_type_has_somewhere_to_put_it(self):
        assert _candidate(explicit=True).explicit is True
        assert _candidate().explicit is None


class TestItSaysWhenEverythingIsCensored:
    def test_the_case_worth_reporting(self):
        """The download succeeds, looks entirely normal, and the file is
        quietly the clean cut. Both facts were already held and never
        compared."""
        note = ep.summarize([ep.CENSORED, ep.CENSORED])
        assert "clean cut" in note and "2 of 2" in note

    def test_a_mixed_field_just_says_it_is_ranking(self):
        assert "ranking explicit first" in ep.summarize([ep.MATCH, ep.CENSORED])

    def test_nothing_to_report_stays_silent(self):
        assert ep.summarize([ep.UNKNOWN, ep.MATCH]) == ""
        assert ep.summarize([]) == ""
