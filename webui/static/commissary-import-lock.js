/*
 * Commissary — "Lock automatic imports" (core/commissary/import_lock.py).
 *
 *   Manage panel (shows, movies)  a toggle beside Watched / Monitored
 *   A show's season bar           a button beside Monitored, for that season
 *
 * Locked = no unattended import may place a file there (manual import still
 * can). Built from upstream's own classes (vmg-toggle; the season bar's
 * discog-btn-compact / vd-season-mon). Admin-only: a non-admin's GET is
 * refused and nothing is shown.
 */
(function () {
    'use strict';
    var API = '/api/video/libraries/';
    var current = null;       // {kind, id} of the open Manage panel
    var shows = {};           // show id -> Promise of {locked, seasons}

    function toast(msg, type) { if (typeof showToast === 'function') showToast(msg, type); }
    function esc(s) {
        return String(s == null ? '' : s).replace(/&/g, '&amp;').replace(/</g, '&lt;')
            .replace(/>/g, '&gt;').replace(/"/g, '&quot;');
    }

    function load(kind, id, force) {
        var key = kind + ':' + id;
        if (!force && shows[key]) return shows[key];
        // A unique URL: upstream's fetch-dedupe.js answers an identical /api GET
        // made within 2.5s with the earlier response, so a read straight after a
        // save came back with the state from before it.
        shows[key] = fetch(API + kind + '/' + id + '/import-lock?t=' + Date.now(),
                           { headers: { 'Accept': 'application/json' } })
            .then(function (r) { return r.ok ? r.json() : null; })
            .catch(function () { return null; });
        return shows[key];
    }

    function post(url, locked) {
        return fetch(url, { method: 'POST', headers: { 'Content-Type': 'application/json' },
                            body: JSON.stringify({ locked: locked }) })
            .then(function (r) { if (!r.ok) throw new Error(); return r.json(); });
    }

    // ── the season bar ───────────────────────────────────────────────────────
    function seasonButton(locked) {
        return '<button class="discog-download-btn discog-btn-compact vd-season-mon' +
            (locked ? ' vd-season-mon--on' : '') + '" type="button" data-commissary-season-lock="' +
            (locked ? '0' : '1') + '" title="' +
            esc(locked ? 'Automatic imports into this season are refused. Click to allow them again.'
                       : 'Refuse automatic imports into this season (manual import still works)') + '">' +
            '<span class="discog-btn-icon">' + (locked ? '◉' : '○') + '</span>' +
            '<span class="discog-btn-text">' + (locked ? 'Imports locked' : 'Lock imports') + '</span>' +
            '<span class="discog-btn-shimmer"></span></button>';
    }

    function decorateSeasons() {
        document.querySelectorAll('.vd-season-actions[data-commissary-season]').forEach(function (bar) {
            var tag = bar.getAttribute('data-commissary-season');
            var old = bar.querySelector('[data-commissary-season-lock]');
            if (old && old.getAttribute('data-for') === tag) return;
            var parts = tag.split(':');
            var showId = parts[0], season = parseInt(parts[1], 10);
            var asked = load('show', showId);
            asked.then(function (st) {
                // a slower, older answer must not put back a stale button
                if (shows['show:' + showId] !== asked) return;
                if (!st || bar.getAttribute('data-commissary-season') !== tag) return;
                var locked = st.locked || (st.seasons || []).indexOf(season) !== -1;
                var prev = bar.querySelector('[data-commissary-season-lock]');
                if (prev) prev.remove();
                var holder = document.createElement('div');
                holder.innerHTML = seasonButton(locked);
                var btn = holder.firstChild;
                btn.setAttribute('data-for', tag);
                if (st.locked) {
                    btn.disabled = true;
                    btn.title = 'The whole show is locked (Manage → Lock automatic imports)';
                }
                var mon = bar.querySelector('[data-vd-season-monitor]');
                if (mon) mon.parentNode.insertBefore(btn, mon.nextSibling);
                else bar.appendChild(btn);
                btn.addEventListener('click', function () {
                    var want = btn.getAttribute('data-commissary-season-lock') === '1';
                    btn.disabled = true;
                    post(API + 'show/' + showId + '/season/' + season + '/import-lock', want)
                        .then(function () {
                            toast(want ? 'Season ' + season + ' locked — automatic imports are refused'
                                       : 'Season ' + season + ' unlocked', 'success');
                            load('show', showId, true).then(function () { btn.remove(); decorateSeasons(); });
                        })
                        .catch(function () { btn.disabled = false; toast('Couldn’t change the lock', 'error'); });
                });
            });
        });
    }

    // ── the Manage panel toggle ──────────────────────────────────────────────
    function decorateManage() {
        if (!current || (current.kind !== 'show' && current.kind !== 'movie')) return;
        var row = document.querySelector('.vmg-overlay .vmg-toggles');
        if (!row || row.querySelector('[data-commissary-import-lock]')) return;
        var target = current;
        load(target.kind, target.id, true).then(function (st) {
            decorateSeasons();      // this fresher answer supersedes any the bar was waiting on
            if (!st || current !== target || row.querySelector('[data-commissary-import-lock]')) return;
            var el = document.createElement('div');
            el.className = 'vmg-toggle' + (st.locked ? ' vmg-toggle--on' : '');
            el.setAttribute('data-commissary-import-lock', '');
            el.setAttribute('role', 'switch');
            el.setAttribute('tabindex', '0');
            el.setAttribute('aria-checked', st.locked ? 'true' : 'false');
            el.title = 'Refuse every automatic import into this ' + target.kind +
                ' (manual import still works)';
            el.innerHTML = '<span>Lock automatic imports</span><span class="vmg-sw"></span>';
            var flip = function () {
                var on = !el.classList.contains('vmg-toggle--on');
                el.classList.toggle('vmg-toggle--on', on);
                el.setAttribute('aria-checked', on ? 'true' : 'false');
                post(API + target.kind + '/' + target.id + '/import-lock', on)
                    .then(function () {
                        toast(on ? 'Locked — automatic imports are refused' : 'Unlocked', 'success');
                        load(target.kind, target.id, true).then(function () {
                            document.querySelectorAll('[data-commissary-season-lock]').forEach(function (b) { b.remove(); });
                            decorateSeasons();
                        });
                    })
                    .catch(function () {
                        el.classList.toggle('vmg-toggle--on', !on);
                        el.setAttribute('aria-checked', !on ? 'true' : 'false');
                        toast('Couldn’t change the lock', 'error');
                    });
            };
            el.addEventListener('click', flip);
            el.addEventListener('keydown', function (e) {
                if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); e.stopPropagation(); flip(); }
            });
            row.appendChild(el);
        });
    }

    function wrapManage() {
        if (!window.VideoManage) return false;
        if (window.VideoManage._commissaryImportLock) return true;
        var open = window.VideoManage.open;
        window.VideoManage.open = function (opts) {
            current = opts ? { kind: opts.kind, id: opts.id } : null;
            return open.apply(this, arguments);
        };
        window.VideoManage._commissaryImportLock = true;
        return true;
    }

    // Re-decorate when pages re-render; a timer, not rAF (a hidden tab pauses rAF).
    var pending = false;
    function schedule() {
        if (pending) return;
        pending = true;
        setTimeout(function () {
            pending = false;
            decorateSeasons();
            decorateManage();
        }, 50);
    }

    function start() {
        if (!wrapManage()) { setTimeout(start, 1000); return; }
        new MutationObserver(function (records) {
            for (var i = 0; i < records.length; i++) {
                var t = records[i].target;
                // our own buttons appearing must not re-trigger the pass
                if (t && t.closest && t.closest('[data-commissary-season-lock],[data-commissary-import-lock]')) continue;
                schedule();
                return;
            }
        }).observe(document.body, { childList: true, subtree: true });
        schedule();
    }

    window.CommissaryImportLock = { seasonButton: seasonButton };
    if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', start);
    else start();
})();
