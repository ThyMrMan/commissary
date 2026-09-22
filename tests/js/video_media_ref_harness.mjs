// Behavioural harness for mediaRef() in webui/static/video/video-download-view.js.
// Exits non-zero on any failure.
//
// A search payload identifies its title with a PAIR — an id and the source that
// says how to read it. A row id and a TMDB id are both just integers, so a pair
// that disagrees is not an error anywhere: the backend resolves a row that does
// not exist and answers nothing, and the search silently runs with one title, no
// aliases and no episode hints. This RUNS the real function against the state
// shapes the page actually builds, rather than pinning its source.
import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { dirname, join } from 'node:path';

const here = dirname(fileURLToPath(import.meta.url));
// Normalised to LF: the working tree is CRLF and the extraction below anchors
// on a newline followed by an indent.
const src = readFileSync(
    join(here, '..', '..', 'webui', 'static', 'video', 'video-download-view.js'), 'utf8')
    .replace(/\r\n/g, '\n');

let failures = 0;
function fail(msg) { console.error('FAIL: ' + msg); failures++; }
function eq(actual, expected, msg) {
    const a = JSON.stringify(actual), b = JSON.stringify(expected);
    if (a !== b) fail(msg + ' — got ' + a + ', wanted ' + b);
}

// Every function in this file lives inside the page IIFE, so it is indented one
// level and its closing brace sits at column 4.
function extract(name) {
    const start = src.indexOf(`    function ${name}(`);
    if (start === -1) { fail(`function ${name}() is gone from video-download-view.js`); process.exit(1); }
    const end = src.indexOf('\n    }', start);
    if (end === -1) { fail(`could not find the end of ${name}()`); process.exit(1); }
    return src.slice(start, end + 6);
}

const mediaRef = (0, eval)(`(function () {
${extract('mediaRef')}
    return mediaRef;
})()`);

// ── the show state the get-modal builds (renderShow's `_dl`) ─────────────────
// mediaId is the library row, tvId is the TMDB id. Reading `id` (absent here)
// and falling back to `tvId` while taking the source from `source` is what sent
// a TMDB id labelled 'library'.
{
    const dl = { tvId: 65942, source: 'library', mediaId: 558, mediaSource: 'library',
                 title: 'Re:ZERO', maxSeason: 4 };
    eq(mediaRef(dl), { id: 558, source: 'library' }, 'a library show searches by its row id');
}

// A show the library does not hold: no row id, so the TMDB id goes out AS a
// TMDB id. This is the case with nothing else to fall back on.
{
    const dl = { tvId: 65942, source: 'tmdb', mediaId: null, mediaSource: 'tmdb' };
    eq(mediaRef(dl), { id: 65942, source: 'tmdb' }, 'a tmdb-only show searches by its tmdb id');
}
{
    const dl = { tvId: 65942, source: 'library', mediaId: undefined, mediaSource: undefined };
    eq(mediaRef(dl), { id: 65942, source: 'tmdb' },
       'a tvId is a TMDB id whatever the page called its source');
}

// ── the movie state (render's `_opts`) and the manual overlay's body._opts ───
{
    eq(mediaRef({ id: 42, source: 'library', year: 1999 }), { id: 42, source: 'library' },
       'a movie searches by the id it was opened with');
    eq(mediaRef({ id: 603, source: 'tmdb' }), { id: 603, source: 'tmdb' },
       'a tmdb movie keeps its source');
    eq(mediaRef({ id: 42 }), { id: 42, source: 'library' },
       'an unstated source means a library row');
    eq(mediaRef({ id: 558, mediaId: 558, source: 'library', mediaSource: 'library' }),
       { id: 558, source: 'library' }, "the overlay's body._opts carries both spellings");
}

// ── nothing to identify ──────────────────────────────────────────────────────
eq(mediaRef({}), null, 'no identity at all is null, not a half-filled pair');
eq(mediaRef(null), null, 'a missing state object is null');
eq(mediaRef({ source: 'library' }), null, 'a source with no id identifies nothing');

// ── id 0 is an id ────────────────────────────────────────────────────────────
eq(mediaRef({ id: 0, source: 'library' }), { id: 0, source: 'library' },
   'zero is a value, not an absence');

if (failures) { console.error(`${failures} failure(s)`); process.exit(1); }
console.log('all assertions passed');
