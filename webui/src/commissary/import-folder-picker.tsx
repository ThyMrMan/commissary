import { useQuery, useQueryClient } from '@tanstack/react-query';
import { useState, useSyncExternalStore } from 'react';

import { apiClient, readJson } from '@/app/api-client';
import { DialogBody, DialogFooter, DialogFrame, DialogHeader } from '@/components/dialog';
import { Button } from '@/components/form/form';
import { IMPORT_QUERY_KEY } from '@/routes/import/-import.api';

import { getImportFolder, setImportFolder, subscribeImportFolder } from './import-folder';
import styles from './import-folder-picker.module.css';

/**
 * Commissary: scan any of your download folders on the Import page, not only
 * the Import folder (core/commissary/import_folders.py). The choice changes
 * what every Import query asks for, so the Import cache is dropped with it -
 * otherwise the previous folder's files would show under the new name.
 */

interface BrowsePayload {
  path: string | null;
  parent: string | null;
  folders: { name: string; path: string }[];
  audio_files?: number;
  total_audio?: number;
  truncated?: boolean;
  shortcuts: { label: string; path: string }[];
  error?: string;
}

async function browse(path: string | null): Promise<BrowsePayload> {
  return readJson<BrowsePayload>(
    apiClient.get('commissary/import/browse', {
      searchParams: path ? { path, t: Date.now() } : { t: Date.now() },
    }),
  );
}

export function useImportFolder(): string | null {
  return useSyncExternalStore(subscribeImportFolder, getImportFolder, getImportFolder);
}

export function CommissaryImportFolder() {
  const folder = useImportFolder();
  const [open, setOpen] = useState(false);
  const queryClient = useQueryClient();

  const choose = (next: string | null) => {
    setImportFolder(next);
    // reset, not invalidate: the old folder's rows must not show under the new
    // name while the refetch runs
    void queryClient.resetQueries({ queryKey: IMPORT_QUERY_KEY });
    setOpen(false);
  };

  return (
    <span className={styles.wrap} data-commissary-import-folder>
      <Button
        variant="ghost"
        size="sm"
        onClick={() => setOpen(true)}
        id="commissary-import-folder-btn"
      >
        {folder ? 'Change folder' : 'Scan another folder'}
      </Button>
      {folder ? (
        <Button
          variant="ghost"
          size="sm"
          onClick={() => choose(null)}
          id="commissary-import-folder-reset"
        >
          Back to Import folder
        </Button>
      ) : null}
      {open ? (
        <FolderDialog start={folder} onClose={() => setOpen(false)} onChoose={choose} />
      ) : null}
    </span>
  );
}

function FolderDialog({
  start,
  onClose,
  onChoose,
}: {
  start: string | null;
  onClose: () => void;
  onChoose: (path: string | null) => void;
}) {
  const [path, setPath] = useState<string | null>(start);
  const query = useQuery({
    queryKey: ['commissary-import-browse', path],
    queryFn: () => browse(path),
    retry: false,
  });
  const data = query.data;
  const error = query.error instanceof Error ? query.error.message : null;

  return (
    <DialogFrame open onOpenChange={(next) => !next && onClose()} className={styles.popup}>
      <DialogHeader title="Scan a download folder">
        <span className={styles.current} title={data?.path ?? undefined}>
          {data?.path ?? 'Your Import folder and download folders'}
        </span>
      </DialogHeader>
      <DialogBody>
        <div className={styles.shortcuts}>
          {(data?.shortcuts ?? []).map((shortcut) => (
            <button
              key={shortcut.path}
              type="button"
              className={styles.shortcut}
              data-active={shortcut.path === data?.path || undefined}
              title={shortcut.path}
              onClick={() => setPath(shortcut.path)}
            >
              {shortcut.label}
            </button>
          ))}
        </div>
        {error ? <p className={styles.error}>{error}</p> : null}
        {query.isLoading ? <p className={styles.note}>Reading…</p> : null}
        {data?.path ? (
          <ul className={styles.list} aria-label="Folders">
            {data.parent ? (
              <li>
                <button type="button" className={styles.row} onClick={() => setPath(data.parent)}>
                  ↑ Up
                </button>
              </li>
            ) : null}
            {data.folders.map((entry) => (
              <li key={entry.path}>
                <button type="button" className={styles.row} onClick={() => setPath(entry.path)}>
                  📁 {entry.name}
                </button>
              </li>
            ))}
            {!data.folders.length ? <li className={styles.note}>No folders inside</li> : null}
          </ul>
        ) : (
          !query.isLoading && <p className={styles.note}>Pick a folder above.</p>
        )}
        {data?.truncated ? <p className={styles.note}>Showing the first 500 folders.</p> : null}
      </DialogBody>
      <DialogFooter>
        <span className={styles.note}>
          {data?.path
            ? `${data.total_audio ?? 0} audio file${data.total_audio === 1 ? '' : 's'} in this folder and below`
            : ''}
        </span>
        <Button variant="ghost" onClick={onClose}>
          Cancel
        </Button>
        <Button
          variant="primary"
          disabled={!data?.path}
          id="commissary-import-folder-use"
          onClick={() => onChoose(data?.path ?? null)}
        >
          Scan this folder
        </Button>
      </DialogFooter>
    </DialogFrame>
  );
}
