import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';

import { apiClient } from '@/app/api-client';

import { commissaryImportFolderHook, getImportFolder, setImportFolder } from './import-folder';
import { CommissaryImportFolder } from './import-folder-picker';

afterEach(() => {
  cleanup();
  setImportFolder(null);
  vi.unstubAllGlobals();
});

function hook(url: string) {
  const out = commissaryImportFolderHook({ request: new Request(url) } as never);
  return out ? new URL(out.url) : null;
}

describe('the scan_path hook', () => {
  it('adds the chosen folder to Import requests only', () => {
    setImportFolder('/dl/complete/Music');
    expect(hook('http://x/api/import/inbox')?.searchParams.get('scan_path')).toBe(
      '/dl/complete/Music',
    );
    expect(hook('http://x/api/import/album/match?a=1')?.searchParams.get('a')).toBe('1');
    expect(hook('http://x/api/import/upload/chunk')).toBeNull();
    expect(hook('http://x/api/library/artists')).toBeNull();
  });

  it('does nothing while the Import folder is in use', () => {
    expect(hook('http://x/api/import/inbox')).toBeNull();
  });

  it('is installed on the shared api client', async () => {
    const urls: string[] = [];
    vi.stubGlobal(
      'fetch',
      vi.fn(async (input: Request) => {
        urls.push(input.url);
        return new Response('{}', { headers: { 'content-type': 'application/json' } });
      }),
    );
    setImportFolder('/dl/a');
    await apiClient.get('import/inbox').json();
    expect(new URL(urls[0]).searchParams.get('scan_path')).toBe('/dl/a');
  });
});

describe('the folder picker', () => {
  it('browses the roots and scans the chosen folder, dropping the old cache', async () => {
    const root = { label: 'Torrents', path: '/dl/complete' };
    vi.stubGlobal(
      'fetch',
      vi.fn(async (input: Request) => {
        const path = new URL(input.url).searchParams.get('path');
        const body = path
          ? {
              path,
              parent: null,
              folders: [{ name: 'Music', path: '/dl/complete/Music' }],
              total_audio: 12,
              shortcuts: [root],
            }
          : { path: null, parent: null, folders: [], shortcuts: [root] };
        return new Response(JSON.stringify(body), {
          headers: { 'content-type': 'application/json' },
        });
      }),
    );
    const client = new QueryClient();
    client.setQueryData(['import', 'inbox'], { staging_path: '/old' });
    render(
      <QueryClientProvider client={client}>
        <CommissaryImportFolder />
      </QueryClientProvider>,
    );
    fireEvent.click(screen.getByText('Scan another folder'));
    fireEvent.click(await screen.findByText('Torrents'));
    await screen.findByText('12 audio files in this folder and below');
    fireEvent.click(screen.getByText('Scan this folder'));
    await waitFor(() => expect(getImportFolder()).toBe('/dl/complete'));
    expect(client.getQueryData(['import', 'inbox'])).toBeUndefined();
    fireEvent.click(screen.getByText('Back to Import folder'));
    expect(getImportFolder()).toBeNull();
  });
});
