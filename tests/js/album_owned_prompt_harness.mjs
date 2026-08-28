// Behavioural harness for the album picker's "you already own this" prompt
// (webui/static/downloads.js). Exits non-zero with a message on any failure.
//
// Source-text assertions cannot see reachability: `if (false && ownership...)`
// still contains every substring a text pin looks for, and a back-out proved
// exactly that. What the prompt RESOLVES decides whether the user's files get
// overwritten, so it is worth running rather than reading.
//
// downloads.js is browser-coupled throughout, so rather than eval the whole
// file this extracts the two functions under test by name — they stay bound to
// the real source (a rename or deletion fails the extraction) without dragging
// in the rest of the page.
import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { dirname, join } from 'node:path';

const here = dirname(fileURLToPath(import.meta.url));
const src = readFileSync(join(here, '..', '..', 'webui', 'static', 'downloads.js'), 'utf8');

function assert(cond, msg) {
    if (!cond) { console.error('FAIL: ' + msg); process.exit(1); }
}

// Pull one top-level `function name(...) { ... }` out of the source. Relies on
// the closing brace sitting at column 0, which is true for every top-level
// function in this file.
function extract(name) {
    const start = src.indexOf(`function ${name}(`);
    assert(start !== -1, `function ${name}() is gone from downloads.js`);
    const end = src.indexOf('\n}', start);
    assert(end !== -1, `could not find the end of ${name}()`);
    return src.slice(start, end + 2);
}

// ── the smallest DOM the prompt actually touches ────────────────────────────
// Deliberately dumb: querySelector answers only for ids the markup really
// emitted, so the "no fill button when the album is complete" case exercises
// the source's own `if (fillBtn)` guard instead of being asserted about.
function makeEl() {
    const el = {
        className: '', innerHTML: '', onclick: null, removed: false, removeCount: 0,
        _listeners: {},
        classList: { _c: new Set(), add(c) { this._c.add(c); }, has(c) { return this._c.has(c); } },
        remove() { this.removed = true; this.removeCount += 1; },
        querySelector(sel) {
            const id = sel.replace('#', '');
            if (!el.innerHTML.includes(`id="${id}"`)) return null;
            const stub = {
                id,
                addEventListener(type, fn) { el._listeners[id] = fn; },
            };
            return stub;
        },
    };
    return el;
}

let lastOverlay = null;
globalThis.document = {
    createElement() { lastOverlay = makeEl(); return lastOverlay; },
    body: { appendChild() {} },
};
globalThis.requestAnimationFrame = (fn) => fn();

const scope = `
    function escapeHtml(s) {
        return String(s == null ? '' : s)
            .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
    }
    ${extract('_ownedAlbumSummary')}
    ${extract('_confirmOwnedAlbumPick')}
    return { _ownedAlbumSummary, _confirmOwnedAlbumPick };
`;
const { _ownedAlbumSummary, _confirmOwnedAlbumPick } = (0, eval)(`(function(){ ${scope} })()`);

// ── _ownedAlbumSummary: what the user is told ───────────────────────────────
assert(_ownedAlbumSummary(null) === '', 'no ownership record must render nothing');
assert(_ownedAlbumSummary({ owned: 0, expected: 12 }) === '',
    'owning nothing must render nothing — the prompt exists only for owned albums');

const partial = _ownedAlbumSummary({ owned: 12, expected: 18, formats: ['MP3-320'] });
assert(partial.includes('12 of 18'), `partial ownership must state both counts: ${partial}`);
assert(partial.includes('MP3-320'),
    'the format they HAVE is the reason they are replacing it — it must be shown');

const complete = _ownedAlbumSummary({ owned: 12, expected: 12, formats: [] });
assert(complete.includes('all 12 tracks'), `complete ownership must say so: ${complete}`);
assert(!complete.includes('of 12'), 'a complete album must not be phrased as a fraction');

const one = _ownedAlbumSummary({ owned: 1, expected: 1, formats: [] });
assert(one.includes('all 1 track') && !one.includes('1 tracks'), `singular: ${one}`);

// A format string is third-party-ish text on its way into innerHTML.
const nasty = _ownedAlbumSummary({ owned: 1, expected: 1, formats: ['<img src=x>'] });
assert(!nasty.includes('<img'), `formats must be escaped: ${nasty}`);

// Unknown expected count is not "partially owned" — it must not render "3 of 0".
const noExpected = _ownedAlbumSummary({ owned: 3, expected: 0, formats: [] });
assert(!noExpected.includes('of 0'), `unknown total must not render as a fraction: ${noExpected}`);

// ── _confirmOwnedAlbumPick: what the answer resolves to ─────────────────────
async function ask(ownership, press) {
    const p = _confirmOwnedAlbumPick(ownership, 'Some.Release-GRP');
    const overlay = lastOverlay;
    await Promise.resolve();
    press(overlay);
    return { answer: await p, overlay };
}

const PARTIAL = { owned: 12, expected: 18, formats: ['MP3-320'] };
const COMPLETE = { owned: 12, expected: 12, formats: ['MP3-320'] };

// Cancel is a real answer. Resolving anything else here turns "no" into a
// download, and resolving nothing at all hangs the picker forever.
let r = await ask(PARTIAL, (o) => o._listeners['album-owned-cancel']());
assert(r.answer === null, `cancel must resolve null, got ${JSON.stringify(r.answer)}`);
assert(r.overlay.removed, 'cancel must tear the dialog down');

// Clicking the backdrop is a cancel, not a silent yes.
r = await ask(PARTIAL, (o) => o.onclick({ target: o }));
assert(r.answer === null, `backdrop click must resolve null, got ${JSON.stringify(r.answer)}`);

// ...but a click INSIDE the dialog must not dismiss it.
{
    const p = _confirmOwnedAlbumPick(PARTIAL, 'X');
    const o = lastOverlay;
    await Promise.resolve();
    o.onclick({ target: { not: 'the overlay' } });
    assert(!o.removed, 'a click inside the dialog must not cancel it');
    o._listeners['album-owned-cancel']();
    await p;
}

r = await ask(PARTIAL, (o) => o._listeners['album-owned-replace']());
assert(r.answer === 'replace', `replace must resolve 'replace', got ${JSON.stringify(r.answer)}`);

r = await ask(PARTIAL, (o) => o._listeners['album-owned-fill']());
assert(r.answer === 'fill', `fill must resolve 'fill', got ${JSON.stringify(r.answer)}`);

// The trap this guards: on a fully-owned album there is nothing to fill, so the
// option must not be offered. If it were, it would resolve 'fill', force would
// stay off, and the pick would be the very no-op the prompt exists to prevent.
{
    const p = _confirmOwnedAlbumPick(COMPLETE, 'X');
    const o = lastOverlay;
    await Promise.resolve();
    assert(!o.innerHTML.includes('id="album-owned-fill"'),
        'a fully-owned album must not offer "get the missing tracks"');
    assert(o.innerHTML.includes('id="album-owned-replace"'), 'replace must still be offered');
    assert(o.innerHTML.includes('id="album-owned-cancel"'), 'cancel must always be offered');
    o._listeners['album-owned-cancel']();
    assert(await p === null, 'complete-album cancel must still resolve null');
}

// Partial ownership offers all three, and names how many are missing.
{
    const p = _confirmOwnedAlbumPick(PARTIAL, 'X');
    const o = lastOverlay;
    await Promise.resolve();
    assert(o.innerHTML.includes('id="album-owned-fill"'), 'partial ownership must offer fill');
    assert(/6 missing tracks/.test(o.innerHTML),
        `must name the 18-12=6 missing tracks: ${o.innerHTML.replace(/\s+/g, ' ').slice(0, 400)}`);
    o._listeners['album-owned-cancel']();
    await p;
}

// The release title reaches the dialog — picking between two rips of the same
// album is the whole point, so the user has to see which one they clicked.
{
    const p = _confirmOwnedAlbumPick(COMPLETE, 'Flo.Rida-Wild.Ones-2012-FLAC');
    const o = lastOverlay;
    await Promise.resolve();
    assert(o.innerHTML.includes('Flo.Rida-Wild.Ones-2012-FLAC'), 'the chosen release must be named');
    o._listeners['album-owned-cancel']();
    await p;
}

// A release title is attacker-adjacent text (it comes from an indexer) landing
// in innerHTML.
{
    const p = _confirmOwnedAlbumPick(COMPLETE, '<img src=x onerror=alert(1)>');
    const o = lastOverlay;
    await Promise.resolve();
    assert(!o.innerHTML.includes('<img src=x'), `release title must be escaped: ${o.innerHTML}`);
    o._listeners['album-owned-cancel']();
    await p;
}

// A second answer must be inert. The promise settles once on its own, so the
// observable half is the teardown: without the `settled` latch the second press
// tears down a dialog that is already gone. Assert the count, not the resolved
// value — a back-out proved that asserting the value tests only Promise itself.
{
    const p = _confirmOwnedAlbumPick(PARTIAL, 'X');
    const o = lastOverlay;
    await Promise.resolve();
    o._listeners['album-owned-replace']();
    o._listeners['album-owned-cancel']();
    assert(await p === 'replace', 'the first answer must win');
    assert(o.removeCount === 1,
        `the dialog must be torn down exactly once, got ${o.removeCount}`);
}

console.log('album owned-prompt harness: OK');
