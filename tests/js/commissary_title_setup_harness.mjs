// Behavioral harness for webui/static/commissary-title-setup.js.
// The entry must appear only where upstream's own Manage can't: a TMDB page
// with no library row, for an admin.
import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { dirname, join } from 'node:path';

const here = dirname(fileURLToPath(import.meta.url));
const src = readFileSync(join(here, '..', '..', 'webui', 'static', 'commissary-title-setup.js'), 'utf8');
globalThis.window = {};
let admin = true;
globalThis.getCurrentProfileContext = () => ({ isAdmin: admin });
(0, eval)(src);
const { applies, buttonHtml } = globalThis.window.CommissaryTitleSetup;

function assert(cond, msg) {
    if (!cond) { console.error('FAIL: ' + msg); process.exit(1); }
}

const unowned = { source: 'tmdb', kind: 'show', tmdb_id: 250008, library_id: null };
assert(applies(unowned), 'an unowned TMDB show gets the entry');
assert(applies({ ...unowned, kind: 'movie' }), 'so does an unowned movie');
assert(!applies({ ...unowned, library_id: 12 }), 'an owned title uses upstream Manage');
assert(!applies({ ...unowned, source: 'library', id: 12 }), 'a library page uses upstream Manage');
assert(!applies({ ...unowned, kind: 'person' }), 'only movies and shows');
assert(!applies({ ...unowned, tmdb_id: null }), 'needs the TMDB id it is keyed by');
assert(!applies(null), 'no detail, no entry');
admin = false;
assert(!applies(unowned), 'a non-admin profile does not see it (the PUT is admin-only)');

const html = buttonHtml();
assert(html.includes('class="vd-manage-btn"'), "upstream's More-menu button style");
assert(html.includes('data-vd-act="commissary-title-setup"'), 'dispatched by the detail page');
assert(!html.includes('vwl-btn'), 'never the watchlist toggle class');

console.log('all assertions passed');
