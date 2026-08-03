"""A torrent is not importable the moment it reads 100%.

Reported as: the program tries to import a file that is incomplete or still
downloading.

The guard in process_client_download reads:

    if state != "completed" and pct < 100.0:
        return {"status": "downloading", ...}

which is an AND, so anything reporting 100% falls straight through to the
import — whatever it is actually doing at the time. qBittorrent's 'moving'
(relocating from the incomplete folder to the complete one) reports progress
1.0, and the adapter maps it to 'queued', which normalises to 'downloading'
here. `"downloading" != "completed"` is True but `1.0 < 1.0` is False, so the
AND is False and the organizer gets handed a file mid-copy. Usenet par2 repair
and unrar have the same shape: 100% long before the folder stops changing.

Fixing this by state alone is not available. The adapter deliberately collapses
'moving' and 'checkingDL' into the same 'queued' bucket as 'queuedUP' — which
means "finished, merely queued to seed" — and refusing to import on 'queued'
would strand every seed-queued torrent at 100% forever. That is the exact bug
the existing comment in process_client_download was written to prevent.

So the gate is the filesystem: hold the import until the content reads the same
twice running. mtime is what makes that work for torrents specifically — a
pre-allocated or sparse file has its FINAL size from the first byte written, so
size alone cannot distinguish a finished file from one still being filled in.
"""

from __future__ import annotations

import pytest

from core.video import client_download as cd


class _Status:
    def __init__(self, state, progress, **kw):
        self.state = state
        self.progress = progress
        for key in ('content_path', 'save_path', 'name', 'incomplete_path', 'error'):
            setattr(self, key, kw.get(key))


def _run(dl, status, *, settled=None, organizer=None, src="/dl/Film.mkv"):
    return cd.process_client_download(
        dl, get_status=lambda s, r: status, resolve_path=lambda p: p,
        find_video=lambda root, name: src, organizer=organizer, settled=settled)


@pytest.fixture(autouse=True)
def _clean_settle_state():
    cd._settle_state.clear()
    yield
    cd._settle_state.clear()


def _dl(**kw):
    base = {"id": 7, "client_ref": "abc", "source": "torrent", "filename": "Film.mkv"}
    base.update(kw)
    return base


# ── the reported defect ──────────────────────────────────────────────────────
def test_a_moving_torrent_reads_as_downloading_but_is_100_percent():
    """The setup for the bug, pinned so the mapping can't drift silently."""
    assert cd._norm_state(_Status("queued", 1.0)) == "downloading"


def test_without_a_gate_a_moving_torrent_is_imported_immediately():
    """Characterising the old behaviour — settled=None is still the default, so
    every existing caller and test keeps exactly today's semantics."""
    imported = []
    res = _run(_dl(), _Status("queued", 1.0, content_path="/dl/Film.mkv"),
               organizer=lambda d, s: imported.append(s) or {"status": "completed"})
    assert imported == ["/dl/Film.mkv"]
    assert res["status"] == "completed"


def test_the_gate_refuses_to_import_while_the_content_is_changing():
    imported = []
    changing = iter([(100, 1, 10.0), (200, 1, 11.0), (300, 1, 12.0)])
    settled = lambda d, p: cd.content_has_settled(d, p, snapshot=lambda _p: next(changing))

    for _tick in range(3):
        res = _run(_dl(), _Status("queued", 1.0, content_path="/dl/Film.mkv"),
                   organizer=lambda d, s: imported.append(s) or {"status": "completed"},
                   settled=settled)
        assert res == {"progress": 100.0}
    assert imported == [], "imported a file that was still being written"


def test_the_gate_lets_it_through_once_the_writes_stop():
    imported = []
    reads = iter([(100, 1, 10.0), (300, 1, 12.0), (300, 1, 12.0)])
    settled = lambda d, p: cd.content_has_settled(d, p, snapshot=lambda _p: next(reads))

    patches = [_run(_dl(), _Status("queued", 1.0, content_path="/dl/Film.mkv"),
                    organizer=lambda d, s: imported.append(s) or {
                        "status": "completed", "progress": 100.0, "dest_path": "/lib/Film.mkv"},
                    settled=settled)
               for _tick in range(3)]

    assert [p.get("status") for p in patches] == [None, None, "completed"]
    assert imported == ["/dl/Film.mkv"]


def test_a_seed_queued_torrent_still_imports():
    """The regression this must not cause. 'queuedUP' — finished, merely queued
    to seed — normalises to the same 'downloading' as 'moving', and blocking on
    state would strand it at 100% forever."""
    imported = []
    steady = lambda _p: (500, 1, 42.0)
    settled = lambda d, p: cd.content_has_settled(d, p, snapshot=steady)

    for _tick in range(2):
        _run(_dl(), _Status("queued", 1.0, content_path="/dl/Film.mkv"),
             organizer=lambda d, s: imported.append(s) or {"status": "completed"},
             settled=settled)
    assert imported == ["/dl/Film.mkv"]


# ── what counts as settled ───────────────────────────────────────────────────
def test_two_identical_reads_are_required():
    """One reading proves nothing — it is just a number."""
    dl = _dl()
    steady = lambda _p: (10, 1, 1.0)
    assert cd.content_has_settled(dl, "/x", snapshot=steady) is False
    assert cd.content_has_settled(dl, "/x", snapshot=steady) is True


def test_an_unreadable_path_is_never_settled():
    """Erring toward waiting: a path we cannot stat might be mid-move."""
    dl = _dl()
    for _ in range(5):
        assert cd.content_has_settled(dl, "/x", snapshot=lambda _p: None) is False


def test_an_empty_directory_is_never_settled():
    """A client that has just created the destination folder and not yet copied
    into it reads identically twice — and is the opposite of finished."""
    dl = _dl()
    empty = lambda _p: (0, 0, 5.0)
    for _ in range(5):
        assert cd.content_has_settled(dl, "/x", snapshot=empty) is False


def test_a_size_that_never_changes_is_still_caught_by_mtime():
    """The torrent-specific case: pre-allocated files are full-size from the
    first byte, so size and file-count alone would call this settled."""
    dl = _dl()
    growing = iter([(1000, 1, 1.0), (1000, 1, 2.0), (1000, 1, 3.0)])
    for _ in range(3):
        assert cd.content_has_settled(dl, "/x", snapshot=lambda _p: next(growing)) is False


def test_the_count_resets_when_the_path_changes():
    """A move relocates the content — the new location has its own history, and
    one reading of it is not two."""
    dl = _dl()
    steady = lambda _p: (10, 1, 1.0)
    assert cd.content_has_settled(dl, "/incomplete/Film", snapshot=steady) is False
    assert cd.content_has_settled(dl, "/complete/Film", snapshot=steady) is False
    assert cd.content_has_settled(dl, "/complete/Film", snapshot=steady) is True


def test_downloads_are_tracked_separately():
    """Two concurrent grabs must not settle each other."""
    a, b = _dl(id=1), _dl(id=2)
    steady = lambda _p: (10, 1, 1.0)
    assert cd.content_has_settled(a, "/x", snapshot=steady) is False
    assert cd.content_has_settled(b, "/y", snapshot=steady) is False
    assert cd.content_has_settled(a, "/x", snapshot=steady) is True


def test_state_is_dropped_once_a_download_settles():
    """It has served its purpose — holding it would leak an entry per download."""
    dl = _dl()
    steady = lambda _p: (10, 1, 1.0)
    cd.content_has_settled(dl, "/x", snapshot=steady)
    assert cd._settle_state
    cd.content_has_settled(dl, "/x", snapshot=steady)
    assert not cd._settle_state


def test_state_can_be_dropped_explicitly():
    dl = _dl()
    cd.content_has_settled(dl, "/x", snapshot=lambda _p: (10, 1, 1.0))
    cd.forget_settle_state(dl["id"])
    assert not cd._settle_state


def test_the_state_map_is_capped():
    """A download that vanishes mid-settle leaves an entry behind; unbounded
    growth in a long-running monitor is a slow leak."""
    steady = lambda _p: (10, 1, 1.0)
    for i in range(cd._SETTLE_STATE_CAP + 50):
        cd.content_has_settled(_dl(id=i), "/x-%d" % i, snapshot=steady)
    assert len(cd._settle_state) <= cd._SETTLE_STATE_CAP


# ── the real snapshot, on real files ─────────────────────────────────────────
def test_the_real_snapshot_notices_a_growing_file(tmp_path):
    """Exercises the actual helper rather than a stub, since everything above
    trusts it to change when bytes are written."""
    f = tmp_path / "Film.mkv"
    f.write_bytes(b"a" * 100)
    first = cd._snapshot(str(f))
    f.write_bytes(b"a" * 200)
    assert cd._snapshot(str(f)) != first


def test_the_real_snapshot_is_stable_for_an_untouched_file(tmp_path):
    f = tmp_path / "Film.mkv"
    f.write_bytes(b"a" * 100)
    assert cd._snapshot(str(f)) == cd._snapshot(str(f))


def test_the_real_snapshot_returns_none_for_a_missing_path(tmp_path):
    assert cd._snapshot(str(tmp_path / "nope")) is None


def test_a_real_directory_settles_only_after_the_last_write(tmp_path):
    """End to end on the filesystem: a pack folder being populated."""
    dl = _dl()
    pack = tmp_path / "Season 1"
    pack.mkdir()
    (pack / "e01.mkv").write_bytes(b"x" * 10)
    assert cd.content_has_settled(dl, str(pack)) is False
    (pack / "e02.mkv").write_bytes(b"x" * 10)          # still arriving
    assert cd.content_has_settled(dl, str(pack)) is False
    assert cd.content_has_settled(dl, str(pack)) is True


# ── the production wiring ────────────────────────────────────────────────────
def test_the_production_entry_point_uses_the_gate():
    """The gate is worthless if the real caller does not pass it."""
    import inspect
    src = inspect.getsource(cd.process_active_client_download)
    assert "settled=content_has_settled" in src
