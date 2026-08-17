"""Work out a torrent's info-hash from what we are about to add.

The adapters used to learn a new torrent's hash by listing the client's torrents
before and after the add and diffing — with a ~5 second poll. That is a race:
qBittorrent needs longer than that whenever it has to resolve magnet metadata, or
the client is busy, or the list is large enough that /torrents/info is slow. When
the poll lost, the add was reported as "the torrent client didn't accept the
release" even though the torrent was sitting there downloading.

That is worse than a wrong message. The grab is recorded as failed, so nothing
ever polls it, and the finished download is never imported — the file arrives and
Commissary doesn't know it exists.

The hash is knowable up front in both cases: a magnet carries it, and a .torrent
is the SHA-1 of its bencoded ``info`` value. Pure functions, no I/O.
"""

from __future__ import annotations

import base64
import hashlib
import re
from typing import Optional
from urllib.parse import parse_qs, urlparse

_BTIH = re.compile(r"^urn:btih:([0-9a-fA-F]{40}|[A-Za-z2-7]{32})$")


def from_magnet(uri) -> Optional[str]:
    """The 40-char hex info-hash in a magnet's ``xt=urn:btih:`` parameter.

    Accepts the base32 form too (some indexers still emit it) and normalises it
    to hex, which is what every client API speaks.
    """
    text = str(uri or "").strip()
    if not text.lower().startswith("magnet:"):
        return None
    try:
        params = parse_qs(urlparse(text).query)
    except ValueError:
        return None
    for xt in params.get("xt", []):
        m = _BTIH.match(str(xt).strip())
        if not m:
            continue
        value = m.group(1)
        if len(value) == 40:
            return value.lower()
        try:
            return base64.b32decode(value.upper()).hex()
        except (ValueError, TypeError):
            continue
    return None


def _skip(data: bytes, i: int) -> int:
    """Index just past the bencoded value starting at ``i``.

    A deliberately small scanner: it only has to find where the ``info`` value
    ends so the exact original bytes can be hashed. Re-encoding a parsed
    structure would risk changing byte order or integer formatting and produce a
    hash that matches nothing.
    """
    if i >= len(data):
        raise ValueError("truncated")
    c = data[i:i + 1]
    if c == b"i":                                   # i<int>e
        end = data.index(b"e", i)
        return end + 1
    if c in (b"l", b"d"):                           # list / dict
        i += 1
        while data[i:i + 1] != b"e":
            i = _skip(data, i)
        return i + 1
    if c.isdigit():                                 # <len>:<bytes>
        colon = data.index(b":", i)
        return colon + 1 + int(data[i:colon])
    raise ValueError("not bencode at %d" % i)


def from_torrent_bytes(data) -> Optional[str]:
    """SHA-1 of the bencoded ``info`` value inside a .torrent file.

    Hashes the ORIGINAL bytes rather than a re-encoding, because the info-hash
    is defined over exactly those bytes.
    """
    if not isinstance(data, (bytes, bytearray)) or data[:1] != b"d":
        return None
    buf = bytes(data)
    try:
        i = 1                                       # into the top-level dict
        while buf[i:i + 1] != b"e":
            key_end = _skip(buf, i)
            colon = buf.index(b":", i)
            key = buf[colon + 1:key_end]
            value_end = _skip(buf, key_end)
            if key == b"info":
                return hashlib.sha1(buf[key_end:value_end]).hexdigest()   # noqa: S324
            i = value_end
    except (ValueError, IndexError):
        return None
    return None


def expected_hash(payload) -> Optional[str]:
    """The info-hash of whatever is about to be added — magnet URI or file bytes.
    None when it can't be derived (an HTTP .torrent URL we haven't fetched yet)."""
    if isinstance(payload, (bytes, bytearray)):
        return from_torrent_bytes(payload)
    return from_magnet(payload)
