import { useCallback, useEffect, useRef, useState } from 'react';

import { apiClient, readJson } from '@/app/api-client';

/**
 * Commissary: video Library tabs in the Download History (Phase 2 item 12).
 *
 * Like the Library page and the Wishlist (commissary-library-tabs.js): a kind
 * with two or more video Libraries (Movies + Anime-Movies, TV + Anime) gets a
 * second row under the History's own kind tabs - "All Movies", then each
 * Library with its count of completed grabs. Picking one narrows the list
 * (?library=<id> on /api/video/downloads/history). A grab belongs to the
 * Library its file landed in, else its title's Library
 * (core/commissary/video_libraries.history_library_sql). A kind with one
 * Library shows nothing new; YouTube has no Libraries.
 */

export interface HistoryLibrary {
  id: number;
  label: string;
  count: number;
}

type Tabs = Partial<Record<string, HistoryLibrary[]>>;

const ALL_LABEL: Record<string, string> = { movie: 'All Movies', show: 'All TV' };

export function useHistoryLibraries(open: boolean) {
  const [tabs, setTabs] = useState<Tabs>({});
  const [picked, setPicked] = useState<Record<string, number | null>>({});
  // read by the modal's load(), which is memoised once
  const pickedRef = useRef(picked);
  pickedRef.current = picked;

  const refresh = useCallback(async () => {
    try {
      const data = await readJson<Tabs>(
        apiClient.get('video/commissary/library-tabs', {
          searchParams: { scope: 'history', t: Date.now() },
        }),
      );
      if (data && typeof data === 'object') setTabs(data);
    } catch {
      setTabs({});
    }
  }, []);

  useEffect(() => {
    if (open) void refresh();
  }, [open, refresh]);

  const libraryFor = useCallback((tab: string): number | null => {
    return pickedRef.current[tab] ?? null;
  }, []);

  const pick = (tab: string, id: number | null) => {
    const next = { ...pickedRef.current, [tab]: id };
    pickedRef.current = next;
    setPicked(next);
  };

  return { tabs, picked, pick, libraryFor, refresh };
}

export type HistoryLibraries = ReturnType<typeof useHistoryLibraries>;

/** The second tab row, in the History's own tab classes. */
export function HistoryLibraryTabs({
  libs,
  tab,
  kindCount,
  onPicked,
}: {
  libs: HistoryLibraries;
  tab: string;
  kindCount: number;
  onPicked: () => void;
}) {
  const list = libs.tabs[tab] ?? [];
  if (list.length < 2) return null;
  const current = libs.picked[tab] ?? null;
  const button = (id: number | null, label: string, count: number) => (
    <button
      key={id ?? 'all'}
      type="button"
      role="tab"
      aria-selected={current === id}
      data-commissary-history-lib={id ?? 'all'}
      className={`vdh-tab${current === id ? ' vdh-tab--on' : ''}`}
      onClick={() => {
        if (current === id) return;
        libs.pick(tab, id);
        onPicked();
      }}
    >
      {label} <span className="vdh-tab-n">{count}</span>
    </button>
  );
  return (
    <div className="vdh-toolbar" style={{ paddingTop: 0 }}>
      <div className="vdh-tabs" role="tablist" aria-label="Libraries">
        {button(null, ALL_LABEL[tab] ?? 'All', kindCount)}
        {list.map((lib) => button(lib.id, lib.label, lib.count))}
      </div>
    </div>
  );
}
