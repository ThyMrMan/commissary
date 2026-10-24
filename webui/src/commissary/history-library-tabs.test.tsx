import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import type { VdlHistoryPage } from '@/routes/active-downloads/-vdl.api';

import { VdlHistoryModal } from '@/routes/active-downloads/-ui/vdl-history';

const fetchVideoHistory = vi.fn();

vi.mock('@/routes/active-downloads/-vdl.api', async (importOriginal) => {
  const orig = await importOriginal<typeof import('@/routes/active-downloads/-vdl.api')>();
  return { ...orig, fetchVideoHistory: (...args: unknown[]) => fetchVideoHistory(...args) };
});

const PAGE: VdlHistoryPage = {
  items: [{ id: 1, kind: 'movie', title: 'Akira' }],
  counts: { movie: 3, show: 5, youtube: 0, total: 8 },
  totalPages: 1,
};

const TABS = {
  movie: [
    { id: 3, label: 'Movies', count: 2 },
    { id: 4, label: 'Anime-Movies', count: 1 },
  ],
  show: [{ id: 2, label: 'TV-Shows', count: 5 }],
};

beforeEach(() => {
  fetchVideoHistory.mockReset();
  fetchVideoHistory.mockResolvedValue(PAGE);
  vi.stubGlobal(
    'fetch',
    vi.fn(async (input: Request | string) => {
      const url = typeof input === 'string' ? input : input.url;
      return new Response(JSON.stringify(url.includes('scope=history') ? TABS : {}), {
        headers: { 'Content-Type': 'application/json' },
      });
    }),
  );
});

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

const lastLibrary = () => fetchVideoHistory.mock.calls.at(-1)?.[4];

describe('Download History Library tabs', () => {
  it("narrows a kind to one Library, and keeps each kind's pick", async () => {
    render(<VdlHistoryModal open onClose={() => {}} onCountChange={() => {}} />);
    // the open load, then the search box's delayed first load: let both land
    // before clicking, or the late one (for the All tab) reads as the last call
    await waitFor(() => expect(fetchVideoHistory).toHaveBeenCalledTimes(2));
    expect(document.querySelector('[data-commissary-history-lib]')).toBeNull(); // All: none

    fireEvent.click(screen.getByRole('tab', { name: /^Movies 3$/ }));
    expect(await screen.findByRole('tab', { name: 'All Movies 3' })).toBeTruthy();
    expect(screen.getByRole('tab', { name: 'Anime-Movies 1' })).toBeTruthy();

    fireEvent.click(screen.getByRole('tab', { name: 'Anime-Movies 1' }));
    await waitFor(() => expect(lastLibrary()).toBe(4));
    expect(fetchVideoHistory.mock.calls.at(-1)?.[0]).toBe('movie');

    // one TV Library: nothing to split, and TV asks for no Library
    fireEvent.click(screen.getByRole('tab', { name: /^TV 5$/ }));
    await waitFor(() => expect(fetchVideoHistory.mock.calls.at(-1)?.[0]).toBe('show'));
    expect(lastLibrary()).toBeNull();
    expect(document.querySelector('[data-commissary-history-lib]')).toBeNull();

    fireEvent.click(screen.getByRole('tab', { name: /^Movies 3$/ }));
    await waitFor(() => expect(lastLibrary()).toBe(4));
    fireEvent.click(screen.getByRole('tab', { name: 'All Movies 3' }));
    await waitFor(() => expect(lastLibrary()).toBeNull());
  });
});
