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
    """The presets as the shipped panel defines them (webui/src/commissary/naming-panel.tsx):
    each is a run of quoted strings joined by +."""
    import re
    src = (ROOT / "webui/src/commissary/naming-panel.tsx").read_text(encoding="utf-8")
    block = src.split("export const TRASH", 1)[1].split("};", 1)[0]
    out = {}
    for key in ("movie", "episode"):
        part = block.split(key + ":", 1)[1]
        part = part.split("episode:", 1)[0] if key == "movie" else part
        out[key] = "".join(re.findall(r"'([^']*)'", part))
    return out


def test_the_trash_presets_are_the_guides_formats_and_render_cleanly(client):
    """Parsed out of the shipped panel, so the test can't drift from the button."""
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


def test_the_settings_tab_shows_the_panel():
    tab = (ROOT / "webui/src/features/settings/tabs/library-video-organization.tsx").read_text(encoding="utf-8")
    assert '<CommissaryTokenHelp scope="movie" />' in tab
    assert '<CommissaryTokenHelp scope="episode" />' in tab
    assert "<CommissaryRenameFirst />" in tab
