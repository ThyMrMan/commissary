"""A video import says where a file went, why it was refused, or why it failed.

Fork 2.0.8 / 2.0.10. The importer logged almost nothing, so "it got filed as the
wrong show" or "it never imported" could only be investigated by reading the
database afterwards.
"""

from __future__ import annotations

import json
import logging

import pytest

from core.video import importer, organization

_LOG = "soulsync.video.importer"
SETTINGS = organization.normalize({"verify_with_ffprobe": False})
SRC = "/downloads/Bleach S17E01 1080p WEB h264-GRP.mkv"
IDENTITY = {"title": "Bleach", "year": 2004, "tmdbid": 30984, "tvdbid": 74796,
            "imdbid": "tt0434665", "episodes": {}}


@pytest.fixture(autouse=True)
def _capture(caplog):
    caplog.set_level(logging.INFO, logger=_LOG)


def _dl(**over):
    ctx = {"scope": "episode", "title": "Bleach", "season": 17, "episode": 1}
    ctx.update(over.pop("ctx", {}))
    row = {"id": 1, "kind": "episode", "title": "Bleach",
           "release_title": "Bleach S17E01 1080p WEB h264-GRP",
           "media_id": "30984", "media_source": "tmdb", "target_dir": "/anime",
           "search_ctx": json.dumps(ctx)}
    row.update(over)
    return row


def _lines(caplog, tag):
    return [r.getMessage() for r in caplog.records if r.name == _LOG and tag in r.getMessage()]


def _plan(**kw):
    return importer.plan_import(_dl(), SRC, list_dir=lambda p: [], settings=SETTINGS, **kw)


# ── where it went, and on whose authority ────────────────────────────────────
def test_an_import_says_where_the_file_is_going(caplog):
    plan = _plan(identity=IDENTITY)
    msg = _lines(caplog, "[Placement]")
    assert msg and plan["dest"]["path"] in msg[0] and "'Bleach'" in msg[0]
    assert "library row (tmdb=30984 tvdb=74796 year=2004)" in msg[0]


@pytest.mark.parametrize("kw,why", [
    ({}, "the grab alone"),
    ({"library_dir": "/anime/Bleach/Season 17"}, "existing library copy"),
    ({"force": True, "override": {"scope": "episode", "season": 17, "episode": 1,
                                  "target_dir": "/anime"}}, "manual placement"),
])
def test_it_names_the_source_of_the_naming(caplog, kw, why):
    _plan(**kw)
    assert why in _lines(caplog, "[Placement]")[0]


# ── refusals and failures ────────────────────────────────────────────────────
class _FS:
    def __init__(self, fail=False):
        self.fail = fail

    def list_dir(self, d):
        return []

    def makedirs(self, d):
        if self.fail:
            raise OSError("disk full")

    def copy(self, s, d):
        pass

    move = copy

    def remove(self, p):
        pass


def test_a_refused_import_says_why(caplog):
    dl = _dl(release_title="Bleach S17E05 1080p WEB h264-GRP")
    patch = importer.run_import(dl, "/downloads/Bleach S17E05.mkv", fs=_FS(), settings=SETTINGS)
    assert patch["status"] == "import_failed"
    msg = _lines(caplog, "[Import]")
    assert msg and "refused" in msg[0] and "S17E05" in msg[0]
    assert "/downloads/Bleach S17E05.mkv" in msg[0]


def test_a_junk_file_is_marked_as_a_bad_release(caplog):
    importer.run_import(_dl(), "/downloads/readme.txt", fs=_FS(), settings=SETTINGS)
    assert "bad release" in _lines(caplog, "[Import]")[0]


def test_a_failed_copy_is_logged_with_its_cause(caplog):
    patch = importer.run_import(_dl(), SRC, fs=_FS(fail=True), settings=SETTINGS)
    assert patch["status"] == "import_failed"
    rec = [r for r in caplog.records if r.name == _LOG and "failed placing" in r.getMessage()]
    assert rec and rec[0].levelno == logging.WARNING and "disk full" in rec[0].getMessage()
