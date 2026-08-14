"""'replace' mode must not rebuild a playlist that is already correct.

``PlexClient.update_playlist`` backs the playlist up, DELETES it, and recreates
it — unconditionally. That was survivable when a sync only ran on the user's own
schedule. It stopped being survivable once the post-download chain re-syncs
every playlist that came up short (core.automation.handlers.resync_playlists):
a playlist holding a few permanently-unavailable tracks would be torn down and
rebuilt after every single database update, re-keying it and churning a backup
copy each time, to arrive at exactly what was already there.

The comparison is an ordered LIST, not a set, so a playlist whose membership
matches but whose order has drifted still gets the rewrite. Behaviour changes in
the genuine no-op case only.
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from core.plex_client import PlexClient


def _t(rk):
    return SimpleNamespace(ratingKey=rk, title=f"t{rk}", artist='a', album='b')


class _Playlist:
    def __init__(self, keys):
        self._items = [_t(k) for k in keys]
        self.deleted = False

    def items(self):
        return list(self._items)

    def delete(self):
        self.deleted = True


def _client(existing, created):
    """A client whose only real behaviour is update_playlist. ``created`` is the
    caller's own list, per-test — a module-level recorder leaks between tests
    (``setup_function`` does NOT run for methods on a class), which silently made
    the 'still rebuilt' control depend on the test above it."""
    c = PlexClient.__new__(PlexClient)
    c.ensure_connection = lambda: True
    c.server = SimpleNamespace(playlist=lambda name: existing)
    c.create_playlist = lambda name, tracks: (created.append((name, tracks)) or True)
    return c


# ── the equality check itself ───────────────────────────────────────────────

class TestEquality:
    def test_same_tracks_same_order_matches(self):
        c = PlexClient.__new__(PlexClient)
        assert c._playlist_already_matches(_Playlist([1, 2, 3]), [_t(1), _t(2), _t(3)]) is True

    def test_int_and_str_rating_keys_are_the_same_track(self):
        """Plex hands back ints; our DB path stringifies. A type mismatch here
        would make every playlist look different and defeat the guard."""
        c = PlexClient.__new__(PlexClient)
        assert c._playlist_already_matches(_Playlist([1, 2]), [_t('1'), _t('2')]) is True

    def test_a_different_order_does_not_match(self):
        c = PlexClient.__new__(PlexClient)
        assert c._playlist_already_matches(_Playlist([1, 2, 3]), [_t(3), _t(2), _t(1)]) is False

    def test_a_missing_track_does_not_match(self):
        c = PlexClient.__new__(PlexClient)
        assert c._playlist_already_matches(_Playlist([1, 2]), [_t(1), _t(2), _t(3)]) is False

    def test_an_extra_server_track_does_not_match(self):
        c = PlexClient.__new__(PlexClient)
        assert c._playlist_already_matches(_Playlist([1, 2, 3]), [_t(1), _t(2)]) is False

    def test_an_empty_desired_list_never_matches(self):
        """A sync that resolved nothing must not read as 'already correct' —
        that would quietly cancel a legitimate emptying of the playlist."""
        c = PlexClient.__new__(PlexClient)
        assert c._playlist_already_matches(_Playlist([]), []) is False

    def test_unresolved_trackinfo_never_matches(self):
        """create_playlist can resolve a bare TrackInfo via a per-track server
        search. We will not pay that to answer a comparison — and a list we
        cannot resolve is exactly where we cannot be sure."""
        c = PlexClient.__new__(PlexClient)
        bare = SimpleNamespace(title='x', artist='y', album='z')   # no ratingKey
        assert c._playlist_already_matches(_Playlist([1]), [_t(1), bare]) is False

    def test_an_exception_means_rewrite_not_skip(self):
        c = PlexClient.__new__(PlexClient)
        boom = SimpleNamespace(items=lambda: (_ for _ in ()).throw(RuntimeError('offline')))
        assert c._playlist_already_matches(boom, [_t(1)]) is False


# ── update_playlist wiring ──────────────────────────────────────────────────

class TestUpdatePlaylist:
    def test_an_identical_playlist_is_left_alone(self):
        existing, created = _Playlist([1, 2, 3]), []
        assert _client(existing, created).update_playlist('P', [_t(1), _t(2), _t(3)]) is True
        assert existing.deleted is False, "the playlist was destroyed for no reason"
        assert created == []

    def test_a_changed_playlist_is_still_rebuilt(self):
        """The reported case: the sync now resolves tracks that were missing
        before. That MUST still write."""
        existing, created = _Playlist([1, 2, 3]), []
        assert _client(existing, created).update_playlist(
            'P', [_t(1), _t(2), _t(3), _t(4)]) is True
        assert existing.deleted is True
        assert [t.ratingKey for t in created[0][1]] == [1, 2, 3, 4]

    def test_a_reordered_playlist_is_still_rebuilt(self):
        existing, created = _Playlist([3, 1, 2]), []
        _client(existing, created).update_playlist('P', [_t(1), _t(2), _t(3)])
        assert existing.deleted is True

    def test_the_backup_is_skipped_along_with_the_rewrite(self):
        """The no-op return happens BEFORE the backup copy, or every database
        update would still churn a '<name> Backup' playlist."""
        src = Path('core/plex_client.py').read_text(encoding='utf-8')
        body = src[src.index('def update_playlist('):]
        body = body[:body.index('\n    def ')]
        assert body.index('_playlist_already_matches') < body.index('create_backup'), \
            'the equality check must short-circuit before the backup copy'


class TestCreatePlaylistLogLevel:
    """The first createPlaylist attempt uses the positional form, which current
    plexapi rejects ("Must include items to add when creating new playlist").
    The very next line retries with items= and succeeds. Logged at ERROR, that
    appeared on EVERY successful playlist creation — a permanent red herring
    sitting in app.log right beside the real playlist problems."""

    def _create_body(self):
        src = Path('core/plex_client.py').read_text(encoding='utf-8')
        body = src[src.index('def create_playlist('):]
        return body[:body.index('\n    def ')]

    def test_the_recovered_first_attempt_is_not_an_error(self):
        body = self._create_body()
        first = body[body.index('self.server.createPlaylist(name, first)'):]
        first = first[:first.index('items=first')]
        assert 'logger.error' not in first, \
            'the retried-and-recovered attempt still logs at ERROR'
        assert 'logger.debug' in first

    def test_the_rest_of_the_cascade_stays_loud(self):
        """Reaching those DOES mean something is wrong — demoting them too would
        hide a genuinely failed creation."""
        body = self._create_body()
        rest = body[body.index('items=first'):]
        assert 'Alternative createPlaylist also failed' in rest
        assert rest.count('logger.error') >= 3
