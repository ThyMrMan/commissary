// Behavioral harness for the Discover per-row "block this artist" button
// (webui/static/discover.js). Exits non-zero with a message on any failure.
//
// blockDiscoveryArtist(), its API endpoint, and its CSS (.track-compact-block,
// revealed on row hover) all shipped long ago — the markup that would let a
// user reach it never did, so the function had ZERO call sites and the only
// way to block an artist was to type their name into the blocked-artists
// modal. These pin the pieces that were missing.
//
// discover.js is ~11k lines of browser-coupled code, so rather than eval the
// whole file this extracts the three functions under test by name. They stay
// bound to the real source (a rename or deletion fails the extraction) without
// dragging in the rest of the page.
import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { dirname, join } from 'node:path';

const here = dirname(fileURLToPath(import.meta.url));
const src = readFileSync(join(here, '..', '..', 'webui', 'static', 'discover.js'), 'utf8');

function assert(cond, msg) {
    if (!cond) { console.error('FAIL: ' + msg); process.exit(1); }
}

// Pull one top-level `function name(...) { ... }` out of the source. Relies on
// the closing brace sitting at column 0, which is true for every top-level
// function in this file.
function extract(name) {
    const start = src.indexOf(`function ${name}(`);
    assert(start !== -1, `function ${name}() is gone from discover.js`);
    const end = src.indexOf('\n}', start);
    assert(end !== -1, `could not find the end of ${name}()`);
    return src.slice(start, end + 2);
}

const _attr = (0, eval)(`(${extract('_attr')})`);
// _blockArtistBtn closes over _attr, so define both in one scope.
const _blockArtistBtn = (0, eval)(
    `(function(){ ${extract('_attr')}\n${extract('_blockArtistBtn')}\n return _blockArtistBtn; })()`);

// ── _attr: attribute-safe escaping ──────────────────────────────────────────
// The page's escapeHtml/_esc go through textContent -> innerHTML, which escapes
// & < > but NOT quotes. Correct for a text node, wrong for an attribute value —
// and artist names come from a scraped third-party source, so a quote in a name
// is a matter of when, not if.
assert(_attr(`Guns N' Roses`) === 'Guns N&#39; Roses', `apostrophe not escaped: ${_attr("Guns N' Roses")}`);
assert(_attr('say "hi"') === 'say &quot;hi&quot;', 'double quote not escaped');
assert(_attr('A & B') === 'A &amp; B', 'ampersand not escaped');
assert(_attr('<b>x</b>') === '&lt;b&gt;x&lt;/b&gt;', 'angle brackets not escaped');
assert(_attr(null) === '' && _attr(undefined) === '', 'null/undefined should render empty, not "null"');
assert(_attr(0) === '0', 'a falsy non-null value must survive');

// The ordering trap: escaping & AFTER the others double-escapes.
assert(_attr(`&#39;`) === '&amp;#39;', 'ampersand must be escaped first');

// ── _blockArtistBtn: the markup that was missing ────────────────────────────
const btn = _blockArtistBtn('Metallica');
assert(btn.includes('class="track-compact-block"'), 'must use the CSS class that already exists');
assert(btn.includes('data-artist="Metallica"'), 'artist must ride in data-artist');
assert(btn.includes('blockDiscoveryArtist(this.dataset.artist)'),
    'must read the name from the dataset, never interpolate it into a JS string');
assert(btn.includes('event.stopPropagation()'),
    "without this the row's own click (open/play) fires alongside the block");
assert(/type="button"/.test(btn), 'an unstyled <button> inside a form defaults to submit');

// A quote in the name must not break out of the attribute.
const tricky = _blockArtistBtn(`Guns N' Roses`);
assert(tricky.includes(`data-artist="Guns N&#39; Roses"`), `apostrophe broke the attribute: ${tricky}`);

// The breakout cases. A raw quote in the name must never close the attribute
// and start a new one — which is what makes the dataset approach worth having.
const evil = _blockArtistBtn('x" onmouseover="alert(1)');
assert(!evil.includes('onmouseover="alert(1)"'), `attribute injection: ${evil}`);
assert(evil.includes('data-artist="x&quot; onmouseover=&quot;alert(1)"'),
    `quote not neutralised inside the attribute: ${evil}`);
// The name may legitimately APPEAR (escaped) in data-artist and title. What it
// must never do is reach the one attribute that executes: onclick is a fixed
// literal with no interpolation at all, which is the whole point of routing the
// value through the dataset.
const evil2 = _blockArtistBtn(`x'); alert(1); ('`);
const onclick = (evil2.match(/onclick="([^"]*)"/) || [])[1];
assert(onclick === 'event.stopPropagation(); blockDiscoveryArtist(this.dataset.artist)',
    `onclick is not a fixed literal: ${onclick}`);
assert(!/data-artist="[^"]*'[^"]*"/.test(evil2), `raw apostrophe survived into the attribute: ${evil2}`);

// Rows with no usable artist get no button rather than one that blocks
// the literal string "Unknown Artist" — which the pool is full of.
assert(!_blockArtistBtn('Unknown Artist').includes('track-compact-block'),
    'must not offer to block "Unknown Artist"');
assert(!_blockArtistBtn('').includes('track-compact-block'), 'empty name must not get a button');
assert(!_blockArtistBtn('   ').includes('track-compact-block'), 'whitespace name must not get a button');
// ...but it must still emit SOMETHING, or the grid column silently shifts.
assert(_blockArtistBtn('').trim().length > 0, 'the grid cell must still be occupied');

// ── _removeBlockedArtistRows: the list you are looking at ───────────────────
// The per-row button lives in the mix modal, and the section reloads that
// blockDiscoveryArtist() already did only rebuild Hidden Gems and Discovery
// Shuffle — neither of which is on screen then. Without this the row you just
// blocked sits there unchanged and the click reads as a no-op.
class FakeEl {
    constructor(tag, cls, text) {
        this.cls = cls; this.text = text; this.children = []; this.parent = null;
        this.dataset = {};
    }
    add(child) { child.parent = this; this.children.push(child); return child; }
    get textContent() { return this.text; }
    set textContent(v) { this.text = v; }
    _all(sel, out) {
        for (const c of this.children) {
            if (c.cls === sel.replace('.', '')) out.push(c);
            c._all(sel, out);
        }
        return out;
    }
    querySelectorAll(sel) { return this._all(sel, []); }
    querySelector(sel) { return this._all(sel, [])[0] || null; }
    remove() {
        if (!this.parent) return;
        this.parent.children = this.parent.children.filter(c => c !== this);
    }
}

function buildList(rows) {
    const list = new FakeEl('div', 'discover-playlist-tracks-compact');
    rows.forEach(([artist], i) => {
        const row = list.add(new FakeEl('div', 'discover-playlist-track-compact'));
        row.add(new FakeEl('div', 'track-compact-number', String(i + 1)));
        row.add(new FakeEl('div', 'track-compact-artist', artist));
    });
    return list;
}

const list = buildList([['Metallica'], ['Slayer'], ['metallica'], ['Anthrax']]);
globalThis.document = { querySelectorAll: (sel) =>
    sel === '.discover-playlist-tracks-compact' ? [list] : [] };

const _removeBlockedArtistRows = (0, eval)(`(${extract('_removeBlockedArtistRows')})`);
_removeBlockedArtistRows('Metallica');

const left = list.querySelectorAll('.discover-playlist-track-compact');
const names = left.map(r => r.querySelector('.track-compact-artist').textContent);
assert(names.length === 2, `expected 2 rows left, got ${names.length}: ${names}`);
assert(names.join('|') === 'Slayer|Anthrax', `wrong rows removed: ${names}`);

// Case-insensitive, because the server compares LOWER(artist_name) — a
// case-sensitive client would leave rows the backend has already blocked.
assert(!names.includes('metallica'), 'lowercase duplicate survived — comparison is case-sensitive');

// Numbering closes up, so the list does not show 1, 2, 4.
const nums = left.map(r => r.querySelector('.track-compact-number').textContent);
assert(nums.join(',') === '1,2', `numbering not closed up: ${nums}`);

// A name matching nothing must leave every row (and its numbering) alone.
const untouched = buildList([['A'], ['B']]);
globalThis.document = { querySelectorAll: () => [untouched] };
_removeBlockedArtistRows('Nobody');
assert(untouched.querySelectorAll('.discover-playlist-track-compact').length === 2,
    'a non-matching block removed rows');
_removeBlockedArtistRows('');
assert(untouched.querySelectorAll('.discover-playlist-track-compact').length === 2,
    'an empty name must be a no-op, not a match-all');

console.log('discover block-button harness: all assertions passed');
