// Behavioural tests for discovery suggestions: the discovery modal
// (webui/static/sync-services.js + wishlist-tools.js) and the Wing It Pool
// (webui/static/stats-automations.js), run against the REAL function bodies
// lifted out by tests/js/vanilla-extract.mjs. Exits non-zero on any failure.
//
// A track discovery couldn't match keeps its best near misses. Its row shows the
// top one with an Accept button, the footer accepts every suggestion at or above
// a chosen percentage, the Fix dialog lists the suggestions straight away, and
// the Wing It Pool offers the same.
import { readFileSync } from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

import { extractFunction } from './vanilla-extract.mjs';

const here = path.dirname(fileURLToPath(import.meta.url));
const read = file => readFileSync(path.join(here, '..', '..', 'webui', 'static', file), 'utf8')
    .replace(/\r\n/g, '\n');
const SOURCES = {
    'sync-services.js': read('sync-services.js'),
    'wishlist-tools.js': read('wishlist-tools.js'),
    'stats-automations.js': read('stats-automations.js'),
};

const report = console.error.bind(console);
let failures = 0;
function fail(msg) { report('FAIL: ' + msg); failures++; }
function assert(cond, msg) { if (!cond) fail(msg); }
function eq(actual, expected, msg) {
    const a = JSON.stringify(actual);
    const e = JSON.stringify(expected);
    assert(a === e, `${msg}\n   expected: ${e}\n   actual:   ${a}`);
}

// The functions call each other through the global scope, as they do on the page.
function load(file, name) {
    try {
        globalThis[name] = new Function(`${extractFunction(name, SOURCES[file])}\nreturn ${name};`)();
    } catch (err) {
        fail(`${file}: ${name} is missing (${err.message})`);
        globalThis[name] = () => { throw new Error(`${name} is missing`); };
    }
}

load('sync-services.js', 'generateDiscoveryActionButton');
load('sync-services.js', 'getModalActionButtons');
load('sync-services.js', 'discoveryBucketFor');
load('sync-services.js', 'discoveryPlatformForState');
globalThis.DISCOVERY_PERFECT_CONFIDENCE = Number(
    (/^const DISCOVERY_PERFECT_CONFIDENCE = ([\d.]+);/m.exec(SOURCES['sync-services.js']) || [])[1]);
for (const name of ['getDiscoveryFixState', 'openDiscoveryFixModal', 'closeDiscoveryFixModal',
                    'selectDiscoveryFixTrack', 'applyDiscoveryMatch', '_discoverySourceNames',
                    'acceptDiscoverySuggestion', 'acceptAllDiscoverySuggestions',
                    'discoverySourceDurationMs', 'setDiscoveryFixReviewChrome', 'drawDiscoveryFixResults',
                    'discoveryBackendIdentifier', 'discoveryReviewQueue', 'discoveryReviewWanted',
                    'showDiscoveryMatchProgress', 'showDiscoveryCardMatches', 'discoveryGuessesCount']) {
    load('wishlist-tools.js', name);
}
for (const name of ['_wingItMatchedName', '_wingItSuggestion', 'renderWingItPoolList', 'acceptWingItSuggestion',
                    'acceptAllWingItSuggestions']) {
    load('stats-automations.js', name);
}

console.log = () => {};
console.warn = () => {};
console.error = () => {};

async function test(name, body) {
    try {
        await body();
    } catch (err) {
        fail(`${name}: threw ${(err && err.message) || err}`);
    }
}

function el() {
    return {
        textContent: '', value: '', innerHTML: '', style: {},
        addEventListener() {}, removeEventListener() {},
        classList: {
            hidden: true,
            add(name) { if (name === 'hidden') this.hidden = true; },
            remove(name) { if (name === 'hidden') this.hidden = false; },
        },
    };
}

const escapeText = s => String(s ?? '').replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;').replace(/'/g, '&#39;');

const SUGGESTION = { id: 'itunes-1', name: 'KICK BACK', artists: ['Kenshi Yonezu'], album: 'KICK BACK',
                     duration_ms: 193000, image_url: '', source: 'itunes', confidence: 0.84 };

const guessRow = (index, confidence) => ({
    index, yt_track: 'Kick Back', yt_artist: 'Kenshi Yonezu', status: 'Wing It', status_class: 'wing-it',
    wing_it_fallback: true, spotify_track: 'Kick Back', spotify_artist: 'Kenshi Yonezu',
    suggestions: [{ ...SUGGESTION, id: `itunes-${index}`, confidence }],
});
const notFoundRow = index => ({ index, yt_track: 'Kick Back', yt_artist: 'Kenshi Yonezu',
                                status: 'Not Found', status_class: 'not-found' });
const foundRow = index => ({ index, yt_track: 'Lemon', yt_artist: 'Kenshi Yonezu', status: 'Found',
                             status_class: 'found', spotify_track: 'Lemon', confidence: 0.97 });

/** A mirrored discovery modal holding ``results``, with the page globals the code uses. */
function modalWorld(identifier, results) {
    const state = { phase: 'discovered', spotify_total: results.length, spotifyMatches: 0,
                    discovery_results: results, discoveryResults: results, is_mirrored_playlist: true };
    const fields = {};
    for (const sel of ['#fix-modal-source-track', '#fix-modal-source-artist', '#fix-modal-track-input',
                       '#fix-modal-artist-input', '#fix-modal-mbid-input']) {
        fields[sel] = el();
    }
    const overlay = el();
    overlay.querySelector = sel => fields[sel] || null;
    const modal = { querySelector: sel => (sel === '.discovery-fix-modal-overlay' ? overlay : null) };
    const minInput = el();
    minInput.value = '80';
    const byId = { [`youtube-discovery-modal-${identifier}`]: modal, [`accept-suggestions-min-${identifier}`]: minInput };
    const w = { state, overlay, minInput, requests: [], toasts: [], confirms: [], rows: [], rendered: [],
                timers: [], footers: [], filters: [] };
    Object.assign(globalThis, {
        document: { getElementById: id => byId[id] || null },
        youtubePlaylistStates: { [identifier]: state },
        listenbrainzPlaylistStates: {}, qobuzPlaylistStates: {}, deezerPlaylistStates: {},
        tidalPlaylistStates: {}, spotifyPublicPlaylistStates: {}, itunesLinkPlaylistStates: {},
        currentDiscoveryFix: { platform: null, identifier: null, trackIndex: null, sourceTrack: null, sourceArtist: null },
        discoveryFixEnterHandler: null,
        discoveryFixMbidEnterHandler: null,
        discoveryFixResults: { token: 0, pinned: [], sources: [], settled: true, chosenKey: null, order: null },
        discoveryFixView: { source: 'all', hideVersions: false },
        discoveryReview: null,
        escapeHtml: escapeText,
        showToast: (message, type) => w.toasts.push({ message, type }),
        showConfirmDialog: async opts => { w.confirms.push(opts.title); return true; },
        formatDuration: () => '3:13',
        searchDiscoveryFix: () => {},
        lookupDiscoveryFixByMbid: () => {},
        renderDiscoveryFixResults: tracks => w.rendered.push(tracks.map(t => t.id)),
        fetch: async (url, init) => {
            w.requests.push({ url, body: JSON.parse(init.body) });
            return { status: 200, json: async () => ({ success: true }) };
        },
        updateDiscoveryModalSingleRow: (...args) => w.rows.push(args),
        setDiscoveryModalFooterActions: urlHash => w.footers.push(urlHash),
        setDiscoveryFilter: urlHash => w.filters.push(urlHash),
        _discoveryFilters: {},
        _isSoulsyncStandalone: false,
        currentMusicSourceName: 'iTunes',
    });
    return w;
}

function openFix(w, platform, identifier, index) {
    const realSetTimeout = globalThis.setTimeout;
    globalThis.setTimeout = (fn, ms) => { w.timers.push(ms); return 0; };
    try {
        openDiscoveryFixModal(platform, identifier, index);
    } finally {
        globalThis.setTimeout = realSetTimeout;
    }
}

// ── the discovery modal's row ───────────────────────────────────────────────
await test('row with a suggestion', () => {
    modalWorld('mirrored_7', []);
    const row = { ...guessRow(0, 0.84), suggestions: [{ ...SUGGESTION, name: '<b>KICK BACK</b>' }] };
    const html = generateDiscoveryActionButton(row, 'mirrored_7', 'mirrored');
    assert(html.includes('&lt;b&gt;KICK BACK&lt;/b&gt;'), 'a suggested name is escaped');
    assert(!html.includes('<b>KICK BACK</b>'), '...never inserted as HTML');
    assert(html.includes('84%'), 'the row shows how close the suggestion is');
    assert(html.includes(`acceptDiscoverySuggestion('mirrored', 'mirrored_7', 0)`), 'Accept saves this row');
    assert(html.includes('openDiscoveryFixModal('), 'Fix is still offered');
});

await test('rows without a suggestion', () => {
    modalWorld('mirrored_7', []);
    const plain = generateDiscoveryActionButton(notFoundRow(3), 'mirrored_7', 'mirrored');
    assert(!plain.includes('acceptDiscoverySuggestion'), 'a row with no suggestions offers no Accept');
    assert(plain.includes('openDiscoveryFixModal('), '...only Fix');
    const found = generateDiscoveryActionButton({ ...foundRow(1), suggestions: [SUGGESTION] }, 'mirrored_7', 'mirrored');
    assert(!found.includes('acceptDiscoverySuggestion'), 'a found row never shows a suggestion');
    const error = generateDiscoveryActionButton({ index: 4, status: 'Error', status_class: 'error', suggestions: [SUGGESTION] },
        'mirrored_7', 'mirrored');
    assert(!error.includes('acceptDiscoverySuggestion'), 'an error row never shows a suggestion');
});

// ── accepting ───────────────────────────────────────────────────────────────
await test('accept one', async () => {
    const w = modalWorld('mirrored_7', [guessRow(0, 0.84)]);
    const ok = await acceptDiscoverySuggestion('mirrored', 'mirrored_7', 0);
    eq(ok, true, 'accepting reports success');
    eq(w.confirms, [], 'accepting a suggestion asks nothing first');
    eq(w.requests.map(r => r.url), ['/api/youtube/discovery/update_match'], 'a mirrored accept goes to the mirrored fix route');
    const body = (w.requests[0] || {}).body || {};
    const pick = body.spotify_track || {};
    eq([body.identifier, body.track_index, body.original_name, body.original_artist, pick.id, pick.source],
        ['mirrored_7', 0, 'Kick Back', 'Kenshi Yonezu', 'itunes-0', 'itunes'],
        '...with the suggestion as the pick, naming its source');
    const result = w.state.discovery_results[0];
    eq([result.status_class, result.manual_match, result.suggestions], ['found', true, []],
        'the track is found and its suggestions are gone');
    eq(w.rows, [['mirrored', 'mirrored_7', 0]], 'the row is redrawn');
});

await test('accept all', async () => {
    const stale = { ...foundRow(2), suggestions: [{ ...SUGGESTION, id: 'itunes-2', confidence: 0.95 }] };
    const w = modalWorld('mirrored_7', [guessRow(0, 0.84), guessRow(1, 0.79), stale, guessRow(3, 0.8)]);
    w.minInput.value = '80';
    await acceptAllDiscoverySuggestions('mirrored', 'mirrored_7');
    eq(w.confirms, ['Accept suggestions'], 'one confirmation for the batch');
    eq(w.requests.map(r => r.body.track_index), [0, 3], 'only suggestions at or above the threshold, in order');
    eq(w.state.discovery_results.map(r => r.status_class), ['found', 'wing-it', 'found', 'found'],
        'the rest are left alone');
    eq(w.toasts.filter(t => /Accepted 2/.test(t.message)).length, 1, 'one summary message');
    eq(w.toasts.filter(t => t.message === 'Match updated successfully!').length, 0, 'no message per track');
    eq(w.footers, ['mirrored_7'], 'the footer is redrawn');
});

await test('accept all with nothing over the line', async () => {
    const w = modalWorld('mirrored_7', [guessRow(0, 0.84)]);
    w.minInput.value = '90';
    await acceptAllDiscoverySuggestions('mirrored', 'mirrored_7');
    eq(w.requests, [], 'nothing under the threshold is accepted');
    eq(w.confirms, [], '...and nothing is asked');
});

await test('Fix still confirms', async () => {
    const w = modalWorld('mirrored_7', [notFoundRow(0)]);
    openFix(w, 'mirrored', 'mirrored_7', 0);
    await selectDiscoveryFixTrack(SUGGESTION);
    eq(w.confirms, ['Confirm Match'], 'a pick from the Fix dialog still asks first');
    eq(w.requests.length, 1, '...then saves');
    eq(currentDiscoveryFix.identifier, null, '...and closes the dialog');
});

// ── the Fix dialog ──────────────────────────────────────────────────────────
await test('Fix opens on the suggestions', () => {
    const w = modalWorld('mirrored_7', [guessRow(0, 0.84)]);
    openFix(w, 'mirrored', 'mirrored_7', 0);
    eq(w.rendered, [['itunes-0']], 'the suggestions are listed straight away');
    eq(w.timers, [500], '...and every source is searched for more');
    assert(!w.overlay.classList.hidden, 'the dialog is shown');
});

await test('Fix without suggestions still searches', () => {
    const w = modalWorld('mirrored_7', [notFoundRow(0)]);
    openFix(w, 'mirrored', 'mirrored_7', 0);
    eq(w.rendered, [], 'nothing to list');
    eq(w.timers, [500], 'the search runs as before');
});

// ── the footer ──────────────────────────────────────────────────────────────
await test('Accept all in the footer', () => {
    modalWorld('mirrored_7', []);
    const state = { phase: 'discovered', is_mirrored_playlist: true, spotify_matches: 1,
                    discovery_results: [guessRow(0, 0.84), { ...foundRow(1), suggestions: [SUGGESTION] }, guessRow(2, 0.6)] };
    const html = getModalActionButtons('mirrored_7', 'discovered', state);
    assert(html.includes(`acceptAllDiscoverySuggestions('mirrored', 'mirrored_7')`), 'a playlist with suggestions offers Accept all');
    assert(html.includes('id="accept-suggestions-min-mirrored_7"'), '...with a threshold to set');
    // Retry Failed counts the same two rows, so read the number off Accept all.
    eq((html.match(/Accept all \((\d+)\)/) || [])[1], '2', '...counting the tracks that have one');
    const none = getModalActionButtons('mirrored_7', 'discovered', { ...state, discovery_results: [foundRow(1)] });
    assert(!none.includes('acceptAllDiscoverySuggestions'), 'no suggestions, no Accept all');
});

// ── the Wing It Pool ────────────────────────────────────────────────────────
function poolWorld(tracks, view = 'attention') {
    const container = el();
    const search = el();
    const minInput = el();
    minInput.value = '80';
    const byId = { 'wing-it-list-content': container, 'wing-it-list-search': search, 'wing-it-suggest-min': minInput };
    const w = { container, minInput, requests: [], toasts: [], confirms: [], refreshes: 0 };
    Object.assign(globalThis, {
        document: { getElementById: id => byId[id] || null },
        _wingItPoolData: view === 'matched' ? { tracks: [], matched: tracks } : { tracks, matched: [] },
        _wingItPoolView: view,
        _esc: escapeText,
        _escJs: s => String(s ?? '').replace(/\\/g, '\\\\').replace(/'/g, "\\'"),
        showToast: (message, type) => w.toasts.push({ message, type }),
        showConfirmDialog: async opts => { w.confirms.push(opts.title); return true; },
        fetch: async (url, init) => {
            w.requests.push({ url, body: JSON.parse(init.body) });
            return { json: async () => ({ success: true }) };
        },
        refreshWingItPool: () => { w.refreshes++; },
    });
    return w;
}

const poolTrack = (id, confidence) => ({
    id, track_name: `Track ${id}`, artist_name: 'Kenshi Yonezu', playlist_name: 'Anime',
    extra_data: JSON.stringify({ wing_it_fallback: true,
        suggestions: confidence == null ? [] : [{ ...SUGGESTION, id: `itunes-${id}`, confidence }] }),
});

await test('pool rows', () => {
    const w = poolWorld([poolTrack(11, 0.84), poolTrack(12, null)]);
    renderWingItPoolList();
    const html = w.container.innerHTML;
    assert(html.includes('KICK BACK') && html.includes('84%'), 'a guess shows its top suggestion');
    assert(html.includes('acceptWingItSuggestion(11)'), '...with Accept');
    eq((html.match(/acceptWingItSuggestion\(/g) || []).length, 1, 'a guess with no suggestions offers no Accept');
    assert(html.includes('acceptAllWingItSuggestions()'), 'the list offers Accept all');
    const resolved = poolWorld([poolTrack(11, 0.84)], 'matched');
    renderWingItPoolList();
    assert(resolved.container.innerHTML.includes('Track 11'), 'the resolved list shows the track, suggestion and all');
    assert(!resolved.container.innerHTML.includes('acceptAllWingItSuggestions'), 'resolved guesses offer no Accept all');
});

await test('pool accept one', async () => {
    const w = poolWorld([poolTrack(11, 0.84)]);
    await acceptWingItSuggestion(11);
    eq(w.requests.map(r => [r.url, r.body.track_id, r.body.spotify_track.id, r.body.spotify_track.source]),
        [['/api/discovery-pool/fix', 11, 'itunes-11', 'itunes']], 'Accept saves the suggestion as the fix');
    eq(w.confirms, [], '...without asking');
    eq(w.refreshes, 1, 'the pool reloads');
});

await test('pool accept all', async () => {
    const w = poolWorld([poolTrack(11, 0.84), poolTrack(12, 0.7), poolTrack(13, 0.8), poolTrack(14, null)]);
    w.minInput.value = '80';
    await acceptAllWingItSuggestions();
    eq(w.confirms, ['Accept suggestions'], 'one confirmation');
    eq(w.requests.map(r => r.body.track_id), [11, 13], 'only suggestions at or above the threshold');
    eq(w.refreshes, 1, 'one reload at the end');
});

if (failures) {
    report(`\n${failures} assertion(s) failed`);
    process.exit(1);
}
process.stdout.write('discovery suggestions harness: all assertions passed\n');
