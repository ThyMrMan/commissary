import type { InspectorCandidate } from '@/features/downloads/inspector';

import { openCandidateInspector } from '@/features/downloads/inspector-modal';

/**
 * Commissary: upstream's Candidate Inspector for any track in the Download
 * Missing Tracks window - every album and every Search page track opens there
 * (core/commissary/track_inspector.py). Picking a file starts exactly that file
 * as a pinned one-task download, the same way the wishlist's pick does.
 */

/** The track as the download window holds it: Spotify-shaped, loosely. */
export interface CommissaryInspectTrack {
  name: string;
  artists?: unknown;
  album?: unknown;
  duration_ms?: number;
  [key: string]: unknown;
}

declare global {
  interface Window {
    openCommissaryTrackInspector?: (track: CommissaryInspectTrack) => void;
  }
}

const RELEASE_LEVEL = new Set(['torrent', 'usenet', 'lidarr']);

function artistOf(track: CommissaryInspectTrack): string | undefined {
  const artists = Array.isArray(track.artists) ? track.artists : [];
  const first: unknown = artists[0];
  if (typeof first === 'string') return first;
  if (first && typeof first === 'object' && 'name' in first) return String(first.name);
  return undefined;
}

function albumOf(track: CommissaryInspectTrack): string | undefined {
  const album = track.album;
  if (typeof album === 'string') return album || undefined;
  if (album && typeof album === 'object' && 'name' in album) return String(album.name);
  return undefined;
}

function candidatePayload(candidate: InspectorCandidate) {
  return {
    username: candidate.username,
    filename: candidate.filename,
    size: candidate.size ?? 0,
    bitrate: candidate.bitrate ?? 0,
    duration: candidate.duration ?? 0,
    quality: candidate.quality ?? '',
    free_upload_slots: candidate.free_upload_slots ?? 0,
    upload_speed: candidate.upload_speed ?? 0,
    queue_length: candidate.queue_length ?? 0,
    artist: candidate.artist ?? '',
    title: candidate.title ?? '',
  };
}

async function postJson(url: string, body: unknown): Promise<void> {
  const response = await fetch(url, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  });
  let data: Record<string, unknown> = {};
  try {
    data = (await response.json()) as Record<string, unknown>;
  } catch {
    /* not json */
  }
  if (!response.ok || data.success === false || data.error) {
    const message = typeof data.error === 'string' && data.error ? data.error : '';
    throw new Error(message || `Request failed (${response.status})`);
  }
}

export function openCommissaryTrackInspector(track: CommissaryInspectTrack): void {
  openCandidateInspector({
    name: track.name,
    artist: artistOf(track),
    album: albumOf(track),
    durationMs: typeof track.duration_ms === 'number' ? track.duration_ms : undefined,
    searchUrl: '/api/commissary/inspect',
    searchBody: { track },
    cannotGrab: (c) =>
      RELEASE_LEVEL.has(String(c.username || '').toLowerCase())
        ? "A torrent or Usenet hit is a whole release, so it can't be picked for one track. Begin Analysis can use it."
        : null,
    onGrab: async (candidate) => {
      const d = candidate.decision;
      await postJson('/api/commissary/inspect/download', {
        track,
        candidate: candidatePayload(candidate),
        override: d && !d.accepted ? { code: d.code, stage: d.stage } : undefined,
      });
    },
  });
}

/** Called where upstream mounts the inspector host, for the classic pages. */
export function installCommissaryTrackInspector(): void {
  window.openCommissaryTrackInspector = openCommissaryTrackInspector;
}
