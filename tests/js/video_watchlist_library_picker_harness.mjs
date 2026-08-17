// Behavioural harness for the watchlist card's Library picker
// (webui/static/video/video-watchlist.js). Exits non-zero on any failure.
//
// 1.9.23 shipped the column (video_watchlist.root_folder_id), the endpoint
// (POST /api/video/watchlist/library) and the cascade that keeps queued
// episodes in step — and no control anywhere that could set one. The choice
// only matters BEFORE a show's first download, which is exactly the window in
// which nothing could express it.
//
// video-watchlist.js is a browser-coupled IIFE, so rather than eval the whole
// file this pulls the functions under test out by name and runs them in one
// shared scope. They stay bound to the real source — a rename or a deletion
// fails the extraction rather than passing vacuously.
import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { dirname, join } from 'node:path';

const here = dirname(fileURLToPath(import.meta.url));
// Normalised to LF: this file is CRLF in the working tree, and every extraction
// below anchors on a newline followed by an indent.
const src = readFileSync(
    join(here, '..', '..', 'webui', 'static', 'video', 'video-watchlist.js'), 'utf8')
    .replace(/\r\n/g, '\n');

function assert(cond, msg) {
    if (!cond) { console.error('FAIL: ' + msg); process.exit(1); }
}

// Every function in this file lives inside the page IIFE, so it is indented one
// level and its closing brace sits at column 4. Nested blocks are deeper, so
// the first `\n    }` after the header is the end of the function.
function extract(name) {
    const start = src.indexOf(`    function ${name}(`);
    assert(start !== -1, `function ${name}() is gone from video-watchlist.js`);
    const end = src.indexOf('\n    }', start);
    assert(end !== -1, `could not find the end of ${name}()`);
    return src.slice(start, end + 6);
}

// Same idea for a top-level `var NAME = …;` — used for the icon, which
// libCogHTML closes over.
function extractVar(name) {
    const start = src.indexOf(`    var ${name} =`);
    assert(start !== -1, `var ${name} is gone from video-watchlist.js`);
    const end = src.indexOf(';\n', start);
    assert(end !== -1, `could not find the end of var ${name}`);
    return src.slice(start, end + 1);
}

const api = (0, eval)(`(function () {
    var _showLibs = [];
${extractVar('LIB_ICON')}
${extract('esc')}
${extract('isAdmin')}
${extract('mayGrab')}
${extract('mayChooseLibrary')}
${extract('libLabel')}
${extract('libById')}
${extract('libCogHTML')}
${extract('libSlot')}
${extract('paintLibCogs')}
${extract('libPickerHTML')}
${extract('saveWatchlistLibrary')}
    return { esc: esc, libLabel: libLabel, libCogHTML: libCogHTML, libSlot: libSlot,
             paintLibCogs: paintLibCogs, libPickerHTML: libPickerHTML,
             saveWatchlistLibrary: saveWatchlistLibrary, icon: LIB_ICON,
             setLibs: function (l) { _showLibs = l; } };
})()`);

const TV = [{ id: 2, label: 'TV-Shows', server_title: 'TV Shows' },
            { id: 5, label: 'Anime', server_title: 'Anime', category: 'Anime' }];

// ── libSlot: who gets the control at all ────────────────────────────────────
api.setLibs(TV);

const unowned = { tmdb_id: 1399, title: 'Brand New Anime', root_folder_id: null };
const slot = api.libSlot(unowned, 'show');
assert(slot.includes('data-vwlp-lib-slot'), 'an un-owned followed show gets no slot');
assert(slot.includes('data-tmdb="1399"'), `tmdb id must ride on the slot: ${slot}`);
assert(slot.includes('data-current=""'), `no choice recorded must render as empty: ${slot}`);

// THE reason query_watchlist had to start returning root_folder_id: a control
// that cannot show what it is set to is indistinguishable from one never set.
assert(api.libSlot({ tmdb_id: 1, title: 'X', root_folder_id: 5 }, 'show').includes('data-current="5"'),
    'the slot does not carry the Library already chosen');
// A missing field (an older payload, or the auto-added arm) must read as
// "default", never as the string "undefined".
assert(api.libSlot({ tmdb_id: 1, title: 'X' }, 'show').includes('data-current=""'),
    'an absent root_folder_id must render as empty, not "undefined"');

// A show you already own has its Library settled, and its detail page reassigns
// it for real. Offering a second control for the same decision — one that only
// records an intent — would be worse than offering none.
assert(api.libSlot({ tmdb_id: 1, title: 'X', library_id: 44 }, 'show') === '',
    'an owned show must not get the picker');
assert(api.libSlot({ tmdb_id: 1, title: 'Y' }, 'person') === '', 'people have no destination');
assert(api.libSlot({ tmdb_id: 1, title: 'Y' }, 'studio') === '', 'studios have no destination');
assert(api.libSlot({ title: 'No id' }, 'show') === '', 'without a tmdb id there is nothing to save against');

// The server refuses profiles that cannot download (the choice redirects where
// grabs land), AND — because the blueprint gate treats a write ending in
// '/library' as a library mutation — non-admins outright. Rendering a control
// that 403s on every press is the dead-end the Manage panel was fixed for.
globalThis.canDownload = () => false;
assert(api.libSlot(unowned, 'show') === '', 'a profile without download rights must not see the picker');
globalThis.canDownload = () => true;
globalThis.currentProfile = { is_admin: false, can_download: true };
assert(api.libSlot(unowned, 'show') === '',
    'a non-admin sees a picker whose every save the blueprint gate answers "Admin only."');
globalThis.currentProfile = { is_admin: true, can_download: true };
assert(api.libSlot(unowned, 'show') !== '', 'an admin who can download must see it');
delete globalThis.canDownload;
delete globalThis.currentProfile;

// Show titles come from TMDB. A quote must not close the attribute and start a
// new one — the title is re-emitted into the picker's heading later.
const tricky = api.libSlot({ tmdb_id: 1, title: 'Bob\'s "Burgers" & Co' }, 'show');
assert(tricky.includes('data-title="Bob\'s &quot;Burgers&quot; &amp; Co"'),
    `title not attribute-escaped: ${tricky}`);
const evil = api.libSlot({ tmdb_id: 1, title: 'x" onmouseover="alert(1)' }, 'show');
assert(!evil.includes('onmouseover="alert(1)"'), `attribute injection via the title: ${evil}`);

// ── libCogHTML: one Library is not a choice ─────────────────────────────────
api.setLibs([]);
assert(api.libCogHTML('') === '', 'no configured Libraries must render no cog');
api.setLibs([TV[0]]);
assert(api.libCogHTML('') === '', 'a single Library is not a decision — the card must be unchanged');

api.setLibs(TV);
const cog = api.libCogHTML('');
assert(cog.includes('data-vwlp-lib'), 'the cog carries no handler hook');
assert(/type="button"/.test(cog), 'a bare <button> defaults to submit');
assert(cog.includes(api.icon), 'the cog lost its icon');
assert(/aria-label="[^"]+"/.test(cog), 'an icon-only button needs an accessible name');

// The card is a poster; the cog has to say what it is set to without being
// opened, or the only way to read the current value is to open it.
const chosen = api.libCogHTML('5');
assert(chosen.includes('Anime'), `the cog does not name the chosen Library: ${chosen}`);
assert(!cog.includes('Anime'), 'an unset cog must not claim a Library');
assert(chosen.includes('accent-rgb'), 'a chosen Library should read differently from the default');

// A Library that has since been deleted from the registry: the fallback is what
// will actually happen, so the cog must say so rather than name a ghost.
const gone = api.libCogHTML('999');
assert(!gone.includes('Library 999') && !gone.includes('undefined'),
    `an unconfigured id must fall back cleanly, got: ${gone}`);

// Library labels are user-typed in Settings.
api.setLibs([{ id: 1, label: 'A' }, { id: 2, label: 'Anime "4K" & more' }]);
assert(api.libCogHTML('2').includes('Anime &quot;4K&quot; &amp; more'),
    'a Library label is not attribute-escaped in the tooltip');
api.setLibs(TV);

// ── paintLibCogs: filling the slots on both ends of the registry race ───────
function fakeSlot(cur) {
    return { html: '', innerHTML: '', attrs: { 'data-current': cur },
             getAttribute(k) { return this.attrs[k] == null ? null : this.attrs[k]; } };
}
const slots = [fakeSlot(''), fakeSlot('5')];
let asked = null;
api.paintLibCogs({ querySelectorAll: (sel) => { asked = sel; return slots; } });
assert(asked === '[data-vwlp-lib-slot]', `painted the wrong selector: ${asked}`);
assert(slots[0].innerHTML.includes('data-vwlp-lib'), 'an empty slot was not filled');
assert(slots[1].innerHTML.includes('Anime'), 'a slot with a choice did not render it');

// The other half of "one Library is not a choice": slots must be emptied, not
// left holding a stale cog.
api.setLibs([TV[0]]);
api.paintLibCogs({ querySelectorAll: () => slots });
assert(slots[0].innerHTML === '' && slots[1].innerHTML === '',
    'slots keep a cog after the registry drops to one Library');
api.setLibs(TV);

// ── libPickerHTML: the options ──────────────────────────────────────────────
const picker = api.libPickerHTML('Brand New Anime', '');
assert(picker.includes('data-vlib-v=""'), 'there is no "default" option — the choice cannot be cleared');
assert(/Default/.test(picker), 'the default option is not labelled as the default');
assert(picker.includes('data-vlib-v="2"') && picker.includes('data-vlib-v="5"'),
    'not every configured Library is offered');
assert(picker.includes('Anime') && picker.includes('TV-Shows'), 'Library labels are missing');
assert(picker.includes('data-vlib-save') && picker.includes('data-vlib-cancel'),
    'the picker has no save/cancel controls');
assert(!/<button(?![^>]*type="button")/.test(picker), 'a button in the picker is missing type="button"');
assert(picker.includes('Brand New Anime'), 'the picker does not name the show');

// Exactly one option is marked, and with no choice recorded it is the default —
// preselecting a real Library instead would turn "I opened this" into "I chose
// the primary", which is the very mistake this control exists to prevent.
function marked(html) {
    return (html.match(/data-vlib-v="([^"]*)"[^>]*rgb\(var\(--accent-rgb\)\)/g) || [])
        .map(s => (s.match(/data-vlib-v="([^"]*)"/) || [])[1]);
}
const dflt = marked(picker), five = marked(api.libPickerHTML('X', '5'));
assert(dflt.length === 1 && dflt[0] === '',
    `with nothing chosen the DEFAULT option must be the marked one, marked: ${JSON.stringify(dflt)}`);
assert(five.length === 1 && five[0] === '5',
    `the current Library must be the only one marked, marked: ${JSON.stringify(five)}`);

// A Library's category is a subtitle under its name — but the common setup
// names the Library after the category, and 'Anime' over 'Anime' is noise.
api.setLibs([{ id: 1, label: 'TV-Shows' }, { id: 5, label: 'Anime', category: 'Anime' }]);
assert((api.libPickerHTML('X', '').match(/Anime/g) || []).length === 1,
    'a Library named after its own category prints itself twice');
api.setLibs([{ id: 1, label: 'TV-Shows' }, { id: 5, label: 'Shelf 2', category: 'Anime' }]);
assert(api.libPickerHTML('X', '').includes('Anime'),
    'a category that is NOT the label is useful and must still show');
api.setLibs(TV);

const evilPicker = api.libPickerHTML('x" onmouseover="alert(1)', '');
assert(!evilPicker.includes('onmouseover="alert(1)"'), `title injection into the picker: ${evilPicker}`);

// ── saveWatchlistLibrary: the request itself ────────────────────────────────
let sent = null;
globalThis.fetch = (url, opts) => {
    sent = { url, opts, body: JSON.parse(opts.body) };
    return Promise.resolve({ ok: true, json: () => Promise.resolve({ success: true, updated: 1 }) });
};

await api.saveWatchlistLibrary('1399', '5');
assert(sent.url === '/api/video/watchlist/library', `wrong endpoint: ${sent.url}`);
// The wishlist's equivalent picker is a PUT on a different endpoint; this route
// is registered POST-only, so copying that verb would 405 every save.
assert(sent.opts.method === 'POST', `wrong method: ${sent.opts.method}`);
assert(sent.opts.headers['Content-Type'] === 'application/json', 'body is JSON but not declared as such');
assert(sent.body.tmdb_id === 1399, `tmdb_id must be a number, got ${JSON.stringify(sent.body.tmdb_id)}`);
assert(sent.body.root_folder_id === 5, `root_folder_id must be a number, got ${JSON.stringify(sent.body.root_folder_id)}`);

// "Default" clears the choice. It has to travel as an explicit null — the
// endpoint reads the field to decide, and '' would be validated as a Library id.
await api.saveWatchlistLibrary(1399, '');
assert(sent.body.root_folder_id === null,
    `Default must send null, sent ${JSON.stringify(sent.body.root_folder_id)}`);
assert('root_folder_id' in sent.body, 'the field must be present — an absent one leaves the old value');
await api.saveWatchlistLibrary(1399, null);
assert(sent.body.root_folder_id === null, 'a null choice must send null, not NaN');

// A refusal (403 for a profile without download rights) must not resolve as
// success — the caller only checks the parsed body.
globalThis.fetch = () => Promise.resolve({ ok: false, status: 403, json: () => Promise.resolve({ success: false }) });
assert((await api.saveWatchlistLibrary(1, '5')) === null, 'a non-2xx response must not read as success');

console.log('video watchlist library-picker harness: all assertions passed');
