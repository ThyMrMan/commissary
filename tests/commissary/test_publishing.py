"""The fork publishes its own image from its own repo."""

import re
import subprocess
from pathlib import Path

import pytest

from core.commissary import brand

REPO = Path(__file__).resolve().parents[2]


def _read(rel):
    return (REPO / rel).read_text(encoding="utf-8")


def test_compose_runs_the_image_the_workflow_publishes():
    assert brand.IMAGE + ":latest" in _read("docker-compose.yml")
    assert "/commissary" in _read(".github/workflows/commissary-publish.yml")


def test_upstream_publish_workflow_is_untouched_and_skips_here():
    """Upstream's workflow stays theirs (no merge conflicts) and is guarded to
    their repo, so it never runs in ours."""
    assert "if: github.repository == 'Nezreka/SoulSync'" in _read(".github/workflows/docker-publish.yml")


def test_publish_guard_matches_the_repo_origin_points_at():
    """A guard naming the wrong repo makes the publish job SKIP, which shows as
    a green run that published nothing (how the legacy 2.0.0 publish failed)."""
    try:
        url = subprocess.run(
            ["git", "remote", "get-url", "origin"],
            capture_output=True, text=True, timeout=10, cwd=REPO, check=True,
        ).stdout.strip()
    except (OSError, subprocess.SubprocessError):
        pytest.skip("no git/origin available")
    m = re.search(r"github\.com[:/]([^/]+/[^/\s]+?)(?:\.git)?$", url)
    if not m:
        pytest.skip(f"origin is not a GitHub URL: {url}")
    assert m.group(1).lower() == brand.GITHUB_REPO.lower()
    assert f"if: github.repository == '{brand.GITHUB_REPO}'" in _read(
        ".github/workflows/commissary-publish.yml")


def test_publish_workflow_reads_the_version_the_app_reports():
    """The workflow derives the tag from web_server.py with a sed pattern; if
    the constant's line shape changes, the publish must not silently tag ''."""
    workflow = _read(".github/workflows/commissary-publish.yml")
    pattern = re.search(r"sed -n 's/(.+?)/\\1/p' web_server\.py", workflow)
    assert pattern, "version sed expression not found"
    regex = pattern.group(1).replace("\\(", "(").replace("\\)", ")")
    hits = [m.group(1) for line in _read("web_server.py").splitlines()
            if (m := re.fullmatch(regex, line))]
    assert len(hits) == 1
    assert re.fullmatch(r"\d+\.\d+\.\d+-c\.\d+", hits[0])


def test_the_page_carries_the_feature_hook():
    import web_server

    assert "{{ commissary_head }}" in _read("webui/index.html")
    with web_server.app.test_request_context("/"):
        html = web_server.render_template("index.html")
    assert "window.COMMISSARY = " in html
    assert "<title>SoulSync - " in html
