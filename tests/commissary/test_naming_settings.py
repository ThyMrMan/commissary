"""The settings card's {Token} help: chips, the TRaSH preset, a real preview."""

from __future__ import annotations

import re
import shutil
import subprocess
from pathlib import Path

import pytest
from flask import Flask

from tests.commissary.test_naming_tokens import (LIVE_EPISODE, LIVE_MOVIE,
                                                  SONARR_STANDARD)

ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture()
def client(tmp_path):
    import api.video as videoapi
    from database.video_database import VideoDatabase
    videoapi._video_db = VideoDatabase(database_path=str(tmp_path / "video_library.db"))
    app = Flask(__name__)
    app.register_blueprint(videoapi.create_video_blueprint(), url_prefix="/api/video")
    try:
        yield app.test_client()
    finally:
        videoapi._video_db = None


def test_the_preview_renders_the_live_templates_through_the_real_engine(client):
    d = client.post("/api/video/libraries/naming/preview", json={
        "movie_template": LIVE_MOVIE, "episode_template": LIVE_EPISODE,
        "youtube_template": "$channel/Season $year/$channel - $sxe - $title"}).get_json()
    assert d["movie"] == ("The Directors Cut (1999)/The Directors Cut (1999) (tmdb-603) "
                          "edition-Directors Cut [IMAX][Bluray-2160p][DV][TrueHD 7.1][x265]-FraMeSToR.mkv")
    assert d["episode"] == ("The Expanse (2015) (tmdb-63639)/Season 04/The Expanse (2015) - S04E02 - "
                            "Jetsam [AMZN][WEBDL-1080p][EAC3 5.1][x264]-NTb.mkv")
    assert d["youtube"] == "Techmoan/Season 2026/Techmoan - s2026e0314 - The forgotten cassette format.mp4"
    assert "{" not in d["movie"] + d["episode"]


def test_the_token_list_is_the_renderers_own(client):
    d = client.get("/api/video/libraries/naming/tokens").get_json()
    assert "Series CleanTitleWithoutYear" in d["episode"] and "Movie CleanTitle" in d["movie"]
    assert "MediaInfo VideoBitDepth" in d["import_only"]


def test_both_are_admin_only(client):
    from flask import g

    @client.application.before_request
    def _member():
        g.is_admin = False

    assert client.get("/api/video/libraries/naming/tokens").status_code == 403
    assert client.post("/api/video/libraries/naming/preview", json={}).status_code == 403


def _presets():
    """The presets as the shipped script defines them (evaluated by node)."""
    import json
    node = shutil.which("node") or shutil.which("node.exe")
    if node is None:
        pytest.skip("node not available")
    code = ("globalThis.window={};globalThis.document={readyState:'loading',addEventListener(){}};"
            "(0,eval)(require('fs').readFileSync('webui/static/commissary-naming-settings.js','utf8'));"
            "console.log(JSON.stringify(window.CommissaryNamingSettings.TRASH))")
    res = subprocess.run([node, "-e", code], cwd=str(ROOT), capture_output=True, text=True)
    assert res.returncode == 0, res.stderr
    return json.loads(res.stdout)


def test_the_trash_presets_are_the_guides_formats_and_render_cleanly(client):
    """Parsed out of the shipped script, so the test can't drift from the button."""
    presets = _presets()
    movie, episode = presets["movie"], presets["episode"]
    assert movie.startswith("{Movie CleanTitle} {(Release Year)} {edition-{Edition Tags}}")
    assert episode.endswith(SONARR_STANDARD.split("{Episode CleanTitle:90} ", 1)[1])
    # the old fork's movie preset puts the HDR type before the audio, and the
    # live template follows it - kept as it was
    assert movie.endswith("{[MediaInfo VideoDynamicRangeType]}{[Mediainfo AudioCodec}"
                          "{ Mediainfo AudioChannels]}{[Mediainfo VideoCodec]}{-Release Group}")
    d = client.post("/api/video/libraries/naming/preview",
                    json={"movie_template": movie, "episode_template": episode}).get_json()
    assert "{" not in d["movie"] + d["episode"] and d["episode"].count("/") == 2


@pytest.mark.skipif(shutil.which("node") is None, reason="node not available")
def test_the_script_parses_and_is_loaded():
    assert "filename='commissary-naming-settings.js'" in (ROOT / "webui/index.html").read_text(encoding="utf-8")
    res = subprocess.run(["node", "--check", "webui/static/commissary-naming-settings.js"],
                         cwd=str(ROOT), capture_output=True, text=True)
    assert res.returncode == 0, res.stderr
