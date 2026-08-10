"""Naming Conformance must actually look, and must judge a file the same way
the Rename Files preview does.

Reported as "changed the naming scheme but the tool finds no episodes that need
renaming". Two separate defects, both of which produced silence:

1. The job stood down on any template naming an import-only token, and
   ``Custom Formats`` was on that list — so the TRaSH scheme this app installs
   with a one-click button made the job skip every file and report 0 findings,
   which is indistinguishable from a library that already conforms. The 1.9.13
   test that was supposed to cover this used a hand-trimmed copy of the TRaSH
   template with ``{[Custom Formats]}`` removed, so it never noticed. These
   tests use the strings the button actually installs, read out of the UI
   source, which is the only version that cannot drift.

2. ``repair_library_files`` — this job's query — never got the column widening
   its sibling rename queries got. Episodes carried a hardcoded ``NULL AS year``
   and neither scope carried audio codec, channels or dynamic range, so the job
   computed a strictly poorer name than Rename Files did for the same file.
   Against a template asking for those, a correctly-named file looked wrong and
   approving the "fix" would have stripped that detail off disk — the very harm
   defect 1 was pretending to prevent.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from core.video import mass_rename
from core.video import organization as org
from core.video.repair.naming_conformance import _fields_of
from core.video.repair.worker import VideoRepairWorker
from database.video_database import VideoDatabase

_ROOT = Path(__file__).resolve().parents[2]
_VSETTINGS = (_ROOT / "webui" / "static" / "video" / "video-settings.js").read_text(encoding="utf-8")


def _shipped_presets() -> dict:
    """The TRaSH templates exactly as the Settings button installs them.

    Parsed out of _TRASH_PRESETS rather than restated here: a paraphrase is what
    let this bug ship — the old test asserted against a template the product
    never actually uses.
    """
    block = _VSETTINGS[_VSETTINGS.index("var _TRASH_PRESETS"):]
    block = block[:block.index("\n    };")]
    mi, ei = block.index("movie:"), block.index("episode:")
    assert mi < ei, "parser assumes movie is declared before episode"
    segments = {"movie": block[mi:ei], "episode": block[ei:]}
    # each preset is several concatenated JS string literals — join them back
    return {scope: "".join(re.findall(r'"((?:[^"\\]|\\.)*)"', seg)).replace('\\"', '"')
            for scope, seg in segments.items()}


@pytest.fixture(scope="module")
def presets():
    p = _shipped_presets()
    # Guard the PARSER as well as the presets: a truncated read would quietly
    # test a template the product does not ship (which is how this shipped).
    assert p["movie"].startswith("{Movie CleanTitle}"), p["movie"][:40]
    assert p["episode"].startswith("{Series CleanTitleWithoutYear}"), p["episode"][:40]
    assert p["movie"].endswith("{-Release Group}"), p["movie"][-40:]
    assert p["episode"].endswith("{-Release Group}"), p["episode"][-40:]
    assert "{[Custom Formats]}" in p["episode"], "the token this bug was about"
    return p


def _library(tmp_path, name):
    db = VideoDatabase(database_path=str(tmp_path / "video_library.db"))
    db.set_setting("tv_path", str(tmp_path / "TV"))
    db.set_setting("movies_path", str(tmp_path / "Movies"))
    real = tmp_path / "TV" / name
    real.parent.mkdir(parents=True, exist_ok=True)
    real.write_bytes(b"x" * 64)
    db.upsert_show_tree("plex", {
        "server_id": "s1", "title": "Silo", "year": 2023, "tmdb_id": 125988,
        "seasons": [{"season_number": 3, "episodes": [
            {"episode_number": 6, "title": "The Dive",
             "file": {"relative_path": str(real), "size_bytes": 64,
                      "quality": "WEBDL-1080p", "resolution": "1080p",
                      "video_codec": "h265", "audio_codec": "eac3",
                      "audio_channels": 6, "dynamic_range": "HDR10"}}]}]})
    return db, real


_MESSY = "Silo.S03E06.The.Dive.1080p.AMZN.WEB-DL.DDP5.1.HDR.H265-NTb.mkv"


def _findings(db):
    return [f for f in db.repair_get_findings(status="pending")["items"]
            if f["finding_type"] == "naming_mismatch"]


# ── defect 1: the shipped scheme must not silence the job ────────────────────

def test_the_shipped_trash_presets_are_not_treated_as_unreproducible(presets):
    """The exact strings the one-click button installs. If either is ever
    blocked again, the Naming Conformance job goes quiet for that scope."""
    for scope, tmpl in presets.items():
        assert org.template_uses_unavailable_tokens(tmpl) == [], (
            "the shipped %s preset would silence Naming Conformance" % scope)


def test_the_trash_scheme_finds_a_misnamed_episode(tmp_path, presets):
    db, real = _library(tmp_path, _MESSY)
    org.save(db, {**org.load(db), "episode_template": presets["episode"]})
    VideoRepairWorker(db)._run_job("naming_conformance", forced=True)
    items = _findings(db)
    assert len(items) == 1, "the recommended scheme must not report an empty library"
    assert items[0]["details"]["current_path"] == str(real)


# ── defect 2: it must judge the file the same way Rename Files does ──────────

def test_conformance_and_mass_rename_agree_on_the_same_file(tmp_path, presets):
    """Two code paths, one file, one template — they must land on one name.
    They did not: this job's query withheld the year and the MediaInfo columns.
    """
    db, _real = _library(tmp_path, _MESSY)
    org.save(db, {**org.load(db), "episode_template": presets["episode"]})
    settings = org.load(db)
    fmts = org.library_custom_formats(db)

    conf_row = [r for r in db.repair_library_files() if r["scope"] == "episode"][0]
    conf = org.render_path("episode", "/TV", _fields_of(conf_row, fmts), settings, ".mkv")["path"]
    ren_row = db.rename_owned_episode_files()[0]
    ren = org.render_path("episode", "/TV", mass_rename._episode_fields(ren_row, fmts),
                          settings, ".mkv")["path"]
    assert conf == ren


def test_the_conformance_query_carries_the_naming_columns(tmp_path):
    """The specific fields that were missing. A template asking for any of them
    against a NULL renders a shorter name, and approving it deletes that detail
    from disk."""
    db, _real = _library(tmp_path, _MESSY)
    ep = [r for r in db.repair_library_files() if r["scope"] == "episode"][0]
    assert ep["year"] == 2023, "the series year was hardcoded to NULL"
    for col in ("audio_codec", "audio_channels", "dynamic_range", "tvdb_id",
                "imdb_id", "air_date", "release_source"):
        assert col in ep, "%s missing — the rendered name silently drops it" % col
    assert ep["audio_codec"] == "eac3" and ep["audio_channels"] == 6
    assert ep["dynamic_range"] == "HDR10"
    mv = [r for r in db.repair_library_files() if r["scope"] == "movie"]
    for col in ("audio_codec", "audio_channels", "dynamic_range", "imdb_id"):
        assert all(col in r for r in mv)


def test_the_rendered_name_keeps_year_audio_and_group(tmp_path, presets):
    db, _real = _library(tmp_path, _MESSY)
    org.save(db, {**org.load(db), "episode_template": presets["episode"]})
    row = [r for r in db.repair_library_files() if r["scope"] == "episode"][0]
    name = org.render_path("episode", "/TV", _fields_of(row, org.library_custom_formats(db)),
                           org.load(db), ".mkv")["path"]
    for expected in ("(2023)", "EAC3", "5.1", "HDR10", "NTb"):
        assert expected in name, "%r lost from %s" % (expected, name)


# ── custom formats are derived, not guessed ─────────────────────────────────

def test_custom_formats_come_from_the_files_own_name(tmp_path):
    db, _real = _library(tmp_path, _MESSY)
    db.set_setting("custom_formats", json.dumps([
        {"id": 1, "name": "AMZN", "include": ["AMZN"], "exclude": [], "score": 50, "kind": "custom"},
        {"id": 2, "name": "Remux", "include": ["remux"], "exclude": [], "score": 100, "kind": "custom"}]))
    row = [r for r in db.repair_library_files() if r["scope"] == "episode"][0]
    got = org.library_media_fields(row, custom_formats=org.library_custom_formats(db))
    assert got.get("custom_formats") == "AMZN", "matched on the filename it actually has"
    # 'Remux' is nowhere in that name — a format must never be invented
    assert "Remux" not in (got.get("custom_formats") or "")


def test_without_definitions_the_token_stays_empty_rather_than_guessing(tmp_path):
    db, _real = _library(tmp_path, _MESSY)
    row = [r for r in db.repair_library_files() if r["scope"] == "episode"][0]
    assert "custom_formats" not in org.library_media_fields(row)


# ── the remaining unreproducible tokens: warn, never go silent ──────────────

def test_an_unreproducible_token_warns_on_the_finding_instead_of_hiding_it(tmp_path, presets):
    db, real = _library(tmp_path, _MESSY)
    org.save(db, {**org.load(db),
                  "episode_template": presets["episode"] + "[{MediaInfo VideoBitDepth}bit]"})
    VideoRepairWorker(db)._run_job("naming_conformance", forced=True)
    items = _findings(db)
    assert len(items) == 1, "a risky template must still be examined and reported"
    f = items[0]
    assert f["severity"] == "warning"
    assert f["details"]["unreproducible_tokens"] == ["MediaInfo VideoBitDepth"]
    assert "MediaInfo VideoBitDepth" in f["description"]
    assert f["details"]["current_path"] == str(real)


def test_a_reproducible_template_carries_no_warning(tmp_path, presets):
    db, _real = _library(tmp_path, _MESSY)
    org.save(db, {**org.load(db), "episode_template": presets["episode"]})
    VideoRepairWorker(db)._run_job("naming_conformance", forced=True)
    f = _findings(db)[0]
    assert f["severity"] == "info"
    assert f["details"]["unreproducible_tokens"] == []
    assert "⚠" not in f["description"]
