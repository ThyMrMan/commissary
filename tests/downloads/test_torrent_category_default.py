"""The configured torrent/usenet category must actually be used.

Reported from a live install: Torrent Client Settings had the category set to
"Music", and every music download still arrived in the client tagged
"soulsync".

The adapters were always right — each resolves ``category or self._category``,
where ``self._category`` is ``torrent_client.category`` from Settings. The bug
was that every layer above them declared ``category: str = "soulsync"``, and a
truthy literal means the ``or`` can never reach the configured value. The video
side was unaffected only because it happens to pass a category explicitly.

So the property worth pinning is not "the default is None" but "an unspecified
category defers to configuration". These assert it at the signature level,
because that is where it broke and where it would break again.
"""

from __future__ import annotations

import inspect
import re
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parents[2]

_CLIENT_FILES = [
    _ROOT / "core" / "torrent_clients" / "aria2.py",
    _ROOT / "core" / "torrent_clients" / "base.py",
    _ROOT / "core" / "torrent_clients" / "deluge.py",
    _ROOT / "core" / "torrent_clients" / "qbittorrent.py",
    _ROOT / "core" / "torrent_clients" / "transmission.py",
    _ROOT / "core" / "usenet_clients" / "base.py",
    _ROOT / "core" / "usenet_clients" / "nzbget.py",
    _ROOT / "core" / "usenet_clients" / "sabnzbd.py",
]


@pytest.mark.parametrize("path", _CLIENT_FILES, ids=lambda p: p.name)
def test_no_hardcoded_category_default(path):
    """A literal default outranks the user's setting, because every adapter
    resolves `category or self._category` and a literal is always truthy."""
    src = path.read_text(encoding="utf-8")
    offenders = re.findall(r'category:\s*str\s*=\s*["\'][^"\']+["\']', src)
    assert not offenders, (
        f"{path.name} declares a literal category default {offenders} — "
        "it would override torrent_client.category / usenet_client.category"
    )


def test_add_torrent_smart_defers_to_the_client():
    from core.torrent_clients.base import add_torrent_smart
    assert inspect.signature(add_torrent_smart).parameters["category"].default is None


def test_the_music_plugin_does_not_pass_a_category():
    """Music deliberately sends no category so the configured one applies.
    If this ever needs to pass one, it must come from config — not a literal,
    which is exactly how the original bug was written."""
    src = (_ROOT / "core" / "download_plugins" / "torrent.py").read_text(encoding="utf-8")
    for call in re.findall(r"add_torrent_smart\((.*?)\)", src, re.S):
        assert "category=" not in call or "config_manager" in call, call


def test_the_video_path_still_passes_its_own_category():
    """Video Libraries have per-library categories; that override has to keep
    working, and is the reason video never saw this bug."""
    src = (_ROOT / "core" / "video" / "client_grab.py").read_text(encoding="utf-8")
    assert "category=cat" in src


@pytest.mark.parametrize("module,attr", [
    ("core.torrent_clients.qbittorrent", "QBittorrentAdapter"),
    ("core.torrent_clients.transmission", "TransmissionAdapter"),
    ("core.torrent_clients.deluge", "DelugeAdapter"),
])
def test_adapters_still_fall_back_to_their_configured_category(module, attr):
    """The fallback is what makes `None` mean "use my setting". Without it,
    the fix above would send no category at all."""
    src = Path(_ROOT / module.replace(".", "/")).with_suffix(".py").read_text(encoding="utf-8")
    assert "category or self._category" in src, module
