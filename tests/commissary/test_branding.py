"""The app shows as Commissary; a handful of ``soulsync`` strings are DATA.

Branding is deliberately limited to user-facing surfaces (see
core/commissary/brand.py). These tests pin both halves: the surfaces say
Commissary, and the identifiers that existing installs depend on did not move.
Each "unchanged" assertion names the concrete breakage a rename would cause.
"""

import re
import subprocess
from pathlib import Path

import pytest

from core.commissary import brand
from core.commissary.web import head_html, template_context

REPO = Path(__file__).resolve().parents[2]


def _read(rel):
    return (REPO / rel).read_text(encoding="utf-8")


# ── user-facing name ────────────────────────────────────────────────────────

def test_index_names_the_app_through_the_brand_hook():
    index = _read("webui/index.html")
    assert "<title>{{ app_name }}" in index
    assert '<h1 class="app-name">{{ app_name }}</h1>' in index
    assert "Sign in to {{ app_name }}" in index
    assert "{{ commissary_head }}" in index


def test_the_rendered_page_says_commissary():
    import web_server

    with web_server.app.test_request_context("/"):
        html = web_server.render_template("index.html")
    assert "<title>Commissary - " in html
    assert '<h1 class="app-name">Commissary</h1>' in html
    assert "window.COMMISSARY = " in html


def test_the_longer_name_fits_the_sidebar_brand_box():
    assert ".sidebar-brand .app-name { font-size: 17px; }" in str(head_html(lambda k, d=None: d))


def test_template_context_carries_the_name():
    assert template_context(lambda k, d=None: d)["app_name"] == "Commissary"


def test_head_script_cannot_be_closed_by_its_payload(monkeypatch):
    monkeypatch.setattr(brand, "APP_NAME", "</script><b>x")
    html = str(head_html(lambda k, d=None: d))
    assert "</script><b>" not in html


def test_pwa_manifest_is_commissary():
    manifest = _read("webui/static/manifest.json")
    assert '"name": "Commissary"' in manifest
    assert '"short_name": "Commissary"' in manifest


def test_readme_says_it_is_a_fork_and_names_upstream():
    head = _read("README.md")[:1500]
    assert "fork of" in head
    assert "Nezreka/SoulSync" in head, "upstream must stay attributed and linkable"


def test_third_parties_contact_the_fork_not_upstream():
    from core.musicbrainz_client import MusicBrainzClient

    assert MusicBrainzClient.DEFAULT_CONTACT == brand.REPO_URL
    assert brand.REPO_URL in _read("core/discogs_client.py")


# ── identifiers that must NOT be renamed ────────────────────────────────────

def test_subsonic_client_name_is_still_soulsync():
    """A renamed `c=` param registers a SECOND Navidrome player whose
    "Report Real Path" setting nobody enabled, breaking stream paths (#809)."""
    text = _read("core/navidrome_client.py")
    assert "'c': 'SoulSync'" in text
    assert "'c': 'Commissary'" not in text


def test_docker_volume_name_is_unchanged():
    """A renamed volume points a running install at an EMPTY database."""
    compose = _read("docker-compose.yml")
    assert "soulsync_database:/app/data" in compose
    assert re.search(r"^\s{2}soulsync_database:$", compose, re.M)


def test_config_env_var_is_unchanged():
    assert "SOULSYNC_CONFIG_PATH" in _read("core/settings.py")
    assert "SOULSYNC_CONFIG_PATH" in _read("docker-compose.yml")


def test_standalone_server_value_is_unchanged():
    """`soulsync` is stored as the server source and in `::soulsync` id
    suffixes; renaming it orphans every standalone library row."""
    assert "'soulsync'" in _read("web_server.py")
    assert '"::soulsync"' in _read("core/imports/side_effects.py")


# ── the published image ─────────────────────────────────────────────────────

def test_compose_workflow_and_unraid_template_agree_on_the_image():
    image = brand.IMAGE + ":latest"
    assert image in _read("docker-compose.yml")
    assert "/commissary" in _read(".github/workflows/commissary-publish.yml")
    assert f"<Repository>{image}</Repository>" in _read("templates/commissary.xml")


def test_upstream_publish_workflow_is_untouched_and_skips_here():
    """Upstream's workflow stays byte-for-byte theirs (no merge conflicts) and
    is guarded to their repo, so it never runs in ours."""
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
