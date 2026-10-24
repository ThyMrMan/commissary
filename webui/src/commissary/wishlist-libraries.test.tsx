import { createMemoryHistory } from '@tanstack/react-router';
import { cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import type { VideoMovieItem, VideoShowItem } from '@/routes/wishlist/-video.types';

import { AppRouterProvider, createAppRouter } from '@/app/router';
import { createTestQueryClient } from '@/test/query-client';
import { createShellBridge } from '@/test/shell-bridge';

/**
 * The fork's video Libraries on upstream's React wishlist, through the real
 * route: the Library tab row narrows the list, and the side panel's Library
 * picker files the next grab.
 */

const res = (body: unknown) =>
  new Response(JSON.stringify(body), { headers: { 'Content-Type': 'application/json' } });

const MOVIES: VideoMovieItem[] = [
  { kind: 'movie', tmdb_id: 11, title: 'Heat', status: 'wanted', library_id: null },
  { kind: 'movie', tmdb_id: 12, title: 'Akira', status: 'wanted', library_id: null },
  { kind: 'movie', tmdb_id: 13, title: 'Owned One', status: 'wanted', library_id: 5 },
];
const SHOWS: VideoShowItem[] = [
  {
    kind: 'show',
    tmdb_id: 31,
    title: 'Nana',
    library_id: null,
    wanted: 1,
    done: 0,
    seasons: [{ season_number: 1, episodes: [{ episode_number: 1, status: 'wanted' }] }],
  },
];

let calls: { method: string; path: string; params: Record<string, string>; body: unknown }[];
let assigned: Record<string, number>;

function stubServer() {
  calls = [];
  assigned = { 12: 2 };
  vi.stubGlobal(
    'fetch',
    vi.fn(async (input: RequestInfo | URL) => {
      const req = input instanceof Request ? input : new Request(String(input));
      const url = new URL(req.url);
      const path = url.pathname.replace(/^\/api\//, '');
      const body = req.method === 'GET' ? null : ((await req.clone().json()) as unknown);
      calls.push({ method: req.method, path, params: Object.fromEntries(url.searchParams), body });
      if (path === 'video/wishlist/counts')
        return res({ success: true, movie: 3, show: 1, episode: 1, total: 4, video: 0 });
      if (path === 'video/wishlist') {
        const lib = url.searchParams.get('library');
        const items =
          url.searchParams.get('kind') === 'show'
            ? SHOWS
            : lib === '2'
              ? MOVIES.filter((m) => m.tmdb_id === 12)
              : MOVIES;
        return res({
          success: true,
          items,
          pagination: {
            page: 1,
            total_pages: 1,
            total_count: items.length,
            has_prev: false,
            has_next: false,
          },
        });
      }
      if (path === 'video/commissary/library-tabs')
        return res({
          movie: [
            { id: 1, label: 'Movies', count: 2 },
            { id: 2, label: 'Anime-Movies', count: 1 },
          ],
          show: [{ id: 3, label: 'TV', count: 1 }],
        });
      if (path === 'video/libraries/assignments')
        return res({
          choices: {
            movie: [
              { id: 1, label: 'Movies' },
              { id: 2, label: 'Anime-Movies' },
            ],
            show: [{ id: 3, label: 'TV' }],
          },
          wishlist: { movie: { ...assigned }, show: {} },
          watchlist: {},
        });
      if (path === 'video/libraries/assign/wishlist') {
        const b = body as { tmdb_id: number; root_folder_id: number | null };
        if (b.root_folder_id == null) delete assigned[b.tmdb_id];
        else assigned[b.tmdb_id] = b.root_folder_id;
        return res({ status: 'saved', updated: 1 });
      }
      if (path.startsWith('video/wishlist/backfill')) return res({ success: true, updated: 0 });
      if (path === 'active-processes') return res({ active_processes: [] });
      return res({});
    }),
  );
}

function renderRoute(entry: string) {
  const queryClient = createTestQueryClient();
  const router = createAppRouter({
    history: createMemoryHistory({ initialEntries: [entry] }),
    queryClient,
  });
  render(<AppRouterProvider router={router} queryClient={queryClient} />);
}

async function findEl(selector: string): Promise<Element> {
  return waitFor(() => {
    const el = document.querySelector(selector);
    if (!el) throw new Error(`not yet: ${selector}`);
    return el;
  });
}

beforeEach(() => {
  window.SoulSyncWebShellBridge = createShellBridge();
  window.showToast = vi.fn();
  window.sessionStorage.clear();
  stubServer();
});

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
  delete window.SoulSyncWebShellBridge;
});

const lastList = () => calls.filter((c) => c.path === 'video/wishlist').at(-1)?.params;

describe('Library tabs', () => {
  it('narrows Movies to one Library and back', async () => {
    renderRoute('/wishlist?media=movies');
    const row = await screen.findByRole('tablist', { name: 'Libraries' });
    expect(
      within(row)
        .getAllByRole('tab')
        .map((t) => t.textContent),
    ).toEqual(['All Movies3', 'Movies2', 'Anime-Movies1']);
    expect(lastList()?.library).toBeUndefined();

    fireEvent.click(within(row).getByRole('tab', { name: /Anime-Movies/ }));
    await waitFor(() => expect(lastList()?.library).toBe('2'));
    await waitFor(() => expect(document.querySelectorAll('.wlp-poster')).toHaveLength(1));
    expect(within(row).getByRole('tab', { name: /Anime-Movies/ })).toHaveAttribute(
      'aria-selected',
      'true',
    );

    // back to every Library (the list it already had, so no new request)
    fireEvent.click(within(row).getByRole('tab', { name: /All Movies/ }));
    await waitFor(() => expect(document.querySelectorAll('.wlp-poster')).toHaveLength(3));
  });

  it('shows no row for a kind with one Library', async () => {
    renderRoute('/wishlist?media=tv');
    await findEl('[data-show="31"]');
    expect(screen.queryByRole('tablist', { name: 'Libraries' })).toBeNull();
    expect(lastList()?.library).toBeUndefined();
  });
});

describe('Library picker', () => {
  it("shows the title's choice and files the next grab elsewhere", async () => {
    renderRoute('/wishlist?media=movies');
    fireEvent.click(await findEl('[data-tmdb="12"]'));
    const select = (await screen.findByRole('combobox', { name: 'Library' })) as HTMLSelectElement;
    await waitFor(() => expect(select.value).toBe('2'));
    expect([...select.options].map((o) => o.text)).toEqual([
      'Default (Movies)',
      'Movies',
      'Anime-Movies',
    ]);

    fireEvent.change(select, { target: { value: '' } });
    await waitFor(() =>
      expect(calls.filter((c) => c.path === 'video/libraries/assign/wishlist')).toHaveLength(1),
    );
    expect(calls.find((c) => c.path === 'video/libraries/assign/wishlist')?.body).toEqual({
      kind: 'movie',
      tmdb_id: 12,
      root_folder_id: null,
    });
    await waitFor(() => expect(select.value).toBe(''));
  });

  it('is not offered for an owned title, a one-Library kind, or a member', async () => {
    renderRoute('/wishlist?media=movies');
    fireEvent.click(await findEl('[data-tmdb="13"]'));
    await screen.findByText('Remove from wishlist');
    expect(document.querySelector('[data-commissary-wishlist-picker]')).toBeNull();
    cleanup();

    renderRoute('/wishlist?media=tv');
    fireEvent.click(await findEl('[data-show="31"] .wl-orb'));
    await screen.findByText('Remove show from wishlist');
    expect(document.querySelector('[data-commissary-wishlist-picker]')).toBeNull();
    cleanup();

    window.SoulSyncWebShellBridge = createShellBridge({
      getCurrentProfileContext: vi.fn(() => ({ profileId: 3, isAdmin: false })),
    });
    renderRoute('/wishlist?media=movies');
    fireEvent.click(await findEl('[data-tmdb="12"]'));
    await screen.findByText('Remove from wishlist');
    expect(document.querySelector('[data-commissary-wishlist-picker]')).toBeNull();
    expect(calls.some((c) => c.path === 'video/libraries/assignments')).toBe(true); // the admin runs
  });
});
