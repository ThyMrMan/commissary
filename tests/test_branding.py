"""The app is called Commissary; a handful of `soulsync` strings are DATA.

The rename from SoulSync to Commissary was deliberately scoped to prose. These
tests pin both halves of that decision, because the failure mode is a future
find-and-replace that looks harmless and silently breaks existing installs:

  * a renamed Subsonic client name registers a SECOND Navidrome player, whose
    "Report Real Path" setting nobody has enabled, breaking stream paths (#809)
  * a renamed docker volume points a running install at an EMPTY database
  * a renamed config env var or standalone server value orphans stored rows

Nothing here is about taste. Each assertion names a concrete breakage.
"""

import re
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]


def _read(rel):
    return (REPO / rel).read_text(encoding="utf-8")


# ── the user-visible name IS Commissary ─────────────────────────────────────

def test_page_title_and_app_name_are_commissary():
    index = _read("webui/index.html")
    assert "<title>Commissary" in index
    assert '<h1 class="app-name">Commissary</h1>' in index


def test_pwa_manifest_is_commissary():
    manifest = _read("webui/static/manifest.json")
    assert '"name": "Commissary"' in manifest
    assert '"short_name": "Commissary"' in manifest


def test_readme_says_it_is_a_fork_and_names_upstream():
    readme = _read("README.md")
    assert "fork of" in readme
    assert "Nezreka/SoulSync" in readme, "upstream must stay attributed and linkable"


# ── the Subsonic client name is NOT branding ────────────────────────────────

def test_subsonic_client_name_is_still_soulsync():
    from core.navidrome_client import SUBSONIC_CLIENT_NAME
    assert SUBSONIC_CLIENT_NAME == "SoulSync"


def test_every_subsonic_c_param_goes_through_the_constant():
    """Two files build Subsonic auth params. A literal in either one is how the
    two ends up disagreeing about which player row the request belongs to."""
    for rel in ("core/navidrome_client.py", "core/metadata/artwork.py"):
        text = _read(rel)
        for literal in ("'c': 'Commissary'", '"c": "Commissary"', "c=Commissary"):
            assert literal not in text, f"{rel} hardcodes the renamed client name"


def test_navidrome_help_text_names_the_player_the_user_will_actually_see():
    """The error message tells the user to pick a player out of a list. It has
    to name the one Navidrome shows them, not the app's new name."""
    text = _read("web_server.py")
    assert "Players → select SoulSync" in text


# ── deployment identifiers that must not move ───────────────────────────────

def test_docker_volume_name_is_unchanged():
    compose = _read("docker-compose.yml")
    assert "soulsync_database:/app/data" in compose
    assert re.search(r"^\s{2}soulsync_database:$", compose, re.M), "named volume declaration"


def test_config_env_var_is_unchanged():
    assert "SOULSYNC_CONFIG_PATH" in _read("web_server.py")
    assert "SOULSYNC_CONFIG_PATH" in _read("docker-compose.yml")


def test_standalone_server_value_is_unchanged():
    """`soulsync` is written into library rows as the server source and into
    `::soulsync` id suffixes. Renaming it orphans every standalone library."""
    text = _read("web_server.py")
    assert "'soulsync'" in text
    assert "::soulsync" in text


# ── the published image ─────────────────────────────────────────────────────

def test_compose_and_workflow_agree_on_the_image_name():
    assert "ghcr.io/thymrman/commissary:latest" in _read("docker-compose.yml")
    assert "/commissary" in _read(".github/workflows/docker-publish.yml")
