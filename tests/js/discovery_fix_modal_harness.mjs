// Behavioural tests for the discovery Fix dialog in webui/static/wishlist-tools.js,
// run against the REAL function bodies lifted out by tests/js/vanilla-extract.mjs.
// Exits non-zero on any failure.
//
// Every discovery modal opens the same Fix dialog, which has to find the modal's
// state to know which track it is fixing, then send the pick to that source's
// route and record it. A Qobuz playlist opened from the Qobuz tab, and an Apple
// Music (iTunes) link, had no case in the dialog's lookup, so Fix only ever
// said "Track data not found".
import { readFileSync } from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

import { extractFunction } from './vanilla-extract.mjs';

const here = path.dirname(fileURLToPath(import.meta.url));
const WISHLIST_TOOLS = readFileSync(path.join(here, '..', '..', 'webui', 'static', 'wishlist-tools.js'), 'utf8')
    .replace(/\r\n/g, '\n');

function load(name) {
    return new Function(`${extractFunction(name, WISHLIST_TOOLS)}\nreturn ${name};`)();
}

// The functions call each other through the global scope, as they do on the page.
for (const name of ['openDiscoveryFixModal', 'selectDiscoveryFixTrack', 'closeDiscoveryFixModal',
                    'discoverySourceDurationMs', 'setDiscoveryFixReviewChrome', 'drawDiscoveryFixResults',
                    'discoveryBackendIdentifier', 'showDiscoveryMatchProgress', 'showDiscoveryCardMatches',
                    'discoveryGuessesCount']) {
    globalThis[name] = load(name);
}
for (const name of ['getDiscoveryFixState', 'applyDiscoveryMatch']) {
    try {
        globalThis[name] = load(name);
    } catch {
        // Older revisions did this inline.
    }
}

const report = console.error.bind(console);
console.log = () => {};
console.warn = () => {};
console.error = () => {};

let failures = 0;
function assert(cond, msg) {
    if (!cond) { report('FAIL: ' + msg); failures++; }
}
function eq(actual, expected, msg) {
    const a = JSON.stringify(actual);
    const e = JSON.stringify(expected);
    assert(a === e, `${msg}\n   expected: ${e}\n   actual:   ${a}`);
}

function element() {
    return {
        textContent: '',
        value: '',
        style: {},
        addEventListener() {},
        removeEventListener() {},
        classList: {
            hidden: true,
            add(name) { if (name === 'hidden') this.hidden = true; },
            remove(name) { if (name === 'hidden') this.hidden = false; },
        },
    };
}

/** A discovery modal for one not-yet-matched track, with the page globals the dialog uses. */
function world({ identifier, state, cardStates = {} }) {
    const result = {
        index: 0, yt_track: 'Kick Back', yt_artist: 'Kenshi Yonezu',
        status: '❌ Not Found', status_class: 'not-found',
    };
    const results = [result];
    const modalState = { spotify_total: 1, spotifyMatches: 0, discovery_results: results, discoveryResults: results, ...state };

    const fields = {};
    for (const sel of ['#fix-modal-source-track', '#fix-modal-source-artist', '#fix-modal-track-input',
                       '#fix-modal-artist-input', '#fix-modal-mbid-input']) {
        fields[sel] = element();
    }
    const overlay = element();
    overlay.querySelector = sel => fields[sel] || null;
    const modal = { querySelector: sel => (sel === '.discovery-fix-modal-overlay' ? overlay : null) };
    const byId = { [`youtube-discovery-modal-${identifier}`]: modal };

    const w = { toasts: [], requests: [], searches: 0, timers: [], rows: [], cards: [], result, state: modalState, overlay, fields };
    Object.assign(globalThis, {
        document: { getElementById: id => byId[id] || null },
        youtubePlaylistStates: { [identifier]: modalState },
        listenbrainzPlaylistStates: {},
        qobuzPlaylistStates: {},
        deezerPlaylistStates: {},
        tidalPlaylistStates: {},
        spotifyPublicPlaylistStates: {},
        itunesLinkPlaylistStates: {},
        ...cardStates,
        currentDiscoveryFix: { platform: null, identifier: null, trackIndex: null, sourceTrack: null, sourceArtist: null },
        discoveryFixEnterHandler: null,
        discoveryFixMbidEnterHandler: null,
        discoveryFixResults: { token: 0, pinned: [], sources: [], settled: true, chosenKey: null, order: null },
        discoveryFixView: { source: 'all', hideVersions: false },
        discoveryReview: null,
        showToast: (message, type) => w.toasts.push({ message, type }),
        searchDiscoveryFix: () => { w.searches++; },
        lookupDiscoveryFixByMbid: () => {},
        showConfirmDialog: async () => true,
        formatDuration: () => '3:13',
        fetch: async (url, init) => {
            w.requests.push({ url, body: JSON.parse(init.body) });
            return { status: 200, json: async () => ({ success: true }) };
        },
        updateDiscoveryModalSingleRow: (...args) => w.rows.push(args),
        updateQobuzCardProgress: (...args) => w.cards.push(['qobuz', ...args]),
        updateDeezerCardProgress: (...args) => w.cards.push(['deezer', ...args]),
        updateSpotifyPublicCardProgress: (...args) => w.cards.push(['spotify_public', ...args]),
        updateITunesLinkCardProgress: (...args) => w.cards.push(['itunes_link', ...args]),
    });
    return w;
}

function openFix(w, platform, identifier) {
    const realSetTimeout = globalThis.setTimeout;
    globalThis.setTimeout = (fn, ms) => { w.timers.push({ fn, ms }); return 0; };
    try {
        openDiscoveryFixModal(platform, identifier, 0);
    } finally {
        globalThis.setTimeout = realSetTimeout;
    }
}

const PICK = { id: 'itunes-1', name: 'KICK BACK', artists: ['Kenshi Yonezu'], album: 'KICK BACK', duration_ms: 193000, image_url: '' };

const CASES = [
    {
        label: 'a Qobuz playlist opened from the Qobuz tab',
        platform: 'qobuz', identifier: 'qobuz_123',
        state: { is_qobuz_playlist: true, qobuz_playlist_id: '123' },
        cardStates: { qobuzPlaylistStates: { '123': { phase: 'discovered' } } },
        url: '/api/qobuz/discovery/update_match', backendId: '123',
        card: ['qobuz', '123', { spotify_matches: 1, spotify_total: 1 }],
        cardCount: () => (qobuzPlaylistStates['123'] || {}).spotify_matches,
    },
    {
        label: 'an Apple Music (iTunes) link',
        platform: 'itunes_link', identifier: 'ituneslink_abc',
        state: { is_itunes_link_playlist: true, itunes_link_playlist_id: 'abc' },
        cardStates: { itunesLinkPlaylistStates: { abc: { phase: 'discovered' } } },
        url: '/api/itunes-link/discovery/update_match', backendId: 'abc',
        card: ['itunes_link', 'abc', { spotify_matches: 1, spotify_total: 1 }],
    },
    {
        label: 'a Deezer playlist',
        platform: 'deezer', identifier: 'deezer_55',
        state: { is_deezer_playlist: true, deezer_playlist_id: '55' },
        cardStates: { deezerPlaylistStates: { '55': { phase: 'discovered' } } },
        url: '/api/deezer/discovery/update_match', backendId: '55',
        card: ['deezer', '55', { spotify_matches: 1, spotify_total: 1 }],
    },
    {
        label: 'a mirrored playlist',
        platform: 'mirrored', identifier: 'mirrored_7',
        state: { is_mirrored_playlist: true },
        url: '/api/youtube/discovery/update_match', backendId: 'mirrored_7',
        card: null,
    },
];

for (const c of CASES) {
    const w = world(c);

    // ── opening Fix ─────────────────────────────────────────────────────────
    openFix(w, c.platform, c.identifier);
    eq(w.toasts.filter(t => t.type === 'error'), [], `${c.label}: Fix opens without an error`);
    eq([currentDiscoveryFix.platform, currentDiscoveryFix.identifier, currentDiscoveryFix.trackIndex],
        [c.platform, c.identifier, 0], `${c.label}: the dialog knows which track it is fixing`);
    eq([currentDiscoveryFix.sourceTrack, currentDiscoveryFix.sourceArtist], ['Kick Back', 'Kenshi Yonezu'],
        `${c.label}: ...and what the source calls it`);
    eq([w.fields['#fix-modal-track-input'].value, w.fields['#fix-modal-artist-input'].value],
        ['Kick Back', 'Kenshi Yonezu'], `${c.label}: the search boxes are filled in`);
    assert(!w.overlay.classList.hidden, `${c.label}: the dialog is shown`);
    eq(w.timers.map(t => t.ms), [500], `${c.label}: a search is scheduled`);
    w.timers.forEach(t => t.fn());
    eq(w.searches, 1, `${c.label}: ...and runs`);

    // ── picking a result ────────────────────────────────────────────────────
    await selectDiscoveryFixTrack(PICK);
    eq(w.requests.map(r => r.url), [c.url], `${c.label}: the pick is sent to its source's route`);
    const body = (w.requests[0] || {}).body || {};
    eq([body.identifier, body.track_index, body.original_name, body.original_artist],
        [c.backendId, 0, 'Kick Back', 'Kenshi Yonezu'],
        `${c.label}: ...naming the playlist the server keeps its state under`);
    eq([w.result.status_class, w.result.spotify_track, w.result.manual_match], ['found', 'KICK BACK', true],
        `${c.label}: the track is marked found`);
    eq(w.state.spotifyMatches, 1, `${c.label}: the modal's match count goes up`);
    if (c.card) {
        eq(w.cards, [c.card], `${c.label}: the playlist card's progress is updated`);
        if (c.cardCount) {
            eq(c.cardCount(), 1, `${c.label}: ...and the count the card redraws from`);
        }
    } else {
        eq(w.cards, [], `${c.label}: no playlist card is touched`);
    }
    eq(w.rows, [[c.platform, c.identifier, 0]], `${c.label}: the row is redrawn`);
    eq([currentDiscoveryFix.identifier, w.overlay.classList.hidden], [null, true], `${c.label}: the dialog closes`);
}

// A source the dialog doesn't know still says so, rather than fixing a track it can't place.
{
    const w = world({ identifier: 'nonesuch_1', state: {} });
    openFix(w, 'nonesuch', 'nonesuch_1');
    eq(w.toasts, [{ message: 'Track data not found', type: 'error' }], 'an unknown source reports the missing track');
    eq(currentDiscoveryFix.identifier, null, '...and opens nothing');
}

if (failures) {
    report(`\n${failures} assertion(s) failed`);
    process.exit(1);
}
process.stdout.write('discovery fix modal harness: all assertions passed\n');
