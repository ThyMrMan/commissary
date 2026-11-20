/**
 * Commissary: the download folder boxes accept several paths ("a; b",
 * core/commissary/download_paths.py). A setting saved by the old fork can still
 * be a JSON list, which upstream's text() would show as an empty box; join it
 * the way the box writes it so saving it back keeps every path.
 */
export function pathsText(value: unknown): string {
  if (Array.isArray(value)) {
    return value
      .filter((v) => typeof v === 'string' && v.trim())
      .map((v: string) => v.trim())
      .join('; ');
  }
  return typeof value === 'string' || typeof value === 'number' ? String(value) : '';
}
