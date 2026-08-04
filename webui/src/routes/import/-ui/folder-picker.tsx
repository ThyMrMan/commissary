import { useQuery } from '@tanstack/react-query';
import { useEffect, useState } from 'react';

import { Button } from '@/components/form/form';
import { Show } from '@/components/primitives';

import { importBrowseQueryOptions } from '../-import.api';
import { useImportScanFolder } from '../-import.store';
import styles from './import-page.module.css';

/** Import used to read one hard-coded folder, so anything sitting where a
 * download client left it had to be moved by hand before it could be imported.
 * This browses the server's configured download / import / library roots and
 * lets the scan point at any folder inside them.
 *
 * The roots are the boundary, not a convenience: the scan endpoints read tags
 * out of every file they find, so they refuse a path outside them. The browser
 * therefore only offers "up" while the parent stays inside a root — walking the
 * user somewhere the next request will reject would be a dead end that looks
 * like a bug.
 */
export function ImportFolderPicker({ onClose }: { onClose: () => void }) {
  const { scanPath, setScanPath } = useImportScanFolder();
  // Start where the scan currently is, so the picker opens in context rather
  // than sending the user back to the top every time.
  const [browsePath, setBrowsePath] = useState(scanPath);
  const browseQuery = useQuery(importBrowseQueryOptions(browsePath));
  const data = browseQuery.data;

  // Esc closes — a modal that can only be dismissed by hitting the one small
  // button is a nuisance on a page you open repeatedly.
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') onClose();
    };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [onClose]);

  const choose = (path: string) => {
    setScanPath(path);
    onClose();
  };

  const dirs = data?.dirs ?? [];
  const audioCount = data?.audio_count ?? 0;
  const currentPath = data?.path ?? browsePath;

  return (
    <div
      className={styles.folderPickerOverlay}
      data-testid="import-folder-picker"
      onClick={(e) => {
        if (e.target === e.currentTarget) onClose();
      }}
    >
      <div className={styles.folderPickerPanel} role="dialog" aria-label="Choose import folder">
        <div className={styles.folderPickerHeader}>
          <h2>Choose a folder to import from</h2>
          <button type="button" className={styles.folderPickerClose} onClick={onClose}>
            ✕
          </button>
        </div>

        <Show when={(data?.shortcuts?.length ?? 0) > 0}>
          <div className={styles.folderPickerShortcuts}>
            {(data?.shortcuts ?? []).map((s) => (
              <button
                key={s.path}
                type="button"
                className={styles.folderPickerShortcut}
                onClick={() => setBrowsePath(s.path)}
              >
                {s.label}
              </button>
            ))}
          </div>
        </Show>

        <div className={styles.folderPickerPath} title={currentPath}>
          {currentPath || '—'}
        </div>

        <Show when={browseQuery.isLoading}>
          <div className={styles.folderPickerStatus}>Reading folder…</div>
        </Show>

        <Show when={Boolean(data?.error)}>
          <div className={styles.folderPickerError}>{data?.error}</div>
        </Show>

        <Show when={browseQuery.isError}>
          <div className={styles.folderPickerError}>Couldn&apos;t read that folder.</div>
        </Show>

        <Show when={!browseQuery.isLoading && !data?.error}>
          <ul className={styles.folderPickerList}>
            <Show when={Boolean(data?.parent)}>
              <li>
                <button
                  type="button"
                  className={styles.folderPickerRow}
                  onClick={() => setBrowsePath(data?.parent ?? '')}
                >
                  ⬆ ..
                </button>
              </li>
            </Show>
            {dirs.map((d) => (
              <li key={d.path}>
                <button
                  type="button"
                  className={styles.folderPickerRow}
                  onClick={() => setBrowsePath(d.path)}
                >
                  📁 {d.name}
                </button>
              </li>
            ))}
            <Show when={dirs.length === 0}>
              <li className={styles.folderPickerEmpty}>No subfolders here.</li>
            </Show>
          </ul>
        </Show>

        <Show when={Boolean(data?.truncated)}>
          <div className={styles.folderPickerStatus}>
            Only the first 1000 entries are shown — open a subfolder to narrow it down.
          </div>
        </Show>

        <div className={styles.folderPickerFooter}>
          {/* Say what's actually here before they commit. A folder with no audio
              directly in it can still be the right choice — the scan walks
              subfolders — so this informs rather than blocks. */}
          <span className={styles.folderPickerCount}>
            {audioCount > 0
              ? `${audioCount} audio file${audioCount === 1 ? '' : 's'} directly in this folder`
              : 'No audio files directly in this folder (subfolders are still scanned)'}
          </span>
          <div className={styles.folderPickerActions}>
            <Show when={scanPath !== ''}>
              <Button variant="secondary" onClick={() => choose('')}>
                Back to Import folder
              </Button>
            </Show>
            <Button
              variant="primary"
              disabled={!currentPath || Boolean(data?.error)}
              onClick={() => choose(currentPath)}
            >
              Scan this folder
            </Button>
          </div>
        </div>
      </div>
    </div>
  );
}
