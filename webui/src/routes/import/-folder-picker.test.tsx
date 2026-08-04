import { createMemoryHistory } from '@tanstack/react-router';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';

import { AppRouterProvider, createAppRouter } from '@/app/router';
import { HttpResponse, http, server } from '@/test/msw';
import { createTestQueryClient } from '@/test/query-client';
import { createShellBridge } from '@/test/shell-bridge';

import { resetImportWorkflowStore, useImportWorkflowStore } from './-import.store';

/**
 * Import used to read one hard-coded folder, so anything a download client left
 * in place had to be moved by hand first. These cover the folder picker that
 * replaces that — and specifically the couplings that would break QUIETLY:
 * the scan following the chosen folder, the cache not serving one folder's
 * files under another folder's name, and stale matching work being dropped.
 */

function renderImportRoute(initialEntries = ['/import']) {
  const queryClient = createTestQueryClient();
  const history = createMemoryHistory({ initialEntries });
  const router = createAppRouter({ history, queryClient });
  return {
    queryClient,
    ...render(<AppRouterProvider router={router} queryClient={queryClient} />),
  };
}

function stagingUrls() {
  return vi
    .mocked(fetch)
    .mock.calls.map(([input]) => (input instanceof Request ? input.url : String(input)))
    .filter((u) => u.includes('/api/import/staging/files'));
}

describe('import folder picker', () => {
  beforeEach(() => {
    resetImportWorkflowStore();
    window.SoulSyncWebShellBridge = createShellBridge();
    window.showToast = vi.fn();
    vi.spyOn(globalThis, 'fetch');

    server.use(
      http.get('/api/import/staging/files', ({ request }) => {
        const path = new URL(request.url).searchParams.get('path');
        return HttpResponse.json({
          success: true,
          staging_path: path || '/music/Staging',
          files: [],
        });
      }),
      http.get('/api/import/staging/groups', () =>
        HttpResponse.json({ success: true, groups: [] }),
      ),
      http.get('/api/import/staging/suggestions', () =>
        HttpResponse.json({ success: true, albums: [] }),
      ),
      http.get('/api/import/search/sources', () =>
        HttpResponse.json({ success: true, sources: [] }),
      ),
      http.get('/api/import/browse', ({ request }) => {
        const path = new URL(request.url).searchParams.get('path') || '/downloads';
        return HttpResponse.json({
          success: true,
          path,
          parent: path === '/downloads' ? null : '/downloads',
          dirs: path === '/downloads' ? [{ name: 'complete', path: '/downloads/complete' }] : [],
          files: path === '/downloads' ? [] : [{ name: 'a.flac', path: `${path}/a.flac`, size: 1 }],
          shortcuts: [{ label: 'Downloads', path: '/downloads' }],
          audio_count: path === '/downloads' ? 0 : 1,
        });
      }),
    );
  });

  it('opens on a configured root without the user typing a path', async () => {
    renderImportRoute();
    fireEvent.click(await screen.findByRole('button', { name: /change folder/i }));
    expect(await screen.findByTestId('import-folder-picker')).toBeTruthy();
    // The shortcut and the folder it opened on both come from the server.
    await waitFor(() => expect(screen.getByText('Downloads')).toBeTruthy());
    await waitFor(() => expect(screen.getByText('📁 complete')).toBeTruthy());
  });

  it('points the staging scan at the folder you choose', async () => {
    renderImportRoute();
    fireEvent.click(await screen.findByRole('button', { name: /change folder/i }));
    fireEvent.click(await screen.findByText('📁 complete'));
    fireEvent.click(await screen.findByRole('button', { name: /scan this folder/i }));

    await waitFor(() => {
      expect(stagingUrls().some((u) => u.includes('path=%2Fdownloads%2Fcomplete'))).toBe(true);
    });
    expect(useImportWorkflowStore.getState().scanPath).toBe('/downloads/complete');
  });

  it('refetches instead of reusing the previous folder’s scan', async () => {
    /* The query key has to include the folder. Sharing one cache entry would
       show the old folder's files under the new folder's name until something
       happened to invalidate it — which reads as "import is broken", not as a
       caching subtlety. */
    renderImportRoute();
    await waitFor(() => expect(stagingUrls().length).toBeGreaterThan(0));
    const before = stagingUrls().length;

    fireEvent.click(await screen.findByRole('button', { name: /change folder/i }));
    fireEvent.click(await screen.findByText('📁 complete'));
    fireEvent.click(await screen.findByRole('button', { name: /scan this folder/i }));

    await waitFor(() => expect(stagingUrls().length).toBeGreaterThan(before));
  });

  it('says when it is scanning somewhere other than the Import folder', async () => {
    /* Otherwise an empty result from a browsed folder is indistinguishable from
       "my Import folder suddenly broke". */
    renderImportRoute();
    expect(await screen.findByText(/^Import: /)).toBeTruthy();

    fireEvent.click(await screen.findByRole('button', { name: /change folder/i }));
    fireEvent.click(await screen.findByText('📁 complete'));
    fireEvent.click(await screen.findByRole('button', { name: /scan this folder/i }));

    expect(await screen.findByText(/^Scanning: /)).toBeTruthy();
  });

  it('drops matching work that belonged to the old folder', async () => {
    /* The selected album and per-file matches name files in the folder you just
       left. Carrying them across would let you import a match built against
       files that are no longer on screen. */
    useImportWorkflowStore.setState({
      albumQuery: 'old query',
      selectedAlbum: { id: 'x', name: 'Old Album' } as never,
      matchOverrides: { 0: 1 },
      selectedSingles: new Set(['old-file']),
    });

    useImportWorkflowStore.getState().setScanPath('/downloads/complete');

    const state = useImportWorkflowStore.getState();
    expect(state.scanPath).toBe('/downloads/complete');
    expect(state.selectedAlbum).toBeNull();
    expect(state.albumQuery).toBe('');
    expect(state.matchOverrides).toEqual({});
    expect(state.selectedSingles.size).toBe(0);
  });

  it('offers a way back to the configured Import folder', async () => {
    useImportWorkflowStore.getState().setScanPath('/downloads/complete');
    renderImportRoute();
    fireEvent.click(await screen.findByRole('button', { name: /change folder/i }));
    fireEvent.click(await screen.findByRole('button', { name: /back to import folder/i }));

    // Empty means "the configured folder", the same request shape as before
    // the picker existed.
    expect(useImportWorkflowStore.getState().scanPath).toBe('');
    await waitFor(() => expect(screen.getByText(/^Import: /)).toBeTruthy());
  });

  it('does not send a path when no folder has been chosen', async () => {
    /* An install that never opens the picker must issue byte-identical
       requests to the ones it issued before this feature existed. */
    renderImportRoute();
    await waitFor(() => expect(stagingUrls().length).toBeGreaterThan(0));
    expect(stagingUrls().every((u) => !u.includes('path='))).toBe(true);
  });
});
