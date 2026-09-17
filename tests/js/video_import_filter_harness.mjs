// Behavioural harness for the import browser's filter
// (webui/static/video/video-import.js). Exits non-zero on any failure.
//
// Finding one release in a download folder meant scrolling the whole listing.
// The filter narrows it as you type, by the same rule the browse endpoint
// applies before its cap: every word of the query, anywhere in the name, in any
// order. This RUNS the real function rather than pinning its source, so a rule
// that quietly stops matching fails here.
import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { dirname, join } from 'node:path';

const here = dirname(fileURLToPath(import.meta.url));
// Normalised to LF: the working tree is CRLF and the extraction below anchors
// on a newline followed by an indent.
const src = readFileSync(
    join(here, '..', '..', 'webui', 'static', 'video', 'video-import.js'), 'utf8')
    .replace(/\r\n/g, '\n');

let failures = 0;
function fail(msg) { console.error('FAIL: ' + msg); failures++; }
function eq(actual, expected, msg) {
    const a = JSON.stringify(actual), b = JSON.stringify(expected);
    if (a !== b) fail(msg + ' — got ' + a + ', wanted ' + b);
}
function ok(cond, msg) { if (!cond) fail(msg); }

// Every function in this file lives inside the page IIFE, so it is indented one
// level and its closing brace sits at column 4; nested blocks are deeper.
function extract(name) {
    const start = src.indexOf(`    function ${name}(`);
    if (start === -1) { fail(`function ${name}() is gone from video-import.js`); process.exit(1); }
    const end = src.indexOf('\n    }', start);
    if (end === -1) { fail(`could not find the end of ${name}()`); process.exit(1); }
    return src.slice(start, end + 6);
}

const browseView = (0, eval)(`(function () {
${extract('browseView')}
    return browseView;
})()`);

const FOLDER = {
    dirs: [{ name: 'Bleach - Sennen Kessen Hen', path: '/d/1' },
           { name: 'Naruto Shippuuden', path: '/d/2' }],
    files: [{ name: '[SubsPlease] Bleach - Sennen Kessen Hen - 48 (1080p).mkv', path: '/f/1' },
            { name: '[SubsPlease] Bleach - Sennen Kessen Hen - 49 (1080p).mkv', path: '/f/2' },
            { name: 'Some.Movie.2019.1080p.mkv', path: '/f/3' }],
};
const names = (v) => v.dirs.map((x) => x.name).concat(v.files.map((x) => x.name));

// ── no filter ────────────────────────────────────────────────────────────────
{
    const v = browseView(FOLDER, '');
    eq(v.matched, 5, 'an empty filter keeps every row');
    eq(v.total, 5, 'the total counts the whole folder');
    ok(v.filtered === false, 'an empty filter does not count as filtering');
}

// ── one word, folders as well as files ───────────────────────────────────────
{
    const v = browseView(FOLDER, 'bleach');
    eq(names(v), ['Bleach - Sennen Kessen Hen',
                  '[SubsPlease] Bleach - Sennen Kessen Hen - 48 (1080p).mkv',
                  '[SubsPlease] Bleach - Sennen Kessen Hen - 49 (1080p).mkv'],
       'one word keeps the release folder and both episodes');
    ok(v.filtered === true, 'a query counts as filtering');
    eq(v.total, 5, 'the total still describes the folder, not the matches');
}
eq(browseView(FOLDER, 'BLEACH').matched, 3, 'matching ignores case');

// ── every word, in any order ─────────────────────────────────────────────────
const ep48 = ['[SubsPlease] Bleach - Sennen Kessen Hen - 48 (1080p).mkv'];
eq(names(browseView(FOLDER, 'bleach 48')), ep48, 'two words narrow to the one episode');
eq(names(browseView(FOLDER, '48 bleach')), ep48, 'word order does not matter');
eq(names(browseView(FOLDER, '  bleach   48  ')), ep48, 'stray spaces are not words');
eq(browseView(FOLDER, 'bleach 47').matched, 0, 'a word nothing carries matches nothing');

// ── a payload the server already narrowed ────────────────────────────────────
{
    const served = { dirs: [], files: [{ name: 'needle.mkv', path: '/f/9' }], total: 1240, q: 'needle' };
    const v = browseView(served, 'needle');
    eq(v.matched, 1, 'a served match survives being re-filtered on the page');
    eq(v.total, 1240, "the server's own total is what the count reads from");
}

// ── nothing here throws ──────────────────────────────────────────────────────
eq(browseView(null, 'x').matched, 0, 'no payload is no rows');
eq(browseView({}, '').total, 0, 'an empty payload totals zero');
eq(browseView({ dirs: [{}], files: [{ name: null }] }, 'a').matched, 0, 'nameless rows never match');

if (failures) { console.error(failures + ' assertion(s) failed'); process.exit(1); }
console.log('all assertions passed');
