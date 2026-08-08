"""Episode wishlist rows alias the show name — the logs have to read it.

From a real app.log: 49 lines of

    video hybrid: torrent grab refused for None: The torrent client didn't
    accept the release.

Episode wishlist rows carry ``show_title``; only movie rows carry ``title``.
Both log sites in ``_default_enqueue`` read ``title`` alone, so every episode
refusal named nothing — and a refusal you can't attribute to a title is a
message that costs log volume and gives back nothing.

``_default_record_outcome`` already handles this exact aliasing ("episode drain
items alias the show id as show_tmdb_id — read both or episodes silently never
record"). These two sites were simply missed.
"""

from __future__ import annotations

import logging

import pytest

from core.automation.handlers import video_process_wishlist as vpw

_LOGGER = "soulsync.automation.video_process_wishlist"

_EPISODE = {"show_tmdb_id": 125988, "show_title": "Silo",
            "season_number": 3, "episode_number": 6}
_MOVIE = {"tmdb_id": 1, "title": "I Saw the Devil", "year": 2010}


def _messages(caplog):
    return [r.getMessage() for r in caplog.records if r.name == _LOGGER]


@pytest.fixture()
def no_disk(monkeypatch):
    """Send _default_enqueue down its disk-guard refusal path (the simpler of
    the two log sites — it returns before any client is contacted)."""
    from core.video import disk_guard, organization
    monkeypatch.setattr("api.video.get_video_db", lambda: None)
    monkeypatch.setattr(organization, "load", lambda db: {})
    monkeypatch.setattr(disk_guard, "has_room", lambda *a, **k: (False, 1.5))


@pytest.fixture()
def refusing_client(monkeypatch):
    """Disk is fine; the torrent client refuses the release."""
    from core.video import client_grab, disk_guard, organization
    monkeypatch.setattr("api.video.get_video_db", lambda: None)
    monkeypatch.setattr(organization, "load", lambda db: {})
    monkeypatch.setattr(disk_guard, "has_room", lambda *a, **k: (True, 500.0))
    monkeypatch.setattr(vpw, "_category_for_item", lambda *a, **k: None)
    monkeypatch.setattr(client_grab, "grab", lambda *a, **k: {
        "ok": False, "error": "The torrent client didn't accept the release."})


def test_a_disk_guard_skip_names_the_show(no_disk, caplog):
    caplog.set_level(logging.WARNING, logger=_LOGGER)
    assert vpw._default_enqueue(_EPISODE, {"source": "torrent"}, [], "episode", "/tv") is False
    msgs = _messages(caplog)
    assert any("Silo" in m for m in msgs), msgs
    assert not any("skipping grab of None" in m for m in msgs), msgs


def test_a_refused_grab_names_the_show(refusing_client, caplog):
    caplog.set_level(logging.WARNING, logger=_LOGGER)
    best = {"source": "torrent", "download_url": "magnet:?xt=urn:btih:abc"}
    assert vpw._default_enqueue(_EPISODE, best, [], "episode", "/tv") is False
    msgs = _messages(caplog)
    assert any("Silo" in m for m in msgs), msgs
    assert not any("refused for None" in m for m in msgs), msgs


def test_a_movie_still_logs_its_own_title(refusing_client, caplog):
    """`title` must stay the first choice — the fallback is for rows that
    genuinely have no `title`, not a replacement for it."""
    caplog.set_level(logging.WARNING, logger=_LOGGER)
    best = {"source": "torrent", "download_url": "magnet:?xt=urn:btih:abc"}
    assert vpw._default_enqueue(_MOVIE, best, [], "movie", "/movies") is False
    assert any("I Saw the Devil" in m for m in _messages(caplog))


def test_a_row_with_neither_name_does_not_print_none(no_disk, caplog):
    caplog.set_level(logging.WARNING, logger=_LOGGER)
    assert vpw._default_enqueue({}, {"source": "torrent"}, [], "episode", "/tv") is False
    msgs = _messages(caplog)
    assert msgs and not any("None" in m for m in msgs), msgs
