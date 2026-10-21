"""A failed row in the download window gets a visible Sources button (fork 1.9.8).

The JS lives in webui/static/commissary-track-inspector.js and is checked by
sources_button.mjs; this also checks downloads.js still calls it.
"""
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
_JS = Path(__file__).with_name('sources_button.mjs')


def test_sources_button_js():
    node = shutil.which('node') or shutil.which('node.exe')
    if not node:
        pytest.skip('node not installed')
    r = subprocess.run([node, '--test', str(_JS)], capture_output=True, text=True, timeout=60, cwd=str(ROOT))
    assert r.returncode == 0, r.stdout + r.stderr


def test_downloads_js_renders_it_in_the_actions_cell():
    source = (ROOT / 'webui' / 'static' / 'downloads.js').read_text(encoding='utf-8')
    assert "actionsEl.innerHTML = window.commissarySourcesButton(task, isQuarantinedTask) || '-';" in source
