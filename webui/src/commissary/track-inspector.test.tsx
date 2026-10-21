import { act, cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';

import {
  CandidateInspectorHost,
  mountCandidateInspectorHost,
} from '@/features/downloads/inspector-modal';

import { openCommissaryTrackInspector } from './track-inspector';

function ndjson(lines: unknown[]) {
  return new Response(lines.map((l) => `${JSON.stringify(l)}\n`).join(''));
}

const HIT = {
  display_name: 'Idol.flac',
  filename: 'YOASOBI/THE BOOK 3/01 Idol.flac',
  username: 'peer',
  confidence: 0.97,
  size: 30_000_000,
  size_display: '28.6 MB',
  quality: 'FLAC',
  decision: { accepted: true, code: 'accepted', stage: 'decision', detail: '', score: 0.97 },
};

const TRACK = {
  name: 'Idol',
  artists: [{ name: 'YOASOBI' }],
  album: { name: 'THE BOOK 3' },
  duration_ms: 213000,
};

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

describe('the inspector for a download-window track', () => {
  it('is installed where upstream mounts its inspector host', () => {
    delete window.openCommissaryTrackInspector;
    document.getElementById('candidate-inspector-root')?.remove();
    mountCandidateInspectorHost();
    expect(window.openCommissaryTrackInspector).toBe(openCommissaryTrackInspector);
    document.getElementById('candidate-inspector-root')?.remove();
  });

  it('searches with the posted track and starts the picked file', async () => {
    const calls: { url: string; body: Record<string, unknown> }[] = [];
    vi.stubGlobal(
      'fetch',
      vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
        const url = input instanceof Request ? input.url : input.toString();
        calls.push({ url, body: JSON.parse(typeof init?.body === 'string' ? init.body : '{}') });
        if (url === '/api/commissary/inspect') {
          return ndjson([{ source: 'soulseek', candidates: [HIT] }, { done: true }]);
        }
        return new Response(JSON.stringify({ success: true, batch_id: 'b', task_id: 't' }));
      }),
    );
    render(<CandidateInspectorHost />);
    act(() => openCommissaryTrackInspector(TRACK));
    await screen.findByText('Idol.flac');
    fireEvent.click(screen.getAllByText('Download Selected')[0]);
    await waitFor(() =>
      expect(calls.some((c) => c.url === '/api/commissary/inspect/download')).toBe(true),
    );
    expect(calls.find((c) => c.url === '/api/commissary/inspect')?.body).toEqual({ track: TRACK });
    expect(calls.find((c) => c.url === '/api/commissary/inspect/download')?.body).toMatchObject({
      track: TRACK,
      candidate: { username: 'peer', filename: HIT.filename },
    });
  });
});
