"""A manual import that failed must not report itself as a success.

``post_process_matched_download`` catches every exception, logs it, re-queues
the file and RETURNS NORMALLY so the download monitor can retry. The manual
import routes have no monitor: they read ``import_rejection_reason`` and took
a clean return as success, so a PermissionError creating the album folder was
reported as "imported" while the file never left the download folder.

Ported from the legacy fork (Release 1.9.4).
"""

from __future__ import annotations

import types

import core.imports.pipeline as import_pipeline
from core.imports.pipeline import import_rejection_reason
from core.imports.routes import ImportRouteRuntime, album_process
from tests.imports.test_import_pipeline import _wire_post_process_common
from tests.imports.test_import_routes import _FakeLogger, _touch


def _runtime():
    return types.SimpleNamespace(automation_engine=None, on_download_completed=None,
                                 web_scan_manager=None, repair_worker=None)


def _context():
    return {"track_info": {}, "original_search_result": {"title": "Track", "album": "Album"}}


# ── the pipeline records it ──────────────────────────────────────────────────
def test_post_process_records_an_unexpected_failure_on_the_context(tmp_path, monkeypatch):
    """Reproduced where it really happened: building the final path creates the
    destination folders, so that is what raises."""
    source_path = tmp_path / "source.flac"
    source_path.write_bytes(b"audio")
    _wire_post_process_common(monkeypatch, tmp_path, tmp_path / "out.flac",
                              track_number=1, is_album_download=False)

    def _denied(*_a, **_kw):
        raise PermissionError(13, "Permission denied", "/media/completed/listening/music/IVE")

    monkeypatch.setattr(import_pipeline, "build_final_path_for_track", _denied)
    context = _context()

    # Must NOT raise -- the download monitor's retry depends on returning normally.
    import_pipeline.post_process_matched_download("ctx-perm", context, str(source_path), _runtime())

    assert "Permission denied" in (context.get("_post_process_error") or "")
    assert "Permission denied" in (import_rejection_reason(context) or "")


def test_a_clean_post_process_records_no_failure(tmp_path, monkeypatch):
    source_path = tmp_path / "source.flac"
    source_path.write_bytes(b"audio")
    _wire_post_process_common(monkeypatch, tmp_path, tmp_path / "out.flac",
                              track_number=1, is_album_download=False)
    context = _context()

    import_pipeline.post_process_matched_download("ctx-ok", context, str(source_path), _runtime())

    assert context.get("_post_process_error") is None
    assert import_rejection_reason(context) is None


# ── the reason ───────────────────────────────────────────────────────────────
def test_a_post_process_exception_is_a_rejection():
    reason = import_rejection_reason({
        "_post_process_error": "[Errno 13] Permission denied: '/media/music/IVE'"})
    assert reason is not None
    assert "Permission denied" in reason and "/media/music/IVE" in reason


def test_a_specific_rejection_outranks_the_generic_error():
    reason = import_rejection_reason({
        "_acoustid_quarantined": True,
        "_acoustid_failure_msg": "fingerprint mismatch",
        "_post_process_error": "disk full",
    })
    assert "AcoustID" in reason
    assert "disk full" not in reason


# ── what the user sees ───────────────────────────────────────────────────────
def test_an_album_import_whose_post_process_failed_is_not_counted(tmp_path):
    f = tmp_path / "11.flac"
    _touch(f)
    runtime = ImportRouteRuntime(
        get_allowed_import_roots=lambda: [str(tmp_path)],
        resolve_album_artist_context=lambda album, source="": {"name": "IVE"},
        build_album_import_context=lambda album, track, **kwargs: {"album": album, "track": track},
        post_process_matched_download=lambda key, context, path: context.update(
            _post_process_error="[Errno 13] Permission denied: '/media/music/IVE'"),
        is_active_media_server_ready=lambda: (True, ""),
        logger=_FakeLogger(),
    )

    payload, status = album_process(runtime, {
        "album": {"id": "a1", "name": "I'VE IVE", "artist": "IVE"},
        "matches": [{
            "staging_file": {"full_path": str(f), "filename": "11.flac"},
            "track": {"name": "Shine With Me", "track_number": 11},
        }],
    })

    assert payload["processed"] == 0, payload
    assert any("Permission denied" in str(e) for e in payload.get("errors", [])), payload


def test_a_retry_that_succeeds_drops_the_last_attempt_s_error(tmp_path, monkeypatch):
    """The download monitor re-queues the SAME context dict for its retry, so an
    error from the attempt before must not outlive a retry that worked."""
    source_path = tmp_path / "source.flac"
    source_path.write_bytes(b"audio")
    _wire_post_process_common(monkeypatch, tmp_path, tmp_path / "out.flac",
                              track_number=1, is_album_download=False)
    context = _context()
    context["_post_process_error"] = "disk full"

    import_pipeline.post_process_matched_download("ctx-retry", context, str(source_path), _runtime())

    assert import_rejection_reason(context) is None
