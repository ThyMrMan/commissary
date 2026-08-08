"""Never guess which title a library row is.

The regression these pin, in the shape it actually happened: a wishlist grab of
Silo S03E06 landed correctly, the scan created the show, and five seconds later
the enrichment worker matched it to an unrelated TMDB id — because it searched
TMDB for the NAME and took the first result on faith. The media server had
already supplied the correct TVDB id; nothing consulted it.

The damage was not cosmetic. The show carried the other title's overview,
status, ratings and SEASON LIST, so a wished S03 had nowhere to land, the
episode never reconciled, and the drain re-grabbed it on every tick. The row
said 'matched', so nothing ever looked again.

Three rules follow, and each has tests here:
  1. resolve by an external id when one exists (exact),
  2. a title search must PROVE the result carries that title, and refuse
     otherwise ('not_found' is retried and visible; a wrong id is neither),
  3. ids already stamped by the old guess get re-checked once, and corrected
     only when a second provider CONTRADICTS them.
"""

from __future__ import annotations

import sys
import types

import pytest

from core.video.enrichment.clients import TMDBClient, TVDBClient
from core.video.enrichment.worker import VideoEnrichmentWorker
from database.video_database import VideoDatabase

# The real ids from the report, so the fixtures read as the incident.
SILO_TMDB = 125988
SILO_TVDB = 457516
IMPOSTOR_TMDB = 278850


@pytest.fixture()
def db(tmp_path):
    return VideoDatabase(database_path=str(tmp_path / "video_library.db"))


class _Resp:
    def __init__(self, body, status=200):
        self._body = body
        self.status_code = status

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError("HTTP %d" % self.status_code)

    def json(self):
        return self._body


def _fake_requests(monkeypatch, handler):
    """Install a fake ``requests`` whose GET is ``handler(url, params) -> _Resp``.
    Every URL requested is recorded, so a test can assert what was NOT called."""
    urls = []

    def _get(url, **kw):
        urls.append(url)
        return handler(url, kw.get("params") or {})

    monkeypatch.setitem(sys.modules, "requests",
                        types.SimpleNamespace(get=_get, post=lambda *a, **k: _Resp({})))
    return urls


# ── rule 1: an external id resolves exactly ──────────────────────────────────
def test_a_show_with_a_tvdb_id_is_resolved_by_it_not_by_its_name(monkeypatch):
    """THE regression. The search endpoint would have answered with the
    impostor; the point is that it is never asked."""
    def handler(url, params):
        if "/find/" in url:
            assert params.get("external_source") == "tvdb_id"
            assert str(SILO_TVDB) in url
            return _Resp({"tv_results": [{"id": SILO_TMDB, "name": "Silo",
                                          "overview": "Correct"}]})
        if "/search/" in url:
            raise AssertionError("resolved by id — the search must never run")
        return _Resp({"overview": "Correct", "seasons": []})

    urls = _fake_requests(monkeypatch, handler)
    res = TMDBClient("KEY").match("show", "Silo", 2026,
                                  external_ids={"tvdb_id": SILO_TVDB})
    assert res["id"] == SILO_TMDB
    assert any("/find/" in u for u in urls)
    assert not any("/search/" in u for u in urls)


def test_a_known_tmdb_id_still_wins_over_a_cross_reference(monkeypatch):
    """The server's own id is the most certain thing we have; carrying a tvdb_id
    as well must not send us on a resolution round-trip."""
    def handler(url, params):
        if "/find/" in url:
            raise AssertionError("known id — /find must never run")
        return _Resp({"overview": "O", "seasons": []})

    _fake_requests(monkeypatch, handler)
    res = TMDBClient("KEY").match("show", "Silo", 2026, known_id=SILO_TMDB,
                                  external_ids={"tvdb_id": SILO_TVDB})
    assert res["id"] == SILO_TMDB


def test_a_404_on_one_external_id_falls_through_to_the_next(monkeypatch):
    """TMDB not carrying a tvdb id is not an error — try the imdb id."""
    def handler(url, params):
        if "/find/" in url and params.get("external_source") == "tvdb_id":
            return _Resp({}, status=404)
        if "/find/" in url and params.get("external_source") == "imdb_id":
            return _Resp({"tv_results": [{"id": SILO_TMDB}]})
        return _Resp({"overview": "O", "seasons": []})

    _fake_requests(monkeypatch, handler)
    res = TMDBClient("KEY").match(
        "show", "Silo", None, external_ids={"tvdb_id": 1, "imdb_id": "tt14688458"})
    assert res["id"] == SILO_TMDB


def test_a_movie_never_asks_tmdb_to_resolve_a_tvdb_id(monkeypatch):
    """tvdb_id is a TV-only external source; sending it for a movie is a
    guaranteed 404 and a wasted call."""
    seen = []

    def handler(url, params):
        if "/find/" in url:
            seen.append(params.get("external_source"))
            return _Resp({}, status=404)
        return _Resp({"results": []})

    _fake_requests(monkeypatch, handler)
    TMDBClient("KEY").match("movie", "Dune", None,
                            external_ids={"tvdb_id": 999, "imdb_id": "tt1"})
    assert seen == ["imdb_id"]


# ── rule 2: a title search has to prove itself ───────────────────────────────
def test_a_namesake_is_refused_rather_than_accepted(monkeypatch):
    """The old code took results[0]. With nothing to resolve by, a result that
    isn't named like the show must yield no match at all."""
    def handler(url, params):
        if "/search/" in url:
            return _Resp({"results": [{"id": IMPOSTOR_TMDB, "name": "Silo Valley"},
                                      {"id": 999, "name": "Silos of Nebraska"}]})
        raise AssertionError("nothing matched — the detail fetch must not run")

    _fake_requests(monkeypatch, handler)
    assert TMDBClient("KEY").match("show", "Silo", None) is None


def test_the_right_result_is_taken_from_anywhere_in_the_list(monkeypatch):
    """Verification is not 'check results[0]' — the real title is often ranked
    below a more popular namesake."""
    def handler(url, params):
        if "/search/" in url:
            return _Resp({"results": [{"id": IMPOSTOR_TMDB, "name": "Silo Valley"},
                                      {"id": SILO_TMDB, "name": "Silo"}]})
        return _Resp({"overview": "O", "seasons": []})

    _fake_requests(monkeypatch, handler)
    assert TMDBClient("KEY").match("show", "Silo", None)["id"] == SILO_TMDB


def test_a_wrong_year_does_not_bury_the_real_title(monkeypatch):
    """A year filter that excludes the real show leaves only namesakes to choose
    between — the precise mechanism that made the impostor look like the best
    answer. The year is dropped and the search retried before giving up."""
    def handler(url, params):
        if "/search/" in url:
            if params.get("first_air_date_year"):
                return _Resp({"results": [{"id": IMPOSTOR_TMDB, "name": "Silo Valley"}]})
            return _Resp({"results": [{"id": SILO_TMDB, "name": "Silo"}]})
        return _Resp({"overview": "O", "seasons": []})

    _fake_requests(monkeypatch, handler)
    assert TMDBClient("KEY").match("show", "Silo", 2026)["id"] == SILO_TMDB


def test_the_name_check_tolerates_punctuation_and_articles(monkeypatch):
    """It routes through the same normalize_title the release matcher uses, so
    accents, '&' and a leading article are not false misses."""
    def handler(url, params):
        if "/search/" in url:
            return _Resp({"results": [{"id": 42, "name": "The Haunting of Hill House"}]})
        return _Resp({"overview": "O", "seasons": []})

    _fake_requests(monkeypatch, handler)
    assert TMDBClient("KEY").match("show", "Haunting of Hill House", None)["id"] == 42


def test_the_original_name_counts_as_a_name(monkeypatch):
    """A foreign-language show is stored under whichever title the server used."""
    def handler(url, params):
        if "/search/" in url:
            return _Resp({"results": [{"id": 7, "name": "Squid Game",
                                       "original_name": "오징어 게임"}]})
        return _Resp({"overview": "O", "seasons": []})

    _fake_requests(monkeypatch, handler)
    assert TMDBClient("KEY").match("show", "Squid Game", None)["id"] == 7


def test_a_failed_search_call_still_raises(monkeypatch):
    """A 429/5xx is a FAILED call, not 'no match' — it must propagate so the
    worker records 'error' (retried) instead of burning the row to not_found."""
    def handler(url, params):
        return _Resp({}, status=429)

    _fake_requests(monkeypatch, handler)
    with pytest.raises(RuntimeError):
        TMDBClient("KEY").match("show", "Silo", None)


def test_tvdb_also_refuses_a_result_that_is_not_named_like_the_show(monkeypatch):
    def handler(url, params):
        if "/search" in url:
            return _Resp({"data": [{"tvdb_id": 1, "name": "Something Else"}]})
        return _Resp({"data": {}})

    monkeypatch.setitem(sys.modules, "requests", types.SimpleNamespace(
        get=lambda url, **kw: handler(url, kw.get("params") or {}),
        post=lambda *a, **k: _Resp({"data": {"token": "t"}})))
    assert TVDBClient("KEY").match("show", "Silo", None) is None


# ── the ids reach the client ─────────────────────────────────────────────────
def test_enrichment_next_carries_the_rows_other_provider_ids(db):
    db.upsert_show_tree("plex", {"server_id": "s1", "title": "Silo",
                                 "tvdb_id": SILO_TVDB, "seasons": []})
    nxt = db.enrichment_next("tmdb")
    assert nxt["known_id"] is None                       # the server gave no TMDB id
    assert nxt["external_ids"]["tvdb_id"] == SILO_TVDB   # ...but it gave this


def test_a_services_own_id_is_never_offered_back_as_a_cross_reference(db):
    """tvdb_id is the TVDB worker's ``known_id``; repeating it under
    external_ids would invite it to resolve itself against itself."""
    db.upsert_show_tree("plex", {"server_id": "s1", "title": "Silo",
                                 "tvdb_id": SILO_TVDB, "tmdb_id": SILO_TMDB,
                                 "seasons": []})
    nxt = db.enrichment_next("tvdb")
    assert nxt["known_id"] == SILO_TVDB
    assert "tvdb_id" not in nxt["external_ids"]
    assert nxt["external_ids"]["tmdb_id"] == SILO_TMDB


def test_the_worker_hands_the_cross_reference_ids_to_the_client(db):
    seen = {}

    class _Client:
        enabled = True

        def match(self, kind, title, year, known_id=None, external_ids=None):
            seen.update({"known_id": known_id, "external_ids": external_ids})
            return {"id": SILO_TMDB, "metadata": {}}

    db.upsert_show_tree("plex", {"server_id": "s1", "title": "Silo",
                                 "tvdb_id": SILO_TVDB, "seasons": []})
    VideoEnrichmentWorker(db, "tmdb", _Client()).process_one()
    assert seen["known_id"] is None
    assert seen["external_ids"] == {"tvdb_id": SILO_TVDB}


# ── rule 3: re-check ids the old guess already stamped ───────────────────────
class _VerifyClient:
    """A client that only answers the cross-reference question."""

    enabled = True

    def __init__(self, resolves_to, boom=False):
        self._to = resolves_to
        self._boom = boom
        self.calls = 0

    def match(self, *a, **k):
        return None

    def find_by_external_id(self, kind, external_ids):
        self.calls += 1
        if self._boom:
            raise RuntimeError("tmdb down")
        return (self._to, None)


def _silo_stamped_wrong(db):
    """The library as the incident left it: Silo, correct TVDB id from the
    server, TMDB id of an unrelated show, and marked 'matched' so nothing
    would ever look at it again."""
    show_id = db.upsert_show_tree("plex", {
        "server_id": "s1", "title": "Silo", "tvdb_id": SILO_TVDB,
        "tmdb_id": IMPOSTOR_TMDB, "overview": "the impostor's plot", "seasons": []})
    db.enrichment_apply("tmdb", "show", show_id, matched=True, external_id=IMPOSTOR_TMDB)
    return show_id


def test_only_shows_that_can_be_checked_are_offered(db):
    """A show with no tvdb_id has nothing to check against — re-running the same
    title search would only agree with itself."""
    db.upsert_show_tree("plex", {"server_id": "s1", "title": "No Cross Ref",
                                 "tmdb_id": 1, "seasons": []})
    db.upsert_show_tree("plex", {"server_id": "s2", "title": "Unmatched",
                                 "tvdb_id": 2, "seasons": []})
    assert db.identity_unverified_show() is None


def test_a_contradicted_id_is_corrected_and_its_metadata_cleared(db):
    show_id = _silo_stamped_wrong(db)
    client = _VerifyClient(SILO_TMDB)
    w = VideoEnrichmentWorker(db, "tmdb", client)

    assert w._verify_identity_one() is True
    with db.connect() as c:
        row = c.execute("SELECT tmdb_id, tmdb_match_status, overview, "
                        "tmdb_identity_verified FROM shows WHERE id=?", (show_id,)).fetchone()
    assert row["tmdb_id"] == SILO_TMDB              # re-pointed at the real show
    assert row["tmdb_match_status"] is None         # ...and re-queued to enrich by it
    assert not row["overview"]                      # the impostor's text is gone
    assert row["tmdb_identity_verified"] == 1


def test_an_agreeing_id_is_left_alone(db):
    """Confirmation must not churn a correct row — no re-point, no cleared
    metadata, no re-enrichment."""
    show_id = db.upsert_show_tree("plex", {
        "server_id": "s1", "title": "Silo", "tvdb_id": SILO_TVDB,
        "tmdb_id": SILO_TMDB, "overview": "the real plot", "seasons": []})
    db.enrichment_apply("tmdb", "show", show_id, matched=True, external_id=SILO_TMDB)

    w = VideoEnrichmentWorker(db, "tmdb", _VerifyClient(SILO_TMDB))
    assert w._verify_identity_one() is True
    with db.connect() as c:
        row = c.execute("SELECT tmdb_id, tmdb_match_status, overview, "
                        "tmdb_identity_verified FROM shows WHERE id=?", (show_id,)).fetchone()
    assert row["tmdb_id"] == SILO_TMDB
    assert row["tmdb_match_status"] == "matched"
    assert row["overview"] == "the real plot"
    assert row["tmdb_identity_verified"] == 1


def test_each_show_is_checked_once_and_then_never_again(db):
    _silo_stamped_wrong(db)
    client = _VerifyClient(SILO_TMDB)
    w = VideoEnrichmentWorker(db, "tmdb", client)
    assert w._verify_identity_one() is True
    assert w._verify_identity_one() is False     # queue drained
    assert client.calls == 1


def test_a_failed_check_leaves_the_row_for_next_time(db):
    """A call that didn't complete is not evidence about the id, so it must not
    bless the match it never actually checked."""
    show_id = _silo_stamped_wrong(db)
    w = VideoEnrichmentWorker(db, "tmdb", _VerifyClient(None, boom=True))
    assert w._verify_identity_one() is True      # it did work (and failed)
    with db.connect() as c:
        row = c.execute("SELECT tmdb_id, tmdb_identity_verified FROM shows WHERE id=?",
                        (show_id,)).fetchone()
    assert row["tmdb_id"] == IMPOSTOR_TMDB       # unchanged — nothing was proven
    assert not row["tmdb_identity_verified"]     # ...so it stays queued
    assert db.identity_unverified_show() is not None


def test_an_id_tmdb_cannot_resolve_is_not_treated_as_a_contradiction(db):
    """TMDB not carrying the tvdb id proves nothing about the stored match.
    Clearing it there would replace a possibly-right id with no id at all."""
    show_id = _silo_stamped_wrong(db)
    w = VideoEnrichmentWorker(db, "tmdb", _VerifyClient(None))
    assert w._verify_identity_one() is True
    with db.connect() as c:
        row = c.execute("SELECT tmdb_id, tmdb_identity_verified FROM shows WHERE id=?",
                        (show_id,)).fetchone()
    assert row["tmdb_id"] == IMPOSTOR_TMDB
    assert row["tmdb_identity_verified"] == 1    # checked as well as we can; don't loop


def test_the_sweep_only_runs_on_the_tmdb_worker(db):
    """The queue is keyed on tmdb_id; the TVDB worker running it would feed TMDB
    ids to TVDB and double-process the same rows."""
    _silo_stamped_wrong(db)
    assert VideoEnrichmentWorker(db, "tvdb", _VerifyClient(SILO_TMDB))._verify_identity_one() is False
