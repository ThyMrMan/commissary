"""Keep API keys out of the log files (the old fork's 2.4.2).

A keyed API puts its key in the query string, requests puts the URL into its
exception, and logger.exception() writes that traceback verbatim - so every
failed call to TMDB, Prowlarr, Last.fm or AcoustID published the key into
app.log (and acoustid.log, which has a formatter of its own).

Redaction happens where a line is COMPLETE - the formatter, after the message,
its arguments and any traceback are joined - and for every formatter at once:
``install()`` wraps ``logging.Formatter.format``, which upstream's
SafeFormatter/ColoredFormatter, the gunicorn logger, the side logs
(source_reuse, post_processing, acoustid) and any library's own formatter all
pass through. Values become ``***``; names stay, so 'api_key=***' still says
which call failed. A line reporting whether a credential is CONFIGURED keeps
its meaning - 'secret=yes' is a status, not a secret.

Best effort, not a guarantee: a secret logged as a bare value with nothing
naming it can't be recognised, an uncaught exception in a background thread
goes to stderr without a formatter, and logs written before this keep what
they leaked.
"""

from __future__ import annotations

import logging
import re

REDACTED = "***"

# A credential in a URL QUERY STRING - the one that actually leaked. Anchored on
# the ?/& that starts a parameter, so 'key=value' in a sentence is left alone.
# 'client' is AcoustID's key parameter (?client=<key>).
_SECRET_QUERY = re.compile(
    r"(?i)([?&](?:api[_-]?key|apikey|key|token|access[_-]?token|refresh[_-]?token|auth|"
    r"password|passwd|pwd|secret|client[_-]?secret|client|arl|sid|session|passkey|"
    r"rss[_-]?key|x[_-]?plex[_-]?token|plextoken|user[_-]?token)=)[^&\s\"'<>\]]+")

# A credential written as a header or field - 'Authorization: Bearer ...',
# "{'api_key': 'abc'}", 'X-Api-Key=abc'. Only names that ARE credentials: a
# bare 'key' is left out on purpose (dict reprs and cache messages are full of
# it). access_token/refresh_token are spelled out - \btoken can't match inside
# them, the underscore being a word character.
_SECRET_FIELD = re.compile(
    r"(?i)\b(authorization|x[_-]?api[_-]?key|api[_-]?key|apikey|x[_-]?plex[_-]?token|"
    r"plex[_-]?token|passkey|rss[_-]?key|client[_-]?secret|secret|password|passwd|"
    r"access[_-]?token|refresh[_-]?token|token|cookie|arl)"
    # '&' ends a value, or this pass would swallow the rest of a URL the query
    # pass already redacted; a status word is not a secret
    r"(['\"]?\s*[:=]\s*['\"]?)(?:bearer\s+|basic\s+)?"
    r"(?!(?:yes|no|none|null|nil|true|false|set|unset|missing|present|"
    r"configured|enabled|disabled|ok)\b)[^\s,;&'\"}\)\]]+")

# Credentials in a URL's authority - 'http://user:pass@host:9696'.
_URL_CREDENTIALS = re.compile(r"(?i)\b([a-z][a-z0-9+.-]*://)[^\s/:@]+:[^\s/@]+@")


def redact_secrets(text):
    """Credential VALUES in a finished log line replaced by ``***``."""
    if not text:
        return text
    out = _SECRET_QUERY.sub(lambda m: m.group(1) + REDACTED, str(text))
    out = _SECRET_FIELD.sub(lambda m: m.group(1) + m.group(2) + REDACTED, out)
    return _URL_CREDENTIALS.sub(lambda m: m.group(1) + REDACTED + ":" + REDACTED + "@", out)


def install() -> None:
    """Wrap ``logging.Formatter.format`` once, for every formatter in the process."""
    original = logging.Formatter.format
    if getattr(original, "_commissary_redacts", False):
        return

    def format(self, record):
        return redact_secrets(original(self, record))

    format._commissary_redacts = True
    format.__wrapped__ = original
    logging.Formatter.format = format
