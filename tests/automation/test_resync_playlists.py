"""The tail of the post-download chain — re-sync playlists that came up short.

Reproduced from a real app.log before writing a line of the fix. Popular Picks,
2026-08-14:

    09:06:33  sync -> 3 of 50 matched; Plex playlist created with 3 tracks
    09:06:37  the other 47 go to the wishlist and start downloading
    09:13:53  batch_complete   -> library scan requested
    09:19:53  library_scan_completed -> database update
    09:20:06  database update done — the downloads are NOW matchable
    09:20:06 .. 10:20   nothing. No sync ran. The playlist still held 3 tracks.

The chain refreshed the library and stopped. Every one of those songs was on
disk, imported, and in the database; the only thing missing was somebody asking
the playlist to look again. Users did that by hand, one "Find & add" at a time.

These tests pin the fourth link.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from core.automation.handlers import resync_playlists as R


# ── candidate selection ─────────────────────────────────────────────────────

def _status(matched, total):
    return {'matched_tracks': matched, 'total_tracks': total}


class TestCameUpShort:
    def test_a_playlist_missing_tracks_is_a_candidate(self):
        assert R.playlist_came_up_short(_status(3, 50)) is True

    def test_a_fully_matched_playlist_is_not(self):
        """The whole point of the narrow scope: a playlist that already has
        everything gains nothing from a re-sync, and re-syncing it would churn
        the server playlist after every database update."""
        assert R.playlist_came_up_short(_status(50, 50)) is False

    def test_matching_more_than_the_source_is_not_short(self):
        assert R.playlist_came_up_short(_status(51, 50)) is False

    def test_a_playlist_that_has_never_synced_is_not_a_candidate(self):
        """No recorded sync means no server playlist to repair. First sync is
        the pipeline's job — silently syncing a playlist the user never asked to
        sync would create playlists on their server out of nowhere."""
        assert R.playlist_came_up_short(None) is False
        assert R.playlist_came_up_short({}) is False

    def test_a_zero_track_playlist_is_not_a_candidate(self):
        assert R.playlist_came_up_short(_status(0, 0)) is False

    @pytest.mark.parametrize("status", [
        {'matched_tracks': 'x', 'total_tracks': 50},
        {'matched_tracks': None, 'total_tracks': 'y'},
        'not a dict',
        [],
    ])
    def test_junk_status_is_never_a_candidate(self, status):
        assert R.playlist_came_up_short(status) is False

    def test_status_counts_written_as_strings_still_work(self):
        """update_and_save_sync_status stores whatever the caller passed; the
        sync path passes ints but the file is JSON round-tripped."""
        assert R.playlist_came_up_short({'matched_tracks': '3', 'total_tracks': '50'}) is True


class TestSelection:
    def test_only_the_short_playlists_come_back(self):
        playlists = [
            {'id': 3, 'name': 'Discovery Shuffle'},
            {'id': 4, 'name': 'Popular Picks'},
            {'id': 5, 'name': 'Never Synced'},
        ]
        statuses = {
            'auto_mirror_3': _status(50, 50),   # complete — leave alone
            'auto_mirror_4': _status(3, 50),    # the reported case
        }                                        # id 5 has no entry at all
        assert [p['name'] for p in R.select_incomplete_playlists(playlists, statuses)] \
            == ['Popular Picks']

    def test_the_status_key_is_the_auto_mirror_id(self):
        """A near-miss key must not accidentally match — the sync-status file is
        shared with non-mirrored syncs, which use different key shapes."""
        playlists = [{'id': 4, 'name': 'Popular Picks'}]
        assert R.select_incomplete_playlists(playlists, {'4': _status(3, 50)}) == []
        assert R.select_incomplete_playlists(playlists, {'mirror_4': _status(3, 50)}) == []

    def test_rows_without_an_id_are_skipped(self):
        assert R.select_incomplete_playlists(
            [{'name': 'no id'}, None, 'junk'], {'auto_mirror_None': _status(1, 2)}) == []

    def test_empty_inputs_are_fine(self):
        assert R.select_incomplete_playlists(None, None) == []


# ── the handler ─────────────────────────────────────────────────────────────

class _State:
    def __init__(self, running=False):
        self.pipeline_running = running
        self.set_calls = []

    def try_start_pipeline(self):
        if self.pipeline_running:
            return False
        self.pipeline_running = True
        return True

    def set_pipeline_running(self, value):
        self.set_calls.append(value)
        self.pipeline_running = value


class _Deps:
    """Enough AutomationDeps surface for the handler, with the sync made
    instantaneous — sync_states is pre-seeded so the poll returns at once."""

    def __init__(self, playlists, statuses, sync_result=None, final_status='finished'):
        self._playlists = playlists
        self._statuses = statuses
        self._sync_result = sync_result or {'status': 'started'}
        self._final = final_status
        self.state = _State()
        self.logger = SimpleNamespace(debug=lambda *a, **k: None,
                                      error=lambda *a, **k: None)
        self.progress = []
        self.sync_calls = []
        self.sync_states = {}
        self.get_database = lambda: SimpleNamespace(
            get_mirrored_playlists=lambda *a, **k: self._playlists)
        self.load_sync_status_file = lambda: self._statuses

    def get_sync_states(self):
        return self.sync_states

    def update_progress(self, aid, **kw):
        self.progress.append(kw)


def _run(deps, monkeypatch, config=None):
    def _fake_sync(cfg, d):
        deps.sync_calls.append(cfg['playlist_id'])
        # The real handler spawns a thread; stand in for it by landing the
        # terminal state the poll is waiting for.
        deps.sync_states[f"auto_mirror_{cfg['playlist_id']}"] = {'status': deps._final}
        return deps._sync_result
    monkeypatch.setattr(R, 'auto_sync_playlist', _fake_sync)
    return R.auto_resync_incomplete_playlists(config or {'_automation_id': 'a1'}, deps)


class TestHandler:
    def test_the_short_playlist_gets_re_synced(self, monkeypatch):
        deps = _Deps([{'id': 4, 'name': 'Popular Picks'}], {'auto_mirror_4': _status(3, 50)})
        out = _run(deps, monkeypatch)
        assert out['status'] == 'completed'
        assert out['resynced'] == '1'
        assert deps.sync_calls == ['4']

    def test_nothing_short_means_nothing_touched(self, monkeypatch):
        """This is what runs after MOST database updates. It must not sync."""
        deps = _Deps([{'id': 3, 'name': 'Done'}], {'auto_mirror_3': _status(50, 50)})
        out = _run(deps, monkeypatch)
        assert out['status'] == 'skipped'
        assert deps.sync_calls == []

    def test_a_stale_finished_state_is_not_mistaken_for_this_run(self, monkeypatch):
        """The previous sync leaves 'finished' in sync_states. Without clearing
        it the poll returns immediately and reports success for a sync that
        never started — the fix would silently do nothing."""
        deps = _Deps([{'id': 4, 'name': 'P'}], {'auto_mirror_4': _status(3, 50)})
        deps.sync_states['auto_mirror_4'] = {'status': 'finished'}
        seen = {}

        def _fake_sync(cfg, d):
            # At this moment the handler must already have dropped the stale row.
            seen['stale_cleared'] = 'auto_mirror_4' not in deps.sync_states
            deps.sync_states['auto_mirror_4'] = {'status': 'finished'}
            return {'status': 'started'}

        monkeypatch.setattr(R, 'auto_sync_playlist', _fake_sync)
        R.auto_resync_incomplete_playlists({'_automation_id': 'a1'}, deps)
        assert seen['stale_cleared'] is True

    def test_it_refuses_to_run_under_a_live_pipeline(self, monkeypatch):
        deps = _Deps([{'id': 4, 'name': 'P'}], {'auto_mirror_4': _status(3, 50)})
        deps.state.pipeline_running = True
        out = _run(deps, monkeypatch)
        assert out['status'] == 'skipped'
        assert deps.sync_calls == []

    def test_it_holds_the_pipeline_flag_and_always_releases_it(self, monkeypatch):
        """Held so a scheduled pipeline can't start syncing the same playlists
        underneath. Released on EVERY exit or the pipeline is dead until
        restart."""
        deps = _Deps([{'id': 4, 'name': 'P'}], {'auto_mirror_4': _status(3, 50)})
        _run(deps, monkeypatch)
        assert deps.state.set_calls[-1] is False
        assert deps.state.pipeline_running is False

    def test_the_flag_is_released_even_when_the_run_blows_up(self, monkeypatch):
        deps = _Deps([{'id': 4, 'name': 'P'}], {'auto_mirror_4': _status(3, 50)})
        deps.get_database = lambda: (_ for _ in ()).throw(RuntimeError('db gone'))
        out = _run(deps, monkeypatch)
        assert out['status'] == 'error'
        assert deps.state.pipeline_running is False

    def test_an_unreadable_status_file_does_not_break_the_chain(self, monkeypatch):
        deps = _Deps([{'id': 4, 'name': 'P'}], {})
        deps.load_sync_status_file = lambda: (_ for _ in ()).throw(OSError('nope'))
        out = _run(deps, monkeypatch)
        assert out['status'] == 'skipped'      # no statuses -> no candidates
        assert deps.state.pipeline_running is False

    def test_a_sync_that_errors_is_counted_not_swallowed(self, monkeypatch):
        deps = _Deps([{'id': 4, 'name': 'P'}], {'auto_mirror_4': _status(3, 50)},
                     final_status='error')
        out = _run(deps, monkeypatch)
        assert out['errors'] == '1'
        assert out['resynced'] == '0'

    def test_a_sync_the_preflight_skips_is_reported_as_skipped(self, monkeypatch):
        deps = _Deps([{'id': 4, 'name': 'P'}], {'auto_mirror_4': _status(3, 50)},
                     sync_result={'status': 'skipped', 'reason': 'unchanged'})
        out = _run(deps, monkeypatch)
        assert out['skipped'] == '1'
        assert out['errors'] == '0'

    def test_every_short_playlist_is_covered(self, monkeypatch):
        deps = _Deps(
            [{'id': 3, 'name': 'A'}, {'id': 4, 'name': 'B'}, {'id': 5, 'name': 'C'}],
            {'auto_mirror_3': _status(1, 9),
             'auto_mirror_4': _status(9, 9),
             'auto_mirror_5': _status(0, 4)},
        )
        out = _run(deps, monkeypatch)
        assert deps.sync_calls == ['3', '5']
        assert out['candidates'] == '2'


# ── the wiring, not just the function ───────────────────────────────────────

class TestChainWiring:
    def test_the_system_automation_closes_the_chain(self):
        """Without the seeded row the handler exists and is never called — which
        is indistinguishable from the bug. Pin the trigger AND the action: the
        chain is batch_complete -> scan -> scan_done -> db update -> THIS."""
        from core.automation_engine import SYSTEM_AUTOMATIONS
        by_action = {a['action_type']: a for a in SYSTEM_AUTOMATIONS}

        assert by_action['scan_library']['trigger_type'] == 'batch_complete'
        assert by_action['start_database_update']['trigger_type'] == 'library_scan_completed'

        resync = by_action.get('resync_incomplete_playlists')
        assert resync is not None, "nothing listens for database_update_completed"
        assert resync['trigger_type'] == 'database_update_completed'
        # Music-side: no owned_by tag, or the video Automations page claims it.
        assert not resync.get('owned_by')

    def test_the_event_it_listens_for_is_actually_emitted(self):
        """A trigger nothing emits is a chain that still ends one link early."""
        import re
        from pathlib import Path
        src = Path('web_server.py').read_text(encoding='utf-8')
        assert re.search(r"emit\(\s*'database_update_completed'", src), \
            "web_server no longer emits the event the re-sync hangs off"

    def test_the_action_is_offered_as_an_automation_block(self):
        from core.automation.blocks import ACTIONS
        block = next((b for b in ACTIONS
                      if b['type'] == 'resync_incomplete_playlists'), None)
        assert block is not None and block.get('available') is True
