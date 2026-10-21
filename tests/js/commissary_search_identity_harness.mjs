// Behavioral harness for webui/static/commissary-search-identity.js.
// The id and its source must travel as a pair: a TMDB id labelled 'library'
// (or a row id labelled 'tmdb') resolves to nothing and the search runs blind.
import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { dirname, join } from 'node:path';

const here = dirname(fileURLToPath(import.meta.url));
const src = readFileSync(join(here, '..', '..', 'webui', 'static', 'commissary-search-identity.js'), 'utf8');
globalThis.window = {};
(0, eval)(src);
const { apply } = globalThis.window.CommissarySearchIdentity;

function assert(cond, msg) {
    if (!cond) { console.error('FAIL: ' + msg); process.exit(1); }
}

// A library show's view: row id on mediaId with its source, TMDB id on tvId.
let p = apply({ _dl: { mediaId: 558, mediaSource: 'library', tvId: 65942, source: 'library' } },
              { scope: 'episode' });
assert(p.media_id === 558 && p.media_source === 'library', 'a library show sends its row id as library');

// A show not held: only the TMDB id is known.
p = apply({ _dl: { mediaId: null, tvId: 65942, source: 'library' } }, { scope: 'episode' });
assert(p.media_id === 65942 && p.media_source === 'tmdb', 'tvId always goes as tmdb, never as library');

// A movie modal keeps its identity on _opts.
p = apply({ _opts: { id: 12, source: 'tmdb' } }, { scope: 'movie' });
assert(p.media_id === 12 && p.media_source === 'tmdb', 'a movie sends its own pair');

// The manual-search overlay keeps it on its inner [data-vgm-dl-content] body.
const inner = { _opts: { id: 7, mediaId: 7, source: 'library', mediaSource: 'library' } };
const overlay = { querySelector: (sel) => (sel === '[data-vgm-dl-content]' ? inner : null) };
p = apply(overlay, { scope: 'episode' });
assert(p.media_id === 7 && p.media_source === 'library', 'the overlay finds its inner identity');

// An explicit identity in the request is never overwritten.
p = apply({ _dl: { mediaId: 1, mediaSource: 'library' } }, { media_id: 99, media_source: 'tmdb' });
assert(p.media_id === 99 && p.media_source === 'tmdb', 'an explicit pair wins');

// Nothing known: the request goes as it was.
p = apply({}, { scope: 'movie' });
assert(!('media_id' in p), 'no identity, nothing added');
p = apply(null, { scope: 'movie' });
assert(!('media_id' in p), 'no container, nothing added');

console.log('all assertions passed');
