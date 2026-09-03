"""The Discover backend ported from upstream SoulSync 3.3.1.

Three modules taken verbatim — ``core/discovery/stations.py``,
``core/discovery/playable.py``, ``core/personalized/daily_mixes.py`` — plus the
endpoints that reach them. Upstream's own suites for all three
(``tests/discovery/test_stations.py``, ``test_playable.py``,
``tests/personalized/test_daily_mixes.py``) are alongside this file and pass
unchanged; that is the evidence the modules behave the same here.

This file covers what upstream's cannot: the fork's own schema and frontend.

The port replaces a Daily Mixes builder that could not work. The legacy one
promised "50% library + 50% discovery" and its own docstring conceded the first
half returns nothing — ``tracks`` carries no source ids, so library rows cannot
flow through the sync/wishlist pipeline discovery rows use. Every mix was a
relabelled genre playlist from the discovery pool. The replacement clusters
``listening_history`` instead, so a mix is mostly music you own and can play.

Deliberately NOT ported from the same upstream range: ``canonical.py`` (a
normaliser threaded through playlist/sync/youtube/file_ops), ``curated_full.py``
(watchlist scanner), ``wing_it.py`` (a different feature), and the
``endpoints.py`` double-sync guard (a bug fix worth taking on its own terms).
Porting a whole diff because part of it was wanted is how a port goes wrong.
"""

from __future__ import annotations

import inspect
import pathlib
import sqlite3

import pytest

_ROOT = pathlib.Path(__file__).resolve().parents[2]


# ── the fork's schema, where upstream's assumption is wrong ─────────────────

def test_track_duration_is_read_as_milliseconds_not_seconds():
    """THE port bug. Upstream reads ``tracks.duration`` as SECONDS and
    multiplies by 1000. This fork stores MILLISECONDS — 205520 is 3:25, and
    core/artists/quality.py uses the column directly as duration_ms. Left
    alone, every track in a mix rendered as roughly 48 hours long, and
    upstream's tests never touch duration so nothing would have caught it."""
    import core.personalized.daily_mixes as mod
    src = inspect.getsource(mod)
    assert '"duration_ms": int(r["duration"] or 0),' in src
    assert '* 1000' not in src.split('"duration_ms"')[1][:80]


def test_the_divergence_is_explained_where_a_re_port_would_undo_it():
    """The next person syncing from upstream sees a one-line difference from
    the file they are copying. Without the reason written down, the obvious
    move is to 'fix' it back."""
    import core.personalized.daily_mixes as mod
    src = inspect.getsource(mod)
    note = src.split('"duration_ms"')[0][-600:]
    assert "FORK DIVERGENCE" in note
    assert "MILLISECONDS" in note


def test_a_real_mix_reports_a_plausible_track_length(tmp_path):
    """The assertion the source pin cannot make: build a mix over a real row
    and check the number a listener would see."""
    from database.music_database import MusicDatabase
    import core.personalized.daily_mixes as mod

    path = str(tmp_path / "m.db")
    MusicDatabase(path)
    conn = sqlite3.connect(path)
    conn.execute("INSERT INTO artists (id, name) VALUES (1, 'Test Artist')")
    conn.execute("INSERT INTO albums (id, title, artist_id) VALUES (1, 'Test Album', 1)")
    conn.execute(
        "INSERT INTO tracks (title, artist_id, album_id, duration, file_path, play_count) "
        "VALUES ('Test Track', 1, 1, 205520, '/music/t.flac', 3)")
    conn.commit()
    conn.close()

    owned = mod._owned_tracks_for(MusicDatabase(path), ["Test Artist"])
    rows = owned.get("test artist") or []
    assert rows, "the owned-track lookup found nothing to check"
    ms = rows[0]["duration_ms"]
    assert 200_000 < ms < 210_000, "3:25 became %s ms" % ms


# ── the endpoints ───────────────────────────────────────────────────────────

@pytest.fixture(scope="module")
def client():
    import web_server
    web_server.app.config["TESTING"] = True
    with web_server.app.test_client() as c:
        yield c


def test_all_three_routes_are_registered(client):
    import web_server
    rules = {r.rule for r in web_server.app.url_map.iter_rules()}
    for path in ("/api/discover/stations",
                 "/api/discover/resolve-playable",
                 "/api/discover/personalized/daily-mixes"):
        assert path in rules, path


def test_stations_answers_on_an_empty_library(client):
    """An install with no listening history has no stations, which is an empty
    list rather than an error."""
    r = client.get("/api/discover/stations")
    assert r.status_code == 200
    body = r.get_json()
    assert body["success"] is True
    assert isinstance(body["stations"], list)


def test_daily_mixes_answers_and_reports_when_it_was_built(client):
    """`generated_at` is new — the mixes are cached on a daily TTL, so the page
    needs to be able to say how fresh they are."""
    r = client.get("/api/discover/personalized/daily-mixes")
    assert r.status_code == 200
    body = r.get_json()
    assert body["success"] is True
    assert isinstance(body["mixes"], list)
    assert "generated_at" in body


def test_resolve_playable_needs_a_list_and_says_so(client):
    r = client.post("/api/discover/resolve-playable", json={})
    assert r.status_code == 400
    assert "tracks list required" in r.get_json()["error"]

    r = client.post("/api/discover/resolve-playable",
                    json={"tracks": [{"artist": "Nobody", "title": "Nothing"}]})
    assert r.status_code == 200
    body = r.get_json()
    assert body["success"] is True
    assert body["total"] == 1 and body["matched"] == 0


# ── the legacy builder is replaced, not shadowed ────────────────────────────

def test_the_dead_legacy_builder_no_longer_serves_the_endpoint():
    """`get_all_daily_mixes` is still in core/personalized_playlists.py — other
    callers may use it — but the Discover endpoint must not, or the port
    changed nothing."""
    src = (_ROOT / "web_server.py").read_text(encoding="utf-8")
    endpoint = src.split("def get_daily_mixes():", 1)[1].split("\n@app.route", 1)[0]
    assert "get_or_build_daily_mixes" in endpoint
    assert "get_all_daily_mixes" not in endpoint
    assert src.count("@app.route('/api/discover/personalized/daily-mixes'") == 1


# ── the fork's vanilla Discover keeps working ───────────────────────────────

def _discover_js() -> str:
    return (_ROOT / "webui" / "static" / "discover.js").read_text(
        encoding="utf-8").replace("\r\n", "\n")


def test_the_existing_page_can_already_read_the_new_track_shape():
    """Why no frontend change shipped with this port. Upstream's mix tracks are
    Spotify-shaped — {name, artists[], album{name,images[]}, duration_ms} — and
    this fork's `_normalizeTrack` was already written to accept exactly that
    alongside the flat form."""
    body = _discover_js().split("function _normalizeTrack(", 1)[1][:1200]
    assert "td.artists && td.artists[0]" in body
    assert "td.album && td.album.name" in body
    assert "td.duration_ms" in body


def test_the_subtitle_is_aliased_so_the_shelf_is_not_blank():
    """The one place the shape did change: this page reads `mix.description`,
    upstream renamed it `subtitle`. Without the alias the shelf falls back to a
    flat "Daily Mix" instead of naming the artists in the cluster."""
    src = (_ROOT / "web_server.py").read_text(encoding="utf-8")
    endpoint = src.split("def get_daily_mixes():", 1)[1].split("\n@app.route", 1)[0]
    assert '_m["description"] = _m.get("subtitle")' in endpoint
    # Both keys ship, so adopting upstream's frontend later needs nothing undone.
    assert '"description" not in _m' in endpoint
    assert "mix.description" in _discover_js()


# ── the scope boundary, written down ────────────────────────────────────────

@pytest.mark.parametrize("module", [
    "core/discovery/canonical.py",
    "core/discovery/curated_full.py",
    "core/discovery/wing_it.py",
])
def test_the_wider_upstream_diff_was_deliberately_left_alone(module):
    """These arrived in the same 3.1.8..3.3.1 range and are NOT part of this
    feature: canonical.py is a normaliser threaded through playlist, sync,
    youtube and file_ops; curated_full.py belongs to the watchlist scanner;
    wing_it.py is a separate feature. Each needs its own assessment against a
    fork that has diverged in those areas. This test exists so their absence
    reads as a decision rather than an oversight."""
    assert not (_ROOT / module).exists(), (
        "%s appeared without its own port — check its callers first" % module)
