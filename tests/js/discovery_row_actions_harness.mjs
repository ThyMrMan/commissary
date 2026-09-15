// Behavioural tests for what a discovery row's actions change beyond the row itself
// (webui/static/wishlist-tools.js, sync-services.js, stats-automations.js), run against
// the REAL function bodies lifted out by tests/js/vanilla-extract.mjs. Exits non-zero on
// any failure.
//
// - Unmatch sent a Qobuz playlist's track to YouTube's route under the modal's id, and
//   changed no count.
// - A Fix on a Tidal, Deezer, Spotify-link, Apple Music-link or Beatport playlist left
//   its card's count behind: a card redraws from its state's `spotify_matches`, and Fix
//   set `spotifyMatches` or nothing. Those platforms count a Wing It guess as a match;
//   YouTube, mirrored and ListenBrainz discovery don't, so a change to a guess follows
//   its platform.
// - The filter chips kept their counts after a row changed group.
// - The Wing It Pool's Fix dialog searched Spotify alone, so without a Spotify
//   connection it found nothing.
import { readFileSync } from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

import { extractFunction } from './vanilla-extract.mjs';

const here = path.dirname(fileURLToPath(import.meta.url));
const read = file => readFileSync(path.join(here, '..', '..', 'webui', 'static', file), 'utf8')
    .replace(/\r\n/g, '\n');
const WISHLIST = read('wishlist-tools.js');
const SYNC = read('sync-services.js');
const STATS = read('stats-automations.js');

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
function load(source, name) {
    try {
        globalThis[name] = new Function(`${extractFunction(name, source)}\nreturn ${name};`)();
    } catch (err) {
        fail(`${name} is missing (${err.message})`);
        globalThis[name] = () => { throw new Error(`${name} is missing`); };
    }
}

function loadConst(source, name) {
    const match = new RegExp(`^const ${name} = ([\\s\\S]*?);\\n`, 'm').exec(source);
    if (!match) {
        fail(`const ${name} is missing`);
        return;
    }
    globalThis[name] = new Function(`return (${match[1]});`)();
}

for (const name of ['getDiscoveryFixState', 'applyDiscoveryMatch', 'unmatchDiscoveryTrack', 'discoveryBackendIdentifier',
                    'discoveryGuessesCount', 'uncountDiscoveryMatch', 'showDiscoveryMatchProgress',
                    'showDiscoveryCardMatches', 'refreshDiscoveryModalChrome', 'updateDiscoveryModalSingleRow',
                    'setDiscoveryTrackUnavailable', 'discoveryFixSourceList', 'discoveryVersionTag']) {
    load(WISHLIST, name);
}
for (const name of ['generateDiscoveryActionButton', 'discoveryBucketFor', 'discoveryBucketCounts',
                    'buildDiscoveryFilterBarHtml', 'refreshDiscoveryFilterBar', 'formatDuration']) {
    load(SYNC, name);
}
for (const name of ['searchPoolFix', 'drawPoolFixResults']) {
    load(STATS, name);
}
loadConst(SYNC, 'DISCOVERY_PERFECT_CONFIDENCE');
loadConst(SYNC, 'DISCOVERY_BUCKETS');

console.log = () => {};
console.warn = () => {};
console.error = () => {};

async function test(name, body) {
    try {
        await body();
    } catch (err) {
        fail(`${name}: threw ${(err && err.stack) || err}`);
    }
}

const flush = async (times = 8) => {
    for (let i = 0; i < times; i++) await new Promise(resolve => setImmediate(resolve));
};

const escapeText = s => String(s ?? '').replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;').replace(/'/g, '&#39;');

function el(extra = {}) {
    return { textContent: '', value: '', innerHTML: '', className: '', style: {}, dataset: {}, ...extra };
}

function answer(body, status = 200) {
    return { ok: status >= 200 && status < 300, status, statusText: '', json: async () => body };
}

const ARTIST = 'Kenshi Yonezu';
const SOURCES = ['spotify', 'deezer', 'itunes', 'musicbrainz'];
const PICK = { id: 'itunes-1', name: 'KICK BACK', artists: [ARTIST], album: 'KICK BACK', duration_ms: 193000,
               image_url: '', source: 'itunes' };
const foundRow = index => ({ index, yt_track: `Track ${index}`, yt_artist: ARTIST, status: 'Found', status_class: 'found',
                             spotify_track: 'Match', spotify_data: { id: `m-${index}`, name: 'Match' }, confidence: 0.95 });
const missRow = index => ({ index, yt_track: `Track ${index}`, yt_artist: ARTIST, status: 'Not Found', status_class: 'not-found' });
const guessRow = index => ({ index, yt_track: `Track ${index}`, yt_artist: ARTIST, status: 'Wing It', status_class: 'wing-it',
                             wing_it_fallback: true, spotify_track: `Track ${index}`, spotify_data: { id: `wing_it_${index}` } });

function rowElement() {
    const cells = Object.fromEntries(['.discovery-status', '.spotify-track', '.spotify-artist', '.spotify-album',
                                      '.discovery-actions'].map(selector => [selector, el()]));
    return { cells, querySelector: selector => cells[selector] || null };
}

/** A discovery modal of ``rows`` with its table, filter chips, card states and the page's globals. */
function world({ identifier, state = {}, rows, cardStates = {}, listenbrainz = false, respond } = {}) {
    const modalState = {
        phase: 'discovered', spotify_total: rows.length, spotifyMatches: 0, spotify_matches: 0,
        playlist: { name: 'Playlist', tracks: rows.map(() => ({})) },
        discovery_results: rows, discoveryResults: rows, ...state,
    };
    const rowEls = Object.fromEntries(rows.map((_, i) => [`discovery-row-${identifier}-${i}`, rowElement()]));
    const bar = { outerHTML: '' };
    const w = { state: modalState, rowEls, bar, requests: [], toasts: [], cards: [], footers: [] };
    Object.assign(globalThis, {
        document: {
            getElementById: id => rowEls[id] || null,
            querySelector: selector => (selector === `#discovery-filter-bar-${identifier}` ? bar : null),
        },
        youtubePlaylistStates: listenbrainz ? {} : { [identifier]: modalState },
        listenbrainzPlaylistStates: listenbrainz ? { [identifier]: modalState } : {},
        tidalPlaylistStates: {}, qobuzPlaylistStates: {}, deezerPlaylistStates: {}, spotifyPublicPlaylistStates: {},
        itunesLinkPlaylistStates: {}, beatportChartStates: {},
        ...cardStates,
        discoveryReview: null,
        _discoveryFilters: {},
        escapeHtml: escapeText,
        showToast: (message, type) => w.toasts.push({ message, type }),
        setDiscoveryModalFooterActions: urlHash => w.footers.push(urlHash),
        updateTidalCardProgress: (id, progress) => w.cards.push(['tidal', id, progress]),
        updateQobuzCardProgress: (id, progress) => w.cards.push(['qobuz', id, progress]),
        updateDeezerCardProgress: (id, progress) => w.cards.push(['deezer', id, progress]),
        updateSpotifyPublicCardProgress: (id, progress) => w.cards.push(['spotify_public', id, progress]),
        updateITunesLinkCardProgress: (id, progress) => w.cards.push(['itunes_link', id, progress]),
        updateBeatportCardProgress: (id, progress) => w.cards.push(['beatport', id, progress]),
        fetch: async (url, init) => {
            const body = init && init.body ? JSON.parse(init.body) : null;
            w.requests.push({ url, body });
            return respond ? respond(url, body) : answer({ success: true });
        },
    });
    return w;
}

/** The filter chips' counts, by group, from the bar's HTML. */
const chipCounts = html => Object.fromEntries(
    [...html.matchAll(/data-discovery-filter="([^"]+)"[\s\S]*?discovery-filter-count">(\d+)</g)]
        .map(match => [match[1], Number(match[2])]));

// ── unmatch ─────────────────────────────────────────────────────────────────
await test('unmatching a track of a Qobuz tab playlist', async () => {
    const w = world({ identifier: 'qobuz_123', rows: [foundRow(0), missRow(1)],
                      state: { is_qobuz_playlist: true, qobuz_playlist_id: '123', spotifyMatches: 1, spotify_matches: 1 },
                      cardStates: { qobuzPlaylistStates: { 123: { phase: 'discovered', spotify_matches: 1 } } } });
    assert(await unmatchDiscoveryTrack('qobuz', 'qobuz_123', 0), 'the match is removed');
    eq(w.requests.map(r => [r.url, r.body]), [['/api/qobuz/discovery/unmatch', { identifier: '123', track_index: 0 }]],
        "it goes to Qobuz's route, naming the playlist by Qobuz's id");
    const row = w.state.discovery_results[0];
    eq([row.status_class, row.spotify_track, row.spotify_data], ['not-found', '', null], 'the row is a miss');
    eq(w.rowEls['discovery-row-qobuz_123-0'].cells['.discovery-status'].textContent, 'Not Found', '...and drawn as one');
    eq([w.state.spotifyMatches, w.state.spotify_matches], [0, 0], "the modal's count drops");
    eq([qobuzPlaylistStates['123'].spotify_matches, w.cards], [0, [['qobuz', '123', { spotify_matches: 0, spotify_total: 2 }]]],
        "...and so does the card's");
    eq(chipCounts(w.bar.outerHTML), { all: 2, 'not-found': 2 }, 'the filter chips count it as a miss');
    eq(w.footers, ['qobuz_123'], 'the footer is redrawn');
});

await test('unmatching a track of a mirrored playlist', async () => {
    const w = world({ identifier: 'mirrored_7', rows: [foundRow(0)],
                      state: { is_mirrored_playlist: true, spotifyMatches: 1, spotify_matches: 1 } });
    await unmatchDiscoveryTrack('mirrored', 'mirrored_7', 0);
    eq(w.requests.map(r => [r.url, r.body.identifier]), [['/api/youtube/discovery/unmatch', 'mirrored_7']],
        "a mirrored playlist goes through YouTube's route");
    eq([w.state.spotifyMatches, w.cards], [0, []], 'its count drops, and it has no card');
});

await test('unmatching a track of a ListenBrainz playlist', async () => {
    const w = world({ identifier: 'mbid-1', listenbrainz: true, rows: [foundRow(0)],
                      state: { is_listenbrainz_playlist: true, spotifyMatches: 1 } });
    await unmatchDiscoveryTrack('listenbrainz', 'mbid-1', 0);
    eq(w.requests.map(r => r.url), ['/api/listenbrainz/discovery/unmatch'], 'it goes to its own route');
    eq(w.state.discovery_results[0].status_class, 'not-found', 'its row, kept apart from the others, is updated');
});

await test('an unmatched guess lowers the count only where guesses count', async () => {
    let w = world({ identifier: 'mirrored_7', rows: [foundRow(0), guessRow(1)],
                    state: { is_mirrored_playlist: true, spotifyMatches: 1, spotify_matches: 1 } });
    await unmatchDiscoveryTrack('mirrored', 'mirrored_7', 1);
    eq(w.state.spotifyMatches, 1, "a mirrored playlist's guess never counted");
    w = world({ identifier: 'beatport_h1', rows: [foundRow(0), { ...guessRow(1), status: 'found' }],
                state: { is_beatport_playlist: true, beatport_chart_hash: 'chart-1', spotifyMatches: 2, spotify_matches: 2 },
                cardStates: { beatportChartStates: { 'chart-1': { spotify_matches: 2 } } } });
    await unmatchDiscoveryTrack('beatport', 'beatport_h1', 1);
    eq(w.state.spotifyMatches, 1, "a Beatport chart's guess did");
    eq(w.cards, [['beatport', 'chart-1', { spotify_matches: 1, spotify_total: 2, failed: 1 }]],
        '...and its card, found by the chart it came from, follows');
});

await test('an unmatch that failed changes nothing', async () => {
    const w = world({ identifier: 'mirrored_7', rows: [foundRow(0)],
                      state: { is_mirrored_playlist: true, spotifyMatches: 1 },
                      respond: () => answer({ success: false, error: 'Could not save the track' }, 500) });
    eq(await unmatchDiscoveryTrack('mirrored', 'mirrored_7', 0), false, 'it reports the failure');
    eq([w.state.discovery_results[0].status_class, w.state.spotifyMatches], ['found', 1], 'the row and the count stay');
    eq(w.toasts, [{ message: 'Could not save the track', type: 'error' }], '...saying why');
});

// ── cards after a Fix ───────────────────────────────────────────────────────
const CARD_CASES = [
    { platform: 'tidal', identifier: 'tidal_9', state: { is_tidal_playlist: true, tidal_playlist_id: '9' },
      cards: 'tidalPlaylistStates', cardId: '9' },
    { platform: 'deezer', identifier: 'deezer_55', state: { is_deezer_playlist: true, deezer_playlist_id: '55' },
      cards: 'deezerPlaylistStates', cardId: '55' },
    { platform: 'qobuz', identifier: 'qobuz_123', state: { is_qobuz_playlist: true, qobuz_playlist_id: '123' },
      cards: 'qobuzPlaylistStates', cardId: '123' },
    { platform: 'spotify_public', identifier: 'sp_hash', cards: 'spotifyPublicPlaylistStates', cardId: 'sp1',
      state: { is_spotify_public_playlist: true, spotify_public_playlist_id: 'sp1' } },
    { platform: 'itunes_link', identifier: 'it_hash', cards: 'itunesLinkPlaylistStates', cardId: 'it1',
      state: { is_itunes_link_playlist: true, itunes_link_playlist_id: 'it1' } },
    { platform: 'beatport', identifier: 'beatport_h1', cards: 'beatportChartStates', cardId: 'chart-1',
      state: { is_beatport_playlist: true, beatport_chart_hash: 'chart-1' }, extra: { failed: 1 } },
];

for (const c of CARD_CASES) {
    await test(`a Fix on a ${c.platform} playlist updates its card`, async () => {
        const w = world({ identifier: c.identifier, state: c.state, rows: [missRow(0), missRow(1)],
                          cardStates: { [c.cards]: { [c.cardId]: { spotify_matches: 0 } } } });
        assert(await applyDiscoveryMatch(c.platform, c.identifier, 0, PICK, {}), `${c.platform}: the match is saved`);
        eq(globalThis[c.cards][c.cardId].spotify_matches, 1, `${c.platform}: the count the card redraws from goes up`);
        eq(w.state.spotify_matches, 1, `${c.platform}: ...and the modal's own count`);
        eq(w.cards, [[c.platform, c.cardId, { spotify_matches: 1, spotify_total: 2, ...(c.extra || {}) }]],
            `${c.platform}: the card is redrawn now`);
    });
}

await test('a Fix on a guess counts it once', async () => {
    let w = world({ identifier: 'deezer_55', rows: [guessRow(0)],
                    state: { is_deezer_playlist: true, deezer_playlist_id: '55', spotifyMatches: 1, spotify_matches: 1 },
                    cardStates: { deezerPlaylistStates: { 55: { spotify_matches: 1 } } } });
    await applyDiscoveryMatch('deezer', 'deezer_55', 0, PICK, {});
    eq(w.state.spotifyMatches, 1, 'a Deezer guess already counted');
    w = world({ identifier: 'mirrored_7', rows: [guessRow(0)], state: { is_mirrored_playlist: true } });
    await applyDiscoveryMatch('mirrored', 'mirrored_7', 0, PICK, {});
    eq(w.state.spotifyMatches, 1, "a mirrored playlist's guess didn't");
});

await test('marking a track not available lowers its card count', async () => {
    const w = world({ identifier: 'deezer_55', rows: [foundRow(0), guessRow(1)],
                      state: { is_deezer_playlist: true, deezer_playlist_id: '55', spotifyMatches: 2, spotify_matches: 2 },
                      cardStates: { deezerPlaylistStates: { 55: { spotify_matches: 2 } } } });
    assert(await setDiscoveryTrackUnavailable('deezer', 'deezer_55', 1, true, { quiet: true }), 'the mark is saved');
    eq([w.state.spotifyMatches, deezerPlaylistStates['55'].spotify_matches], [1, 1], 'a Deezer guess counted, so both counts drop');
    eq(w.cards, [['deezer', '55', { spotify_matches: 1, spotify_total: 2 }]], 'the card is redrawn');
});

// ── the filter chips ────────────────────────────────────────────────────────
await test("a row that changes group updates the filter chips' counts", () => {
    const w = world({ identifier: 'mirrored_7', rows: [missRow(0), missRow(1)], state: { is_mirrored_playlist: true } });
    Object.assign(w.state.discovery_results[0], { status: 'Found', status_class: 'found', spotify_track: 'X', manual_match: true });
    updateDiscoveryModalSingleRow('mirrored', 'mirrored_7', 0);
    eq(chipCounts(w.bar.outerHTML), { all: 2, perfect: 1, 'not-found': 1 }, 'the fixed row moves from Not found to Perfect');
});

// ── the Wing It Pool's search ───────────────────────────────────────────────
function poolWorld() {
    const fields = {
        'pool-fix-track-input': el({ value: 'Kick Back' }),
        'pool-fix-artist-input': el({ value: ARTIST }),
        'pool-fix-results': el(),
    };
    const pending = {};
    const w = {
        fields, searches: [],
        answer(key, body, status = 200, which = -1) {
            const list = pending[key] || [];
            const entry = list[which < 0 ? list.length + which : which];
            if (entry) entry(answer(body, status));
        },
    };
    Object.assign(globalThis, {
        document: { getElementById: id => fields[id] || null },
        currentMusicSourceName: 'Deezer',
        // The page's _esc escapes through a DOM element; the same escaping without one.
        _esc: s => String(s ?? '').replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;'),
        fetch: async url => {
            const key = new URLSearchParams(url.split('?')[1]).get('source');
            w.searches.push(url);
            return new Promise(resolve => { (pending[key] = pending[key] || []).push(resolve); });
        },
    });
    return w;
}

const poolResult = (source, id, name, extra = {}) => ({
    id, name, artists: [ARTIST], album: 'KICK BACK', duration_ms: 193000, source, relevance: 1, ...extra,
});
const poolRows = html => html.split(/<div class="pool-fix-result(?=[ "])/).slice(1).map(row => ({
    id: (row.match(/&quot;id&quot;:&quot;([^&]+)&quot;|"id":"([^"]+)"/) || []).slice(1).find(Boolean),
    source: (row.match(/pool-fix-result-source">([^<]*)</) || [])[1],
    version: /^[^"]*is-version/.test(row),
}));

await test('the Wing It Pool searches every source', async () => {
    const w = poolWorld();
    const searching = searchPoolFix();
    await flush();
    eq(w.searches.map(url => new URLSearchParams(url.split('?')[1]).get('source')), ['deezer', 'spotify', 'itunes', 'musicbrainz'],
        'every source is asked at once, the active one first');
    w.answer('spotify', { source: 'spotify', status: 'not_connected', tracks: [] });
    w.answer('deezer', { status: 'ok', tracks: [poolResult('deezer', 'd-kara', 'Kick Back (Karaoke Version)', { relevance: 0.9 }),
                                               poolResult('deezer', 'd-1', 'KICK BACK', { relevance: 1.2 })] });
    await flush();
    assert(w.fields['pool-fix-results'].innerHTML.includes('Still searching iTunes, MusicBrainz'),
        'answers are listed while the rest search');
    w.answer('itunes', { status: 'ok', tracks: [poolResult('itunes', 'i-1', 'KICK BACK', { relevance: 1.4 })] });
    w.answer('musicbrainz', { source: 'musicbrainz', status: 'error', error: 'rate limited', tracks: [] }, 502);
    await searching;
    const html = w.fields['pool-fix-results'].innerHTML;
    eq(poolRows(html).map(row => [row.source, row.id, row.version]),
        [['iTunes', 'i-1', false], ['Deezer', 'd-1', false], ['Deezer', 'd-kara', true]],
        "every source's results, best first and versions last, with no Spotify connection");
    assert(html.includes('fix-result-version">Karaoke<'), 'the karaoke version says so');
    assert(!html.includes('Still searching'), 'nothing is pending once every source answered');
});

await test('when every source fails, the Wing It Pool says why', async () => {
    const w = poolWorld();
    const searching = searchPoolFix();
    await flush();
    for (const key of SOURCES) w.answer(key, { status: 'error', error: 'offline', tracks: [] }, 502);
    await searching;
    assert(w.fields['pool-fix-results'].innerHTML.includes('Search error: offline'), 'the error is shown');
});

await test('a newer Wing It Pool search replaces an older one', async () => {
    const w = poolWorld();
    const first = searchPoolFix();
    await flush();
    const second = searchPoolFix();
    await flush();
    w.answer('deezer', { status: 'ok', tracks: [poolResult('deezer', 'old', 'Old answer')] }, 200, 0);
    await flush();
    assert(!w.fields['pool-fix-results'].innerHTML.includes('Old answer'), "the first search's answer isn't listed");
    for (const key of SOURCES) {
        w.answer(key, { status: 'ok', tracks: [] }, 200, 0);
        w.answer(key, { status: 'ok', tracks: key === 'itunes' ? [poolResult('itunes', 'new', 'KICK BACK')] : [] });
    }
    await Promise.all([first, second]);
    eq(poolRows(w.fields['pool-fix-results'].innerHTML).map(row => row.id), ['new'], "the second search's is");
});

if (failures) {
    report(`\n${failures} assertion(s) failed`);
    process.exit(1);
}
process.stdout.write('discovery row actions harness: all assertions passed\n');
