import type { SourceVerticalConfig } from '@/routes/sync/-sync.sources';
import type { SourcePlaylistState } from '@/routes/sync/-sync.state';
import type { DiscoveryRow, RawDiscoveryResult } from '@/routes/sync/-sync.transform';

import { toDiscoveryRows } from '@/routes/sync/-sync.transform';

/**
 * Commissary: mark a discovery track "Not available" (core/commissary/discovery_unavailable.py).
 *
 * A track no metadata source has keeps coming back on every retry. The mark
 * clears its match and its Wing It guess, so nothing syncs or downloads it,
 * and the YouTube / mirrored discovery never searches it again; "Available
 * again" undoes it, and so does fixing it by hand.
 */

export interface AvailabilityResponse {
  success?: boolean;
  error?: string;
  result?: RawDiscoveryResult;
  spotify_matches?: number;
}

export async function postAvailability(
  config: SourceVerticalConfig,
  sourceId: string,
  trackIndex: number,
  unavailable: boolean,
): Promise<AvailabilityResponse> {
  const response = await fetch('/api/commissary/discovery/unavailable', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({
      identifier: sourceId,
      track_index: trackIndex,
      unavailable,
      platform: config.id,
    }),
  });
  return (await response.json()) as AvailabilityResponse;
}

/** The modal state with the server's answer for one row (as applyUnmatched patches it). */
export function applyAvailability(
  state: SourcePlaylistState,
  config: SourceVerticalConfig,
  trackIndex: number,
  answer: AvailabilityResponse,
): SourcePlaylistState {
  const previous = state.rawResults[trackIndex];
  if (!previous || !answer.result) return state;
  const rawResults = state.rawResults.slice();
  rawResults[trackIndex] = { ...previous, ...answer.result };
  return {
    ...state,
    rawResults,
    rows: toDiscoveryRows(config.id, config.ux.foundVariant, rawResults),
    spotifyMatches:
      typeof answer.spotify_matches === 'number' ? answer.spotify_matches : state.spotifyMatches,
  };
}

/** A raw result the user marked: the transform shows it as "Not available". */
export function isUnavailableResult(r: RawDiscoveryResult): boolean {
  return r.status_class === 'unavailable';
}

export function AvailabilityButton({
  row,
  onAvailability,
}: {
  row: DiscoveryRow;
  onAvailability?: (row: DiscoveryRow, unavailable: boolean) => void;
}) {
  if (!onAvailability) return null;
  if (row.status_class === 'unavailable') {
    return (
      <button
        type="button"
        className="fix-match-btn"
        title="Search for this track again"
        data-commissary-availability="available"
        onClick={() => onAvailability(row, false)}
      >
        ↺ Available again
      </button>
    );
  }
  if (row.status_class === 'found') return null;
  return (
    <button
      type="button"
      className="rematch-btn"
      style={{ marginLeft: 4 }}
      title="Not available on any source - stop searching for it"
      data-commissary-availability="unavailable"
      onClick={() => onAvailability(row, true)}
    >
      🚫
    </button>
  );
}
