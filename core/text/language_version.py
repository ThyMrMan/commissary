"""Language versions of a song: "(English Version)", "-Japanese ver.-", "英語版".

An English version and the Japanese original are different recordings. They
usually share the backing track and the length, and a title comparison that
drops bracketed text sees one title twice -- so until the matchers could see the
marker, a request for either version took the other, and a Japanese version was
filed into the Korean album of the same name ("FEARLESS (Japanese Version)" in
"FEARLESS").

``language_version`` names the language a title's marker claims, or ``None``.
Matchers refuse to pair two titles whose answers differ: a marked title pairs
only with the same language, an unmarked title only with another unmarked one.
"""

from __future__ import annotations

import re
from typing import Optional

# Latin spellings, as tags and release names write them.
_LATIN = {
    'english': 'en', 'eng': 'en',
    'japanese': 'ja', 'jpn': 'ja', 'jp': 'ja',
    'korean': 'ko', 'kor': 'ko', 'kr': 'ko',
    'chinese': 'zh', 'mandarin': 'zh', 'cantonese': 'zh', 'chn': 'zh',
}
# Full names alone, for a bare bracketed "(English)". An abbreviation in brackets
# ("(JP)") is as often a release's region as a language version.
_FULL_NAMES = ('english', 'japanese', 'korean', 'chinese', 'mandarin', 'cantonese')
# The language written in CJK: Japanese (英語), Chinese (英文), Korean (영어).
_CJK = {
    '英語': 'en', '英文': 'en', '영어': 'en',
    '日本語': 'ja', '日文': 'ja', '일본어': 'ja',
    '韓国語': 'ko', '韓國語': 'ko', '韓文': 'ko', '韩文': 'ko', '한국어': 'ko',
    '中国語': 'zh', '中國語': 'zh', '中文': 'zh', '国语': 'zh', '國語': 'zh',
    '広東語': 'zh', '粵語': 'zh', '粤语': 'zh', '중국어': 'zh',
}


def _alternation(words):
    return '|'.join(re.escape(word) for word in sorted(words, key=len, reverse=True))


# A language, at most one short word ("english tv version"), then version / ver.
# Bounded by non-alphanumerics rather than \b, so a CJK character right before
# the language name still counts as a boundary.
_LATIN_VERSION_RE = re.compile(
    r'(?<![a-z0-9])(' + _alternation(_LATIN) + r')(?![a-z0-9])'
    r'[\s._\-]*(?:[a-z]{1,4}[\s._\-]+)?(?:versions?|ver)(?![a-z])')
_BRACKETED_RE = re.compile(
    r'[(\[（【]\s*(' + _alternation(_FULL_NAMES) + r')\s*[)\]）】]')
_CJK_VERSION_RE = re.compile(
    r'(' + _alternation(_CJK) + r')\s*(?:版|ver|バージョン|ヴァージョン|버전)')

# The labels MusicMatchingEngine.detect_version_type gives a language version.
LANGUAGE_VERSION_LABELS = {
    'en': 'english version',
    'ja': 'japanese version',
    'ko': 'korean version',
    'zh': 'chinese version',
}
_LABELS = frozenset(LANGUAGE_VERSION_LABELS.values())


def language_version(text: Optional[str]) -> Optional[str]:
    """The language (``'en'``, ``'ja'``, ``'ko'``, ``'zh'``) a title's
    language-version marker names, or ``None`` when it carries none."""
    if not text:
        return None
    lowered = str(text).lower()
    match = _CJK_VERSION_RE.search(lowered)
    if match:
        return _CJK[match.group(1)]
    match = _LATIN_VERSION_RE.search(lowered) or _BRACKETED_RE.search(lowered)
    if match:
        return _LATIN[match.group(1)]
    return None


def is_language_version_label(label: Optional[str]) -> bool:
    """Whether a ``detect_version_type`` label is a language version."""
    return label in _LABELS


__all__ = ['LANGUAGE_VERSION_LABELS', 'is_language_version_label', 'language_version']
