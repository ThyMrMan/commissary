"""Catch identifiers used in the legacy static JS but never declared.

Shipped in 1.8.5: the out-of-place check threw "src is not defined" at runtime.
A patch adding `var src = ...` failed partway and never wrote, while a separate
edit that USED src did — so the usage landed without its declaration.

Nothing caught it. `node --check` only validates syntax, and a ReferenceError is
perfectly valid syntax. oxlint is configured for webui/src (the React app) and
these files aren't covered, nor are its node_modules installed.

The check is deliberately narrow rather than a general scope analyser: it looks
at identifiers interpolated into string concatenation (`' + name + '`), which is
where user-facing message templates live and exactly the shape that broke. A
real scope checker over ES5 with nested closures would produce enough false
positives to be turned off, and a guard people switch off protects nothing.
"""

from __future__ import annotations

import pathlib
import re

import pytest

# The static JS that predates the React app and gets no linting.
FILES = [
    "webui/static/video/video-manage-panel.js",
    "webui/static/video/video-wishlist.js",
]

# Identifiers legitimately reachable without a local declaration.
GLOBALS = {
    "window", "document", "console", "JSON", "Math", "Date", "String", "Number",
    "Boolean", "Array", "Object", "URLSearchParams", "CustomEvent", "Promise",
    "fetch", "setTimeout", "clearTimeout", "location", "navigator", "encodeURIComponent",
    "decodeURIComponent", "parseInt", "parseFloat", "isNaN", "undefined", "null", "true",
    "false", "this", "arguments",
    # app-wide helpers defined in other bundles
    "showToast", "showConfirmDialog", "toast", "esc", "VideoYoutube", "sized",
}

_ROOT = pathlib.Path(__file__).resolve().parents[1]
# `' + ident + '` / `" + ident + "` — a bare identifier spliced into a template.
_INTERP = re.compile(r"[+]\s*([A-Za-z_$][A-Za-z0-9_$]*)\s*[+]")
_DECL = re.compile(r"\b(?:var|let|const)\s+([A-Za-z_$][A-Za-z0-9_$]*)")
_FUNC = re.compile(r"\bfunction\s*[A-Za-z0-9_$]*\s*\(([^)]*)\)")


def _strip_comments(text: str) -> str:
    """Blank out comments, tracking string state so a '//' inside a URL or a
    quoted literal isn't mistaken for one. Prose is full of '+ word +' — this
    file's own comments say "movies + episodes + YouTube videos" — and scanning
    it produces noise that would get the whole check disabled."""
    out, i, n = [], 0, len(text)
    quote = None
    while i < n:
        c = text[i]
        if quote:
            out.append(c)
            if c == "\\" and i + 1 < n:
                out.append(text[i + 1]); i += 2; continue
            if c == quote:
                quote = None
            i += 1
            continue
        if c in "'\"`":
            quote = c; out.append(c); i += 1; continue
        if c == "/" and i + 1 < n and text[i + 1] == "/":
            while i < n and text[i] != "\n":
                i += 1
            continue
        if c == "/" and i + 1 < n and text[i + 1] == "*":
            end = text.find("*/", i + 2)
            i = n if end == -1 else end + 2
            continue
        out.append(c); i += 1
    return "".join(out)


def _declared_names(text: str) -> set:
    """Everything declared anywhere in the file: vars, consts, and parameters.

    File-wide rather than per-scope on purpose — a name declared in ANOTHER
    function is not proof it's in scope here, but treating it as proof is the
    conservative direction. This test exists to catch a name declared NOWHERE."""
    names = set(_DECL.findall(text))
    for params in _FUNC.findall(text):
        for p in params.split(","):
            p = p.strip()
            if p:
                names.add(p)
    return names


@pytest.mark.parametrize("rel", FILES)
def test_interpolated_identifiers_are_declared(rel):
    text = _strip_comments((_ROOT / rel).read_text(encoding="utf-8"))
    declared = _declared_names(text) | GLOBALS
    missing = sorted({n for n in _INTERP.findall(text)
                      if n not in declared and not n.isdigit()})
    assert not missing, (
        "%s interpolates identifiers that are declared nowhere in the file: %s\n"
        "This is the 'src is not defined' shape — a usage whose declaration "
        "never landed." % (rel, missing))


def test_the_check_would_have_caught_the_shipped_bug():
    """Pin the detector itself, so a future refactor can't quietly defang it."""
    broken = """
        function f(b) {
            if (note) note.textContent = b.count + ' rows ' + src + ' does not list';
        }
    """
    broken = _strip_comments(broken)
    declared = _declared_names(broken) | GLOBALS
    assert "src" in {n for n in _INTERP.findall(broken) if n not in declared}

    fixed = """
        function f(b) {
            var src = String(b.source || 'tmdb').toUpperCase();
            if (note) note.textContent = b.count + ' rows ' + src + ' does not list';
        }
    """
    fixed = _strip_comments(fixed)
    declared = _declared_names(fixed) | GLOBALS
    assert not {n for n in _INTERP.findall(fixed) if n not in declared}
