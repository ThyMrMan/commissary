// A failed row in the download window gets a Sources button
// (webui/static/commissary-track-inspector.js, fork 1.9.8). Run by
// test_sources_button.py.
//
//     node --test tests/commissary/sources_button.mjs

import { test } from 'node:test';
import assert from 'node:assert/strict';
import vm from 'node:vm';
import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { dirname, resolve } from 'node:path';

const __dirname = dirname(fileURLToPath(import.meta.url));
const SOURCE = readFileSync(
    resolve(__dirname, '..', '..', 'webui', 'static', 'commissary-track-inspector.js'), 'utf8');

function load(withModal = true) {
    const opened = [];
    const window = {};
    if (withModal) window.showCandidatesModal = (id) => opened.push(id);
    const context = vm.createContext({ window, escapeForInlineJs: (s) => String(s).replace(/'/g, "\\'") });
    vm.runInContext(SOURCE, context);
    return { button: window.commissarySourcesButton, window, opened };
}

test('failed, not found and cancelled rows get a Sources button', () => {
    const { button } = load();
    for (const status of ['failed', 'not_found', 'cancelled']) {
        assert.match(button({ task_id: 't1', status }, false), /class="approve-quarantine-inline-btn[^"]*"[^>]*>Sources</);
    }
});

test('other rows, quarantined rows and a missing modal keep upstream\'s dash', () => {
    const { button } = load();
    for (const status of ['completed', 'downloading', 'post_processing', 'searching']) {
        assert.equal(button({ task_id: 't1', status }, false), '');
    }
    assert.equal(button({ task_id: 't1', status: 'failed' }, true), '');
    assert.equal(button({ status: 'failed' }, false), '');
    assert.equal(load(false).button({ task_id: 't1', status: 'failed' }, false), '');
});

test('the button opens upstream\'s search modal for that task', () => {
    const { button, window, opened } = load();
    const html = button({ task_id: "t'1", status: 'not_found' }, false);
    const onclick = html.match(/onclick="([^"]*)"/)[1];
    const event = { stopPropagation() {} };
    vm.runInContext(onclick, vm.createContext({ window, event }));
    assert.deepEqual(opened, ["t'1"]);
});
