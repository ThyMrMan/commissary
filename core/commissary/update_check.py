"""Update check for Commissary's version scheme.

Commissary versions are upstream's version plus a fork counter:
``3.5.7-c.1``, ``3.5.7-c.2``, then ``3.5.8-c.1`` after merging upstream 3.5.8.
Upstream's ``core.update_check.parse_semver`` stops at MAJOR.MINOR.PATCH, so
``3.5.7-c.2`` would look identical to ``3.5.7-c.1`` and the fork's own
releases would never be offered. This wraps it with the counter and otherwise
keeps upstream's severity rules (critical marker anywhere in the jump wins,
then a MAJOR bump, else routine).
"""

from __future__ import annotations

import re
from typing import Any, Dict, List, Optional

from core.update_check import _CRITICAL_MARKERS, parse_semver

_FORK_COUNTER = re.compile(r"-c\.(\d+)")


def parse_version(value: Any) -> Optional[tuple]:
    """'3.5.7-c.2+abc1234' -> (3, 5, 7, 2); '3.5.7' -> (3, 5, 7, 0)."""
    base = parse_semver(value)
    if base is None:
        return None
    m = _FORK_COUNTER.search(str(value or ""))
    return base + (int(m.group(1)) if m else 0,)


def evaluate_update(current_base: str, releases: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Same contract as ``core.update_check.evaluate_update``."""
    out = {"available": False, "latest_version": None, "severity": None,
           "release_url": None, "notes": None}
    current = parse_version(current_base)
    if current is None:
        return out
    newer = []
    for rel in releases or []:
        if not isinstance(rel, dict) or rel.get("draft") or rel.get("prerelease"):
            continue
        tag = rel.get("tag_name") or rel.get("name")
        ver = parse_version(tag)
        if ver is None or ver <= current:
            continue
        newer.append((ver, str(tag).lstrip("vV"), rel))
    if not newer:
        return out
    newer.sort(key=lambda item: item[0], reverse=True)
    latest_ver, latest_tag, latest_rel = newer[0]
    if any(_CRITICAL_MARKERS.search(f"{rel.get('name') or ''} {rel.get('body') or ''}")
           for _v, _t, rel in newer):
        severity = "critical"
    elif latest_ver[0] > current[0]:
        severity = "major"
    else:
        severity = "update"
    out.update({
        "available": True,
        "latest_version": latest_tag,
        "severity": severity,
        "release_url": latest_rel.get("html_url"),
        "notes": (latest_rel.get("name") or "").strip() or None,
    })
    return out
