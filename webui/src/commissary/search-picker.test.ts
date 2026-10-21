import { afterEach, describe, expect, it, vi } from 'vitest';

import type { SearchTrack } from '@/routes/search/-search.types';

import { openSearchTrack } from '@/routes/search/-search.actions';

type Win = Record<string, unknown>;

afterEach(() => {
  for (const key of [
    'openCommissaryTrackInspector',
    'openDownloadMissingModalForArtistAlbum',
    'reopenActiveDownloadModal',
    'canDownload',
  ]) {
    delete (window as unknown as Win)[key];
  }
});

const TRACK = {
  id: 't1',
  name: 'Idol',
  artist: 'YOASOBI',
  artists: ['YOASOBI'],
  album: 'Idol',
  duration_ms: 213000,
} as SearchTrack;

function stub(canDownload: boolean, running = false) {
  const w = window as unknown as Win;
  const inspector = vi.fn();
  const open = vi.fn(async () => {});
  w.openCommissaryTrackInspector = inspector;
  w.openDownloadMissingModalForArtistAlbum = open;
  w.reopenActiveDownloadModal = () => running;
  w.canDownload = () => canDownload;
  return { inspector, open };
}

describe('a Search page track click', () => {
  it('opens the download window, then the every-source picker on top', async () => {
    const { inspector, open } = stub(true);
    await openSearchTrack(TRACK);
    expect(open).toHaveBeenCalledOnce();
    expect(inspector).toHaveBeenCalledOnce();
    const picked = inspector.mock.calls[0][0] as Record<string, unknown>;
    expect([picked.name, picked.artists, picked.duration_ms]).toEqual([
      'Idol',
      ['YOASOBI'],
      213000,
    ]);
    expect((picked.album as { name: string }).name).toBe('Idol');
  });

  it('leaves a profile that requests, and a running window, as upstream has them', async () => {
    const asks = stub(false);
    await openSearchTrack(TRACK);
    expect(asks.open).toHaveBeenCalledOnce();
    expect(asks.inspector).not.toHaveBeenCalled();

    const running = stub(true, true);
    await openSearchTrack(TRACK);
    expect(running.open).not.toHaveBeenCalled();
    expect(running.inspector).not.toHaveBeenCalled();
  });
});
