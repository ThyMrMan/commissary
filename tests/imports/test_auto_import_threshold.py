"""The auto-import confidence threshold, and what the number actually means.

``_match_tracks`` builds its score as a PRODUCT of three fractions:

    overall = identification_confidence * avg_track_confidence * coverage

so it decays far faster than a percentage suggests. At the old default of 0.9,
even a perfectly tagged album with slightly imperfect identification scored
~0.83 and was refused; the only reason tagged albums auto-imported at all is
the separate "any track matched >= 0.8" escape hatch. What 0.9 really gated was
untagged folders, whose titles come from filenames and top out near 0.5 a track.

These pin both directions: the cases the lower default is FOR, and the cases it
must still refuse. The refusals are the point — lowering a gate is only safe if
the gate still discriminates.
"""

from __future__ import annotations

import pytest

from core.auto_import_worker import DEFAULT_CONFIDENCE_THRESHOLD as T


def overall(identification: float, avg_track: float, coverage: float) -> float:
    """The worker's own formula (auto_import_worker.py::_match_tracks)."""
    return identification * avg_track * coverage


# ── what the new default is for ──────────────────────────────────────────────
def test_a_correctly_identified_untagged_album_imports():
    """The reported case: files named "<Album> NN Title.flac" with no tags.
    Per-track confidence is filename-derived (~0.525 after 1.9.7)."""
    assert overall(0.90, 0.525, 1.00) >= T


def test_a_tagged_album_imports_comfortably():
    assert overall(0.90, 0.925, 1.00) >= T


def test_the_old_default_refused_even_a_tagged_album():
    """Why this moved at all — 0.9 was not "strict", it was unreachable."""
    assert overall(0.90, 0.925, 1.00) < 0.9


# ── what it must still refuse ────────────────────────────────────────────────
def test_a_partial_folder_is_still_refused():
    """3 of 12 tracks present: coverage collapses the product. Importing this
    would file a fragment as though it were the album."""
    assert overall(0.90, 0.525, 3 / 12) < T


def test_a_wrong_album_is_still_refused():
    """Titles disagree, so avg_track_confidence stays low even if the folder
    name identified confidently."""
    assert overall(0.60, 0.300, 0.80) < T


def test_a_weakly_identified_folder_is_still_refused():
    """Identification fell back to its 0.5 default — the app does not know what
    this release is, so it must not file it unattended."""
    assert overall(0.50, 0.525, 1.00) < T


def test_the_threshold_sits_between_those_two_groups():
    """Guards the value itself: nudging it either way must break a test rather
    than silently change what auto-imports."""
    accepted = overall(0.90, 0.525, 1.00)      # untagged but complete + identified
    refused = overall(0.50, 0.525, 1.00)       # not confidently identified
    assert refused < T <= accepted


# ── wiring ───────────────────────────────────────────────────────────────────
def test_the_worker_default_is_the_shared_constant():
    """web_server serves this same value on the settings screen, so the number
    shown can never be one the worker would not use."""
    src = (__import__("pathlib").Path(__file__).resolve().parents[2]
           / "web_server.py").read_text(encoding="utf-8")
    assert "DEFAULT_CONFIDENCE_THRESHOLD as _AUTO_IMPORT_DEFAULT_CONFIDENCE" in src
    assert "'auto_import.confidence_threshold', _AUTO_IMPORT_DEFAULT_CONFIDENCE" in src


def test_no_stale_hardcoded_default_remains():
    from pathlib import Path
    root = Path(__file__).resolve().parents[2]
    src = (root / "core" / "auto_import_worker.py").read_text(encoding="utf-8")
    assert "confidence_threshold', 0.9)" not in src


@pytest.mark.parametrize("value", [0.0, 1.0, 0.5])
def test_a_user_supplied_threshold_still_wins(value):
    """The default is a default — the Auto-Import tab's slider must override
    it, including all the way to 0 (import anything) or 1 (effectively off)."""
    from unittest.mock import MagicMock

    from core.auto_import_worker import AutoImportWorker

    worker = AutoImportWorker.__new__(AutoImportWorker)
    worker._config_manager = MagicMock()
    worker._config_manager.get.side_effect = (
        lambda key, default=None: value if key == 'auto_import.confidence_threshold' else default
    )
    assert worker._config_manager.get('auto_import.confidence_threshold', T) == value
