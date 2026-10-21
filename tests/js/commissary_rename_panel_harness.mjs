// Behavioral harness for webui/static/commissary-rename-panel.js: the entry
// appears only for an owned library title and an admin.
import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { dirname, join } from 'node:path';

const here = dirname(fileURLToPath(import.meta.url));
const src = readFileSync(join(here, '..', '..', 'webui', 'static', 'commissary-rename-panel.js'), 'utf8');
globalThis.window = {};
let admin = true;
globalThis.getCurrentProfileContext = () => ({ isAdmin: admin });
(0, eval)(src);
const { applies, buttonHtml } = globalThis.window.CommissaryRename;

function assert(cond, msg) {
    if (!cond) { console.error('FAIL: ' + msg); process.exit(1); }
}

assert(applies({ source: 'library', kind: 'show', id: 2 }), 'a library show');
assert(applies({ source: 'library', kind: 'movie', id: 7 }), 'a library movie');
assert(applies({ source: 'tmdb', kind: 'show', id: 9, library_id: 2 }), 'a TMDB page of an owned show');
assert(!applies({ source: 'tmdb', kind: 'show', id: 9, library_id: null }), 'nothing to rename unowned');
assert(!applies({ source: 'youtube', kind: 'show', id: 3 }), 'YouTube channels name themselves');
assert(!applies({ source: 'library', kind: 'person', id: 1 }), 'only movies and shows');
admin = false;
assert(!applies({ source: 'library', kind: 'show', id: 2 }), 'admins only');
const html = buttonHtml();
assert(html.includes('class="vd-manage-btn"') && html.includes('data-vd-act="commissary-rename"'),
       "upstream's More-menu button, dispatched by the detail page");
console.log('all assertions passed');
