"""The staging file cache keeps each file's length.

A staged album file can only be claimed by its place in the album and its length
when the cache the claim reads from carries the length. The metadata reader
already returned it; ``_get_staging_file_cache`` dropped it.
"""

from __future__ import annotations

import os
import struct
import tempfile

import pytest

# Redirect the DB before importing web_server so it never touches a real library.
_TMP = tempfile.mkdtemp(prefix='soulsync-testdb-stagingcache-')
os.environ['DATABASE_PATH'] = os.path.join(_TMP, 'staging_cache.db')
os.environ['SOULSYNC_TEST_DB_READY'] = '1'

web_server = pytest.importorskip('web_server')


def _write_flac(path, seconds, title):
    """A real FLAC header announcing ``seconds`` of 44.1 kHz stereo audio."""
    from mutagen.flac import FLAC

    sample_rate, channels, bits = 44100, 2, 16
    streaminfo = bytearray(34)
    streaminfo[0:2] = struct.pack(">H", 4096)
    streaminfo[2:4] = struct.pack(">H", 4096)
    streaminfo[10:18] = struct.pack(
        ">Q", (sample_rate << 44) | ((channels - 1) << 41) | ((bits - 1) << 36) | (sample_rate * seconds))
    with open(path, "wb") as f:
        f.write(b"fLaC" + bytes([0x80, 0x00, 0x00, 0x22]) + bytes(streaminfo))
    audio = FLAC(path)
    audio["TITLE"] = title
    audio.save()


def test_a_staged_file_keeps_its_length(tmp_path, monkeypatch):
    staging = tmp_path / "Staging"
    staging.mkdir()
    _write_flac(str(staging / "05 - 常夏の島.flac"), 55, "常夏の島")
    monkeypatch.setattr(web_server, "get_staging_path", lambda: str(staging))
    batch_id = "staging-cache-duration-test"
    try:
        (entry,) = web_server._get_staging_file_cache(batch_id)
    finally:
        web_server._staging_cache.pop(batch_id, None)

    assert entry["duration_ms"] == 55_000
    assert entry["track_number"] == 5
