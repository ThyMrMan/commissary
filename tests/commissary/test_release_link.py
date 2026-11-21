"""A manual-search release title links to its page at the source (legacy fork 1.8.6).

Upstream sends ``info_url`` (EXT.to needs it to resolve a magnet) but renders
the title as plain text. The fork adds ``source_url``: the same page, checked by
core/commissary/tracker_link.py, so an indexer download link or a key never
becomes an href. commissary-release-link.js turns it into the title's link.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

from database.video_database import VideoDatabase

ROOT = Path(__file__).resolve().parents[2]
DETAILS = "https://tracker.example/torrents.php?id=4242"
DOWNLOAD = "http://prowlarr:9696/3/download?apikey=SECRETKEY&link=abc"


@pytest.fixture
def profile(tmp_path):
    from core.video.quality_profile import load as load_profile
    return load_profile(VideoDatabase(database_path=str(tmp_path / "v.db")))


def _evaluate(profile, **hit):
    from api.video.downloads import _evaluate_hits
    base = {"title": "Heat.1995.1080p.BluRay.x264-GRP", "size_bytes": 8 * 1024 ** 3,
            "seeders": 20, "protocol": "torrent", "download_url": DOWNLOAD}
    base.update(hit)
    out = _evaluate_hits([base], profile, "movie", None, None, blocked=set(),
                         blocked_users=set(), want_title="Heat", want_year=1995)
    return out[0]


def test_a_details_page_becomes_the_source_url(profile):
    assert _evaluate(profile, info_url=DETAILS)["source_url"] == DETAILS


@pytest.mark.parametrize("info_url", [DOWNLOAD, "javascript:alert(1)",
                                      "https://t.example/details/1?passkey=X", None])
def test_an_unsafe_page_is_not_sent(profile, info_url):
    assert _evaluate(profile, info_url=info_url)["source_url"] is None


def test_the_script_is_loaded_and_hooked():
    html = (ROOT / "webui" / "index.html").read_text(encoding="utf-8")
    assert "commissary-release-link.js" in html
    view = (ROOT / "webui" / "static" / "video" / "video-download-view.js").read_text(encoding="utf-8")
    assert view.count("_relTitle(r) +") == 2


def _render(results):
    node = shutil.which("node")
    if not node:
        pytest.skip("node not installed")
    script = (ROOT / "webui" / "static" / "commissary-release-link.js").read_text(encoding="utf-8")
    harness = (
        "globalThis.document = {addEventListener(){}, createElement(){return {}},"
        " head: {appendChild(){}}};\n" + script +
        "\nprocess.stdout.write(JSON.stringify(" + json.dumps(results) +
        ".map(r => globalThis.CommissaryReleaseLink.titleHTML(r))));")
    out = subprocess.run([node, "-e", harness], capture_output=True, text=True, check=True)
    return json.loads(out.stdout)


def test_the_title_links_out_safely():
    linked, plain, bad = _render([
        {"title": "Heat <1995>", "source_url": DETAILS},
        {"title": "Heat", "source_url": None},
        {"title": "Heat", "source_url": "javascript:alert(1)"},
    ])
    assert linked.startswith("<a ") and 'href="https://tracker.example/torrents.php?id=4242"' in linked
    assert 'target="_blank" rel="noopener noreferrer"' in linked and "Heat &lt;1995&gt;" in linked
    assert plain.startswith("<span ") and "href" not in plain
    assert bad.startswith("<span ") and "javascript" not in bad
