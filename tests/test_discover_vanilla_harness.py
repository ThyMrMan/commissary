"""Behavioural coverage for webui/static/discover.js.

discover.js is 12k lines and, until now, everything asserted about it was a
source-text pin — ``assert "someString" in discover.js``. A pin cannot see a
return value and cannot see whether a branch is reachable: two pins in this repo
passed with the feature they described switched off, and were only caught by
backing the feature out and noticing nothing failed.

``tests/js/vanilla-extract.mjs`` (ported from upstream SoulSync 3.3.1) lifts a
real function body out of the page by brace-matching that understands strings,
regex literals and nested template literals, so a single function can be run
without evaluating 12k lines of browser-coupled code. The harness beside it
calls those functions.

The first subject is ``_normalizeTrack``, and deliberately so: the 2.3.1
Discover port depends on it. The ported Daily Mixes emit Spotify-shaped tracks,
and the claim that this page could already render them shipped as three
substring assertions in tests/discovery/test_discover_backend_port.py. Now it is
checked by running it.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parent.parent
_HARNESS = _ROOT / "tests" / "js" / "discover_vanilla_harness.mjs"
_EXTRACT = _ROOT / "tests" / "js" / "vanilla-extract.mjs"


def _node_available() -> bool:
    if not shutil.which("node"):
        return False
    try:
        out = subprocess.run(["node", "--version"], capture_output=True,
                             text=True, timeout=15)
        return int(out.stdout.strip().lstrip("v").split(".")[0]) >= 18
    except Exception:
        return False


@pytest.mark.skipif(not _node_available(), reason="node >= 18 not available")
def test_discover_vanilla_behaviour():
    """Runs the real bodies. See the harness for what each case protects."""
    result = subprocess.run(
        ["node", str(_HARNESS)], capture_output=True, text=True, timeout=60,
        cwd=str(_ROOT),
    )
    assert result.returncode == 0, (
        f"discover.js vanilla harness failed:\n{result.stdout}\n{result.stderr}"
    )


def test_the_extractor_is_available_to_other_harnesses():
    """It is infrastructure, not a one-off: any future test wanting to run a
    real discover.js function imports this rather than writing another naive
    `indexOf('\\n}')` matcher, which breaks on nested braces."""
    src = _EXTRACT.read_text(encoding="utf-8")
    assert "export function extractFunction(" in src
    assert "export function loadVanilla(" in src
    # The three cases a naive matcher gets wrong, each handled deliberately.
    assert "REGEX_PRECEDERS" in src, "a regex literal must not desync the matcher"
    assert "tmplStack" in src, "nested template literals must return to the right template"
    assert "parens" in src, "the parameter list must be walked before the body brace"


def test_it_reads_the_live_page_not_a_copy():
    """Upstream freezes a fixture because it deleted its vanilla Discover. This
    fork still ships discover.js, so the harness must read the file that
    actually runs — a stale copy would pass while the page was broken."""
    src = _EXTRACT.read_text(encoding="utf-8")
    assert "'webui', 'static', 'discover.js'" in src
    assert "__fixtures__" not in src
