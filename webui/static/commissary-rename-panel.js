/*
 * Commissary — Rename Files for one show or movie.
 *
 *   CommissaryRename.applies(detail)   an owned library title, admin profile
 *   CommissaryRename.buttonHtml()      the detail page's More-menu entry
 *   CommissaryRename.open(detail)      the slide-over
 *
 * The naming template (the saved one, or one typed here to try - never saved),
 * every variable with the value it takes for THIS title, and each file's
 * current and proposed name. Nothing moves until "Rename" is pressed, and the
 * server rebuilds the plan with the same template rather than trusting the
 * browser's list. Built from upstream's Manage panel classes (vmg-*).
 */
(function () {
    'use strict';
    var API = '/api/video/libraries/rename/';
    var state = null;   // {kind, id, overlay, template, entries, timer, asked}

    function toast(msg, type) { if (typeof showToast === 'function') showToast(msg, type); }
    function confirmDlg(opts) {
        if (typeof showConfirmDialog === 'function') return showConfirmDialog(opts);
        return Promise.resolve(true);
    }
    function esc(s) {
        return String(s == null ? '' : s).replace(/&/g, '&amp;').replace(/</g, '&lt;')
            .replace(/>/g, '&gt;').replace(/"/g, '&quot;');
    }
    function base(p) { return String(p || '').replace(/\\/g, '/').split('/').pop(); }
    function isAdmin() {
        try {
            if (typeof getCurrentProfileContext !== 'function') return true;
            var ctx = getCurrentProfileContext();
            return !ctx || !!ctx.isAdmin;
        } catch (e) { return true; }
    }
    function libId(d) {
        if (!d) return null;
        if (d.source === 'tmdb') return d.library_id != null ? d.library_id : null;
        return d.source === 'youtube' ? null : d.id;
    }

    function applies(d) {
        return !!d && (d.kind === 'movie' || d.kind === 'show') && libId(d) != null && isAdmin();
    }

    function buttonHtml() {
        return '<button class="vd-manage-btn" type="button" data-vd-act="commissary-rename" ' +
            'title="Rename this title’s files to a naming template">' +
            '<span class="vd-manage-ic">✎</span> Rename Files</button>';
    }

    function panelHtml(title) {
        return '<div class="vmg-panel" role="dialog" aria-modal="true" aria-label="Rename files">' +
            '<div class="vmg-head">' +
                '<div class="vmg-kick"><span class="vmg-kick-dot"></span>Rename Files</div>' +
                '<div class="vmg-title">' + esc(title) + '</div>' +
                '<div class="vmg-sub">Nothing moves until you press Rename. Sidecars move with their file.</div>' +
                '<button class="vmg-save" type="button" data-crn-apply disabled>Rename</button>' +
                '<button class="vmg-close" type="button" data-crn-close aria-label="Close">×</button>' +
            '</div>' +
            '<div class="vmg-body">' +
                '<div class="vmg-sect">Template</div>' +
                '<div class="vmg-field">' +
                    '<input class="vmg-input" data-crn-template spellcheck="false">' +
                    '<div class="vmg-hint" data-crn-template-hint style="padding-bottom:0">Typed here only ' +
                    'previews and renames this title; the saved template is in Settings → Library.</div>' +
                    '<button class="vmg-btn-ghost" type="button" data-crn-vars style="align-self:flex-start">' +
                    'Show variables</button>' +
                    '<div class="commissary-token-list" data-crn-tokens hidden></div>' +
                '</div>' +
                '<div class="vmg-sect">Files</div>' +
                '<div class="vmg-field" data-crn-files><div class="vmg-hint">Checking…</div></div>' +
            '</div>' +
        '</div>';
    }

    function renderTokens(tokens) {
        var host = state.overlay.querySelector('[data-crn-tokens]');
        host.innerHTML = (tokens || []).map(function (t) {
            var tip = t.description + (t.example ? ' — here: ' + t.example : '');
            return '<button type="button" class="commissary-token" title="' + esc(tip) + '"' +
                (t.import_only ? ' style="opacity:.55"' : '') + '>' + esc(t.token) + '</button>';
        }).join('');
    }

    function renderFiles(d) {
        var host = state.overlay.querySelector('[data-crn-files]');
        var entries = d.entries || [];
        state.entries = entries;
        var warn = (d.import_only && d.import_only.length)
            ? '<div class="vmg-hint" style="padding-bottom:6px">This template uses ' +
              esc(d.import_only.join(', ')) + ', which only an import can fill: renaming would ' +
              'leave ' + (d.import_only.length === 1 ? 'it' : 'them') + ' out.</div>' : '';
        if (!entries.length) {
            host.innerHTML = warn + '<div class="vmg-hint">Every file already matches this template.' +
                (d.unresolved ? ' ' + d.unresolved + ' file(s) could not be found on disk.' : '') + '</div>';
        } else {
            host.innerHTML = warn + entries.map(function (e, i) {
                return '<label class="vmg-src vmg-src--on" style="display:flex;align-items:flex-start;' +
                    'gap:9px;font-weight:500;line-height:1.45">' +
                    '<input type="checkbox" data-crn-key="' + esc(e.key) + '" checked style="margin-top:3px">' +
                    '<span style="min-width:0;word-break:break-word">' +
                    '<span style="opacity:.6">' + esc(base(e.current)) + '</span><br>→ ' +
                    esc(base(e.proposed)) + '</span></label>';
            }).join('') +
            (d.unresolved ? '<div class="vmg-hint">' + d.unresolved +
                ' file(s) could not be found on disk and are left alone.</div>' : '');
        }
        refreshButton();
    }

    function picked() {
        var keys = [];
        state.overlay.querySelectorAll('[data-crn-key]').forEach(function (cb) {
            if (cb.checked) keys.push(cb.getAttribute('data-crn-key'));
        });
        return keys;
    }

    function refreshButton() {
        var n = picked().length;
        var btn = state.overlay.querySelector('[data-crn-apply]');
        btn.disabled = !n;
        btn.textContent = n ? 'Rename ' + n : 'Rename';
    }

    function typed() {
        var v = state.overlay.querySelector('[data-crn-template]').value.trim();
        return v && v !== state.template ? v : '';
    }

    function load() {
        var s = state;
        var mine = ++s.asked;
        var t = typed();
        // a unique URL: upstream's fetch-dedupe.js would replay a re-check within 2.5s
        fetch(API + s.kind + '/' + s.id + '?t=' + Date.now() + (t ? '&template=' + encodeURIComponent(t) : ''),
              { headers: { 'Accept': 'application/json' } })
            .then(function (r) { return r.ok ? r.json() : null; })
            .then(function (d) {
                if (state !== s || mine !== s.asked) return;
                if (!d) { s.overlay.querySelector('[data-crn-files]').innerHTML =
                    '<div class="vmg-hint">Couldn’t check the files.</div>'; return; }
                var input = s.overlay.querySelector('[data-crn-template]');
                if (s.template == null) {
                    s.template = d.template || '';
                    input.value = s.template;
                }
                renderTokens(d.tokens);
                renderFiles(d);
            })
            .catch(function () { /* the list keeps its last state */ });
    }

    function apply() {
        var s = state;
        var keys = picked();
        if (!keys.length) return;
        confirmDlg({ title: 'Rename ' + keys.length + ' file' + (keys.length === 1 ? '' : 's') + '?',
                     message: 'Files and their sidecars are moved to the names shown. Your media ' +
                              'server picks the new names up on its next scan.',
                     confirmText: 'Rename', cancelText: 'Cancel' })
            .then(function (yes) {
                if (!yes || state !== s) return;
                var btn = s.overlay.querySelector('[data-crn-apply]');
                btn.disabled = true;
                btn.textContent = 'Renaming…';
                fetch(API + s.kind + '/' + s.id, {
                    method: 'POST', headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ keys: keys, template: typed() || null }) })
                    .then(function (r) { return r.json(); })
                    .then(function (d) {
                        if (d && d.error) { toast(d.error, 'error'); return; }
                        var failed = (d && d.failures) || [];
                        toast('Renamed ' + ((d && d.renamed) || 0) + ' file(s)' +
                              (failed.length ? ', ' + failed.length + ' skipped: ' + failed[0].reason : ''),
                              failed.length ? 'warning' : 'success');
                    })
                    .catch(function () { toast('Couldn’t rename the files', 'error'); })
                    .then(function () { if (state === s) load(); });
            });
    }

    function close() {
        if (!state) return;
        var ov = state.overlay;
        clearTimeout(state.timer);
        state = null;
        document.removeEventListener('keydown', onKey, true);
        ov.classList.remove('vmg-open');
        setTimeout(function () { ov.remove(); }, 230);
    }
    function onKey(e) {
        if (e.key === 'Escape' && state) { e.stopPropagation(); close(); }
    }

    function open(d) {
        if (!applies(d)) return;
        if (state) close();
        if (window.VideoManage && window.VideoManage.ensureStyles) window.VideoManage.ensureStyles();
        var ov = document.createElement('div');
        ov.className = 'vmg-overlay';
        ov.setAttribute('data-commissary-rename', '');
        ov.innerHTML = panelHtml(d.title || '');
        document.body.appendChild(ov);
        state = { kind: d.kind, id: libId(d), overlay: ov, template: null, entries: [],
                  timer: null, asked: 0 };
        ov.addEventListener('click', function (e) {
            if (e.target === ov || e.target.closest('[data-crn-close]')) { close(); return; }
            if (e.target.closest('[data-crn-apply]')) { apply(); return; }
            var vars = e.target.closest('[data-crn-vars]');
            if (vars) {
                var list = ov.querySelector('[data-crn-tokens]');
                list.hidden = !list.hidden;
                vars.textContent = list.hidden ? 'Show variables' : 'Hide variables';
                return;
            }
            var chip = e.target.closest('.commissary-token');
            if (chip) {
                var input = ov.querySelector('[data-crn-template]');
                var s = input.selectionStart == null ? input.value.length : input.selectionStart;
                var t = input.selectionEnd == null ? s : input.selectionEnd;
                input.value = input.value.slice(0, s) + chip.textContent + input.value.slice(t);
                input.focus();
                input.selectionStart = input.selectionEnd = s + chip.textContent.length;
                input.dispatchEvent(new Event('input', { bubbles: true }));
            }
        });
        ov.addEventListener('change', function (e) {
            if (e.target.closest('[data-crn-key]')) refreshButton();
        });
        ov.querySelector('[data-crn-template]').addEventListener('input', function () {
            if (!state) return;
            clearTimeout(state.timer);
            state.timer = setTimeout(load, 400);
        });
        document.addEventListener('keydown', onKey, true);
        setTimeout(function () { ov.classList.add('vmg-open'); }, 20);   // not rAF: hidden tabs pause it
        load();
    }

    window.CommissaryRename = { applies: applies, buttonHtml: buttonHtml, open: open };
})();
