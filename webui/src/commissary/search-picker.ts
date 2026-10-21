import { profileAsksFirst } from '@/platform/shell/download-rights';

import type { CommissaryInspectTrack } from './track-inspector';

/**
 * Commissary: a track clicked on the Search page opens the every-source picker
 * first (old fork 1.9.8).
 *
 * Upstream opens a one-track download window whose Begin Analysis downloads
 * automatically from the first source that answers. The picker (the Candidate
 * Inspector over every source, commissary/track-inspector.ts) now opens on top
 * of that window; closing it leaves the window, so the automatic download is
 * still one click away. A profile that asks instead of downloading keeps
 * upstream's request flow.
 */
export function openPickerFirst(track: CommissaryInspectTrack): void {
  if (profileAsksFirst()) return;
  window.openCommissaryTrackInspector?.(track);
}
