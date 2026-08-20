"""Quality Check upgrades failed with "No matched track in finding" — every one.

Carried over from upstream 3.2.2. Two users reported the same thing: the tool
finds plenty to upgrade, then refuses every single one of them.

Two independent causes, and the first is the "always":

  · The scanner records ``entity_id=None`` for any file it could not match to a
    library track row (entity_type='file'). The fix handler was gated on it —
    ``if not track_data and entity_id:`` — so for exactly those findings the
    resolver was never called at all, even though the finding's own details
    carry the title, artist and album.

  · The resolver itself returned None on a missing DB row, discarding those same
    usable details. That is the rarer trigger: a full refresh calls
    clear_server_data, which DELETEs every track for the server and re-inserts
    it with new autoincrement ids, orphaning every finding written beforehand.

The details are now treated as a valid SOURCE rather than a per-field fallback,
and both detail vocabularies are read — the Quality Check scanner writes
expected_title/expected_artist, the Quality Upgrade job writes
track_title/artist, and reading only one set left the other's findings
unresolvable.
"""

from __future__ import annotations

from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parents[2]


class _FakeCursor:
    def __init__(self, row):
        self._row = row

    def execute(self, *_a, **_kw):
        return self

    def fetchone(self):
        return self._row


class _FakeConn:
    def __init__(self, row):
        self._row = row

    def cursor(self):
        return _FakeCursor(self._row)

    def close(self):
        pass


class _FakeDB:
    def __init__(self, row=None):
        self._row = row

    def _get_connection(self):
        return _FakeConn(self._row)


def _worker(row=None):
    from core.repair_worker import RepairWorker
    w = RepairWorker.__new__(RepairWorker)      # no __init__ side effects
    w.db = _FakeDB(row)
    return w


# A library row as the resolver's SELECT returns it.
def _row(**over):
    base = {
        'id': 42, 'title': 'Dec.', 'track_number': 3, 'duration': 134340,
        'spotify_track_id': None, 'itunes_track_id': None, 'deezer_id': None,
        'artist_name': 'Kanaria', 'album_title': 'Dec.', 'spotify_album_id': None,
        'record_type': 'single', 'track_count': 1, 'year': 2021, 'album_thumb': None,
    }
    base.update(over)
    return base


class TestTheOrphanedFinding:
    def test_a_finding_with_no_db_row_still_resolves_from_its_details(self):
        """THE refresh case: clear_server_data re-inserts every track with a new
        id, so findings written beforehand point at ids that no longer exist."""
        w = _worker(row=None)
        out = w._track_identity_for_redownload(9999, {
            'track_title': 'Dec.', 'artist': 'Kanaria', 'album_title': 'Dec.'})
        assert out is not None
        assert out['name'] == 'Dec.'
        assert out['artists'] == [{'name': 'Kanaria'}]

    def test_a_finding_with_no_entity_id_resolves_too(self):
        """The scanner records entity_id=None for a file it could not match to
        a library row. That is not an error — the details are the only source."""
        w = _worker(row=None)
        out = w._track_identity_for_redownload(None, {
            'expected_title': 'Dec.', 'expected_artist': 'Kanaria',
            'file_path': '/music/Kanaria/Dec.opus'})
        assert out is not None and out['name'] == 'Dec.'

    @pytest.mark.parametrize("details", [
        {'track_title': 'Dec.', 'artist': 'Kanaria'},          # upgrade job's names
        {'expected_title': 'Dec.', 'expected_artist': 'Kanaria'},  # scanner's names
    ])
    def test_both_detail_vocabularies_are_read(self, details):
        """The two producers disagree on key names, and reading one set left the
        other's findings unresolvable."""
        w = _worker(row=None)
        out = w._track_identity_for_redownload(None, details)
        assert out is not None and out['name'] == 'Dec.'

    def test_the_db_row_still_wins_when_there_is_one(self):
        w = _worker(row=_row())
        out = w._track_identity_for_redownload(42, {
            'track_title': 'Something Else', 'artist': 'Someone Else'})
        assert out['name'] == 'Dec.' and out['artists'] == [{'name': 'Kanaria'}]
        assert out['track_number'] == 3

    def test_nothing_identifiable_is_refused_rather_than_queued(self):
        """A wishlist entry built from "Unknown - Unknown" would search for
        nothing and sit there forever."""
        w = _worker(row=None)
        assert w._track_identity_for_redownload(None, {}) is None
        assert w._track_identity_for_redownload(None, {'track_title': 'Dec.'}) is None
        assert w._track_identity_for_redownload(None, {'artist': 'Kanaria'}) is None


class TestTheFallbackIdIsUnique:
    def test_two_unmatched_findings_do_not_collide(self):
        """A literal "redownload_None" would make every entity_id=None finding
        share one wishlist row — the second deduped away and never downloaded."""
        w = _worker(row=None)
        a = w._track_identity_for_redownload(None, {
            'track_title': 'Dec.', 'artist': 'Kanaria', 'file_path': '/a.opus'})
        b = w._track_identity_for_redownload(None, {
            'track_title': 'Monsters', 'artist': 'All Time Low', 'file_path': '/b.opus'})
        assert a['id'] != b['id']
        assert 'None' not in a['id'] and 'None' not in b['id']

    def test_the_same_finding_resolves_to_the_same_id(self):
        """Stable, so re-applying does not queue a duplicate."""
        w = _worker(row=None)
        kw = {'track_title': 'Dec.', 'artist': 'Kanaria', 'file_path': '/a.opus'}
        assert (w._track_identity_for_redownload(None, dict(kw))['id']
                == w._track_identity_for_redownload(None, dict(kw))['id'])

    def test_a_real_service_id_is_preferred_over_the_fallback(self):
        w = _worker(row=_row(spotify_track_id='sp123'))
        assert w._track_identity_for_redownload(42, {})['id'] == 'sp123'

    def test_an_entity_id_still_names_its_own_fallback(self):
        w = _worker(row=_row())
        assert w._track_identity_for_redownload(42, {})['id'] == 'redownload_42'


def test_the_handler_is_no_longer_gated_on_entity_id():
    """Source guard on the one-line gate that caused the every-time failure.
    A revert would be exactly as small as the fix."""
    src = (_ROOT / "core" / "repair_worker.py").read_text(encoding="utf-8")
    assert "if not track_data and entity_id:" not in src
    assert "if not track_data:" in src
