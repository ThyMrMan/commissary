// Behavioural tests for the Re-identify Album modal's decisions in
// webui/static/library.js, run against the REAL function bodies lifted out by
// tests/js/vanilla-extract.mjs. Exits non-zero on any failure.
//
// These two functions decide what the modal SENDS and what it TELLS you:
// which confirmed pairings are applied (an unticked row, or a release track
// with no file behind it, must never be sent) and the one-line summary after
// a run, which is the only place a partial failure is reported.
import { readFileSync } from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

import { extractFunction } from './vanilla-extract.mjs';

const here = path.dirname(fileURLToPath(import.meta.url));
const LIBRARY_JS = readFileSync(path.join(here, '..', '..', 'webui', 'static', 'library.js'), 'utf8');

function load(name) {
    return new Function(`${extractFunction(name, LIBRARY_JS)}\nreturn ${name};`)();
}

let failures = 0;
function assert(cond, msg) {
    if (!cond) { console.error('FAIL: ' + msg); failures++; }
}
function eq(actual, expected, msg) {
    const a = JSON.stringify(actual);
    const e = JSON.stringify(expected);
    assert(a === e, `${msg}\n   expected: ${e}\n   actual:   ${a}`);
}

const _reidaPlan = load('_reidaPlan');
const _reidaSummary = load('_reidaSummary');

const pair = (key, libraryId) => ({
    release_track: { key },
    library_track: libraryId == null ? null : { id: libraryId },
});

// ── what gets applied ───────────────────────────────────────────────────────
eq(_reidaPlan([pair('1-1', 7), pair('1-2', 8)], new Set()),
    [{ library_track_id: 7, release_track_key: '1-1' },
     { library_track_id: 8, release_track_key: '1-2' }],
    'every paired row is applied by default');

eq(_reidaPlan([pair('1-1', 7), pair('1-2', 8)], new Set(['1-2'])),
    [{ library_track_id: 7, release_track_key: '1-1' }],
    'an unticked row is left alone');

eq(_reidaPlan([pair('1-1', null), pair('1-2', 8)], new Set()),
    [{ library_track_id: 8, release_track_key: '1-2' }],
    'a release track with no file in the library is never sent');

eq(_reidaPlan(null, null), [], 'no preview means nothing to apply');
eq(_reidaPlan([pair('2-1', 9)], undefined),
    [{ library_track_id: 9, release_track_key: '2-1' }],
    'a missing exclusion set excludes nothing');

// ── what the user is told ───────────────────────────────────────────────────
eq(_reidaSummary([{ success: true, removed_original: '/a.flac' }, { success: true, removed_original: null }]),
    'Re-filed 2 of 2 tracks · replaced 1 original',
    'counts re-filed tracks and the originals actually removed');

eq(_reidaSummary([{ success: true }, { success: false, error: 'AcoustID mismatch' }]),
    'Re-filed 1 of 2 tracks · 1 left as it was',
    'a failed track is reported as kept, not silently dropped');

eq(_reidaSummary([{ success: false }, { success: false }]),
    'Re-filed 0 of 2 tracks · 2 left as they were',
    'plural wording');

eq(_reidaSummary([]), 'Re-filed 0 of 0 tracks', 'an empty run');

if (failures) {
    console.error(`\n${failures} assertion(s) failed`);
    process.exit(1);
}
console.log('album re-identify harness: all assertions passed');
