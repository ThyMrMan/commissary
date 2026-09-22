"""Behavioural coverage for mediaRef() in webui/static/video/video-download-view.js.

Which (id, source) pair a search payload carries is checked by RUNNING the real
function body against the state shapes the page builds -- a source pin cannot
tell an id paired with the right source from one paired with the wrong one, which
is exactly how a TMDB id went out labelled 'library' for every show search.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parent.parent
_HARNESS = _ROOT / "tests" / "js" / "video_media_ref_harness.mjs"


def _node_available() -> bool:
    if not shutil.which("node"):
        return False
    try:
        out = subprocess.run(["node", "--version"], capture_output=True, text=True, timeout=15)
    except (OSError, subprocess.SubprocessError):
        return False
    return out.returncode == 0


@pytest.mark.skipif(not _node_available(), reason="node is not installed")
def test_the_search_payloads_media_identity():
    result = subprocess.run(["node", str(_HARNESS)], cwd=_ROOT, capture_output=True,
                            text=True, encoding="utf-8", timeout=120)
    assert result.returncode == 0, result.stdout + result.stderr
    assert "all assertions passed" in result.stdout
