"""Language versions of a song: "(English Version)", "-Japanese ver.-", "英語版".

An English version and the Japanese original are different recordings. They
usually share the backing track and the length, and a title comparison that
drops bracketed text sees one title twice - so a request for either version
took the other, and a Japanese version was filed into the Korean album of the
same name ("FEARLESS (Japanese Version)" in "FEARLESS"). Old fork 2.x.

Upstream's ``recording_version_markers`` already treats a bracketed language
word as a marker ("(English Version)" -> 'english'). This adds the spellings it
can't see - a dash-wrapped tag with no spaces ("-Japanese ver.-"), an
abbreviation with ver ("(JP ver.)"), the language written in CJK ("英語版") -
and carries the same rule into the matchers upstream never taught about
languages: the engine's title similarity, its version detector (staging and
AcoustID verification compare those labels) and Soulseek scorer, and album
import's title scoring.

The rule everywhere: a marked title pairs only with the same language, an
unmarked title only with another unmarked one.
"""

from __future__ import annotations

import os
import re
from difflib import SequenceMatcher
from typing import Any, Optional

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

# The labels the engine's detect_version_type gives a language version.
LANGUAGE_VERSION_LABELS = {
    'en': 'english version',
    'ja': 'japanese version',
    'ko': 'korean version',
    'zh': 'chinese version',
}
# The names upstream's recording_version_markers uses for the same languages.
_MARKER_NAMES = {'en': 'english', 'ja': 'japanese', 'ko': 'korean', 'zh': 'chinese'}

# What similarity a language mismatch is capped at - below every accept gate.
SIMILARITY_CAP = 0.30


def language_version(text: Any) -> Optional[str]:
    """The language (``'en'``, ``'ja'``, ``'ko'``, ``'zh'``) a title's
    language-version marker names, or ``None`` when it carries none."""
    if not text or not isinstance(text, str):
        return None
    lowered = text.lower()
    match = _CJK_VERSION_RE.search(lowered)
    if match:
        return _CJK[match.group(1)]
    match = _LATIN_VERSION_RE.search(lowered) or _BRACKETED_RE.search(lowered)
    if match:
        return _LATIN[match.group(1)]
    return None


def first_language(*texts: Any) -> Optional[str]:
    """The first language any of ``texts`` names - a track's title, then its album."""
    for text in texts:
        language = language_version(text)
        if language:
            return language
    return None


def file_language(path: Any, title: Any = None, album: Any = None) -> Optional[str]:
    """A file's language version, from its title tag, album tag or filename."""
    stem = os.path.splitext(os.path.basename(str(path or '')))[0]
    return first_language(title, album, stem)


# ── hooks ───────────────────────────────────────────────────────────────────

def recording_markers(title: Any) -> frozenset:
    """Extra ``recording_version_markers`` for a title, in upstream's names."""
    language = language_version(title)
    return frozenset({_MARKER_NAMES[language]}) if language else frozenset()


def capped_similarity(str1: str, str2: str) -> Optional[float]:
    """The engine's similarity for a pair in different language versions -
    capped, never raised - or ``None`` when the languages agree."""
    if language_version(str1) == language_version(str2):
        return None
    return min(SequenceMatcher(None, str1, str2).ratio(), SIMILARITY_CAP)


def version_label(text: Any) -> Optional[str]:
    """``detect_version_type``'s label for a language version, or ``None``."""
    language = language_version(text)
    return LANGUAGE_VERSION_LABELS[language] if language else None


def soulseek_mismatch(wanted_track: Any, filename: Any) -> bool:
    """A Soulseek file in another language than the request. The whole path
    counts - "UNDEAD (English Version)/01 UNDEAD.flac" is the English one - and
    so does the requested album: "UNDEAD" on the single "UNDEAD (English
    Version)" wants the English one."""
    wanted = first_language(getattr(wanted_track, 'name', None),
                            getattr(wanted_track, 'album', None))
    return wanted != language_version(filename)


__all__ = [
    'LANGUAGE_VERSION_LABELS', 'SIMILARITY_CAP', 'capped_similarity', 'file_language',
    'first_language', 'language_version', 'recording_markers', 'soulseek_mismatch',
    'version_label',
]
