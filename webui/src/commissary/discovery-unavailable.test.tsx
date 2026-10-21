import { cleanup, fireEvent, render, screen } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';

import type { DiscoveryRow, RawDiscoveryResult } from '@/routes/sync/-sync.transform';

import { SYNC_SOURCES } from '@/routes/sync/-sync.sources';
import { freshSourceState } from '@/routes/sync/-sync.state';
import { toDiscoveryRow } from '@/routes/sync/-sync.transform';
import { DiscoveryModal } from '@/routes/sync/-ui/discovery-modal';

import { applyAvailability, postAvailability } from './discovery-unavailable';

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

const NOT_FOUND: DiscoveryRow = {
  index: 0,
  yt_track: 'Rare B-side',
  yt_artist: 'A',
  status: '❌ Not Found',
  status_class: 'not-found',
  spotify_track: '-',
  spotify_artist: '-',
  spotify_album: '-',
};
const UNAVAILABLE: DiscoveryRow = {
  ...NOT_FOUND,
  index: 1,
  status: '🚫 Not available',
  status_class: 'unavailable',
};

function modal(rows: DiscoveryRow[], onAvailabilityTrack = vi.fn()) {
  const state = {
    ...freshSourceState(SYNC_SOURCES.youtube, 'y1'),
    playlist: { name: 'P', tracks: [{}, {}] },
    phase: 'discovered' as const,
    rows,
  };
  render(
    <DiscoveryModal
      config={SYNC_SOURCES.youtube}
      state={state}
      standalone={false}
      onClose={() => {}}
      onStartDiscovery={() => {}}
      onStartSync={() => {}}
      onCancelSync={() => {}}
      onDownloadMissing={() => {}}
      onFixTrack={() => {}}
      onUnmatchTrack={() => {}}
      onAvailabilityTrack={onAvailabilityTrack}
    />,
  );
  return onAvailabilityTrack;
}

describe('Not available', () => {
  it('shows a marked result as Not available', () => {
    const row = toDiscoveryRow(
      'youtube',
      'lenient',
      {
        status: 'Not available',
        status_class: 'unavailable',
        yt_track: 'Rare',
      } as RawDiscoveryResult,
      0,
    );
    expect([row.status, row.status_class]).toEqual(['🚫 Not available', 'unavailable']);
  });

  it('offers the mark beside Fix, and Available again on a marked row', () => {
    const onAvailability = modal([NOT_FOUND, UNAVAILABLE]);
    fireEvent.click(screen.getByTitle('Not available on any source - stop searching for it'));
    expect(onAvailability).toHaveBeenLastCalledWith(NOT_FOUND, true);
    fireEvent.click(screen.getByText('↺ Available again'));
    expect(onAvailability).toHaveBeenLastCalledWith(UNAVAILABLE, false);
    expect(screen.getAllByText('🔧 Fix')).toHaveLength(1);
  });

  it('posts the platform with the row and applies the answer with its count', async () => {
    const calls: unknown[] = [];
    vi.stubGlobal(
      'fetch',
      vi.fn(async (_url: string, init: RequestInit) => {
        calls.push(JSON.parse(init.body as string));
        return new Response(
          JSON.stringify({
            success: true,
            spotify_matches: 0,
            result: { status: 'Not available', status_class: 'unavailable', spotify_data: null },
          }),
        );
      }),
    );
    const answer = await postAvailability(SYNC_SOURCES.deezer, 'd1', 0, true);
    expect(calls).toEqual([
      { identifier: 'd1', track_index: 0, unavailable: true, platform: 'deezer' },
    ]);
    const state = {
      ...freshSourceState(SYNC_SOURCES.deezer, 'd1'),
      rawResults: [{ status: 'Found', status_class: 'found', spotify_data: { name: 'x' } }],
      spotifyMatches: 1,
    };
    const next = applyAvailability(state, SYNC_SOURCES.deezer, 0, answer);
    expect(next.rows[0].status_class).toBe('unavailable');
    expect(next.spotifyMatches).toBe(0);
  });
});
