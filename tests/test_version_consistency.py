"""The release version lives in several places that must agree.

This exists because they silently drifted: releases 1.6.2 and 1.6.3 bumped
``database/__init__.py``'s ``__version__`` (which nothing reads) and left
``web_server.py``'s ``_SOULSYNC_BASE_VERSION`` — the one the sidebar button,
the update check and the backup metadata all use — sitting at 1.6.1.

The changelog silently broke with it: helper.js's ``_getLatestWhatsNewVersion``
only surfaces WHATS_NEW entries at or below the BUILD version, so with the
build reporting 1.6.1 a newer entry is filtered out entirely and the What's New
panel falls through to an undefined version. A stale constant is therefore not
cosmetic — it hides the release notes for every version after it.

Textual reads (no imports) so this stays cheap and can't be defeated by an
import side effect — the same source-guard convention the JS/HTML tests use.
"""

from __future__ import annotations

import re
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent

_SEMVER = r"(\d+\.\d+\.\d+)"


def _web_server_version() -> str:
    src = (_ROOT / "web_server.py").read_text(encoding="utf-8")
    m = re.search(r'^_SOULSYNC_BASE_VERSION\s*=\s*["\']' + _SEMVER + r'["\']',
                  src, re.M)
    assert m, "_SOULSYNC_BASE_VERSION not found in web_server.py"
    return m.group(1)


def _database_version() -> str:
    src = (_ROOT / "database" / "__init__.py").read_text(encoding="utf-8")
    m = re.search(r'^__version__\s*=\s*["\']' + _SEMVER + r'["\']', src, re.M)
    assert m, "__version__ not found in database/__init__.py"
    return m.group(1)


def _docker_default_tag() -> str:
    src = (_ROOT / ".github" / "workflows" / "docker-publish.yml").read_text(encoding="utf-8")
    m = re.search(r"default:\s*['\"]" + _SEMVER + r"['\"]", src)
    assert m, "version_tag default not found in docker-publish.yml"
    return m.group(1)


def _whats_new_versions() -> list:
    src = (_ROOT / "webui" / "static" / "helper.js").read_text(encoding="utf-8")
    block = src[src.index("const WHATS_NEW = {"):src.index("VERSION_MODAL_SECTIONS")]
    return re.findall(r"^\s*'" + _SEMVER + r"':\s*\[", block, re.M)


def test_app_version_and_package_version_agree():
    """database/__init__.py's __version__ is unused metadata, so nothing breaks
    when it drifts — which is exactly why it drifted. Pin it to the real one."""
    assert _database_version() == _web_server_version()


def test_docker_workflow_default_tag_matches_the_release():
    """The publish workflow's prefilled tag is what actually gets pushed when
    someone runs it without editing the field — a stale default overwrites
    :latest with an image labelled as an older release."""
    assert _docker_default_tag() == _web_server_version()


def test_changelog_has_an_entry_at_or_below_the_build_version():
    """helper.js filters WHATS_NEW to entries <= the build version. If every
    entry sits ABOVE it, the What's New panel resolves to an undefined version
    and shows nothing — the exact failure this file was written for."""
    build = tuple(int(p) for p in _web_server_version().split("."))
    versions = [tuple(int(p) for p in v.split(".")) for v in _whats_new_versions()]
    assert versions, "WHATS_NEW has no version blocks"
    assert any(v <= build for v in versions), (
        "every WHATS_NEW entry is newer than the build version %s — the What's "
        "New panel would show nothing" % _web_server_version())


def test_the_current_release_has_its_own_changelog_entry():
    """A release that ships without release notes is the drift this guards."""
    assert _web_server_version() in _whats_new_versions()
