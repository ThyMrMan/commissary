/*
 * Commissary — video Library tabs on the Library page and the Wishlist.
 *
 * A kind with two or more video Libraries (Movies + Anime-Movies, TV + Anime)
 * gets a second row under that page's own kind tabs: "All Movies", then each
 * Library with its count (titles on the Library page, wished titles on the
 * Wishlist - shows counted as shows, like the TV tab). Picking one narrows the
 * list (?library=<id>, added through one hook in each page's load()). A kind
 * with one Library shows nothing new; YouTube has no Libraries.
 *
 * Each page's own tab classes, but never its data-*-tab attribute, so the
 * page's tab handlers ignore these buttons.
 */
(function () {
    'use strict';
    var SURFACES = {
        library: { kinds: '.video-library-tabs', attr: 'data-video-lib-tab', on: 'active',
                   btn: 'video-lib-tab', badge: null, scope: '',
                   map: { movies: 'movie', shows: 'show' }, all: { movies: 'All Movies', shows: 'All Shows' } },
        wishlist: { kinds: '.vwsh-tabs', attr: 'data-vwsh-tab', on: 'vwsh-tab--on',
                    btn: 'vwsh-tab', badge: 'vwsh-tab-n', scope: 'wishlist',
                    map: { movie: 'movie', show: 'show' }, all: { movie: 'All Movies', show: 'All TV' } },
    };
    var state = {};   // surface -> {tabs, picked: {tab: id}, current}

    function esc(s) {
        return String(s == null ? '' : s).replace(/&/g, '&amp;').replace(/</g, '&lt;')
            .replace(/>/g, '&gt;').replace(/"/g, '&quot;');
    }
    function st(name) {
        return state[name] || (state[name] = { tabs: null, picked: {}, current: null, loading: false });
    }

    // called from each page's load() with its query params and current tab
    function apply(name, params, tab) {
        var s = st(name), cfg = SURFACES[name];
        s.current = tab;
        var id = s.picked[tab];
        if (id != null && cfg.map[tab]) params.set('library', String(id));
        // the Wishlist's counts change as titles are wished; refresh with each load
        if (name === 'wishlist') fetchTabs(name);
        else render(name);
    }

    function render(name) {
        var cfg = SURFACES[name], s = st(name);
        var kinds = document.querySelector(cfg.kinds);
        if (!kinds || !s.tabs) return;
        if (!s.current) {
            var a = document.querySelector('[' + cfg.attr + '].' + cfg.on);
            s.current = a ? a.getAttribute(cfg.attr) : null;
        }
        var row = document.querySelector('[data-commissary-lib-row="' + name + '"]');
        var list = s.tabs[cfg.map[s.current]] || [];
        if (!list.length) { if (row) row.remove(); return; }
        if (!row) {
            row = document.createElement('div');
            row.className = kinds.className;
            row.setAttribute('data-commissary-lib-row', name);
            row.style.marginTop = '6px';
            kinds.parentNode.insertBefore(row, kinds.nextSibling);
        }
        var cur = s.picked[s.current];
        var count = function (n) {
            return cfg.badge ? ' <span class="' + cfg.badge + '">' + esc(n) + '</span>'
                             : ' <span style="opacity:.55">' + esc(n) + '</span>';
        };
        row.innerHTML = '<button class="' + cfg.btn + (cur == null ? ' ' + cfg.on : '') +
            '" type="button" data-commissary-lib="" data-commissary-surface="' + name + '">' +
            esc(cfg.all[s.current] || 'All') + '</button>' +
            list.map(function (l) {
                return '<button class="' + cfg.btn + (String(cur) === String(l.id) ? ' ' + cfg.on : '') +
                    '" type="button" data-commissary-lib="' + esc(l.id) + '" data-commissary-surface="' +
                    name + '">' + esc(l.label) + count(l.count) + '</button>';
            }).join('');
    }

    function fetchTabs(name) {
        var s = st(name);
        if (s.loading) return;
        s.loading = true;
        var scope = SURFACES[name].scope;
        fetch('/api/video/commissary/library-tabs?t=' + Date.now() + (scope ? '&scope=' + scope : ''),
              { headers: { 'Accept': 'application/json' } })
            .then(function (r) { return r.ok ? r.json() : null; })
            .then(function (d) { s.loading = false; if (d) { s.tabs = d; render(name); } })
            .catch(function () { s.loading = false; });
    }

    function reloadPage(name) {
        // each page reloads on a click of its active kind tab
        var cfg = SURFACES[name];
        var active = document.querySelector('[' + cfg.attr + '].' + cfg.on);
        if (active) active.click();
    }

    document.addEventListener('click', function (e) {
        var lib = e.target.closest('[data-commissary-lib]');
        if (lib) {
            var name = lib.getAttribute('data-commissary-surface');
            var s = st(name), v = lib.getAttribute('data-commissary-lib');
            s.picked[s.current] = v === '' ? null : v;
            render(name);
            reloadPage(name);
            return;
        }
        Object.keys(SURFACES).forEach(function (name) {
            var kind = e.target.closest('[' + SURFACES[name].attr + ']');
            if (kind) {
                st(name).current = kind.getAttribute(SURFACES[name].attr);
                setTimeout(function () { render(name); }, 0);
            }
        });
    });

    function start() {
        Object.keys(SURFACES).forEach(fetchTabs);
    }

    window.CommissaryLibraryTabs = {
        apply: function (params, tab) { apply('library', params, tab); },
        applyWishlist: function (params, tab) { apply('wishlist', params, tab); },
    };
    if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', start);
    else start();
})();
