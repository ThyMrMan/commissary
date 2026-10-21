import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';

import type { DiscoveryRow } from '@/routes/sync/-sync.transform';

import { SYNC_SOURCES } from '@/routes/sync/-sync.sources';
import { FixModal } from '@/routes/sync/-ui/fix-modal';

import { rankFixResults, searchEverySource, SOURCE_KEY } from './fix-search';

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
  delete (window as { showConfirmDialog?: unknown }).showConfirmDialog;
  delete (window as { showToast?: unknown }).showToast;
});

const ROW: DiscoveryRow = {
  index: 2,
  yt_track: 'Idol',
  yt_artist: 'YOASOBI',
  status: '❌ Not Found',
  status_class: 'not-found',
  spotify_track: '-',
  spotify_artist: '-',
  spotify_album: '-',
};

describe('ranking', () => {
  it('puts the exact title first and versions last, the active source breaking ties', () => {
    const ranked = rankFixResults(
      [
        { name: 'Idol (Live)', artists: ['YOASOBI'], [SOURCE_KEY]: 'deezer' },
        { name: 'Idol - Cover', artists: ['Someone'], [SOURCE_KEY]: 'itunes' },
        { name: 'Idol', artists: ['YOASOBI'], [SOURCE_KEY]: 'itunes' },
        { name: 'Idol', artists: ['YOASOBI'], [SOURCE_KEY]: 'deezer' },
      ],
      'Idol',
      'YOASOBI',
      ['deezer', 'itunes'],
    );
    expect(ranked.map((t) => `${t.name}@${t[SOURCE_KEY]}`)).toEqual([
      'Idol@deezer',
      'Idol@itunes',
      'Idol (Live)@deezer',
      'Idol - Cover@itunes',
    ]);
  });

  it('asks every source at once and keeps going past a failing one', async () => {
    const asked: string[] = [];
    const updates: number[] = [];
    const found = await searchEverySource(
      'Idol',
      'YOASOBI',
      'Deezer',
      (merged) => updates.push(merged.length),
      async (endpoint) => {
        asked.push(endpoint);
        if (endpoint.includes('spotify')) throw new Error('401');
        return endpoint.includes('musicbrainz')
          ? []
          : [{ id: endpoint, name: 'Idol', artists: ['YOASOBI'] }];
      },
    );
    expect(asked.sort()).toEqual([
      '/api/deezer/search_tracks',
      '/api/itunes/search_tracks',
      '/api/musicbrainz/search_tracks',
      '/api/spotify/search_tracks',
    ]);
    expect(found.map((t) => t[SOURCE_KEY])).toEqual(['deezer', 'itunes']);
    expect(found[0].source).toBe('deezer');
    expect(updates.at(-1)).toBe(2);
  });
});

describe('the Fix dialog', () => {
  it('shows every source with chips, and saves the pick completed from its own source', async () => {
    const posts: { url: string; body: Record<string, unknown> }[] = [];
    vi.stubGlobal(
      'fetch',
      vi.fn(async (url: string, init?: RequestInit) => {
        if (init?.method === 'POST') {
          const body = JSON.parse(init.body as string) as Record<string, unknown>;
          posts.push({ url, body });
          if (url.includes('fix-pick/complete')) {
            return new Response(
              JSON.stringify({
                track: {
                  ...(body.track as object),
                  track_number: 4,
                  album: { name: 'Idol', id: '9' },
                },
              }),
            );
          }
          return new Response(JSON.stringify({ success: true }));
        }
        const tracks = url.includes('/deezer/')
          ? [{ id: 'd1', name: 'Idol', artists: ['YOASOBI'], album: 'Idol', source: 'deezer' }]
          : url.includes('/itunes/')
            ? [{ id: 'i1', name: 'Idol', artists: ['YOASOBI'], album: 'Idol' }]
            : [];
        return new Response(JSON.stringify({ tracks }));
      }),
    );
    window.showConfirmDialog = vi.fn(async () => true);
    const onFixed = vi.fn();
    render(
      <FixModal
        config={SYNC_SOURCES.youtube}
        sourceId="y1"
        row={ROW}
        onClose={() => {}}
        onFixed={onFixed}
      />,
    );
    await waitFor(() => expect(document.querySelectorAll('.fix-result-card')).toHaveLength(2));
    expect(screen.getByText('All 2')).toBeTruthy();
    fireEvent.click(screen.getByText('iTunes 1'));
    expect(document.querySelectorAll('.fix-result-card')).toHaveLength(1);
    fireEvent.click(document.querySelector('.fix-result-card') as Element);
    await waitFor(() => expect(onFixed).toHaveBeenCalled());
    const save = posts.find((p) => p.url.includes('/discovery/update_match'));
    expect(save).toBeDefined();
    expect((save!.body.spotify_track as Record<string, unknown>).track_number).toBe(4);
    expect(onFixed.mock.calls[0][1].album).toEqual({ name: 'Idol', id: '9' });
  });
});
