"""The worker and the settings screen share one confidence default.

Each carried its own literal 0.9, so the value the settings screen showed could
drift from the one the worker used. See core/commissary/auto_import_defaults.py
for what the number means and why its value is still upstream's.
"""

from __future__ import annotations

import pytest
from flask import Flask

import api.auto_import as api_auto_import
import core.commissary.auto_import_defaults as defaults
from core.auto_import_worker import AutoImportWorker, FolderCandidate


class _NoKeys:
    """A config with nothing set: every read gets its default."""

    def get(self, key, default=None):
        return default

    def set(self, key, value):
        pass


@pytest.fixture
def moved_default(monkeypatch):
    """Move the shared default somewhere no literal could be, so a reader that
    does not use it shows."""
    monkeypatch.setattr(defaults, "DEFAULT_CONFIDENCE_THRESHOLD", 0.33)
    return 0.33


def test_the_settings_screen_shows_the_shared_default(moved_default, monkeypatch):
    app = Flask(__name__)
    app.register_blueprint(api_auto_import.bp)
    # monkeypatch, not configure(): configure() rebinds the module globals for
    # the rest of the run, so every later test of the real app's auto-import
    # routes would see "Auto-import not available".
    module_globals = vars(api_auto_import)
    monkeypatch.setitem(module_globals, "get_database", lambda: None)
    monkeypatch.setitem(module_globals, "config_manager", _NoKeys())
    monkeypatch.setitem(module_globals, "_auto_import_worker", lambda: None)

    payload = app.test_client().get("/api/auto-import/settings").get_json()

    assert payload["confidence_threshold"] == moved_default


def test_the_worker_imports_by_the_shared_default(moved_default, monkeypatch):
    """A 0.5 match with no strong track: over the moved default it imports,
    where a private 0.9 would have sent it to identification."""
    worker = AutoImportWorker(database=None, config_manager=_NoKeys())
    candidate = FolderCandidate(path="/s/A", name="A", audio_files=["/s/A/01.flac"],
                                folder_hash="h")
    monkeypatch.setattr(worker, "_resolve_rematch_hint", lambda c: (None, None))
    monkeypatch.setattr(worker, "_identify_folder", lambda c: {"album_id": "a", "album_name": "A"})
    monkeypatch.setattr(worker, "_match_tracks", lambda c, i: {
        "matches": [{"file": "/s/A/01.flac", "confidence": 0.6, "track": {}}],
        "unmatched_files": [], "confidence": 0.5, "matched_count": 1, "total_tracks": 1})
    monkeypatch.setattr(worker, "_consume_approval", lambda c: None)
    monkeypatch.setattr(worker, "_record_in_progress", lambda *a, **k: 1)
    monkeypatch.setattr(worker, "_finalize_result", lambda *a, **k: None)
    monkeypatch.setattr(worker, "_record_result", lambda *a, **k: None)
    monkeypatch.setattr(worker, "_emit_needs_attention", lambda *a, **k: None)
    monkeypatch.setattr(worker, "_clear_finished_folder", lambda c: None)
    processed = []
    monkeypatch.setattr(worker, "_process_matches",
                        lambda c, i, m: processed.append(c) or ("completed", []))

    worker._process_one_candidate(candidate)

    assert processed == [candidate]
