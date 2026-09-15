"""ISRC codes: the identifier every release of one recording shares.

Sources write them with or without hyphens and in either case, and Qobuz
sometimes wraps one in an object. Everything that stores or looks one up uses
the one shape here: twelve upper-case characters, or '' for no valid code.
"""

from __future__ import annotations

import re
from typing import Any

_ISRC_RE = re.compile(r'^[A-Z]{2}[A-Z0-9]{3}[0-9]{7}$')


def normalize_isrc(value: Any) -> str:
    """The ISRC in ``value`` as twelve upper-case characters, or '' when it holds no valid one."""
    if isinstance(value, dict):
        value = value.get('value') or value.get('id') or ''
    if not isinstance(value, str):
        return ''
    code = re.sub(r'[\s-]', '', value).upper()
    return code if _ISRC_RE.match(code) else ''
