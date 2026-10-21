import type { FixSearchSource, FixTrack } from '@/routes/sync/-sync.fix';

import { orderedFixSearchSources, searchFixTracks } from '@/routes/sync/-sync.fix';

/**
 * Commissary: the Fix Track Match dialog searches every metadata source at once
 * (old fork 2.3.9).
 *
 * Upstream's dialog tried one source at a time and stopped at the first that
 * returned anything - usually the active one - so a better match on another
 * source was never seen. Here every source is asked in parallel; the answers
 * are merged as they land, ranked by how close the title is to the source
 * track (live, remix, cover and other versions last, then the active source
 * first), and the dialog can narrow the list to one source with its chips.
 *
 * A pick from another source than the main one is completed from its own
 * source before it is saved (POST /api/commissary/fix-pick/complete,
 * core/commissary/fix_pick.py): a download would otherwise look its id up on
 * the main source's client.
 */

export const SOURCE_KEY = '_commissarySource';

export type SourcedTrack = FixTrack & { [SOURCE_KEY]?: string };

const VARIANT =
  /\b(live|remix|remaster|refix|cover|acoustic|demo|instrumental|karaoke|radio edit|single version|soundtrack|from .* (film|movie)|bonus track|sped up|slowed)\b|\b\w+ mix\b/i;

function norm(text: string | number | null | undefined): string {
  return String(text ?? '')
    .toLowerCase()
    .normalize('NFKD')
    .replace(/[̀-ͯ]/g, '')
    .replace(/\([^)]*\)|\[[^\]]*\]/g, ' ')
    .replace(/[^\p{L}\p{N}]+/gu, ' ')
    .trim();
}

function artistNames(track: FixTrack): string[] {
  const artists = track.artists;
  if (Array.isArray(artists)) {
    return artists.map((a) => norm(typeof a === 'object' && a !== null ? a.name : a));
  }
  return [norm(typeof artists === 'string' ? artists : '')];
}

/** Lower is better: title closeness, then artist, then version. */
export function matchScore(track: FixTrack, wantedTitle: string, wantedArtist: string): number {
  const title = norm(track.name);
  const want = norm(wantedTitle);
  let score = 0;
  if (!want) score += 0;
  else if (title === want) score += 0;
  else if (title.includes(want) || want.includes(title)) score += 1;
  else score += 3;
  const artist = norm(wantedArtist);
  if (artist && !artistNames(track).some((a) => a && (a.includes(artist) || artist.includes(a)))) {
    score += 2;
  }
  const versionInWanted = VARIANT.test(wantedTitle);
  if (!versionInWanted && VARIANT.test(String(track.name ?? ''))) score += 4;
  return score;
}

export function rankFixResults(
  tracks: SourcedTrack[],
  wantedTitle: string,
  wantedArtist: string,
  sourceOrder: string[],
): SourcedTrack[] {
  const order = (t: SourcedTrack) => {
    const i = sourceOrder.indexOf(t[SOURCE_KEY] ?? '');
    return i === -1 ? sourceOrder.length : i;
  };
  return tracks
    .map((track, i) => ({ track, i, score: matchScore(track, wantedTitle, wantedArtist) }))
    .sort((a, b) => a.score - b.score || order(a.track) - order(b.track) || a.i - b.i)
    .map((entry) => entry.track);
}

/**
 * Ask every source at once. `onUpdate` gets the merged, ranked list each time a
 * source answers; the promise resolves to the final list. A failing source is
 * skipped, as upstream's cascade skipped it.
 */
export async function searchEverySource(
  track: string,
  artist: string,
  activeLabel: string,
  onUpdate: (merged: SourcedTrack[]) => void,
  search: (
    endpoint: string,
    track: string,
    artist: string,
  ) => Promise<FixTrack[]> = searchFixTracks,
): Promise<SourcedTrack[]> {
  const sources: FixSearchSource[] = orderedFixSearchSources(activeLabel);
  const keys = sources.map((s) => s.key);
  let merged: SourcedTrack[] = [];
  await Promise.all(
    sources.map(async (source) => {
      let found: FixTrack[] = [];
      try {
        found = await search(source.endpoint, track, artist);
      } catch {
        return;
      }
      const tagged = found.map((t) => ({
        ...t,
        source: t.source || source.key,
        [SOURCE_KEY]: source.key,
      }));
      merged = rankFixResults([...merged, ...tagged], track, artist, keys);
      onUpdate(merged);
    }),
  );
  return merged;
}

/** Sources whose picks the server completes (core/commissary/fix_pick.py COMPLETED_SOURCES). */
const COMPLETED_SOURCES = new Set(['itunes', 'deezer']);

/** The pick completed from its own source; unchanged when that fails. */
export async function completePick(track: SourcedTrack): Promise<FixTrack> {
  const { [SOURCE_KEY]: _tag, ...pick } = track;
  if (!COMPLETED_SOURCES.has(String(pick.source ?? '').toLowerCase())) return pick;
  try {
    const response = await fetch('/api/commissary/fix-pick/complete', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ track: pick }),
    });
    if (!response.ok) return pick;
    const data = (await response.json()) as { track?: FixTrack };
    return data.track ?? pick;
  } catch {
    return pick;
  }
}

const LABELS: Record<string, string> = {
  spotify: 'Spotify',
  deezer: 'Deezer',
  itunes: 'iTunes',
  musicbrainz: 'MusicBrainz',
};

export function sourceLabelOf(track: SourcedTrack): string {
  const key = track[SOURCE_KEY] ?? String(track.source ?? '');
  return LABELS[key] ?? key;
}

export function FixSourceChips({
  results,
  filter,
  onFilter,
}: {
  results: SourcedTrack[];
  filter: string;
  onFilter: (key: string) => void;
}) {
  const counts = new Map<string, number>();
  for (const track of results) {
    const key = track[SOURCE_KEY] ?? '';
    counts.set(key, (counts.get(key) ?? 0) + 1);
  }
  if (counts.size < 2) return null;
  const chip = (key: string, label: string, count: number) => (
    <button
      key={key}
      type="button"
      className="modal-btn secondary"
      data-commissary-fix-chip={key}
      aria-pressed={filter === key}
      style={{
        padding: '4px 10px',
        fontSize: 12,
        marginRight: 6,
        opacity: filter === key ? 1 : 0.65,
      }}
      onClick={() => onFilter(key)}
    >
      {label} {count}
    </button>
  );
  return (
    <div style={{ display: 'flex', flexWrap: 'wrap', gap: 4, marginBottom: 8 }}>
      {chip('all', 'All', results.length)}
      {[...counts.entries()].map(([key, count]) => chip(key, LABELS[key] ?? key, count))}
    </div>
  );
}

export function FixCardSource({ track }: { track: SourcedTrack }) {
  const label = sourceLabelOf(track);
  return (
    <>
      {track.image_url ? (
        <img
          src={String(track.image_url)}
          alt=""
          loading="lazy"
          style={{ width: 40, height: 40, borderRadius: 6, objectFit: 'cover', flex: '0 0 auto' }}
        />
      ) : null}
      {label ? (
        <span className="fix-result-source" style={{ fontSize: 11, opacity: 0.7 }}>
          {label}
        </span>
      ) : null}
    </>
  );
}
