/*
 * Commissary — a show's episode numbering, in its Manage panel.
 *
 * An "Episode numbering" field under Series type (Auto / TMDB / TVDB), with
 * the provider actually in force named once beneath it, plus two tools:
 *   Re-scan episode list            re-read the full list from that provider
 *   Check for out-of-place episodes rows listed under a season the provider
 *                                   doesn't give them (preview, then remove)
 *
 * Added to upstream's Manage panel after it renders, from upstream's own
 * classes (vmg-field / vmg-input / vmg-hint / vmg-btn-ghost). Admin-only, like
 * the endpoints under /api/video/libraries/.
 */
(function () {
    'use strict';
    var API = '/api/video/libraries/show/';
    var OPTIONS = [['auto', 'Auto (match your server’s seasons)'], ['tmdb', 'TMDB'], ['tvdb', 'TVDB']];
    var NAMES = { tmdb: 'TMDB', tvdb: 'TVDB' };
    var current = null;     // {kind, id} of the open Manage panel

    function toast(msg, type) { if (typeof showToast === 'function') showToast(msg, type); }
    function confirmDlg(opts) {
        if (typeof showConfirmDialog === 'function') return showConfirmDialog(opts);
        return Promise.resolve(true);
    }
    function isAdmin() {
        try {
            if (typeof getCurrentProfileContext !== 'function') return true;
            var ctx = getCurrentProfileContext();
            return !ctx || !!ctx.isAdmin;
        } catch (e) { return true; }
    }
    function el(tag, cls, text) {
        var n = document.createElement(tag);
        if (cls) n.className = cls;
        if (text != null) n.textContent = text;
        return n;
    }
    function seasons(list) {
        list = list || [];
        if (!list.length) return '';
        // 3, 4, 5, 9 -> "3–5, 9"
        var out = [], start = list[0], prev = list[0];
        for (var i = 1; i <= list.length; i++) {
            var n = list[i];
            if (n === prev + 1) { prev = n; continue; }
            out.push(start === prev ? String(start) : start + '–' + prev);
            start = prev = n;
        }
        return out.join(', ');
    }

    // What the provider note says: which one is in force, and why.
    function note(d) {
        var name = NAMES[d.source] || 'TMDB';
        if (d.override) return 'Using ' + name + ' (set here).';
        var why = d.source === 'tvdb'
            ? 'your server’s seasons match TVDB' +
              (d.missing_from_tmdb && d.missing_from_tmdb.length
                  ? '; TMDB has no season ' + seasons(d.missing_from_tmdb) : '')
            : (!d.tvdb_configured ? 'no TVDB key is set'
               : !d.has_tvdb_id ? 'this show has no TVDB match'
               : 'it matches your server’s seasons');
        return 'Using ' + name + ' — ' + why + '.';
    }

    function build(id) {
        var wrap = el('div', 'vmg-field');
        wrap.setAttribute('data-commissary-numbering', '');
        wrap.appendChild(el('label', null, 'Episode numbering'));
        var sel = el('select', 'vmg-input');
        sel.setAttribute('data-commissary-numbering-select', '');
        OPTIONS.forEach(function (o) {
            var opt = el('option', null, o[1]);
            opt.value = o[0];
            sel.appendChild(opt);
        });
        sel.disabled = true;
        wrap.appendChild(sel);
        var hint = el('div', 'vmg-hint', 'Checking which database lists this show’s episodes…');
        hint.setAttribute('data-commissary-numbering-note', '');
        hint.style.paddingBottom = '0';   // .vmg-hint ends a field; this one sits mid-field
        wrap.appendChild(hint);
        var row = el('div');
        row.style.cssText = 'display:flex;flex-wrap:wrap;gap:8px;';
        var rescan = el('button', 'vmg-btn-ghost', 'Re-scan episode list');
        rescan.type = 'button';
        var check = el('button', 'vmg-btn-ghost', 'Check for out-of-place episodes');
        check.type = 'button';
        row.appendChild(rescan);
        row.appendChild(check);
        wrap.appendChild(row);
        var result = el('div', 'vmg-hint');
        result.setAttribute('data-commissary-numbering-result', '');
        result.hidden = true;
        // shown once there is something to say
        new MutationObserver(function () { result.hidden = !result.textContent; })
            .observe(result, { childList: true, characterData: true, subtree: true });
        wrap.appendChild(result);

        var asked = 0;      // only the newest answer may write the note
        function refreshNote() {
            var mine = ++asked;
            // unique URL: upstream's fetch-dedupe.js would answer a re-read made
            // within 2.5s of the last one (e.g. right after a save) with the old answer
            fetch(API + id + '/numbering?t=' + Date.now(), { headers: { 'Accept': 'application/json' } })
                .then(function (r) { return r.json(); })
                .then(function (d) {
                    if (mine !== asked) return;
                    if (!d || !d.success) {
                        hint.textContent = (d && d.error) || 'Couldn’t check the providers.';
                        sel.disabled = false;
                        return;
                    }
                    sel.value = d.setting || 'auto';
                    sel.disabled = false;
                    hint.textContent = note(d);
                })
                .catch(function () {
                    if (mine !== asked) return;
                    hint.textContent = 'Couldn’t check the providers.';
                    sel.disabled = false;
                });
        }

        sel.addEventListener('change', function () {
            fetch(API + id + '/numbering', {
                method: 'PUT', headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ episode_source: sel.value }) })
                .then(function (r) {
                    if (!r.ok) throw new Error();
                    toast('Episode numbering saved — re-scan to apply it', 'success');
                    refreshNote();
                })
                .catch(function () { toast('Couldn’t save the episode numbering', 'error'); });
        });

        function busy(btn, on, label) {
            btn.disabled = on;
            btn.textContent = label;
        }

        rescan.addEventListener('click', function () {
            busy(rescan, true, 'Re-scanning…');
            fetch(API + id + '/rescan-episodes', { method: 'POST' })
                .then(function (r) { return r.json(); })
                .then(function (d) {
                    result.textContent = d && d.success
                        ? (d.added ? 'Added ' + d.added + ' episode' + (d.added === 1 ? '' : 's') +
                           ' (' + d.total + ' listed).' : 'Up to date — ' + d.total + ' episodes listed.')
                        : ((d && d.error) || 'Couldn’t re-scan.');
                })
                .catch(function () { result.textContent = 'Couldn’t re-scan.'; })
                .then(function () { busy(rescan, false, 'Re-scan episode list'); });
        });

        check.addEventListener('click', function () {
            busy(check, true, 'Checking…');
            fetch(API + id + '/out-of-place?t=' + Date.now(), { headers: { 'Accept': 'application/json' } })
                .then(function (r) { return r.json(); })
                .then(function (d) {
                    if (!d || !d.success) { result.textContent = (d && d.error) || 'Couldn’t check.'; return; }
                    var name = NAMES[d.source] || 'TMDB';
                    var eps = d.episodes || [];
                    if (!eps.length) {
                        result.textContent = 'Checked against ' + name + ': no out-of-place episodes.';
                        return;
                    }
                    var by = {};
                    eps.forEach(function (e) { (by[e.season_number] = by[e.season_number] || []).push(e.episode_number); });
                    result.textContent = 'Checked against ' + name + ': ' + eps.length +
                        ' episode' + (eps.length === 1 ? '' : 's') + ' listed where ' + name +
                        ' has none (' + Object.keys(by).map(function (s) {
                            return 'S' + s + ': ' + seasons(by[s].sort(function (a, b) { return a - b; }));
                        }).join('; ') + '). None are on your server or on disk. ';
                    var rm = el('button', 'vmg-btn-ghost', 'Remove them');
                    rm.type = 'button';
                    rm.addEventListener('click', function () {
                        confirmDlg({ title: 'Remove out-of-place episodes?',
                                     message: 'Removes ' + eps.length + ' listed-but-missing episode' +
                                         (eps.length === 1 ? '' : 's') + ' from the library. Nothing on ' +
                                         'disk or on your server is touched.',
                                     confirmText: 'Remove', cancelText: 'Keep', destructive: true })
                            .then(function (yes) {
                                if (!yes) return;
                                fetch(API + id + '/out-of-place', { method: 'POST' })
                                    .then(function (r) { return r.json(); })
                                    .then(function (x) {
                                        result.textContent = x && x.success
                                            ? 'Removed ' + x.removed + ' out-of-place episode' + (x.removed === 1 ? '' : 's') + '.'
                                            : ((x && x.error) || 'Couldn’t remove them.');
                                    })
                                    .catch(function () { result.textContent = 'Couldn’t remove them.'; });
                            });
                    });
                    result.appendChild(rm);
                })
                .catch(function () { result.textContent = 'Couldn’t check.'; })
                .then(function () { busy(check, false, 'Check for out-of-place episodes'); });
        });

        refreshNote();
        return wrap;
    }

    function decorate() {
        if (!current || current.kind !== 'show' || !isAdmin()) return;
        var st = document.querySelector('.vmg-overlay [data-vmg-series-type]');
        if (!st || document.querySelector('[data-commissary-numbering]')) return;
        var field = st.closest('.vmg-field');
        if (field) field.parentNode.insertBefore(build(current.id), field.nextSibling);
    }

    function wrapManage() {
        if (!window.VideoManage || window.VideoManage._commissaryNumbering) return false;
        var open = window.VideoManage.open;
        window.VideoManage.open = function (opts) {
            current = opts ? { kind: opts.kind, id: opts.id } : null;
            return open.apply(this, arguments);
        };
        window.VideoManage._commissaryNumbering = true;
        return true;
    }

    // The panel is appended to <body> when it opens; a timer, not rAF (a
    // hidden tab pauses rAF).
    function start() {
        if (!wrapManage() && !(window.VideoManage && window.VideoManage._commissaryNumbering)) {
            setTimeout(start, 1000);
            return;
        }
        new MutationObserver(function () { setTimeout(decorate, 0); })
            .observe(document.body, { childList: true, subtree: false });
    }

    window.CommissaryEpisodeNumbering = { note: note, seasons: seasons };
    if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', start);
    else start();
})();
