/*
 * Commissary — import any file or folder (core/commissary/manual_import.py).
 *
 *   "Add file or folder…"  beside upstream's Refresh: browse the server's
 *                          download and library folders, filter as you type,
 *                          add a video file or a whole season folder to the queue
 *   Place on a folder row  "Place this folder": one show for every episode,
 *                          "Import as season" to renumber, a preview of each
 *                          file's S08E01 -> S07E01 before anything is copied
 *
 * Built from upstream's Import modal classes (vimp-*), but NOT its data
 * attributes: upstream's handlers and its ensureModal() key off
 * [data-vimp-modal], so these dialogs carry their own.
 */
(function () {
    'use strict';
    var API = '/api/video/import/';
    var browseState = null;     // {path, data, q, timer}
    var placeState = null;      // {item, picked, results, season, timer, asked}

    function toast(msg, kind) { if (typeof showToast === 'function') showToast(msg, kind || 'info'); }
    function esc(s) {
        return String(s == null ? '' : s).replace(/&/g, '&amp;').replace(/</g, '&lt;')
            .replace(/>/g, '&gt;').replace(/"/g, '&quot;');
    }
    function base(p) { return String(p || '').replace(/\\/g, '/').replace(/\/+$/, '').split('/').pop(); }
    function pad(n) { n = parseInt(n, 10) || 0; return (n < 10 ? '0' : '') + n; }
    function fmtSize(b) {
        if (!b) return '';
        var u = ['B', 'KB', 'MB', 'GB', 'TB'], i = 0;
        while (b >= 1024 && i < u.length - 1) { b /= 1024; i++; }
        return b.toFixed(i ? 1 : 0) + ' ' + u[i];
    }
    function refreshQueue() {
        var r = document.querySelector('[data-vimp-refresh]');
        if (r) r.click();
    }
    function modal(attr, title, sub) {
        var m = document.createElement('div');
        m.className = 'vimp-modal';
        m.setAttribute(attr, '');
        m.innerHTML =
            '<div class="vimp-modal-scrim" data-cim-close></div>' +
            '<div class="vimp-modal-card" role="dialog" aria-label="' + esc(title) + '">' +
                '<div class="vimp-modal-head"><div class="vimp-modal-titles">' +
                    '<h2 class="vimp-modal-title">' + esc(title) + '</h2>' +
                    '<div class="vimp-modal-file" data-cim-sub>' + esc(sub || '') + '</div></div>' +
                    '<button class="vimp-modal-x" type="button" data-cim-close aria-label="Close">&times;</button>' +
                '</div>' +
                '<div class="commissary-import-body" data-cim-body></div>' +
                '<div class="vimp-modal-foot" data-cim-foot></div>' +
            '</div>';
        document.body.appendChild(m);
        return m;
    }
    function searchBox(placeholder, attr) {
        return '<div class="vimp-search">' +
            '<svg class="vimp-search-icon" viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.8">' +
            '<circle cx="7" cy="7" r="4.6"/><path d="M10.8 10.8 14 14" stroke-linecap="round"/></svg>' +
            '<input type="text" class="vimp-search-input" ' + attr + ' placeholder="' + esc(placeholder) +
            '" autocomplete="off" spellcheck="false"></div>';
    }

    // ── the folder browser ───────────────────────────────────────────────────
    function openBrowse() {
        if (browseState) return;
        var m = modal('data-cim-browse', 'Add a file or folder', '');
        browseState = { m: m, path: '', data: null, q: '', timer: null };
        m.querySelector('[data-cim-body]').innerHTML =
            '<div class="vimp-kindtabs" data-cim-shortcuts></div>' +
            searchBox('Filter this folder…', 'data-cim-filter') +
            '<div class="vimp-results" data-cim-list style="max-height:46vh;overflow:auto"></div>';
        m.querySelector('[data-cim-foot]').innerHTML =
            '<button class="vimp-btn vimp-btn--ghost" type="button" data-cim-close>Cancel</button>' +
            '<button class="vimp-btn vimp-btn--place" type="button" data-cim-addfolder hidden></button>';
        m.addEventListener('click', onBrowseClick);
        m.querySelector('[data-cim-filter]').addEventListener('input', function (e) {
            browseState.q = e.target.value;
            renderBrowse();
            if (browseState.data && browseState.data.truncated) {
                clearTimeout(browseState.timer);
                browseState.timer = setTimeout(function () { list(browseState.path); }, 350);
            }
        });
        document.addEventListener('keydown', onBrowseKey, true);
        list('');
    }
    function closeBrowse() {
        if (!browseState) return;
        browseState.m.remove();
        browseState = null;
        document.removeEventListener('keydown', onBrowseKey, true);
    }
    function onBrowseKey(e) {
        if (e.key !== 'Escape' || !browseState) return;
        e.stopPropagation();
        var f = browseState.m.querySelector('[data-cim-filter]');
        if (f.value) { f.value = ''; browseState.q = ''; renderBrowse(); }   // Esc clears the filter first
        else closeBrowse();
    }
    function list(path) {
        var s = browseState;
        var q = s.q && s.data && s.data.truncated ? '&q=' + encodeURIComponent(s.q) : '';
        fetch(API + 'browse?t=' + Date.now() + '&path=' + encodeURIComponent(path || '') + q,
              { headers: { Accept: 'application/json' } })
            .then(function (r) { return r.json(); })
            .then(function (d) {
                if (browseState !== s) return;
                if (!d.success && d.error && !d.dirs) { toast(d.error, 'error'); return; }
                if (d.path !== s.path) { s.q = ''; s.m.querySelector('[data-cim-filter]').value = ''; }
                s.path = d.path;
                s.data = d;
                renderBrowse();
            })
            .catch(function () { toast('Couldn’t read that folder', 'error'); });
    }
    function renderBrowse() {
        var s = browseState, d = s.data;
        if (!d) return;
        s.m.querySelector('[data-cim-sub]').textContent = d.path || '';
        s.m.querySelector('[data-cim-shortcuts]').innerHTML = (d.shortcuts || []).map(function (sc) {
            return '<button class="vimp-kindtab' + (sc.path === d.path ? ' vimp-kindtab--on' : '') +
                '" type="button" data-cim-go="' + esc(sc.path) + '">' + esc(sc.label) + '</button>';
        }).join('');
        var terms = String(s.q || '').toLowerCase().split(/\s+/).filter(Boolean);
        var ok = function (n) { var l = n.toLowerCase(); return terms.every(function (t) { return l.indexOf(t) !== -1; }); };
        var dirs = (d.dirs || []).filter(function (x) { return ok(x.name); });
        var files = (d.files || []).filter(function (x) { return ok(x.name); });
        var row = function (attr, val, icon, name, meta) {
            return '<button class="vimp-res" type="button" ' + attr + '="' + esc(val) + '">' +
                '<div class="vimp-res-ph">' + icon + '</div><span class="vimp-res-info">' +
                '<span class="vimp-res-title">' + esc(name) + '</span>' +
                (meta ? '<span class="vimp-res-meta">' + esc(meta) + '</span>' : '') + '</span></button>';
        };
        var html = d.parent ? row('data-cim-go', d.parent, '↑', 'Up a folder', '') : '';
        html += dirs.map(function (x) { return row('data-cim-go', x.path, '📁', x.name, ''); }).join('');
        html += files.map(function (x) { return row('data-cim-add', x.path, '🎞', x.name, fmtSize(x.size)); }).join('');
        if (!dirs.length && !files.length) {
            html += '<div class="vimp-res-note">' + (terms.length ? 'Nothing here matches “' + esc(s.q) + '”.'
                                                               : 'No folders or video files here.') + '</div>';
        } else if (terms.length) {
            html += '<div class="vimp-res-note">' + (dirs.length + files.length) + ' of ' + d.total + '</div>';
        }
        s.m.querySelector('[data-cim-list]').innerHTML = html;
        var addFolder = s.m.querySelector('[data-cim-addfolder]');
        addFolder.hidden = !d.pack;
        if (d.pack) addFolder.textContent = 'Add this folder (' + d.pack.count + ' episodes)';
    }
    function add(path) {
        fetch(API + 'add', { method: 'POST', headers: { 'Content-Type': 'application/json' },
                             body: JSON.stringify({ path: path }) })
            .then(function (r) { return r.json(); })
            .then(function (d) {
                if (!d || !d.success) { toast((d && d.error) || 'Couldn’t add it', 'error'); return; }
                toast(d.already ? 'Already in the list' : 'Added — place it from the list', 'success');
                closeBrowse();
                refreshQueue();
            })
            .catch(function () { toast('Couldn’t add it', 'error'); });
    }
    function onBrowseClick(e) {
        if (e.target.closest('[data-cim-close]')) { closeBrowse(); return; }
        var go = e.target.closest('[data-cim-go]');
        if (go) { list(go.getAttribute('data-cim-go')); return; }
        var a = e.target.closest('[data-cim-add]');
        if (a) { add(a.getAttribute('data-cim-add')); return; }
        if (e.target.closest('[data-cim-addfolder]')) add(browseState.path);
    }

    // ── placing a folder ─────────────────────────────────────────────────────
    function openPlace(item) {
        var m = modal('data-cim-place', 'Place this folder', item.file);
        placeState = { m: m, item: item, picked: null, results: [], season: item.season == null ? '' : String(item.season),
                       timer: null, ptimer: null, asked: 0, busy: false };
        m.querySelector('[data-cim-body]').innerHTML =
            searchBox('Search your library & TMDB for the show…', 'data-cim-q') +
            '<div class="vimp-results" data-cim-results></div>' +
            '<div class="vimp-ep"><label class="vimp-ep-field">Import as season ' +
            '<input type="number" min="0" data-cim-season placeholder="each file’s own"></label></div>' +
            '<div class="vimp-results" data-cim-preview style="max-height:30vh;overflow:auto"></div>';
        m.querySelector('[data-cim-foot]').innerHTML =
            '<button class="vimp-btn vimp-btn--ghost" type="button" data-cim-close>Cancel</button>' +
            '<button class="vimp-btn vimp-btn--place" type="button" data-cim-confirm disabled>Place folder</button>';
        var q = m.querySelector('[data-cim-q]');
        q.value = String(item.title || base(item.file)).replace(/[._]/g, ' ')
            .replace(/\b(S\d+|Season \d+|\d{3,4}p|BD|WEB.*)$/i, '').trim();
        m.querySelector('[data-cim-season]').value = placeState.season;
        m.addEventListener('click', onPlaceClick);
        m.addEventListener('input', onPlaceInput);
        document.addEventListener('keydown', onPlaceKey, true);
        search();
        preview();
        q.focus();
    }
    function closePlace() {
        if (!placeState) return;
        placeState.m.remove();
        placeState = null;
        document.removeEventListener('keydown', onPlaceKey, true);
    }
    function onPlaceKey(e) {
        if (e.key === 'Escape' && placeState && !placeState.busy) { e.stopPropagation(); closePlace(); }
    }
    function search() {
        var s = placeState;
        var q = s.m.querySelector('[data-cim-q]').value.trim();
        var box = s.m.querySelector('[data-cim-results]');
        if (!q) { box.innerHTML = ''; return; }
        box.innerHTML = '<div class="vimp-res-note">Searching…</div>';
        fetch('/api/video/search?q=' + encodeURIComponent(q), { headers: { Accept: 'application/json' } })
            .then(function (r) { return r.ok ? r.json() : null; })
            .then(function (d) {
                if (placeState !== s) return;
                s.results = ((d && d.results) || []).filter(function (it) {
                    var k = String(it.kind || it.media_type || '').toLowerCase();
                    return k === 'show' || k === 'tv';
                }).map(function (it) {
                    return { media_id: it.tmdb_id != null ? it.tmdb_id : it.id, title: it.title || it.name,
                             year: String(it.year || it.first_air_date || '').slice(0, 4) || null,
                             poster: it.poster_url || '', owned: it.library_id != null };
                }).sort(function (a, b) { return (b.owned ? 1 : 0) - (a.owned ? 1 : 0); });
                renderResults();
            })
            .catch(function () { if (placeState === s) box.innerHTML = ''; });
    }
    function renderResults() {
        var s = placeState;
        var box = s.m.querySelector('[data-cim-results]');
        if (!s.results.length) { box.innerHTML = '<div class="vimp-res-note">No shows match — try a different search.</div>'; return; }
        box.innerHTML = s.results.map(function (it, i) {
            var on = s.picked && String(s.picked.media_id) === String(it.media_id);
            var meta = [it.year, it.owned ? 'In library' : null].filter(Boolean).join(' · ');
            var art = it.poster ? '<img class="vimp-res-img" src="' + esc(it.poster) + '" alt="" loading="lazy">'
                                : '<div class="vimp-res-ph">📺</div>';
            return '<button class="vimp-res' + (on ? ' vimp-res--on' : '') + (it.owned ? ' vimp-res--owned' : '') +
                '" type="button" data-cim-pick="' + i + '">' + art + '<span class="vimp-res-info">' +
                '<span class="vimp-res-title">' + esc(it.title) + '</span>' +
                (meta ? '<span class="vimp-res-meta">' + esc(meta) + '</span>' : '') + '</span>' +
                '<span class="vimp-res-check">✓</span></button>';
        }).join('');
    }
    function preview() {
        var s = placeState;
        var mine = ++s.asked;
        var season = s.m.querySelector('[data-cim-season]').value.trim();
        fetch(API + 'pack-preview?t=' + Date.now() + '&path=' + encodeURIComponent(s.item.file) +
              '&season=' + encodeURIComponent(season) +
              (s.picked ? '&media_id=' + encodeURIComponent(s.picked.media_id) : ''),
              { headers: { Accept: 'application/json' } })
            .then(function (r) { return r.json(); })
            .then(function (d) {
                if (placeState !== s || mine !== s.asked) return;
                s.preview = d;
                var box = s.m.querySelector('[data-cim-preview]');
                if (!d || !d.success) { box.innerHTML = '<div class="vimp-res-note">' + esc((d && d.error) || 'Couldn’t read the folder.') + '</div>'; return; }
                var rows = (d.items || []).map(function (it) {
                    var from = it.season == null ? '#' + pad(it.absolute) : 'S' + pad(it.season) + 'E' + pad(it.episode) +
                        (it.episode_end ? '-E' + pad(it.episode_end) : '');
                    var to = it.to_season == null ? '' : 'S' + pad(it.to_season) + 'E' + pad(it.to_episode) +
                        (it.episode_end ? '-E' + pad(it.episode_end) : '');   // the span is kept
                    var moved = to && from.indexOf(to) !== 0;
                    return '<div class="vimp-res-note" style="text-align:left">' + esc(from) +
                        (moved ? ' → <strong>' + esc(to) + '</strong>' : '') + ' &nbsp;' +
                        '<span style="opacity:.6">' + esc(it.name) + '</span></div>';
                }).join('');
                var head = d.count + ' episode' + (d.count === 1 ? '' : 's') +
                    (d.absolute ? ' (' + d.absolute + ' by absolute number: placed by the show’s episode list, '
                        + 'else the season above, else season 1)' : '');
                box.innerHTML = '<div class="vimp-res-note">' + esc(head) + '</div>' + rows +
                    (d.truncated ? '<div class="vimp-res-note">…and more</div>' : '') +
                    (d.error ? '<div class="vimp-res-note" style="color:#ff8484">' + esc(d.error) + '</div>' : '');
                updateConfirm();
            })
            .catch(function () { /* the preview keeps its last state */ });
    }
    function updateConfirm() {
        var s = placeState;
        var btn = s.m.querySelector('[data-cim-confirm]');
        btn.disabled = s.busy || !s.picked || !s.preview || !!s.preview.error || !s.preview.count;
    }
    function placeNow() {
        var s = placeState;
        if (!s || !s.picked) return;
        s.busy = true;
        updateConfirm();
        var btn = s.m.querySelector('[data-cim-confirm]');
        btn.textContent = 'Placing…';
        var season = s.m.querySelector('[data-cim-season]').value.trim();
        var body = { media_id: s.picked.media_id, title: s.picked.title,
                     year: s.picked.year ? parseInt(s.picked.year, 10) : null,
                     season: season === '' ? null : parseInt(season, 10) };
        var finish = function (d) {
            if (placeState !== s) return;
            if (d && d.running) { setTimeout(poll, 2000); return; }
            s.busy = false;
            btn.textContent = 'Place folder';
            if (d && d.success) {
                var failed = (d.failed || []).length;
                toast('Placed ' + (d.imported || 0) + ' of ' + (d.total || 0) + ' into “' + s.picked.title + '”' +
                      (failed ? ' — ' + failed + ' not placed' : ''), failed ? 'warning' : 'success');
                closePlace();
                refreshQueue();
            } else {
                toast((d && d.error) || 'Couldn’t place the folder', 'error');
                updateConfirm();
            }
        };
        // after a failed request, the status says whether it actually landed
        var poll = function () {
            fetch(API + s.item.id + '/place/status?t=' + Date.now(), { headers: { Accept: 'application/json' } })
                .then(function (r) { return r.json(); }).then(finish)
                .catch(function () { setTimeout(poll, 4000); });
        };
        fetch(API + s.item.id + '/place-folder', { method: 'POST',
              headers: { 'Content-Type': 'application/json', Accept: 'application/json' },
              body: JSON.stringify(body) })
            .then(function (r) { return r.json(); }).then(finish)
            .catch(poll);
    }
    function onPlaceClick(e) {
        var s = placeState;
        if (e.target.closest('[data-cim-close]')) { if (!s.busy) closePlace(); return; }
        if (e.target.closest('[data-cim-confirm]')) { placeNow(); return; }
        var pk = e.target.closest('[data-cim-pick]');
        if (pk) {
            s.picked = s.results[parseInt(pk.getAttribute('data-cim-pick'), 10)] || null;
            renderResults();
            preview();
        }
    }
    function onPlaceInput(e) {
        var s = placeState;
        if (e.target.matches('[data-cim-q]')) { clearTimeout(s.timer); s.timer = setTimeout(search, 300); }
        if (e.target.matches('[data-cim-season]')) { clearTimeout(s.ptimer); s.ptimer = setTimeout(preview, 250); }
    }

    // A folder row's Place opens this dialog instead of upstream's file dialog.
    // Capture phase on the document runs before upstream's listener on the grid.
    document.addEventListener('click', function (e) {
        var p = e.target.closest('[data-vimp-place]');
        if (!p || !p.closest('[data-vimp-grid]')) return;
        var id = p.getAttribute('data-vimp-place');
        var card = p.closest('[data-vimp-card]');
        if (!card || card.getAttribute('data-commissary-folder') !== '1') return;
        e.stopPropagation();
        e.preventDefault();
        fetch(API + 'failed?t=' + Date.now(), { headers: { Accept: 'application/json' } })
            .then(function (r) { return r.json(); })
            .then(function (d) {
                var it = ((d && d.items) || []).filter(function (x) { return String(x.id) === String(id); })[0];
                if (it) openPlace(it);
            });
    }, true);

    // Mark folder rows (scope 'season') so the click above can tell them apart,
    // and add the browser button beside Refresh.
    var folders = {};
    function markFolders() {
        document.querySelectorAll('[data-vimp-card]').forEach(function (c) {
            var on = folders[c.getAttribute('data-vimp-card')] ? '1' : '0';
            if (c.getAttribute('data-commissary-folder') !== on) c.setAttribute('data-commissary-folder', on);
        });
    }
    function loadFolders() {
        fetch(API + 'failed?t=' + Date.now(), { headers: { Accept: 'application/json' } })
            .then(function (r) { return r.ok ? r.json() : null; })
            .then(function (d) {
                folders = {};
                ((d && d.items) || []).forEach(function (x) { if (x.scope === 'season') folders[String(x.id)] = 1; });
                markFolders();
            })
            .catch(function () { /* upstream's own behaviour stays */ });
    }
    function attach() {
        var refresh = document.querySelector('[data-vimp-refresh]');
        if (!refresh) return false;
        if (!document.querySelector('[data-cim-open]')) {
            var b = document.createElement('button');
            b.className = 'vimp-refresh';
            b.type = 'button';
            b.setAttribute('data-cim-open', '');
            b.textContent = 'Add file or folder…';
            b.style.marginRight = '8px';
            refresh.parentNode.insertBefore(b, refresh);
            b.addEventListener('click', openBrowse);
            refresh.addEventListener('click', function () { setTimeout(loadFolders, 600); });
        }
        var grid = document.querySelector('[data-vimp-grid]');
        if (grid && !grid._commissaryImport) {
            grid._commissaryImport = true;
            var pending = false;
            new MutationObserver(function () {
                if (pending) return;
                pending = true;
                setTimeout(function () { pending = false; loadFolders(); }, 300);
            }).observe(grid, { childList: true });
        }
        return true;
    }
    function start() { if (!attach()) setTimeout(start, 1000); }

    window.CommissaryImport = { openBrowse: openBrowse };
    if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', start);
    else start();
})();
