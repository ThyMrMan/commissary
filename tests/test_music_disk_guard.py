"""Music min-free-disk guard (Discord: Kazimir Iskander's hung LXC).

A fresh non-Docker install left on the default ./downloads path downloads onto
the install disk — a Proxmox LXC's 8GB root — until the container hangs. The
video side has had a guard since its downloads phase; the music side had none.
Every music download (Soulseek AND streaming) funnels through
DownloadOrchestrator.download(), which now refuses when the download disk is
below the configured floor (default 5, 0 = off). Probe failures never block (a
guard error must not wedge downloads). That floor is now ``settings.
min_free_disk_gb``, SHARED with the video guard — it used to exist on both
sides with different defaults — falling back to the legacy music-only
``soulseek.min_free_disk_gb``.

Also: /api/settings carries ``_environment.docker`` so the paths UI can tell
the Docker story vs the bare-metal one and warn on the default-path landmine.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

import core.disk_guard as dg

_ROOT = Path(__file__).resolve().parent.parent
_SETTINGS_JS = (_ROOT / "webui" / "static" / "settings.js").read_text(encoding="utf-8")
_INDEX = (_ROOT / "webui" / "index.html").read_text(encoding="utf-8")


@pytest.fixture()
def real_floor(monkeypatch):
    """The suite pins the guard off (conftest); these tests turn it back on."""
    monkeypatch.setattr(dg, "_floor_override", None)


def test_guard_refuses_below_floor(real_floor, monkeypatch):
    monkeypatch.setattr(dg, "free_gb", lambda p: 1.2)
    ok, free, floor = dg.music_has_room()
    assert ok is False and free == 1.2 and floor == 5.0


def test_guard_passes_above_floor_and_when_disabled(real_floor, monkeypatch):
    monkeypatch.setattr(dg, "free_gb", lambda p: 42.0)
    assert dg.music_has_room()[0] is True
    monkeypatch.setattr(dg, "_floor_override", 0.0)
    monkeypatch.setattr(dg, "free_gb", lambda p: (_ for _ in ()).throw(AssertionError("must not probe when off")))
    assert dg.music_has_room() == (True, None, 0.0)


def test_probe_failure_never_blocks(real_floor, monkeypatch):
    monkeypatch.setattr(dg, "free_gb", lambda p: None)
    ok, free, _floor = dg.music_has_room()
    assert ok is True and free is None


def test_free_gb_walks_to_nearest_existing_ancestor(tmp_path):
    val = dg.free_gb(str(tmp_path / "does" / "not" / "exist" / "yet"))
    assert isinstance(val, float) and val > 0


# ---------------------------------------------------------------------------
# One floor, shared with the video side
# ---------------------------------------------------------------------------

class _Cfg:
    def __init__(self, d=None):
        self._d = dict(d or {})

    def get(self, key, default=None):
        return self._d.get(key, default)

    def set(self, key, value):
        self._d[key] = value


def test_canonical_key_wins_over_the_legacy_music_only_key(monkeypatch):
    import config.settings as cfg
    monkeypatch.setattr(cfg, "config_manager",
                        _Cfg({"settings.min_free_disk_gb": 12,
                              "soulseek.min_free_disk_gb": 5.0}), raising=False)
    assert dg.configured_floor_gb() == 12.0


def test_legacy_key_is_the_fallback_so_existing_installs_keep_their_value(monkeypatch):
    """The canonical key is only written on the next save — until then the floor
    must still be whatever the install had configured, not the default."""
    import config.settings as cfg
    monkeypatch.setattr(cfg, "config_manager",
                        _Cfg({"soulseek.min_free_disk_gb": 25}), raising=False)
    assert dg.configured_floor_gb() == 25.0


def test_floor_defaults_when_neither_key_is_set(monkeypatch):
    import config.settings as cfg
    monkeypatch.setattr(cfg, "config_manager", _Cfg(), raising=False)
    assert dg.configured_floor_gb() == dg.DEFAULT_FLOOR_GB


def test_video_guard_resolves_the_same_shared_floor(monkeypatch, tmp_path):
    """core/video/disk_guard used to read its own organization-blob key with a
    different default (0 vs 5). With no explicit floor passed it must now land
    on the same value the music guard uses."""
    import config.settings as cfg
    from core.video import disk_guard as vdg
    monkeypatch.setattr(cfg, "config_manager",
                        _Cfg({"settings.min_free_disk_gb": 10 ** 6}), raising=False)
    monkeypatch.setattr(dg, "_floor_override", None)
    ok, free = vdg.has_room(str(tmp_path))
    assert ok is False, "the video guard ignored the shared floor"
    # An explicit per-call floor still wins (the organization payload path).
    assert vdg.has_room(str(tmp_path), {"min_free_disk_gb": 0}) == (True, None)


def test_video_promotion_moves_an_explicit_floor_to_the_shared_key(monkeypatch):
    import config.settings as cfg
    from core.video import organization

    class _DB:
        def __init__(self, store):
            self.store = dict(store)

        def get_setting(self, key, default=None):
            return self.store.get(key, default)

        def set_setting(self, key, value):
            self.store[key] = value

    fake = _Cfg()
    monkeypatch.setattr(cfg, "config_manager", fake, raising=False)
    monkeypatch.setattr(organization, "_min_free_promotion_checked", False, raising=False)
    db = _DB({"organization": json.dumps({"min_free_disk_gb": 20})})

    organization.load(db)
    assert fake.get("settings.min_free_disk_gb") == 20.0, (
        "an explicitly configured video floor must survive the move")
    assert db.store[organization._MIN_FREE_PROMOTION_MARKER] == "1"

    # Idempotent: a later deliberate change is not reverted on the next load.
    monkeypatch.setattr(organization, "_min_free_promotion_checked", False, raising=False)
    fake.set("settings.min_free_disk_gb", 3)
    assert organization.load(db)["min_free_disk_gb"] == 3.0


def test_orchestrator_refuses_downloads_when_disk_full(monkeypatch):
    import asyncio

    from core.download_orchestrator import DownloadOrchestrator
    monkeypatch.setattr(dg, "_floor_override", None)
    monkeypatch.setattr(dg, "free_gb", lambda p: 0.5)
    orch = DownloadOrchestrator.__new__(DownloadOrchestrator)   # no client init
    with pytest.raises(RuntimeError, match="Download refused: only 0.5 GB free"):
        asyncio.run(orch.download("someuser", "some\\file.flac", 1000))


# ---------------------------------------------------------------------------
# Environment-aware settings UI contracts
# ---------------------------------------------------------------------------

def test_settings_ui_carries_the_guard_knob_and_environment_awareness():
    assert 'id="min-free-disk-gb"' in _INDEX
    assert "min_free_disk_gb" in _SETTINGS_JS                    # saved with the shared section
    assert "function applyPathsEnvironment" in _SETTINGS_JS
    assert "_environment" in _SETTINGS_JS
    assert 'data-paths-guide="docker"' in _INDEX
    assert 'data-paths-guide="native"' in _INDEX
    assert "data-paths-default-warning" in _INDEX
    # the landmine banner only fires for non-Docker installs still on defaults
    assert "docker || !isDefault" in _SETTINGS_JS
