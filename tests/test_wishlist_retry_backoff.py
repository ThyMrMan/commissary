"""javiavid — wishlist retry accounting + progressive backoff + ignore TTL.

The attempt counter existed but was DEAD: update_wishlist_retry (the only
retry_count increment) had a single caller, mark_track_download_result, which
itself had no callers — so retry_count stayed 0 forever and the 3.1.1 failing
badge/filter (keyed on retry_count >= 3) never fired on the music side.

Under test:
  * record_failed_attempt stamps every failed cycle attempt (fresh add AND
    duplicate-skip), feeding the badge and the backoff
  * the backoff ladder (0-1 → none, 2 → 4h, 3 → 24h, 4+ → 7d), fail-open on
    unparseable timestamps, and the due/cooling split
  * scheduled cycles apply backoff, the manual Process Now click does not
    (source contract — automation_id gates it)
  * IGNORE_TTL_DAYS honors wishlist.ignore_ttl_days (clamped 1-365)
"""

from __future__ import annotations

from datetime import datetime, timedelta
from pathlib import Path

import pytest

from core.wishlist.retry_backoff import (
    cooldown_seconds,
    is_due,
    split_due_for_retry,
)

_ROOT = Path(__file__).resolve().parent.parent


# ── the ladder ───────────────────────────────────────────────────────────────

def test_cooldown_ladder():
    assert cooldown_seconds(0) == 0
    assert cooldown_seconds(1) == 0
    assert cooldown_seconds(2) == 4 * 3600
    assert cooldown_seconds(3) == 24 * 3600
    assert cooldown_seconds(4) == 7 * 24 * 3600
    assert cooldown_seconds(25) == 7 * 24 * 3600
    assert cooldown_seconds(None) == 0
    assert cooldown_seconds("nope") == 0


def test_is_due_and_split():
    now = datetime(2026, 7, 23, 12, 0, 0)
    fresh = {"retry_count": 0, "last_attempted": None}
    twice_recent = {"retry_count": 2,
                    "last_attempted": (now - timedelta(hours=1)).strftime('%Y-%m-%d %H:%M:%S')}
    twice_stale = {"retry_count": 2,
                   "last_attempted": (now - timedelta(hours=5)).strftime('%Y-%m-%d %H:%M:%S')}
    chronic = {"retry_count": 9,
               "last_attempted": (now - timedelta(days=3)).strftime('%Y-%m-%d %H:%M:%S')}
    chronic_due = {"retry_count": 9,
                   "last_attempted": (now - timedelta(days=8)).strftime('%Y-%m-%d %H:%M:%S')}
    broken_ts = {"retry_count": 9, "last_attempted": "not a date"}   # fail-open

    assert is_due(fresh, now) is True
    assert is_due(twice_recent, now) is False
    assert is_due(twice_stale, now) is True
    assert is_due(chronic, now) is False
    assert is_due(chronic_due, now) is True
    assert is_due(broken_ts, now) is True

    due, cooling = split_due_for_retry(
        [fresh, twice_recent, twice_stale, chronic, chronic_due, broken_ts], now)
    assert len(due) == 4 and len(cooling) == 2


# ── the counter finally counts ───────────────────────────────────────────────

class _ForwardingService:
    """mark_track_download_result → the real DB method (hermetic singleton-free)."""

    def __init__(self, db):
        self.db = db

    def mark_track_download_result(self, spotify_track_id, success,
                                   error_message=None, profile_id=1):
        return self.db.update_wishlist_retry(spotify_track_id, success,
                                             error_message, profile_id=profile_id)


def _wishlisted_track(db, sp_id="trk1"):
    payload = {
        'id': sp_id, 'name': 'Elusive Song', 'artists': [{'name': 'Ghost Artist'}],
        'album': {'id': 'a1', 'name': 'Elusive Song', 'artists': [{'name': 'Ghost Artist'}],
                  'images': [], 'album_type': 'single', 'release_date': '2020-01-01',
                  'total_tracks': 1},
        'duration_ms': 1000, 'track_number': 1, 'disc_number': 1,
    }
    assert db.add_to_wishlist(spotify_track_data=payload, failure_reason='Not found',
                              source_type='wishlist', source_info='{}', profile_id=1)


def test_record_failed_attempt_accumulates(tmp_path):
    from database.music_database import MusicDatabase
    from core.wishlist.processing import record_failed_attempt

    db = MusicDatabase(database_path=str(tmp_path / 'm.db'))
    _wishlisted_track(db)
    svc = _ForwardingService(db)

    assert record_failed_attempt(svc, {'id': 'trk1'}, 'Not found', 1) is True
    assert record_failed_attempt(svc, {'id': 'trk1'}, 'Still not found', 1) is True
    row = db.get_wishlist_tracks()[0]
    assert row['retry_count'] == 2
    assert row['last_attempted']                       # stamped
    assert row['failure_reason'] == 'Still not found'


def test_record_failed_attempt_guards(tmp_path):
    from database.music_database import MusicDatabase
    from core.wishlist.processing import record_failed_attempt

    db = MusicDatabase(database_path=str(tmp_path / 'm.db'))
    svc = _ForwardingService(db)
    assert record_failed_attempt(svc, {'id': 'wing_it_x'}, 'e', 1) is False   # wing-it skip
    assert record_failed_attempt(svc, {}, 'e', 1) is False                    # no id
    assert record_failed_attempt(svc, None, 'e', 1) is False                  # bad shape
    assert record_failed_attempt(svc, {'id': 'unknown'}, 'e', 1) is False     # no row → no-op

    class _Boom:
        def mark_track_download_result(self, *a, **k):
            raise RuntimeError('db locked')
    assert record_failed_attempt(_Boom(), {'id': 'x'}, 'e', 1) is False       # swallowed


# ── wiring contracts ─────────────────────────────────────────────────────────

def test_failed_processor_stamps_every_attempt():
    src = (_ROOT / "core" / "downloads" / "wishlist_failed.py").read_text(encoding="utf-8")
    assert "_record_failed_attempt(" in src
    # the stamp must NOT be gated on the add succeeding — the duplicate-skip
    # IS the repeat-failure signal
    body = src[src.index("_record_failed_attempt("):]
    assert body.index("if success:") > 0


def test_backoff_applies_to_scheduled_cycles_only():
    src = (_ROOT / "core" / "wishlist" / "processing.py").read_text(encoding="utf-8")
    assert "split_due_for_retry" in src
    gate = src[src.index("split_due_for_retry") - 700:src.index("split_due_for_retry")]
    assert "automation_id is not None" in gate     # manual Process Now bypasses
    assert "apply_backoff" in gate                 # pipelines can opt in explicitly


# ── configurable ignore TTL ──────────────────────────────────────────────────

def test_ignore_ttl_reads_config(monkeypatch):
    import core.wishlist.ignore as ig

    class _Cfg:
        def __init__(self, v):
            self.v = v

        def get(self, key, default=None):
            return self.v if key == 'wishlist.ignore_ttl_days' else default

    import config.settings as cs
    monkeypatch.setattr(cs, 'config_manager', _Cfg(7))
    assert ig.configured_ttl_days() == 7
    monkeypatch.setattr(cs, 'config_manager', _Cfg(9999))
    assert ig.configured_ttl_days() == 365          # clamped
    monkeypatch.setattr(cs, 'config_manager', _Cfg('garbage'))
    assert ig.configured_ttl_days() == 30           # fallback
    monkeypatch.setattr(cs, 'config_manager', _Cfg(0))
    assert ig.configured_ttl_days() == 1            # floor


# ── the retry loop, as it actually happened (from a real 12-hour app.log) ────
# The two stamps above run back-to-back. A real drain cycle does not: it re-adds
# the failed track to the wishlist FIRST, then stamps the attempt.
#
# That re-add used to fork a SECOND row keyed '<track>::<album>' whenever the
# base id already existed — without checking whether the album was actually
# different. So a track failing repeatedly from the same album grew a duplicate
# of itself. record_failed_attempt stamps the BASE id, so the fork's retry_count
# stayed 0, is_due() was permanently true for it, and the progressive backoff
# could never quiet it. Measured behaviour before the fix:
#
#     cycle 1: rows=1  [('trk1', 1)]
#     cycle 2: rows=2  [('trk1', 2), ('trk1::a1', 0)]
#     cycle 3: rows=2  [('trk1', 3), ('trk1::a1', 0)]   ← forever
#
# In the log that is 34 files re-downloaded and re-quarantined up to 132 times
# each, and not one cooldown in twelve hours.

def _payload(sp_id="trk1", album_id="a1"):
    return {
        'id': sp_id, 'name': 'Elusive Song', 'artists': [{'name': 'Ghost Artist'}],
        'album': {'id': album_id, 'name': f'Album {album_id}',
                  'artists': [{'name': 'Ghost Artist'}], 'images': [],
                  'album_type': 'single', 'release_date': '2020-01-01', 'total_tracks': 1},
        'duration_ms': 1000, 'track_number': 1, 'disc_number': 1,
    }


def _failed_cycle(db, svc, album_id="a1", sp_id="trk1"):
    """What a drain cycle really does to a track that failed again."""
    from core.wishlist.processing import record_failed_attempt
    db.add_to_wishlist(spotify_track_data=_payload(sp_id, album_id),
                       failure_reason='Not found', source_type='wishlist',
                       source_info='{}', profile_id=1)
    record_failed_attempt(svc, {'id': sp_id}, 'Not found', 1)


def test_repeated_failures_do_not_fork_a_duplicate_row(tmp_path):
    from database.music_database import MusicDatabase

    db = MusicDatabase(database_path=str(tmp_path / 'm.db'))
    svc = _ForwardingService(db)
    for _ in range(5):
        _failed_cycle(db, svc)

    rows = db.get_wishlist_tracks()
    assert len(rows) == 1, [r['spotify_track_id'] for r in rows]
    assert rows[0]['retry_count'] == 5


def test_the_backoff_ladder_engages_across_real_cycles(tmp_path):
    """The whole point of the counter — and unreachable before the fix, because
    the duplicate row reset the effective state every cycle."""
    from datetime import datetime, timezone

    from core.wishlist.retry_backoff import is_due
    from database.music_database import MusicDatabase

    db = MusicDatabase(database_path=str(tmp_path / 'm.db'))
    svc = _ForwardingService(db)
    now = datetime.now(timezone.utc).replace(tzinfo=None)

    _failed_cycle(db, svc)
    assert all(is_due(r, now) for r in db.get_wishlist_tracks())      # attempt 1: still every cycle
    _failed_cycle(db, svc)
    assert not any(is_due(r, now) for r in db.get_wishlist_tracks())  # attempt 2: 4h cooldown


def test_the_same_track_from_a_different_album_still_gets_its_own_row(tmp_path):
    """The composite key exists for a real case — don't break it."""
    from database.music_database import MusicDatabase

    db = MusicDatabase(database_path=str(tmp_path / 'm.db'))
    svc = _ForwardingService(db)
    _failed_cycle(db, svc, album_id='a1')
    _failed_cycle(db, svc, album_id='a2')

    ids = sorted(r['spotify_track_id'] for r in db.get_wishlist_tracks())
    assert ids == ['trk1', 'trk1::a2']


def test_a_re_add_still_refreshes_the_failure_reason(tmp_path):
    """Preserving the counter must not freeze the row."""
    from database.music_database import MusicDatabase

    db = MusicDatabase(database_path=str(tmp_path / 'm.db'))
    db.add_to_wishlist(spotify_track_data=_payload(), failure_reason='Not found',
                       source_type='wishlist', source_info='{}', profile_id=1)
    db.add_to_wishlist(spotify_track_data=_payload(), failure_reason='All sources failed',
                       source_type='wishlist', source_info='{}', profile_id=1)
    rows = db.get_wishlist_tracks()
    assert len(rows) == 1
    assert rows[0]['failure_reason'] == 'All sources failed'


def test_existing_duplicate_rows_are_swept_once(tmp_path):
    """Databases already carry these forks — that is what is looping right now."""
    import json

    from database.music_database import MusicDatabase

    db = MusicDatabase(database_path=str(tmp_path / 'm.db'))
    db.add_to_wishlist(spotify_track_data=_payload(), failure_reason='x',
                       source_type='wishlist', source_info='{}', profile_id=1)
    with db._get_connection() as conn:
        c = conn.cursor()
        # the self-duplicate the old code produced...
        c.execute("INSERT INTO wishlist_tracks (spotify_track_id, spotify_data, profile_id) "
                  "VALUES (?, ?, 1)", ('trk1::a1', json.dumps(_payload())))
        # ...and a legitimate different-album fork, which must survive
        c.execute("INSERT INTO wishlist_tracks (spotify_track_id, spotify_data, profile_id) "
                  "VALUES (?, ?, 1)", ('trk1::a2', json.dumps(_payload(album_id='a2'))))
        conn.commit()
        assert db._prune_self_duplicate_wishlist_rows(c) == 1
        conn.commit()

    ids = sorted(r['spotify_track_id'] for r in db.get_wishlist_tracks())
    assert ids == ['trk1', 'trk1::a2']
