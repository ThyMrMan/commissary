"""Behavioural coverage for what a discovery row's actions change beyond the row
(webui/static/wishlist-tools.js, sync-services.js, stats-automations.js).

Which route an unmatch goes to and what it names, the match counts a Fix, an unmatch
or "Not available" leaves on the modal and the playlist card, the filter chips' counts,
and the Wing It Pool's every-source search are checked by RUNNING the real function
bodies, lifted out by ``tests/js/vanilla-extract.mjs``.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parent.parent
_HARNESS = _ROOT / "tests" / "js" / "discovery_row_actions_harness.mjs"


def _node_available() -> bool:
    if not shutil.which("node"):
        return False
    try:
        out = subprocess.run(["node", "--version"], capture_output=True, text=True, timeout=15)
    except (OSError, subprocess.SubprocessError):
        return False
    return out.returncode == 0


@pytest.mark.skipif(not _node_available(), reason="node is not installed")
def test_the_discovery_row_actions_logic():
    result = subprocess.run(["node", str(_HARNESS)], cwd=_ROOT, capture_output=True,
                            text=True, encoding="utf-8", timeout=120)
    assert result.returncode == 0, result.stdout + result.stderr
    assert "all assertions passed" in result.stdout
