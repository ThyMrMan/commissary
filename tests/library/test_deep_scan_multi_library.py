"""The standalone Deep Scan covers every Music Library, scored per library.

Deep Scan walks the music folder, treats anything the DB doesn't know about as
a new arrival, and RELOCATES it into Staging. That's fine when the folder is a
landing area and dangerous when it's the user's real library — issue #904, which
is why ``plan_standalone_deep_scan`` blocks the move when the untracked share of
a folder is implausibly large.

Music Libraries (1.9.2) gave music several destinations, and Deep Scan only
knew about ``soulseek.transfer_path``. Fixing that is not just "walk more
folders": pooling every library into one set DEFEATS the guard. A library the
user has just added is 100% untracked by definition, and measured against an
existing large library that share falls under the threshold — so adding a
library full of music they already own would relocate all of it.

Scoring per library is therefore the safety property, not a tidiness choice,
and that is what most of this file pins.
"""

from __future__ import annotations

from core.library.standalone_scan import (
    BLOCK_DESYNC,
    BLOCK_NONE,
    BLOCK_TRANSFER_PERMANENT,
    plan_standalone_deep_scan,
)


def _lib(prefix: str, n: int) -> set:
    return {f"{prefix}/{i}.flac" for i in range(n)}


# ── the reason this is per-library ───────────────────────────────────────────
def test_pooling_libraries_would_relocate_a_newly_added_one():
    """The failure mode being prevented, asserted directly so the rationale
    can't quietly stop being true."""
    established = _lib("/musicA", 100)
    just_added = _lib("/musicB", 40)
    db = set(established)                      # only A has ever been imported

    pooled = plan_standalone_deep_scan(established | just_added, db)
    # 40 of 140 is under the 50% floor, so the guard sees nothing wrong...
    assert pooled["move_blocked"] is False
    assert len(pooled["untracked"]) == 40      # ...and all 40 would be moved


def test_scored_per_library_the_new_one_is_protected():
    established = _lib("/musicA", 100)
    just_added = _lib("/musicB", 40)
    db = set(established)

    a = plan_standalone_deep_scan(established, db)
    b = plan_standalone_deep_scan(just_added, db)

    assert a["untracked"] == set() and a["move_blocked"] is False
    assert b["move_blocked"] is True
    assert b["block_reason"] == BLOCK_DESYNC


def test_a_normal_batch_of_new_arrivals_still_moves():
    """The guard must not become "never move anything" — a handful of genuinely
    new files in a known library is the case Deep Scan exists for."""
    known = _lib("/music", 200)
    arrivals = {"/music/new1.flac", "/music/new2.flac", "/music/new3.flac"}
    plan = plan_standalone_deep_scan(known | arrivals, set(known))
    assert plan["move_blocked"] is False
    assert plan["block_reason"] == BLOCK_NONE
    assert plan["untracked"] == arrivals


def test_permanent_library_blocks_every_library_not_just_the_first():
    """The setting is a statement about how the user treats their music, so it
    applies wherever that music lives."""
    for prefix in ("/musicA", "/musicB"):
        plan = plan_standalone_deep_scan(_lib(prefix, 5), set(), never_move=True)
        assert plan["move_blocked"] is True
        assert plan["block_reason"] == BLOCK_TRANSFER_PERMANENT


# ── the scan roots ───────────────────────────────────────────────────────────
def test_scan_roots_cover_every_library_and_skip_ones_that_are_not_mounted(tmp_path, monkeypatch):
    """An unmounted library must be DROPPED, not scanned: an empty read of it
    would look like every file in it had vanished, and phase 5 deletes DB rows
    for files it can't find."""
    import web_server

    a = tmp_path / "main"
    b = tmp_path / "archive"
    a.mkdir()
    b.mkdir()

    class _DB:
        @staticmethod
        def list_music_libraries():
            return [
                {"label": "Main", "path": str(a)},
                {"label": "Archive", "path": str(b)},
                {"label": "Unmounted", "path": str(tmp_path / "not-here")},
            ]

    monkeypatch.setattr(web_server, "get_database", lambda: _DB())
    roots = web_server._music_scan_roots()

    assert [lbl for lbl, _ in roots] == ["Main", "Archive"]
    assert all(p in (str(a), str(b)) for _, p in roots)


def test_scan_roots_fall_back_to_the_configured_folder(tmp_path, monkeypatch):
    """Every install before Music Libraries, and any whose table is empty."""
    import web_server
    from config.settings import config_manager

    legacy = tmp_path / "Transfer"
    legacy.mkdir()

    class _EmptyDB:
        @staticmethod
        def list_music_libraries():
            return []

    monkeypatch.setattr(web_server, "get_database", lambda: _EmptyDB())
    original = config_manager.get
    monkeypatch.setattr(config_manager, "get",
                        lambda k, d=None: (str(legacy) if k == "soulseek.transfer_path"
                                           else original(k, d)))

    roots = web_server._music_scan_roots()
    assert roots == [("Music Library", str(legacy))]


def test_scan_roots_survive_a_database_error(tmp_path, monkeypatch):
    """A DB hiccup must fall back to the configured folder rather than
    returning nothing — an empty root list would abort the scan."""
    import web_server
    from config.settings import config_manager

    legacy = tmp_path / "Transfer"
    legacy.mkdir()

    def _boom():
        raise RuntimeError("db unavailable")

    monkeypatch.setattr(web_server, "get_database",
                        lambda: type("D", (), {"list_music_libraries": staticmethod(_boom)})())
    original = config_manager.get
    monkeypatch.setattr(config_manager, "get",
                        lambda k, d=None: (str(legacy) if k == "soulseek.transfer_path"
                                           else original(k, d)))

    assert web_server._music_scan_roots() == [("Music Library", str(legacy))]


def test_scan_roots_dedupe_the_same_folder_listed_twice(tmp_path, monkeypatch):
    """Scanning one folder twice would double every count and try to move each
    file a second time after it had already gone."""
    import web_server

    only = tmp_path / "music"
    only.mkdir()

    class _DupeDB:
        @staticmethod
        def list_music_libraries():
            return [{"label": "A", "path": str(only)},
                    {"label": "B", "path": str(only) + "/"}]

    monkeypatch.setattr(web_server, "get_database", lambda: _DupeDB())
    assert len(web_server._music_scan_roots()) == 1
