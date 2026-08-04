"""The Music Library write probe.

A live install imported a whole album into a folder the container could not
write to. Every track raised ``PermissionError`` on the artist folder, and
nothing surfaced in the UI — the destination simply stayed empty. The probe
exists so that state is visible in Settings before an import discovers it.
"""

from __future__ import annotations

import os
import sys

import pytest

from core.imports.destinations import probe_destination_writable


def test_a_writable_folder_reports_ok(tmp_path):
    result = probe_destination_writable(str(tmp_path))
    assert result["writable"] is True
    assert result["status"] == "ok"


def test_the_probe_leaves_nothing_behind(tmp_path):
    """A stray dot-folder appearing in someone's music library would be a bug
    report of its own."""
    before = set(os.listdir(tmp_path))
    probe_destination_writable(str(tmp_path))
    assert set(os.listdir(tmp_path)) == before


def test_a_missing_folder_is_distinguished_from_an_unwritable_one(tmp_path):
    """Different problems, different fixes: one is a typo or an unmapped
    volume, the other is ownership. Collapsing them into "broken" would send
    the user looking in the wrong place."""
    result = probe_destination_writable(str(tmp_path / "not-here"))
    assert result["writable"] is False
    assert result["status"] == "missing"


def test_a_file_is_not_a_destination(tmp_path):
    f = tmp_path / "a-file.txt"
    f.write_text("x")
    result = probe_destination_writable(str(f))
    assert result["writable"] is False
    assert result["status"] == "not_a_directory"


def test_an_empty_path_is_reported_not_probed():
    result = probe_destination_writable("")
    assert result["writable"] is False
    assert result["status"] == "unset"
    assert probe_destination_writable(None)["status"] == "unset"


@pytest.mark.skipif(sys.platform == "win32",
                    reason="POSIX mode bits do not restrict directory creation on Windows")
@pytest.mark.skipif(os.getuid() == 0 if hasattr(os, "getuid") else False,
                    reason="root ignores the permission bits this asserts on")
def test_a_read_only_folder_reports_unwritable(tmp_path):
    """The reported failure, reproduced: the folder exists and is readable,
    and creating a subdirectory in it is denied."""
    locked = tmp_path / "music"
    locked.mkdir()
    os.chmod(locked, 0o555)
    try:
        result = probe_destination_writable(str(locked))
        assert result["writable"] is False
        assert result["status"] == "unwritable"
        # The message has to point at the actual remedy.
        assert "PUID" in result["detail"] or "denied" in result["detail"].lower()
    finally:
        os.chmod(locked, 0o755)


def test_the_probe_creates_a_directory_not_a_file(tmp_path, monkeypatch):
    """A share can permit file creation while denying mkdir, and mkdir of the
    artist folder is what actually failed — so probing with a file would have
    returned "writable" for the very install that reported this."""
    made = []
    import tempfile as _tempfile
    real = _tempfile.mkdtemp
    monkeypatch.setattr(_tempfile, "mkdtemp",
                        lambda *a, **kw: made.append(kw.get("dir")) or real(*a, **kw))

    probe_destination_writable(str(tmp_path))

    assert made == [str(tmp_path)]
