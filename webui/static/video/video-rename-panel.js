/*
 * SoulSync — Rename Files panel (per-title, from a show/movie detail page).
 *
 *   VideoRename.open({kind, id, title})   — from "Rename Files".
 *
 * A slide-over holding three things: the naming template (seeded from your
 * saved one), the $variables you can use with the value each takes for THIS
 * title, and a live preview of current → proposed for every file. Nothing
 * touches disk until Apply.
 *
 * The template typed here is a ONE-OFF: it is sent with both the preview and
 * the apply, and never written back to Settings. The server recomputes the plan
 * on apply from that same template + scope rather than trusting the proposed
 * paths the browser is holding, so what you approved is what happens.
 *
 * Self-contained (own styles), mirrors the manage-panel / poster-modal pattern.
 */
(function () {
    'use strict';

    var API = '/api/video/organization/rename';

    function esc(s) {
        return String(s == null ? '' : s)
            .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;');
    }
    function toast(msg, type) { if (typeof showToast === 'function') showToast(msg, type); }
    function confirmDlg(opts) {
        if (typeof showConfirmDialog === 'function') return showConfirmDialog(opts);
        return Promise.resolve(true);   // headless fallback (never window.confirm)
    }
    function basename(p) {
        var s = String(p || '').replace(/\\/g, '/');
        return s.slice(s.lastIndexOf('/') + 1);
    }
    function jget(url) {
        return fetch(url, { headers: { Accept: 'application/json' } })
            .then(function (r) { return r.json().catch(function () { return { success: false, error: 'HTTP ' + r.status }; }); });
    }
    function jpost(url, body) {
        return fetch(url, { method: 'POST', headers: { 'Content-Type': 'application/json' },
                            body: JSON.stringify(body || {}) })
            .then(function (r) { return r.json().catch(function () { return { success: false, error: 'HTTP ' + r.status }; }); });
    }

    var state = { kind: 'show', id: null, title: '', template: '', saved: '', busy: false, seq: 0 };

    // ── one-time styles ──────────────────────────────────────────────────────
    function ensureStyles() {
        if (document.getElementById('vrn-styles')) return;
        var A = 'var(--accent-rgb, 88 101 242)';
        var css =
            '.vrn-overlay{position:fixed;inset:0;z-index:9100;background:rgba(5,5,8,.55);backdrop-filter:blur(4px);' +
                'opacity:0;transition:opacity .22s ease;}' +
            '.vrn-overlay.vrn-open{opacity:1;}' +
            '.vrn-panel{position:fixed;top:0;right:0;bottom:0;width:min(680px,100vw);z-index:9101;display:flex;' +
                'flex-direction:column;background:var(--bg-secondary,#16171c);border-left:1px solid rgba(255,255,255,.08);' +
                'transform:translateX(100%);transition:transform .26s cubic-bezier(.22,.9,.3,1);}' +
            '.vrn-panel.vrn-open{transform:none;}' +
            '.vrn-head{display:flex;align-items:center;gap:12px;padding:18px 20px;border-bottom:1px solid rgba(255,255,255,.07);}' +
            '.vrn-head h3{margin:0;font-size:1.05rem;flex:1;}' +
            '.vrn-sub{font-size:.8rem;opacity:.62;margin-top:2px;}' +
            '.vrn-x{background:none;border:0;color:inherit;font-size:1.4rem;cursor:pointer;opacity:.6;line-height:1;}' +
            '.vrn-x:hover{opacity:1;}' +
            '.vrn-body{flex:1;overflow:auto;padding:18px 20px;}' +
            '.vrn-label{font-size:.78rem;text-transform:uppercase;letter-spacing:.06em;opacity:.6;margin:0 0 6px;}' +
            '.vrn-input{width:100%;padding:10px 12px;border-radius:8px;font-family:ui-monospace,SFMono-Regular,Menlo,monospace;' +
                'font-size:.86rem;background:rgba(255,255,255,.04);border:1px solid rgba(255,255,255,.12);color:inherit;}' +
            '.vrn-input:focus{outline:none;border-color:rgba(' + A + ',.75);}' +
            '.vrn-row{display:flex;gap:8px;align-items:center;margin-top:8px;flex-wrap:wrap;}' +
            '.vrn-mini{background:rgba(255,255,255,.06);border:1px solid rgba(255,255,255,.12);color:inherit;' +
                'border-radius:7px;padding:5px 10px;font-size:.76rem;cursor:pointer;}' +
            '.vrn-mini:hover{background:rgba(255,255,255,.11);}' +
            '.vrn-chips{display:flex;flex-wrap:wrap;gap:6px;margin-top:8px;}' +
            '.vrn-chip{background:rgba(' + A + ',.13);border:1px solid rgba(' + A + ',.32);border-radius:999px;' +
                'padding:4px 11px;font-size:.76rem;cursor:pointer;font-family:ui-monospace,Menlo,monospace;' +
                'color:inherit;transition:background .15s;}' +
            '.vrn-chip:hover{background:rgba(' + A + ',.26);}' +
            '.vrn-chip small{opacity:.65;font-family:inherit;margin-left:6px;}' +
            '.vrn-section{margin-top:22px;}' +
            '.vrn-list{margin-top:10px;display:flex;flex-direction:column;gap:8px;}' +
            '.vrn-item{padding:9px 11px;border-radius:8px;background:rgba(255,255,255,.03);' +
                'border:1px solid rgba(255,255,255,.07);font-size:.82rem;}' +
            '.vrn-from{opacity:.55;text-decoration:line-through;word-break:break-all;}' +
            '.vrn-to{color:rgb(' + A + ');word-break:break-all;margin-top:3px;}' +
            '.vrn-empty{opacity:.6;font-size:.85rem;padding:14px 0;}' +
            '.vrn-warn{margin-top:10px;padding:9px 11px;border-radius:8px;font-size:.8rem;' +
                'background:rgba(240,173,78,.12);border:1px solid rgba(240,173,78,.3);}' +
            '.vrn-foot{padding:14px 20px;border-top:1px solid rgba(255,255,255,.07);display:flex;gap:10px;' +
                'align-items:center;justify-content:flex-end;}' +
            '.vrn-count{margin-right:auto;font-size:.82rem;opacity:.7;}' +
            '.vrn-apply{background:rgb(' + A + ');border:0;color:#fff;border-radius:8px;padding:9px 18px;' +
                'font-size:.86rem;cursor:pointer;}' +
            '.vrn-apply[disabled]{opacity:.45;cursor:default;}';
        var el = document.createElement('style');
        el.id = 'vrn-styles';
        el.textContent = css;
        document.head.appendChild(el);
    }

    // ── shell ────────────────────────────────────────────────────────────────
    function close() {
        var ov = document.getElementById('vrn-overlay');
        var pn = document.getElementById('vrn-panel');
        if (ov) ov.classList.remove('vrn-open');
        if (pn) pn.classList.remove('vrn-open');
        setTimeout(function () {
            if (ov) ov.remove();
            if (pn) pn.remove();
        }, 260);
        document.removeEventListener('keydown', onKey);
    }

    function onKey(e) { if (e.key === 'Escape') close(); }

    function build() {
        ensureStyles();
        var old = document.getElementById('vrn-panel');
        if (old) old.remove();
        old = document.getElementById('vrn-overlay');
        if (old) old.remove();

        var ov = document.createElement('div');
        ov.id = 'vrn-overlay';
        ov.className = 'vrn-overlay';
        ov.addEventListener('click', close);

        var pn = document.createElement('div');
        pn.id = 'vrn-panel';
        pn.className = 'vrn-panel';
        pn.innerHTML =
            '<div class="vrn-head">' +
                '<div><h3>Rename Files</h3><div class="vrn-sub">' + esc(state.title) + '</div></div>' +
                '<button class="vrn-x" type="button" data-vrn-close aria-label="Close">&times;</button>' +
            '</div>' +
            '<div class="vrn-body">' +
                '<p class="vrn-label">Naming template</p>' +
                '<input class="vrn-input" id="vrn-template" spellcheck="false" ' +
                    'placeholder="loading your template…">' +
                '<div class="vrn-row">' +
                    '<button class="vrn-mini" type="button" data-vrn-reset>Reset to saved</button>' +
                    '<button class="vrn-mini" type="button" data-vrn-default>Use the default</button>' +
                    '<span class="vrn-sub" style="margin:0">Slashes make folders. This is a one-off — ' +
                        'your saved template is not changed.</span>' +
                '</div>' +
                '<div class="vrn-section">' +
                    '<p class="vrn-label">Variables — click to insert</p>' +
                    '<div class="vrn-chips" id="vrn-chips"></div>' +
                '</div>' +
                '<div class="vrn-section">' +
                    '<p class="vrn-label">Preview</p>' +
                    '<div class="vrn-list" id="vrn-list">' +
                        '<div class="vrn-empty">Loading…</div>' +
                    '</div>' +
                '</div>' +
            '</div>' +
            '<div class="vrn-foot">' +
                '<span class="vrn-count" id="vrn-count"></span>' +
                '<button class="vrn-mini" type="button" data-vrn-close>Cancel</button>' +
                '<button class="vrn-apply" type="button" id="vrn-apply" disabled>Rename</button>' +
            '</div>';

        document.body.appendChild(ov);
        document.body.appendChild(pn);
        // rAF gives the browser a frame to apply the closed transform before the
        // open class starts the transition — but it does NOT fire in a tab that
        // isn't compositing (backgrounded, or a headless check). The panel starts
        // at translateX(100%), so never adding the class leaves it fully built and
        // fully off-screen. Time-based fallback so it always ends up open.
        var reveal = function () {
            ov.classList.add('vrn-open');
            pn.classList.add('vrn-open');
        };
        requestAnimationFrame(reveal);
        setTimeout(reveal, 50);
        document.addEventListener('keydown', onKey);

        pn.addEventListener('click', function (e) {
            if (e.target.closest('[data-vrn-close]')) { close(); return; }
            if (e.target.closest('[data-vrn-reset]')) { setTemplate(state.saved); return; }
            if (e.target.closest('[data-vrn-default]')) { setTemplate(state.defaultTemplate); return; }
            var chip = e.target.closest('[data-vrn-token]');
            if (chip) { insertToken(chip.getAttribute('data-vrn-token')); return; }
            if (e.target.closest('#vrn-apply')) { applyRenames(); }
        });

        var input = document.getElementById('vrn-template');
        var t = null;
        input.addEventListener('input', function () {
            state.template = input.value;
            clearTimeout(t);
            t = setTimeout(refreshPreview, 350);   // debounce — every keystroke would hammer the disk
        });
    }

    function setTemplate(v) {
        var input = document.getElementById('vrn-template');
        if (!input) return;
        input.value = state.template = String(v || '');
        refreshPreview();
    }

    function insertToken(token) {
        var input = document.getElementById('vrn-template');
        if (!input) return;
        var at = input.selectionStart == null ? input.value.length : input.selectionStart;
        var end = input.selectionEnd == null ? at : input.selectionEnd;
        input.value = input.value.slice(0, at) + token + input.value.slice(end);
        state.template = input.value;
        input.focus();
        input.setSelectionRange(at + token.length, at + token.length);
        refreshPreview();
    }

    // ── data ─────────────────────────────────────────────────────────────────
    function loadTokens() {
        return jget(API + '/tokens?kind=' + encodeURIComponent(state.kind) + '&id=' + encodeURIComponent(state.id))
            .then(function (d) {
                if (!d || !d.success) {
                    document.getElementById('vrn-list').innerHTML =
                        '<div class="vrn-empty">' + esc((d && d.error) || 'Could not load the naming template.') + '</div>';
                    return null;
                }
                state.saved = d.template || '';
                state.defaultTemplate = d.default_template || '';
                state.template = state.saved;
                var input = document.getElementById('vrn-template');
                if (input) { input.value = state.saved; input.placeholder = state.defaultTemplate; }
                var chips = document.getElementById('vrn-chips');
                chips.innerHTML = (d.tokens || []).map(function (t) {
                    var ex = t.example ? '<small>' + esc(t.example) + '</small>' : '';
                    return '<button class="vrn-chip" type="button" data-vrn-token="' + esc(t.token) + '" title="' +
                        esc(t.description || '') + '">' + esc(t.token) + ex + '</button>';
                }).join('') || '<span class="vrn-empty">No variables available.</span>';
                return d;
            });
    }

    function refreshPreview() {
        var seq = ++state.seq;         // ignore results from superseded keystrokes
        var list = document.getElementById('vrn-list');
        var applyBtn = document.getElementById('vrn-apply');
        var count = document.getElementById('vrn-count');
        if (!list) return;
        list.innerHTML = '<div class="vrn-empty">Working out the new names…</div>';
        if (applyBtn) applyBtn.disabled = true;
        return jpost(API + '/preview/title',
                     { kind: state.kind, id: state.id, template: state.template })
            .then(function (d) {
                if (seq !== state.seq) return;      // a newer keystroke already won
                if (!d || !d.success) {
                    list.innerHTML = '<div class="vrn-empty">' +
                        esc((d && d.error) || 'Preview failed.') + '</div>';
                    if (count) count.textContent = '';
                    return;
                }
                var entries = d.entries || [];
                if (!entries.length) {
                    list.innerHTML = d.unresolved
                        ? '<div class="vrn-empty">Nothing to rename — none of this title’s ' + d.unresolved +
                          ' file path(s) could be matched to a file on disk. Check your library paths in ' +
                          'Settings → Organization.</div>'
                        : '<div class="vrn-empty">Every file already matches this template.</div>';
                    if (count) count.textContent = '';
                    if (applyBtn) applyBtn.disabled = true;
                    return;
                }
                list.innerHTML = entries.map(function (en) {
                    return '<div class="vrn-item" title="' + esc(en.current + '\n→\n' + en.proposed) + '">' +
                        '<div class="vrn-from">' + esc(basename(en.current)) + '</div>' +
                        '<div class="vrn-to">' + esc(basename(en.proposed)) + '</div>' +
                    '</div>';
                }).join('') + (d.unresolved
                    ? '<div class="vrn-warn">' + d.unresolved + ' file(s) skipped — their stored path ' +
                      'could not be matched to a file on disk.</div>'
                    : '');
                if (count) count.textContent = entries.length + ' file(s) will be renamed';
                if (applyBtn) applyBtn.disabled = false;
            });
    }

    function applyRenames() {
        if (state.busy) return;
        var applyBtn = document.getElementById('vrn-apply');
        confirmDlg({
            title: 'Rename these files on disk?',
            message: 'Every previewed file is moved to its new name. Subtitles and other sidecars ' +
                     'travel with it, and a name that is already taken is skipped rather than overwritten.',
            confirmText: 'Rename', destructive: true,
        }).then(function (ok) {
            if (!ok) return;
            state.busy = true;
            if (applyBtn) applyBtn.disabled = true;
            // Scope AND template go with the request: the server rebuilds the plan
            // from them rather than trusting the paths this page is holding.
            jpost(API + '/apply/title',
                  { kind: state.kind, id: state.id, template: state.template })
                .then(function (d) {
                    state.busy = false;
                    if (d && d.success) {
                        toast(d.renamed + ' file(s) renamed' +
                              (d.skipped ? ', ' + d.skipped + ' skipped' : ''), 'success');
                        refreshPreview();
                    } else {
                        toast((d && d.error) || 'Rename failed', 'error');
                        if (applyBtn) applyBtn.disabled = false;
                    }
                })
                .catch(function () {
                    state.busy = false;
                    if (applyBtn) applyBtn.disabled = false;
                    toast('Rename failed', 'error');
                });
        });
    }

    // ── entry point ──────────────────────────────────────────────────────────
    function open(opts) {
        opts = opts || {};
        // Renaming library files is management — the endpoints are admin-only,
        // so a non-admin reaching this would only collect 403s.
        var isAdmin = (typeof currentProfile === 'undefined') || !currentProfile ||
            !!currentProfile.is_admin || currentProfile.id === 1;
        if (!isAdmin) { toast('Only an admin can rename library files', 'error'); return; }
        if (opts.id == null) { toast('This title has no library entry to rename', 'error'); return; }
        state.kind = opts.kind === 'movie' ? 'movie' : 'show';
        state.id = opts.id;
        state.title = opts.title || '';
        state.busy = false;
        build();
        loadTokens().then(function (d) { if (d) refreshPreview(); });
    }

    window.VideoRename = { open: open, close: close };
})();
