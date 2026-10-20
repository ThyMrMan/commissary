"""Credentials must not reach the log file (ported from the old fork's 2.4.2).

Found while reading a user's app.log: every TMDB 404 had written the key out in
full, because ``requests`` puts the URL it called into the exception's message
and ``logger.exception()`` writes that traceback verbatim. The log is the file
people paste into issues and hand to support, so a key in it is a key disclosed.

Redaction happens in the FORMATTER -- the one place every line passes through
(console and file, app and gunicorn), and the only place where the line is
already complete: message, arguments and appended traceback.
"""

from __future__ import annotations

import logging

from core.commissary.log_redaction import redact_secrets
from utils.logging_config import ColoredFormatter, SafeFormatter

TMDB = ("404 Client Error: Not Found for url: "
        "https://api.themoviedb.org/3/tv/65942/season/4?api_key=0123456789abcdef"
        "&append_to_response=credits")


def _line(fmt, msg, *args, exc=None):
    rec = logging.LogRecord("soulsync.test", logging.ERROR, __file__, 1, msg, args, exc)
    return fmt.format(rec)


# ── the leak that was found ──────────────────────────────────────────────────
def test_a_keyed_url_in_a_message_loses_its_key():
    out = redact_secrets(TMDB)
    assert "0123456789abcdef" not in out
    assert "api_key=***" in out
    # what the line was logged FOR survives
    assert "https://api.themoviedb.org/3/tv/65942/season/4" in out
    assert "append_to_response=credits" in out


def test_the_key_is_gone_from_a_traceback_too():
    """The real shape: nothing in the MESSAGE carries the key -- it is inside the
    exception the formatter appends, which is why redacting the message alone
    would have left the log exactly as leaky as it was."""
    fmt = SafeFormatter(fmt="%(message)s")
    try:
        raise RuntimeError(TMDB)
    except RuntimeError as exc:
        line = _line(fmt, "tmdb_season failed for %s S%s", 65942, 4,
                     exc=(type(exc), exc, exc.__traceback__))
    assert "tmdb_season failed for 65942 S4" in line
    assert "0123456789abcdef" not in line
    assert "api_key=***" in line


def test_the_console_formatter_redacts_as_well():
    """ColoredFormatter is what the console and the gunicorn handlers use."""
    line = _line(ColoredFormatter(fmt="%(message)s"), TMDB)
    assert "0123456789abcdef" not in line and "api_key=***" in line


# ── the other shapes a credential arrives in ─────────────────────────────────
def test_other_query_parameters_are_covered():
    # 'key' and 'sid' are here because they are credential names ONLY in a query
    # string — nothing outside a URL may key off a word that plain prose uses.
    for param, secret in (("apikey", "prowlarrkey99"), ("X-Plex-Token", "plexy"),
                          ("token", "xyzzy42"), ("passkey", "pk991"), ("arl", "deezerarl"),
                          ("key", "googlish99"), ("sid", "sess771"),
                          ("access_token", "at_9911")):
        out = redact_secrets("GET http://h/api?q=x&%s=%s&page=2" % (param, secret))
        assert secret not in out, param
        assert "%s=***" % param in out and "page=2" in out


def test_a_token_with_a_prefix_is_still_a_token():
    """`\\btoken` does not match inside 'access_token' — the underscore is a word
    character — so a field-shaped one needs naming in its own right."""
    out = redact_secrets("refreshed {'access_token': 'at_9911', 'refresh_token': 'rt_2'}")
    assert "at_9911" not in out and "rt_2" not in out


def test_headers_and_field_reprs_are_covered():
    out = redact_secrets("headers={'Authorization': 'Bearer eyJhbGciOi.abc', "
                         "'X-Api-Key': 'sekret99'}")
    assert "eyJhbGciOi.abc" not in out and "sekret99" not in out
    assert "Authorization" in out and "X-Api-Key" in out


def test_credentials_in_a_url_authority_are_covered():
    out = redact_secrets("connecting to http://admin:hunter2@192.168.50.100:8080/api")
    assert "hunter2" not in out and "admin" not in out
    assert "192.168.50.100:8080/api" in out    # the host is the useful part


def test_redacting_twice_does_not_stack():
    once = redact_secrets(TMDB)
    assert redact_secrets(once) == once


# ── what must NOT be redacted ────────────────────────────────────────────────
def test_ordinary_lines_are_untouched():
    for line in ("[Auto-Import] Scan cycle: 88 candidates in /media/temp/music",
                 "Retrieved 500 play history entries from Plex",
                 "cache key=album:123 hit=True count=5",
                 "Slow request: POST /api/video/downloads/search/start -> 200 in 6207.7ms"):
        assert redact_secrets(line) == line


def test_a_reported_status_is_not_a_secret():
    """Found on a real log line: 'Last.fm credentials: key=yes, secret=yes,
    session=NO' says whether each one is CONFIGURED. Blacking those out costs
    the diagnostic the line exists for and protects nothing."""
    line = "[Your Artists] Last.fm credentials: key=yes, secret=yes, session=NO"
    assert redact_secrets(line) == line
    assert redact_secrets("token: none") == "token: none"
    # ...but a value that merely STARTS like one is still a value
    assert redact_secrets("password=yesterday7") == "password=***"


def test_a_url_with_nothing_secret_in_it_survives_whole():
    url = "https://api.themoviedb.org/3/tv/65942?language=en-US&page=2"
    assert redact_secrets(url) == url


def test_empty_input_is_returned_as_given():
    assert redact_secrets("") == ""
    assert redact_secrets(None) is None


# ── what the rebuild adds ───────────────────────────────────────────────────
def test_acoustids_client_key_is_covered():
    """AcoustID sends its key as ?client=, and acoustid.log has its own formatter."""
    out = redact_secrets("GET https://api.acoustid.org/v2/lookup?client=AbCd1234&meta=recordings")
    assert "AbCd1234" not in out and "client=***" in out and "meta=recordings" in out


def test_every_formatter_redacts_not_only_upstreams():
    """The side logs (source_reuse, post_processing, acoustid) use a plain
    logging.Formatter - the wrap reaches them too."""
    plain = logging.Formatter(fmt="%(asctime)s - %(message)s")
    assert "api_key=***" in _line(plain, TMDB) and "0123456789abcdef" not in _line(plain, TMDB)


def test_it_is_installed_by_importing_the_logging_setup():
    import utils.logging_config  # noqa: F401
    assert getattr(logging.Formatter.format, "_commissary_redacts", False)


def test_installing_twice_wraps_once():
    from core.commissary import log_redaction
    before = logging.Formatter.format
    log_redaction.install()
    assert logging.Formatter.format is before
