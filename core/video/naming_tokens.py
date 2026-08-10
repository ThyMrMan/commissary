"""Sonarr/Radarr-style ``{Token}`` naming, alongside the existing ``$token`` scheme.

The point of this module is that a scheme copied verbatim out of the TRaSH guides
renders correctly. That takes more than a bigger vocabulary — their schemes lean
on a small grammar:

    {Series CleanTitleWithoutYear} {(Series Year)} - S{season:00}E{episode:00} -
    {Episode CleanTitle:90} {[Custom Formats]}{[Quality Full]}
    {[Mediainfo AudioCodec}{ Mediainfo AudioChannels]}
    {[MediaInfo VideoDynamicRangeType]}{[Mediainfo VideoCodec]}{-Release Group}

Three rules make that work:

  · A ``{…}`` GROUP is literal text wrapped around one token. When the token has
    no value the WHOLE group disappears — brackets, dashes and all. That is what
    keeps an unknown release group from leaving a trailing '-', and a missing
    codec from leaving an empty '[]'.
  · ``:spec`` means zero-padding on a numeric token (``{season:00}``) and a
    length cap on a text one (``{Episode CleanTitle:90}``). Same syntax, decided
    by the token, exactly as Sonarr does it.
  · Token names are matched case- and space-insensitively, because the guides
    themselves are inconsistent — ``{Mediainfo AudioCodec}`` and
    ``{MediaInfo VideoCodec}`` appear in the same line.

The split-bracket idiom ``{[A}{ B]}`` deliberately opens a bracket in one group
and closes it in the next, so that ``[EAC3 5.1]`` collapses when either half is
missing. Rather than model Sonarr's bracket pairing, an unmatched bracket left
behind by a vanished group is swept up afterwards by ``strip_orphan_brackets`` —
one rule that also cleans up any other way a user's template can end up lopsided.

Pure: no I/O, no DB. ``organization.render_path`` supplies the values.
"""

from __future__ import annotations

import re
from typing import Any, Dict, Iterable, Optional

# A token reference inside a group: the name, then an optional ':spec'.
_SPEC = re.compile(r"^(?P<name>.*?)(?::(?P<spec>[^:]*))?$", re.S)

# Characters a filename can't carry. Sonarr's "CleanTitle" drops them outright
# rather than substituting, which is why 'Marvel's Daredevil' cleans to
# 'Marvels Daredevil' and not 'Marvel_s Daredevil'.
_CLEAN_DROP = re.compile(r"[\\/:*?\"<>|]+")
_CLEAN_SMART = str.maketrans({"’": "", "'": "", "‘": "", "“": "", "”": ""})

_ARTICLES = ("The ", "A ", "An ")


def canonical(name: Any) -> str:
    """Fold a token name for lookup: lowercase, spaces and underscores removed.

    Hyphens are KEPT significant because Sonarr has two tokens that differ only
    by one: ``{Air-Date}`` is '2026-07-08' and ``{AirDate}`` is '2026.07.08'.
    Folding the hyphen away would silently collapse them into whichever was
    defined last."""
    return re.sub(r"[^a-z0-9-]+", "", str(name or "").lower())


def clean_title(value: Any) -> str:
    """Sonarr's CleanTitle: filename-hostile characters removed, apostrophes
    dropped rather than replaced, whitespace collapsed."""
    s = str(value or "").translate(_CLEAN_SMART)
    s = _CLEAN_DROP.sub("", s)
    return re.sub(r"\s+", " ", s).strip()


def title_the(value: Any) -> str:
    """'The Matrix' → 'Matrix, The' — the sort-friendly form Sonarr calls
    TitleThe. Leaves a title that doesn't start with an article alone."""
    s = str(value or "").strip()
    for art in _ARTICLES:
        if s.startswith(art):
            return "%s, %s" % (s[len(art):].strip(), art.strip())
    return s


def strip_orphan_brackets(text: str) -> str:
    """Drop brackets left unpaired by a group that rendered empty.

    The split-bracket idiom ``{[Mediainfo AudioCodec}{ Mediainfo AudioChannels]}``
    is one bracket pair spread across two groups. When only one of them survives
    the result is lopsided — '[EAC3' or ' 5.1]' — and neither belongs in a
    filename. Sweeping unmatched brackets afterwards handles that case and every
    other way a hand-written template can end up unbalanced, without having to
    track which group owned which bracket."""
    out, depth = [], 0
    for ch in str(text or ""):
        if ch == "[":
            depth += 1
            out.append(ch)
        elif ch == "]":
            if depth <= 0:
                continue                     # a close with nothing open — drop it
            depth -= 1
            out.append(ch)
        else:
            out.append(ch)
    if depth <= 0:
        return "".join(out)
    # Unclosed opens remain: remove them from the right, innermost first.
    result = "".join(out)
    for _ in range(depth):
        idx = result.rfind("[")
        if idx < 0:
            break
        result = result[:idx] + result[idx + 1:]
    return result


def _apply_spec(raw: str, spec: Optional[str], numeric: bool) -> str:
    """':00' zero-pads a number; ':90' caps a title's length. Same syntax, and
    the token decides which is meant — Sonarr's own rule."""
    if not spec:
        return raw
    spec = spec.strip()
    if not spec.isdigit():
        return raw
    if numeric:
        try:
            return "%0*d" % (len(spec), int(raw))
        except (TypeError, ValueError):
            return raw
    limit = int(spec)
    return raw[:limit].rstrip() if limit > 0 and len(raw) > limit else raw


class TokenSet:
    """A resolved token vocabulary: canonical name → (value, is_numeric)."""

    def __init__(self, values: Dict[str, Any], numeric: Iterable[str] = ()):
        self._numeric = {canonical(n) for n in numeric}
        self._values: Dict[str, str] = {}
        for name, value in (values or {}).items():
            key = canonical(name)
            self._values[key] = "" if value is None else str(value)
        # Longest first so 'Series CleanTitleWithoutYear' is preferred over
        # 'Series CleanTitle', which is a prefix of it.
        self._ordered = sorted(self._values, key=len, reverse=True)

    def names(self) -> list:
        return list(self._values)

    def match(self, body: str):
        """Split a group body into (prefix, value, suffix), or None when it
        holds no token this set knows. The token may sit anywhere inside —
        '[Quality Full]' is prefix '[', token, suffix ']'."""
        m = _SPEC.match(body or "")
        head, spec = (m.group("name") or ""), m.group("spec")
        folded = canonical(head)
        for key in self._ordered:
            idx = folded.find(key)
            if idx < 0:
                continue
            # Map the fold back onto the raw text: walk the original, counting
            # only the characters the fold kept.
            start = end = None
            kept = 0
            for pos, ch in enumerate(head):
                if canonical(ch):
                    if kept == idx and start is None:
                        start = pos
                    kept += 1
                    if kept == idx + len(key):
                        end = pos + 1
                        break
            if start is None or end is None:
                continue
            raw = _apply_spec(self._values[key], spec, key in self._numeric)
            return head[:start], raw, head[end:]
        return None


def _render(text: str, tokens: TokenSet, sanitize):
    """(rendered, produced) — ``produced`` is True when at least one token in
    this stretch of template resolved to a non-empty value.

    The flag is what makes nesting work. In ``{edition-{Edition Tags}}`` the
    outer group holds no token of its own, only the literal 'edition-' and an
    inner group. Without knowing whether the INNER group produced anything, the
    outer would emit a bare 'edition-' on a release with no edition."""
    out, i, produced = [], 0, False
    while True:
        start = text.find("{", i)
        if start < 0:
            out.append(text[i:])
            break
        depth, j = 1, start + 1
        while j < len(text) and depth:
            if text[j] == "{":
                depth += 1
            elif text[j] == "}":
                depth -= 1
            j += 1
        if depth:                       # unterminated '{' — emit it literally
            out.append(text[i:])
            break
        out.append(text[i:start])
        body = text[start + 1:j - 1]
        if "{" in body:
            inner, inner_produced = _render(body, tokens, sanitize)
            # A group whose only tokens are nested ones lives or dies with them.
            if inner_produced:
                out.append(inner)
                produced = True
            i = j
            continue
        hit = tokens.match(body)
        if hit is None:
            out.append(body)            # no token in here — pure literal
        else:
            prefix, value, suffix = hit
            if value:
                out.append(prefix + (sanitize(value) if sanitize else value) + suffix)
                produced = True
            # else: the whole group disappears, brackets and dashes included
        i = j
    return "".join(out), produced


def render(template: Any, tokens: TokenSet, *, sanitize=None) -> str:
    """Render ``{Token}`` groups. ``$token`` text is left untouched for the
    legacy renderer that runs alongside this one.

    ``sanitize`` (optional) is applied to each token VALUE only, never to the
    template's own literals — a title containing '/' must not be able to spawn a
    directory, but a '/' the user typed in their template is a real separator."""
    return _render(str(template or ""), tokens, sanitize)[0]


def has_brace_tokens(template: Any) -> bool:
    """Does this template use the ``{Token}`` scheme at all? Lets the renderer
    skip the extra pass — and the orphan-bracket sweep — for the ``$token``
    templates every existing install still has."""
    return "{" in str(template or "")


__all__ = ["TokenSet", "canonical", "clean_title", "title_the", "render",
           "strip_orphan_brackets", "has_brace_tokens"]
