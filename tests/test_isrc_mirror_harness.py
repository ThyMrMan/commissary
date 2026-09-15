"""Behavioural coverage for carrying a Qobuz track's ISRC into its mirrored playlist
(webui/static/sync-services.js loadQobuzPlaylists, stats-automations.js mirrorPlaylist).

Both rebuild the track field by field before it is saved, so they are checked by
RUNNING the real function bodies, lifted out by ``tests/js/vanilla-extract.mjs``.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parent.parent
_HARNESS = _ROOT / "tests" / "js" / "isrc_mirror_harness.mjs"


def _node_available() -> bool:
    if not shutil.which("node"):
        return False
    try:
        out = subprocess.run(["node", "--version"], capture_output=True, text=True, timeout=15)
    except (OSError, subprocess.SubprocessError):
        return False
    return out.returncode == 0


@pytest.mark.skipif(not _node_available(), reason="node is not installed")
def test_the_isrc_reaches_the_mirror():
    result = subprocess.run(["node", str(_HARNESS)], cwd=_ROOT, capture_output=True,
                            text=True, encoding="utf-8", timeout=120)
    assert result.returncode == 0, result.stdout + result.stderr
    assert "all assertions passed" in result.stdout
