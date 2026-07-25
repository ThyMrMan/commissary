"""Video download source-config — pure normalize for mode + hybrid chain
(soulseek/torrent/usenet only), isolated from music.

The SEEDING keys in this payload are the exception: they're shared with music
under ``torrent_client.*`` (one physical torrent client, one setting), so these
tests stub the app-wide config the same way
tests/test_video_api.py::test_slskd_config_shared_via_config_manager does.
"""

from __future__ import annotations

import json

import pytest

import config.settings as cfg
import core.video.download_config as dc
from core.video.download_config import (
    MODES,
    SOURCES,
    load,
    normalize_hybrid_order,
    normalize_mode,
    save,
)


class _Cfg:
    """Stand-in for the app-wide config_manager."""

    def __init__(self, initial=None):
        self._d = dict(initial or {})

    def get(self, key, default=None):
        return self._d.get(key, default)

    def set(self, key, value):
        self._d[key] = value


@pytest.fixture(autouse=True)
def fake_config(monkeypatch):
    """Every test gets a clean shared-config stub, and the module's one-shot
    promotion flag is reset so each test exercises it from scratch."""
    fake = _Cfg()
    monkeypatch.setattr(cfg, "config_manager", fake, raising=False)
    monkeypatch.setattr(dc, "_promotion_checked", False, raising=False)
    return fake


def test_modes_are_video_only():
    assert SOURCES == ("soulseek", "torrent", "usenet")
    assert MODES == ("soulseek", "torrent", "usenet", "hybrid")


def test_normalize_mode():
    assert normalize_mode("torrent") == "torrent"
    assert normalize_mode("HYBRID") == "hybrid"
    assert normalize_mode("spotify") == "soulseek"   # music sources rejected
    assert normalize_mode(None) == "soulseek"
    assert normalize_mode("") == "soulseek"


def test_normalize_hybrid_order_filters_dedupes_defaults():
    assert normalize_hybrid_order(["torrent", "usenet"]) == ["torrent", "usenet"]
    assert normalize_hybrid_order(["torrent", "torrent", "spotify"]) == ["torrent"]
    assert normalize_hybrid_order([]) == ["soulseek"]        # never empty
    assert normalize_hybrid_order("garbage") == ["soulseek"]
    # Accepts a JSON string (as stored in the KV table).
    assert normalize_hybrid_order(json.dumps(["usenet", "soulseek"])) == ["usenet", "soulseek"]


class _FakeDB:
    def __init__(self):
        self._kv = {}

    def get_setting(self, key, default=None):
        return self._kv.get(key, default)

    def set_setting(self, key, value):
        self._kv[key] = value


# seeding lifecycle keys (arr-parity P5) ride the same config payload
_SEED_DEFAULTS = {"seed_ratio_goal": 0.0, "seed_time_goal_hours": 0, "seed_remove_data": True,
                  "seed_mode": "soulsync"}


def test_load_defaults():
    assert load(_FakeDB()) == {"download_mode": "soulseek", "hybrid_order": ["soulseek"],
                               **_SEED_DEFAULTS}


def test_save_validates_and_roundtrips():
    db = _FakeDB()
    out = save(db, {"download_mode": "hybrid", "hybrid_order": ["torrent", "bogus", "torrent", "usenet"]})
    assert out == {"download_mode": "hybrid", "hybrid_order": ["torrent", "usenet"],
                   **_SEED_DEFAULTS}
    assert load(db) == out                                  # persisted + reloads identically


def test_save_ignores_absent_keys():
    db = _FakeDB()
    save(db, {"download_mode": "usenet"})
    assert load(db)["download_mode"] == "usenet"
    save(db, {"hybrid_order": ["soulseek", "torrent"]})     # mode key absent → unchanged
    assert load(db)["download_mode"] == "usenet"
    assert load(db)["hybrid_order"] == ["soulseek", "torrent"]


# ── seeding keys are SHARED with music (torrent_client.*) ───────────────────

def test_seed_keys_read_and_write_the_shared_config(fake_config):
    db = _FakeDB()
    save(db, {"seed_ratio_goal": 2.5, "seed_time_goal_hours": 48,
              "seed_remove_data": False, "seed_mode": "client"})
    # Landed in the app-wide config, where music's sweep reads them too...
    assert fake_config.get("torrent_client.seed_ratio_goal") == 2.5
    assert fake_config.get("torrent_client.seed_time_goal_hours") == 48
    assert fake_config.get("torrent_client.seed_remove_data") is False
    assert fake_config.get("torrent_client.seed_mode") == "client"
    # ...and NOT in video.db (that's what let the two sides drift).
    assert "seed_ratio_goal" not in db._kv
    assert "seed_mode" not in db._kv
    # Still surfaced on the same payload, so core.video.seeding is unchanged.
    assert load(db)["seed_ratio_goal"] == 2.5
    assert load(db)["seed_mode"] == "client"


def test_video_source_config_stays_in_video_db(fake_config):
    db = _FakeDB()
    save(db, {"download_mode": "torrent", "hybrid_order": ["torrent"]})
    assert db._kv["download_mode"] == "torrent"
    assert fake_config.get("torrent_client.download_mode") is None


# ── the one-shot promotion: safer value always wins ─────────────────────────

@pytest.mark.parametrize("video,music,expected", [
    # identical → promoted silently
    ((2.0, 24, "1", "soulsync"), (2.0, 24, True, "soulsync"), (2.0, 24, True, "soulsync")),
    # either side 0 (seed forever) wins over a finite goal
    ((2.0, 0, "1", "soulsync"), (0, 24, True, "soulsync"), (0.0, 0, True, "soulsync")),
    ((0, 0, "1", "soulsync"), (5.0, 100, True, "soulsync"), (0.0, 0, True, "soulsync")),
    # both finite → the longer goal wins (seeds more, deletes later)
    ((2.0, 10, "1", "soulsync"), (3.0, 5, True, "soulsync"), (3.0, 10, True, "soulsync")),
    # remove_data OFF on either side wins — never newly delete client data
    ((1.0, 1, "0", "soulsync"), (1.0, 1, True, "soulsync"), (1.0, 1, False, "soulsync")),
    ((1.0, 1, "1", "soulsync"), (1.0, 1, False, "soulsync"), (1.0, 1, False, "soulsync")),
    # "client" mode survives only when BOTH chose it
    ((1.0, 1, "1", "client"), (1.0, 1, True, "client"), (1.0, 1, True, "client")),
    ((1.0, 1, "1", "client"), (1.0, 1, True, "soulsync"), (1.0, 1, True, "soulsync")),
])
def test_promotion_merges_toward_the_safer_value(fake_config, video, music, expected):
    db = _FakeDB()
    db._kv.update({"seed_ratio_goal": str(video[0]), "seed_time_goal_hours": str(video[1]),
                   "seed_remove_data": video[2], "seed_mode": video[3]})
    fake_config.set("torrent_client.seed_ratio_goal", music[0])
    fake_config.set("torrent_client.seed_time_goal_hours", music[1])
    fake_config.set("torrent_client.seed_remove_data", music[2])
    fake_config.set("torrent_client.seed_mode", music[3])

    out = load(db)
    assert (out["seed_ratio_goal"], out["seed_time_goal_hours"],
            out["seed_remove_data"], out["seed_mode"]) == expected
    assert db._kv[dc._SEED_PROMOTION_MARKER] == "1"


def test_promotion_is_idempotent(fake_config):
    """A second run must not re-merge — otherwise a later deliberate change
    would be silently reverted toward the old video.db value on every load."""
    db = _FakeDB()
    db._kv.update({"seed_ratio_goal": "2.0", "seed_time_goal_hours": "0",
                   "seed_remove_data": "1", "seed_mode": "soulsync"})
    fake_config.set("torrent_client.seed_ratio_goal", 5.0)
    # Neither ratio is 0, so the longer goal (5.0) wins and is written through.
    assert load(db)["seed_ratio_goal"] == 5.0
    assert fake_config.get("torrent_client.seed_ratio_goal") == 5.0

    dc._promotion_checked = False                       # simulate a fresh process
    save(db, {"seed_ratio_goal": 1.0})                  # deliberate later change
    assert load(db)["seed_ratio_goal"] == 1.0, "promotion re-ran and clobbered a real edit"


def test_promotion_on_a_fresh_install_is_a_no_op(fake_config):
    db = _FakeDB()
    out = load(db)
    assert (out["seed_ratio_goal"], out["seed_time_goal_hours"]) == (0.0, 0)
    assert out["seed_remove_data"] is True and out["seed_mode"] == "soulsync"
    assert db._kv[dc._SEED_PROMOTION_MARKER] == "1"
