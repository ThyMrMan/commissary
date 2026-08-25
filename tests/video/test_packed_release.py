"""A release that is a pile of RAR parts can never be imported. Say so, early.

Reported as: "sometimes a file will be selected which is a collection of rar'ed
files which then get stuck since they don't get extracted automatically."

They get stuck for a precise reason. ``client_download`` walks the finished
folder for a video extension via ``_largest_video``; a folder of ``.rar`` /
``.r00`` / ``.part01.rar`` matches nothing, so ``src`` is None and the patch
returned carries progress but NO status — which the monitor reads as "complete
but the file isn't visible yet, keep polling". Since 2.0.7 the stall clock ends
that after thirty minutes, but with the at-completion message, which tells you
to go and check your save paths for a path-mapping bug that was never there.

Nothing on the video side unpacks anything. ``core/archive_pipeline`` is real,
but its entry point is ``collect_audio_after_extraction`` and it is wired only
into the music plugins — and in the shipped image it could not do rars anyway
(``rarfile``/``py7zr`` are not in requirements, ``unrar``/``p7zip`` are not in
the Dockerfile). So this is not a wait-longer problem. It is lost on arrival.

Two moments can answer it, and this covers both:

  · while it is still DOWNLOADING, from the torrent client's own file list —
    the only moment the bandwidth can still be saved. Prowlarr's results carry
    no file list at all (``prowlarr_search._project`` hardcodes ``files=[]``),
    so there is nothing to judge at grab time; the client is the first place
    that knows.

  · at COMPLETION, from what actually arrived on disk — which also catches a
    usenet job whose unrar failed, something no file list could tell you.

The refusal is a ``_bad_release``, so the exact release is blocklisted before
the retry runs — being packed is a permanent property of it, not a bad night on
the swarm.
"""

from __future__ import annotations

import os
from unittest.mock import MagicMock

import pytest

from core.video import client_download as cd
from core.video import packed_release as pr


# ── the classifier ──────────────────────────────────────────────────────────

class TestWhatCountsAsPacked:
    @pytest.mark.parametrize("names,expect", [
        (["Show.S01E01.1080p.WEB.H264-GRP.mkv"], pr.VIDEO),
        (["Show.part01.rar", "Show.part02.rar", "Show.nfo"], pr.PACKED),
        (["Show.rar", "Show.r00", "Show.r01", "Show.sfv"], pr.PACKED),
        (["film.mkv.001", "film.mkv.002"], pr.PACKED),
        (["pack.zip", "pack.z01"], pr.PACKED),
        (["Show.7z"], pr.PACKED),
        ([], pr.UNKNOWN),
        (["Show.nfo", "poster.jpg"], pr.UNKNOWN),
    ])
    def test_the_verdicts(self, names, expect):
        assert pr.classify(names) == expect

    def test_a_video_beside_a_subs_rar_is_a_normal_release(self):
        """The false positive that would matter most. Refusing this would be a
        far worse bug than the one being fixed."""
        assert pr.classify(["Show.S01E01.mkv", "Subs/Show.S01E01.subs.rar"]) == pr.VIDEO

    def test_a_sample_does_not_count_as_playable(self):
        """``_largest_video`` skips anything with 'sample' in the name, so rars
        plus a sample.mkv hold nothing importable. Calling that VIDEO here would
        put the poll loop straight back into the silent spin."""
        assert pr.classify(["Sample/sample.mkv", "Show.part01.rar"]) == pr.PACKED

    def test_shouty_scene_extensions_still_count(self):
        """Scene releases are routinely named Film.PART01.RAR, and an extension
        set is case-sensitive. This is the half of the name normalising that
        actually decides a verdict."""
        assert pr.classify(["Film.PART01.RAR", "Film.PART02.RAR"]) == pr.PACKED
        assert pr.is_archive("Film.R00") is True

    def test_folders_never_decide_the_verdict(self):
        """Client file lists are relative PATHS, and aria2 gives absolute ones."""
        assert pr.classify(["Some.Release.RARBG/film.mkv"]) == pr.VIDEO
        assert pr.classify(["/downloads/Film.rar/film.mkv"]) == pr.VIDEO

    def test_a_codec_in_the_name_is_not_a_split_volume(self):
        """The continuation regex is anchored — `.001` at the END is a volume,
        `x264` in the middle of a title is not."""
        assert pr.classify(["Show.S01E01.1080p.BluRay.x264-GRP.mkv"]) == pr.VIDEO
        assert pr.is_archive("Show.S01E01.1080p.x265.mkv") is False


class TestTheMessage:
    def test_it_names_the_count_the_format_and_an_example(self):
        """A message that doesn't say what it found is a message you can't check
        against the folder."""
        msg = pr.reason(["Show.part%02d.rar" % i for i in range(1, 15)])
        assert "14 rar files" in msg
        assert "Show.part01.rar" in msg

    def test_one_archive_reads_as_singular(self):
        assert "1 zip file and" in pr.reason(["Show.zip"])

    def test_the_two_moments_read_differently(self):
        early = pr.reason(["a.rar"], before_finishing=True)
        late = pr.reason(["a.rar"], before_finishing=False)
        assert "Refused before finishing" in early
        assert "Finished, but there is no video file" in late


# ── B: refused while it is still downloading ────────────────────────────────

class _Status:
    def __init__(self, state, progress, **kw):
        self.state = state
        self.progress = progress
        self.download_speed = kw.pop("download_speed", 0)
        self.size = kw.pop("size", 0)
        self.downloaded = kw.pop("downloaded", 0)
        for key in ("content_path", "save_path", "name", "incomplete_path", "error", "eta"):
            setattr(self, key, kw.get(key))


def _dl(**kw):
    base = {"id": 7, "client_ref": "abc", "source": "torrent",
            "filename": "Film.2024.1080p-GRP", "username": "indexer",
            "release_title": "Film.2024.1080p-GRP"}
    base.update(kw)
    return base


def _mid_download(dl, *, list_files=None):
    return cd.process_client_download(
        dl, get_status=lambda s, r: _Status("downloading", 0.4),
        resolve_path=lambda p: p, find_video=lambda root, name: None,
        list_files=list_files)


class TestRefusedBeforeItFinishes:
    def test_a_rar_only_torrent_is_refused_mid_download(self):
        """The point of the whole exercise: stop before the bandwidth is spent."""
        out = _mid_download(_dl(), list_files=lambda s, r: [
            "Film/film.part01.rar", "Film/film.part02.rar", "Film/film.nfo"])
        assert out["status"] == "failed"
        assert out["_bad_release"] is True
        assert "rar" in out["error"]

    def test_a_normal_torrent_keeps_downloading(self):
        out = _mid_download(_dl(), list_files=lambda s, r: ["Film/film.mkv"])
        assert out["status"] == "downloading"

    def test_an_unresolved_magnet_keeps_downloading(self):
        """Empty means 'no metadata yet', never 'no files'. Refusing what you
        cannot read is how a working release gets thrown away."""
        out = _mid_download(_dl(), list_files=lambda s, r: [])
        assert out["status"] == "downloading"

    def test_an_unreadable_listing_keeps_downloading(self):
        out = _mid_download(_dl(), list_files=lambda s, r: None)
        assert out["status"] == "downloading"

    def test_a_raising_client_keeps_downloading(self):
        def _boom(s, r):
            raise RuntimeError("client down")
        assert _mid_download(_dl(), list_files=_boom)["status"] == "downloading"

    def test_usenet_is_never_judged_by_its_file_list(self):
        """A usenet release is ALWAYS rars — that is what usenet is — and
        SABnzbd/NZBGet unpack them server-side before Commissary sees the folder.
        Judging one this way would refuse every usenet download there is."""
        out = _mid_download(_dl(source="usenet"),
                            list_files=lambda s, r: ["film.part01.rar", "film.part02.rar"])
        assert out["status"] == "downloading"

    def test_no_seam_means_exactly_the_old_behaviour(self):
        assert _mid_download(_dl())["status"] == "downloading"


# ── A: refused at completion, instead of spinning ───────────────────────────

@pytest.fixture(autouse=True)
def _clean_state():
    cd._settle_state.clear()
    cd._files_cache.clear()
    yield
    cd._settle_state.clear()
    cd._files_cache.clear()


def _finished(dl, *, src=None, listing=None, settled=None, organizer=None,
              name="Film.2024.1080p-GRP", find_pack=None):
    return cd.process_client_download(
        dl, get_status=lambda s, r: _Status("completed", 1.0, save_path="/dl", name=name),
        resolve_path=lambda p: p, find_video=lambda root, nm: src,
        find_pack=find_pack, organizer=organizer, settled=settled, listing=listing)


class TestRefusedAtCompletion:
    def test_a_settled_folder_of_rars_fails_with_the_real_reason(self, monkeypatch):
        monkeypatch.setattr(cd, "scoped_content_path", lambda root, nm=None: "/dl/Film")
        out = _finished(_dl(), listing=lambda p: ["/dl/Film/film.part01.rar",
                                                  "/dl/Film/film.part02.rar"],
                        settled=lambda d, p: True)
        assert out["status"] == "failed"
        assert out["_bad_release"] is True
        assert "no video file in it" in out["error"]

    def test_an_unsettled_folder_keeps_polling(self, monkeypatch):
        """The usenet unrar race: SABnzbd is still working, the rars are sitting
        right there while it does. Failing on sight would kill the exact case
        that was about to succeed."""
        monkeypatch.setattr(cd, "scoped_content_path", lambda root, nm=None: "/dl/Film")
        out = _finished(_dl(source="usenet"),
                        listing=lambda p: ["/dl/Film/film.part01.rar"],
                        settled=lambda d, p: False)
        assert out.get("status") is None
        assert out["progress"] == 100.0

    def test_an_empty_folder_still_just_keeps_polling(self, monkeypatch):
        """Unknown is not packed. This is the path-mapping case the stall clock
        already covers, and it must keep reaching it."""
        monkeypatch.setattr(cd, "scoped_content_path", lambda root, nm=None: "/dl/Film")
        out = _finished(_dl(), listing=lambda p: [], settled=lambda d, p: True)
        assert out.get("status") is None

    def test_no_seam_means_exactly_the_old_behaviour(self):
        out = _finished(_dl())
        assert out.get("status") is None and out["progress"] == 100.0

    def test_a_placed_download_still_completes(self):
        """dest_path already set wins over everything — it is already imported."""
        out = _finished(_dl(dest_path="/library/Film.mkv"),
                        listing=lambda p: ["/dl/Film/film.part01.rar"])
        assert out["status"] == "completed"


class TestAPackOfRars:
    """``find_pack_dir`` hands back its content FOLDER whatever is in it, so a
    season delivered as RAR parts gets past the `not src` branch with src set and
    would fail deep in the importer, per file, about the wrong thing entirely."""

    def _pack(self, listing, organizer):
        return _finished(_dl(kind="episode"), src="/dl/Season",
                         find_pack=lambda root, nm: "/dl/Season",
                         listing=listing, settled=lambda d, p: True,
                         organizer=organizer)

    def test_it_is_refused_rather_than_handed_to_the_importer(self):
        called = []
        out = self._pack(lambda p: ["/dl/Season/s01.part01.rar", "/dl/Season/s01.part02.rar"],
                         lambda d, s: called.append(s) or {"status": "completed"})
        assert out["status"] == "failed" and out["_bad_release"] is True
        assert not called, "the organizer should never have seen it"

    def test_a_real_season_folder_still_reaches_the_importer(self):
        called = []
        out = self._pack(lambda p: ["/dl/Season/S01E01.mkv", "/dl/Season/S01E02.mkv"],
                         lambda d, s: called.append(s) or {"status": "completed"})
        assert out["status"] == "completed"
        assert called == ["/dl/Season"]


class TestTheFilesystemListing:
    def test_it_walks_a_directory(self, tmp_path):
        (tmp_path / "sub").mkdir()
        (tmp_path / "sub" / "a.rar").write_text("x")
        (tmp_path / "b.nfo").write_text("x")
        names = cd.content_listing(str(tmp_path))
        assert {os.path.basename(n) for n in names} == {"a.rar", "b.nfo"}

    def test_a_single_file_is_its_own_listing(self, tmp_path):
        f = tmp_path / "film.mkv"
        f.write_text("x")
        assert cd.content_listing(str(f)) == [str(f)]

    def test_a_missing_path_is_empty_not_an_error(self, tmp_path):
        assert cd.content_listing(str(tmp_path / "nope")) == []
        assert cd.content_listing(None) == []

    def test_the_scoped_path_is_what_find_video_looked_at(self, tmp_path):
        """A shared download folder holds every concurrent grab, so a
        neighbour's .rar files must never be what condemns this release."""
        (tmp_path / "MyJob").mkdir()
        assert cd.scoped_content_path(str(tmp_path), "MyJob") == str(tmp_path / "MyJob")
        assert cd.scoped_content_path(str(tmp_path), "NotHere") is None
        assert cd.scoped_content_path(None, "x") is None


# ── the client seam ─────────────────────────────────────────────────────────

class TestTheClientSeamIsAskedOnce:
    def _adapter(self, names, counter):
        class _A:
            async def list_files(self, ref):
                counter.append(ref)
                return names
        return _A()

    def test_a_real_answer_is_cached(self, monkeypatch):
        calls = []
        monkeypatch.setattr("core.torrent_clients.get_active_adapter",
                            lambda: self._adapter(["film.mkv"], calls))
        assert cd._list_files("torrent", "abc") == ["film.mkv"]
        assert cd._list_files("torrent", "abc") == ["film.mkv"]
        assert len(calls) == 1

    def test_an_empty_answer_is_not_cached(self, monkeypatch):
        """Caching 'no metadata yet' as final would mean never looking again at
        exactly the torrents that had not told us anything."""
        calls = []
        monkeypatch.setattr("core.torrent_clients.get_active_adapter",
                            lambda: self._adapter([], calls))
        cd._list_files("torrent", "abc")
        cd._list_files("torrent", "abc")
        assert len(calls) == 2

    def test_usenet_is_never_asked(self, monkeypatch):
        calls = []
        monkeypatch.setattr("core.torrent_clients.get_active_adapter",
                            lambda: self._adapter(["x.rar"], calls))
        assert cd._list_files("usenet", "abc") is None
        assert not calls

    def test_a_dead_client_is_unknown_not_bad(self, monkeypatch):
        def _boom():
            raise RuntimeError("no client")
        monkeypatch.setattr("core.torrent_clients.get_active_adapter", _boom)
        assert cd._list_files("torrent", "abc") is None

    def test_the_cache_can_be_dropped(self, monkeypatch):
        calls = []
        monkeypatch.setattr("core.torrent_clients.get_active_adapter",
                            lambda: self._adapter(["film.mkv"], calls))
        cd._list_files("torrent", "abc")
        cd.forget_file_list("abc")
        cd._list_files("torrent", "abc")
        assert len(calls) == 2


# ── the adapters ────────────────────────────────────────────────────────────

def _resp(status_code, json_body=None):
    r = MagicMock()
    r.ok = 200 <= status_code < 400
    r.status_code = status_code
    r.json.return_value = json_body
    r.text = ""
    return r


class TestEachAdapterAnswers:
    def test_qbittorrent(self):
        from core.torrent_clients.qbittorrent import QBittorrentAdapter
        a = QBittorrentAdapter()
        a._call = lambda m, path, **kw: _resp(200, [{"name": "Film/film.part01.rar"},
                                                    {"name": "Film/film.nfo"}])
        assert a._list_files_sync("h") == ["Film/film.part01.rar", "Film/film.nfo"]

    def test_qbittorrent_http_failure_is_none(self):
        from core.torrent_clients.qbittorrent import QBittorrentAdapter
        a = QBittorrentAdapter()
        a._call = lambda m, path, **kw: _resp(404)
        assert a._list_files_sync("h") is None

    def test_transmission(self):
        from core.torrent_clients.transmission import TransmissionAdapter
        a = TransmissionAdapter()
        seen = {}

        def _rpc(method, args):
            seen["method"], seen["args"] = method, args
            return {"torrents": [{"files": [{"name": "Film/film.part01.rar"}]}]}
        a._rpc = _rpc
        assert a._list_files_sync("7") == ["Film/film.part01.rar"]
        assert seen["args"]["fields"] == ["files"], "must not bloat the status poll"

    def test_transmission_unknown_torrent_is_none(self):
        from core.torrent_clients.transmission import TransmissionAdapter
        a = TransmissionAdapter()
        a._rpc = lambda m, args: {"torrents": []}
        assert a._list_files_sync("7") is None

    def test_deluge(self):
        from core.torrent_clients.deluge import DelugeAdapter
        a = DelugeAdapter()
        a._rpc_sync = lambda m, p: {"files": [{"path": "Film/film.r00", "size": 1}]}
        assert a._list_files_sync("h") == ["Film/film.r00"]

    def test_deluge_unresolved_magnet_has_no_files_key(self):
        from core.torrent_clients.deluge import DelugeAdapter
        a = DelugeAdapter()
        a._rpc_sync = lambda m, p: {}
        assert a._list_files_sync("h") == []

    def test_aria2(self):
        from core.torrent_clients.aria2 import Aria2Adapter
        a = Aria2Adapter()
        a._rpc = lambda m, *p: {"files": [{"path": "/downloads/Film/film.part01.rar"}]}
        assert a._list_files_sync("gid") == ["/downloads/Film/film.part01.rar"]

    def test_aria2_absolute_paths_still_classify(self):
        assert pr.classify(["/downloads/Film/film.part01.rar"]) == pr.PACKED

    def test_every_adapter_has_the_call(self):
        """The Protocol is runtime_checkable, so a missing method is a real
        isinstance failure — but only if something asks. This asks."""
        from core.torrent_clients.aria2 import Aria2Adapter
        from core.torrent_clients.base import TorrentClientAdapter
        from core.torrent_clients.deluge import DelugeAdapter
        from core.torrent_clients.qbittorrent import QBittorrentAdapter
        from core.torrent_clients.transmission import TransmissionAdapter
        for cls in (QBittorrentAdapter, TransmissionAdapter, DelugeAdapter, Aria2Adapter):
            assert isinstance(cls(), TorrentClientAdapter)
            assert callable(getattr(cls(), "list_files", None))


# ── the monitor blocklists it before retrying ───────────────────────────────

class TestTheRefusedReleaseIsBlocklistedFirst:
    def test_order_matters(self, monkeypatch):
        """``plan_retry`` filters the stored candidate list through the
        blocklist. Blocklisting AFTER the retry would let it pick the very same
        release straight back off that list."""
        from core.video import download_monitor as dm
        order = []
        monkeypatch.setattr(dm, "_blocklist_release",
                            lambda db, dl, reason: order.append(("blocklist", reason)))
        monkeypatch.setattr(dm, "_fail_or_retry",
                            lambda db, dl, err: order.append(("retry", err)))
        dl = _dl()
        upd = {"status": "failed", "_bad_release": True, "error": "packed in rars"}
        # the exact branch the monitor runs
        if upd.get("_bad_release") and dl.get("username") and dl.get("filename"):
            dm._blocklist_release(None, dl, upd.get("error"))
        dm._fail_or_retry(None, dl, upd.get("error"))
        assert [step for step, _ in order] == ["blocklist", "retry"]

    def test_the_monitor_source_actually_does_it(self):
        """Pinned against the source, because the loop above cannot be run
        without a live DB and the ordering is the whole point."""
        src = (cd.__file__.rsplit("client_download.py", 1)[0] + "download_monitor.py")
        with open(src, encoding="utf-8") as fh:
            body = fh.read().replace("\r\n", "\n")
        _, _, after = body.partition('if upd.get("status") == "failed":')
        branch = after.split("continue", 1)[0]
        assert "_blocklist_release" in branch
        assert branch.index("_blocklist_release") < branch.index("_fail_or_retry")
