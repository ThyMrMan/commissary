// Behavioural tests for the discovery Fix dialog's every-source search and its
// review mode (webui/static/wishlist-tools.js + sync-services.js), run against the
// REAL function bodies lifted out by tests/js/vanilla-extract.mjs. Exits non-zero on
// any failure.
//
// Fixing a playlist meant opening each unmatched track, waiting while one source
// after another was searched, and confirming every pick. The dialog now asks every
// metadata source at once and shows each result's artwork, source and how far its
// length is off, with karaoke, cover, live and remix versions sunk or hidden. Review
// steps through every track still waiting for a decision in one dialog: a pick is
// saved at once and the next track loads, 1–9 choose, Enter accepts, S skips, Esc
// closes, and "Not available" keeps a track from coming back.
import { readFileSync } from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

import { extractFunction } from './vanilla-extract.mjs';

const here = path.dirname(fileURLToPath(import.meta.url));
const read = file => readFileSync(path.join(here, '..', '..', 'webui', 'static', file), 'utf8')
    .replace(/\r\n/g, '\n');
const WISHLIST = read('wishlist-tools.js');
const SYNC = read('sync-services.js');

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

/** A page's top-level `const NAME = <literal>;`, evaluated. */
function loadConst(source, name) {
    const match = new RegExp(`^const ${name} = ([\\s\\S]*?);\\n`, 'm').exec(source);
    if (!match) {
        fail(`const ${name} is missing`);
        return;
    }
    globalThis[name] = new Function(`return (${match[1]});`)();
}

for (const name of [
    'getDiscoveryFixState', 'openDiscoveryFixModal', 'closeDiscoveryFixModal', 'searchDiscoveryFix',
    'lookupDiscoveryFixByMbid', 'renderDiscoveryFixResults', 'selectDiscoveryFixTrack', 'applyDiscoveryMatch',
    'discoveryFixSourceList', 'discoverySourceDurationMs', 'discoveryVersionTag', 'discoveryLengthDelta',
    'discoveryThumbUrl', 'discoveryFixCandidateKey', 'discoveryFixRanked', 'discoveryFixVisible', 'discoveryFixChosen',
    'discoveryFixOverlay', 'drawDiscoveryFixResults', 'pickDiscoveryFixResult', 'setDiscoveryFixSource',
    'toggleDiscoveryFixVersions', 'discoveryBackendIdentifier', 'showDiscoveryMatchProgress',
    'refreshDiscoveryModalChrome', 'setDiscoveryTrackUnavailable', 'discoveryReviewWanted', 'discoveryReviewQueue',
    'startDiscoveryReview', 'openDiscoveryReviewTrack', 'setDiscoveryFixReviewChrome', 'discoveryReviewKeydown',
    'chooseDiscoveryFixResult', 'acceptDiscoveryReviewChoice', 'acceptDiscoveryReviewCandidate',
    'skipDiscoveryReviewTrack', 'markDiscoveryReviewUnavailable', 'closeDiscoveryReview',
    'showDiscoveryCardMatches', 'discoveryGuessesCount', 'uncountDiscoveryMatch',
]) {
    load(WISHLIST, name);
}
for (const name of ['discoveryBucketFor', 'discoveryBucketCounts', 'buildDiscoveryFilterBarHtml',
                    'generateDiscoveryActionButton', 'getModalActionButtons', 'discoveryPlatformForState',
                    'formatDuration', 'refreshDiscoveryFilterBar']) {
    load(SYNC, name);
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
    return {
        textContent: '', value: '', innerHTML: '', hidden: false, style: {},
        addEventListener() {}, removeEventListener() {}, focus() {},
        classList: {
            hidden: true,
            add(name) { if (name === 'hidden') this.hidden = true; },
            remove(name) { if (name === 'hidden') this.hidden = false; },
        },
        ...extra,
    };
}

function answer(body, status = 200) {
    return { ok: status >= 200 && status < 300, status, json: async () => body };
}

const ARTIST = 'Kenshi Yonezu';
const LENGTH = 193000;
const SOURCES = ['spotify', 'deezer', 'itunes', 'musicbrainz'];

const result = (source, id, name, extra = {}) => ({
    id, name, artists: [ARTIST], album: 'KICK BACK', duration_ms: LENGTH, image_url: '', source, relevance: 1, ...extra,
});
const missRow = (index, name = 'Kick Back') => ({
    index, yt_track: name, yt_artist: ARTIST, status: 'Not Found', status_class: 'not-found',
});

/** A discovery modal with its Fix dialog, and the page globals the code uses. */
function world({ identifier = 'mirrored_7', state = { is_mirrored_playlist: true }, rows, source = 'iTunes',
                 matchResponse } = {}) {
    const results = rows || [missRow(0)];
    const modalState = {
        phase: 'discovered', spotify_total: results.length, spotifyMatches: 0, spotify_matches: 0,
        playlist: { name: 'Playlist', tracks: results.map(() => ({ duration_ms: LENGTH })) },
        discovery_results: results, discoveryResults: results, ...state,
    };
    const selectors = ['#fix-modal-source-track', '#fix-modal-source-artist', '#fix-modal-track-input',
        '#fix-modal-artist-input', '#fix-modal-mbid-input', '#fix-modal-results', '#fix-modal-source-chips',
        '.discovery-fix-modal-header h2', '.fix-review-progress', '.fix-review-keys', '.fix-review-skip',
        '.fix-review-unavailable', '.fix-modal-cancel', '.discovery-fix-modal'];
    const fields = Object.fromEntries(selectors.map(selector => [selector, el()]));
    for (const selector of ['.fix-review-progress', '.fix-review-keys', '.fix-review-skip', '.fix-review-unavailable']) {
        fields[selector].hidden = true;
    }
    const overlay = el({ querySelector: selector => fields[selector] || null });
    const modal = { querySelector: selector => (selector === '.discovery-fix-modal-overlay' ? overlay : null) };
    const pending = {};
    const w = {
        state: modalState, overlay, fields, requests: [], searches: [], toasts: [], confirms: [], rows: [],
        footers: [], timers: [], listeners: [], clock: 1000, mbid: null,
        answer(key, body, status = 200, which = -1) {
            const list = pending[key] || [];
            const entry = list[which < 0 ? list.length + which : which];
            if (entry) entry(answer(body, status));
        },
    };
    Object.assign(globalThis, {
        document: {
            getElementById: id => (id === `youtube-discovery-modal-${identifier}` ? modal : null),
            addEventListener: (type, fn, capture) => w.listeners.push([type, fn, capture]),
            removeEventListener: (type, fn, capture) => {
                w.listeners = w.listeners.filter(([t, f, c]) => !(t === type && f === fn && c === capture));
            },
            activeElement: { blur() {} },
            querySelector: () => null,
        },
        youtubePlaylistStates: { [identifier]: modalState },
        listenbrainzPlaylistStates: {}, qobuzPlaylistStates: {}, deezerPlaylistStates: {}, tidalPlaylistStates: {},
        spotifyPublicPlaylistStates: {}, itunesLinkPlaylistStates: {},
        currentDiscoveryFix: { platform: null, identifier: null, trackIndex: null, sourceTrack: null, sourceArtist: null },
        discoveryFixEnterHandler: null,
        discoveryFixMbidEnterHandler: null,
        discoveryFixResults: { token: 0, pinned: [], sources: [], settled: true, chosenKey: null, order: null },
        discoveryFixView: { source: 'all', hideVersions: false },
        discoveryReview: null,
        _discoveryFilters: {},
        _isSoulsyncStandalone: false,
        currentMusicSourceName: source,
        escapeHtml: escapeText,
        parseMusicBrainzMbid: value => value,
        showToast: (message, type) => w.toasts.push({ message, type }),
        showConfirmDialog: async opts => { w.confirms.push(opts.title); return true; },
        updateDiscoveryModalSingleRow: (...args) => w.rows.push(args),
        setDiscoveryModalFooterActions: urlHash => w.footers.push(urlHash),
        setTimeout: (fn, ms) => { w.timers.push({ fn, ms }); return w.timers.length; },
        clearTimeout: () => {},
        fetch: async (url, init) => {
            if (url.startsWith('/api/discovery/fix-search')) {
                const key = new URLSearchParams(url.split('?')[1]).get('source');
                w.searches.push(url);
                return new Promise(resolve => { (pending[key] = pending[key] || []).push(resolve); });
            }
            const body = init && init.body ? JSON.parse(init.body) : null;
            w.requests.push({ url, body });
            if (url.startsWith('/api/musicbrainz/recording/')) return answer(w.mbid || {});
            if (url === '/api/discovery/unavailable') return answer({ success: true });
            return answer(matchResponse ? matchResponse(body)
                : { success: true, result: { spotify_data: { id: body.spotify_track.id, saved_by: 'server' } } });
        },
    });
    Date.now = () => w.clock;
    return w;
}

/** Run the timers a dialog set to start its search; the 15 s answer deadlines stay put. */
function runSearchTimers(w) {
    const due = w.timers.filter(timer => timer.ms <= 1000);
    w.timers = w.timers.filter(timer => timer.ms > 1000);
    due.forEach(timer => timer.fn());
    return due.map(timer => timer.ms);
}

async function answerEverySource(w, answers = {}) {
    runSearchTimers(w);
    await flush();
    for (const key of SOURCES) w.answer(key, { status: 'ok', tracks: answers[key] || [] });
    await flush();
}

const sourcesAsked = w => w.searches.map(url => new URLSearchParams(url.split('?')[1]).get('source'));
const cards = w => w.fields['#fix-modal-results'].innerHTML.split(/<div class="fix-result-card(?=[ "])/).slice(1).map(html => ({
    key: (html.match(/data-key="([^"]+)"/) || [])[1],
    chosen: /^[^"]*is-chosen/.test(html),
    version: /^[^"]*is-version/.test(html),
    html,
}));
const chips = w => w.fields['#fix-modal-source-chips'].innerHTML.split('<button').slice(1).map(html => ({
    key: (html.match(/data-fix-source="([^"]+)"/) || [])[1] || (html.includes('fix-versions-chip') ? 'versions' : ''),
    active: /class="[^"]*is-active/.test(html),
    disabled: /"\s+disabled|\sdisabled\s/.test(html),
    title: (html.match(/title="([^"]*)"/) || [])[1] || '',
    count: (html.match(/discovery-filter-count">([^<]*)</) || [])[1],
}));
const chip = (w, key) => chips(w).find(entry => entry.key === key) || {};

function press(w, key, extra = {}) {
    const event = {
        key, repeat: false, target: { tagName: 'DIV' }, prevented: false, stopped: false,
        preventDefault() { this.prevented = true; }, stopPropagation() { this.stopped = true; }, ...extra,
    };
    w.listeners.filter(([type]) => type === 'keydown').forEach(([, fn]) => fn(event));
    return event;
}

const saves = w => w.requests.filter(r => r.url.endsWith('/discovery/update_match'));

// ── what counts as a version ────────────────────────────────────────────────
await test('what counts as a version', () => {
    const names = ['Live Forever', 'Song (Live)', 'Song - Live at Wembley', 'Song (Original Mix)',
                   'Song (Extended Mix)', 'Song (Radio Edit)', 'Song (Acoustic Cover)', 'Song [Karaoke Version]'];
    eq(names.map(name => discoveryVersionTag({ name })), ['', 'live', 'live', '', 'remix', '', 'cover', 'karaoke'],
        "only a title's bracketed or dashed-off part counts");
    eq(discoveryVersionTag({ name: 'Song', album: 'Karaoke Hits, Vol. 3' }), 'karaoke', 'a karaoke album counts');
    eq(discoveryVersionTag({ name: 'Song', artists: [{ name: 'The Tribute Band' }] }), 'cover', '...and a tribute act');
    eq(discoveryVersionTag({ name: 'Song', album: 'Live at Budokan' }), 'live', '...and a live album');
});

// ── every source at once ────────────────────────────────────────────────────
await test('every source is searched at once', async () => {
    const w = world();
    openDiscoveryFixModal('mirrored', 'mirrored_7', 0);
    eq(runSearchTimers(w), [500], 'the dialog starts its search as before');
    await flush();
    eq(sourcesAsked(w), ['itunes', 'spotify', 'deezer', 'musicbrainz'],
        'every source is asked before any answers, the active one first');
    const params = new URLSearchParams(w.searches[0].split('?')[1]);
    eq([params.get('track'), params.get('artist')], ['Kick Back', ARTIST], '...for the track in the search boxes');
    assert(w.fields['#fix-modal-results'].innerHTML.includes('Searching iTunes, Spotify, Deezer, MusicBrainz'),
        'the dialog says who it is waiting on');
    eq(chips(w).map(entry => [entry.key, entry.count]),
        [['all', '…'], ['itunes', '…'], ['spotify', '…'], ['deezer', '…'], ['musicbrainz', '…']],
        'a chip for every source, each still searching');

    w.answer('deezer', { status: 'ok', tracks: [result('deezer', 'd-1', 'KICK BACK')] });
    await flush();
    eq(cards(w).map(card => card.key), ['deezer:d-1'], 'the first answer is listed while the rest search');
    assert(w.fields['#fix-modal-results'].innerHTML.includes('Still searching iTunes, Spotify, MusicBrainz'),
        '...saying who is still searching');

    w.answer('spotify', { source: 'spotify', status: 'not_connected', tracks: [] });
    w.answer('itunes', { source: 'itunes', status: 'error', error: 'the source is down', tracks: [] }, 502);
    w.answer('musicbrainz', { status: 'ok', tracks: [result('musicbrainz', 'mb-1', 'Kick Back')] });
    await flush();
    eq([chip(w, 'all').count, chip(w, 'deezer').count, chip(w, 'musicbrainz').count], ['2', '1', '1'],
        "chips count each source's results");
    eq([chip(w, 'spotify').disabled, chip(w, 'spotify').count], [true, '–'], "a source that isn't connected is greyed out");
    eq([chip(w, 'itunes').count, chip(w, 'itunes').title], ['!', 'iTunes: the source is down'], 'a failed source says why');
    assert(!w.fields['#fix-modal-results'].innerHTML.includes('Still searching'), 'nothing is pending once all answered');
});

const MIXED = {
    deezer: [
        result('deezer', 'd-live', 'Kick Back (Live at Budokan)', { relevance: 1.4 }),
        result('deezer', 'd-real', 'KICK BACK', {
            relevance: 1.2, image_url: 'https://e-cdns-images.dzcdn.net/images/cover/abc/1000x1000-000000-80-0-0.jpg' }),
    ],
    itunes: [
        result('itunes', 'i-kara', 'Kick Back (Karaoke Version)', { relevance: 1.3 }),
        result('itunes', 'i-long', 'KICK BACK', {
            relevance: 1.25, duration_ms: 250000, image_url: 'https://is1-ssl.mzstatic.com/image/thumb/Music/x/3000x3000bb.jpg' }),
    ],
};

async function searched(answers, options = {}) {
    const w = world(options);
    openDiscoveryFixModal('mirrored', 'mirrored_7', 0);
    await answerEverySource(w, answers);
    return w;
}

await test('every source is ranked together, with versions below', async () => {
    const w = await searched(MIXED);
    eq(cards(w).map(card => card.key), ['deezer:d-real', 'itunes:i-long', 'deezer:d-live', 'itunes:i-kara'],
        'relevance and a matching length first, then the versions');
    const [real, long, live, karaoke] = cards(w);
    eq([real.version, live.version, karaoke.version], [false, true, true], 'versions are marked');
    assert(live.html.includes('fix-result-version">Live<') && karaoke.html.includes('fix-result-version">Karaoke<'),
        '...saying which kind');
    assert(real.html.includes('same length') && long.html.includes('+57s') && long.html.includes('is-far'),
        'each result says how far its length is off');
    assert(real.html.includes('fix-result-source">Deezer<') && long.html.includes('fix-result-source">iTunes<'),
        '...and which source it came from');
    assert(real.html.includes('/250x250-000000-80-0-0.jpg') && long.html.includes('/100x100bb.jpg'),
        '...with its artwork, at thumbnail size');
    assert(!real.html.includes('fix-result-number'), 'a single fix numbers nothing');
});

await test('a live playlist track keeps live results up', async () => {
    const w = await searched({ deezer: [result('deezer', 'd-live', 'Kick Back (Live)', { relevance: 1.4 }),
                                        result('deezer', 'd-real', 'KICK BACK', { relevance: 1.2 })] },
                             { rows: [missRow(0, 'Kick Back (Live)')] });
    eq(cards(w).map(card => [card.key, card.version]), [['deezer:d-live', false], ['deezer:d-real', false]],
        'a live cut is what this track is');
});

await test('chips filter by source and hide versions', async () => {
    const w = await searched(MIXED);
    setDiscoveryFixSource('itunes');
    eq(cards(w).map(card => card.key), ['itunes:i-long', 'itunes:i-kara'], 'a source chip shows that source alone');
    assert(chip(w, 'itunes').active && !chip(w, 'all').active, '...and is the one marked');
    toggleDiscoveryFixVersions();
    eq(cards(w).map(card => card.key), ['itunes:i-long'], 'hiding versions drops them');
    eq([chip(w, 'versions').active, chip(w, 'versions').count], [true, '2'], 'the versions chip is on and counts them');
    eq([chip(w, 'all').count, chip(w, 'deezer').count], ['2', '1'], 'the other chips count what is left');
    setDiscoveryFixSource('all');
    eq(cards(w).map(card => card.key), ['deezer:d-real', 'itunes:i-long'], 'every source again, versions still hidden');
});

await test('a suggestion found again by the search shows once, on top', async () => {
    const suggestion = { ...result('itunes', 'i-real', 'KICK BACK'), confidence: 0.84 };
    delete suggestion.relevance;
    const w = world({ rows: [{ ...missRow(0), status: 'Wing It', status_class: 'wing-it', wing_it_fallback: true,
                               suggestions: [suggestion] }] });
    openDiscoveryFixModal('mirrored', 'mirrored_7', 0);
    eq(cards(w).map(card => card.key), ['itunes:i-real'], 'Fix opens on the suggestion straight away');
    await answerEverySource(w, { itunes: [result('itunes', 'i-real', 'KICK BACK', { relevance: 1.5 }),
                                         result('itunes', 'i-2', 'Kick Back', { relevance: 1.6 })] });
    eq(sourcesAsked(w).length, 4, '...and searches every source too');
    eq(cards(w).map(card => card.key), ['itunes:i-real', 'itunes:i-2'], "the suggestion stays on top, listed once");
    assert(cards(w)[0].html.includes('fix-result-suggested') && cards(w)[0].html.includes('84%'),
        "...with discovery's score for it");
});

await test('an answer to a replaced search is dropped', async () => {
    const w = world();
    openDiscoveryFixModal('mirrored', 'mirrored_7', 0);
    runSearchTimers(w);
    await flush();
    searchDiscoveryFix();
    await flush();
    w.answer('deezer', { status: 'ok', tracks: [result('deezer', 'old', 'Old answer')] }, 200, 0);
    await flush();
    eq(cards(w).map(card => card.key), [], "the first search's answer is not listed");
    w.answer('deezer', { status: 'ok', tracks: [result('deezer', 'new', 'KICK BACK')] });
    await flush();
    eq(cards(w).map(card => card.key), ['deezer:new'], "the second search's is");
});

await test('an MBID lookup is listed and saved as a MusicBrainz result', async () => {
    const w = world();
    openDiscoveryFixModal('mirrored', 'mirrored_7', 0);
    w.fields['#fix-modal-mbid-input'].value = 'mbid-1';
    w.mbid = { id: 'mbid-1', name: 'KICK BACK', artists: [ARTIST], album: 'KICK BACK', duration_ms: LENGTH };
    await lookupDiscoveryFixByMbid();
    await flush();
    eq(cards(w).map(card => card.key), ['musicbrainz:mbid-1'], 'the recording is listed under MusicBrainz');
    pickDiscoveryFixResult('musicbrainz:mbid-1');
    await flush();
    eq(saves(w).map(r => r.body.spotify_track.source), ['musicbrainz'], '...and saved as one');
});

await test('a pick sends what its result knows, and keeps what the server saved', async () => {
    const w = await searched({ deezer: [result('deezer', 'd-real', 'KICK BACK', {
        track_number: 3, disc_number: 1, release_date: '2022-11-16', total_tracks: 4, album_type: 'single', album_id: '' })] });
    pickDiscoveryFixResult('deezer:d-real');
    await flush();
    eq(w.confirms, ['Confirm Match'], 'outside a review a pick is still confirmed');
    const sent = (saves(w)[0] || { body: { spotify_track: {} } }).body.spotify_track;
    eq([sent.source, sent.track_number, sent.disc_number, sent.release_date, sent.total_tracks, sent.album_type,
        'album_id' in sent], ['deezer', 3, 1, '2022-11-16', 4, 'single', false],
        'the pick names its source and its place on its album');
    eq(w.state.discovery_results[0].spotify_data, { id: 'd-real', saved_by: 'server' },
        'the row keeps the match the server saved');
});

// ── Review ──────────────────────────────────────────────────────────────────
const reviewRows = () => [
    { index: 0, yt_track: 'Lemon', yt_artist: ARTIST, status: 'Found', status_class: 'found', spotify_track: 'Lemon', confidence: 0.97 },
    { index: 1, yt_track: 'Kick Back', yt_artist: ARTIST, status: 'Found', status_class: 'found', spotify_track: 'Kick Back (TV)', confidence: 0.8,
      spotify_data: { id: 'x-kick', name: 'Kick Back (TV)' } },
    { index: 2, yt_track: 'Paprika', yt_artist: ARTIST, status: 'Found', status_class: 'found', spotify_track: 'Paprika', confidence: 0.5, manual_match: true },
    { index: 3, yt_track: 'Peace Sign', yt_artist: ARTIST, status: 'Wing It', status_class: 'wing-it', wing_it_fallback: true },
    missRow(4, 'Loser'),
    { index: 5, yt_track: 'Flamingo', yt_artist: ARTIST, status: 'Error', status_class: 'error' },
    { index: 6, yt_track: 'Gone', yt_artist: ARTIST, status: 'Not available', status_class: 'unavailable', unavailable: true },
];

function reviewing(options = {}) {
    const w = world({ rows: reviewRows(), ...options });
    startDiscoveryReview(options.platform || 'mirrored', options.identifier || 'mirrored_7');
    return w;
}

await test('the footer offers a review of what still waits for a decision', () => {
    const w = world({ rows: reviewRows() });
    const html = getModalActionButtons('mirrored_7', 'discovered', w.state);
    eq((html.match(/🎯 Review \((\d+)\)/) || [])[1], '4', 'a low-confidence match, a guess, a miss and an error');
    assert(html.includes(`startDiscoveryReview('mirrored', 'mirrored_7')`), '...reviewed in this modal');
    eq((html.match(/Retry Failed \((\d+)\)/) || [])[1], '3', 'Retry Failed leaves out a track marked not available');
    const settled = [reviewRows()[0], reviewRows()[2], reviewRows()[6]];
    const none = getModalActionButtons('mirrored_7', 'discovered',
        { ...w.state, discovery_results: settled, discoveryResults: settled });
    assert(!none.includes('startDiscoveryReview'), 'nothing to review, no Review');
});

await test('a review opens the first track that waits for a decision', async () => {
    const w = reviewing();
    eq(currentDiscoveryFix.trackIndex, 1, 'the low-confidence match comes first');
    assert(!w.overlay.classList.hidden, 'the dialog is shown');
    eq(w.fields['.discovery-fix-modal-header h2'].textContent, 'Review Matches', '...as a review');
    eq(w.fields['.fix-review-progress'].textContent, 'Track 1 of 4', '...saying how far along it is');
    eq(['.fix-review-progress', '.fix-review-keys', '.fix-review-skip', '.fix-review-unavailable']
        .map(selector => w.fields[selector].hidden), [false, false, false, false], '...with its keys, Skip and Not available');
    eq(w.fields['.fix-modal-cancel'].textContent, 'Close', 'Cancel reads Close');
    eq(w.listeners.map(([type, , capture]) => [type, capture]), [['keydown', true]], 'the review listens for its keys');
    eq(runSearchTimers(w), [0], 'the search starts at once');
});

await test('a number chooses a result and Enter saves it, with nothing to confirm', async () => {
    const w = reviewing();
    await answerEverySource(w, { deezer: [result('deezer', 'd-1', 'KICK BACK', { relevance: 1.5 }),
                                          result('deezer', 'd-2', 'Kick Back', { relevance: 1.2 })] });
    w.clock += 1000;
    eq(cards(w).filter(card => card.chosen).map(card => card.key), ['deezer:d-1'],
        'with every source in, the top result waits for Enter');
    assert(cards(w)[0].html.includes('fix-result-number">1<'), 'results are numbered');
    const pressed = press(w, '2');
    eq([pressed.prevented, pressed.stopped], [true, true], "the key is the review's alone");
    eq(cards(w).filter(card => card.chosen).map(card => card.key), ['deezer:d-2'], '2 chooses the second result');
    press(w, 'Enter');
    await flush();
    eq(saves(w).map(r => [r.url, r.body.track_index, r.body.spotify_track.id]),
        [['/api/youtube/discovery/update_match', 1, 'd-2']], "Enter saves it as this track's match");
    eq(w.confirms, [], '...without asking');
    eq([currentDiscoveryFix.trackIndex, w.fields['.fix-review-progress'].textContent], [3, 'Track 2 of 4'],
        'the next track opens');
});

await test('a later answer joins at the end once a result is chosen', async () => {
    const w = reviewing();
    runSearchTimers(w);
    await flush();
    w.answer('deezer', { status: 'ok', tracks: [result('deezer', 'd-1', 'A', { relevance: 1.0 }),
                                               result('deezer', 'd-2', 'B', { relevance: 0.9 })] });
    await flush();
    w.clock += 1000;
    press(w, '2');
    w.answer('itunes', { status: 'ok', tracks: [result('itunes', 'i-best', 'KICK BACK', { relevance: 2 })] });
    await flush();
    eq(cards(w).map(card => card.key), ['deezer:d-1', 'deezer:d-2', 'itunes:i-best'], 'the list holds still under a choice');
    eq(cards(w).filter(card => card.chosen).map(card => card.key), ['deezer:d-2'], '...and the choice stays chosen');
    toggleDiscoveryFixVersions();
    eq(cards(w).filter(card => card.chosen).map(card => card.key), ['deezer:d-2'], '...through a change of filter');
});

await test('Enter waits for every source unless a result was chosen', async () => {
    const w = reviewing();
    runSearchTimers(w);
    await flush();
    w.answer('deezer', { status: 'ok', tracks: [result('deezer', 'd-1', 'KICK BACK', { relevance: 1.5 })] });
    await flush();
    w.clock += 1000;
    press(w, 'Enter');
    await flush();
    eq(saves(w), [], 'a later answer could still rank above the top result');
    assert(w.toasts.some(t => t.message.includes('press 1–9')), '...so the review says to choose one');
    press(w, '1');
    press(w, 'Enter');
    await flush();
    eq(saves(w).map(r => r.body.spotify_track.id), ['d-1'], 'a chosen result is saved at once');
});

await test('a kept suggestion can be accepted before the search answers', async () => {
    const w = reviewing({ rows: [{ ...missRow(0), status: 'Wing It', status_class: 'wing-it', wing_it_fallback: true,
                                   suggestions: [{ ...result('itunes', 'i-sugg', 'KICK BACK'), confidence: 0.84 }] }] });
    runSearchTimers(w);
    await flush();
    eq(sourcesAsked(w).length, 4, 'every source is still searching');
    w.clock += 1000;
    press(w, 'Enter');
    await flush();
    eq(saves(w).map(r => r.body.spotify_track.id), ['i-sugg'], 'no search answer can rank above a kept suggestion');
});

await test('a replaced search finishing lets no Enter through early', async () => {
    const w = reviewing();
    runSearchTimers(w);
    await flush();
    searchDiscoveryFix();
    await flush();
    for (const key of SOURCES) w.answer(key, { status: 'ok', tracks: [] }, 200, 0);
    await flush();
    w.answer('deezer', { status: 'ok', tracks: [result('deezer', 'd-1', 'KICK BACK', { relevance: 1.5 })] });
    await flush();
    w.clock += 1000;
    press(w, 'Enter');
    await flush();
    eq(saves(w), [], 'the new search still waits on three sources');
});

await test('S skips a track', () => {
    const w = reviewing();
    w.clock += 1000;
    press(w, 's');
    eq([currentDiscoveryFix.trackIndex, w.fields['.fix-review-progress'].textContent], [3, 'Track 2 of 4'],
        'the next track opens');
    eq(w.requests, [], 'nothing is saved');
});

await test('an early click and a held key save nothing', async () => {
    const w = reviewing();
    await answerEverySource(w, { deezer: [result('deezer', 'd-1', 'KICK BACK')] });
    pickDiscoveryFixResult('deezer:d-1');
    await flush();
    eq(saves(w), [], 'a click in the moment the track loaded, meant for the last one, is ignored');
    w.clock += 1000;
    press(w, 'Enter', { repeat: true });
    await flush();
    eq(saves(w), [], 'a held Enter is ignored');
    pickDiscoveryFixResult('deezer:d-1');
    await flush();
    eq(saves(w).map(r => r.body.spotify_track.id), ['d-1'], 'a click once the track has settled saves the result');
});

await test('typing in the search boxes is left alone, and Esc still closes', async () => {
    const w = reviewing();
    await answerEverySource(w, { deezer: [result('deezer', 'd-1', 'A', { relevance: 1.5 }), result('deezer', 'd-2', 'B')] });
    w.clock += 1000;
    const typed = press(w, '2', { target: { tagName: 'INPUT' } });
    eq([typed.prevented, cards(w).filter(card => card.chosen).map(card => card.key)], [false, ['deezer:d-1']],
        'a digit typed into a search box stays there');
    press(w, 's', { target: { tagName: 'INPUT' } });
    eq(currentDiscoveryFix.trackIndex, 1, '...and so does an S');
    press(w, 'Escape', { target: { tagName: 'INPUT' } });
    eq([discoveryReview, w.overlay.classList.hidden, w.listeners.length], [null, true, 0], 'Esc ends the review');
});

await test('Not available marks the track and moves on', async () => {
    const w = reviewing();
    w.clock += 1000;
    await markDiscoveryReviewUnavailable();
    await flush();
    eq(w.requests.map(r => [r.url, r.body]),
        [['/api/discovery/unavailable', { identifier: 'mirrored_7', track_index: 1, unavailable: true }]], 'the mark is saved');
    const row = w.state.discovery_results[1];
    eq([row.status, row.status_class, row.spotify_track, row.spotify_data], ['Not available', 'unavailable', '', null],
        'the row loses its match');
    eq(w.rows, [['mirrored', 'mirrored_7', 1]], '...and is redrawn');
    eq([currentDiscoveryFix.trackIndex, w.fields['.fix-review-progress'].textContent], [3, 'Track 2 of 4'],
        'the next track opens');
});

await test('a playlist from the Qobuz tab is marked under its own id', async () => {
    const w = reviewing({ identifier: 'qobuz_123', platform: 'qobuz', rows: [missRow(0)],
                          state: { is_qobuz_playlist: true, qobuz_playlist_id: '123' } });
    w.clock += 1000;
    await markDiscoveryReviewUnavailable();
    eq(w.requests.map(r => r.body.identifier), ['123'], "the Qobuz routes know the playlist by Qobuz's id");
});

await test('a track settled during the review is passed over', () => {
    const w = reviewing();
    Object.assign(w.state.discovery_results[3], { status: 'Found', status_class: 'found', manual_match: true,
                                                  wing_it_fallback: false });
    w.clock += 1000;
    press(w, 's');
    eq([currentDiscoveryFix.trackIndex, w.fields['.fix-review-progress'].textContent], [4, 'Track 3 of 4'],
        'the guess fixed from its row is not offered again');
});

await test('a match that fails to save keeps the track open', async () => {
    const w = reviewing({ matchResponse: () => ({ error: 'nope' }) });
    await answerEverySource(w, { deezer: [result('deezer', 'd-1', 'KICK BACK')] });
    w.clock += 1000;
    press(w, 'Enter');
    await flush();
    eq(currentDiscoveryFix.trackIndex, 1, 'the same track stays open');
    assert(w.toasts.some(t => t.type === 'error' && t.message.includes('not saved')), '...saying the match was not saved');
    eq(discoveryReview && discoveryReview.matched, 0, 'nothing is counted');
});

await test('the last track ends the review with a summary', async () => {
    const w = reviewing();
    w.clock += 1000;
    press(w, 's');
    w.clock += 1000;
    await markDiscoveryReviewUnavailable();
    await flush();
    await answerEverySource(w, { deezer: [result('deezer', 'd-loser', 'LOSER', { relevance: 1.5 })] });
    w.clock += 1000;
    press(w, 'Enter');
    await flush();
    w.clock += 1000;
    press(w, 's');
    eq([discoveryReview, w.overlay.classList.hidden, w.listeners.length], [null, true, 0],
        'the dialog closes and the keys are released');
    eq(w.toasts[w.toasts.length - 1], { message: 'Review finished: 1 matched, 1 not available, 2 skipped', type: 'success' },
        'a summary says what was done');
    eq(w.footers, ['mirrored_7'], 'the footer is redrawn once, Review count and all');
});

await test('Esc or Close ends a review early', () => {
    let w = reviewing();
    w.clock += 1000;
    press(w, 's');
    press(w, 'Escape');
    eq(w.toasts[w.toasts.length - 1], { message: 'Review closed: 1 skipped', type: 'success' }, 'Esc says what was done');
    eq([discoveryReview, w.overlay.classList.hidden], [null, true], '...and closes the dialog');
    w = reviewing();
    closeDiscoveryFixModal();
    eq([discoveryReview, w.overlay.classList.hidden, w.listeners.length, w.toasts], [null, true, 0, []],
        "the dialog's Close ends the review too, with nothing to report");
});

// ── a row marked not available ──────────────────────────────────────────────
await test('a row marked not available', () => {
    const w = world({ rows: reviewRows() });
    const row = w.state.discovery_results[6];
    eq(discoveryBucketFor(row), 'unavailable', 'has its own filter bucket');
    eq(discoveryBucketCounts(w.state.discovery_results).unavailable, 1, '...counted');
    assert(buildDiscoveryFilterBarHtml(w.state, 'mirrored_7').includes('Not available'), '...with its own chip');
    const html = generateDiscoveryActionButton(row, 'mirrored_7', 'mirrored');
    assert(html.includes(`openDiscoveryFixModal('mirrored', 'mirrored_7', 6)`), 'Fix can still find it a match');
    assert(html.includes(`setDiscoveryTrackUnavailable('mirrored', 'mirrored_7', 6, false)`), '↺ puts it back in line');
});

await test('marking a track available again', async () => {
    const w = world({ rows: reviewRows() });
    assert(await setDiscoveryTrackUnavailable('mirrored', 'mirrored_7', 6, false), 'the change is saved');
    eq(w.requests.map(r => r.body), [{ identifier: 'mirrored_7', track_index: 6, unavailable: false }], 'the server is told');
    const row = w.state.discovery_results[6];
    eq([row.status_class, row.unavailable], ['not-found', false], 'the row is a miss again');
    eq(w.footers, ['mirrored_7'], 'the footer is redrawn, Review count and all');
});

if (failures) {
    report(`\n${failures} assertion(s) failed`);
    process.exit(1);
}
process.stdout.write('discovery review harness: all assertions passed\n');
