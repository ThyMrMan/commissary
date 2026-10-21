import type { BasicSource } from '@/routes/search/-basic.types';

import { sourceLabel } from '@/routes/search/-basic.api';

/**
 * Commissary: the Search page's file-source options.
 *
 * Every configured download source is searchable (core/commissary/search_sources.py),
 * not only the download order. The order still leads; sources outside it are
 * grouped below so it's clear they are not what the automatic downloads try.
 */
export function CommissarySourceOptions({ sources }: { sources: BasicSource[] }) {
  const option = (source: BasicSource) => (
    <option key={source.name} value={source.name}>
      {sourceLabel(source)}
    </option>
  );
  const chain = sources.filter((source) => source.in_chain !== false);
  const others = sources.filter((source) => source.in_chain === false);
  if (!chain.length || !others.length) return <>{sources.map(option)}</>;
  return (
    <>
      <optgroup label="Your download order">{chain.map(option)}</optgroup>
      <optgroup label="Other sources">{others.map(option)}</optgroup>
    </>
  );
}
