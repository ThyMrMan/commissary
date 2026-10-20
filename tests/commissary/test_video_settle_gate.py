"""A torrent is not importable the moment it reads 100% (fork 1.8.16).

qBittorrent's 'moving' reports progress 1.0 and the adapter maps it to 'queued',
which normalises to 'downloading'; the ``state != completed and pct < 100`` guard
lets it straight through, so the organizer got a file mid-copy. The gate is the
filesystem: hold the import until the content reads the same twice running.
"""

from __future__ import annotations

import pytest

from core.commissary import settle
from core.video import client_download as cd


class _Status:
    def __init__(self, state, progress, **kw):
        self.state = state
        self.progress = progress
        for key in ("content_path", "save_path", "name", "incomplete_path", "error"):
            setattr(self, key, kw.get(key))


def _run(dl, status, *, settled=None, organizer=None, src="/dl/Film.mkv", import_pack=None):
    return cd.process_client_download(
        dl, get_status=lambda s, r: status, resolve_path=lambda p: p,
        find_video=lambda root, name: src, organizer=organizer, settled=settled,
        import_pack=import_pack)


@pytest.fixture(autouse=True)
def _clean_settle_state():
    settle._settle_state.clear()
    yield
    settle._settle_state.clear()


def _dl(**kw):
    base = {"id": 7, "client_ref": "abc", "source": "torrent", "filename": "Film.mkv"}
    base.update(kw)
    return base


def _organize(sink):
    return lambda d, s: sink.append(s) or {"status": "completed", "progress": 100.0,
                                            "dest_path": "/lib/Film.mkv"}


# ── the reported defect ──────────────────────────────────────────────────────
def test_a_moving_torrent_reads_as_downloading_but_is_100_percent():
    assert cd._norm_state(_Status("queued", 1.0)) == "downloading"


def test_the_gate_refuses_to_import_while_the_content_is_changing():
    imported = []
    changing = iter([(100, 1, 10.0), (200, 1, 11.0), (300, 1, 12.0)])
    gate = lambda d, p: settle.content_has_settled(d, p, snapshot=lambda _p: next(changing))
    for _tick in range(3):
        res = _run(_dl(), _Status("queued", 1.0, content_path="/dl/Film.mkv"),
                   organizer=_organize(imported), settled=gate)
        assert res == {"progress": 100.0}
    assert imported == [], "imported a file that was still being written"


def test_the_gate_lets_it_through_once_the_writes_stop():
    imported = []
    reads = iter([(100, 1, 10.0), (300, 1, 12.0), (300, 1, 12.0)])
    gate = lambda d, p: settle.content_has_settled(d, p, snapshot=lambda _p: next(reads))
    patches = [_run(_dl(), _Status("queued", 1.0, content_path="/dl/Film.mkv"),
                    organizer=_organize(imported), settled=gate) for _tick in range(3)]
    assert [p.get("status") for p in patches] == [None, None, "completed"]
    assert imported == ["/dl/Film.mkv"]


def test_a_seed_queued_torrent_still_imports():
    """'queuedUP' normalises to the same 'downloading' as 'moving'; blocking on
    state would strand it at 100% forever."""
    imported = []
    gate = lambda d, p: settle.content_has_settled(d, p, snapshot=lambda _p: (500, 1, 42.0))
    for _tick in range(2):
        _run(_dl(), _Status("queued", 1.0, content_path="/dl/Film.mkv"),
             organizer=_organize(imported), settled=gate)
    assert imported == ["/dl/Film.mkv"]


def test_a_pack_folder_is_held_until_it_settles(tmp_path, monkeypatch):
    pack = tmp_path / "Show.S01"
    pack.mkdir()
    (pack / "e01.mkv").write_bytes(b"x" * 10)
    monkeypatch.setattr(cd, "is_pack_download", lambda dl: True)
    imported = []
    pack_import = lambda dl, root, name: imported.append(root) or {"status": "completed"}
    status = _Status("queued", 1.0, content_path=str(pack))
    first = _run(_dl(), status, settled=settle.content_has_settled, import_pack=pack_import)
    assert first == {"progress": 100.0} and imported == []
    (pack / "e02.mkv").write_bytes(b"x" * 10)          # still arriving
    assert _run(_dl(), status, settled=settle.content_has_settled,
                import_pack=pack_import) == {"progress": 100.0}
    assert _run(_dl(), status, settled=settle.content_has_settled,
                import_pack=pack_import)["status"] == "completed"
    assert imported == [str(pack)]


# ── what counts as settled ───────────────────────────────────────────────────
def test_two_identical_reads_are_required():
    dl = _dl()
    steady = lambda _p: (10, 1, 1.0)
    assert settle.content_has_settled(dl, "/x", snapshot=steady) is False
    assert settle.content_has_settled(dl, "/x", snapshot=steady) is True


def test_an_unreadable_path_is_never_settled():
    dl = _dl()
    for _ in range(5):
        assert settle.content_has_settled(dl, "/x", snapshot=lambda _p: None) is False


def test_an_empty_directory_is_never_settled():
    dl = _dl()
    for _ in range(5):
        assert settle.content_has_settled(dl, "/x", snapshot=lambda _p: (0, 0, 5.0)) is False


def test_a_size_that_never_changes_is_still_caught_by_mtime():
    dl = _dl()
    growing = iter([(1000, 1, 1.0), (1000, 1, 2.0), (1000, 1, 3.0)])
    for _ in range(3):
        assert settle.content_has_settled(dl, "/x", snapshot=lambda _p: next(growing)) is False


def test_the_count_resets_when_the_path_changes():
    dl = _dl()
    steady = lambda _p: (10, 1, 1.0)
    assert settle.content_has_settled(dl, "/incomplete/Film", snapshot=steady) is False
    assert settle.content_has_settled(dl, "/complete/Film", snapshot=steady) is False
    assert settle.content_has_settled(dl, "/complete/Film", snapshot=steady) is True


def test_downloads_are_tracked_separately():
    a, b = _dl(id=1), _dl(id=2)
    steady = lambda _p: (10, 1, 1.0)
    assert settle.content_has_settled(a, "/x", snapshot=steady) is False
    assert settle.content_has_settled(b, "/y", snapshot=steady) is False
    assert settle.content_has_settled(a, "/x", snapshot=steady) is True


def test_state_is_dropped_once_settled_or_forgotten():
    dl = _dl()
    steady = lambda _p: (10, 1, 1.0)
    settle.content_has_settled(dl, "/x", snapshot=steady)
    assert settle._settle_state
    settle.content_has_settled(dl, "/x", snapshot=steady)
    assert not settle._settle_state
    settle.content_has_settled(dl, "/x", snapshot=steady)
    settle.forget_settle_state(dl["id"])
    assert not settle._settle_state


def test_the_state_map_is_capped():
    steady = lambda _p: (10, 1, 1.0)
    for i in range(settle.SETTLE_STATE_CAP + 50):
        settle.content_has_settled(_dl(id=i), "/x-%d" % i, snapshot=steady)
    assert len(settle._settle_state) <= settle.SETTLE_STATE_CAP


# ── the real snapshot, on real files ─────────────────────────────────────────
def test_the_real_snapshot_notices_a_growing_file(tmp_path):
    f = tmp_path / "Film.mkv"
    f.write_bytes(b"a" * 100)
    first = settle.snapshot(str(f))
    f.write_bytes(b"a" * 200)
    assert settle.snapshot(str(f)) != first
    assert settle.snapshot(str(tmp_path / "nope")) is None


# ── the production wiring ────────────────────────────────────────────────────
def test_the_production_entry_point_uses_the_gate(monkeypatch):
    seen = {}
    monkeypatch.setattr(cd, "process_client_download",
                        lambda dl, **kw: seen.update(kw) or {})
    cd.process_active_client_download(_dl())
    assert seen["settled"] is settle.content_has_settled
