/*
 * Commissary — set up a title's search before it is owned.
 *
 *   CommissaryTitleSetup.applies(detail)  — a TMDB detail page with no library row
 *   CommissaryTitleSetup.buttonHtml()     — the More-menu entry (upstream's Manage style)
 *   CommissaryTitleSetup.open(detail)     — the slide-over
 *
 * Upstream's Manage panel only opens for a title already in the library, which
 * hides Series type and "Also known as" — the two settings that decide whether
 * the FIRST release matches at all — behind having downloaded it. This panel
 * is built from upstream's own Manage classes (vmg-*) and writes to
 * /api/video/commissary/title/<kind>/<tmdb_id>/overrides; once the title is
 * owned the values show up in upstream's Manage panel as normal.
 */
(function () {
    'use strict';

    function esc(s) {
        return String(s == null ? '' : s)
            .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;');
    }
    function toast(msg, type) { if (typeof showToast === 'function') showToast(msg, type); }

    var SERIES = [['standard', 'Standard'], ['daily', 'Daily (releases by air date)'],
                  ['anime', 'Anime (absolute numbering)']];
    var PACKS = [['auto', 'Follow the global setting'], ['prefer', 'Always try a season pack first'],
                 ['never', 'Never grab season packs'],
                 ['only', 'Season packs only — wait for one']];

    var state = null;   // {kind, tmdbId, overlay, saved}

    function isAdmin() {
        // the PUT is admin-gated server-side; hide the entry the same way
        // upstream hides its other management actions
        try {
            if (typeof getCurrentProfileContext !== 'function') return true;
            var ctx = getCurrentProfileContext();
            return !ctx || !!ctx.isAdmin;
        } catch (e) { return true; }
    }

    function applies(d) {
        return !!d && d.source === 'tmdb' && (d.kind === 'movie' || d.kind === 'show') &&
            d.tmdb_id != null && d.library_id == null && isAdmin();
    }

    function buttonHtml() {
        return '<button class="vd-manage-btn" type="button" data-vd-act="commissary-title-setup" ' +
            'title="How this title is searched for, before it is in your library">' +
            '<span class="vd-manage-ic">✎</span> Manage</button>';
    }

    function options(list, cur) {
        return list.map(function (o) {
            return '<option value="' + o[0] + '"' + (o[0] === cur ? ' selected' : '') + '>' +
                esc(o[1]) + '</option>';
        }).join('');
    }

    function panelHtml(title, s) {
        var show = s.kind === 'show';
        return '<div class="vmg-panel" role="dialog" aria-modal="true" aria-label="Manage search">' +
            '<div class="vmg-head">' +
                '<div class="vmg-kick"><span class="vmg-kick-dot"></span>Manage</div>' +
                '<div class="vmg-title">' + esc(title) + '</div>' +
                '<div class="vmg-sub">Not in your library yet. These decide how it is searched for, ' +
                    'and carry over once it is.</div>' +
                '<button class="vmg-save" type="button" data-cts-save disabled>Save</button>' +
                '<button class="vmg-close" type="button" data-cts-close aria-label="Close">×</button>' +
            '</div>' +
            '<div class="vmg-body">' +
                '<div class="vmg-sect">Acquisition</div>' +
                (show ? '<div class="vmg-field"><label>Series type</label>' +
                    '<select class="vmg-input" data-cts-f="series_type">' +
                    options(SERIES, s.series_type || 'standard') + '</select></div>' : '') +
                '<div class="vmg-field"><label>Also known as</label>' +
                    '<input class="vmg-input" data-cts-f="manual_aliases" spellcheck="false" ' +
                    'placeholder="names the scene uses, comma separated" value="' +
                    esc((s.manual_aliases || []).join(', ')) + '">' +
                    '<div class="vmg-hint">TMDB’s own aliases are already matched. Add one here ' +
                    'only when a release is named something TMDB does not list.</div></div>' +
                (show ? '<div class="vmg-sect">Acquisition overrides</div>' +
                    '<div class="vmg-field"><label>Season packs</label>' +
                    '<select class="vmg-input" data-cts-f="pack_preference">' +
                    options(PACKS, s.pack_preference || 'auto') + '</select></div>' : '') +
            '</div>' +
        '</div>';
    }

    // Same list cleaning as upstream's Manage panel: trimmed, de-duped
    // case-insensitively, empties dropped.
    function parseList(text) {
        var seen = {}, out = [];
        String(text || '').split(',').forEach(function (raw) {
            var v = raw.trim();
            if (!v || seen[v.toLowerCase()]) return;
            seen[v.toLowerCase()] = 1; out.push(v);
        });
        return out;
    }

    function current() {
        var out = {};
        state.overlay.querySelectorAll('[data-cts-f]').forEach(function (el) {
            var k = el.getAttribute('data-cts-f');
            out[k] = k === 'manual_aliases' ? parseList(el.value) : el.value;
        });
        return out;
    }

    function dirty() {
        return JSON.stringify(current()) !== state.saved;
    }

    function close() {
        if (!state) return;
        var ov = state.overlay;
        state = null;
        document.removeEventListener('keydown', onKey, true);
        ov.classList.remove('vmg-open');
        setTimeout(function () { ov.remove(); }, 230);
    }

    function onKey(e) {
        if (e.key === 'Escape' && state) { e.stopPropagation(); close(); }
    }

    function save() {
        var s = state;
        if (!s) return;
        var btn = s.overlay.querySelector('[data-cts-save]');
        btn.disabled = true;
        var body = current();
        fetch('/api/video/commissary/title/' + s.kind + '/' + s.tmdbId + '/overrides', {
            method: 'PUT', headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(body) })
            .then(function (r) { if (!r.ok) throw new Error(); return r.json(); })
            .then(function () {
                if (state === s) s.saved = JSON.stringify(body);
                // the same wording as upstream's Manage panel
                toast('Saved — applies from the next search', 'success');
            })
            .catch(function () {
                if (state === s) btn.disabled = false;
                toast('Couldn’t save the overrides', 'error');
            });
    }

    function open(d) {
        if (!applies(d)) return;
        if (state) close();
        if (window.VideoManage && window.VideoManage.ensureStyles) window.VideoManage.ensureStyles();
        var kind = d.kind, tmdbId = d.tmdb_id;
        // ?t: upstream's fetch-dedupe.js would hand a reopen within 2.5s of a save the old answer
        fetch('/api/video/commissary/title/' + kind + '/' + encodeURIComponent(tmdbId) + '/overrides?t=' + Date.now())
            .then(function (r) { return r.ok ? r.json() : null; })
            .then(function (s) {
                if (!s) { toast('Couldn’t load item', 'error'); return; }
                var ov = document.createElement('div');
                ov.className = 'vmg-overlay';
                ov.setAttribute('data-commissary-title-setup', '');
                ov.innerHTML = panelHtml(d.title || '', s);
                document.body.appendChild(ov);
                state = { kind: kind, tmdbId: tmdbId, overlay: ov, saved: '' };
                state.saved = JSON.stringify(current());
                var refresh = function () {
                    if (state) ov.querySelector('[data-cts-save]').disabled = !dirty();
                };
                ov.addEventListener('input', refresh);
                ov.addEventListener('change', refresh);
                ov.addEventListener('click', function (e) {
                    if (e.target === ov || e.target.closest('[data-cts-close]')) { close(); return; }
                    if (e.target.closest('[data-cts-save]')) save();
                });
                document.addEventListener('keydown', onKey, true);
                // a timer, not requestAnimationFrame: a hidden tab pauses rAF
                setTimeout(function () { ov.classList.add('vmg-open'); }, 20);
            })
            .catch(function () { toast('Couldn’t load item', 'error'); });
    }

    window.CommissaryTitleSetup = { applies: applies, buttonHtml: buttonHtml, open: open };
})();
