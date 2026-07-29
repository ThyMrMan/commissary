"""Tell the admin when somebody is waiting on them.

Asked for: a Discord webhook when something is requested and needs approval to
download.

The notification framework already existed (Settings → Notifications, and the
Automations WHEN/THEN builder, both of which already speak Discord). What did not
exist was an event worth subscribing to. The two 'added' events fire for EVERY
add — the admin's own, and every automation add (watchlist scan, collections,
RSS, import lists) which can write dozens of rows in one batch — and their
payload carries no approval state and no requester. Subscribing to those to catch
member requests means drowning in adds you already knew about, with nothing
saying which one needs you.

So: a distinct `video_request_pending`, published only when a request actually
lands awaiting approval, carrying who asked, how many, and which queue.

Both queues publish it. The watchlist has had an approval gate since 1.6.7 and
has never been able to tell anyone about it; the wishlist gained one in 1.8.10.
"""

from __future__ import annotations

import os
import tempfile

import pytest

from core.automation import blocks as automation_blocks
from core.video import notifications
from database.video_database import VideoDatabase

PENDING = "video_request_pending"


@pytest.fixture()
def events(monkeypatch):
    """Capture what the DB layer publishes to the video event bus."""
    import core.video.download_events as dev
    seen: list = []
    monkeypatch.setattr(dev, "publish", lambda t, d=None: seen.append((t, dict(d or {}))))
    return seen


@pytest.fixture()
def db(tmp_path):
    return VideoDatabase(database_path=str(tmp_path / "v.db"))


def _pending(events):
    return [d for t, d in events if t == PENDING]


# ── it is subscribable at all ────────────────────────────────────────────────
def test_the_event_is_a_registered_notification_type():
    """normalize_connection silently DROPS any event not in EVENTS, so leaving it
    out would let someone tick the box and never receive anything."""
    assert PENDING in notifications.EVENTS
    assert PENDING in notifications._EVENT_LABEL


def test_a_connection_can_actually_subscribe_to_it():
    conn = notifications.normalize_connection({
        "type": "discord", "url": "https://discord.com/api/webhooks/x/y",
        "name": "admin alerts", "events": [PENDING]})
    assert conn is not None
    assert conn["events"] == [PENDING]


def test_every_notification_event_has_a_settings_label():
    """The Settings → Notifications checkboxes are built from the SERVER's event
    list but labelled from a hardcoded map, falling back to the raw key. Without
    this the new event's checkbox reads 'video_request_pending' on the very
    screen you go to in order to switch it on."""
    import pathlib
    js = (pathlib.Path(__file__).resolve().parents[1] / "webui" / "static" / "video"
          / "video-settings.js").read_text(encoding="utf-8")
    label_map = js.split("_NOTIFY_EVENT_LABEL = {", 1)[1].split("};", 1)[0]
    for event in notifications.EVENTS:
        assert event in label_map, "%s has no label in video-settings.js" % event


def test_it_is_an_automation_trigger_too():
    """The other half of the answer: the WHEN/THEN builder can drive a Discord
    action off it, with conditions on who asked and which queue."""
    trig = next((b for b in automation_blocks.TRIGGERS if b["type"] == PENDING), None)
    assert trig is not None, "not registered as an automation trigger"
    assert trig["available"] is True
    for field in ("queue", "requested_by"):
        assert field in trig["condition_fields"], field
    for var in ("count", "queue", "requested_by", "title"):
        assert var in trig["variables"], var


# ── it fires exactly when it should ──────────────────────────────────────────
def test_an_admin_wish_does_not_raise_a_request(db, events):
    db.add_movie_to_wishlist(550, "Fight Club")
    assert _pending(events) == []


def test_a_member_wish_does(db, events):
    db.add_movie_to_wishlist(603, "The Matrix", approved=False,
                             requested_by=7, requested_by_name="Member")
    got = _pending(events)
    assert len(got) == 1
    assert got[0]["kind"] == "movie"
    assert got[0]["queue"] == "wishlist"
    assert got[0]["requested_by"] == "Member"
    assert got[0]["count"] == 1


def test_the_added_event_still_fires_alongside_it(db, events):
    """Both, deliberately: the item WAS added, so an existing subscription to the
    'added' event goes on behaving exactly as it did."""
    db.add_movie_to_wishlist(603, "The Matrix", approved=False, requested_by=7)
    assert [t for t, _ in events] == ["video_wishlist_item_added", PENDING]


def test_a_season_request_is_ONE_notification_not_one_per_episode(db, events):
    """The property that makes this usable. 24 episodes must not be 24 Discord
    messages — one event carrying the count."""
    eps = [{"season_number": 1, "episode_number": n} for n in range(1, 25)]
    db.add_episodes_to_wishlist(1396, "Breaking Bad", eps,
                                approved=False, requested_by=7, requested_by_name="Member")
    got = _pending(events)
    assert len(got) == 1
    assert got[0]["count"] == 24


def test_re_asking_for_something_already_requested_stays_quiet(db, events):
    """Otherwise a member refreshing the page re-notifies the admin."""
    eps = [{"season_number": 1, "episode_number": 1}]
    db.add_episodes_to_wishlist(1396, "BB", eps, approved=False, requested_by=7)
    events.clear()
    db.add_episodes_to_wishlist(1396, "BB", eps, approved=False, requested_by=7)
    assert _pending(events) == []


def test_a_youtube_request_fires_too(db, events):
    db.add_videos_to_wishlist({"youtube_id": "c1", "title": "Chan"},
                              [{"youtube_id": "v1", "title": "Vid"}],
                              approved=False, requested_by=7, requested_by_name="Member")
    got = _pending(events)
    assert len(got) == 1 and got[0]["kind"] == "youtube"


# ── the watchlist, which never had this ──────────────────────────────────────
def test_a_member_follow_raises_a_request(db, events):
    db.add_to_watchlist("show", 1399, "Game of Thrones",
                        approved=False, requested_by=7, requested_by_name="Member")
    got = _pending(events)
    assert len(got) == 1
    assert got[0]["queue"] == "watchlist"
    assert got[0]["requested_by"] == "Member"


def test_an_admin_follow_does_not(db, events):
    db.add_to_watchlist("show", 1400, "Some Show")
    assert _pending(events) == []


def test_the_two_queues_are_distinguishable(db, events):
    """One subscription covers both, so the message has to say which list to open."""
    db.add_movie_to_wishlist(550, "Fight Club", approved=False, requested_by=7)
    db.add_to_watchlist("show", 1399, "GoT", approved=False, requested_by=7)
    assert [d["queue"] for d in _pending(events)] == ["wishlist", "watchlist"]


# ── what it reads like ───────────────────────────────────────────────────────
def test_the_message_names_the_asker_and_the_queue():
    msg = notifications.format_message(PENDING, {
        "kind": "movie", "title": "Fight Club", "count": 1,
        "queue": "wishlist", "requested_by": "Member"})
    assert "Fight Club" in msg and "Member" in msg and "wishlist" in msg


def test_the_message_says_how_many_for_a_bulk_request():
    msg = notifications.format_message(PENDING, {
        "kind": "episode", "title": "Breaking Bad", "count": 24,
        "queue": "wishlist", "requested_by": "Member"})
    assert "24 items" in msg


def test_a_missing_requester_does_not_break_the_message():
    msg = notifications.format_message(PENDING, {"title": "Anon", "count": 1,
                                                 "queue": "wishlist"})
    assert "Anon" in msg and "asked by" not in msg


def test_existing_event_messages_are_unchanged():
    """A regression pin: the new fields must not leak into the events people
    already have wired up."""
    assert notifications.format_message(
        "video_download_completed", {"title": "Dune", "year": 2021, "quality": "2160p"}
    ) == "✅ Imported: Dune (2021 · 2160p)"
    assert notifications.format_message(
        "video_wishlist_item_added", {"kind": "movie", "title": "Fight Club", "count": 1}
    ) == "⭐ Wishlisted: Fight Club"


# ── dispatch only reaches subscribers ────────────────────────────────────────
def test_handle_event_ignores_an_unknown_event():
    notifications.handle_event("not_a_real_event", {"title": "x"})   # must not raise


def test_a_connection_not_subscribed_gets_nothing(monkeypatch):
    sent: list = []
    monkeypatch.setattr(notifications, "_send",
                        lambda c, e, d: sent.append((c["name"], e)) or True)
    monkeypatch.setattr(notifications, "load_connections", lambda db: [
        {"id": 1, "name": "wants-requests", "type": "discord", "url": "http://x",
         "token": "", "chat_id": "", "events": [PENDING], "enabled": True},
        {"id": 2, "name": "wants-downloads-only", "type": "discord", "url": "http://y",
         "token": "", "chat_id": "", "events": ["video_download_completed"], "enabled": True},
    ])
    import api.video as videoapi
    monkeypatch.setattr(videoapi, "get_video_db", lambda: object())

    import threading
    real = threading.Thread

    class _Now(real):          # run the fan-out inline so the assert is deterministic
        def start(self):
            self.run()

    monkeypatch.setattr(threading, "Thread", _Now)
    notifications.handle_event(PENDING, {"title": "Fight Club"})
    assert sent == [("wants-requests", PENDING)]
