import type { BeforeRequestState } from 'ky';

/**
 * Commissary: which folder the Import page scans (core/commissary/import_folders.py).
 *
 * null is upstream's Import folder. Any other value rides along on every
 * /api/import/ request as `scan_path`, so the page's existing queries scan it
 * without each one knowing - except uploads, which always land in the Import
 * folder. The server only honours a folder inside the download / Import roots.
 */

let current: string | null = null;
const listeners = new Set<() => void>();

export function getImportFolder(): string | null {
  return current;
}

export function setImportFolder(folder: string | null): void {
  const next = folder || null;
  if (next === current) return;
  current = next;
  for (const listener of listeners) listener();
}

export function subscribeImportFolder(listener: () => void): () => void {
  listeners.add(listener);
  return () => listeners.delete(listener);
}

const IMPORT_PATH = /\/api\/import\//;
const UPLOAD_PATH = /\/api\/import\/upload/;

/** ky beforeRequest hook: adds `scan_path` to Import requests while a folder is chosen. */
export function commissaryImportFolderHook({ request }: BeforeRequestState): Request | void {
  if (!current) return;
  const url = new URL(request.url);
  if (!IMPORT_PATH.test(url.pathname) || UPLOAD_PATH.test(url.pathname)) return;
  url.searchParams.set('scan_path', current);
  return new Request(url.toString(), request);
}
