// The candidate picker names the tracker and links its details page
// (webui/static/downloads.js, fork 1.9.8). Run by test_tracker_link.py.
//
//     node --test tests/commissary/tracker_link.mjs

import { test } from 'node:test';
import assert from 'node:assert/strict';
import vm from 'node:vm';
import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { dirname, resolve } from 'node:path';

const __dirname = dirname(fileURLToPath(import.meta.url));
// CRLF checkout: normalise so the brace scan is stable
const SOURCE = readFileSync(
    resolve(__dirname, '..', '..', 'webui', 'static', 'downloads.js'), 'utf8').replace(/\r\n/g, '\n');

function lift(name) {
    const start = SOURCE.indexOf(`function ${name}(`);
    assert.ok(start !== -1, `${name} not found in downloads.js`);
    let i = SOURCE.indexOf('{', start);
    let depth = 0;
    for (; i < SOURCE.length; i++) {
        if (SOURCE[i] === '{') depth++;
        else if (SOURCE[i] === '}') { depth--; if (depth === 0) { i++; break; } }
    }
    return SOURCE.slice(start, i);
}

// escapeHtml without a DOM: the same output downloads.js produces
// (textContent escaping + &quot; for attributes)
const ESCAPE = `function escapeHtml(t) {
    return String(t ?? '').replace(/&/g, '&amp;').replace(/</g, '&lt;')
        .replace(/>/g, '&gt;').replace(/"/g, '&quot;');
}`;

const sb = {};
vm.createContext(sb);
vm.runInContext([ESCAPE, lift('_candidatesFmtSize'), lift('_candidatesFmtDur'),
    lift('_ssShortFileLabel'), lift('_candidateTrackerHtml'), lift('_renderCandidateRow'),
    'this.render = _renderCandidateRow; this.tracker = _candidateTrackerHtml;'].join('\n'), sb);

const userCell = (html) => html.match(/<td class="candidates-col-user"[^>]*>([\s\S]*?)<\/td>/)[1].trim();

test('a torrent row names its tracker, linked to the details page', () => {
    const html = sb.render({ username: 'torrent', source: 'torrent', filename: 'tok||Album',
        indexer: 'RED', info_url: 'https://tracker.example/torrents.php?id=1' }, 0, 'r', false);
    const cell = userCell(html);
    assert.match(cell, /^<a class="candidates-tracker-link" href="https:\/\/tracker\.example\/torrents\.php\?id=1"/);
    assert.match(cell, /target="_blank" rel="noopener noreferrer"/);
    assert.match(cell, />RED ↗<\/a>$/);
});

test('a tracker without a details page is named, not linked', () => {
    assert.equal(sb.tracker({ indexer: 'RED' }, 'torrent'), 'RED');
});

test('a non-http link is never rendered as an href', () => {
    assert.equal(sb.tracker({ indexer: 'RED', info_url: 'javascript:alert(1)' }, 'torrent'), 'RED');
});

test('an injection attempt in name or url stays text', () => {
    const out = sb.tracker({ indexer: '"><img src=x onerror=alert(1)>',
        info_url: 'https://t.example/"><script>alert(1)</script>' }, 'torrent');
    assert.ok(!out.includes('<img'));
    assert.ok(!out.includes('<script'));
    assert.match(out, /href="https:\/\/t\.example\/&quot;&gt;&lt;script&gt;/);
});

test('a row with no tracker falls back to the username as before', () => {
    const html = sb.render({ username: 'youtube', source: 'youtube', filename: 'id||Song' }, 0, 'r', false);
    assert.equal(userCell(html), 'youtube');
});

test('a soulseek peer is still a chat link, not a tracker', () => {
    const html = sb.render({ username: 'peer', source: 'soulseek', filename: 'a/b.flac' }, 0, 'r', false);
    assert.match(userCell(html), /class="chat-user-link"/);
});
