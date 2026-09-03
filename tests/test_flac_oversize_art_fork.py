"""The oversized-FLAC-cover fix, wired into THIS fork.

``tests/test_flac_oversize_art.py`` beside this file is upstream SoulSync
3.3.1's own suite, taken verbatim — it passing unchanged is the evidence the two
halves of the fix behave the same here. This file covers what upstream's does
not: a third bug that arrived in the same hunk, and a fork divergence a careless
port would have trampled silently.

The bug being fixed, from upstream's report:

    Album art successfully embedded.
    [Atomic Save] atomic path failed (block is too long to write) — in-place fallback
    Error enhancing metadata ...: block is too long to write

A FLAC metadata block carries a 24-bit length, so nothing over 16,777,215 bytes
can be written. mutagen only discovers that at SAVE time. The atomic path failed
safely against a copy — and then the in-place fallback ran the same impossible
write against the user's real file, leaving the track with neither art nor tags.
"""

from __future__ import annotations

import inspect
import pathlib
import re

_ROOT = pathlib.Path(__file__).resolve().parent.parent


# ── the fork divergence upstream's hunk would have overwritten ─────────────

def test_the_settings_import_was_not_taken_from_upstream():
    """Upstream's same hunk changes `from config.settings` to
    `from core.settings`. That module does NOT exist in this fork — and
    get_config_manager() swallows the ImportError, so taking the line would not
    crash. It would silently hand back None instead of the config manager,
    which is the worst of both outcomes."""
    import core.metadata.common as mod
    src = inspect.getsource(mod)
    assert "from config.settings import config_manager" in src
    assert "from core.settings import" not in src

    # And the module it names must actually be the one that exists here.
    import importlib
    importlib.import_module("config.settings")
    assert not (_ROOT / "core" / "settings.py").exists(), (
        "core/settings.py now exists — re-check which module this fork's "
        "config_manager should come from before trusting this test")


def test_get_config_manager_still_returns_something(monkeypatch):
    """The behavioural half of the above: whatever the import says, the
    function has to hand back a real config manager rather than None."""
    from core.metadata.common import get_config_manager
    assert get_config_manager() is not None


# ── the third bug in the same hunk, which the release notes never mention ──

def test_the_atomic_temp_file_name_is_unique_per_writer():
    """A FIXED `.sstmp` name is only safe if writers to one file are
    serialised, and they are not: a bulk-fix thread and a scan's own auto-fix
    can reach the same file at once. The loser's copy2/replace then failed,
    dropped into the generic except, and fell back to the non-atomic in-place
    save this whole function exists to replace.

    Upstream fixed it in the same hunk as the FLAC cover, and their tests do
    not cover it."""
    import core.metadata.common as mod
    src = inspect.getsource(mod.save_audio_file)
    assert 'f"{path}.sstmp"' not in src, "the fixed temp name is back"
    tmp_line = next(l for l in src.split("\n") if "sstmp" in l and "tmp =" in l)
    # Process, thread and a random component: two writers in one process on one
    # file are the actual reported case, so the thread id alone is not enough
    # either — two sweeps can reuse a thread from a pool.
    for part in ("os.getpid()", "threading.get_ident()", "uuid.uuid4()"):
        assert part in tmp_line, f"temp name lacks {part}: {tmp_line.strip()}"


def test_two_temp_names_for_the_same_path_differ():
    """The assertion the source pin cannot make."""
    import os
    import threading
    import uuid

    def name(path):
        return f"{path}.{os.getpid()}.{threading.get_ident()}.{uuid.uuid4().hex[:8]}.sstmp"

    a, b = name("/music/x.flac"), name("/music/x.flac")
    assert a != b


def test_uuid_is_imported_where_the_temp_name_needs_it():
    import core.metadata.common as mod
    assert hasattr(mod, "uuid"), "save_audio_file would NameError on its first call"


# ── the fallback still exists for the failures it was written for ──────────

def test_re_encoding_is_tried_before_throwing_pixels_away():
    """Upstream's stated order: quality first, then dimensions — "a 4000px cover
    re-encoded at quality 85 is usually well under the limit already, and
    halving the pixels is a bigger loss than dropping a little JPEG quality."

    Deleting the quality loop does not FAIL: the dimension loop still gets the
    image under the cap, so a back-out of it passed every other test here. What
    is lost is the cover's resolution, silently. This pins the order by checking
    the picture that comes back is still full size."""
    import io as _io
    import os

    from PIL import Image

    from core.metadata.artwork import FLAC_MAX_PICTURE_BYTES, _shrink_for_flac

    # Incompressible noise as PNG: genuinely oversized, but a JPEG re-encode of
    # these dimensions lands comfortably under the cap.
    side = 2600
    buf = _io.BytesIO()
    Image.frombytes("RGB", (side, side), os.urandom(side * side * 3)).save(buf, format="PNG")
    raw = buf.getvalue()
    assert len(raw) > FLAC_MAX_PICTURE_BYTES, "fixture is not actually oversized"

    out = _shrink_for_flac(raw, "image/png")
    assert out is not None and len(out) <= FLAC_MAX_PICTURE_BYTES
    with Image.open(_io.BytesIO(out)) as got:
        assert got.size == (side, side), (
            "the cover was downscaled to %s when re-encoding alone would have "
            "fit it — the quality-first step is gone" % (got.size,))


def test_each_half_of_the_classifier_stands_alone():
    """`_is_tag_write_error` is an OR of two independent checks: the mutagen
    exception TYPE, and the message text (mutagen.flac.error subclasses IOError
    on some versions rather than MutagenError, so neither alone is enough).

    Upstream's tests use messages containing "too long to write" for BOTH
    cases, so disabling either branch still passed — each was covering for the
    other. These exercise one branch at a time."""
    import mutagen
    from core.metadata.common import _is_tag_write_error

    # Type only: a mutagen error whose message says nothing recognisable.
    assert _is_tag_write_error(mutagen.MutagenError("something opaque")) is True

    # Message only: not a MutagenError at all, which is the IOError-subclass case.
    class _NotMutagen(Exception):
        pass

    assert _is_tag_write_error(_NotMutagen("block is too long to write")) is True


def test_a_disk_error_still_falls_back_in_place():
    """The split must not turn every atomic failure into a refusal. A copy or
    permission problem is exactly the case the in-place fallback exists for,
    and OSError is deliberately excluded from the tag-write classifier."""
    from core.metadata.common import _is_tag_write_error
    assert _is_tag_write_error(OSError("[Errno 28] No space left on device")) is False
    assert _is_tag_write_error(PermissionError("denied")) is False

    import core.metadata.common as mod
    src = inspect.getsource(mod.save_audio_file)
    # The fallback path is still reachable below the refusal.
    assert "in-place fallback" in src
    assert src.index("_is_tag_write_error(atomic_err)") < src.index("in-place fallback")


def test_the_refusal_says_the_original_was_left_alone():
    """A user reading this line needs to know their file is intact — the whole
    point of the change is that nothing was written to it."""
    import core.metadata.common as mod
    src = inspect.getsource(mod.save_audio_file)
    refusal = src.split("_is_tag_write_error(atomic_err)", 1)[1][:400]
    assert "left untouched" in refusal
    assert "tags NOT written" in refusal


# ── the guard sits where the oversized data actually arrives ───────────────

def test_the_size_check_runs_before_anything_is_handed_to_mutagen():
    """Ordering is the fix. Checking after the tag object has been built would
    be checking after the damage is already queued."""
    import core.metadata.artwork as mod
    src = inspect.getsource(mod.embed_album_art_metadata)
    assert "FLAC_MAX_PICTURE_BYTES" in src
    assert src.index("FLAC_MAX_PICTURE_BYTES") < src.index("symbols.APIC("), (
        "the size guard must precede the tag construction")


def test_the_size_check_is_reachable_not_merely_present():
    """Upstream's wiring test asserts the NAMES appear in the AST, which a
    `if False:` satisfies — the constant is still referenced inside the dead
    branch's log line. Backing the whole guard out that way passed every test
    in both files. This walks to the `if` that actually gates it and refuses a
    constant test."""
    import ast
    import core.metadata.artwork as mod

    tree = ast.parse(inspect.getsource(mod))
    fn = next(n for n in ast.walk(tree)
              if isinstance(n, ast.FunctionDef) and n.name == "embed_album_art_metadata")

    gates = [
        node for node in ast.walk(fn)
        if isinstance(node, ast.If)
        and any(isinstance(x, ast.Name) and x.id == "FLAC_MAX_PICTURE_BYTES"
                for x in ast.walk(node.test))
    ]
    assert gates, "no `if` actually tests the size against FLAC_MAX_PICTURE_BYTES"
    for gate in gates:
        assert not isinstance(gate.test, ast.Constant), (
            "the size guard is gated on a constant — it can never fire")
        # And it must be comparing the real payload, not something incidental.
        names = {x.id for x in ast.walk(gate.test) if isinstance(x, ast.Name)}
        assert "image_data" in names, (
            "the guard does not look at image_data: %s" % sorted(names))


def test_a_cover_that_cannot_be_shrunk_drops_the_art_rather_than_the_tags():
    """`None` from the shrinker means "write the tags without art". Returning
    the oversized bytes anyway would put us straight back into the failing
    save."""
    import core.metadata.artwork as mod
    src = inspect.getsource(mod.embed_album_art_metadata)
    branch = src.split("shrunk = _shrink_for_flac", 1)[1][:600]
    assert "if shrunk is None:" in branch
    assert "return False" in branch
    assert re.search(r"writing tags without art", branch), (
        "the log line has to say what happened to the art")
