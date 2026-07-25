"""Settings that existed in the UI but did nothing (or lied about what they did).

Three separate faults, all found while unifying the music/video settings:

* ``streaming_region`` was READ by the TMDB streaming worker and written by
  NOBODY, so the Streaming overlay was pinned to US whatever the user picked.
  The field they were actually setting is ``watch_region``.
* ``prefer_audio`` was stored and normalized but never reached the scorer — the
  audio bonus was unconditional, so the control did nothing.
* The "YouTube Libraries" editor round-tripped into ``root_folders`` but nothing
  read those rows back, and its help text claimed the first one was the default
  destination. The real destination is the ``youtube_path`` scalar.

Plus the YouTube cookie fields, which were real but unreachable from the video
side (their only editor sat inside a container gated on the MUSIC download mode).
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parent.parent
_INDEX = (_ROOT / "webui" / "index.html").read_text(encoding="utf-8")
_SETTINGS_JS = (_ROOT / "webui" / "static" / "settings.js").read_text(encoding="utf-8")
_VSETTINGS_JS = (_ROOT / "webui" / "static" / "video" / "video-settings.js").read_text(encoding="utf-8")


# ── streaming_region → watch_region ─────────────────────────────────────────

def test_streaming_region_key_is_gone_from_the_codebase():
    """It had zero writers, so nothing can be lost by deleting it — but while it
    existed the overlay silently ignored the user's region."""
    offenders = []
    for path in list((_ROOT / "core").rglob("*.py")) + list((_ROOT / "api").rglob("*.py")):
        if "streaming_region" in path.read_text(encoding="utf-8"):
            offenders.append(str(path.relative_to(_ROOT)))
    assert offenders == [], f"the write-only streaming_region key is back in: {offenders}"


def test_streaming_worker_reads_the_key_the_ui_actually_writes():
    from core.video.enrichment.backfill import TmdbStreamingWorker

    class _DB:
        def __init__(self, kv):
            self.kv = kv

        def get_setting(self, key, default=None):
            return self.kv.get(key, default)

    w = TmdbStreamingWorker.__new__(TmdbStreamingWorker)
    w.db = _DB({"watch_region": "gb"})
    assert w._region() == "GB"
    w.db = _DB({})
    assert w._region() == "US"          # unchanged default


# ── prefer_audio actually scores now ────────────────────────────────────────

def _score(audio, prefer_audio):
    from core.video.quality_eval import evaluate_release
    parsed = {"title": "X", "resolution": "1080p", "source": "web-dl", "audio": audio}
    out = evaluate_release(parsed, {"cutoff_resolution": "1080p", "prefer_audio": prefer_audio},
                           scope="movie", want_title="X")
    return out["score"]


@pytest.mark.parametrize("audio", ["atmos", "truehd", "dts-hd", "ac3", None])
def test_any_scores_exactly_as_before(audio):
    """Regression lock: 'any' is every existing profile's value, and its scoring
    must be byte-identical to the old unconditional bonus."""
    expected_bonus = 15 if audio in ("atmos", "truehd", "dts-hd") else 0
    assert _score(audio, "any") - _score(audio, None) == 0
    assert _score(audio, "any") == _score("ac3", "any") + expected_bonus


def test_atmos_preference_outranks_other_lossless():
    assert _score("atmos", "atmos") > _score("dts-hd", "atmos")
    assert _score("dts-hd", "atmos") == _score("ac3", "atmos")


def test_surround_preference_widens_the_bonus_to_lossy_multichannel():
    assert _score("ac3", "surround") > _score("ac3", "any")
    assert _score("atmos", "surround") == _score("ac3", "surround")


def test_audio_preference_never_rejects():
    """A hidden audio filter would starve grabs — it must only ever score. So the
    accept/reject verdict has to be identical whatever prefer_audio says."""
    from core.video.quality_eval import evaluate_release
    parsed = {"title": "X", "resolution": "1080p", "source": "web-dl", "audio": "ac3"}
    verdicts = set()
    for pref in ("any", "surround", "lossless", "atmos"):
        out = evaluate_release(parsed, {"cutoff_resolution": "1080p", "prefer_audio": pref},
                               scope="movie", want_title="X")
        verdicts.add((out["accepted"], out["rejected"]))
    assert len(verdicts) == 1, f"prefer_audio changed the verdict, not just the score: {verdicts}"


# ── the YouTube Libraries editor is gone, and its folder isn't lost ─────────

def test_youtube_library_editor_removed_from_ui_and_js():
    assert "data-video-ytlib-add" not in _INDEX
    assert 'data-video-lib-group="youtube"' not in _INDEX
    assert "collectYtLibraries" not in _VSETTINGS_JS
    assert "ytLibraryRow" not in _VSETTINGS_JS


def test_libraries_endpoint_ignores_a_youtube_payload(tmp_path, monkeypatch):
    import api.video as videoapi
    from flask import Flask
    from database.video_database import VideoDatabase
    import core.video.sources as vs

    monkeypatch.setattr(vs, "resolve_video_server", lambda: "plex")
    db = VideoDatabase(database_path=str(tmp_path / "video_library.db"))
    videoapi._video_db = db
    app = Flask(__name__)
    app.register_blueprint(videoapi.create_video_blueprint(), url_prefix="/api/video")
    try:
        r = app.test_client().post("/api/video/libraries", json={
            "movies": [{"server_title": "Movies", "path": "/m"}],
            "tv": [],
            "youtube": [{"server_title": "YT", "path": "/yt"}]})
        assert r.status_code == 200
        assert r.get_json()["configured"]["youtube"] == [], (
            "a youtube root_folder was written again — nothing ever reads it back")
    finally:
        videoapi._video_db = None


def test_orphan_youtube_library_path_is_adopted_as_youtube_path(tmp_path):
    """Anyone who DID configure a folder in the removed editor had it ignored;
    it must not simply vanish with the control."""
    from database.video_database import VideoDatabase
    path = str(tmp_path / "v.db")
    db = VideoDatabase(database_path=path)
    with db.connect() as c:
        c.execute("INSERT INTO root_folders (path, content_kind, server, server_title, sort_order) "
                  "VALUES ('/media/yt', 'youtube', 'plex', 'YT', 0)")
        c.execute("DELETE FROM video_settings WHERE key IN ('youtube_path','youtube_lib_path_rescued')")
        c.commit()

    db2 = VideoDatabase(database_path=path)
    with db2.connect() as c:
        db2._rescue_orphan_youtube_library_path(c)
        c.commit()
    assert db2.get_setting("youtube_path") == "/media/yt"

    # Idempotent: a later deliberate change isn't reverted.
    db2.set_setting("youtube_path", "/somewhere/else")
    with db2.connect() as c:
        db2._rescue_orphan_youtube_library_path(c)
        c.commit()
    assert db2.get_setting("youtube_path") == "/somewhere/else"


# ── YouTube cookies are reachable from the video side ───────────────────────

def test_youtube_cookies_are_a_shared_section_not_gated_on_the_music_mode():
    """yt-dlp uses these for video downloads too (core/video/youtube.py), but the
    only editor used to live inside #youtube-settings-container, which is hidden
    unless the MUSIC download mode is youtube/hybrid."""
    head = _INDEX.index("<h3>YouTube Cookies</h3>")
    section = _INDEX[head - 400:head + 3000]
    assert "data-shared" in section
    assert 'id="youtube-cookies-browser"' in section
    assert 'id="youtube-cookies-paste"' in section
    # ...and they are no longer inside the music-mode-gated container.
    gated_start = _INDEX.index('id="youtube-settings-container"')
    gated = _INDEX[gated_start:gated_start + 3000]
    assert 'id="youtube-cookies-browser"' not in gated


def test_shared_youtube_builder_sends_only_the_cookie_keys():
    """download_delay is music-only; the shared partial must not carry it (the
    endpoint merges per key, so sending it would let video overwrite it)."""
    start = _SETTINGS_JS.index("SHARED_SECTION_BUILDERS")
    block = _SETTINGS_JS[start:_SETTINGS_JS.index("\nfunction collectSharedSettings", start)]
    yt = block[block.index("youtube: () => ({"):]
    yt = yt[:yt.index("}),")]
    assert "cookies_browser" in yt and "cookies_paste" in yt
    assert "download_delay" not in yt
