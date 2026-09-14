"""Tests for core/downloads/master.py — full missing-tracks master worker."""

from __future__ import annotations

import threading
from types import SimpleNamespace

import pytest

from core.downloads import master as mw
from core.runtime_state import download_batches, download_tasks, tasks_lock


# ---------------------------------------------------------------------------
# Fixtures + fakes
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def reset_state():
    download_tasks.clear()
    download_batches.clear()
    yield
    download_tasks.clear()
    download_batches.clear()


class _FakeConfig:
    def __init__(self, values=None):
        self._v = values or {}

    def get(self, key, default=None):
        return self._v.get(key, default)

    def get_active_media_server(self):
        return self._v.get('_active_server', 'plex')


class _FakeDB:
    def __init__(self, found_tracks=None, album=None, album_tracks=None, album_confidence=0.95):
        self.found_tracks = found_tracks or {}  # (title_lower, artist_lower) -> confidence
        self.album = album
        self.album_tracks = album_tracks or []
        self.album_confidence = album_confidence
        self.sync_history_calls = []
        self.track_results_calls = []
        self.manual_matches = []

    def check_track_exists(self, title, artist, confidence_threshold=0.7, server_source=None, album=None):
        key = (title.lower().strip(), artist.lower().strip())
        if key in self.found_tracks:
            conf = self.found_tracks[key]
            return (object(), conf)  # (DatabaseTrack-ish, confidence)
        return (None, 0.0)

    def check_album_exists_with_editions(self, title, artist, confidence_threshold=0.7,
                                         expected_track_count=None, server_source=None, expected_year=None):
        return (self.album, self.album_confidence)

    def get_tracks_by_album(self, album_id):
        return self.album_tracks

    def _string_similarity(self, a, b):
        if a == b:
            return 1.0
        if a in b or b in a:
            return 0.85
        return 0.0

    def update_sync_history_completion(self, batch_id, **kwargs):
        self.sync_history_calls.append((batch_id, kwargs))

    def update_sync_history_track_results(self, batch_id, results_json):
        self.track_results_calls.append((batch_id, results_json))

    def get_manual_library_match(self, profile_id, source, source_track_id, server_source=''):
        for match in self.manual_matches:
            if (
                match["profile_id"] == profile_id
                and match["source"] == source
                and match["source_track_id"] == source_track_id
                and match.get("server_source", "") == server_source
            ):
                return match
        return None

    def find_manual_library_match_by_source_track_id(self, profile_id, source_track_id, server_source=''):
        for match in self.manual_matches:
            if match["profile_id"] == profile_id and match["source_track_id"] == source_track_id:
                return match
        return None


class _DBTrack:
    def __init__(self, title):
        self.title = title


class _DBAlbum:
    def __init__(self, id_, title):
        self.id = id_
        self.title = title


class _FakeSoulseek:
    def __init__(self, album_results=None, track_results=None, browse_files=None, parsed_tracks=None):
        self._album_results = album_results or []
        self._track_results = track_results or []
        self._browse_files = browse_files
        self._parsed_tracks = parsed_tracks or []
        self.search_calls = []

    async def search(self, query, timeout=30):
        self.search_calls.append(query)
        return (self._track_results, self._album_results)

    def filter_results_by_quality_preference(self, tracks):
        return tracks  # no-op, accept all

    async def browse_user_directory(self, username, path):
        return self._browse_files

    def parse_browse_results_to_tracks(self, username, browse_files, directory):
        return self._parsed_tracks


class _FakeSoulseekWrapper:
    """Wraps a soulseek client at .soulseek attribute (matches web_server pattern)."""
    def __init__(self, inner):
        self.soulseek = inner

    def client(self, name):
        return self.soulseek if name == 'soulseek' else None


class _FakePluginWrapper:
    def __init__(self, plugins):
        self._plugins = dict(plugins)

    def client(self, name):
        return self._plugins.get(name)


class _FakeAlbumBundleSoulseek:
    def __init__(self, outcome=None):
        self.calls = []
        self.outcome = outcome or {'success': True, 'files': ['/tmp/a.flac']}

    def download_album_to_staging(self, album, artist, staging, emit, **kwargs):
        self.calls.append((album, artist, staging, kwargs))
        emit({'state': 'staged', 'count': len(self.outcome.get('files', []))})
        return self.outcome


class _FakePreflightAlbumBundleSoulseek(_FakeSoulseek):
    def __init__(self, *args, outcome=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.calls = []
        self.outcome = outcome or {'success': True, 'files': ['/tmp/a.flac']}

    def download_album_to_staging(self, album, artist, staging, emit, **kwargs):
        self.calls.append((album, artist, staging, kwargs))
        emit({'state': 'staged', 'count': len(self.outcome.get('files', []))})
        return self.outcome


class _FakeMonitor:
    def __init__(self):
        self.started = []

    def start_monitoring(self, batch_id):
        self.started.append(batch_id)


class _FakeExecutor:
    def __init__(self):
        self.submitted = []

    def submit(self, fn, *args):
        self.submitted.append((fn, args))


class _FakeMBSvc:
    pass


class _FakeMBWorker:
    def __init__(self, svc=None):
        self.mb_service = svc


def _run_async_sync(coro):
    """Synchronously run a coroutine for tests."""
    import asyncio
    return asyncio.get_event_loop().run_until_complete(coro) if not asyncio.iscoroutine(coro) else asyncio.new_event_loop().run_until_complete(coro)


def _make_run_async():
    import asyncio
    def _runner(coro):
        loop = asyncio.new_event_loop()
        try:
            return loop.run_until_complete(coro)
        finally:
            loop.close()
    return _runner


def _build_deps(
    *,
    config=None,
    soulseek=None,
    run_async=None,
    mb_worker=None,
    mb_release_cache=None,
    mb_release_cache_lock=None,
    mb_release_detail_cache=None,
    mb_release_detail_cache_lock=None,
    normalize_album_cache_key=None,
    wishlist_remove=None,
    is_explicit_blocked=None,
    yt_states=None,
    tidal_states=None,
    deezer_states=None,
    spotify_states=None,
    executor=None,
    process_failed_auto=None,
    source_reuse_logger=None,
    monitor=None,
    start_next_batch=None,
    reset_wishlist_auto=None,
):
    return mw.MasterDeps(
        config_manager=config or _FakeConfig(),
        download_orchestrator=soulseek or _FakeSoulseekWrapper(_FakeSoulseek()),
        run_async=run_async or _make_run_async(),
        mb_worker=mb_worker,
        mb_release_cache=mb_release_cache if mb_release_cache is not None else {},
        mb_release_cache_lock=mb_release_cache_lock or threading.Lock(),
        mb_release_detail_cache=mb_release_detail_cache if mb_release_detail_cache is not None else {},
        mb_release_detail_cache_lock=mb_release_detail_cache_lock or threading.Lock(),
        normalize_album_cache_key=normalize_album_cache_key or (lambda s: s.lower().strip()),
        check_and_remove_track_from_wishlist_by_metadata=wishlist_remove or (lambda td: None),
        is_explicit_blocked=is_explicit_blocked or (lambda td: False),
        youtube_playlist_states=yt_states if yt_states is not None else {},
        tidal_discovery_states=tidal_states if tidal_states is not None else {},
        deezer_discovery_states=deezer_states if deezer_states is not None else {},
        spotify_public_discovery_states=spotify_states if spotify_states is not None else {},
        missing_download_executor=executor or _FakeExecutor(),
        process_failed_tracks_to_wishlist_exact_with_auto_completion=process_failed_auto or (lambda bid: None),
        source_reuse_logger=source_reuse_logger or _StubLogger(),
        download_monitor=monitor or _FakeMonitor(),
        start_next_batch_of_downloads=start_next_batch or (lambda bid: None),
        reset_wishlist_auto_processing=reset_wishlist_auto or (lambda: None),
    )


class _StubLogger:
    def info(self, *a, **kw): pass
    def warning(self, *a, **kw): pass
    def error(self, *a, **kw): pass
    def debug(self, *a, **kw): pass


def _seed_batch(batch_id, **overrides):
    base = {
        'phase': 'queued',
        'queue': [],
        'analysis_total': 0,
        'analysis_processed': 0,
        'analysis_results': [],
    }
    base.update(overrides)
    download_batches[batch_id] = base


def _slsk_track(title, number, folder='Artist/Test Album'):
    return SimpleNamespace(
        username='peer',
        filename=f'{folder}/{number:02d} - {title}.flac',
        title=title,
        track_number=number,
        quality='flac',
        bitrate=None,
        duration=180000,
        size=20_000_000,
        free_upload_slots=1,
        upload_speed=2_000_000,
        queue_length=0,
        quality_score=1.0,
    )


def _album_result(username, path, title, tracks, *, artist='Artist', year='2020',
                  quality_score=0.9):
    return SimpleNamespace(
        username=username,
        album_path=path,
        album_title=title,
        artist=artist,
        year=year,
        track_count=len(tracks),
        tracks=tracks,
        dominant_quality='flac',
        quality_score=quality_score,
    )


# ---------------------------------------------------------------------------
# PHASE 1: analysis
# ---------------------------------------------------------------------------

def test_analysis_phase_sets_state(monkeypatch):
    """Analysis phase marks batch counters; phase moves to 'downloading' when there are missing tracks."""
    db = _FakeDB()  # found_tracks empty → every track marked missing
    monkeypatch.setattr('database.music_database.MusicDatabase', lambda: db)

    _seed_batch('B1')
    deps = _build_deps()
    tracks = [{'name': 'T1', 'artists': ['A']}]

    mw.run_full_missing_tracks_process('B1', 'P1', tracks, deps)

    # Track was missing → progressed to 'downloading' phase
    assert download_batches['B1']['phase'] == 'downloading'
    assert download_batches['B1']['analysis_processed'] == 1
    assert len(download_batches['B1']['analysis_results']) == 1


def test_force_download_treats_all_as_missing(monkeypatch):
    """force_download_all skips DB check — every track marked missing."""
    db = _FakeDB(found_tracks={('t1', 'a'): 1.0, ('t2', 'a'): 1.0})  # would otherwise be found
    monkeypatch.setattr('database.music_database.MusicDatabase', lambda: db)

    _seed_batch('B2', force_download_all=True)
    deps = _build_deps()
    tracks = [
        {'name': 'T1', 'artists': ['A']},
        {'name': 'T2', 'artists': ['A']},
    ]

    mw.run_full_missing_tracks_process('B2', 'playlist1', tracks, deps)

    # All 2 tracks should produce queue tasks (treated as missing)
    assert len(download_batches['B2']['queue']) == 2
    assert download_batches['B2']['phase'] == 'downloading'


def test_manual_match_overrides_internal_force_download(monkeypatch):
    """Internal wishlist force mode still honors explicit manual library matches."""
    db = _FakeDB()
    monkeypatch.setattr('database.music_database.MusicDatabase', lambda: db)
    monkeypatch.setattr(
        'core.library.manual_library_match.get_match_for_track',
        lambda *_args, **_kwargs: {'id': 1, 'library_track_id': 42},
    )

    removed = []
    _seed_batch(
        'B2a',
        force_download_all=True,
        ignore_manual_matches=False,
        profile_id=1,
        batch_source='spotify',
    )
    deps = _build_deps(wishlist_remove=lambda td: removed.append(td.get('name')))
    tracks = [{'id': 'spotify-track-1', 'name': 'T1', 'artists': ['A']}]

    mw.run_full_missing_tracks_process('B2a', 'wishlist', tracks, deps)

    assert download_batches['B2a']['queue'] == []
    assert download_batches['B2a']['analysis_results'][0]['found'] is True
    assert download_batches['B2a']['analysis_results'][0]['match_reason'] == 'manual_library_match'
    assert removed == ['T1']


def test_manual_match_saved_under_mirrored_source_overrides_wishlist_batch(monkeypatch):
    """Wishlist batches honor matches saved from mirrored sync history."""
    db = _FakeDB()
    db.manual_matches.append({
        "id": 1,
        "profile_id": 1,
        "source": "mirrored",
        "source_track_id": "track-abc",
        "server_source": "",
        "library_track_id": 42,
    })
    monkeypatch.setattr('database.music_database.MusicDatabase', lambda: db)

    removed = []
    _seed_batch(
        'B2m',
        force_download_all=True,
        ignore_manual_matches=False,
        profile_id=1,
        batch_source='wishlist',
    )
    deps = _build_deps(wishlist_remove=lambda td: removed.append(td.get('name')))
    tracks = [{'id': 'track-abc', 'name': 'Coffee Break', 'artists': [{'name': 'Zeds Dead'}], 'provider': 'wishlist'}]

    mw.run_full_missing_tracks_process('B2m', 'wishlist', tracks, deps)

    assert download_batches['B2m']['queue'] == []
    assert download_batches['B2m']['analysis_results'][0]['found'] is True
    assert download_batches['B2m']['analysis_results'][0]['match_reason'] == 'manual_library_match'
    assert removed == ['Coffee Break']


def test_explicit_force_download_ignores_manual_match(monkeypatch):
    """User-facing Force Download All can intentionally bypass manual matches."""
    db = _FakeDB()
    monkeypatch.setattr('database.music_database.MusicDatabase', lambda: db)

    calls = []
    monkeypatch.setattr(
        'core.library.manual_library_match.get_match_for_track',
        lambda *_args, **_kwargs: calls.append(True) or {'id': 1, 'library_track_id': 42},
    )

    _seed_batch(
        'B2b',
        force_download_all=True,
        ignore_manual_matches=True,
        profile_id=1,
        batch_source='spotify',
    )
    deps = _build_deps()
    tracks = [{'id': 'spotify-track-1', 'name': 'T1', 'artists': ['A']}]

    mw.run_full_missing_tracks_process('B2b', 'playlist1', tracks, deps)

    assert calls == []
    assert len(download_batches['B2b']['queue']) == 1
    assert download_batches['B2b']['analysis_results'][0]['found'] is False


def test_found_tracks_trigger_wishlist_removal(monkeypatch):
    """When DB lookup succeeds, master worker invokes wishlist removal callback."""
    db = _FakeDB(found_tracks={('t1', 'a'): 0.9})
    monkeypatch.setattr('database.music_database.MusicDatabase', lambda: db)

    removed = []
    deps = _build_deps(wishlist_remove=lambda td: removed.append(td.get('name')))

    _seed_batch('B3')
    tracks = [{'name': 'T1', 'artists': ['A']}]

    mw.run_full_missing_tracks_process('B3', 'P1', tracks, deps)

    assert removed == ['T1']


def test_explicit_filter_removes_blocked_tracks(monkeypatch):
    """When content_filter.allow_explicit=False, blocked tracks dropped from missing set."""
    db = _FakeDB()
    monkeypatch.setattr('database.music_database.MusicDatabase', lambda: db)

    config = _FakeConfig({'content_filter.allow_explicit': False})
    deps = _build_deps(
        config=config,
        is_explicit_blocked=lambda td: td.get('name') == 'BLOCKED',
    )

    _seed_batch('B4')
    tracks = [
        {'name': 'CLEAN', 'artists': ['A']},
        {'name': 'BLOCKED', 'artists': ['A']},
    ]

    mw.run_full_missing_tracks_process('B4', 'P1', tracks, deps)

    # only CLEAN survives the filter
    assert len(download_batches['B4']['queue']) == 1


# ---------------------------------------------------------------------------
# PHASE 2: no missing -> complete + state updates
# ---------------------------------------------------------------------------

def test_no_missing_marks_batch_complete(monkeypatch):
    """If every track found in DB, batch transitions directly to complete."""
    db = _FakeDB(found_tracks={('t1', 'a'): 0.9, ('t2', 'a'): 0.9})
    monkeypatch.setattr('database.music_database.MusicDatabase', lambda: db)

    deps = _build_deps()
    _seed_batch('B5')
    tracks = [
        {'name': 'T1', 'artists': ['A']},
        {'name': 'T2', 'artists': ['A']},
    ]

    mw.run_full_missing_tracks_process('B5', 'P1', tracks, deps)

    assert download_batches['B5']['phase'] == 'complete'
    assert 'completion_time' in download_batches['B5']
    assert db.sync_history_calls  # sync history written


def test_no_missing_updates_youtube_playlist_state(monkeypatch):
    """YouTube playlist phase set to 'download_complete' on no-missing."""
    db = _FakeDB(found_tracks={('t1', 'a'): 0.9})
    monkeypatch.setattr('database.music_database.MusicDatabase', lambda: db)

    yt_states = {'abc123': {'phase': 'discovered'}}
    deps = _build_deps(yt_states=yt_states)

    _seed_batch('B6')
    mw.run_full_missing_tracks_process('B6', 'youtube_abc123', [{'name': 'T1', 'artists': ['A']}], deps)

    assert yt_states['abc123']['phase'] == 'download_complete'


def test_no_missing_with_auto_wishlist_submits_completion(monkeypatch):
    """auto_initiated wishlist batch with no missing tracks submits auto-completion handler."""
    db = _FakeDB(found_tracks={('t1', 'a'): 0.9})
    monkeypatch.setattr('database.music_database.MusicDatabase', lambda: db)

    executor = _FakeExecutor()
    auto_called = []
    deps = _build_deps(executor=executor, process_failed_auto=lambda bid: auto_called.append(bid))

    _seed_batch('B7', auto_initiated=True)
    mw.run_full_missing_tracks_process('B7', 'wishlist', [{'name': 'T1', 'artists': ['A']}], deps)

    assert len(executor.submitted) == 1
    fn, args = executor.submitted[0]
    assert args == ('B7',)


def test_no_missing_album_does_not_dispatch_torrent_bundle(monkeypatch):
    """Release-level sources must wait until analysis confirms missing tracks."""
    album = _DBAlbum(id_=42, title='Test Album')
    db = _FakeDB(album=album, album_tracks=[_DBTrack('T1')])
    monkeypatch.setattr('database.music_database.MusicDatabase', lambda: db)

    plugin = _FakeAlbumBundleSoulseek()
    deps = _build_deps(
        config=_FakeConfig({'download_source.mode': 'torrent'}),
        soulseek=_FakePluginWrapper({'torrent': plugin}),
    )
    _seed_batch(
        'B7a',
        is_album_download=True,
        album_context={'name': 'Test Album', 'total_tracks': 1},
        artist_context={'name': 'Artist'},
    )

    mw.run_full_missing_tracks_process(
        'B7a',
        'album:1',
        [{'name': 'T1', 'artists': ['Artist'], 'track_number': 1}],
        deps,
    )

    assert plugin.calls == []
    assert download_batches['B7a']['phase'] == 'complete'
    assert 'album_bundle_source' not in download_batches['B7a']


# ---------------------------------------------------------------------------
# Album fast path
# ---------------------------------------------------------------------------

def test_album_fast_path_direct_match(monkeypatch):
    """Album lookup + direct title match → track marked found, no queue entry."""
    album = _DBAlbum(id_=42, title='Test Album')
    album_tracks = [_DBTrack('T1'), _DBTrack('T2')]
    db = _FakeDB(album=album, album_tracks=album_tracks)
    monkeypatch.setattr('database.music_database.MusicDatabase', lambda: db)

    deps = _build_deps()
    _seed_batch('B8',
                is_album_download=True,
                album_context={'name': 'Test Album', 'total_tracks': 2},
                artist_context={'name': 'Artist'})

    tracks = [{'name': 'T1', 'artists': ['Artist']}, {'name': 'T2', 'artists': ['Artist']}]
    mw.run_full_missing_tracks_process('B8', 'album:1', tracks, deps)

    assert download_batches['B8']['phase'] == 'complete'  # all matched


def test_album_fast_path_misses_fall_through_to_global(monkeypatch):
    """Album lookup with track not in album → fuzzy fallback or per-track global search."""
    album = _DBAlbum(id_=42, title='Test Album')
    album_tracks = [_DBTrack('Existing')]
    db = _FakeDB(
        album=album,
        album_tracks=album_tracks,
        found_tracks={},  # global search finds nothing for Other
    )
    monkeypatch.setattr('database.music_database.MusicDatabase', lambda: db)

    deps = _build_deps()
    _seed_batch('B9',
                is_album_download=True,
                album_context={'name': 'Test Album', 'total_tracks': 2},
                artist_context={'name': 'Artist'})

    # 'Other' is not in album, allow_duplicates default True → marked missing without global search
    tracks = [{'name': 'Other', 'artists': ['Artist']}]
    mw.run_full_missing_tracks_process('B9', 'album:1', tracks, deps)

    assert len(download_batches['B9']['queue']) == 1


# ---------------------------------------------------------------------------
# MB release preflight
# ---------------------------------------------------------------------------

def test_mb_release_preflight_caches_mbid(monkeypatch):
    """MB preflight caches release MBID under both normalized and exact keys."""
    db = _FakeDB()
    monkeypatch.setattr('database.music_database.MusicDatabase', lambda: db)

    fake_release = {'id': 'mbid-xyz', 'title': 'Test Album'}

    def fake_find_best_release(album, artist, count, svc):
        return fake_release

    import core.album_consistency as ac
    monkeypatch.setattr(ac, '_find_best_release', fake_find_best_release)

    cache = {}
    detail_cache = {}
    deps = _build_deps(
        mb_worker=_FakeMBWorker(svc=_FakeMBSvc()),
        mb_release_cache=cache,
        mb_release_detail_cache=detail_cache,
    )
    _seed_batch('B10',
                is_album_download=True,
                album_context={'name': 'Test Album', 'total_tracks': 1},
                artist_context={'name': 'Artist'})

    mw.run_full_missing_tracks_process('B10', 'album:1', [{'name': 'T1', 'artists': ['Artist']}], deps)

    # Should have cached under both normalized and exact-lower keys
    assert ('test album', 'artist') in cache
    assert cache[('test album', 'artist')] == 'mbid-xyz'
    assert detail_cache['mbid-xyz'] == fake_release


def test_mb_release_preflight_skipped_when_no_mb_worker(monkeypatch):
    """Without mb_worker, preflight quietly skips."""
    db = _FakeDB()
    monkeypatch.setattr('database.music_database.MusicDatabase', lambda: db)

    cache = {}
    deps = _build_deps(mb_worker=None, mb_release_cache=cache)
    _seed_batch('B11',
                is_album_download=True,
                album_context={'name': 'Album', 'total_tracks': 1},
                artist_context={'name': 'Artist'})

    mw.run_full_missing_tracks_process('B11', 'album:1', [{'name': 'T1', 'artists': ['Artist']}], deps)

    assert cache == {}  # nothing cached


# ---------------------------------------------------------------------------
# Soulseek album preflight
# ---------------------------------------------------------------------------

def test_soulseek_album_preflight_scores_release_folder_over_larger_wrong_edition(monkeypatch):
    """Album preflight chooses the folder whose tracklist matches the target release."""
    db = _FakeDB()
    monkeypatch.setattr('database.music_database.MusicDatabase', lambda: db)

    target_tracks = [
        {'name': f'Track {i}', 'artists': ['Artist'], 'track_number': i}
        for i in range(1, 11)
    ]
    correct_tracks = [_slsk_track(f'Track {i}', i, folder='Artist/Test Album (2020)') for i in range(1, 11)]
    wrong_tracks = [
        _slsk_track(f'Remix {i}', i, folder='Artist/Test Album Deluxe Remixes (2020)')
        for i in range(1, 13)
    ]
    wrong = _album_result(
        'slow-peer',
        'Artist/Test Album Deluxe Remixes (2020)',
        'Test Album Deluxe Remixes',
        wrong_tracks,
        quality_score=1.0,
    )
    correct = _album_result(
        'good-peer',
        'Artist/Test Album (2020)',
        'Test Album',
        correct_tracks,
        quality_score=0.8,
    )
    slsk = _FakeSoulseek(album_results=[wrong, correct], browse_files=[{'filename': 'x.flac'}],
                         parsed_tracks=correct_tracks)
    deps = _build_deps(
        config=_FakeConfig({'download_source.mode': 'soulseek'}),
        soulseek=_FakeSoulseekWrapper(slsk),
    )
    _seed_batch(
        'B22',
        is_album_download=True,
        album_context={'name': 'Test Album', 'total_tracks': 10, 'release_date': '2020-01-01'},
        artist_context={'name': 'Artist'},
    )

    mw.run_full_missing_tracks_process('B22', 'album:1', target_tracks, deps)

    assert download_batches['B22']['last_good_source'] == {
        'username': 'good-peer',
        'folder_path': 'Artist/Test Album (2020)',
    }
    assert download_batches['B22']['source_folder_tracks'] == correct_tracks


def test_soulseek_album_preflight_runs_when_soulseek_is_hybrid_primary(monkeypatch):
    """Album preflight runs for hybrid album downloads when Soulseek is first."""
    db = _FakeDB()
    monkeypatch.setattr('database.music_database.MusicDatabase', lambda: db)

    tracks = [{'name': 'T1', 'artists': ['Artist'], 'track_number': 1}]
    folder_tracks = [_slsk_track('T1', 1)]
    album = _album_result('peer', 'Artist/Test Album', 'Test Album', folder_tracks)
    slsk = _FakeSoulseek(album_results=[album], browse_files=None, parsed_tracks=folder_tracks)
    config = _FakeConfig({
        'download_source.mode': 'hybrid',
        'download_source.hybrid_order': ['soulseek', 'hifi'],
    })
    deps = _build_deps(config=config, soulseek=_FakeSoulseekWrapper(slsk))
    _seed_batch(
        'B23',
        is_album_download=True,
        album_context={'name': 'Test Album', 'total_tracks': 1},
        artist_context={'name': 'Artist'},
    )

    mw.run_full_missing_tracks_process('B23', 'album:1', tracks, deps)

    assert slsk.search_calls
    assert download_batches['B23']['last_good_source']['username'] == 'peer'


def test_soulseek_album_preflight_does_not_jump_ahead_of_hybrid_primary(monkeypatch):
    """If Soulseek is only a fallback source, album preflight does not preempt source order."""
    db = _FakeDB()
    monkeypatch.setattr('database.music_database.MusicDatabase', lambda: db)

    tracks = [{'name': 'T1', 'artists': ['Artist'], 'track_number': 1}]
    folder_tracks = [_slsk_track('T1', 1)]
    album = _album_result('peer', 'Artist/Test Album', 'Test Album', folder_tracks)
    slsk = _FakeSoulseek(album_results=[album], browse_files=None, parsed_tracks=folder_tracks)
    config = _FakeConfig({
        'download_source.mode': 'hybrid',
        'download_source.hybrid_order': ['deezer_dl', 'soulseek'],
    })
    deps = _build_deps(config=config, soulseek=_FakeSoulseekWrapper(slsk))
    _seed_batch(
        'B24',
        is_album_download=True,
        album_context={'name': 'Test Album', 'total_tracks': 1},
        artist_context={'name': 'Artist'},
    )

    mw.run_full_missing_tracks_process('B24', 'album:1', tracks, deps)

    assert slsk.search_calls == []
    assert 'last_good_source' not in download_batches['B24']


def test_soulseek_album_bundle_runs_after_missing_analysis(monkeypatch):
    """Soulseek whole-folder bundles should engage only after analysis
    has confirmed there is something missing."""
    db = _FakeDB()
    monkeypatch.setattr('database.music_database.MusicDatabase', lambda: db)

    plugin = _FakeAlbumBundleSoulseek()
    deps = _build_deps(
        config=_FakeConfig({'download_source.mode': 'soulseek'}),
        soulseek=_FakeSoulseekWrapper(plugin),
    )
    _seed_batch(
        'B25',
        is_album_download=True,
        album_context={'name': 'Test Album', 'total_tracks': 1},
        artist_context={'name': 'Artist'},
    )
    tracks = [{'name': 'T1', 'artists': ['Artist'], 'track_number': 1}]

    mw.run_full_missing_tracks_process('B25', 'album:1', tracks, deps)

    assert len(plugin.calls) == 1
    album, artist, staging, kwargs = plugin.calls[0]
    assert (album, artist) == ('Test Album', 'Artist')
    assert staging.replace('\\', '/').endswith('storage/album_bundle_staging/B25')
    assert kwargs == {}
    assert download_batches['B25']['album_bundle_source'] == 'soulseek'
    assert download_batches['B25']['album_bundle_private_staging'] is True
    assert download_batches['B25']['album_bundle_state'] == 'staged'
    assert 'last_good_source' not in download_batches['B25']


def test_hybrid_first_soulseek_uses_album_bundle(monkeypatch):
    """Hybrid keeps fallback semantics, but the first source can own
    album-bundle downloads when it supports them."""
    db = _FakeDB()
    monkeypatch.setattr('database.music_database.MusicDatabase', lambda: db)

    plugin = _FakeAlbumBundleSoulseek()
    deps = _build_deps(
        config=_FakeConfig({
            'download_source.mode': 'hybrid',
            'download_source.hybrid_order': ['soulseek', 'hifi'],
        }),
        soulseek=_FakeSoulseekWrapper(plugin),
    )
    _seed_batch(
        'B26',
        is_album_download=True,
        album_context={'name': 'Test Album', 'total_tracks': 1},
        artist_context={'name': 'Artist'},
    )
    tracks = [{'name': 'T1', 'artists': ['Artist'], 'track_number': 1}]

    mw.run_full_missing_tracks_process('B26', 'album:1', tracks, deps)

    assert len(plugin.calls) == 1
    assert download_batches['B26']['album_bundle_source'] == 'soulseek'
    assert download_batches['B26']['album_bundle_private_staging'] is True


def test_soulseek_album_bundle_uses_preflight_source_without_preloading_reuse(monkeypatch):
    """When the bundle path stages files, workers must claim staging
    before any Soulseek source-reuse attempt can fire."""
    db = _FakeDB()
    monkeypatch.setattr('database.music_database.MusicDatabase', lambda: db)

    tracks = [{'name': 'T1', 'artists': ['Artist'], 'track_number': 1}]
    folder_tracks = [_slsk_track('T1', 1, folder='Artist/Test Album')]
    album = _album_result('peer', 'Artist/Test Album', 'Test Album', folder_tracks)
    slsk = _FakePreflightAlbumBundleSoulseek(
        album_results=[album],
        browse_files=None,
        parsed_tracks=folder_tracks,
    )
    deps = _build_deps(
        config=_FakeConfig({'download_source.mode': 'soulseek'}),
        soulseek=_FakeSoulseekWrapper(slsk),
    )
    _seed_batch(
        'B28',
        is_album_download=True,
        album_context={'name': 'Test Album', 'total_tracks': 1},
        artist_context={'name': 'Artist'},
    )

    mw.run_full_missing_tracks_process('B28', 'album:1', tracks, deps)

    assert len(slsk.calls) == 1
    assert slsk.calls[0][3] == {
        'preferred_source': {
            'username': 'peer',
            'folder_path': 'Artist/Test Album',
        },
        'preferred_tracks': folder_tracks,
    }
    assert download_batches['B28']['album_bundle_private_staging'] is True
    assert 'last_good_source' not in download_batches['B28']
    assert 'source_folder_tracks' not in download_batches['B28']


def test_hybrid_first_torrent_uses_album_bundle_before_per_track(monkeypatch):
    db = _FakeDB()
    monkeypatch.setattr('database.music_database.MusicDatabase', lambda: db)

    plugin = _FakeAlbumBundleSoulseek()
    deps = _build_deps(
        config=_FakeConfig({
            'download_source.mode': 'hybrid',
            'download_source.hybrid_order': ['torrent', 'soulseek'],
        }),
        soulseek=_FakePluginWrapper({'torrent': plugin}),
    )
    _seed_batch(
        'B27',
        is_album_download=True,
        album_context={'name': 'Test Album', 'total_tracks': 1},
        artist_context={'name': 'Artist'},
    )
    tracks = [{'name': 'T1', 'artists': ['Artist'], 'track_number': 1}]

    mw.run_full_missing_tracks_process('B27', 'album:1', tracks, deps)

    assert len(plugin.calls) == 1
    assert download_batches['B27']['album_bundle_source'] == 'torrent'


def test_album_bundle_fallback_clears_private_staging(monkeypatch):
    db = _FakeDB()
    monkeypatch.setattr('database.music_database.MusicDatabase', lambda: db)

    plugin = _FakeAlbumBundleSoulseek({
        'success': False,
        'fallback': True,
        'error': 'No release passed validation',
    })
    deps = _build_deps(
        config=_FakeConfig({'download_source.mode': 'torrent'}),
        soulseek=_FakePluginWrapper({'torrent': plugin}),
    )
    _seed_batch(
        'B29',
        is_album_download=True,
        album_context={'name': 'Test Album', 'total_tracks': 1},
        artist_context={'name': 'Artist'},
    )
    tracks = [{'name': 'T1', 'artists': ['Artist'], 'track_number': 1}]

    mw.run_full_missing_tracks_process('B29', 'album:1', tracks, deps)

    assert len(plugin.calls) == 1
    assert download_batches['B29']['album_bundle_state'] == 'fallback'
    assert download_batches['B29']['album_bundle_private_staging'] is False
    assert download_batches['B29']['album_bundle_staging_path'] is None
    assert len(download_batches['B29']['queue']) == 1


# ---------------------------------------------------------------------------
# Task creation
# ---------------------------------------------------------------------------

def test_missing_tracks_create_queue_tasks(monkeypatch):
    """Missing tracks produce download_tasks + are appended to batch queue."""
    db = _FakeDB()
    monkeypatch.setattr('database.music_database.MusicDatabase', lambda: db)

    deps = _build_deps()
    _seed_batch('B12')

    tracks = [{'name': 'T1', 'artists': ['A']}, {'name': 'T2', 'artists': ['A']}]
    mw.run_full_missing_tracks_process('B12', 'P1', tracks, deps)

    assert len(download_batches['B12']['queue']) == 2
    for task_id in download_batches['B12']['queue']:
        assert task_id in download_tasks
        assert download_tasks[task_id]['status'] == 'pending'
        assert download_tasks[task_id]['batch_id'] == 'B12'


def test_album_download_injects_explicit_context(monkeypatch):
    """Album downloads embed _explicit_album_context + _explicit_artist_context per task."""
    db = _FakeDB()
    monkeypatch.setattr('database.music_database.MusicDatabase', lambda: db)

    deps = _build_deps()
    album_ctx = {'name': 'Album', 'total_tracks': 1}
    artist_ctx = {'name': 'Artist'}
    _seed_batch('B13',
                is_album_download=True,
                album_context=album_ctx,
                artist_context=artist_ctx)

    mw.run_full_missing_tracks_process('B13', 'album:1', [{'name': 'T1', 'artists': ['Artist']}], deps)

    assert len(download_batches['B13']['queue']) == 1
    task_id = download_batches['B13']['queue'][0]
    info = download_tasks[task_id]['track_info']
    assert info['_explicit_album_context'] == album_ctx
    assert info['_explicit_artist_context'] == artist_ctx
    assert info['_is_explicit_album_download'] is True


def test_wishlist_album_grouping_resolves_artist(monkeypatch):
    """Wishlist tracks sharing an album_id all get the same artist context."""
    db = _FakeDB()
    monkeypatch.setattr('database.music_database.MusicDatabase', lambda: db)

    deps = _build_deps()
    _seed_batch('B14')

    # Two tracks on same album with different track-level artists — wishlist grouping
    # should resolve ONE artist for the album (first track wins).
    tracks = [
        {
            'name': 'T1', 'artists': [{'name': 'Track Artist 1'}],
            'spotify_data': {
                'album': {'id': 'A1', 'name': 'Test Album', 'artists': [{'name': 'Album Artist'}]},
                'artists': [{'name': 'Track Artist 1'}],
            },
        },
        {
            'name': 'T2', 'artists': [{'name': 'Track Artist 2'}],
            'spotify_data': {
                'album': {'id': 'A1', 'name': 'Test Album', 'artists': [{'name': 'Album Artist'}]},
                'artists': [{'name': 'Track Artist 2'}],
            },
        },
    ]
    mw.run_full_missing_tracks_process('B14', 'wishlist', tracks, deps)

    assert len(download_batches['B14']['queue']) == 2
    artist_names = set()
    for tid in download_batches['B14']['queue']:
        info = download_tasks[tid]['track_info']
        artist_names.add(info['_explicit_artist_context']['name'])

    # Both tracks should resolve to the same album-level artist
    assert len(artist_names) == 1
    assert 'Album Artist' in artist_names


def test_wishlist_album_grouping_uses_shared_rich_album_context(monkeypatch):
    """Tracks in one wishlist album reuse the richest album context to avoid year folder splits."""
    db = _FakeDB()
    monkeypatch.setattr('database.music_database.MusicDatabase', lambda: db)

    deps = _build_deps()
    _seed_batch('B14b')

    tracks = [
        {
            'name': 'T1', 'artists': [{'name': 'Artist'}],
            'spotify_data': {
                'album': {
                    'id': 'A1', 'name': 'Test Album', 'artists': [{'name': 'Album Artist'}],
                    'release_date': '2024-05-05', 'total_tracks': 2,
                    'album_type': 'album', 'images': [{'url': 'http://img'}],
                },
                'artists': [{'name': 'Artist'}],
            },
        },
        {
            'name': 'T2', 'artists': [{'name': 'Artist'}],
            'spotify_data': {
                'album': {'id': 'A1', 'name': 'Test Album'},
                'artists': [{'name': 'Artist'}],
            },
        },
    ]

    mw.run_full_missing_tracks_process('B14b', 'wishlist', tracks, deps)

    release_dates = set()
    images = set()
    for tid in download_batches['B14b']['queue']:
        album_ctx = download_tasks[tid]['track_info']['_explicit_album_context']
        release_dates.add(album_ctx['release_date'])
        images.add(album_ctx['images'][0]['url'])

    assert release_dates == {'2024-05-05'}
    assert images == {'http://img'}


def test_organize_by_playlist_keeps_provenance_not_routing(monkeypatch):
    """Organize-by-playlist no longer routes the file per-track. Each track imports
    NORMALLY into Artist/Album (what a normal download does); the playlist folder
    is built as links AFTER the batch. So the old routing flag
    `_playlist_folder_mode` is NOT set, but `_playlist_name` provenance IS kept
    (core/downloads/origin.py)."""
    db = _FakeDB()
    monkeypatch.setattr('database.music_database.MusicDatabase', lambda: db)

    deps = _build_deps()
    _seed_batch('B15',
                playlist_folder_mode=True,
                playlist_name='My Mix')

    mw.run_full_missing_tracks_process('B15', 'P1', [{'name': 'T1', 'artists': ['A']}], deps)

    task_id = download_batches['B15']['queue'][0]
    info = download_tasks[task_id]['track_info']
    assert '_playlist_folder_mode' not in info        # routing retired → normal import
    assert info['_playlist_name'] == 'My Mix'          # provenance preserved


# ---------------------------------------------------------------------------
# Hand-off to monitor + start_next_batch
# ---------------------------------------------------------------------------

def test_handoff_starts_monitor_and_next_batch(monkeypatch):
    """After task creation, master worker starts monitor + next batch."""
    db = _FakeDB()
    monkeypatch.setattr('database.music_database.MusicDatabase', lambda: db)

    monitor = _FakeMonitor()
    started_next = []
    deps = _build_deps(monitor=monitor, start_next_batch=lambda bid: started_next.append(bid))

    _seed_batch('B16')
    mw.run_full_missing_tracks_process('B16', 'P1', [{'name': 'T1', 'artists': ['A']}], deps)

    assert monitor.started == ['B16']
    assert started_next == ['B16']


# ---------------------------------------------------------------------------
# Multi-disc album_context
# ---------------------------------------------------------------------------

def test_multi_disc_total_discs_computed(monkeypatch):
    """For album downloads, total_discs computed from max(disc_number) across all tracks."""
    db = _FakeDB()
    monkeypatch.setattr('database.music_database.MusicDatabase', lambda: db)

    deps = _build_deps()
    album_ctx = {'name': 'Album', 'total_tracks': 3}
    _seed_batch('B17',
                is_album_download=True,
                album_context=album_ctx,
                artist_context={'name': 'Artist'})

    tracks = [
        {'name': 'T1', 'artists': ['Artist'], 'disc_number': 1},
        {'name': 'T2', 'artists': ['Artist'], 'disc_number': 2},
        {'name': 'T3', 'artists': ['Artist'], 'disc_number': 2},
    ]
    mw.run_full_missing_tracks_process('B17', 'album:1', tracks, deps)

    assert album_ctx['total_discs'] == 2


# ---------------------------------------------------------------------------
# Error handler
# ---------------------------------------------------------------------------

def test_error_handler_marks_batch_error(monkeypatch):
    """Exception during analysis → batch.phase=error, batch.error=str(exception)."""
    def boom():
        raise RuntimeError("DB exploded")
    monkeypatch.setattr('database.music_database.MusicDatabase', boom)

    deps = _build_deps()
    _seed_batch('B18')

    mw.run_full_missing_tracks_process('B18', 'P1', [{'name': 'T1', 'artists': ['A']}], deps)

    assert download_batches['B18']['phase'] == 'error'
    assert 'DB exploded' in download_batches['B18']['error']


def test_error_handler_resets_youtube_phase(monkeypatch):
    """Error on a youtube_<hash> playlist resets that playlist's phase to 'discovered'."""
    def boom():
        raise RuntimeError("kaboom")
    monkeypatch.setattr('database.music_database.MusicDatabase', boom)

    yt_states = {'abc': {'phase': 'downloading'}}
    deps = _build_deps(yt_states=yt_states)
    _seed_batch('B19')

    mw.run_full_missing_tracks_process('B19', 'youtube_abc', [{'name': 'T1', 'artists': ['A']}], deps)

    assert yt_states['abc']['phase'] == 'discovered'


def test_error_handler_resets_auto_wishlist(monkeypatch):
    """Auto-initiated wishlist error invokes reset_wishlist_auto_processing callback."""
    def boom():
        raise RuntimeError("oops")
    monkeypatch.setattr('database.music_database.MusicDatabase', boom)

    reset_called = []
    deps = _build_deps(reset_wishlist_auto=lambda: reset_called.append(True))
    _seed_batch('B20', auto_initiated=True)

    mw.run_full_missing_tracks_process('B20', 'wishlist', [{'name': 'T1', 'artists': ['A']}], deps)

    assert reset_called == [True]


# ---------------------------------------------------------------------------
# Batch removed mid-flight
# ---------------------------------------------------------------------------

def test_batch_removed_before_phase_two_returns_cleanly(monkeypatch):
    """If batch is deleted between analysis and download phase, function returns without crashing."""
    db = _FakeDB(found_tracks={('t1', 'a'): 0.9})  # marks T1 found → wishlist_remove fires
    monkeypatch.setattr('database.music_database.MusicDatabase', lambda: db)

    monitor = _FakeMonitor()
    next_batch_calls = []

    # Wishlist removal callback deletes the batch mid-analysis to simulate cancel.
    # T1 will be analyzed as 'found' → callback fires → batch deleted.
    def kill_batch(td):
        download_batches.pop('B21', None)

    deps = _build_deps(
        wishlist_remove=kill_batch,
        monitor=monitor,
        start_next_batch=lambda bid: next_batch_calls.append(bid),
    )
    _seed_batch('B21')

    # Should not raise even though batch vanishes during analysis loop
    mw.run_full_missing_tracks_process('B21', 'P1', [{'name': 'T1', 'artists': ['A']}], deps)

    # All tracks were 'found' → no missing → no monitor/next_batch calls
    # (batch was deleted, so phase=complete update silently no-ops)
    assert monitor.started == []
    assert next_batch_calls == []


# ---------------------------------------------------------------------------
# Prefer deluxe editions (wishlist.prefer_deluxe_editions)
# ---------------------------------------------------------------------------
# Owning "X" used to count as owning every song "X (Deluxe Edition)" shares with
# it, so a deluxe download fetched only the bonus tracks, into a folder of their
# own, and the album was split. With the option on, an owned SMALLER edition does
# not own the deluxe's songs; off, the analysis is exactly what it was.

_STANDARD_TITLE = 'Curtain Call: The Hits'
_DELUXE_TITLE = 'Curtain Call: The Hits (Deluxe Edition)'
_STANDARD_SONGS = ['My Name Is', 'The Way I Am', 'Stan', 'Without Me', 'Mockingbird']
_BONUS_SONGS = ['Intro', 'FACK', 'Shit On You', 'Criminal']
_DELUXE_SONGS = _BONUS_SONGS[:2] + _STANDARD_SONGS + _BONUS_SONGS[2:]


class _LibraryAlbum:
    def __init__(self, id_, title, songs):
        self.id = id_
        self.title = title
        self.track_count = len(songs)


class _LibraryTrack:
    def __init__(self, title, album):
        self.title = title
        self.album_id = album.id
        self.album_title = album.title


class _EditionLibraryDB(_FakeDB):
    """Whole albums: the album match, per-album tracks and the global per-track
    search all read the same rows, in the order the albums were given."""

    def __init__(self, albums, matched=None):
        super().__init__()
        self._albums = {}
        self._tracks = {}
        for album_id, (title, songs) in albums.items():
            album = _LibraryAlbum(album_id, title, songs)
            self._albums[album_id] = album
            self._tracks[album_id] = [_LibraryTrack(song, album) for song in songs]
        self._matched = matched

    def check_album_exists_with_editions(self, title, artist, confidence_threshold=0.7,
                                         expected_track_count=None, server_source=None,
                                         expected_year=None):
        if self._matched is None:
            return (None, 0.0)
        return (self._albums[self._matched], 0.95)

    def get_tracks_by_album(self, album_id):
        return list(self._tracks.get(album_id, []))

    def get_album_title_year(self, album_id):
        album = self._albums.get(album_id)
        return (album.title, 2005) if album else None

    def check_track_exists(self, title, artist, confidence_threshold=0.7, server_source=None, album=None):
        for tracks in self._tracks.values():
            for track in tracks:
                if track.title.lower() == title.lower():
                    return (track, 0.95)
        return (None, 0.0)


def _queued_songs(monkeypatch, batch_id, db, *, album, songs, prefer, allow_duplicates=True):
    monkeypatch.setattr('database.music_database.MusicDatabase', lambda: db)
    deps = _build_deps(config=_FakeConfig({
        'wishlist.prefer_deluxe_editions': prefer,
        'wishlist.allow_duplicate_tracks': allow_duplicates,
    }))
    _seed_batch(batch_id, is_album_download=True,
                album_context={'name': album, 'total_tracks': len(songs)},
                artist_context={'name': 'Eminem'})
    mw.run_full_missing_tracks_process(
        batch_id, 'album:1', [{'name': song, 'artists': ['Eminem']} for song in songs], deps)
    return sorted(download_tasks[task_id]['track_info']['name']
                  for task_id in download_batches[batch_id]['queue'])


def test_prefer_deluxe_downloads_the_whole_deluxe_when_the_standard_is_owned(monkeypatch):
    db = _EditionLibraryDB({1: (_STANDARD_TITLE, _STANDARD_SONGS)}, matched=1)
    queued = _queued_songs(monkeypatch, 'PD1', db, album=_DELUXE_TITLE, songs=_DELUXE_SONGS,
                           prefer=True)
    assert queued == sorted(_DELUXE_SONGS)


def test_without_prefer_deluxe_the_deluxe_gets_only_its_bonus_tracks(monkeypatch):
    """The split the option exists to avoid, pinned so the default stays put."""
    db = _EditionLibraryDB({1: (_STANDARD_TITLE, _STANDARD_SONGS)}, matched=1)
    queued = _queued_songs(monkeypatch, 'PD2', db, album=_DELUXE_TITLE, songs=_DELUXE_SONGS,
                           prefer=False)
    assert queued == sorted(_BONUS_SONGS)


def test_an_owned_deluxe_still_satisfies_a_standard_download(monkeypatch):
    db = _EditionLibraryDB({2: (_DELUXE_TITLE, _DELUXE_SONGS)}, matched=2)
    queued = _queued_songs(monkeypatch, 'PD3', db, album=_STANDARD_TITLE, songs=_STANDARD_SONGS,
                           prefer=True)
    assert queued == []
    assert download_batches['PD3']['phase'] == 'complete'


def test_a_split_library_gets_the_rest_of_the_deluxe_with_duplicates_off(monkeypatch):
    """The library the old behaviour leaves: the standard album plus a deluxe row
    holding two bonus tracks. The album match finds that deluxe row; the shared
    songs fall back to the global search, which lands on the standard album."""
    library = {1: (_STANDARD_TITLE, _STANDARD_SONGS), 2: (_DELUXE_TITLE, ['Intro', 'FACK'])}
    queued = _queued_songs(monkeypatch, 'PD4', _EditionLibraryDB(library, matched=2),
                           album=_DELUXE_TITLE, songs=_DELUXE_SONGS, prefer=True,
                           allow_duplicates=False)
    assert queued == sorted(_STANDARD_SONGS + ['Shit On You', 'Criminal'])


def test_a_split_library_without_the_option_keeps_counting_the_standard(monkeypatch):
    library = {1: (_STANDARD_TITLE, _STANDARD_SONGS), 2: (_DELUXE_TITLE, ['Intro', 'FACK'])}
    queued = _queued_songs(monkeypatch, 'PD5', _EditionLibraryDB(library, matched=2),
                           album=_DELUXE_TITLE, songs=_DELUXE_SONGS, prefer=False,
                           allow_duplicates=False)
    assert queued == ['Criminal', 'Shit On You']


def test_duplicates_off_with_no_album_match_uses_the_same_gate(monkeypatch):
    library = {1: (_STANDARD_TITLE, _STANDARD_SONGS)}
    on = _queued_songs(monkeypatch, 'PD6', _EditionLibraryDB(library), album=_DELUXE_TITLE,
                       songs=_DELUXE_SONGS, prefer=True, allow_duplicates=False)
    off = _queued_songs(monkeypatch, 'PD7', _EditionLibraryDB(library), album=_DELUXE_TITLE,
                        songs=_DELUXE_SONGS, prefer=False, allow_duplicates=False)
    assert on == sorted(_DELUXE_SONGS)
    assert off == sorted(_BONUS_SONGS)


def test_a_remaster_is_not_treated_as_a_bigger_edition(monkeypatch):
    songs = ['One', 'Two', 'Three']
    db = _EditionLibraryDB({1: ('Album X', songs)}, matched=1)
    queued = _queued_songs(monkeypatch, 'PD8', db, album='Album X (Remastered)',
                           songs=songs + ['Four'], prefer=True)
    assert queued == ['Four']
