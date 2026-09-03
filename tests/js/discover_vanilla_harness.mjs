// Behavioural tests for webui/static/discover.js, run against the REAL bodies
// lifted out by tests/js/vanilla-extract.mjs. Exits non-zero on any failure.
//
// Why this exists: discover.js is 12k lines and its coverage was source-text
// pins — `assert "someString" in discover.js`. A pin cannot see a return value,
// and it cannot see whether a branch is reachable at all; two pins in this repo
// passed with the feature they described switched off. Everything below calls
// the function.
//
// The first subject is deliberate. `_normalizeTrack` is what the 2.3.1 Discover
// port leaned on: the ported Daily Mixes emit Spotify-shaped tracks, and the
// claim that this page could already render them shipped as three substring
// assertions. That claim is now checked by running it.
import { extractFunction, loadVanilla, VANILLA_DISCOVER } from './vanilla-extract.mjs';

let failures = 0;
function assert(cond, msg) {
    if (!cond) { console.error('FAIL: ' + msg); failures++; }
}
function eq(actual, expected, msg) {
    assert(actual === expected, `${msg}\n   expected: ${JSON.stringify(expected)}\n   actual:   ${JSON.stringify(actual)}`);
}

// ── the extractor itself ────────────────────────────────────────────────────
// It is the thing every assertion below depends on, so a silent mis-extraction
// would make the whole file meaningless. These are the shapes that break naive
// brace matching.
{
    const attr = extractFunction('_attr');
    assert(attr.trim().endsWith('}'), 'a regex literal (/"/g) must not desync the matcher');
    assert(attr.includes("&#39;"), '_attr body looks truncated');

    const btn = extractFunction('_blockArtistBtn');
    assert(btn.includes('</button>`'), 'a template substitution must not close it early');
    assert(btn.trim().endsWith('}'), '_blockArtistBtn did not brace-match to its end');

    // A DEFAULT OBJECT PARAMETER. `sourceData = {}` puts a brace inside the
    // parameter list, so matching from the first `{` after the declaration
    // returns a truncated body. Upstream's comment names this exact function as
    // the one that taught them to walk the parameter list first.
    const ctx = extractFunction('_buildDiscoverArtistContext');
    assert(ctx.trim().endsWith('}'), 'default-param brace ended the extraction early');
    assert(ctx.includes('return'), '_buildDiscoverArtistContext body looks truncated');
    assert(ctx.split('\n').length > 10, `only ${ctx.split('\n').length} lines extracted`);

    // A TEMPLATE NESTED INSIDE A SUBSTITUTION. Treating every backtick as a
    // plain toggle closes the outer template on the inner's opener.
    //
    // `_artWebShowGenre` is named on purpose. Sweeping all 358 top-level
    // functions with the `${` re-entry disabled changes the extraction of
    // exactly 11 of them, and this is one — a simple nested template happens to
    // re-balance by accident, so a gentler subject would leave the guard
    // untested. (_miniStat and _whyIcon are the accidental cases; kept below as
    // the everyday shape.)
    // The failure is an OVERRUN, not a truncation: without the guard the
    // matcher sails past the closing brace and swallows the functions after it
    // (_artWebShowGenre goes from 24 lines to 334). So the assertion has to be
    // an upper bound — "ends with }" and "is long enough" are both true of the
    // broken output, which is how a first attempt at this test passed with the
    // guard disabled.
    for (const name of ['_artWebShowGenre', '_miniStat', '_whyIcon', '_blockArtistBtn',
                        '_buildDiscoverArtistContext']) {
        const fn = extractFunction(name);
        assert(fn.trim().endsWith('}'), `${name}: did not brace-match to its end`);
        // A second top-level declaration inside the body means it overran.
        assert(!/\nfunction [A-Za-z_]/.test(fn),
            `${name}: extraction ran past its own end into the next function ` +
            `(${fn.split('\n').length} lines)`);
    }

    let threw = false;
    try { extractFunction('_definitelyNotAFunctionInThisFile'); } catch { threw = true; }
    assert(threw, 'a missing function must throw, not silently return nothing');
}

const { _normalizeTrack, _attr } = loadVanilla(['_normalizeTrack', '_attr']);

// ── _normalizeTrack: the shape the 2.3.1 port depends on ────────────────────
// core/personalized/daily_mixes.py emits exactly this. If it stopped being
// handled, mixes would render "Unknown Track / Unknown Artist" and the Python
// side would have no idea.
{
    const t = _normalizeTrack({
        name: 'Stan',
        artists: [{ name: 'Eminem' }],
        album: { name: 'The Marshall Mathers LP', images: [{ url: '/art.jpg' }] },
        duration_ms: 405000,
        owned: true,
    });
    eq(t.name, 'Stan', 'ported mix track: title');
    eq(t.artist, 'Eminem', 'ported mix track: artist comes out of artists[0].name');
    eq(t.album, 'The Marshall Mathers LP', 'ported mix track: album name');
    eq(t.cover, '/art.jpg', 'ported mix track: cover from album.images[0].url');
    eq(t.durationMs, 405000, 'ported mix track: duration passes through unscaled');
}

// The flat shape the older personalized mixes emit. Both must keep working —
// this page renders several sources through one normaliser.
{
    const t = _normalizeTrack({
        track_name: 'Whistle', artist_name: 'Flo Rida',
        album_name: 'Wild Ones', album_cover_url: '/w.jpg', duration_ms: 224000,
    });
    eq(t.name, 'Whistle', 'flat shape: title');
    eq(t.artist, 'Flo Rida', 'flat shape: artist');
    eq(t.album, 'Wild Ones', 'flat shape: album');
    eq(t.cover, '/w.jpg', 'flat shape: cover');
}

// Nested under track_data_json, which is how sync-bound rows arrive.
{
    const t = _normalizeTrack({
        track_data_json: { name: 'Nested', artists: [{ name: 'Someone' }] },
        track_name: 'WRONG', artist_name: 'WRONG',
    });
    eq(t.name, 'Nested', 'track_data_json must win over the flat fields beside it');
    eq(t.artist, 'Someone', 'track_data_json artist must win too');
}

// An artist array of bare strings rather than objects — ListenBrainz does this.
{
    const t = _normalizeTrack({ name: 'X', artists: ['Plain String Artist'] });
    eq(t.artist, 'Plain String Artist', 'artists[] may hold strings, not only {name}');
}

// ── the placeholders, which are the visible failure when a shape is missed ──
{
    const t = _normalizeTrack({});
    eq(t.name, 'Unknown Track', 'an empty row must not render "undefined"');
    eq(t.artist, 'Unknown Artist', 'an empty row must not render "undefined"');
    // Album and duration are deliberately BLANK rather than placeheld: a
    // ListenBrainz recording playlist carries neither, and "Unknown Album" /
    // "0:00" would be an assertion the data does not support.
    eq(t.album, '', 'a missing album stays blank rather than "Unknown Album"');
    eq(t.durationMs, 0, 'a missing duration is 0, which the renderer hides');
}

// ── _attr: the escaping that keeps a scraped name inside its attribute ──────
// Artist names on this page come from music-map.com. The page's own escapeHtml
// goes through textContent -> innerHTML, which does NOT escape quotes; _attr
// exists because that is wrong for an attribute value.
{
    eq(_attr(`Guns N' Roses`), 'Guns N&#39; Roses', 'apostrophe must be escaped');
    eq(_attr('say "hi"'), 'say &quot;hi&quot;', 'double quote must be escaped');
    eq(_attr('A & B'), 'A &amp; B', 'ampersand must be escaped');
    eq(_attr(null), '', 'null renders empty, not "null"');
    eq(_attr(0), '0', 'a falsy non-null value survives');
    // Ordering: escaping & last would double-escape the entities just written.
    eq(_attr('&#39;'), '&amp;#39;', 'ampersand must be escaped first');
    // The breakout that matters.
    const out = _attr('" onmouseover="alert(1)');
    assert(!out.includes('"'), `a raw quote must not survive into an attribute: ${out}`);
}

if (failures) {
    console.error(`\n${failures} assertion(s) failed`);
    process.exit(1);
}
console.log('discover.js vanilla harness: OK');
