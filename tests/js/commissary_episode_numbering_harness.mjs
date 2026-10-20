// Behavioral harness for webui/static/commissary-episode-numbering.js: the
// note under "Episode numbering" must name the provider in force and why -
// 'Auto' is otherwise unfalsifiable.
import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { dirname, join } from 'node:path';

const here = dirname(fileURLToPath(import.meta.url));
const src = readFileSync(join(here, '..', '..', 'webui', 'static', 'commissary-episode-numbering.js'), 'utf8');
globalThis.window = {};
globalThis.document = { readyState: 'loading', addEventListener() {} };
(0, eval)(src);
const { note, seasons } = globalThis.window.CommissaryEpisodeNumbering;

function assert(cond, msg) {
    if (!cond) { console.error('FAIL: ' + msg); process.exit(1); }
}

assert(seasons([3, 4, 5, 9]) === '3–5, 9', 'season runs collapse');
assert(seasons([17]) === '17' && seasons([]) === '', 'single and empty');

let n = note({ source: 'tvdb', override: null, missing_from_tmdb: [3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 14, 15, 16, 17],
               tvdb_configured: true, has_tvdb_id: true });
assert(n.startsWith('Using TVDB') && n.includes('TMDB has no season 3–17'), 'auto picked TVDB, says why: ' + n);
n = note({ source: 'tvdb', override: 'tvdb' });
assert(n === 'Using TVDB (set here).', 'an explicit choice says so');
n = note({ source: 'tmdb', override: null, tvdb_configured: false });
assert(n.includes('no TVDB key'), 'auto stayed on TMDB because TVDB is off');
n = note({ source: 'tmdb', override: null, tvdb_configured: true, has_tvdb_id: false });
assert(n.includes('no TVDB match'), 'or because the show has no TVDB id');
n = note({ source: 'tmdb', override: null, tvdb_configured: true, has_tvdb_id: true });
assert(n.includes('matches your server'), 'or because TMDB fits');

console.log('all assertions passed');
