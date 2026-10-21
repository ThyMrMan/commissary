"""A release that is RAR parts and nothing playable is refused, not polled forever."""

from __future__ import annotations

import os
from types import SimpleNamespace

import pytest

from core.commissary import packed_release as pr
from core.video.client_download import process_client_download

RARS = ["Film.2020.1080p/film.part01.rar", "Film.2020.1080p/film.part02.rar",
        "Film.2020.1080p/film.r00", "Film.2020.1080p/film.sfv"]


# ── the classifier ──────────────────────────────────────────────────────────

@pytest.mark.parametrize("name", ["a.rar", "A.PART01.RAR", "a.r00", "a.r123", "a.z01", "film.mkv.001",
                                  "a.7z", "a.zip", "a.tar.gz"])
def test_archives_are_recognised(name):
    assert pr.is_archive(name)


@pytest.mark.parametrize("name", ["Show.S01E01.1080p.mkv", "Show.S01E001.mkv", "a.nfo", "a.r", ""])
def test_nothing_else_is(name):
    assert not pr.is_archive(name)


def test_a_playable_file_wins_outright():
    """film.mkv + subs.rar is a normal release."""
    assert pr.classify(["film.mkv", "subs.rar"]) == pr.VIDEO
    assert pr.classify(RARS) == pr.PACKED


def test_a_sample_is_not_playable():
    """The importer's own pick skips samples; if this said VIDEO the poll would spin again."""
    assert pr.classify(RARS + ["Film.2020.1080p/Sample/film-sample.mkv"]) == pr.PACKED


def test_nothing_known_is_left_alone():
    assert pr.classify([]) == pr.UNKNOWN
    assert pr.classify(["readme.nfo"]) == pr.UNKNOWN


def test_the_reason_says_what_it_found():
    msg = pr.reason(RARS, before_finishing=True)
    assert "3 rar files" in msg and "film.part01.rar" in msg and "Refused before finishing" in msg


# ── the client's file list ──────────────────────────────────────────────────

class _Resp:
    ok = True

    def __init__(self, data):
        self._d = data

    def json(self):
        return self._d


def _fake(name, **methods):
    return type(name, (), methods)()


@pytest.mark.parametrize("adapter,expected", [
    (_fake("QBittorrentAdapter", _call=lambda s, m, p, **k: _Resp([{"name": "a/b.rar"}])), ["a/b.rar"]),
    (_fake("TransmissionAdapter", _rpc=lambda s, m, a: {"torrents": [{"files": [{"name": "x.r00"}]}]}),
     ["x.r00"]),
    (_fake("DelugeAdapter", _rpc_sync=lambda s, m, p: {"files": [{"path": "y/z.mkv"}]}), ["y/z.mkv"]),
    (_fake("Aria2Adapter", _rpc=lambda s, m, *a: {"files": [{"path": "/d/w.rar"}]}), ["/d/w.rar"]),
])
def test_each_upstream_adapter_is_asked_its_own_way(adapter, expected):
    assert pr._adapter_files(adapter, "abc") == expected


def test_an_empty_answer_is_asked_again_and_a_full_one_remembered(monkeypatch):
    answers = [[], ["a.rar"]]
    asked = []

    class QBittorrentAdapter:
        def _call(self, *a, **k):
            asked.append(1)
            return _Resp([{"name": n} for n in answers[min(len(asked) - 1, 1)]])

    monkeypatch.setattr("core.torrent_clients.get_active_adapter", lambda: QBittorrentAdapter())
    pr._files_cache.clear()
    assert pr.client_files("torrent", "h1") == []
    assert pr.client_files("torrent", "h1") == ["a.rar"]
    assert pr.client_files("torrent", "h1") == ["a.rar"] and len(asked) == 2
    assert pr.client_files("usenet", "h1") is None, "usenet is always rars - never judged by its list"


# ── through upstream's process_client_download ──────────────────────────────

def _status(**kw):
    base = dict(state="downloading", progress=0.4, download_speed=1, eta=60, size=10, downloaded=4,
                content_path=None, save_path=None, name=None, error=None)
    base.update(kw)
    return SimpleNamespace(**base)


def _run(dl, status, **kw):
    return process_client_download(dl, get_status=lambda s, r: status, resolve_path=lambda p: p,
                                   find_video=lambda root, name: None, **kw)


def test_a_rar_only_torrent_is_refused_while_downloading():
    dl = {"id": 1, "source": "torrent", "client_ref": "h", "release_title": "Film.2020.1080p"}
    out = _run(dl, _status(), list_files=lambda s, r: RARS)
    assert out["status"] == "failed" and out["_bad_release"] is True
    assert "Refused before finishing" in out["error"]


def test_but_not_a_usenet_job_or_a_video_or_a_list_not_in_yet():
    torrent = {"id": 1, "source": "torrent", "client_ref": "h"}
    assert _run({**torrent, "source": "usenet"}, _status(), list_files=lambda s, r: RARS)["status"] == "downloading"
    assert _run(torrent, _status(), list_files=lambda s, r: RARS + ["a/film.mkv"])["status"] == "downloading"
    assert _run(torrent, _status(), list_files=lambda s, r: [])["status"] == "downloading"
    assert _run(torrent, _status(), list_files=lambda s, r: None)["status"] == "downloading"


def test_a_finished_job_of_archives_is_refused_once_settled(tmp_path):
    folder = tmp_path / "Film.2020.1080p"
    folder.mkdir()
    for n in ("film.part01.rar", "film.part02.rar"):
        (folder / n).write_bytes(b"x")
    dl = {"id": 2, "source": "usenet", "client_ref": "n"}
    st = _status(state="completed", progress=1.0, content_path=str(folder))
    still_unpacking = _run(dl, st, listing=pr.listing, settled=lambda d, p: False)
    assert still_unpacking == {"progress": 100.0}, "a usenet unrar in progress is left to finish"
    out = _run(dl, st, listing=pr.listing, settled=lambda d, p: True)
    assert out["status"] == "failed" and "Finished, but there is no video file" in out["error"]


def test_a_season_pack_of_archives_is_refused_before_the_importer(tmp_path):
    folder = tmp_path / "Show.S01.1080p"
    folder.mkdir()
    (folder / "show.s01.part01.rar").write_bytes(b"x")
    dl = {"id": 3, "source": "torrent", "client_ref": "h", "kind": "show",
          "search_ctx": '{"scope": "season", "season": 1}'}
    st = _status(state="completed", progress=1.0, content_path=str(folder))
    called = []
    out = process_client_download(dl, get_status=lambda s, r: st, resolve_path=lambda p: p,
                                  find_video=lambda root, name: None,
                                  import_pack=lambda *a: called.append(a) or {"status": "completed"},
                                  settled=lambda d, p: True, listing=pr.listing)
    assert out["status"] == "failed" and called == [], "never handed to the pack importer"


def test_production_wiring_passes_both_seams():
    src = open(os.path.join(os.path.dirname(__file__), "..", "..", "core", "video",
                            "client_download.py"), encoding="utf-8").read()
    assert "list_files=_packed.client_files, listing=_packed.listing" in src
