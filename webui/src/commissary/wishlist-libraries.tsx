import { useQuery, useQueryClient } from '@tanstack/react-query';
import { useState } from 'react';

import { apiClient, readJson } from '@/app/api-client';
import { useProfile } from '@/platform/shell/route-controllers';

/**
 * Commissary: video Libraries on the Wishlist (Phase 2 item 12).
 *
 * Upstream 3.5.9 rebuilt the video Wishlist in React; the fork's two pieces of
 * the old page come back here:
 *
 * - Library tabs. A kind with two or more video Libraries (Movies +
 *   Anime-Movies, TV + Anime) gets a second row under the Movies / TV tabs:
 *   "All Movies", then each Library with this profile's wished titles in it.
 *   Picking one narrows the list (?library=<id> on /api/video/wishlist).
 * - A Library picker in a title's side panel: which Library the next grab for
 *   it is filed into (POST /api/video/libraries/assign/wishlist). Admins only
 *   (the routes are admin-only), only for a kind with two or more Libraries,
 *   and only for titles not in the library yet - an owned title's Library is
 *   changed in its Manage panel. Nothing moves on disk.
 */

type Kind = 'movie' | 'show';

interface TabLibrary {
  id: number;
  label: string;
  count: number;
}

interface PickerState {
  choices: Record<Kind, { id: number; label: string }[]>;
  wishlist: Record<Kind, Record<string, number>>;
}

/** The wishlist's media tab as a Library kind; YouTube has no Libraries. */
export function libraryKind(media: string): Kind | null {
  if (media === 'movies') return 'movie';
  if (media === 'tv') return 'show';
  return null;
}

const TABS_KEY = ['commissary', 'wishlist-library-tabs'] as const;
const PICKER_KEY = ['commissary', 'wishlist-library-picker'] as const;

/** The picked Library per kind, and the tab counts. */
export function useWishlistLibraries(media: string) {
  const kind = libraryKind(media);
  const [picked, setPicked] = useState<Partial<Record<Kind, number | null>>>({});
  const tabs = useQuery({
    queryKey: TABS_KEY,
    queryFn: () =>
      readJson<Partial<Record<Kind, TabLibrary[]>>>(
        apiClient.get('video/commissary/library-tabs', { searchParams: { scope: 'wishlist' } }),
      ),
    // wished titles come and go: the counts follow the list's own refresh
    refetchInterval: 30_000,
  });
  const list = (kind && tabs.data?.[kind]) || [];
  // a Library that's gone (or a kind with one) narrows nothing
  const choice = kind ? (picked[kind] ?? null) : null;
  const library = choice != null && list.some((l) => l.id === choice) ? choice : null;
  return {
    kind,
    list,
    library,
    pick: (id: number | null) => {
      if (kind) setPicked((p) => ({ ...p, [kind]: id }));
    },
  };
}

export type WishlistLibraries = ReturnType<typeof useWishlistLibraries>;

const ALL_LABEL: Record<Kind, string> = { movie: 'All Movies', show: 'All TV' };

/** The second tab row, in the wishlist's own tab style. */
export function WishlistLibraryTabs({
  libs,
  kindCount,
}: {
  libs: WishlistLibraries;
  kindCount: number;
}) {
  if (!libs.kind || libs.list.length < 2) return null;
  const tab = (id: number | null, label: string, count: number) => (
    <button
      key={id ?? 'all'}
      type="button"
      role="tab"
      aria-selected={libs.library === id}
      data-commissary-wishlist-lib={id ?? 'all'}
      onClick={() => libs.pick(id)}
    >
      {label}
      <span className="wlp-tab-count">{count}</span>
    </button>
  );
  // upstream's row is inline-flex: a block of its own puts this one under it
  return (
    <div style={{ marginTop: -8 }}>
      <div className="wlp-media-tabs" role="tablist" aria-label="Libraries">
        {tab(null, ALL_LABEL[libs.kind], kindCount)}
        {libs.list.map((l) => tab(l.id, l.label, l.count))}
      </div>
    </div>
  );
}

/** "Library" in a wished title's side panel. */
export function WishlistLibraryPicker({
  kind,
  tmdbId,
  owned,
}: {
  kind: Kind;
  tmdbId: number;
  owned: boolean;
}) {
  const queryClient = useQueryClient();
  const profile = useProfile();
  const admin = Boolean(profile && (profile.isAdmin || profile.profileId === 1));
  const state = useQuery({
    queryKey: PICKER_KEY,
    queryFn: () =>
      readJson<PickerState>(
        // ?t: upstream's fetch-dedupe would hand a re-read right after a save the old answer
        apiClient.get('video/libraries/assignments', { searchParams: { t: Date.now() } }),
      ),
    enabled: admin && !owned,
    retry: false,
  });
  const [saving, setSaving] = useState(false);
  const choices = state.data?.choices?.[kind] ?? [];
  if (!admin || owned || choices.length < 2) return null;
  const current = state.data?.wishlist?.[kind]?.[String(tmdbId)] ?? null;

  const save = async (value: string) => {
    setSaving(true);
    try {
      await readJson(
        apiClient.post('video/libraries/assign/wishlist', {
          json: { kind, tmdb_id: tmdbId, root_folder_id: value ? Number(value) : null },
        }),
      );
      const label = choices.find((c) => String(c.id) === value)?.label;
      window.showToast?.(
        label ? `The next grab goes to ${label}` : 'Back to the default Library',
        'success',
      );
    } catch {
      window.showToast?.("Couldn't change the Library", 'error');
    } finally {
      setSaving(false);
      await queryClient.invalidateQueries({ queryKey: PICKER_KEY });
      await queryClient.invalidateQueries({ queryKey: TABS_KEY });
    }
  };

  return (
    <section className="wlp-sheet-album" data-commissary-wishlist-picker>
      <label className="wlp-sort" title="Which Library the next grab is filed into">
        Library
        <select
          aria-label="Library"
          value={current == null ? '' : String(current)}
          disabled={saving}
          onChange={(event) => void save(event.target.value)}
        >
          <option value="">Default ({choices[0].label})</option>
          {choices.map((c) => (
            <option key={c.id} value={String(c.id)}>
              {c.label}
            </option>
          ))}
        </select>
      </label>
    </section>
  );
}
