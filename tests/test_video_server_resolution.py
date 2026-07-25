"""Seam tests: the video side resolves its server (Plex/Jellyfin) INDEPENDENTLY
of the music 'active server' pointer — so music-only servers never apply and a
mixed setup (Navidrome music + Plex video) works."""

from __future__ import annotations

import config.settings as cs
import pytest

from core.video.sources import (resolve_video_server, video_plex_config,
                                 video_jellyfin_config)
from database.video_database import VideoDatabase


def _set_cm(monkeypatch, plex, jelly, active, overrides=None):
    """Stub the app-wide config. ``overrides`` seeds the video side's OWN server
    creds, which now live here (encrypted) rather than in video.db."""
    store = dict(overrides or {})

    class CM:
        def get_plex_config(self): return {"base_url": "http://p", "token": "t"} if plex else {}
        def get_jellyfin_config(self): return {"base_url": "http://j", "api_key": "k"} if jelly else {}
        def get_active_media_server(self): return active
        def get(self, key, default=None): return store.get(key, default)
        def set(self, key, value): store[key] = value

    cm = CM()
    monkeypatch.setattr(cs, "config_manager", cm)
    # The one-shot legacy promotion is process-global; reset it per test.
    import core.video.sources as vs
    monkeypatch.setattr(vs, "_creds_promotion_checked", False, raising=False)
    return store


@pytest.fixture()
def vdb(tmp_path):
    return VideoDatabase(database_path=str(tmp_path / "v.db"))


def test_plex_only(monkeypatch, vdb):
    _set_cm(monkeypatch, True, False, "plex")
    assert resolve_video_server(vdb) == "plex"


def test_jellyfin_only(monkeypatch, vdb):
    _set_cm(monkeypatch, False, True, "jellyfin")
    assert resolve_video_server(vdb) == "jellyfin"


def test_none_when_only_a_music_server(monkeypatch, vdb):
    _set_cm(monkeypatch, False, False, "navidrome")
    assert resolve_video_server(vdb) is None


def test_independent_of_music_active(monkeypatch, vdb):
    # Navidrome is the music server, but Plex is configured → video uses Plex.
    _set_cm(monkeypatch, True, False, "navidrome")
    assert resolve_video_server(vdb) == "plex"


def test_both_configured_default_then_explicit_pick(monkeypatch, vdb):
    _set_cm(monkeypatch, True, True, "plex")
    assert resolve_video_server(vdb) == "plex"           # both → Plex default
    vdb.set_setting("video_server", "jellyfin")
    assert resolve_video_server(vdb) == "jellyfin"       # explicit video pick wins


def test_does_not_follow_music_active_server(monkeypatch, vdb):
    # Both configured + music set to Jellyfin, but NO explicit video pick → video
    # stays on Plex. Changing the music server must never change video.
    _set_cm(monkeypatch, True, True, "jellyfin")
    assert resolve_video_server(vdb) == "plex"
    vdb.set_setting("video_server", "jellyfin")          # only an explicit pick switches video
    assert resolve_video_server(vdb) == "jellyfin"


# ── Effective connection config: video's OWN creds, or inherited from music ──

def test_plex_config_inherits_music_when_unset(monkeypatch, vdb):
    _set_cm(monkeypatch, True, False, "plex")
    cfg = video_plex_config(vdb)
    assert cfg["base_url"] == "http://p" and cfg["token"] == "t"
    assert cfg["source"] == "music"                      # inherited, read-only


def test_plex_config_own_creds_override_music(monkeypatch, vdb):
    _set_cm(monkeypatch, True, False, "plex")
    vdb.set_setting("video_plex_url", "http://video-plex")
    vdb.set_setting("video_plex_token", "vt")
    cfg = video_plex_config(vdb)
    assert cfg["base_url"] == "http://video-plex" and cfg["token"] == "vt"
    assert cfg["source"] == "video"                      # video's own, not music's


def test_own_creds_do_not_touch_music_config(monkeypatch, vdb):
    # Setting video's own Plex creds must not change what music reports.
    _set_cm(monkeypatch, True, False, "plex")
    vdb.set_setting("video_plex_url", "http://video-plex")
    vdb.set_setting("video_plex_token", "vt")
    assert cs.config_manager.get_plex_config()["base_url"] == "http://p"


def test_video_jellyfin_override_while_music_is_plex(monkeypatch, vdb):
    # The headline scenario: music = Plex, video adds its OWN Jellyfin + picks it.
    _set_cm(monkeypatch, True, False, "plex")
    vdb.set_setting("video_jellyfin_url", "http://video-jelly")
    vdb.set_setting("video_jellyfin_key", "jk")
    jcfg = video_jellyfin_config(vdb)
    assert jcfg["base_url"] == "http://video-jelly" and jcfg["source"] == "video"
    vdb.set_setting("video_server", "jellyfin")
    assert resolve_video_server(vdb) == "jellyfin"       # video on Jellyfin, music still Plex


def test_partial_own_creds_falls_back_to_inherited(monkeypatch, vdb):
    # URL without token isn't a usable override → inherit music's full config.
    _set_cm(monkeypatch, True, False, "plex")
    vdb.set_setting("video_plex_url", "http://video-plex")  # token missing
    cfg = video_plex_config(vdb)
    assert cfg["base_url"] == "http://p" and cfg["source"] == "music"


# ── the creds live in the encrypted app config, not plaintext in video.db ────

def test_promotion_moves_plaintext_creds_out_of_video_db(monkeypatch, vdb):
    """These tokens sat in video.db in the CLEAR while music's equivalents were
    Fernet-encrypted. The one-shot moves them to the app config (where
    _SENSITIVE_PATHS covers them) and blanks the plaintext copies."""
    store = _set_cm(monkeypatch, True, False, "plex")
    vdb.set_setting("video_plex_url", "http://video-plex")
    vdb.set_setting("video_plex_token", "SECRET-TOKEN")

    cfg = video_plex_config(vdb)
    assert cfg["source"] == "video" and cfg["token"] == "SECRET-TOKEN"
    assert store["video_plex.token"] == "SECRET-TOKEN"
    assert (vdb.get_setting("video_plex_token") or "") == "", "the plaintext token survived"
    assert (vdb.get_setting("video_plex_url") or "") == ""


def test_promotion_is_idempotent_and_does_not_resurrect_cleared_creds(monkeypatch, vdb):
    store = _set_cm(monkeypatch, True, False, "plex")
    vdb.set_setting("video_plex_url", "http://video-plex")
    vdb.set_setting("video_plex_token", "vt")
    assert video_plex_config(vdb)["source"] == "video"

    # Clear the override, then let a fresh process re-run the promotion.
    store["video_plex.base_url"] = ""
    import core.video.sources as vs
    monkeypatch.setattr(vs, "_creds_promotion_checked", False, raising=False)
    assert video_plex_config(vdb)["source"] == "music", (
        "the promotion re-ran and restored an override the user had cleared")


def test_video_server_secrets_are_registered_as_sensitive():
    from config.settings import ConfigManager
    assert 'video_plex.token' in ConfigManager._SENSITIVE_PATHS
    assert 'video_jellyfin.api_key' in ConfigManager._SENSITIVE_PATHS
