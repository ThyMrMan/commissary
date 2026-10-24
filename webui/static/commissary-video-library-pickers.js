/**
 * Commissary: "which video Library does this title go to?" pickers.
 *
 *   Watchlist show cards     a folder button in the card's control row (vwl-btn look)
 *   Manage panel (owned)     a "Library" field under Acquisition (vmg-field)
 *
 * The Wishlist (React since 3.5.9) has its own, in a title's side panel:
 * webui/src/commissary/wishlist-libraries.tsx.
 *
 * Only for admins (the endpoints are admin-only), only for a kind with more
 * than one library, and only for titles not yet owned on the cards: an owned
 * title's library is changed in its Manage panel. Nothing moves on disk; the
 * choice decides where the NEXT grab for the title is filed.
 *
 * Added to upstream's own markup after it renders, keyed off attributes the
 * pages already carry, so upstream's page scripts stay untouched. The menu
 * reuses the Requests page's .vreq-menu styling.
 */
(function () {
    'use strict';
    var API = '/api/video/libraries/';
    var FOLDER = '<svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" ' +
        'stroke-width="2.2" stroke-linejoin="round" aria-hidden="true">' +
        '<path d="M3 7a2 2 0 0 1 2-2h4l2 2h8a2 2 0 0 1 2 2v8a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2z"/></svg>';
    var state = null;          // /assignments payload
    var loading = null;
    var denied = false;
    var menu = null;
    var lastManage = null;     // {kind, id} of the open Manage panel

    function toast(msg, type) { if (typeof showToast === 'function') showToast(msg, type); }

    function load(force) {
        if (denied) return Promise.resolve(null);
        if (loading && !force) return loading;
        // ?t: upstream's fetch-dedupe.js would hand load(true) right after an assign the old answer
        loading = fetch(API + 'assignments?t=' + Date.now(), { headers: { 'Accept': 'application/json' } })
            .then(function (r) {
                if (r.status === 403 || r.status === 401) { denied = true; return null; }
                return r.ok ? r.json() : null;
            })
            .then(function (d) { if (d) state = d; return d; })
            .catch(function () { return null; });
        return loading;
    }

    function choices(kind) { return (state && state.choices && state.choices[kind]) || []; }
    function hasChoice(kind) { return choices(kind).length > 1; }
    function labelOf(kind, id) {
        var c = choices(kind).filter(function (x) { return String(x.id) === String(id); })[0];
        return c ? c.label : null;
    }

    function current(target) {
        if (!state) return null;
        if (target.where === 'watchlist') return state.watchlist[String(target.tmdb)] || null;
        return (state.wishlist[target.kind] || {})[String(target.tmdb)] || null;
    }

    // ── the menu (Requests page styling) ────────────────────────────────────
    function closeMenu() {
        if (menu) { menu.remove(); menu = null; }
        window.removeEventListener('scroll', closeMenu, true);
        document.removeEventListener('keydown', onKey, true);
        document.removeEventListener('mousedown', onOutside, true);
    }
    function onKey(e) { if (e.key === 'Escape') closeMenu(); }
    function onOutside(e) { if (menu && !menu.contains(e.target)) closeMenu(); }

    function openMenu(trigger, target) {
        closeMenu();
        var cur = current(target);
        var primary = choices(target.kind)[0];
        var items = [{ id: '', label: 'Default' + (primary ? ' (' + primary.label + ')' : '') }]
            .concat(choices(target.kind));
        menu = document.createElement('div');
        menu.className = 'vreq-menu';
        menu.setAttribute('role', 'menu');
        items.forEach(function (it) {
            var b = document.createElement('button');
            b.type = 'button';
            b.className = 'vreq-menu-item';
            b.setAttribute('role', 'menuitemradio');
            var on = String(it.id || '') === String(cur || '');
            b.setAttribute('aria-checked', on ? 'true' : 'false');
            b.textContent = (on ? '✓ ' : '') + it.label;
            b.addEventListener('click', function (e) {
                e.preventDefault();
                e.stopPropagation();
                closeMenu();
                assign(target, it.id || null);
            });
            menu.appendChild(b);
        });
        document.body.appendChild(menu);
        var r = trigger.getBoundingClientRect();
        var w = menu.offsetWidth;
        var top = r.bottom + 6;
        if (top + menu.offsetHeight > window.innerHeight - 8) top = Math.max(8, r.top - menu.offsetHeight - 6);
        menu.style.top = top + 'px';
        menu.style.left = Math.max(8, Math.min(window.innerWidth - w - 8, r.right - w)) + 'px';
        window.addEventListener('scroll', closeMenu, true);
        document.addEventListener('keydown', onKey, true);
        document.addEventListener('mousedown', onOutside, true);
        var first = menu.querySelector('[aria-checked="true"]') || menu.firstChild;
        if (first) first.focus();
    }

    function assign(target, rid) {
        var url = API + 'assign/' + target.where;
        var body = target.where === 'watchlist'
            ? { tmdb_id: target.tmdb, root_folder_id: rid }
            : { kind: target.kind, tmdb_id: target.tmdb, root_folder_id: rid };
        fetch(url, { method: 'POST', headers: { 'Content-Type': 'application/json' },
                     body: JSON.stringify(body) })
            .then(function (r) { if (!r.ok) throw new Error(); return load(true); })
            .then(function () {
                var name = rid ? labelOf(target.kind, rid) : null;
                toast(name ? 'New grabs for this title go to ' + name : 'Using the default library',
                      'success');
                decorate();
            })
            .catch(function () { toast('Couldn’t change the library', 'error'); });
    }

    // ── buttons on upstream's cards ──────────────────────────────────────────
    function button(cls, target) {
        var b = document.createElement('button');
        b.type = 'button';
        b.className = cls + ' commissary-vlib-btn';
        b.setAttribute('data-commissary-vlib', target.where + ':' + target.kind + ':' + target.tmdb);
        var name = labelOf(target.kind, current(target));
        var tip = name ? 'Library: ' + name : 'Choose which library this lands in';
        b.title = tip;
        b.setAttribute('aria-label', tip);
        b.innerHTML = FOLDER;
        if (name) b.classList.add('commissary-vlib-btn--set');
        b.addEventListener('click', function (e) {
            e.preventDefault();
            e.stopPropagation();
            openMenu(b, target);
        });
        return b;
    }

    // Leaves an up-to-date button alone: replacing it on every pass would be a
    // DOM change, which wakes the observer, which decorates again, forever.
    function refresh(host, cls, target, first) {
        var cur = String(current(target) || '');
        var old = host.querySelector(':scope > .commissary-vlib-btn');
        if (old && old.getAttribute('data-commissary-vlib-cur') === cur) return;
        if (old) old.remove();
        var b = button(cls, target);
        b.setAttribute('data-commissary-vlib-cur', cur);
        if (first) host.insertBefore(b, host.firstChild);
        else host.appendChild(b);
    }

    function decorate() {
        if (!state) return;
        if (hasChoice('show')) {
            // First in the card's own control row (watch / get), as one of them.
            document.querySelectorAll('a.vwlp-card[data-vwlp-open="show"][data-vwlp-source="tmdb"]')
                .forEach(function (card) {
                    var row = card.querySelector('.vcard-ctrls');
                    // Its own class styled like .vwl-btn, NOT .vwl-btn: upstream catches
                    // every .vwl-btn click page-wide and toggles the watchlist with it.
                    if (row) refresh(row, 'commissary-vlib-wbtn', { where: 'watchlist', kind: 'show',
                                                       tmdb: card.getAttribute('data-vwlp-openid') }, true);
                });
        }
    }

    // ── the Manage panel field (owned titles) ────────────────────────────────
    function decorateManage() {
        var qp = document.querySelector('[data-vmg-quality-profile]');
        if (!qp || !lastManage || document.querySelector('[data-commissary-vmg-library]')) return;
        var kind = lastManage.kind === 'movie' ? 'movie' : 'show';
        var field = qp.closest('.vmg-field');
        if (!field) return;
        load().then(function () {
            if (!hasChoice(kind) || document.querySelector('[data-commissary-vmg-library]')) return;
            fetch(API + 'assignments?kind=' + kind + '&library_id=' + encodeURIComponent(lastManage.id) +
                  '&t=' + Date.now())
                .then(function (r) { return r.ok ? r.json() : null; })
                .then(function (d) {
                    if (!d || document.querySelector('[data-commissary-vmg-library]')) return;
                    var wrap = document.createElement('div');
                    wrap.className = 'vmg-field';
                    var label = document.createElement('label');
                    label.textContent = 'Library';
                    var sel = document.createElement('select');
                    sel.className = 'vmg-input';
                    sel.setAttribute('data-commissary-vmg-library', '');
                    choices(kind).forEach(function (c) {
                        var o = document.createElement('option');
                        o.value = c.id;
                        o.textContent = c.label;
                        if (String(c.id) === String(d.title)) o.selected = true;
                        sel.appendChild(o);
                    });
                    if (d.title == null) {
                        var none = document.createElement('option');
                        none.value = '';
                        none.textContent = 'Not in a library';
                        none.selected = true;
                        sel.insertBefore(none, sel.firstChild);
                    }
                    var hint = document.createElement('div');
                    hint.className = 'vmg-hint';
                    hint.textContent = 'Where new downloads for this title are filed. Files already ' +
                        'on disk stay where they are.';
                    sel.addEventListener('change', function () {
                        if (!sel.value) return;
                        var target = { kind: kind, id: lastManage.id };
                        fetch(API + 'assign/title', {
                            method: 'POST', headers: { 'Content-Type': 'application/json' },
                            body: JSON.stringify({ kind: target.kind, library_id: target.id,
                                                   root_folder_id: parseInt(sel.value, 10) }) })
                            .then(function (r) {
                                if (!r.ok) throw new Error();
                                toast('Library updated — new grabs for this title go there', 'success');
                                load(true);
                            })
                            .catch(function () { toast('Couldn’t update the library', 'error'); });
                    });
                    wrap.appendChild(label);
                    wrap.appendChild(sel);
                    wrap.appendChild(hint);
                    field.parentNode.insertBefore(wrap, field.nextSibling);
                });
        });
    }

    function wrapManage() {
        if (!window.VideoManage || window.VideoManage._commissaryWrapped) return;
        var open = window.VideoManage.open;
        window.VideoManage.open = function (opts) {
            lastManage = opts ? { kind: opts.kind, id: opts.id } : null;
            return open.apply(this, arguments);
        };
        window.VideoManage._commissaryWrapped = true;
    }

    // Re-decorate whenever the pages re-render their grids or the panel opens.
    var pending = false;
    function schedule() {
        if (pending) return;
        pending = true;
        // A timer, not requestAnimationFrame: rAF is paused in background tabs,
        // and a page left there would never get its buttons.
        setTimeout(function () {
            pending = false;
            wrapManage();
            if (document.querySelector('a.vwlp-card') &&
                !document.querySelector('.commissary-vlib-btn')) {
                load().then(decorate);
            } else if (state) {
                decorate();
            }
            decorateManage();
        }, 60);
    }

    function start() {
        wrapManage();
        new MutationObserver(function (records) {
            for (var i = 0; i < records.length; i++) {
                var added = records[i].addedNodes;
                for (var j = 0; j < added.length; j++) {
                    var n = added[j];
                    // our own buttons and menu don't count
                    if (n.nodeType === 1 && (n.classList.contains('commissary-vlib-btn') ||
                                             n.classList.contains('vreq-menu'))) continue;
                    schedule();
                    return;
                }
            }
        }).observe(document.body, { childList: true, subtree: true });
        schedule();
    }

    if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', start);
    else start();

    window._commissaryVideoLibraryPickers = { load: load, decorate: decorate,
                                               state: function () { return state; } };
})();
