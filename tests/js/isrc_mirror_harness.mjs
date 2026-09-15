// Behavioural tests for carrying a Qobuz track's ISRC into its mirrored playlist:
// the Qobuz tab's auto-mirror (webui/static/sync-services.js, loadQobuzPlaylists)
// and the mirror save (webui/static/stats-automations.js, mirrorPlaylist), run
// against the REAL function bodies lifted out by tests/js/vanilla-extract.mjs.
// Exits non-zero on any failure.
//
// The Qobuz client returns each track's ISRC; both steps rebuild the track
// field by field on the way to /api/mirror-playlist, so a field they don't name
// never reaches the database.
import { readFileSync } from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

import { extractFunction } from './vanilla-extract.mjs';

const here = path.dirname(fileURLToPath(import.meta.url));
const read = file => readFileSync(path.join(here, '..', '..', 'webui', 'static', file), 'utf8')
    .replace(/\r\n/g, '\n');
const SYNC = read('sync-services.js');
const STATS = read('stats-automations.js');

const report = console.error.bind(console);
let failures = 0;
function eq(actual, expected, msg) {
    const a = JSON.stringify(actual);
    const e = JSON.stringify(expected);
    if (a !== e) {
        report(`FAIL: ${msg}\n   expected: ${e}\n   actual:   ${a}`);
        failures++;
    }
}

function load(source, name) {
    return new Function(`${extractFunction(name, source)}\nreturn ${name};`)();
}

console.log = () => {};
console.warn = () => {};
console.error = () => {};

const CODE = 'GBAYE0601498';

// ── the mirror save sends each track's ISRC ────────────────────────────────
{
    const mirrorPlaylist = load(STATS, 'mirrorPlaylist');
    const sent = [];
    globalThis.fetch = (url, init) => {
        sent.push(JSON.parse(init.body));
        return Promise.resolve({ json: () => Promise.resolve({ success: true }) });
    };

    mirrorPlaylist('qobuz', 'p1', 'Playlist', [
        { track_name: 'Harder', artist_name: 'Daft Punk', source_track_id: '1', isrc: CODE },
        { name: 'One More Time', artists: ['Daft Punk'], id: '2' },
    ]);
    await new Promise(resolve => setImmediate(resolve));

    eq(sent.map(body => body.tracks.map(t => t.isrc)), [[CODE, '']], "the mirror save sends each track's ISRC");
}

// ── the Qobuz tab's auto-mirror carries it on both paths ───────────────────
{
    const loadQobuzPlaylists = load(SYNC, 'loadQobuzPlaylists');
    const container = { innerHTML: '' };
    const refreshBtn = { disabled: false, textContent: '' };
    const mirrored = [];
    Object.assign(globalThis, {
        document: {
            getElementById: id => ({ 'qobuz-playlist-container': container, 'qobuz-refresh-btn': refreshBtn })[id] || null,
            querySelector: () => null,
        },
        fetch: async url => {
            if (url === '/api/qobuz/playlists') {
                return { ok: true, json: async () => [
                    { id: 'p1', name: 'Listed with tracks', tracks: [
                        { id: '1', name: 'Harder', artists: ['Daft Punk'], album: 'Discovery', duration_ms: 224000, isrc: CODE }] },
                    { id: 'p2', name: 'Tracks fetched', tracks: [] },
                ] };
            }
            if (url === '/api/qobuz/playlist/p2') {
                return { ok: true, json: async () => ({ tracks: [
                    { id: '2', name: 'One More Time', artists: ['Daft Punk'], album: 'Discovery', duration_ms: 320000, isrc: 'FRZ030100014' }] }) };
            }
            throw new Error(`unexpected fetch ${url}`);
        },
        qobuzPlaylists: [],
        qobuzPlaylistsLoaded: false,
        renderQobuzPlaylists: () => {},
        loadQobuzPlaylistStatesFromBackend: async () => {},
        showToast: () => {},
        mirrorPlaylist: (source, id, name, tracks) => mirrored.push({ source, id, isrcs: tracks.map(t => t.isrc) }),
    });

    await loadQobuzPlaylists();

    eq(mirrored, [
        { source: 'qobuz', id: 'p1', isrcs: [CODE] },
        { source: 'qobuz', id: 'p2', isrcs: ['FRZ030100014'] },
    ], "both of the Qobuz tab's mirror paths carry the ISRC");
}

if (failures) {
    report(`\n${failures} assertion(s) failed`);
    process.exit(1);
}
process.stdout.write('isrc mirror harness: all assertions passed\n');
