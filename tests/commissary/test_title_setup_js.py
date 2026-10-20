"""The browser half: a title's search can be set up before it is owned."""

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
    res = subprocess.run([NODE, "tests/js/commissary_title_setup_harness.mjs"],
                         cwd=str(ROOT), capture_output=True, text=True, timeout=120)
    assert res.returncode == 0, f"harness failed:\n{res.stdout}\n{res.stderr}"
    assert "all assertions passed" in res.stdout


def test_the_detail_page_offers_and_dispatches_it():
    assert "filename='commissary-title-setup.js'" in _read("webui/index.html")
    detail = _read("webui/static/video/video-detail.js")
    assert "CommissaryTitleSetup.applies(d)" in detail
    assert "which === 'commissary-title-setup') CommissaryTitleSetup.open(data)" in detail
    assert "ensureStyles: ensureStyles" in _read("webui/static/video/video-manage-panel.js")


def test_the_panel_uses_upstreams_manage_classes():
    js = _read("webui/static/commissary-title-setup.js")
    for cls in ("vmg-overlay", "vmg-panel", "vmg-head", "vmg-body", "vmg-field",
                "vmg-input", "vmg-save", "vmg-close", "vmg-sect", "vmg-hint"):
        assert cls in js, cls
    assert "requestAnimationFrame(" not in js, "a hidden pane pauses rAF"
