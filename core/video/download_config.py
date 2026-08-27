"""Video download SOURCE config — which source(s) to download from.

Video only ever uses three sources: **soulseek / torrent / usenet** (no streaming
APIs — those are music-only). ``download_mode`` is one of those three, or
``hybrid``; in hybrid mode ``hybrid_order`` is the ordered chain of enabled sources
the (later-phase) engine tries in turn.

Pure normalize here (no DB, no network) so it's unit-tested in isolation. Stored in
video.db's ``video_settings`` (``download_mode`` + ``hybrid_order`` JSON), and the
music side never imports this module.

EXCEPT the seeding-lifecycle keys, which are SHARED with music — see
``_SEED_KEYS``. They ride this config payload (so ``core.video.seeding`` keeps
reading them off the same dict) but live in the app-wide config store, not
video.db.
"""

from __future__ import annotations

import json
from typing import Any

from utils.logging_config import get_logger

logger = get_logger("video.download_config")

SOURCES = ("soulseek", "torrent", "usenet")
MODES = SOURCES + ("hybrid",)


def normalize_mode(value: Any) -> str:
    v = str(value or "").strip().lower()
    return v if v in MODES else "soulseek"


def normalize_hybrid_order(value: Any) -> list:
    """Ordered, de-duped list of valid sources; defaults to ['soulseek']. Accepts a
    JSON string (as stored) or a list (as posted)."""
    arr = value
    if isinstance(arr, str):
        try:
            arr = json.loads(arr)
        except (ValueError, TypeError):
            arr = None
    out = []
    if isinstance(arr, list):
        for s in arr:
            s = str(s or "").strip().lower()
            if s in SOURCES and s not in out:
                out.append(s)
    return out or ["soulseek"]


# ── Season packs (TV only) ──────────────────────────────────────────────────
#
# One grab for a whole season instead of N grabs for N episodes. The machinery
# has existed since season-pack support landed; what it never had was a way to
# turn on, because `video.season_packs` and `video.season_pack_min_episodes`
# were the only two `video.*` config keys in the tree and nothing wrote them.
#
# They stay in the APP-WIDE config rather than moving into video.db, because
# that is where the drain has always READ them from. Moving the store while
# leaving the reader alone is precisely the 2.1.2 download-source bug (see
# core/downloads/source_chain.py): one setting, two stores, and a save that
# looks like it worked.
SEASON_PACK_MODES = ("prefer", "only")
SEASON_PACK_MIN_EPISODES = 4

_SEASON_KEYS = {
    "season_packs": ("video.season_packs", False),
    "season_pack_min_episodes": ("video.season_pack_min_episodes", SEASON_PACK_MIN_EPISODES),
    "season_pack_mode": ("video.season_pack_mode", "prefer"),
}


def normalize_season_pack_mode(value: Any) -> str:
    """``prefer`` (pack if there is one, else per-episode) or ``only`` (pack or
    wait). Anything unrecognised reads as ``prefer`` — the mode that still
    acquires the episodes."""
    v = str(value or "").strip().lower()
    return v if v in SEASON_PACK_MODES else "prefer"


def normalize_min_episodes(value: Any) -> int:
    """How many genuinely-missing episodes a season needs before one pack beats
    N singles. Floored at 2: a 'pack' covering a single episode is an episode,
    and season_pack_groups already refuses that."""
    try:
        return max(2, min(200, int(value)))
    except (TypeError, ValueError):
        return SEASON_PACK_MIN_EPISODES


def season_pack_settings(config_get=None) -> dict:
    """The three season-pack settings, normalized. ONE reader, so the drain and
    the settings page cannot disagree about what is configured."""
    if config_get is None:
        from config.settings import config_manager
        config_get = config_manager.get
    return {
        "season_packs": bool(config_get(*_SEASON_KEYS["season_packs"])),
        "season_pack_min_episodes": normalize_min_episodes(
            config_get(*_SEASON_KEYS["season_pack_min_episodes"])),
        "season_pack_mode": normalize_season_pack_mode(
            config_get(*_SEASON_KEYS["season_pack_mode"])),
    }


def _norm_ratio(value: Any) -> float:
    try:
        return max(0.0, min(100.0, float(value)))
    except (TypeError, ValueError):
        return 0.0


def _norm_hours(value: Any) -> int:
    try:
        return max(0, min(24 * 365, int(value)))
    except (TypeError, ValueError):
        return 0


def _norm_seed_mode(value: Any) -> str:
    return "client" if str(value or "").strip().lower() == "client" else "soulsync"


# Seeding lifecycle (arr-parity P5) — SHARED with music. Both sides drive the
# SAME physical torrent client through core.torrent_clients, and each runs its
# own sweep (core.automation.handlers.seeding_sweep / video_seeding_sweep). When
# these lived in video.db too, the two sweeps judged the same client against two
# independent goal sets, and in seed_mode="client" both wrote share limits into
# it — potentially conflicting ones. One physical resource, one setting: they
# live in the app-wide config under torrent_client.*, which video already reads
# for the client category (core/video/client_grab.py).
#
# BOTH goals default 0 = the sweep is OFF and torrents behave exactly as before —
# managing (and deleting from) someone's torrent client is strictly opt-in.
_SEED_KEYS = {
    "seed_ratio_goal": ("torrent_client.seed_ratio_goal", 0),
    "seed_time_goal_hours": ("torrent_client.seed_time_goal_hours", 0),
    "seed_remove_data": ("torrent_client.seed_remove_data", True),
    # Who enforces the goal: "soulsync" (sweep polls + removes) or "client"
    # (write the ratio/time limit into the torrent client, arr-style).
    "seed_mode": ("torrent_client.seed_mode", "soulsync"),
}

# Marker row in video.db recording that the one-shot promotion below has run.
_SEED_PROMOTION_MARKER = "seed_goals_promoted"
_promotion_checked = False


def _promote_seed_goals_once(db) -> None:
    """One-shot: fold any pre-split video.db seeding goals into the shared
    config. Runs before the first read so no consumer ever sees a half-migrated
    state; the video.db marker makes it permanent, the module flag makes repeat
    calls free.

    Existing installs have values in BOTH stores and there is no merge that
    leaves both sides unchanged, so every field resolves toward whichever
    setting DELETES LESS. A migration may leave torrents seeding longer than
    intended; it must never start removing data (or removing it sooner) than
    what the user configured on either side.
    """
    global _promotion_checked
    if _promotion_checked:
        return
    try:
        if str(db.get_setting(_SEED_PROMOTION_MARKER) or "") == "1":
            _promotion_checked = True
            return
        from config.settings import config_manager

        raw = {k: db.get_setting(k) for k in _SEED_KEYS}
        if any(v is not None for v in raw.values()):
            v_ratio = _norm_ratio(raw["seed_ratio_goal"])
            v_hours = _norm_hours(raw["seed_time_goal_hours"])
            v_remove = (raw["seed_remove_data"] or "1") != "0"
            v_mode = _norm_seed_mode(raw["seed_mode"])

            m_ratio = _norm_ratio(config_manager.get("torrent_client.seed_ratio_goal", 0))
            m_hours = _norm_hours(config_manager.get("torrent_client.seed_time_goal_hours", 0))
            m_remove = bool(config_manager.get("torrent_client.seed_remove_data", True))
            m_mode = _norm_seed_mode(config_manager.get("torrent_client.seed_mode", "soulsync"))

            # 0 means "seed forever" — the safest state, so it always wins.
            merged_ratio = 0.0 if (v_ratio == 0 or m_ratio == 0) else max(v_ratio, m_ratio)
            merged_hours = 0 if (v_hours == 0 or m_hours == 0) else max(v_hours, m_hours)
            # Off wins: never newly enable deleting the client's data.
            merged_remove = bool(v_remove and m_remove)
            # "client" pushes limits into the user's own client — keep it only
            # if both sides had already chosen it.
            merged_mode = "client" if (v_mode == "client" and m_mode == "client") else "soulsync"

            if (v_ratio, v_hours, v_remove, v_mode) != (m_ratio, m_hours, m_remove, m_mode):
                logger.warning(
                    "Seeding goals differed between the video and music sides and are now "
                    "shared; merged to the setting that deletes less. video=%s music=%s -> %s",
                    (v_ratio, v_hours, v_remove, v_mode),
                    (m_ratio, m_hours, m_remove, m_mode),
                    (merged_ratio, merged_hours, merged_remove, merged_mode))

            config_manager.set("torrent_client.seed_ratio_goal", merged_ratio)
            config_manager.set("torrent_client.seed_time_goal_hours", merged_hours)
            config_manager.set("torrent_client.seed_remove_data", merged_remove)
            config_manager.set("torrent_client.seed_mode", merged_mode)

        # The old video.db rows are left in place but are never read again —
        # the marker is what stops this re-running.
        db.set_setting(_SEED_PROMOTION_MARKER, "1")
        _promotion_checked = True
    except Exception:
        # Never let a migration hiccup break loading the download config; the
        # marker stays unset so the next call retries.
        logger.exception("seed-goal promotion failed (non-fatal)")


def load(db) -> dict:
    from config.settings import config_manager
    _promote_seed_goals_once(db)
    return {
        "download_mode": normalize_mode(db.get_setting("download_mode")),
        "hybrid_order": normalize_hybrid_order(db.get_setting("hybrid_order")),
        "seed_ratio_goal": _norm_ratio(config_manager.get(*_SEED_KEYS["seed_ratio_goal"])),
        "seed_time_goal_hours": _norm_hours(config_manager.get(*_SEED_KEYS["seed_time_goal_hours"])),
        "seed_remove_data": bool(config_manager.get(*_SEED_KEYS["seed_remove_data"])),
        "seed_mode": _norm_seed_mode(config_manager.get(*_SEED_KEYS["seed_mode"])),
        **season_pack_settings(config_manager.get),
    }


def save(db, body: Any) -> dict:
    """Persist whichever known keys are present in ``body``. The video-specific
    source config goes to video.db; the seeding keys go to the shared config."""
    from config.settings import config_manager
    body = body if isinstance(body, dict) else {}
    _promote_seed_goals_once(db)
    if "download_mode" in body:
        db.set_setting("download_mode", normalize_mode(body.get("download_mode")))
    if "hybrid_order" in body:
        db.set_setting("hybrid_order", json.dumps(normalize_hybrid_order(body.get("hybrid_order"))))
    if "seed_ratio_goal" in body:
        config_manager.set(_SEED_KEYS["seed_ratio_goal"][0], _norm_ratio(body.get("seed_ratio_goal")))
    if "seed_time_goal_hours" in body:
        config_manager.set(_SEED_KEYS["seed_time_goal_hours"][0], _norm_hours(body.get("seed_time_goal_hours")))
    if "seed_remove_data" in body:
        config_manager.set(_SEED_KEYS["seed_remove_data"][0], bool(body.get("seed_remove_data")))
    if "seed_mode" in body:
        config_manager.set(_SEED_KEYS["seed_mode"][0], _norm_seed_mode(body.get("seed_mode")))
    if "season_packs" in body:
        config_manager.set(_SEASON_KEYS["season_packs"][0], bool(body.get("season_packs")))
    if "season_pack_min_episodes" in body:
        config_manager.set(_SEASON_KEYS["season_pack_min_episodes"][0],
                           normalize_min_episodes(body.get("season_pack_min_episodes")))
    if "season_pack_mode" in body:
        config_manager.set(_SEASON_KEYS["season_pack_mode"][0],
                           normalize_season_pack_mode(body.get("season_pack_mode")))
    return load(db)


__all__ = ["SOURCES", "MODES", "SEASON_PACK_MODES", "SEASON_PACK_MIN_EPISODES",
           "normalize_mode", "normalize_hybrid_order", "normalize_season_pack_mode",
           "normalize_min_episodes", "season_pack_settings", "load", "save"]
