"""#1139 — an album torrent stalls for hours, then loses the file.

Adapted from upstream 3.2.0 (6180aff4). All six sub-bugs were confirmed present
in this fork before adapting; each test below names the one it pins.

The report is a single user-visible symptom — an album grab that sits at 0%
until the deadline and then fails with "No audio files found" — produced by six
independent faults that each had to be fixed for the flow to work.
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from core.download_plugins import torrent as T
from core.download_plugins.album_bundle import poll_album_download


def _release(title="Album", magnet=None, url=None, seeders=10):
    return SimpleNamespace(title=title, magnet_uri=magnet, download_url=url,
                           seeders=seeders, protocol="torrent", size=100 * 2 ** 20,
                           indexer_name="idx")


# ── (1) the .torrent is preferred over the magnet ───────────────────────────

def test_the_torrent_file_is_preferred_when_both_are_offered():
    """A magnet is an info-hash — the client has to find the swarm itself, and
    one that cannot sits on 'downloading metadata' forever. add_torrent_smart
    fetches the .torrent server-side (the Sonarr/Radarr handoff) and never got
    the chance."""
    src = Path(T.__file__).read_text(encoding="utf-8")
    album = src[src.index("def download_album_to_staging"):]
    album = album[:album.index("\n    def ")] if "\n    def " in album else album
    assert "picked.download_url or picked.magnet_uri" in album, \
        "the album flow still prefers the magnet"
    assert "picked.magnet_uri or picked.download_url" not in album
    assert "fallback_magnet" in album, "a failed fetch must still have the magnet to fall back on"


# ── (3) the seeder floor ────────────────────────────────────────────────────

def test_releases_known_to_have_no_seeders_are_dropped(monkeypatch):
    monkeypatch.setattr(T.config_manager, "get",
                        lambda k, d=None: 1 if k == "download_source.torrent_min_seeders" else d)
    kept = T._drop_dead_releases([_release("dead", seeders=0), _release("alive", seeders=5)])
    assert [r.title for r in kept] == ["alive"]


def test_a_release_that_reports_no_seeder_count_is_kept(monkeypatch):
    """Some indexers omit the field entirely. This may only drop what we
    positively know is dead, never what is merely unknown."""
    monkeypatch.setattr(T.config_manager, "get",
                        lambda k, d=None: 1 if k == "download_source.torrent_min_seeders" else d)
    kept = T._drop_dead_releases([_release("unknown", seeders=None)])
    assert [r.title for r in kept] == ["unknown"]


def test_the_floor_can_be_switched_off(monkeypatch):
    monkeypatch.setattr(T.config_manager, "get",
                        lambda k, d=None: 0 if k == "download_source.torrent_min_seeders" else d)
    kept = T._drop_dead_releases([_release("dead", seeders=0)])
    assert len(kept) == 1


# ── (2) the album poll finally consults the stall timeout ───────────────────

def _status(state="downloading", downloaded=0, size=0, save_path=None):
    return SimpleNamespace(state=state, progress=0.0, downloaded=downloaded, size=size,
                           download_speed=0, save_path=save_path, incomplete_path=None,
                           error=None, name="rel", content_path=None)


def test_a_stalled_album_download_is_given_up_on():
    emitted = []
    clock = {"t": 0.0}
    cleaned = []

    out = poll_album_download(
        get_status=lambda: _status(),
        title="Dead Release", emit=lambda s, **k: emitted.append((s, k)),
        complete_states=frozenset(["seeding"]),
        poll_interval=0, timeout=100,
        sleep=lambda _s: clock.__setitem__("t", clock["t"] + 10),
        monotonic=lambda: clock["t"],
        stall_check=lambda st, now: now >= 30,          # "stalled" after 30s
        on_stall=lambda: cleaned.append(True),
        log_prefix="[Torrent album]",
    )
    assert out is None
    assert cleaned == [True], "a stalled album torrent must be cleaned up (4)"
    assert emitted[-1][0] == "failed"
    assert "stalled" in emitted[-1][1]["error"].lower()
    assert clock["t"] < 100, "it must give up well before the full deadline"


def test_a_download_that_completed_this_poll_is_never_killed_for_not_moving():
    """The gate sits LAST in the loop on purpose: everything above can return
    on a terminal state this very poll, and a job that finished must not be
    failed for 'no progress'."""
    cleaned = []
    out = poll_album_download(
        get_status=lambda: _status(state="seeding", save_path="/done"),
        title="Fine", emit=lambda s, **k: None,
        complete_states=frozenset(["seeding"]),
        poll_interval=0, timeout=100, sleep=lambda _s: None, monotonic=lambda: 999.0,
        stall_check=lambda st, now: True,               # would fail everything
        on_stall=lambda: cleaned.append(True),
    )
    assert out == "/done"
    assert cleaned == [], "a completed download was cleaned up as if stalled"


def test_usenet_passes_no_stall_check_and_is_unaffected():
    """A stalled torrent is a protocol-specific idea — the metadata phase's
    byte counter is noise, which only StallTracker knows — and usenet has no
    equivalent. It passes neither argument and behaves exactly as before."""
    clock = {"t": 0.0}
    out = poll_album_download(
        get_status=lambda: _status(),
        title="Usenet", emit=lambda s, **k: None,
        complete_states=frozenset(["completed"]),
        poll_interval=0, timeout=50,
        sleep=lambda _s: clock.__setitem__("t", clock["t"] + 10),
        monotonic=lambda: clock["t"],
    )
    assert out is None
    assert clock["t"] >= 50, "without a stall check it must run to the deadline"


def test_a_timeout_also_cleans_up():
    """(4) A timed-out album torrent left active in the client, untracked here,
    was re-grabbed as a duplicate next time."""
    clock = {"t": 0.0}
    cleaned = []
    poll_album_download(
        get_status=lambda: _status(),
        title="Slow", emit=lambda s, **k: None,
        complete_states=frozenset(["seeding"]),
        poll_interval=0, timeout=30,
        sleep=lambda _s: clock.__setitem__("t", clock["t"] + 10),
        monotonic=lambda: clock["t"],
        on_stall=lambda: cleaned.append(True),
    )
    assert cleaned == [True]


def test_the_album_flow_actually_passes_the_stall_check_to_the_poll():
    """The tests above prove the LOOP honours the callbacks. This proves the
    torrent plugin hands them over — without it the loop is correct and the
    album flow still polls a dead torrent for hours, which is the reported bug
    exactly."""
    src = Path(T.__file__).read_text(encoding="utf-8")
    album = src[src.index("def download_album_to_staging"):]
    call = album[album.index("poll_album_download("):]
    call = call[:call.index("\n        )") + 10]
    assert "stall_check=" in call, "the album poll is not given a stall check"
    assert "on_stall=" in call, "a stalled album torrent would not be cleaned up"
    assert "StallTracker" in call and "get_stall_timeout()" in call, \
        "the configured stall timeout must be what drives it"


def test_the_stall_adapter_reads_the_fields_the_tracker_needs():
    seen = {}

    class Tracker:
        def is_stalled(self, downloaded, state, now, size=None):
            seen.update(downloaded=downloaded, state=state, now=now, size=size)
            return False

    T._album_stall_check(Tracker())(_status(downloaded=42, size=99), 7.0)
    assert seen == {"downloaded": 42, "state": "downloading", "now": 7.0, "size": 99}


# ── (5) a dead release falls back instead of ending the batch ───────────────

@pytest.mark.parametrize("marker", [
    "Torrent client refused the release",
    "Torrent download failed or timed out",
    "No audio files copied to staging",
])
def test_album_failures_are_fallback_eligible(marker):
    """Every album failure used to be terminal — none set `fallback`, so one
    dead release ended the whole batch instead of returning to the per-track
    flow (and, in hybrid mode, the next source)."""
    src = Path(T.__file__).read_text(encoding="utf-8")
    at = src.index(marker)
    window = src[at:at + 400]
    assert "result['fallback'] = True" in window, f"{marker!r} is still terminal"


# ── (6) staging uses content_path ───────────────────────────────────────────

def test_a_single_file_torrent_stages_only_its_own_file(tmp_path):
    """content_path for a single-file torrent points at the FILE. Walking its
    parent would be the shared download root — every concurrent grab's audio
    would be donated into this album's staging."""
    src = Path(T.__file__).read_text(encoding="utf-8")
    album = src[src.index("def download_album_to_staging"):]
    assert "resolved_content.is_file()" in album
    assert "single_file = resolved_content" in album
    assert "[single_file] if single_file else collect_audio_after_extraction" in album, \
        "a single-file torrent must not walk its parent directory"


def test_content_path_is_preferred_over_save_path_plus_name():
    src = Path(T.__file__).read_text(encoding="utf-8")
    album = src[src.index("def download_album_to_staging"):]
    # Assert it is READ from the adapter, not merely that the word appears —
    # nulling the read left every looser assertion passing.
    assert "getattr(_final_status, 'content_path', None)" in album, \
        "content_path is never read back from the client"
    assert album.index("if content_path:") < album.index("if single_file is None and torrent_name"), \
        "content_path must be consulted before the old name-based resolution"


def test_an_unreadable_path_is_reported_differently_from_an_empty_one():
    """They need completely different things from the user: a path mapping, or
    a different release."""
    src = Path(T.__file__).read_text(encoding="utf-8")
    album = src[src.index("def download_album_to_staging"):]
    assert "path_mappings" in album, "an unreadable path must name the setting that fixes it"
    assert "No audio files found" in album
