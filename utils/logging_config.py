import logging
import logging.handlers
import sys
import re
from pathlib import Path
from datetime import datetime
from typing import Optional

LOGGER_NAMESPACE = "soulsync"

REDACTED = "***"

# A credential carried in a URL QUERY STRING — 'https://api.themoviedb.org/3/tv/1?api_key=…'.
# This is the one that actually leaked: a requests HTTPError stringifies the whole
# URL it called, and logger.exception() writes that traceback out verbatim, so every
# 404 from a keyed API published the key. Anchored on the ?/& that starts a query
# parameter, so an ordinary 'key=value' written in a sentence is left alone.
_SECRET_QUERY = re.compile(
    r"(?i)([?&](?:api[_-]?key|apikey|key|token|access[_-]?token|refresh[_-]?token|auth|"
    r"password|passwd|pwd|secret|client[_-]?secret|arl|sid|session|passkey|rss[_-]?key|"
    r"x[_-]?plex[_-]?token|plextoken|user[_-]?token)=)[^&\s\"'<>\]]+")

# A credential written as a header or a field — 'Authorization: Bearer …',
# "{'api_key': 'abc'}", 'X-Api-Key=abc'. Only names that ARE credentials are
# listed: a bare 'key' is left out on purpose, because dict reprs and cache
# messages are full of it and redacting those would cost more than it protects.
_SECRET_FIELD = re.compile(
    # access_token / refresh_token are spelled out: \btoken does not match inside
    # them (the underscore is a word character), so a bare 'token' alternative
    # leaves '{"access_token": "…"}' untouched.
    r"(?i)\b(authorization|x[_-]?api[_-]?key|api[_-]?key|apikey|x[_-]?plex[_-]?token|"
    r"plex[_-]?token|passkey|rss[_-]?key|client[_-]?secret|secret|password|passwd|"
    r"access[_-]?token|refresh[_-]?token|token|cookie|arl)"
    # `&` ends a value. Without that, this second pass re-matches what the query
    # pass just replaced and swallows the rest of the URL with it
    # ('api_key=***&append_to_response=credits' → 'api_key=***').
    # A plain status word is not a secret either — plenty of lines report whether
    # a credential is CONFIGURED ('Last.fm credentials: key=yes, secret=yes'), and
    # blacking those out costs the diagnostic while protecting nothing.
    r"(['\"]?\s*[:=]\s*['\"]?)(?:bearer\s+|basic\s+)?"
    r"(?!(?:yes|no|none|null|nil|true|false|set|unset|missing|present|"
    r"configured|enabled|disabled|ok)\b)[^\s,;&'\"}\)\]]+")

# Credentials in a URL's authority — 'http://user:pass@host:9696'.
_URL_CREDENTIALS = re.compile(r"(?i)\b([a-z][a-z0-9+.-]*://)[^\s/:@]+:[^\s/@]+@")


def redact_secrets(text: str) -> str:
    """Replace credential VALUES in a log line with ``***``, keeping their names.

    Applied to the finished line rather than to the message, so it covers the
    traceback a logger.exception() appends as well — which is where API keys
    reached the log file from (the URL inside a requests exception). Names are
    deliberately kept: 'api_key=***' still tells you which call failed, which is
    the whole reason the line was logged.

    Redaction is best-effort defence in depth, not a guarantee: it cannot know a
    secret that is logged as a bare value with nothing naming it."""
    if not text:
        return text
    out = _SECRET_QUERY.sub(lambda m: m.group(1) + REDACTED, str(text))
    out = _SECRET_FIELD.sub(lambda m: m.group(1) + m.group(2) + REDACTED, out)
    return _URL_CREDENTIALS.sub(lambda m: m.group(1) + REDACTED + ":" + REDACTED + "@", out)


class SafeFormatter(logging.Formatter):
    """Formatter that handles Unicode characters safely on Windows"""
    
    @staticmethod
    def strip_emojis(text):
        """Remove emoji characters from text for Windows compatibility"""
        # Remove emoji characters but keep other Unicode
        emoji_pattern = re.compile("["
            u"\U0001F600-\U0001F64F"  # emoticons
            u"\U0001F300-\U0001F5FF"  # symbols & pictographs
            u"\U0001F680-\U0001F6FF"  # transport & map symbols
            u"\U0001F1E0-\U0001F1FF"  # flags (iOS)
            u"\U00002702-\U000027B0"
            u"\U000024C2-\U0001F251"
            "]+", flags=re.UNICODE)
        return emoji_pattern.sub('', text)
    
    def format(self, record):
        # Try to format with emojis first, fall back to stripped version.
        # Both paths go out through redact_secrets: this is the one place every
        # log line (console and file, app and gunicorn) passes through, and the
        # line is already complete here — message, arguments and the traceback an
        # exception log appends, which is where the keys were getting out.
        try:
            return redact_secrets(super().format(record))
        except UnicodeEncodeError:
            # Strip emojis and try again for Windows compatibility
            record.getMessage = lambda: self.strip_emojis(record.msg % record.args if record.args else record.msg)
            return redact_secrets(super().format(record))

class ColoredFormatter(SafeFormatter):
    COLORS = {
        'DEBUG': '\033[94m',
        'INFO': '\033[92m',
        'WARNING': '\033[93m',
        'ERROR': '\033[91m',
        'CRITICAL': '\033[95m',
        'RESET': '\033[0m'
    }
    
    def format(self, record):
        log_color = self.COLORS.get(record.levelname, self.COLORS['RESET'])
        reset_color = self.COLORS['RESET']
        
        record.levelname = f"{log_color}{record.levelname}{reset_color}"
        return super().format(record)

def setup_logging(level: str = "INFO", log_file: Optional[str] = None) -> logging.Logger:
    log_level = getattr(logging, level.upper(), logging.INFO)
    
    logger = logging.getLogger(LOGGER_NAMESPACE)
    logger.setLevel(log_level)
    
    if logger.handlers:
        logger.handlers.clear()
    
    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setLevel(log_level)
    # Force UTF-8 encoding for Windows compatibility with Unicode characters
    if hasattr(console_handler.stream, 'reconfigure'):
        console_handler.stream.reconfigure(encoding='utf-8', errors='replace')
    
    console_formatter = ColoredFormatter(
        fmt='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
        datefmt='%Y-%m-%d %H:%M:%S'
    )
    console_handler.setFormatter(console_formatter)
    logger.addHandler(console_handler)
    
    if log_file:
        log_path = Path(log_file)
        log_path.parent.mkdir(parents=True, exist_ok=True)
        
        file_handler = logging.handlers.RotatingFileHandler(
            log_path, encoding='utf-8', maxBytes=10*1024*1024, backupCount=3
        )
        file_handler.setLevel(log_level)
        
        file_formatter = SafeFormatter(
            fmt='%(asctime)s - %(name)s - %(levelname)s - %(funcName)s:%(lineno)d - %(message)s',
            datefmt='%Y-%m-%d %H:%M:%S'
        )
        file_handler.setFormatter(file_formatter)
        logger.addHandler(file_handler)
    
    logger.info(f"Logging initialized with level: {level}")
    return logger

def get_logger(name: str) -> logging.Logger:
    return logging.getLogger(f"{LOGGER_NAMESPACE}.{name}")

def set_log_level(level: str) -> bool:
    """Dynamically change the log level for all loggers without restart"""
    try:
        log_level = getattr(logging, level.upper(), logging.INFO)

        # Get the root "soulsync" logger
        root_logger = logging.getLogger(LOGGER_NAMESPACE)
        root_logger.setLevel(log_level)

        # Update all handlers
        for handler in root_logger.handlers:
            handler.setLevel(log_level)

        root_logger.info(f"Log level changed to: {level.upper()}")
        return True
    except Exception as e:
        logging.getLogger(LOGGER_NAMESPACE).error(f"Error setting log level: {e}")
        return False

def get_current_log_level() -> str:
    """Get the current log level"""
    root_logger = logging.getLogger(LOGGER_NAMESPACE)
    return logging.getLevelName(root_logger.level)

main_logger = get_logger("main")
