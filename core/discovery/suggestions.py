"""Near-miss candidates kept for a track discovery couldn't match.

When no candidate reaches the auto-match bar the track becomes a Wing It guess,
and every candidate that was scored used to be thrown away. The best few are
kept on the result instead -- and saved with a mirrored track -- so the discovery
modal and the Wing It Pool can offer one to accept without searching again.

A suggestion is shaped like a row from the Fix dialog's search endpoints, so
accepting one posts it to the same routes a Fix does.
"""

from __future__ import annotations

from typing import Any, Iterable, List, Tuple

SUGGESTION_LIMIT = 3


def _name(value: Any) -> str:
    if isinstance(value, dict):
        return str(value.get('name') or '')
    return str(value or '')


def build_discovery_suggestions(
    ranked: Iterable[Tuple[float, int, Any]],
    source: str,
    *,
    below: float,
    limit: int = SUGGESTION_LIMIT,
) -> List[dict]:
    """The best candidates under ``below``, one per track id, best first.

    ``ranked`` holds ``(confidence, index, candidate)`` from every search the
    track ran (see ``_discovery_rank_candidates``), so one track can appear more
    than once; its best confidence is the one kept. A candidate with no id can't
    be matched to, so it is never suggested.
    """
    suggestions: List[dict] = []
    seen = set()
    for confidence, _index, candidate in sorted(ranked, key=lambda entry: entry[0], reverse=True):
        if confidence >= below:
            continue
        track_id = str(getattr(candidate, 'id', '') or '')
        if not track_id or track_id in seen:
            continue
        seen.add(track_id)
        artists = (_name(artist) for artist in (getattr(candidate, 'artists', None) or []))
        suggestions.append({
            'id': track_id,
            'name': str(getattr(candidate, 'name', '') or ''),
            'artists': [artist for artist in artists if artist],
            'album': _name(getattr(candidate, 'album', '')),
            'duration_ms': int(getattr(candidate, 'duration_ms', 0) or 0),
            'image_url': str(getattr(candidate, 'image_url', '') or ''),
            'source': source,
            'confidence': round(float(confidence), 3),
        })
        if len(suggestions) >= limit:
            break
    return suggestions
