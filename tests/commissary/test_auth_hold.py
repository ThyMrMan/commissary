"""Boot-time API calls wait for the sign-in verdict instead of being refused.

With login (or the launch PIN) on, ~15 modules fire a fetch the instant the
page loads and every one was refused with a 401. The behavioural contract is
in tests/js/commissary_auth_hold_harness.mjs; this runs it with the server's
real open-endpoint list and pins the wiring.
"""

import json
import shutil
import subprocess
from pathlib import Path

import pytest

from core.commissary.web import auth_open_paths, head_html

ROOT = Path(__file__).resolve().parents[2]
HTML = (ROOT / "webui" / "index.html").read_text(encoding="utf-8")
NODE = shutil.which("node") or shutil.which("node.exe")


@pytest.mark.skipif(NODE is None, reason="node not available")
def test_behavioral_harness_passes():
    res = subprocess.run(
        [NODE, "tests/js/commissary_auth_hold_harness.mjs", json.dumps(auth_open_paths())],
        cwd=str(ROOT), capture_output=True, text=True, timeout=120)
    assert res.returncode == 0, f"harness failed:\n{res.stdout}\n{res.stderr}"
    assert "all assertions passed" in res.stdout


def test_it_loads_after_the_dedupe_wrapper_so_it_is_the_outer_one():
    """A held call must never reach fetch-dedupe's cache, which would memoise
    the synthetic 401 and starve the real request after unlock."""
    dedupe = HTML.index("filename='fetch-dedupe.js'")
    hold = HTML.index("filename='commissary-auth-hold.js'")
    assert dedupe < hold
    for later in ("vite_assets('body')", "filename='core.js'", "filename='init.js'"):
        assert hold < HTML.index(later), f"must wrap fetch before {later} runs"


def test_the_open_list_is_upstreams_own_gate_lists():
    """Built from core/security at render time, so it cannot drift."""
    from core.security import launch_lock, login_gate, session_profile

    paths = auth_open_paths()
    for gate in (login_gate, launch_lock, session_profile):
        assert set(gate._ALLOWED_GET) <= set(paths["get"])
        assert set(gate._ALLOWED_POST) <= set(paths["post"])
    assert "/api/profiles/current" in paths["get"], "holding it would deadlock"


def test_the_page_hands_the_list_to_the_script():
    assert '"authOpen": {"get": [' in str(head_html(lambda k, d=None: d))


def test_head_hook_runs_before_the_script():
    assert HTML.index("{{ commissary_head }}") < HTML.index("commissary-auth-hold.js")
