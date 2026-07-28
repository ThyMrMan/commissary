/*
 * SoulSync — Video Import page (isolated).
 *
 * Mirrors the music Import page's job for the video side: a "Needs Attention" queue
 * of downloads that finished but couldn't be auto-placed (sample / wrong episode /
 * not-an-upgrade / corrupt / parse fail). Each one is resolved by HAND — pick the
 * right movie or show+episode (library/owned results float to the top, falling back
 * to a full TMDB search) and place it, or dismiss it.
 *
 * Reads /api/video/import/failed; resolves via /import/<id>/place + /dismiss; the
 * identity picker reuses /api/video/search. Polls every 5s while shown, like the
 * music page. Self-contained IIFE, no globals.
 */
(function () {
    'use strict';

    var PAGE_ID = 'video-import';
    var POLL_MS = 5000;
    var state = { loaded: false, items: [], resolve: null, expanded: {} };
    var pollTimer = null;
    var searchTimer = null;
    var _lastSig = null;

    function $(s, r) { return (r || document).querySelector(s); }
    function esc(s) {
        return String(s == null ? '' : s)
            .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;');
    }
    function basename(p) { return String(p || '').replace(/\\/g, '/').replace(/\/+$/, '').split('/').pop(); }
    function toast(msg, kind) { if (typeof showToast === 'function') showToast(msg, kind || 'info'); }
    function isShown() { return document.body.getAttribute('data-video-page') === PAGE_ID; }
    function fmtSize(bytes) {
        if (bytes == null) return '';
        var gb = bytes / (1024 * 1024 * 1024);
        return gb >= 0.1 ? (Math.round(gb * 10) / 10) + ' GB' : Math.round(bytes / (1024 * 1024)) + ' MB';
    }
    function pad2(n) { n = parseInt(n, 10) || 0; return (n < 10 ? '0' : '') + n; }

    // Why the auto-importer parked it — classified into a colored chip so the
    // queue reads at a glance (the full error text lives in the drawer).
    var REASONS = [
        [/sample|too short|short file|duration|min\b/i, ['sample', 'Sample / too short']],
        [/upgrade|better|not an? improvement|existing copy/i, ['upgrade', 'Not an upgrade']],
        [/corrupt|unreadable|ffprobe|damaged|invalid stream/i, ['corrupt', 'Corrupt file']],
        [/parse|identif|match|recogni|couldn.t tell|unknown episode|wrong episode/i, ['identify', "Couldn't identify"]],
    ];
    function classifyReason(text) {
        var t = String(text || '');
        for (var i = 0; i < REASONS.length; i++) if (REASONS[i][0].test(t)) return REASONS[i][1];
        return ['other', 'Needs attention'];
    }

    // ── needs-attention list ──────────────────────────────────────────────────
    function load() {
        fetch('/api/video/import/failed', { headers: { Accept: 'application/json' } })
            .then(function (r) { return r.ok ? r.json() : null; })
            .then(function (d) {
                state.loaded = true;
                state.items = (d && d.items) || [];
                render();
            })
            .catch(function () { state.loaded = true; render(); });
    }

    function isEpisode(it) { return it.scope === 'episode' || it.kind === 'show'; }

    function drawerHTML(it) {
        function fact(k, v, cls) {
            return v ? '<div class="vimp-f' + (cls ? ' ' + cls : '') + '"><span class="vimp-fk">' + esc(k) +
                '</span><span class="vimp-fv">' + v + '</span></div>' : '';
        }
        var se = (it.season != null && it.episode != null)
            ? 'S' + pad2(it.season) + 'E' + pad2(it.episode) : '';
        var facts = '';
        facts += fact('Identified as', esc([it.title, it.year ? '(' + it.year + ')' : '', se].filter(Boolean).join(' ')));
        facts += fact('Release', esc(it.release_title));
        facts += fact('Quality', esc(it.quality_label));
        facts += fact('On disk', it.file_exists ? esc(fmtSize(it.file_size)) :
            '<span class="vimp-fv-warn">file is gone</span>');
        if (it.size_bytes && it.file_size && Math.abs(it.size_bytes - it.file_size) > 1024 * 1024) {
            facts += fact('Advertised', esc(fmtSize(it.size_bytes)) + ' <span class="vimp-fv-dim">(differs from disk)</span>');
        }
        facts += fact('Source', esc([it.source, it.username ? '👤 ' + it.username : ''].filter(Boolean).join('  ·  ')));
        facts += fact('Attempts', it.attempts > 1 ? esc(it.attempts + 'x') : '');
        facts += fact('Grabbed', esc(String(it.grabbed_at || '').slice(0, 16).replace('T', '  ')));
        var path = it.file
            ? '<div class="vimp-f vimp-f--wide"><span class="vimp-fk">Path</span>' +
              '<span class="vimp-fv vimp-mono">' + esc(it.file) + '</span>' +
              '<button class="vimp-copy" type="button" data-vimp-copy="' + esc(it.file) + '" title="Copy path">⧉</button></div>'
            : '';
        var reason = it.reason
            ? '<div class="vimp-f vimp-f--wide vimp-f--err"><span class="vimp-fk">Why it\'s here</span>' +
              '<span class="vimp-fv">' + esc(it.reason) + '</span></div>'
            : '';
        return '<div class="vimp-dr-facts">' + facts + path + reason + '</div>' +
            '<div class="vimp-dr-actions">' +
                '<button class="vimp-btn vimp-btn--danger" type="button" data-vimp-delete="' + esc(it.id) + '"' +
                    (it.file_exists ? '' : ' disabled title="The file is no longer on disk"') + '>Delete file</button>' +
                '<span class="vimp-dr-spacer"></span>' +
                '<button class="vimp-btn vimp-btn--ghost" type="button" data-vimp-dismiss="' + esc(it.id) + '">Dismiss</button>' +
                '<button class="vimp-btn vimp-btn--place" type="button" data-vimp-place="' + esc(it.id) + '">' + PLACE_SVG + ' Place&hellip;</button>' +
            '</div>';
    }

    var PLACE_SVG = '<svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round"><path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"/><polyline points="7 10 12 15 17 10"/><line x1="12" y1="15" x2="12" y2="3"/></svg>';

    function card(it) {
        var ep = isEpisode(it);
        var open = !!state.expanded[it.id];
        var rc = classifyReason(it.reason);
        var se = (it.season != null && it.episode != null)
            ? ' · S' + pad2(it.season) + 'E' + pad2(it.episode) : '';
        var art = it.poster_url
            ? '<div class="vimp-art"><img src="' + esc(it.poster_url) + '" alt="" loading="lazy" ' +
              'onerror="this.parentNode.classList.add(\'vimp-art--none\');this.remove()">' +
              '<span class="vimp-art-badge">' + (ep ? '📺' : '🎬') + '</span></div>'
            : '<div class="vimp-art vimp-art--none">' + (ep ? '📺' : '🎬') +
              '<span class="vimp-art-badge">' + (ep ? '📺' : '🎬') + '</span></div>';
        return '<div class="vimp-card' + (open ? ' vimp-card--open' : '') +
            '" data-vimp-card="' + esc(it.id) + '" data-vtype="' + (ep ? 'tv' : 'movie') + '">' +
            '<div class="vimp-card-row">' +
                art +
                '<div class="vimp-card-main">' +
                    '<div class="vimp-card-title" title="' + esc(it.title || it.release_title) + '">' +
                        esc(it.title || it.release_title || 'Unknown') +
                        (it.year ? ' <span class="vimp-card-year">(' + esc(it.year) + ')</span>' : '') +
                        esc(se) + '</div>' +
                    '<div class="vimp-card-meta">' +
                        '<span class="vimp-rchip vimp-rchip--' + rc[0] + '">' + esc(rc[1]) + '</span>' +
                        (it.quality_label ? '<span class="vimp-qchip">' + esc(it.quality_label) + '</span>' : '') +
                        '<span class="vimp-card-file" title="' + esc(it.file) + '">' + esc(basename(it.file) || '—') + '</span>' +
                    '</div>' +
                '</div>' +
                '<div class="vimp-card-side">' +
                    (it.file_size != null ? '<span class="vimp-card-size">' + esc(fmtSize(it.file_size)) + '</span>' : '') +
                    '<button class="vimp-btn vimp-btn--place" type="button" data-vimp-place="' + esc(it.id) + '">' + PLACE_SVG + ' Place&hellip;</button>' +
                    '<span class="vimp-caret">' + (open ? '▴' : '▾') + '</span>' +
                '</div>' +
            '</div>' +
            '<div class="vimp-drawer"' + (open ? '' : ' hidden') + '>' + (open ? drawerHTML(it) : '') + '</div>' +
        '</div>';
    }

    function render(force) {
        var grid = $('[data-vimp-grid]');
        var loading = $('[data-vimp-loading]');
        var empty = $('[data-vimp-empty]');
        var count = $('[data-vimp-count]');
        if (!grid) return;
        if (loading) loading.classList.toggle('hidden', state.loaded);
        if (count) count.textContent = state.items.length ? String(state.items.length) : '';
        if (!state.loaded) { grid.innerHTML = ''; return; }
        // The 5s poll re-renders the whole grid; skip when nothing changed so an
        // open drawer (or a text selection) never blinks away under the user.
        var sig = JSON.stringify([state.items, state.expanded]);
        if (!force && sig === _lastSig) return;
        _lastSig = sig;
        if (!state.items.length) {
            grid.innerHTML = '';
            if (empty) empty.classList.remove('hidden');
            return;
        }
        if (empty) empty.classList.add('hidden');
        grid.innerHTML = state.items.map(card).join('');
    }

    function itemById(id) {
        for (var i = 0; i < state.items.length; i++)
            if (String(state.items[i].id) === String(id)) return state.items[i];
        return null;
    }

    // ── add-file modal: fully manual import, no failed download required ──────
    function ensureAddModal() {
        if ($('[data-vimp-add-modal]')) return;
        var m = document.createElement('div');
        m.className = 'vimp-modal';
        m.setAttribute('data-vimp-add-modal', '');
        m.innerHTML =
            '<div class="vimp-modal-scrim" data-vimp-add-close></div>' +
            '<div class="vimp-modal-card" role="dialog" aria-label="Add a file to import">' +
                '<div class="vimp-modal-head">' +
                    '<div class="vimp-modal-titles">' +
                        '<h2 class="vimp-modal-title">Add a file to import</h2>' +
                        '<div class="vimp-modal-file">The full path to a video file already on this server &mdash; ' +
                            'nothing needs to have failed first.</div>' +
                    '</div>' +
                    '<button class="vimp-modal-x" type="button" data-vimp-add-close aria-label="Close">&times;</button>' +
                '</div>' +
                '<div class="vimp-search">' +
                    '<input type="text" class="vimp-search-input" data-vimp-add-path ' +
                        'placeholder="/path/to/the/file.mkv" autocomplete="off" spellcheck="false">' +
                '</div>' +
                '<div class="vimp-browse" data-vimp-browse>' +
                    '<div class="vimp-browse-shortcuts" data-vimp-shortcuts></div>' +
                    '<div class="vimp-browse-crumb" data-vimp-crumb></div>' +
                    '<div class="vimp-browse-list" data-vimp-list></div>' +
                '</div>' +
                '<div class="vimp-modal-foot">' +
                    '<button class="vimp-btn vimp-btn--ghost" type="button" data-vimp-add-close>Cancel</button>' +
                    '<button class="vimp-btn vimp-btn--place" type="button" data-vimp-add-confirm disabled>Add</button>' +
                '</div>' +
            '</div>';
        document.body.appendChild(m);
    }

    // ── folder browser ────────────────────────────────────────────────────────
    // Typing a full path by hand was the only way in. The text field stays (it's
    // still the fastest route for a pasted path) but it starts on the download
    // folder and the list below walks the server's filesystem, so the usual case
    // is a couple of clicks.
    var _browse = { path: '', loading: false };

    function browseTo(path) {
        _browse.loading = true;
        renderBrowse(null);
        var q = path ? '?path=' + encodeURIComponent(path) : '';
        fetch('/api/video/import/browse' + q, { headers: { Accept: 'application/json' } })
            .then(function (r) { return r.json().catch(function () { return null; }); })
            .then(function (d) {
                _browse.loading = false;
                if (!d) { renderBrowse({ error: 'Couldn’t read that folder.' }); return; }
                if (d.path) _browse.path = d.path;
                renderBrowse(d);
            })
            .catch(function () {
                _browse.loading = false;
                renderBrowse({ error: 'Couldn’t read that folder.' });
            });
    }

    function renderBrowse(d) {
        var sc = $('[data-vimp-shortcuts]'), crumb = $('[data-vimp-crumb]'), list = $('[data-vimp-list]');
        if (!list) return;
        if (_browse.loading) { list.innerHTML = '<div class="vimp-browse-msg">Loading…</div>'; return; }
        if (!d) return;
        if (sc && d.shortcuts) {
            sc.innerHTML = (d.shortcuts || []).map(function (s) {
                return '<button type="button" class="vimp-chip' +
                    (s.path === _browse.path ? ' vimp-chip--on' : '') +
                    '" data-vimp-go="' + esc(s.path) + '">' + esc(s.label) + '</button>';
            }).join('');
        }
        if (crumb) crumb.textContent = d.path || '';
        if (d.error) {
            list.innerHTML = '<div class="vimp-browse-msg">' + esc(d.error) + '</div>';
            return;
        }
        var rows = '';
        // A season pack: offer the WHOLE folder in one go. Placing twelve files
        // one at a time means answering "which show is this?" twelve times, which
        // is the entire reason this exists. Only shown at 2+ numbered episodes.
        if (d.pack && d.pack.count >= 2) {
            var sn = (d.pack.seasons || []);
            rows += '<button type="button" class="vimp-row vimp-row--pack" data-vimp-pick-folder="' +
                esc(d.path) + '"><span class="vimp-row-ic">🗂️</span>' +
                '<span class="vimp-row-name">Import this whole folder — ' + d.pack.count +
                ' episodes' + (sn.length === 1 ? ' (season ' + sn[0] + ')'
                    : sn.length > 1 ? ' (seasons ' + sn.join(', ') + ')' : '') +
                '</span></button>';
        }
        if (d.parent) {
            rows += '<button type="button" class="vimp-row vimp-row--dir" data-vimp-go="' +
                esc(d.parent) + '"><span class="vimp-row-ic">↰</span>' +
                '<span class="vimp-row-name">Parent folder</span></button>';
        }
        rows += (d.dirs || []).map(function (x) {
            return '<button type="button" class="vimp-row vimp-row--dir" data-vimp-go="' + esc(x.path) + '">' +
                '<span class="vimp-row-ic">📁</span><span class="vimp-row-name">' + esc(x.name) + '</span></button>';
        }).join('');
        rows += (d.files || []).map(function (x) {
            return '<button type="button" class="vimp-row vimp-row--file" data-vimp-pick="' + esc(x.path) + '">' +
                '<span class="vimp-row-ic">🎬</span><span class="vimp-row-name">' + esc(x.name) + '</span>' +
                '<span class="vimp-row-size">' + fmtSize(x.size) + '</span></button>';
        }).join('');
        if (!rows) rows = '<div class="vimp-browse-msg">Nothing to import in this folder.</div>';
        else if (d.truncated) {
            rows += '<div class="vimp-browse-msg">Too many entries to show them all — ' +
                'open a subfolder or type the path above.</div>';
        }
        list.innerHTML = rows;
    }

    function fmtSize(n) {
        if (!n && n !== 0) return '';
        var u = ['B', 'KB', 'MB', 'GB', 'TB'], i = 0;
        while (n >= 1024 && i < u.length - 1) { n /= 1024; i++; }
        return (i > 1 ? n.toFixed(1) : Math.round(n)) + ' ' + u[i];
    }

    function pickBrowsedFile(path) {
        var input = $('[data-vimp-add-path]');
        if (input) input.value = path;
        syncAddConfirm();
        var list = $('[data-vimp-list]');
        if (list) {
            var on = list.querySelector('.vimp-row--on');
            if (on) on.classList.remove('vimp-row--on');
            var q = (window.CSS && CSS.escape) ? CSS.escape(path) : path;
            var me = list.querySelector('[data-vimp-pick="' + q + '"], [data-vimp-pick-folder="' + q + '"]');
            if (me) me.classList.add('vimp-row--on');
        }
    }

    function syncAddConfirm() {
        var input = $('[data-vimp-add-path]'), btn = $('[data-vimp-add-confirm]');
        if (btn) btn.disabled = !(input && input.value.trim());
    }

    function openAddFile() {
        ensureAddModal();
        var input = $('[data-vimp-add-path]');
        if (input) { input.value = ''; input.focus(); }
        var btn = $('[data-vimp-add-confirm]'); if (btn) { btn.disabled = true; btn.textContent = 'Add'; }
        _browse = { path: '', loading: false };
        browseTo('');   // no path → the backend opens on the first configured download folder
    }

    function closeAddFile() {
        var m = $('[data-vimp-add-modal]');
        if (m) m.remove();
    }

    function submitAddFile() {
        var input = $('[data-vimp-add-path]');
        var path = input ? input.value.trim() : '';
        if (!path) return;
        var btn = $('[data-vimp-add-confirm]'); if (btn) { btn.disabled = true; btn.textContent = 'Adding…'; }
        fetch('/api/video/import/add', {
            method: 'POST', headers: { 'Content-Type': 'application/json', Accept: 'application/json' },
            body: JSON.stringify({ path: path }),
        }).then(function (r) { return r.json().catch(function () { return null; }); })
            .then(function (d) {
                if (d && d.success) {
                    toast(d.already ? 'Already queued for placement' : 'Added — place it below', 'success');
                    closeAddFile();
                    load();
                } else {
                    toast((d && d.error) || 'Couldn’t add that file', 'error');
                    if (btn) { btn.disabled = false; btn.textContent = 'Add'; }
                }
            })
            .catch(function () { toast('Couldn’t add that file', 'error');
                if (btn) { btn.disabled = false; btn.textContent = 'Add'; } });
    }

    // ── resolve modal ─────────────────────────────────────────────────────────
    function openResolve(item) {
        state.resolve = {
            item: item,
            // 'season' = a whole FOLDER. It searches shows like an episode does,
            // but asks for no season/episode: every file carries its own, and the
            // dialog's numbers are deliberately not applied (see run_season_import).
            kind: item.scope === 'season' ? 'season'
                : ((item.scope === 'episode' || item.kind === 'show') ? 'episode' : 'movie'),
            pack: item.scope === 'season' ? { count: null, items: [] } : null,
            query: item.title || basename(item.file) || '',
            results: [], picked: null, season: item.season || '', episode: item.episode || '',
            searching: false, rootFolderId: '',
        };
        ensureModal();
        loadLibraries();
        renderModal();
        if (state.resolve.kind === 'season') loadPack(state.resolve);
        runSearch();
        var input = $('[data-vimp-q]');
        if (input) { input.value = state.resolve.query; input.focus(); }
    }

    function closeResolve() {
        state.resolve = null;
        var m = $('[data-vimp-modal]');
        if (m) m.remove();
    }

    function ensureModal() {
        if ($('[data-vimp-modal]')) return;
        var m = document.createElement('div');
        m.className = 'vimp-modal';
        m.setAttribute('data-vimp-modal', '');
        m.innerHTML =
            '<div class="vimp-modal-scrim" data-vimp-close></div>' +
            '<div class="vimp-modal-card" role="dialog" aria-label="Place file">' +
                '<div class="vimp-modal-head">' +
                    '<div class="vimp-modal-titles">' +
                        '<h2 class="vimp-modal-title">Place this file</h2>' +
                        '<div class="vimp-modal-file" data-vimp-modal-file></div>' +
                    '</div>' +
                    '<button class="vimp-modal-x" type="button" data-vimp-close aria-label="Close">&times;</button>' +
                '</div>' +
                '<div class="vimp-kindtabs" data-vimp-kindtabs>' +
                    '<button class="vimp-kindtab" type="button" data-vimp-kind="movie">Movie</button>' +
                    '<button class="vimp-kindtab" type="button" data-vimp-kind="episode">Episode</button>' +
                '</div>' +
                '<div class="vimp-search">' +
                    '<input type="text" class="vimp-search-input" data-vimp-q placeholder="Search your library &amp; TMDB&hellip;" autocomplete="off" spellcheck="false">' +
                '</div>' +
                '<div class="vimp-results" data-vimp-results></div>' +
                '<div class="vimp-lib" data-vimp-lib-row hidden>' +
                    '<label class="vimp-ep-field vimp-ep-field--wide">Library ' +
                        '<select data-vimp-lib></select></label>' +
                '</div>' +
                '<div class="vimp-pack" data-vimp-pack hidden></div>' +
                '<div class="vimp-ep" data-vimp-ep hidden>' +
                    '<label class="vimp-ep-field">Season <input type="number" min="0" data-vimp-season></label>' +
                    '<label class="vimp-ep-field">Episode <input type="number" min="0" data-vimp-episode></label>' +
                    '<label class="vimp-ep-field vimp-ep-field--wide">Title <input type="text" data-vimp-eptitle placeholder="optional"></label>' +
                '</div>' +
                '<div class="vimp-modal-foot">' +
                    '<button class="vimp-btn vimp-btn--ghost" type="button" data-vimp-close>Cancel</button>' +
                    '<button class="vimp-btn vimp-btn--place" type="button" data-vimp-confirm disabled>Place file</button>' +
                '</div>' +
            '</div>';
        document.body.appendChild(m);
    }

    // Which Library the placed file lands in. Without this the backend fell
    // back to the PRIMARY Library for the kind, so an Anime episode was filed
    // into the standard TV (or, when the kind was guessed wrong, Movies) root
    // with no way to say otherwise. Reads d.configured — the registry every
    // profile can see — not the admin-only d.movies/d.tv discovery list.
    var LIB_KEY = { movie: 'movies', episode: 'tv' };
    var _libs = null;
    function loadLibraries() {
        if (_libs) { renderLibraryPicker(); return; }
        fetch('/api/video/libraries', { headers: { Accept: 'application/json' } })
            .then(function (r) { return r.ok ? r.json() : null; })
            .then(function (d) { _libs = (d && d.configured) || {}; renderLibraryPicker(); })
            .catch(function () { _libs = {}; renderLibraryPicker(); });
    }

    function renderLibraryPicker() {
        var r = state.resolve, row = $('[data-vimp-lib-row]'), sel = $('[data-vimp-lib]');
        if (!r || !row || !sel) return;
        var libs = (_libs || {})[LIB_KEY[r.kind === 'season' ? 'episode' : r.kind]] || [];
        // One Library for this kind is not a choice — the backend's primary
        // fallback already lands there.
        if (libs.length < 2) { row.hidden = true; r.rootFolderId = ''; return; }
        if (!libs.some(function (l) { return String(l.id) === String(r.rootFolderId); }))
            r.rootFolderId = String(libs[0].id);   // lowest sort_order = the primary
        sel.innerHTML = libs.map(function (l) {
            return '<option value="' + esc(l.id) + '"' +
                (String(l.id) === String(r.rootFolderId) ? ' selected' : '') + '>' +
                esc(l.label || l.server_title || 'Library') + '</option>';
        }).join('');
        row.hidden = false;
    }

    // ── whole-folder (season pack) panel ──────────────────────────────────────
    // Loaded once per dialog. Shows exactly which files will be imported and the
    // episode each one parsed to — a pack whose names parse wrongly is far
    // cheaper to catch here than to unpick from the library afterwards.
    function packHTML(r) {
        var p = r.pack || {};
        if (p.error) return '<div class="vimp-pack-msg">' + esc(p.error) + '</div>';
        if (p.count == null) return '<div class="vimp-pack-msg">Reading folder…</div>';
        if (!p.count) return '<div class="vimp-pack-msg">No numbered episodes in this folder.</div>';
        var rows = (p.items || []).map(function (i) {
            return '<div class="vimp-pack-row"><span class="vimp-pack-se">S' + pad2(i.season) +
                'E' + pad2(i.episode) + '</span><span class="vimp-pack-name">' +
                esc(i.name) + '</span></div>';
        }).join('');
        return '<div class="vimp-pack-head">' + p.count + ' episode' + (p.count === 1 ? '' : 's') +
            ' will be imported' + (p.seasons && p.seasons.length === 1
                ? ' (season ' + p.seasons[0] + ')'
                : (p.seasons && p.seasons.length > 1 ? ' (seasons ' + p.seasons.join(', ') + ')' : '')) +
            '. Each file keeps its own episode number.</div>' +
            '<div class="vimp-pack-list">' + rows + '</div>' +
            (p.truncated ? '<div class="vimp-pack-msg">…and more.</div>' : '');
    }

    function loadPack(r) {
        fetch('/api/video/import/pack-preview?path=' + encodeURIComponent(r.item.file || ''),
              { headers: { Accept: 'application/json' } })
            .then(function (res) { return res.json().catch(function () { return {}; }); })
            .then(function (d) {
                if (state.resolve !== r) return;      // dialog closed or moved on
                r.pack = d && d.success
                    ? { count: d.count, seasons: d.seasons, items: d.items, truncated: d.truncated }
                    : { count: 0, items: [], error: (d && d.error) || 'Could not read that folder.' };
                renderModal();
            })
            .catch(function () {
                if (state.resolve !== r) return;
                r.pack = { count: 0, items: [], error: 'Could not read that folder.' };
                renderModal();
            });
    }

    function renderModal() {
        var r = state.resolve;
        if (!r) return;
        var fileEl = $('[data-vimp-modal-file]');
        if (fileEl) fileEl.textContent = basename(r.item.file) + ' — ' + (r.item.reason || '');
        var tabs = document.querySelectorAll('[data-vimp-kind]');
        for (var i = 0; i < tabs.length; i++)
            tabs[i].classList.toggle('vimp-kindtab--on', tabs[i].getAttribute('data-vimp-kind') === r.kind);
        var tabWrap = $('[data-vimp-kindtabs]');
        if (tabWrap) tabWrap.hidden = r.kind === 'season';
        renderLibraryPicker();
        var pk = $('[data-vimp-pack]');
        if (pk) {
            pk.hidden = r.kind !== 'season';
            if (r.kind === 'season') pk.innerHTML = packHTML(r);
        }
        var ep = $('[data-vimp-ep]');
        if (ep) ep.hidden = !(r.kind === 'episode' && r.picked);
        var sEl = $('[data-vimp-season]'); if (sEl && r.season !== '') sEl.value = r.season;
        var eEl = $('[data-vimp-episode]'); if (eEl && r.episode !== '') eEl.value = r.episode;
        renderResults();
        updateConfirm();
    }

    function renderResults() {
        var box = $('[data-vimp-results]');
        var r = state.resolve;
        if (!box || !r) return;
        if (r.searching) { box.innerHTML = '<div class="vimp-res-note">Searching&hellip;</div>'; return; }
        if (!r.results.length) { box.innerHTML = '<div class="vimp-res-note">No matches — try a different search.</div>'; return; }
        box.innerHTML = r.results.map(function (it, idx) {
            var on = r.picked && String(r.picked.media_id) === String(it.media_id);
            var meta = [it.year, it.owned ? 'In library' : null].filter(Boolean).join(' · ');
            var art = it.poster
                ? '<img class="vimp-res-img" src="' + esc(it.poster) + '" alt="" loading="lazy" onerror="this.style.visibility=\'hidden\'">'
                : '<div class="vimp-res-ph">' + (r.kind === 'movie' ? '🎬' : '📺') + '</div>';
            return '<button class="vimp-res' + (on ? ' vimp-res--on' : '') + (it.owned ? ' vimp-res--owned' : '') +
                '" type="button" data-vimp-pick="' + idx + '">' + art +
                '<span class="vimp-res-info"><span class="vimp-res-title">' + esc(it.title) + '</span>' +
                (meta ? '<span class="vimp-res-meta">' + esc(meta) + '</span>' : '') + '</span></button>';
        }).join('');
    }

    function updateConfirm() {
        var btn = $('[data-vimp-confirm]');
        var r = state.resolve;
        if (!btn || !r) return;
        var ok = !!r.picked && (r.kind === 'movie' || r.kind === 'season' ||
            (r.kind === 'episode' && r.season !== '' && r.episode !== ''));
        btn.disabled = !ok;
        var n = (r.pack || {}).count;
        btn.textContent = r.kind === 'season'
            ? (n ? 'Import ' + n + ' episode' + (n === 1 ? '' : 's') : 'Import folder')
            : 'Place file';
    }

    // Normalise a /api/video/search result into the picker's shape; keep only the
    // kind we're resolving (movies for 'movie', shows for 'episode'). Owned titles
    // (library_id present) are flagged so they can float to the top.
    //
    // `kind` FIRST: /api/video/search returns {kind:'movie'|'show'|'person', ...}
    // (see clients.py's search()) — it has no media_type, no type and no
    // first_air_date, so the old chain fell all the way through to its 'movie'
    // default for every row and shows could never appear under the Episode tab.
    // The other names are kept as a tolerant fallback for raw-TMDB shapes.
    function normResults(raw, kind) {
        var want = kind === 'episode' ? ['tv', 'show'] : ['movie'];
        var out = [];
        (raw || []).forEach(function (it) {
            var mt = String(it.kind || it.media_type || it.type ||
                            (it.first_air_date ? 'tv' : 'movie')).toLowerCase();
            if (want.indexOf(mt) === -1) return;
            var date = it.year || it.release_date || it.first_air_date || '';
            out.push({
                media_id: it.tmdb_id != null ? it.tmdb_id : it.id,
                title: it.title || it.name || 'Unknown',
                year: String(date).slice(0, 4) || null,
                poster: it.poster_url || it.poster || (it.poster_path ? 'https://image.tmdb.org/t/p/w185' + it.poster_path : ''),
                owned: it.library_id != null,
            });
        });
        out.sort(function (a, b) { return (b.owned ? 1 : 0) - (a.owned ? 1 : 0); });   // library first
        return out;
    }

    function runSearch() {
        var r = state.resolve;
        if (!r) return;
        var q = (r.query || '').trim();
        if (!q) { r.results = []; r.searching = false; renderResults(); return; }
        r.searching = true; renderResults();
        fetch('/api/video/search?q=' + encodeURIComponent(q), { headers: { Accept: 'application/json' } })
            .then(function (res) { return res.ok ? res.json() : null; })
            .then(function (d) {
                if (!state.resolve || state.resolve !== r) return;
                r.searching = false;
                r.results = normResults((d && d.results) || [], r.kind === 'season' ? 'episode' : r.kind);
                renderResults();
            })
            .catch(function () { if (state.resolve === r) { r.searching = false; renderResults(); } });
    }

    function place() {
        var r = state.resolve;
        if (!r || !r.picked) return;
        var body = {
            scope: r.kind, media_id: r.picked.media_id,
            title: r.picked.title, year: r.picked.year ? parseInt(r.picked.year, 10) : null,
            root_folder_id: r.rootFolderId || null,
        };
        if (r.kind === 'episode') {
            body.season = parseInt(r.season, 10);
            body.episode = parseInt(r.episode, 10);
            var t = $('[data-vimp-eptitle]'); if (t && t.value.trim()) body.episode_title = t.value.trim();
        }
        var btn = $('[data-vimp-confirm]'); if (btn) { btn.disabled = true; btn.textContent = 'Placing…'; }
        fetch('/api/video/import/' + r.item.id + '/place', {
            method: 'POST', headers: { 'Content-Type': 'application/json', Accept: 'application/json' },
            body: JSON.stringify(body),
        }).then(function (res) { return res.ok ? res.json() : res.json().catch(function () { return null; }); })
            .then(function (d) {
                if (d && d.success) {
                    // A pack's headline is what LANDED: partial success is success
                    // (already-owned or better-quality episodes are skipped), so
                    // "Placed X" alone would hide that 4 of 12 went in.
                    var msg = (d.imported != null && d.total != null)
                        ? 'Imported ' + d.imported + ' of ' + d.total + ' episodes into “' +
                          r.picked.title + '”'
                        : 'Placed “' + r.picked.title + '”';
                    toast(msg, 'success');
                    delete state.expanded[r.item.id]; closeResolve(); load();
                }
                else { toast((d && d.error) || 'Couldn’t place the file', 'error');
                    if (btn) { btn.disabled = false; updateConfirm(); } }
            })
            .catch(function () { toast('Couldn’t place the file', 'error');
                if (btn) { btn.disabled = false; updateConfirm(); } });
    }

    function _dismissCall(id, del, doneMsg) {
        fetch('/api/video/import/' + id + '/dismiss', {
            method: 'POST', headers: { 'Content-Type': 'application/json', Accept: 'application/json' },
            body: JSON.stringify({ delete_file: !!del }),
        }).then(function (r) { return r.ok ? r.json() : null; })
            .then(function (d) { if (d && d.success) { toast(doneMsg, 'info'); delete state.expanded[id]; load(); }
                else toast('Couldn’t dismiss', 'error'); })
            .catch(function () { toast('Couldn’t dismiss', 'error'); });
    }

    function dismiss(id) {
        var it = itemById(id);
        if (typeof showConfirmDialog === 'function') {
            showConfirmDialog({
                title: 'Dismiss this import',
                message: 'Remove “' + ((it && it.title) || basename(it && it.file)) + '” from the list? ' +
                    'The file stays on disk.',
                confirmText: 'Dismiss', cancelText: 'Cancel',
            }).then(function (ok) { if (ok) _dismissCall(id, false, 'Dismissed'); });
        } else { _dismissCall(id, false, 'Dismissed'); }
    }

    function dismissDelete(id) {
        var it = itemById(id);
        if (typeof showConfirmDialog !== 'function') return;
        showConfirmDialog({
            title: 'Delete this file',
            message: 'Delete “' + basename(it && it.file) + '” from disk and remove it from the ' +
                'list? This can’t be undone.',
            confirmText: 'Delete file', cancelText: 'Cancel', destructive: true,
        }).then(function (ok) { if (ok) _dismissCall(id, true, 'File deleted'); });
    }

    // ── events ────────────────────────────────────────────────────────────────
    function onGridClick(e) {
        var p = e.target.closest('[data-vimp-place]');
        if (p) { var it = itemById(p.getAttribute('data-vimp-place')); if (it) openResolve(it); return; }
        var d = e.target.closest('[data-vimp-dismiss]');
        if (d) { dismiss(d.getAttribute('data-vimp-dismiss')); return; }
        var del = e.target.closest('[data-vimp-delete]');
        if (del) { if (!del.disabled) dismissDelete(del.getAttribute('data-vimp-delete')); return; }
        var cp = e.target.closest('[data-vimp-copy]');
        if (cp) {
            var path = cp.getAttribute('data-vimp-copy');
            if (navigator.clipboard) navigator.clipboard.writeText(path).then(function () { toast('Path copied', 'success'); }, function () {});
            else toast('Copy not supported here', 'info');
            return;
        }
        // click anywhere else on the card ROW → toggle its detail drawer
        // (clicks inside the drawer body — selecting a path, say — don't close it)
        if (e.target.closest('button, a, input') || e.target.closest('.vimp-drawer')) return;
        var cardEl = e.target.closest('[data-vimp-card]');
        if (cardEl) {
            var id = cardEl.getAttribute('data-vimp-card');
            if (state.expanded[id]) delete state.expanded[id];
            else state.expanded[id] = true;
            render(true);
        }
    }

    function onModalClick(e) {
        if (e.target.closest('[data-vimp-close]')) { closeResolve(); return; }
        var k = e.target.closest('[data-vimp-kind]');
        if (k) { state.resolve.kind = k.getAttribute('data-vimp-kind'); state.resolve.picked = null;
            runSearch(); renderModal(); return; }
        var pk = e.target.closest('[data-vimp-pick]');
        if (pk) { var r = state.resolve;
            r.picked = r.results[parseInt(pk.getAttribute('data-vimp-pick'), 10)] || null;
            renderModal(); return; }
    }

    function onModalInput(e) {
        var r = state.resolve; if (!r) return;
        if (e.target.matches('[data-vimp-q]')) {
            r.query = e.target.value;
            clearTimeout(searchTimer); searchTimer = setTimeout(runSearch, 300); return;
        }
        if (e.target.matches('[data-vimp-season]')) { r.season = e.target.value; updateConfirm(); return; }
        if (e.target.matches('[data-vimp-episode]')) { r.episode = e.target.value; updateConfirm(); return; }
        if (e.target.matches('[data-vimp-lib]')) { r.rootFolderId = e.target.value || ''; return; }
    }

    function startPoll() {
        if (pollTimer) return;
        pollTimer = setInterval(function () { if (isShown() && !state.resolve) load(); }, POLL_MS);
    }

    function onShown(e) {
        if (e && e.detail !== PAGE_ID) return;
        load();
        startPoll();
    }

    function init() {
        var grid = $('[data-vimp-grid]');
        if (grid) grid.addEventListener('click', onGridClick);
        var refresh = $('[data-vimp-refresh]');
        if (refresh) refresh.addEventListener('click', load);
        var addBtn = $('[data-vimp-add]');
        if (addBtn) addBtn.addEventListener('click', openAddFile);
        // The resolve + add-file modals are created on demand; delegate from the document.
        document.addEventListener('click', function (e) {
            if (state.resolve && e.target.closest('[data-vimp-modal]')) {
                if (e.target.closest('[data-vimp-confirm]')) { place(); return; }
                onModalClick(e);
            }
            if (e.target.closest('[data-vimp-add-modal]')) {
                if (e.target.closest('[data-vimp-add-close]')) { closeAddFile(); return; }
                if (e.target.closest('[data-vimp-add-confirm]')) { submitAddFile(); return; }
                var go = e.target.closest('[data-vimp-go]');
                if (go) { browseTo(go.getAttribute('data-vimp-go')); return; }
                var pickDir = e.target.closest('[data-vimp-pick-folder]');
                if (pickDir) { pickBrowsedFile(pickDir.getAttribute('data-vimp-pick-folder')); return; }
                var pick = e.target.closest('[data-vimp-pick]');
                if (pick) { pickBrowsedFile(pick.getAttribute('data-vimp-pick')); return; }
            }
        });
        // 'change' too — a <select> (the Library picker) is not covered by 'input'
        // in every browser this ships to.
        document.addEventListener('change', function (e) {
            if (state.resolve && e.target.closest('[data-vimp-modal]')) onModalInput(e);
        });
        document.addEventListener('input', function (e) {
            if (state.resolve && e.target.closest('[data-vimp-modal]')) onModalInput(e);
            if (e.target.matches('[data-vimp-add-path]')) syncAddConfirm();
        });
        document.addEventListener('keydown', function (e) {
            if (e.key === 'Enter' && e.target.matches('[data-vimp-add-path]') && e.target.value.trim()) submitAddFile();
        });
        document.addEventListener('soulsync:video-page-shown', onShown);
        if (isShown()) onShown({ detail: PAGE_ID });
    }

    if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', init);
    else init();
})();
