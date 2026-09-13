"""Behavioural coverage for the Re-identify Album modal in webui/static/library.js.

The modal decides which confirmed pairings are sent to the server and what the
user is told after a run. Both are checked by RUNNING the real function bodies,
lifted out by ``tests/js/vanilla-extract.mjs`` -- a source pin could not tell an
unticked row being skipped from one being sent.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parent.parent
_HARNESS = _ROOT / "tests" / "js" / "album_reidentify_harness.mjs"


def _node_available() -> bool:
    if not shutil.which("node"):
        return False
    try:
        out = subprocess.run(["node", "--version"], capture_output=True, text=True, timeout=15)
    except (OSError, subprocess.SubprocessError):
        return False
    return out.returncode == 0


@pytest.mark.skipif(not _node_available(), reason="node is not installed")
def test_the_album_reidentify_modal_logic():
    result = subprocess.run(["node", str(_HARNESS)], cwd=_ROOT, capture_output=True,
                            text=True, timeout=120)
    assert result.returncode == 0, result.stdout + result.stderr
    assert "all assertions passed" in result.stdout
