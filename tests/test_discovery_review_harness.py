"""Behavioural coverage for the discovery Fix dialog's every-source search and its
review mode (webui/static/wishlist-tools.js, sync-services.js).

Which sources are asked, how their results are ranked, chipped and drawn, and what
a review's keys, clicks, Skip and "Not available" do are checked by RUNNING the real
function bodies, lifted out by ``tests/js/vanilla-extract.mjs``.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parent.parent
_HARNESS = _ROOT / "tests" / "js" / "discovery_review_harness.mjs"


def _node_available() -> bool:
    if not shutil.which("node"):
        return False
    try:
        out = subprocess.run(["node", "--version"], capture_output=True, text=True, timeout=15)
    except (OSError, subprocess.SubprocessError):
        return False
    return out.returncode == 0


@pytest.mark.skipif(not _node_available(), reason="node is not installed")
def test_the_discovery_review_logic():
    result = subprocess.run(["node", str(_HARNESS)], cwd=_ROOT, capture_output=True,
                            text=True, encoding="utf-8", timeout=120)
    assert result.returncode == 0, result.stdout + result.stderr
    assert "all assertions passed" in result.stdout
