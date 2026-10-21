"""Choose which release of an album downloads (old fork 1.9.0 / 2.0.11).

Upstream sends a whole album to a release source - a torrent, an NZB, a
Soulseek folder - by picking one itself: ``pick_best_album_release`` for
Prowlarr, ``_rank_album_bundle_folders`` for Soulseek. There was no way to say
"get me THIS release of the album". This lists what each release source has
for the album and pins the one the user picks onto the batch:

- ``stream_releases``: NDJSON, one line per source as it answers. Torrent and
  usenet run the very search the album download itself runs ("artist album",
  ``prowlarr_search_with_variants``), so the pick is among the candidates when
  the download looks again. Soulseek lists complete album folders.
- A torrent/usenet pick travels as an opaque candidate-store token, never a
  download URL (indexer URLs carry API keys - upstream's P0-03 rule). A Soulseek
  pick is a username + folder path; its album flow browses the folder itself.
- ``pinned_release``: what the start route keeps on the batch, validated.
- ``bundle_pin``: the master's ``(source, plugin kwargs)`` for a pin.
- ``pick_pinned``: the plugin's choice among its candidates - the pinned one,
  or None. A pin that is no longer offered fails the album visibly; silently
  grabbing a release the user did not choose would be worse.
"""

from __future__ import annotations

import json
import os
import threading
from queue import Queue
from typing import Any, Callable, Dict, Iterator, List, Optional, Tuple

from utils.logging_config import get_logger

logger = get_logger("commissary.release_picker")

RELEASE_SOURCES = ("torrent", "usenet", "soulseek")
MAX_RELEASES = 40
PIN_GONE = ("The release you picked is no longer offered by the indexer - "
            "choose a release again or let SoulSync choose")


# ── candidate keys: what a token resolves to, per plugin ───────────────────

def torrent_key(result) -> str:
    from core.download_plugins.torrent import _encode_candidate
    return _encode_candidate(result.download_url or result.magnet_uri, result.magnet_uri)


def usenet_key(result) -> str:
    return result.download_url


_KEYS = {"torrent": torrent_key, "usenet": usenet_key}


# ── listing ─────────────────────────────────────────────────────────────────

def _prowlarr_releases(source: str, plugin, album: str, artist: str) -> List[Dict[str, Any]]:
    from core.download_plugins.album_bundle import pick_best_album_release
    from core.download_plugins.candidate_store import get_candidate_store
    from core.download_plugins.torrent import _guess_quality_from_title, prowlarr_search_with_variants
    from core.quality.release_format import is_sample_release
    from utils.async_helpers import run_async

    query = f"{artist} {album}".strip()
    results = run_async(prowlarr_search_with_variants(plugin._prowlarr, query, source))
    key = _KEYS[source]
    candidates = [r for r in results if r.protocol == source and key(r)
                  and not is_sample_release(r.title, r.size)]
    auto = pick_best_album_release(candidates, _guess_quality_from_title,
                                   album_name=album, artist_name=artist)
    rows = []
    for r in candidates:
        token = get_candidate_store().put(key(r), metadata={
            "categories": list(r.categories or []), "release_title": r.title})
        rows.append({
            "source": source, "token": token, "title": r.title,
            "size": r.size or 0, "seeders": r.seeders, "grabs": r.grabs,
            "indexer": r.indexer_name or "", "quality": _guess_quality_from_title(r.title) or "",
            "auto_pick": r is auto,
        })
    rows.sort(key=lambda row: (not row["auto_pick"], -(row["seeders"] or row["grabs"] or 0)))
    return rows[:MAX_RELEASES]


def _soulseek_releases(plugin, album: str, artist: str) -> List[Dict[str, Any]]:
    from utils.async_helpers import run_async

    query = f"{artist} {album}".strip()
    _tracks, albums = run_async(plugin.search(query, timeout=30))
    ranked = plugin._rank_album_bundle_folders(list(albums or []), album, artist) or []
    rows = []
    for i, a in enumerate(ranked[:MAX_RELEASES]):
        path = getattr(a, "album_path", "") or ""
        rows.append({
            "source": "soulseek", "username": a.username, "folder_path": path,
            "title": os.path.basename(path.replace("\\", "/").rstrip("/")) or getattr(a, "album_title", ""),
            "size": getattr(a, "total_size", 0) or 0, "track_count": getattr(a, "track_count", 0),
            "quality": getattr(a, "dominant_quality", "") or "",
            "free_upload_slots": getattr(a, "free_upload_slots", 0),
            "queue_length": getattr(a, "queue_length", 0),
            "auto_pick": i == 0,
        })
    return rows


def release_sources(orchestrator) -> List[Tuple[str, Any]]:
    """The configured sources that can download a whole album, in a fixed order."""
    out = []
    for name in RELEASE_SOURCES:
        try:
            client = orchestrator.client(name)
            if client and hasattr(client, "download_album_to_staging") and client.is_configured():
                out.append((name, client))
        except Exception:  # noqa: BLE001 - a source that can't say is not offered
            continue
    return out


def releases_for(source: str, plugin, album: str, artist: str) -> List[Dict[str, Any]]:
    if source == "soulseek":
        return _soulseek_releases(plugin, album, artist)
    return _prowlarr_releases(source, plugin, album, artist)


def stream_releases(orchestrator, album: str, artist: str,
                    lister: Callable = releases_for) -> Iterator[str]:
    """NDJSON lines: a header, one ``{source, label, releases}`` or
    ``{source, label, error}`` per source as it answers, then ``{done}``."""
    sources = release_sources(orchestrator)
    yield json.dumps({"type": "header", "sources": [name for name, _ in sources]}) + "\n"
    out: Queue = Queue()

    def run(name, plugin):
        label = orchestrator.registry.display_name(name)
        try:
            out.put({"type": "source", "source": name, "label": label,
                     "releases": lister(name, plugin, album, artist)})
        except Exception as exc:  # noqa: BLE001 - one source failing is just that source
            logger.warning("release picker: %s failed: %s", name, exc)
            out.put({"type": "source", "source": name, "label": label, "releases": [],
                     "error": str(exc) or exc.__class__.__name__})

    for name, plugin in sources:
        threading.Thread(target=run, args=(name, plugin), daemon=True,
                         name=f"release-picker-{name}").start()
    for _ in sources:
        yield json.dumps(out.get()) + "\n"
    yield json.dumps({"type": "done"}) + "\n"


# ── the pin ─────────────────────────────────────────────────────────────────

def pinned_release(value: Any) -> Optional[Dict[str, str]]:
    """The pin a start request may carry, validated - or None. Only a
    candidate-store token travels for torrent/usenet, never a URL."""
    if not isinstance(value, dict):
        return None
    source = str(value.get("source") or "").strip().lower()
    title = str(value.get("title") or "")[:500]
    if source in ("torrent", "usenet"):
        from core.download_plugins.candidate_store import CandidateStore
        token = str(value.get("token") or "").strip()
        return {"source": source, "token": token, "title": title} if CandidateStore.is_token(token) else None
    if source == "soulseek":
        username = str(value.get("username") or "").strip()
        folder = str(value.get("folder_path") or "").strip()
        if username and folder:
            return {"source": source, "username": username, "folder_path": folder, "title": title}
    return None


def bundle_pin(pin: Any) -> Tuple[Optional[str], Optional[Dict[str, Any]]]:
    """``(source, plugin kwargs)`` for the master worker, or ``(None, None)``."""
    pin = pinned_release(pin)
    if pin is None:
        return None, None
    if pin["source"] == "soulseek":
        # no track list: given a folder, the Soulseek album flow browses it
        return "soulseek", {"preferred_source": {"username": pin["username"],
                                                 "folder_path": pin["folder_path"]},
                            "preferred_tracks": None, "preferred_alternatives": []}
    return pin["source"], {"preferred_release": {"token": pin["token"], "title": pin["title"]}}


def pick_pinned(source: str, candidates: List[Any], preferred_release: Any) -> Optional[Any]:
    """The candidate the user pinned, by its candidate-store token, or None."""
    from core.download_plugins.candidate_store import get_candidate_store
    token = str((preferred_release or {}).get("token") or "")
    wanted = get_candidate_store().resolve(token) if token else None
    if not wanted:
        return None
    key = _KEYS[source]
    for candidate in candidates:
        try:
            if key(candidate) == wanted:
                return candidate
        except Exception:  # noqa: BLE001
            continue
    return None


# ── the route ───────────────────────────────────────────────────────────────

def register(app, *, orchestrator: Callable, check_permission: Callable) -> None:
    """``POST /api/commissary/album-releases`` ``{album, artist}`` -> NDJSON."""
    from flask import Response, jsonify, request, stream_with_context

    @app.route("/api/commissary/album-releases", methods=["POST"])
    def commissary_album_releases():
        denied = check_permission()
        if denied:
            return denied
        data = request.get_json(silent=True) or {}
        album = str(data.get("album") or "").strip()
        artist = str(data.get("artist") or "").strip()
        orch = orchestrator()
        if not album or not artist:
            return jsonify({"success": False, "error": "album and artist are required"}), 400
        if orch is None:
            return jsonify({"success": False, "error": "Download sources are not ready"}), 503
        return Response(stream_with_context(stream_releases(orch, album, artist)),
                        mimetype="application/x-ndjson",
                        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})
