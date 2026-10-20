/**
 * Commissary: video Libraries editor (Settings → video server section).
 *
 * Several libraries per kind (Movies, Anime-Movies, TV Shows, Anime, ...), each
 * a Plex/Jellyfin section plus the folder new grabs for it are filed into, its
 * download-client category, the Prowlarr trackers its searches use, and default
 * quality profile / series type. The first library of a kind is its primary:
 * titles that belong to no library are filed there.
 *
 * Renders into [data-commissary-video-libraries]. Once libraries exist, they
 * decide what is scanned, so upstream's single Movies/TV selectors are hidden.
 * Loads the first time the container is on screen (the settings page is
 * admin-only; the endpoints are too).
 */
(function () {
    'use strict';
    var API = '/api/video/libraries/registry';
    var KINDS = [['movies', 'Movie libraries'], ['tv', 'TV libraries']];
    var state = null;

    function el(tag, attrs, children) {
        var n = document.createElement(tag);
        Object.keys(attrs || {}).forEach(function (k) {
            if (attrs[k] == null) return;
            if (k === 'text') n.textContent = attrs[k];
            else if (k === 'className') n.className = attrs[k];
            else if (k.indexOf('on') === 0) n.addEventListener(k.slice(2), attrs[k]);
            else n.setAttribute(k, attrs[k]);
        });
        (children || []).forEach(function (c) { if (c) n.appendChild(c); });
        return n;
    }

    function root() { return document.querySelector('[data-commissary-video-libraries]'); }

    function setStatus(text) {
        var s = document.querySelector('[data-commissary-video-libraries-status]');
        if (s) s.textContent = text || '';
    }

    function toggleUpstreamSelectors() {
        var hasLibs = !!state && KINDS.some(function (k) { return (state.libraries[k[0]] || []).length; });
        document.querySelectorAll('.video-library-selectors').forEach(function (n) {
            n.style.display = hasLibs ? 'none' : '';
        });
    }

    function input(value, placeholder, onInput) {
        return el('input', { type: 'text', value: value == null ? '' : String(value),
                             placeholder: placeholder || '', oninput: onInput });
    }

    function select(options, value, onChange) {
        var s = el('select', { className: 'form-select', onchange: onChange });
        options.forEach(function (o) {
            var opt = el('option', { value: o[0], text: o[1] });
            if (String(o[0]) === String(value == null ? '' : value)) opt.selected = true;
            s.appendChild(opt);
        });
        return s;
    }

    // One settings field the way upstream builds them: .form-group, its label
    // above the control.
    function field(label, control) {
        return el('div', { className: 'form-group' },
                  [el('label', { text: label }), control]);
    }

    function row(kind, lib, index, list) {
        var sections = (state.available[kind] || []).slice();
        if (lib.server_title && sections.indexOf(lib.server_title) === -1) sections.unshift(lib.server_title);
        var fields = [
            field('Server section', select(
                [['', 'Pick a server section']].concat(sections.map(function (t) { return [t, t]; })),
                lib.server_title, function (e) { lib.server_title = e.target.value; })),
            field('Name', input(lib.label, lib.server_title || 'Shown in the app',
                                function (e) { lib.label = e.target.value; })),
            field('Folder', input(lib.path, kind === 'tv' ? '/media/tv' : '/media/movies',
                                  function (e) { lib.path = e.target.value; })),
            field('Download client category', input(lib.category, 'Blank uses the global category',
                                                    function (e) { lib.category = e.target.value; })),
            field('Trackers (Prowlarr indexer ids)', input(lib.preferred_indexer_ids, 'Blank searches all, e.g. 1,7,9',
                  function (e) { lib.preferred_indexer_ids = e.target.value; })),
            field('Default quality profile', select(
                [['', 'Global default']].concat((state.profiles || []).filter(function (p) {
                    return p.id; }).map(function (p) { return [p.id, p.name]; })),
                lib.default_quality_profile_id,
                function (e) { lib.default_quality_profile_id = e.target.value || null; }))
        ];
        if (kind === 'tv') {
            fields.push(field('Default series type', select(
                [['', 'Not set']].concat(state.series_types.map(function (t) {
                    return [t, t.charAt(0).toUpperCase() + t.slice(1)]; })),
                lib.default_series_type,
                function (e) { lib.default_series_type = e.target.value || null; })));
        }
        var move = function (delta) {
            return function () {
                var to = index + delta;
                if (to < 0 || to >= list.length) return;
                list.splice(to, 0, list.splice(index, 1)[0]);
                render();
            };
        };
        var title = (lib.label || lib.server_title || 'New library') + (index === 0 ? ' — primary' : '');
        var head = el('div', { className: 'commissary-vlib-head' }, [
            el('span', { className: 'commissary-vlib-title', text: title }),
            el('div', { className: 'form-actions commissary-vlib-actions' }, [
                el('button', { type: 'button', className: 'test-button', text: 'Move up',
                               onclick: move(-1), disabled: index === 0 ? '' : null }),
                el('button', { type: 'button', className: 'test-button', text: 'Move down',
                               onclick: move(1), disabled: index === list.length - 1 ? '' : null }),
                el('button', { type: 'button', className: 'test-button', text: 'Remove',
                               onclick: function () { list.splice(index, 1); render(); } })
            ])
        ]);
        return el('div', { className: 'commissary-vlib-row' },
                  [head, el('div', { className: 'commissary-vlib-grid' }, fields)]);
    }

    function render() {
        var r = root();
        if (!r || !state) return;
        r.textContent = '';
        r.appendChild(el('h4', { className: 'settings-subheading', text: 'Video Libraries' }));
        r.appendChild(el('div', { className: 'setting-help-text', text:
            'Each library is a server section plus the folder its new downloads are filed into. '
            + 'The first library of a kind is its primary: titles that belong to no library go there. '
            + "Trackers limit the searches for a library's titles, and the category sorts its downloads "
            + 'in the torrent or usenet client.' }));
        if (!state.server) {
            r.appendChild(el('div', { className: 'setting-help-text', text:
                'Connect a Plex or Jellyfin server above to set up libraries.' }));
            return;
        }
        KINDS.forEach(function (k) {
            var list = state.libraries[k[0]] = state.libraries[k[0]] || [];
            var box = el('div', { className: 'commissary-vlib-kind' },
                         [el('h4', { className: 'settings-subheading', text: k[1] })]);
            list.forEach(function (lib, i) { box.appendChild(row(k[0], lib, i, list)); });
            box.appendChild(el('div', { className: 'form-actions' }, [
                el('button', { type: 'button', className: 'test-button', text: 'Add library',
                               onclick: function () { list.push({}); render(); } })]));
            r.appendChild(box);
        });
        r.appendChild(el('div', { className: 'form-actions commissary-vlib-save' }, [
            el('button', { type: 'button', className: 'test-button', text: 'Save libraries', onclick: save }),
            el('span', { className: 'settings-hint', 'data-commissary-video-libraries-status': '' })
        ]));
        toggleUpstreamSelectors();
    }

    function load() {
        setStatus('Loading…');
        return fetch(API, { headers: { 'Accept': 'application/json' } })
            .then(function (resp) { return resp.ok ? resp.json() : null; })
            .then(function (d) {
                if (!d) { setStatus('Could not load libraries'); return; }
                state = d;
                state.libraries = state.libraries || { movies: [], tv: [] };
                render();
            })
            .catch(function () { setStatus('Could not load libraries'); });
    }

    function save() {
        var payload = { server: state.server };
        KINDS.forEach(function (k) { payload[k[0]] = state.libraries[k[0]] || []; });
        var missing = KINDS.some(function (k) {
            return (payload[k[0]] || []).some(function (l) { return !l.server_title; });
        });
        if (missing) { setStatus('Pick a server section for every library.'); return; }
        setStatus('Saving…');
        fetch(API, { method: 'POST', headers: { 'Content-Type': 'application/json',
                                                 'Accept': 'application/json' },
                     body: JSON.stringify(payload) })
            .then(function (resp) { return resp.json().then(function (j) { return [resp.ok, j]; }); })
            .then(function (res) {
                if (!res[0]) { setStatus(res[1].error || 'Save failed'); return; }
                state.libraries = res[1].libraries;
                render();
                setStatus('Saved. The next scan reads these sections.');
            })
            .catch(function () { setStatus('Save failed'); });
    }

    // Load the first time the editor is actually on screen. The settings page
    // shows its sections by toggling display, which an IntersectionObserver
    // doesn't reliably report, so check visibility once a second until then.
    function arm() {
        var timer = setInterval(function () {
            var r = root();
            if (!r) { clearInterval(timer); return; }
            if (r.offsetParent !== null) {
                clearInterval(timer);
                load();
            }
        }, 1000);
    }

    if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', arm);
    else arm();

    window._commissaryVideoLibraries = { load: load, state: function () { return state; } };
})();
