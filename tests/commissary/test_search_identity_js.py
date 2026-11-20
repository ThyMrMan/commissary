"""The browser half: searches carry which title they are for."""

import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
NODE = shutil.which("node") or shutil.which("node.exe")


def _read(rel):
    return (ROOT / rel).read_text(encoding="utf-8").replace("\r\n", "\n")


@pytest.mark.skipif(NODE is None, reason="node not available")
def test_behavioral_harness_passes():
    res = subprocess.run([NODE, "tests/js/commissary_search_identity_harness.mjs"],
                         cwd=str(ROOT), capture_output=True, text=True, timeout=120)
    assert res.returncode == 0, f"harness failed:\n{res.stdout}\n{res.stderr}"
    assert "all assertions passed" in res.stdout


def test_every_manual_search_path_sends_the_identity():
    assert "filename='commissary-search-identity.js'" in _read("webui/index.html")
    view = _read("webui/static/video/video-download-view.js")
    head = view.split("function searchInto(container, resultsEl, params, triggerRows, onDone) {")[1][:400]
    assert "CommissarySearchIdentity.apply(container, params)" in head
    grab = _read("webui/static/video/video-grab.js")
    # upstream 3.6.8 sends the identity itself (#2027): tmdb_id, which the
    # fork's Identity reads first (core/commissary/search_identity.tmdb_for)
    assert "if (opts.tmdbId != null) out.tmdb_id = opts.tmdbId;" in grab
    assert grab.count("identityParams(opts)") >= 2, "episode + season-pack searches"
