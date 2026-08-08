"""Deezer must recover its own expired session.

From a real 12-hour log: 669 consecutive download failures, ~20 an hour, every
one of them this pair —

    WARNING  Deezer API error (song.getData): Invalid CSRF token
    ERROR    deezer download <uuid> failed (impl returned None)

The gateway's ``api_token`` (checkForm) is minted once at authentication and
cached for the life of the process. When Deezer expired it, ``_gw_call`` logged
a warning and returned None — and nothing renewed it, so every later call failed
identically and forever. ``reconnect()`` had existed the whole time; nothing
called it.

Two things made it worse than a dead source: ``_authenticated`` was never
cleared, so ``is_configured()`` kept reporting a healthy source and the wishlist
kept handing it 20 tracks an hour; and the only message that named a cause was
the WARNING, while the ERROR the user saw said "impl returned None".
"""

from __future__ import annotations

import threading

import pytest

from core.deezer_download_client import _TOKEN_EXPIRED_KEY, DeezerDownloadClient

EXPIRED = {"error": {_TOKEN_EXPIRED_KEY: "Invalid CSRF token"}}


class _Resp:
    def __init__(self, body):
        self._body = body

    def raise_for_status(self):
        pass

    def json(self):
        return self._body


class _Session:
    """Records every gateway call and replays a scripted list of responses."""

    def __init__(self, script):
        self._script = list(script)
        self.tokens_used = []

    def post(self, url, params=None, json=None, timeout=None):
        self.tokens_used.append((params or {}).get("api_token"))
        return _Resp(self._script.pop(0) if self._script else {"results": {}})


class _TokenAwareSession:
    """The gateway as it really behaves: it rejects a STALE token and accepts a
    renewed one. A flat script can't model this — it would keep rejecting the
    fresh token too, which is a different scenario (see the two-strikes test)."""

    def __init__(self, good_token):
        self._good = good_token
        self.tokens_used = []
        self._lock = threading.Lock()

    def post(self, url, params=None, json=None, timeout=None):
        token = (params or {}).get("api_token")
        with self._lock:
            self.tokens_used.append(token)
        if token != self._good:
            return _Resp(EXPIRED)
        return _Resp({"results": {"ok": 1}})


def _client(script, *, api_token="stale", arl="fake-arl"):
    """A client with no __init__ (no config, no network) — just the seams under test."""
    c = DeezerDownloadClient.__new__(DeezerDownloadClient)
    c._api_lock = threading.Lock()
    c._reauth_lock = threading.Lock()
    c._last_request = 0
    c._min_interval = 0
    c._api_token = api_token
    c._authenticated = True
    c._pending_arl = None
    c._last_gw_error = None
    c._config = {"deezer_download.arl": arl}
    c._session = _Session(script)
    return c


def _renews_to(c, token="fresh"):
    """Stub the handshake: succeeds and installs a new token."""
    calls = []

    def _auth(arl):
        calls.append(arl)
        c._api_token = token
        c._authenticated = True
        return True

    c._authenticate = _auth
    return calls


# ── the renewal itself ───────────────────────────────────────────────────────
def test_an_expired_token_is_renewed_and_the_call_retried():
    """THE regression: this used to return None here, and go on doing so for
    every subsequent call until the process restarted."""
    c = _client([EXPIRED, {"results": {"SNG_ID": "123"}}])
    calls = _renews_to(c)

    assert c._gw_call("song.getData") == {"SNG_ID": "123"}
    assert calls == ["fake-arl"]                        # renewed once
    assert c._session.tokens_used == ["stale", "fresh"]  # ...and retried with the new token


def test_the_retry_does_not_renew_again():
    """If the fresh token is ALSO rejected, that is a real failure, not another
    renewal — otherwise a permanently-bad ARL loops forever."""
    c = _client([EXPIRED, EXPIRED])
    calls = _renews_to(c)

    assert c._gw_call("song.getData") is None
    assert calls == ["fake-arl"]        # exactly one handshake, not two


def test_a_renewal_that_fails_stops_advertising_the_source():
    """is_configured() reads is_authenticated(). Leaving that True is what let
    a client which could no longer talk to Deezer keep being handed tracks."""
    c = _client([EXPIRED])
    c._authenticate = lambda arl: False

    assert c._gw_call("song.getData") is None
    assert c._authenticated is False


def test_no_arl_configured_cannot_renew():
    c = _client([EXPIRED], arl="")
    c._authenticate = lambda arl: pytest.fail("nothing to authenticate with")

    assert c._gw_call("song.getData") is None
    assert c._authenticated is False


def test_the_handshakes_own_call_never_recurses():
    """_authenticate() calls _gw_call('deezer.getUserData'). If that call were
    allowed to renew on failure it would re-enter the handshake that made it."""
    c = _client([EXPIRED])
    c._authenticate = lambda arl: pytest.fail("must not renew from the handshake's own call")

    assert c._gw_call("deezer.getUserData", allow_reauth=False) is None


def test_concurrent_callers_share_one_handshake():
    """A batch of workers all hit the same expiry at once. Without the
    stale-token check each would fire its own handshake — eight logins to
    Deezer for one expiry is a good way to get an ARL banned."""
    c = _client([])
    c._session = _TokenAwareSession("fresh")
    calls = []
    calls_lock = threading.Lock()

    def _auth(arl):
        with calls_lock:
            calls.append(arl)
        c._api_token = "fresh"
        c._authenticated = True
        return True

    c._authenticate = _auth
    results = []
    threads = [threading.Thread(target=lambda: results.append(c._gw_call("song.getData")))
               for _ in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert len(calls) == 1
    assert results == [{"ok": 1}] * 8      # ...and every caller still got its answer


# ── everything else must behave exactly as before ────────────────────────────
def test_an_ordinary_gateway_error_does_not_trigger_a_renewal():
    """Renewal is for the ONE error the client can fix by itself. Re-handshaking
    on every gateway complaint would hammer Deezer's login endpoint."""
    c = _client([{"error": {"GATEWAY_ERROR": "unavailable"}}])
    c._authenticate = lambda arl: pytest.fail("not a token problem")

    assert c._gw_call("song.getData") is None
    assert c._session.tokens_used == ["stale"]          # one attempt, no retry


def test_a_successful_call_is_unchanged_and_clears_the_last_error():
    c = _client([{"results": {"SNG_ID": "1"}}])
    c._last_gw_error = "something old"
    assert c._gw_call("song.getData") == {"SNG_ID": "1"}
    assert c._last_gw_error is None


# ── the failure says what happened ───────────────────────────────────────────
def test_the_reason_reaches_the_download_record():
    """'Failed to get track data' + 'impl returned None' never once said the
    session had expired. The gateway's own reason now rides out with it."""
    c = _client([EXPIRED])
    c._authenticate = lambda arl: False
    c.shutdown_check = None
    c._engine = None
    errors = []
    c._set_error = lambda dl_id, msg: errors.append(msg)

    assert c._download_sync("dl-1", "123", "Some Track") is None
    assert len(errors) == 1
    assert "expired" in errors[0].lower()
    assert "arl" in errors[0].lower()          # ...and says what to do about it


def test_a_failure_with_no_gateway_reason_keeps_the_generic_message():
    c = _client([{"results": {}}])             # empty results, no error recorded
    c.shutdown_check = None
    c._engine = None
    errors = []
    c._set_error = lambda dl_id, msg: errors.append(msg)

    assert c._download_sync("dl-1", "123", "Some Track") is None
    assert errors == ["Failed to get track data"]


# ── a failed handshake never leaves the flag set ─────────────────────────────
def test_authenticate_clears_the_flag_when_deezer_returns_no_user():
    """An invalid ARL returned False while _authenticated stayed True, so the
    source kept advertising itself on a session that had never been valid."""
    c = _client([{"results": {"USER": {"USER_ID": 0}}}])
    assert c._authenticate("bad-arl") is False
    assert c._authenticated is False


def test_authenticate_clears_the_flag_when_the_gateway_says_nothing():
    c = _client([{"error": {"GATEWAY_ERROR": "down"}}])
    assert c._authenticate("fake-arl") is False
    assert c._authenticated is False
