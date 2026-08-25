"""Two things the video side never said out loud.

Both came out of a real investigation that could not be finished. A show was
filed as the wrong series and a grab was refused 324 times for one title over a
week, and eight days of app.log could answer neither question:

  · ``core/video/importer.py`` had NO LOGGER AT ALL. Not one line in the whole
    file. So nothing ever recorded where a video file was placed, or on whose
    authority — while the music side has logged ``Resolved path:`` on every
    import for years. "It got filed as the wrong show" could only be
    investigated by reading the database afterwards and inferring backwards.

  · When a torrent client refuses a release, the reason died in the adapter.
    Across those same eight days there was not a single line from the
    ``soulsync.torrent.*`` loggers, while ``_default_enqueue`` logged "the
    torrent client didn't accept the release" 324 times without ever saying
    what had been handed over.

Neither is a behaviour change. They are the lines that make the next occurrence
answerable from the log instead of from a database post-mortem.
"""

from __future__ import annotations

import asyncio
import json
import logging
from pathlib import Path

import pytest

from core.video import importer, organization

_ROOT = Path(__file__).resolve().parents[2]
_LOG = "soulsync.video.importer"


def _dl(**over):
    ctx = {"scope": "episode", "title": "Bleach", "season": 17, "episode": 1}
    ctx.update(over.pop("ctx", {}))
    row = {"id": 1, "kind": "episode", "title": "Bleach",
           "release_title": "Bleach S17E01 1080p WEB h264-GRP",
           "media_id": "30984", "media_source": "tmdb", "target_dir": "/anime",
           "search_ctx": json.dumps(ctx)}
    row.update(over)
    return row


SETTINGS = organization.normalize({"verify_with_ffprobe": False})
SRC = "/downloads/Bleach S17E01 1080p WEB h264-GRP.mkv"

IDENTITY = {"title": "Bleach", "year": 2004, "tmdbid": 30984, "tvdbid": 74796,
            "imdbid": "tt0434665", "episodes": {}}


def _plan(caplog, **kw):
    caplog.set_level(logging.INFO, logger=_LOG)
    return importer.plan_import(_dl(), SRC, list_dir=lambda p: [],
                                settings=SETTINGS, **kw)


def _lines(caplog):
    return [r.getMessage() for r in caplog.records if r.name == _LOG]


class TestThePlacementLine:
    def test_an_import_says_where_the_file_is_going(self, caplog):
        """THE gap: nothing recorded a destination, ever."""
        _plan(caplog, identity=IDENTITY)
        msgs = [m for m in _lines(caplog) if "[Placement]" in m]
        assert msgs, "no placement line was logged"
        assert "Bleach" in msgs[0]
        assert "/anime" in msgs[0]

    def test_it_names_the_library_row_it_named_the_file_from(self, caplog):
        """The SOURCE matters as much as the path — a destination built from the
        grab rather than the library row is how a show acquires a second
        folder, and the ids are what a media server matches on."""
        _plan(caplog, identity=IDENTITY)
        msg = [m for m in _lines(caplog) if "[Placement]" in m][0]
        assert "library row" in msg
        assert "30984" in msg and "74796" in msg and "2004" in msg

    def test_it_says_so_when_the_library_knows_nothing(self, caplog):
        """The case that creates the folder a media server then has to guess
        the identity of."""
        _plan(caplog, identity=None)
        msg = [m for m in _lines(caplog) if "[Placement]" in m][0]
        assert "not in the library yet" in msg

    def test_an_existing_copy_is_called_out(self, caplog):
        """An upgrade lands beside the copy already on disk, not at the
        templated path — worth saying, because the two differ."""
        _plan(caplog, identity=IDENTITY, library_dir="/anime/Bleach (2004)")
        msg = [m for m in _lines(caplog) if "[Placement]" in m][0]
        assert "existing library copy" in msg
        assert "/anime/Bleach (2004)" in msg

    def test_a_manual_placement_is_called_out(self, caplog):
        caplog.set_level(logging.INFO, logger=_LOG)
        importer.plan_import(_dl(), SRC, list_dir=lambda p: [], settings=SETTINGS,
                             identity=IDENTITY, force=True,
                             override={"scope": "episode", "title": "Bleach",
                                       "season": 17, "episode": 1,
                                       "target_dir": "/anime"})
        msg = [m for m in _lines(caplog) if "[Placement]" in m][0]
        assert "manual placement" in msg

    def test_a_rejected_import_logs_no_placement(self, caplog):
        """It never got a destination, so claiming one would be a lie."""
        caplog.set_level(logging.INFO, logger=_LOG)
        out = importer.plan_import(_dl(), "/downloads/notes.txt", list_dir=lambda p: [],
                                   settings=SETTINGS, identity=IDENTITY)
        assert out["action"] == "reject"
        assert not [m for m in _lines(caplog) if "[Placement]" in m]

    def test_the_line_can_never_cost_an_import(self, caplog, monkeypatch):
        """Wrapped, because a diagnostic that can fail an import is worse than
        no diagnostic."""
        monkeypatch.setattr(importer.logger, "info",
                            lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom")))
        out = importer.plan_import(_dl(), SRC, list_dir=lambda p: [],
                                   settings=SETTINGS, identity=IDENTITY)
        assert out["action"] == "import"


class TestTheImporterHasALoggerAtAll:
    def test_it_does(self):
        """The root of the whole gap: the file had none."""
        src = (_ROOT / "core" / "video" / "importer.py").read_text(encoding="utf-8")
        assert 'get_logger("video.importer")' in src


# ── the refusal reason ──────────────────────────────────────────────────────

class _Refusing:
    """An adapter that takes nothing, the way a client does for a duplicate."""

    def __init__(self):
        self.added = []

    async def add_torrent(self, what, category=None, save_path=None):
        self.added.append(what)
        return None

    async def add_torrent_file(self, data, category=None, save_path=None):
        return None


class TestTheClientRefusalIsAttributable:
    def test_a_refused_magnet_says_so(self, caplog):
        from core.torrent_clients import base
        caplog.set_level(logging.WARNING, logger="soulsync.torrent.add")
        asyncio.run(base.add_torrent_smart(_Refusing(), "magnet:?xt=urn:btih:abc"))
        msgs = [r.getMessage() for r in caplog.records
                if r.name == "soulsync.torrent.add"]
        assert msgs and "magnet" in msgs[0]
        assert "already in the client" in msgs[0]      # the likeliest cause, named

    def test_an_empty_url_is_visible_as_empty(self, caplog):
        """Its own kind of bug, and indistinguishable from a refusal before."""
        from core.torrent_clients import base
        caplog.set_level(logging.WARNING, logger="soulsync.torrent.add")
        asyncio.run(base.add_torrent_smart(_Refusing(), ""))
        msgs = [r.getMessage() for r in caplog.records
                if r.name == "soulsync.torrent.add"]
        assert msgs and "<empty>" in msgs[0]

    def test_a_successful_add_stays_quiet(self):
        from core.torrent_clients import base

        class _Ok(_Refusing):
            async def add_torrent(self, what, category=None, save_path=None):
                return "HASH"

        assert asyncio.run(base.add_torrent_smart(_Ok(), "magnet:?xt=1")) == "HASH"


class TestTheGrabErrorNamesWhatWasTried:
    def _grab(self, monkeypatch, url):
        from core.video import client_grab as cg
        monkeypatch.setattr("core.torrent_clients.get_active_adapter",
                            lambda: type("A", (), {"is_configured": lambda s: True})())

        async def _none(*a, **k):
            return None
        monkeypatch.setattr("core.torrent_clients.base.add_torrent_smart", _none)
        return cg.grab_torrent(url)

    @pytest.mark.parametrize("url,expected", [
        ("magnet:?xt=urn:btih:abc", "magnet"),
        ("https://indexer/x.torrent", ".torrent URL"),
        ("", "empty URL"),
    ])
    def test_the_error_says_what_was_handed_over(self, monkeypatch, url, expected):
        """'The torrent client didn't accept the release' alone cannot be acted
        on. A magnet-only release behaves differently from one with a URL."""
        out = self._grab(monkeypatch, url)
        assert out["ok"] is False
        assert expected in out["error"], out["error"]
