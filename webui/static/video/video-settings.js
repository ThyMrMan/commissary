/*
 * Commissary — Video settings additions (isolated).
 *
 * The video side shows the real music settings page; this module only drives the
 * VIDEO-specific bits added to it — for now, the Movies/TV library mapping
 * (which server library the scan reads). It populates the dropdowns from
 * /api/video/libraries when the Settings page is shown on the video side and
 * saves the choice back. Self-contained IIFE, no globals, no inline handlers.
 */
(function () {
    'use strict';

    var PAGE_ID = 'video-settings';
    var URL = '/api/video/libraries';
    var CONFIG_URL = '/api/video/enrichment/config';
    var SERVER_URL = '/api/video/server';
    var CONN_URL = '/api/video/server-config';
    var DOWNLOADS_URL = '/api/video/downloads/config';
    var QUALITY_URL = '/api/video/downloads/quality';
    var YT_QUALITY_URL = '/api/video/downloads/youtube-quality';
    var SLSKD_URL = '/api/video/downloads/slskd';
    var _videoQuality = null;
    var _videoYtQuality = null;
    // Pretty labels for the source×resolution quality ladder (keys come from the backend).
    var TIER_LABEL = {
        'remux-2160p': 'Remux · 4K', 'bluray-2160p': 'BluRay · 4K', 'web-2160p': 'WEB · 4K',
        'remux-1080p': 'Remux · 1080p', 'bluray-1080p': 'BluRay · 1080p', 'web-1080p': 'WEB-DL · 1080p',
        'webrip-1080p': 'WEBRip · 1080p', 'hdtv-1080p': 'HDTV · 1080p',
        'bluray-720p': 'BluRay · 720p', 'web-720p': 'WEB-DL · 720p', 'hdtv-720p': 'HDTV · 720p',
        'dvd': 'DVD', 'sdtv': 'SDTV'
    };
    var REJECT_LABEL = {
        'cam': 'CAM / TS', 'screener': 'Screener', 'workprint': 'Workprint', '3d': '3D', 'x264': 'x264 / AVC'
    };
    var REJECT_ORDER = ['cam', 'screener', 'workprint', '3d', 'x264'];

    function esc(s) {
        return String(s == null ? '' : s).replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
    }

    // ── Server Connection ───────────────────────────────────────────────────
    // Mirrors the MUSIC server picker (toggle = select + configure), scoped to
    // Plex/Jellyfin. Clicking a toggle reveals that server's creds AND sets it as
    // the active video server. Creds are video's own (video.db), pre-filled from
    // music; the picker writes only to /api/video/* — never the music config.
    function connEl(name) { return document.querySelector('[data-video-conn="' + name + '"]'); }
    function note(server, text) {
        var n = document.querySelector('[data-video-conn-note="' + server + '"]');
        if (n) n.textContent = text || '';
    }
    function showServerConfig(server) {
        var btns = document.querySelectorAll('[data-video-server-toggle]');
        for (var i = 0; i < btns.length; i++) {
            btns[i].classList.toggle('active', btns[i].getAttribute('data-video-server-toggle') === server);
        }
        var cfgs = document.querySelectorAll('[data-video-server-config]');
        for (var j = 0; j < cfgs.length; j++) {
            cfgs[j].classList.toggle('hidden', cfgs[j].getAttribute('data-video-server-config') !== server);
        }
    }
    // Which server's config to show on load: the explicit pick, else the
    // configured one, else Plex (so there's always a panel to fill in).
    function loadServer() {
        fetch(SERVER_URL, { headers: { 'Accept': 'application/json' } })
            .then(function (r) { return r.ok ? r.json() : null; })
            .then(function (d) {
                d = d || {};
                var active = d.server || (d.jellyfin && !d.plex ? 'jellyfin' : 'plex');
                showServerConfig(active);
                if (active === 'jellyfin') loadJellyfinUsers();
            })
            .catch(function () { showServerConfig('plex'); });
    }
    function loadConn() {
        fetch(CONN_URL, { headers: { 'Accept': 'application/json' } })
            .then(function (r) { return r.ok ? r.json() : null; })
            .then(function (d) {
                if (!d) return;
                var p = d.plex || {}, j = d.jellyfin || {};
                var pu = connEl('plex-url'); if (pu) pu.value = p.base_url || '';
                var pt = connEl('plex-token'); if (pt) pt.value = p.has_token ? p.token : '';
                var ju = connEl('jellyfin-url'); if (ju) ju.value = j.base_url || '';
                var jk = connEl('jellyfin-key'); if (jk) jk.value = j.has_key ? j.api_key : '';
                note('plex', p.base_url
                    ? (p.inherited ? 'Inherited from your Music Plex connection — edit to use a different server for video.'
                                   : 'Custom video connection.')
                    : 'Not connected — add a server URL and token.');
                note('jellyfin', j.base_url
                    ? (j.inherited ? 'Inherited from your Music Jellyfin connection — edit to use a different server for video.'
                                   : 'Custom video connection.')
                    : 'Not connected — add a server URL and API key.');
            })
            .catch(function () { /* ignore */ });
    }
    function saveConn(silent) {
        var pu = connEl('plex-url'), pt = connEl('plex-token');
        var ju = connEl('jellyfin-url'), jk = connEl('jellyfin-key');
        return fetch(CONN_URL, {
            method: 'POST', headers: { 'Content-Type': 'application/json', 'Accept': 'application/json' },
            body: JSON.stringify({
                plex: { base_url: pu ? pu.value : '', token: pt ? pt.value : '' },
                jellyfin: { base_url: ju ? ju.value : '', api_key: jk ? jk.value : '' }
            })
        }).then(function () { loadConn(); if (!silent) toast('Connection saved', 'success'); })
          .catch(function () { if (!silent) toast('Could not save connection', 'error'); });
    }
    // Toggle click: reveal that server's config immediately (like the music
    // toggle) and persist it as the active video server pick.
    function pickServer(server) {
        showServerConfig(server);
        if (server === 'jellyfin') loadJellyfinUsers();
        fetch(SERVER_URL, {
            method: 'POST', headers: { 'Content-Type': 'application/json', 'Accept': 'application/json' },
            body: JSON.stringify({ server: server })
        }).then(function () {
            load();
            toast('Video server set to ' + (server === 'plex' ? 'Plex' : 'Jellyfin'), 'success');
        }).catch(function () { /* ignore */ });
    }
    function testConn(server) {
        var name = server === 'plex' ? 'Plex' : 'Jellyfin';
        toast('Testing ' + name + ' connection…', 'info');
        saveConn(true).then(function () {
            return fetch(CONN_URL + '/test', {
                method: 'POST', headers: { 'Content-Type': 'application/json', 'Accept': 'application/json' },
                body: JSON.stringify({ server: server })
            });
        }).then(function (r) { return r.json(); }).then(function (res) {
            if (res && res.success) {
                toast(res.message || (name + ' connection successful'), 'success');
                if (server === 'jellyfin') { loadJellyfinUsers(); load(); }  // user + libraries
            } else {
                toast(name + ' connection failed: ' + ((res && res.error) || 'unknown'), 'error');
            }
        }).catch(function () { toast('Failed to test ' + name + ' connection', 'error'); });
    }

    // ── Jellyfin user picker (mirrors music: pick a user, then its libraries) ──
    function loadJellyfinUsers() {
        var wrap = document.querySelector('[data-video-jellyfin-user-wrap]');
        var sel = document.querySelector('[data-video-jellyfin-user]');
        if (!wrap || !sel) return;
        fetch('/api/video/jellyfin/users', { headers: { 'Accept': 'application/json' } })
            .then(function (r) { return r.ok ? r.json() : null; })
            .then(function (d) {
                if (!d || !d.success || !d.users || !d.users.length) { wrap.style.display = 'none'; return; }
                sel.textContent = '';
                var none = document.createElement('option');
                none.value = ''; none.textContent = 'Select User';
                sel.appendChild(none);
                d.users.forEach(function (u) {
                    var o = document.createElement('option');
                    o.value = u.id;
                    o.textContent = u.name + (u.admin ? ' (admin)' : '');
                    if (u.id === d.selected) o.selected = true;
                    sel.appendChild(o);
                });
                wrap.style.display = 'block';
            })
            .catch(function () { wrap.style.display = 'none'; });
    }
    function selectJellyfinUser(id) {
        fetch('/api/video/jellyfin/user', {
            method: 'POST', headers: { 'Content-Type': 'application/json', 'Accept': 'application/json' },
            body: JSON.stringify({ user: id })
        }).then(function () {
            load();  // refresh libraries for the user
            if (id) toast('Jellyfin user updated', 'success');
        }).catch(function () { /* ignore */ });
    }

    function status(text) {
        var n = document.querySelector('[data-video-lib-status]');
        if (n) n.textContent = text || '';
    }

    // The global Torrent Client category field (Settings → Connections →
    // Torrent Client) — read live so a per-library category input can show
    // it as the "blank = inherits X" placeholder.
    function _globalTorrentCategory() {
        var el = document.getElementById('torrent-client-category');
        return (el && el.value.trim()) || 'soulsync';
    }

    // Each row = one discovered server section, optionally CONFIGURED as a
    // Library (checked, with a rename + destination folder + torrent category).
    // Text inputs stay hidden until checked, and only fire save() on 'change'
    // (blur), never per keystroke, so typing a path never fights a re-render.
    function libraryRow(title, configured, kind) {
        var row = document.createElement('div');
        row.className = 'library-editor-row';
        row.dataset.serverTitle = title;
        row.dataset.libId = (configured && configured.id) || '';

        var top = document.createElement('label');
        top.className = 'checkbox-label';
        var box = document.createElement('input');
        box.type = 'checkbox';
        box.checked = !!configured;
        box.setAttribute('data-lib-checkbox', '');
        top.appendChild(box);
        top.appendChild(document.createTextNode(title));
        row.appendChild(top);

        var fields = document.createElement('div');
        fields.className = 'library-editor-fields';
        fields.style.display = configured ? '' : 'none';

        var labelInput = document.createElement('input');
        labelInput.type = 'text';
        labelInput.placeholder = 'Tab name (default: ' + title + ')';
        labelInput.value = (configured && configured.label) || '';
        labelInput.setAttribute('data-lib-label', '');
        fields.appendChild(labelInput);

        var pathInput = document.createElement('input');
        pathInput.type = 'text';
        pathInput.placeholder = 'Destination folder, e.g. /media/movies';
        pathInput.value = (configured && configured.path) || '';
        pathInput.setAttribute('data-lib-path', '');
        fields.appendChild(pathInput);

        var categoryInput = document.createElement('input');
        categoryInput.type = 'text';
        categoryInput.placeholder = 'Torrent category (blank = "' + _globalTorrentCategory() + '")';
        categoryInput.title = 'Overrides the global Torrent Client category for grabs that land in this library.';
        categoryInput.value = (configured && configured.category) || '';
        categoryInput.setAttribute('data-lib-category', '');
        fields.appendChild(categoryInput);

        // Preferred trackers. This is stored as comma-separated Prowlarr indexer
        // IDS, but the app never showed anyone what those ids were — so the field
        // used to be a bare text box asking for a number you had no way to look
        // up, and _norm_indexer_ids silently dropped anything non-numeric. Typing
        // a tracker's NAME therefore saved as blank, which read as "it doesn't
        // save". The value is still ids on the wire; the picker just supplies them.
        var indexerIdsInput = document.createElement('input');
        indexerIdsInput.type = 'text';
        indexerIdsInput.placeholder = 'Trackers (blank = search all of them)';
        indexerIdsInput.title = 'Prowlarr indexer(s) this library may be grabbed from. Blank searches every indexer.';
        indexerIdsInput.value = (configured && configured.preferred_indexer_ids) || '';
        indexerIdsInput.setAttribute('data-lib-indexer-ids', '');
        fields.appendChild(indexerIdsInput);

        // Default series type — TV libraries only (a film has no episode
        // numbering). series_type decides how a release is SEARCHED for: anime
        // by absolute number ('Show 1071'), dailies by air date, everything
        // else by SxxExx. It was per-show and buried, so in practice it went
        // unset — on the library this was built for, 565 of 571 shows in a
        // Library called "Anime" had no type, and were all being hunted as
        // standard SxxExx. A Library that exists to hold anime already knows.
        // Applies only to shows here that have no type of their OWN.
        if (kind === 'tv') {
            var stWrap = document.createElement('label');
            stWrap.className = 'library-series-type';
            stWrap.style.cssText = 'display:flex;align-items:center;gap:8px;margin-top:6px;font-size:12px;opacity:.85';
            stWrap.appendChild(document.createTextNode('Shows here are'));
            var stSel = document.createElement('select');
            stSel.setAttribute('data-lib-series-type', '');
            stSel.title = 'Applied to shows in this Library that have no series type of their own. '
                        + 'Changes how releases are SEARCHED for, not where they are stored.';
            [['', 'not assumed to be anything'],
             ['standard', 'standard (S01E01)'],
             ['daily', 'daily (by air date)'],
             ['anime', 'anime (absolute numbering)']].forEach(function (o) {
                var opt = document.createElement('option');
                opt.value = o[0]; opt.textContent = o[1];
                stSel.appendChild(opt);
            });
            stSel.value = (configured && configured.default_series_type) || '';
            stWrap.appendChild(stSel);
            fields.appendChild(stWrap);
        }

        // Default quality profile — every kind, unlike series type (a film has
        // no episode numbering, but it certainly has a resolution). Profiles
        // were per-TITLE only, so "everything in my 4K Library is judged at 4K"
        // had to be said one title at a time — and a title not in the library
        // yet had nowhere to say it at all, so its FIRST grab, the one that
        // decides what actually lands on disk, was always judged by the global
        // Default no matter which Library it was headed for.
        // A title carrying a profile of its own still outranks this.
        var qpWrap = document.createElement('label');
        qpWrap.className = 'library-quality-profile';
        qpWrap.style.cssText = 'display:flex;align-items:center;gap:8px;margin-top:6px;font-size:12px;opacity:.85';
        qpWrap.appendChild(document.createTextNode('Judge titles here by'));
        var qpSel = document.createElement('select');
        qpSel.setAttribute('data-lib-quality-profile', '');
        qpSel.title = 'The quality profile titles in this Library are grabbed and upgraded '
                    + 'under. A title with a profile of its own overrides it. Set profiles up '
                    + 'in the Quality section below.';
        qpWrap.appendChild(qpSel);
        fields.appendChild(qpWrap);
        renderProfilePicker(qpSel, configured && configured.default_quality_profile_id);

        // A VISIBLE label and explanation, not a tooltip on the input. The input
        // is switched to type=hidden as soon as the checkbox picker renders, and
        // a hidden input shows neither tooltip nor placeholder — so the previous
        // wording vanished precisely when the checkboxes appeared, which is how
        // a list of trackers with no caption came to read as a search filter it
        // was not. Now it IS a filter, and the caption says so where it is read.
        var trackerLabel = document.createElement('div');
        trackerLabel.className = 'library-tracker-label';
        trackerLabel.setAttribute('data-lib-trackers-label', '');
        trackerLabel.innerHTML = '<strong>Trackers to search for this library</strong>' +
            '<span>Tick the trackers this library may be grabbed from. Only those are ' +
            'searched — automatic searches included. Leave every box unticked to search ' +
            'all of them.</span>';
        fields.appendChild(trackerLabel);

        var trackerBox = document.createElement('div');
        trackerBox.className = 'library-tracker-picker';
        trackerBox.setAttribute('data-lib-trackers', '');
        fields.appendChild(trackerBox);
        renderTrackerPicker(trackerBox, indexerIdsInput);

        row.appendChild(fields);
        box.addEventListener('change', function () { fields.style.display = box.checked ? '' : 'none'; });
        return row;
    }

    // The named quality profiles, fetched once per page and shared by every
    // Library row's picker. Separate from loadQuality()'s fetch of the same
    // endpoint: that one drives the profile EDITOR further down the page and
    // only runs when that section opens, so a Library row cannot wait on it.
    // null = not fetched yet, [] = unreachable.
    var _qProfiles = null;
    var _qProfilesPromise = null;

    function loadQualityProfileList() {
        if (_qProfiles !== null) return Promise.resolve(_qProfiles);
        if (_qProfilesPromise) return _qProfilesPromise;
        _qProfilesPromise = fetch(QUALITY_URL + '/profiles', { headers: { Accept: 'application/json' } })
            .then(function (r) { return r.ok ? r.json() : null; })
            .then(function (d) { _qProfiles = (d && d.profiles) || []; return _qProfiles; })
            .catch(function () { _qProfiles = []; return _qProfiles; });
        return _qProfilesPromise;
    }

    function renderProfilePicker(sel, currentId) {
        // "no Library default" is a real choice, not an absence — it means every
        // title here falls through to the global Default profile, which is what
        // this install did before a Library could carry one. Profile id 0 IS that
        // global Default, so it is dropped from the list rather than offered as a
        // second option that does the same thing under a different name.
        var cur = parseInt(currentId, 10) || 0;
        var head = function (on) {
            return '<option value="0"' + (on ? ' selected' : '') + '>no Library default</option>';
        };
        sel.innerHTML = head(!cur);
        loadQualityProfileList().then(function (list) {
            if (!sel.isConnected) return;
            sel.innerHTML = head(!cur) + list.filter(function (p) { return p.id > 0; })
                .map(function (p) {
                    return '<option value="' + p.id + '"' + (p.id === cur ? ' selected' : '') +
                        '>' + esc(p.name) + '</option>';
                }).join('');
        });
    }

    // Prowlarr's indexer list, fetched once per page and shared by every Library
    // row's picker. null = not fetched yet, [] = Prowlarr unconfigured/unreachable.
    var _indexers = null;
    var _indexersPromise = null;   // in-flight dedupe: every Library row asks at once

    function loadIndexers() {
        if (_indexers !== null) return Promise.resolve(_indexers);
        if (_indexersPromise) return _indexersPromise;
        _indexersPromise = fetch('/api/video/downloads/indexers', { headers: { Accept: 'application/json' } })
            .then(function (r) { return r.ok ? r.json() : null; })
            .then(function (d) { _indexers = (d && d.indexers) || []; return _indexers; })
            .catch(function () { _indexers = []; return _indexers; });
        return _indexersPromise;
    }

    function _selectedIds(input) {
        return String(input.value || '').split(',')
            .map(function (s) { return s.trim(); })
            .filter(function (s) { return s; });
    }

    function renderTrackerPicker(box, input) {
        loadIndexers().then(function (list) {
            if (!box.isConnected) return;
            if (!list.length) {
                // No Prowlarr, or it couldn't be reached. Keep the text field
                // usable rather than hiding the setting entirely.
                box.innerHTML = '<div class="library-tracker-empty">Connect Prowlarr to pick trackers ' +
                    'by name. Until then this field takes indexer IDs.</div>';
                input.placeholder = 'Trackers to search (indexer IDs, e.g. 1,3 — blank = all)';
                return;
            }
            // The picker owns the value from here, so the raw id box is redundant.
            input.type = 'hidden';
            var sel = _selectedIds(input);
            box.innerHTML = list.map(function (ix) {
                var on = sel.indexOf(String(ix.id)) !== -1;
                return '<label class="library-tracker' + (on ? ' library-tracker--on' : '') + '">' +
                    '<input type="checkbox" data-lib-tracker="' + ix.id + '"' + (on ? ' checked' : '') + '>' +
                    '<span>' + esc(ix.name) + '</span>' +
                    '<em>' + esc(ix.protocol || '') + (ix.enable ? '' : ' · disabled') + '</em></label>';
            }).join('');
            box.addEventListener('change', function (e) {
                var cb = e.target.closest('[data-lib-tracker]');
                if (!cb) return;
                var picked = [];
                box.querySelectorAll('[data-lib-tracker]').forEach(function (c) {
                    if (c.checked) picked.push(c.getAttribute('data-lib-tracker'));
                });
                input.value = picked.join(',');
                cb.closest('.library-tracker').classList.toggle('library-tracker--on', cb.checked);
            });
        });
    }

    function fill(group, items, configured, kind) {
        if (!group) return;
        group.textContent = '';
        items = items || [];
        if (!items.length) {
            group.textContent = 'No libraries found on this server.';
            return;
        }
        var seen = {};
        (configured || []).forEach(function (c) {
            group.appendChild(libraryRow(c.server_title, c, kind));
            seen[c.server_title] = true;
        });
        items.forEach(function (it) {
            if (!seen[it.title]) group.appendChild(libraryRow(it.title, null, kind));
        });
    }

    function collectLibraries(group) {
        if (!group) return [];
        var rows = group.querySelectorAll('.library-editor-row');
        var out = [];
        for (var i = 0; i < rows.length; i++) {
            var row = rows[i];
            var box = row.querySelector('[data-lib-checkbox]');
            if (!box || !box.checked) continue;
            var labelInput = row.querySelector('[data-lib-label]');
            var pathInput = row.querySelector('[data-lib-path]');
            var categoryInput = row.querySelector('[data-lib-category]');
            var indexerIdsInput = row.querySelector('[data-lib-indexer-ids]');
            out.push({
                id: row.dataset.libId ? parseInt(row.dataset.libId, 10) : null,
                server_title: row.dataset.serverTitle,
                label: labelInput ? labelInput.value.trim() : '',
                path: pathInput ? pathInput.value.trim() : '',
                category: categoryInput ? categoryInput.value.trim() : '',
                preferred_indexer_ids: indexerIdsInput ? indexerIdsInput.value.trim() : '',
                default_series_type: (function () {
                    var el = row.querySelector('[data-lib-series-type]');
                    return el ? el.value : '';
                })(),
                default_quality_profile_id: (function () {
                    var el = row.querySelector('[data-lib-quality-profile]');
                    return el ? (parseInt(el.value, 10) || 0) : 0;
                })()
            });
        }
        return out;
    }

    // After a save, stamp back the (possibly new) row ids from the response so
    // a later edit UPDATES instead of creating a duplicate — no DOM rebuild
    // (would steal focus mid-edit), just a data-attribute patch.
    function reconcileIds(group, configured) {
        if (!group || !configured) return;
        var byTitle = {};
        configured.forEach(function (c) { byTitle[c.server_title] = c; });
        var rows = group.querySelectorAll('.library-editor-row');
        for (var i = 0; i < rows.length; i++) {
            var match = byTitle[rows[i].dataset.serverTitle];
            rows[i].dataset.libId = match ? match.id : '';
        }
    }

    // The YouTube Libraries editor lived here. Its rows round-tripped into
    // root_folders but nothing ever read them back (primary_root_folder maps
    // only movie/show), so the folder you typed did nothing — the real
    // destination is the youtube_path scalar (Settings → Downloads). Removed
    // rather than left half-wired; per-channel routing is its own feature.

    function load() {
        status('Loading…');
        fetch(URL, { headers: { 'Accept': 'application/json' } })
            .then(function (r) { return r.ok ? r.json() : null; })
            .then(function (d) {
                if (!d || d.error) { status('Could not load libraries'); return; }
                var cfg = d.configured || {};
                fill(document.querySelector('[data-video-lib-group="movies"]'), d.movies || [], cfg.movies, 'movies');
                fill(document.querySelector('[data-video-lib-group="tv"]'), d.tv || [], cfg.tv, 'tv');
                        status('');
                probeLibraries();
            })
            .catch(function () { status('Could not load libraries'); });
    }

    // A destination the server can't write to is the one failure that looks
    // like nothing at all: the grab succeeds, the import fails, and the folder
    // just stays empty. Ask the server whether each Library actually works and
    // say so on the row, rather than leaving it to be found in app.log.
    function probeLibraries() {
        fetch('/api/video/libraries/probe', { headers: { 'Accept': 'application/json' } })
            // Admin-only. A non-admin on this page simply gets no badges
            // instead of a console error and a page that looks broken.
            .then(function (r) { return r.ok ? r.json() : null; })
            .then(function (d) {
                if (!d || !d.success) return;
                var byPath = {};
                Object.keys(d.configured || {}).forEach(function (kind) {
                    (d.configured[kind] || []).forEach(function (e) {
                        byPath[String(e.path || '')] = e;
                    });
                });
                document.querySelectorAll('[data-video-lib-group] .library-editor-row')
                    .forEach(function (row) {
                        var input = row.querySelector('[data-lib-path]');
                        applyLibraryProbe(row, input ? byPath[input.value.trim()] : null);
                    });
            })
            .catch(function () { /* the probe is advisory; never break the page over it */ });
    }

    function applyLibraryProbe(row, probe) {
        var head = row.querySelector('.checkbox-label');
        if (!head) return;
        var badge = row.querySelector('.video-lib-write-badge');
        // An unconfigured row has no path to test, and a writable one is the
        // expected state — decorating it would bury the row that needs
        // attention. Only the broken case gets a badge.
        if (!probe || probe.writable) { if (badge) badge.remove(); return; }
        if (!badge) {
            badge = document.createElement('span');
            badge.className = 'video-lib-write-badge';
            head.appendChild(badge);
        }
        badge.textContent = 'NOT WRITABLE';
        badge.title = probe.detail || '';
    }

    function save(silent) {
        var m = document.querySelector('[data-video-lib-group="movies"]');
        var t = document.querySelector('[data-video-lib-group="tv"]');
        status('Saving…');
        return fetch(URL, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json', 'Accept': 'application/json' },
            body: JSON.stringify({ movies: collectLibraries(m), tv: collectLibraries(t) })
        })
            .then(function (r) { return r.json(); })
            .then(function (d) {
                if (d && d.configured) {
                    reconcileIds(m, d.configured.movies);
                    reconcileIds(t, d.configured.tv);
                }
                // Re-probe: the paths may have just changed, and a stale badge
                // is worse than none — it would vouch for an untested folder.
                probeLibraries();
                status('Saved'); if (!silent) toast('Libraries saved', 'success');
            })
            .catch(function () { status('Save failed'); if (!silent) toast('Could not save libraries', 'error'); });
    }

    // ── Enrichment API keys (TMDB / TVDB) ───────────────────────────────────
    function loadKeys() {
        fetch(CONFIG_URL, { headers: { 'Accept': 'application/json' } })
            .then(function (r) { return r.ok ? r.json() : null; })
            .then(function (d) {
                if (!d) return;
                var t = document.getElementById('tmdb-api-key');
                var v = document.getElementById('tvdb-api-key');
                var o = document.getElementById('omdb-api-key');
                if (t && d.tmdb_api_key != null) t.value = d.tmdb_api_key;
                if (v && d.tvdb_api_key != null) v.value = d.tvdb_api_key;
                if (o && d.omdb_api_key != null) o.value = d.omdb_api_key;
                var fa = document.getElementById('fanart-api-key');
                if (fa && d.fanart_api_key != null) fa.value = d.fanart_api_key;
                var sub = document.getElementById('opensubtitles-api-key');
                if (sub && d.opensubtitles_api_key != null) sub.value = d.opensubtitles_api_key;
                var trakt = document.getElementById('trakt-api-key');
                if (trakt && d.trakt_api_key != null) trakt.value = d.trakt_api_key;
                var mdbl = document.getElementById('mdblist-api-key');
                if (mdbl && d.mdblist_api_key != null) mdbl.value = d.mdblist_api_key;
                var ryd = document.getElementById('video-ryd-enabled');
                if (ryd && d.ryd_enabled != null) ryd.checked = !!d.ryd_enabled;
                var sb = document.getElementById('video-sponsorblock-enabled');
                if (sb && d.sponsorblock_enabled != null) sb.checked = !!d.sponsorblock_enabled;
                var dea = document.getElementById('video-dearrow-enabled');
                if (dea && d.dearrow_enabled != null) dea.checked = !!d.dearrow_enabled;
                var tvm = document.getElementById('video-tvmaze-enabled');
                if (tvm && d.tvmaze_enabled != null) tvm.checked = !!d.tvmaze_enabled;
                var anl = document.getElementById('video-anilist-enabled');
                if (anl && d.anilist_enabled != null) anl.checked = !!d.anilist_enabled;
                var wkd = document.getElementById('video-wikidata-enabled');
                if (wkd && d.wikidata_enabled != null) wkd.checked = !!d.wikidata_enabled;
                var ap = document.getElementById('video-billboard-autoplay');
                if (ap && d.billboard_autoplay != null) ap.checked = !!d.billboard_autoplay;
                var wr = document.getElementById('video-watch-region');
                if (wr && d.watch_region) wr.value = d.watch_region;
            })
            .catch(function () { /* ignore */ });
    }

    function savePrefs(silent) {
        var ap = document.getElementById('video-billboard-autoplay');
        var wr = document.getElementById('video-watch-region');
        return fetch(CONFIG_URL, {
            method: 'POST', headers: { 'Content-Type': 'application/json', 'Accept': 'application/json' },
            body: JSON.stringify({
                billboard_autoplay: ap ? ap.checked : true,
                watch_region: wr ? wr.value : 'US',
            })
        }).then(function () { if (!silent) toast('Preferences saved', 'success'); })
          .catch(function () { /* ignore */ });
    }

    // ── Downloads tab: folders + source mode + hybrid chain ──
    var VIDEO_SOURCES = ['soulseek', 'torrent', 'usenet'];
    var SRC_DL_LABEL = { soulseek: 'Soulseek', torrent: 'Torrent', usenet: 'Usenet' };
    var SRC_DL_EMOJI = { soulseek: '🎵', torrent: '🧲', usenet: '📰' };
    var _videoMode = 'soulseek';
    var _videoHybrid = ['soulseek'];

    function loadDownloads() {
        fetch(DOWNLOADS_URL, { headers: { 'Accept': 'application/json' } })
            .then(function (r) { return r.ok ? r.json() : null; })
            .then(function (d) {
                if (!d) return;
                var setP = function (id, v) { var el = document.getElementById(id); if (el && v != null) el.value = v; };
                setP('video-download-path', d.download_path);
                setP('video-movies-path', d.movies_path);
                setP('video-tv-path', d.tv_path);
                setP('video-youtube-path', d.youtube_path);
                _videoMode = d.download_mode || 'soulseek';
                _videoHybrid = (d.hybrid_order && d.hybrid_order.length) ? d.hybrid_order : ['soulseek'];
                var ms = document.getElementById('video-download-mode');
                if (ms) ms.value = _videoMode;
                // The seeding goals are SHARED with music (one torrent client, one
                // set of goals) and are rendered by the data-shared Torrent Client
                // section, loaded and saved by settings.js. Nothing to do here.
                renderVideoHybrid();
                updateVideoSourceUI();
            })
            .catch(function () { /* ignore */ });
    }

    function saveDownloads(silent) {
        var val = function (id) { var el = document.getElementById(id); return el ? el.value : ''; };
        return fetch(DOWNLOADS_URL, {
            method: 'POST', headers: { 'Content-Type': 'application/json', 'Accept': 'application/json' },
            body: JSON.stringify({
                download_path: val('video-download-path'),
                movies_path: val('video-movies-path'),
                tv_path: val('video-tv-path'),
                youtube_path: val('video-youtube-path'),
                download_mode: _videoMode,
                hybrid_order: _videoHybrid,
                // seed_* deliberately omitted — shared with music, saved by the
                // data-shared Torrent Client section via /api/settings.
            })
        }).then(function () { if (!silent) toast('Download folders saved', 'success'); })
          .catch(function () { /* ignore */ });
    }

    // ── import lists editor (arr-parity P6) ─────────────────────────────────
    var IMPLIST_URL = DOWNLOADS_URL + '/import-lists';
    var _vqImpLists = [];

    function loadImportLists() {
        fetch(IMPLIST_URL, { headers: { 'Accept': 'application/json' } })
            .then(function (r) { return r.ok ? r.json() : null; })
            .then(function (d) {
                if (!d) return;
                _vqImpLists = d.lists || [];
                renderImportLists();
                wireImportLists();
            })
            .catch(function () { /* ignore */ });
    }

    function _sel(options, value) {
        return options.map(function (o) {
            return '<option value="' + o[0] + '"' + (o[0] === value ? ' selected' : '') + '>' + o[1] + '</option>';
        }).join('');
    }

    function renderImportLists() {
        var host = document.getElementById('vq-implist-rows');
        if (!host) return;
        host.innerHTML = _vqImpLists.map(function (l) {
            return '<div class="vq-fmt-row vq-implist-row" data-vq-implist="' + l.id + '">' +
                '<input class="vq-fmt-in" data-vq-il-f="name" value="' + escA(l.name) + '" placeholder="Name">' +
                '<select class="vq-fmt-in" data-vq-il-f="source">' +
                    _sel([['tmdb_list', 'TMDB list'], ['tmdb_chart', 'TMDB chart'],
                          ['imdb_list', 'IMDb list'], ['plex_watchlist', 'Plex Watchlist']], l.source) + '</select>' +
                '<input class="vq-fmt-in" data-vq-il-f="ref" value="' + escA(l.ref) + '" placeholder="list id / chart / ls…">' +
                '<select class="vq-fmt-in" data-vq-il-f="media">' +
                    _sel([['both', 'Both'], ['movie', 'Movies'], ['show', 'Shows']], l.media) + '</select>' +
                '<select class="vq-fmt-in" data-vq-il-f="monitor" title="Shows: what to wish when followed">' +
                    _sel([['future', 'Future eps'], ['all', 'All aired'], ['latest_season', 'Latest season'],
                          ['first_season', 'First season'], ['pilot', 'Pilot']], l.monitor) + '</select>' +
                '<label class="vq-il-on" title="Enabled"><input type="checkbox" data-vq-il-f="enabled"' + (l.enabled ? ' checked' : '') + '></label>' +
                '<button class="vq-fmt-del" type="button" data-vq-implist-del="' + l.id + '" title="Delete list">✕</button>' +
                '</div>';
        }).join('') || '<div class="settings-hint" style="padding:6px 0;">No import lists yet.</div>';
    }

    function _impListFromRow(row) {
        var val = function (k) {
            var el = row.querySelector('[data-vq-il-f="' + k + '"]');
            return el ? (el.type === 'checkbox' ? el.checked : el.value) : '';
        };
        return { id: parseInt(row.getAttribute('data-vq-implist'), 10),
                 name: val('name'), source: val('source'), ref: val('ref'),
                 media: val('media'), monitor: val('monitor'), enabled: val('enabled') };
    }

    function wireImportLists() {
        var host = document.getElementById('vq-implist-rows');
        if (!host || host._vqWired) return;
        host._vqWired = true;
        host.addEventListener('change', function (e) {
            var row = e.target.closest('[data-vq-implist]');
            if (!row) return;
            fetch(IMPLIST_URL, { method: 'POST', headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify(_impListFromRow(row)) })
                .then(function (r) { return r.ok ? r.json() : null; })
                .then(function (res) {
                    if (!res || !res.success) { toast('A list needs a valid source + ref', 'error'); return; }
                    for (var i = 0; i < _vqImpLists.length; i++) {
                        if (_vqImpLists[i].id === res.id) _vqImpLists[i] = res;
                    }
                })
                .catch(function () { toast('Couldn’t save the list', 'error'); });
        });
        host.addEventListener('click', function (e) {
            var del = e.target.closest('[data-vq-implist-del]');
            if (!del) return;
            fetch(IMPLIST_URL + '/' + del.getAttribute('data-vq-implist-del'), { method: 'DELETE' })
                .then(function (r) { return r.ok ? r.json() : null; })
                .then(function (res) {
                    if (!res || !res.success) throw new Error();
                    _vqImpLists = _vqImpLists.filter(function (l) { return String(l.id) !== del.getAttribute('data-vq-implist-del'); });
                    renderImportLists();
                })
                .catch(function () { toast('Couldn’t delete the list', 'error'); });
        });
        var add = document.querySelector('[data-vq-implist-add]');
        if (add && !add._vqWired) {
            add._vqWired = true;
            add.addEventListener('click', function () {
                fetch(IMPLIST_URL, { method: 'POST', headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ name: 'New list', source: 'tmdb_chart', ref: 'trending_movies' }) })
                    .then(function (r) { return r.ok ? r.json() : null; })
                    .then(function (res) {
                        if (!res || !res.success) throw new Error();
                        _vqImpLists.push(res);
                        renderImportLists();
                    })
                    .catch(function () { toast('Couldn’t add a list', 'error'); });
            });
        }
    }

    // ── notification connections (arr-parity P11) ────────────────────────────
    var NOTIFY_URL = '/api/video/notifications';
    var _vqNotify = [];
    var _vqNotifyEvents = [];
    var _NOTIFY_EVENT_LABEL = {
        video_download_completed: 'Imported', video_upgrade_completed: 'Upgraded',
        video_import_failed: 'Import failed', video_download_failed: 'Failed',
        video_wishlist_item_added: 'Wishlisted', video_watchlist_added: 'Followed',
        // The event list itself comes from the server; this map is only labels,
        // and an unlisted event falls back to its raw key — so a new one has to
        // be added here or the checkbox reads 'video_request_pending'.
        video_request_pending: 'Needs approval',
    };

    function loadNotify() {
        fetch(NOTIFY_URL, { headers: { 'Accept': 'application/json' } })
            .then(function (r) { return r.ok ? r.json() : null; })
            .then(function (d) {
                if (!d) return;
                _vqNotify = d.connections || [];
                _vqNotifyEvents = d.events || [];
                renderNotify();
                wireNotify();
            })
            .catch(function () { /* non-admins get a 403 — section stays empty */ });
    }

    function renderNotify() {
        var host = document.getElementById('vq-notify-rows');
        if (!host) return;
        host.innerHTML = _vqNotify.map(function (c) {
            var tg = c.type === 'telegram';
            var evs = _vqNotifyEvents.map(function (ev) {
                return '<label class="vq-nt-ev"><input type="checkbox" data-vq-nt-ev="' + ev + '"' +
                    (c.events.indexOf(ev) > -1 ? ' checked' : '') + '>' +
                    (_NOTIFY_EVENT_LABEL[ev] || ev) + '</label>';
            }).join('');
            return '<div class="vq-nt-block" data-vq-notify="' + c.id + '">' +
                '<div class="vq-fmt-row vq-nt-row">' +
                    '<input class="vq-fmt-in" data-vq-nt-f="name" value="' + escA(c.name) + '" placeholder="Name">' +
                    '<select class="vq-fmt-in" data-vq-nt-f="type">' +
                        _sel([['discord', 'Discord'], ['webhook', 'Webhook'], ['telegram', 'Telegram']], c.type) + '</select>' +
                    '<input class="vq-fmt-in" data-vq-nt-f="url" value="' + escA(c.url) + '" placeholder="Webhook URL"' + (tg ? ' style="display:none"' : '') + '>' +
                    '<input class="vq-fmt-in" data-vq-nt-f="token" value="' + escA(c.token) + '" placeholder="Bot token"' + (tg ? '' : ' style="display:none"') + '>' +
                    '<input class="vq-fmt-in" data-vq-nt-f="chat_id" value="' + escA(c.chat_id) + '" placeholder="Chat id"' + (tg ? '' : ' style="display:none"') + '>' +
                    '<button class="test-button vq-nt-test" type="button" data-vq-notify-test="' + c.id + '">Test</button>' +
                    '<label class="vq-il-on" title="Enabled"><input type="checkbox" data-vq-nt-f="enabled"' + (c.enabled ? ' checked' : '') + '></label>' +
                    '<button class="vq-fmt-del" type="button" data-vq-notify-del="' + c.id + '" title="Delete">✕</button>' +
                '</div>' +
                '<div class="vq-nt-events">' + evs + '</div>' +
                '</div>';
        }).join('') || '<div class="settings-hint" style="padding:6px 0;">No connections yet.</div>';
    }

    function _notifyFromBlock(block) {
        var val = function (k) {
            var el = block.querySelector('[data-vq-nt-f="' + k + '"]');
            return el ? (el.type === 'checkbox' ? el.checked : el.value) : '';
        };
        var events = [];
        Array.prototype.forEach.call(block.querySelectorAll('[data-vq-nt-ev]'), function (cb) {
            if (cb.checked) events.push(cb.getAttribute('data-vq-nt-ev'));
        });
        return { id: parseInt(block.getAttribute('data-vq-notify'), 10),
                 name: val('name'), type: val('type'), url: val('url'),
                 token: val('token'), chat_id: val('chat_id'),
                 enabled: val('enabled'), events: events };
    }

    function wireNotify() {
        var host = document.getElementById('vq-notify-rows');
        if (!host || host._vqWired) return;
        host._vqWired = true;
        host.addEventListener('change', function (e) {
            var block = e.target.closest('[data-vq-notify]');
            if (!block) return;
            var conn = _notifyFromBlock(block);
            fetch(NOTIFY_URL, { method: 'POST', headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify(conn) })
                .then(function (r) { return r.ok ? r.json() : null; })
                .then(function (res) {
                    if (!res || !res.success) { toast('Connection needs a valid target (URL or token + chat id)', 'error'); return; }
                    for (var i = 0; i < _vqNotify.length; i++) {
                        if (_vqNotify[i].id === res.id) _vqNotify[i] = res;
                    }
                    if (e.target.getAttribute('data-vq-nt-f') === 'type') renderNotify();   // swap target inputs
                })
                .catch(function () { toast('Couldn’t save the connection', 'error'); });
        });
        host.addEventListener('click', function (e) {
            var del = e.target.closest('[data-vq-notify-del]');
            if (del) {
                fetch(NOTIFY_URL + '/' + del.getAttribute('data-vq-notify-del'), { method: 'DELETE' })
                    .then(function (r) { return r.ok ? r.json() : null; })
                    .then(function (res) {
                        if (!res || !res.success) throw new Error();
                        _vqNotify = _vqNotify.filter(function (c) { return String(c.id) !== del.getAttribute('data-vq-notify-del'); });
                        renderNotify();
                    })
                    .catch(function () { toast('Couldn’t delete the connection', 'error'); });
                return;
            }
            var tb = e.target.closest('[data-vq-notify-test]');
            if (tb) {
                var block = tb.closest('[data-vq-notify]');
                tb.disabled = true;
                fetch(NOTIFY_URL + '/test', { method: 'POST', headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify(_notifyFromBlock(block)) })
                    .then(function (r) { return r.json().catch(function () { return null; }).then(function (j) { return { ok: r.ok, j: j }; }); })
                    .then(function (res) {
                        tb.disabled = false;
                        if (res.ok && res.j && res.j.success) toast('Test sent — check the channel', 'success');
                        else toast((res.j && res.j.error) || 'Test failed', 'error');
                    })
                    .catch(function () { tb.disabled = false; toast('Test failed', 'error'); });
            }
        });
        var add = document.querySelector('[data-vq-notify-add]');
        if (add && !add._vqWired) {
            add._vqWired = true;
            add.addEventListener('click', function () {
                fetch(NOTIFY_URL, { method: 'POST', headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ name: 'Discord', type: 'discord',
                                           url: 'https://discord.com/api/webhooks/REPLACE-ME' }) })
                    .then(function (r) { return r.ok ? r.json() : null; })
                    .then(function (res) {
                        if (!res || !res.success) throw new Error();
                        _vqNotify.push(res);
                        renderNotify();
                    })
                    .catch(function () { toast('Couldn’t add a connection', 'error'); });
            });
        }
    }

    // Hybrid chain — reuses music's .hybrid-source-item markup/CSS for visual
    // parity. Enabled sources (ordered, numbered) first, disabled ones appended.
    // No album-level/track-level badge — that's a music-only concept.
    function renderVideoHybrid() {
        var host = document.getElementById('video-hybrid-rows');
        if (!host) return;
        var enabled = _videoHybrid.filter(function (s) { return VIDEO_SOURCES.indexOf(s) >= 0; });
        var disabled = VIDEO_SOURCES.filter(function (s) { return enabled.indexOf(s) < 0; });
        var visual = enabled.concat(disabled);
        host.innerHTML = visual.map(function (s) {
            var on = enabled.indexOf(s) >= 0;
            var i = enabled.indexOf(s);
            return '<div class="hybrid-source-item' + (on ? '' : ' disabled') + '">' +
                '<span class="hybrid-source-arrows">' +
                '<button type="button" class="hybrid-arrow-btn" data-vh-move="' + s + '" data-dir="-1"' + ((!on || i === 0) ? ' disabled' : '') + ' title="Move up">▲</button>' +
                '<button type="button" class="hybrid-arrow-btn" data-vh-move="' + s + '" data-dir="1"' + ((!on || i === enabled.length - 1) ? ' disabled' : '') + ' title="Move down">▼</button>' +
                '</span>' +
                '<span class="hybrid-source-icon emoji-icon">' + (SRC_DL_EMOJI[s] || '') + '</span>' +
                '<span class="hybrid-source-name">' + SRC_DL_LABEL[s] + '</span>' +
                '<span class="hybrid-source-priority">' + (on ? (i + 1) : '') + '</span>' +
                '<label class="hybrid-source-toggle"><input type="checkbox" data-vh-toggle="' + s + '"' + (on ? ' checked' : '') + '><span class="toggle-track"></span></label>' +
                '</div>';
        }).join('');
    }

    function moveVH(s, dir) {
        var i = _videoHybrid.indexOf(s), j = i + dir;
        if (i < 0 || j < 0 || j >= _videoHybrid.length) return;
        _videoHybrid[i] = _videoHybrid[j]; _videoHybrid[j] = s;
        renderVideoHybrid(); saveDownloads(true);
    }

    function toggleVH(s, on) {
        if (on) {
            if (_videoHybrid.indexOf(s) < 0) _videoHybrid.push(s);
        } else {
            if (_videoHybrid.length <= 1) { renderVideoHybrid(); return; }  // keep at least one
            _videoHybrid = _videoHybrid.filter(function (x) { return x !== s; });
        }
        renderVideoHybrid(); saveDownloads(true);
    }

    function soulseekActive() {
        return _videoMode === 'soulseek' ||
            (_videoMode === 'hybrid' && _videoHybrid.indexOf('soulseek') >= 0);
    }

    function updateVideoSourceUI() {
        var hc = document.getElementById('video-hybrid-container');
        if (hc) hc.style.display = _videoMode === 'hybrid' ? 'block' : 'none';
        // slskd connection only matters when soulseek is in play.
        var sc = document.getElementById('video-slskd-container');
        if (sc) sc.style.display = soulseekActive() ? 'block' : 'none';
    }

    // ── Shared slskd connection (writes the app-wide soulseek.* — affects Music too) ──
    function _byId(id) { return document.getElementById(id); }
    function loadSlskd() {
        fetch(SLSKD_URL, { headers: { 'Accept': 'application/json' } })
            .then(function (r) { return r.ok ? r.json() : null; })
            .then(function (d) {
                if (!d) return;
                if (_byId('video-slskd-url')) _byId('video-slskd-url').value = d.slskd_url || '';
                if (_byId('video-slskd-api-key')) _byId('video-slskd-api-key').value = d.api_key || '';
                if (_byId('video-slskd-search-timeout')) _byId('video-slskd-search-timeout').value = d.search_timeout != null ? d.search_timeout : 60;
                if (_byId('video-slskd-search-timeout-buffer')) _byId('video-slskd-search-timeout-buffer').value = d.search_timeout_buffer != null ? d.search_timeout_buffer : 15;
                if (_byId('video-slskd-search-min-delay')) _byId('video-slskd-search-min-delay').value = d.search_min_delay_seconds != null ? d.search_min_delay_seconds : 0;
                if (_byId('video-slskd-min-peer-speed')) _byId('video-slskd-min-peer-speed').value = d.min_peer_upload_speed != null ? d.min_peer_upload_speed : 0;
                if (_byId('video-slskd-max-peer-queue')) _byId('video-slskd-max-peer-queue').value = d.max_peer_queue != null ? d.max_peer_queue : 0;
                // config stores seconds; UI shows minutes.
                if (_byId('video-slskd-download-timeout')) _byId('video-slskd-download-timeout').value = Math.round((d.download_timeout != null ? d.download_timeout : 600) / 60);
                if (_byId('video-slskd-auto-clear')) _byId('video-slskd-auto-clear').checked = d.auto_clear_searches !== false;
            })
            .catch(function () { /* ignore */ });
    }

    function _num(id, dflt) { var el = _byId(id); var v = el ? parseInt(el.value, 10) : NaN; return Number.isFinite(v) ? v : dflt; }

    function saveSlskd(silent) {
        var url = _byId('video-slskd-url');
        if (!url) return Promise.resolve();   // section not in DOM
        return fetch(SLSKD_URL, {
            method: 'POST', headers: { 'Content-Type': 'application/json', 'Accept': 'application/json' },
            body: JSON.stringify({
                slskd_url: url.value,
                api_key: _byId('video-slskd-api-key') ? _byId('video-slskd-api-key').value : '',
                search_timeout: _num('video-slskd-search-timeout', 60),
                search_timeout_buffer: _num('video-slskd-search-timeout-buffer', 15),
                search_min_delay_seconds: _num('video-slskd-search-min-delay', 0),
                min_peer_upload_speed: _num('video-slskd-min-peer-speed', 0),
                max_peer_queue: _num('video-slskd-max-peer-queue', 0),
                download_timeout: _num('video-slskd-download-timeout', 10) * 60,   // minutes → seconds
                auto_clear_searches: _byId('video-slskd-auto-clear') ? _byId('video-slskd-auto-clear').checked : true,
            })
        }).then(function () { if (!silent) toast('slskd settings saved (shared with Music)', 'success'); })
          .catch(function () { /* ignore */ });
    }

    function wireSlskd() {
        var ids = ['video-slskd-url', 'video-slskd-api-key', 'video-slskd-search-timeout',
            'video-slskd-search-timeout-buffer', 'video-slskd-search-min-delay',
            'video-slskd-min-peer-speed', 'video-slskd-max-peer-queue',
            'video-slskd-download-timeout', 'video-slskd-auto-clear'];
        ids.forEach(function (id) {
            var el = _byId(id);
            if (el && !el._vsWired) { el._vsWired = true; el.addEventListener('change', function () { saveSlskd(true); }); }
        });
    }

    function wireDownloads() {
        var ms = document.getElementById('video-download-mode');
        if (ms && !ms._vdWired) {
            ms._vdWired = true;
            ms.addEventListener('change', function () {
                _videoMode = ms.value; updateVideoSourceUI(); saveDownloads(true);
            });
        }
        var host = document.getElementById('video-hybrid-rows');
        if (host && !host._vdWired) {
            host._vdWired = true;
            host.addEventListener('click', function (e) {
                var mv = e.target.closest('[data-vh-move]');
                if (mv) moveVH(mv.getAttribute('data-vh-move'), parseInt(mv.getAttribute('data-dir'), 10));
            });
            host.addEventListener('change', function (e) {
                var tg = e.target.closest('[data-vh-toggle]');
                if (tg) toggleVH(tg.getAttribute('data-vh-toggle'), tg.checked);
            });
        }
        // Folder inputs save on change too.
        ['video-download-path', 'video-movies-path', 'video-tv-path', 'video-youtube-path'].forEach(function (id) {
            var el = document.getElementById(id);
            if (el && !el._vdWired) { el._vdWired = true; el.addEventListener('change', function () { saveDownloads(true); }); }
        });
    }

    // ── Video quality profile (resolution tiers + source/codec/HDR/size) ──
    // ── named profiles (per-title assignment; arr-parity P2) ─────────────────
    // The editor edits ONE profile at a time; the bar above it picks which.
    // id 0 = Default (the classic single profile), >=1 = named profiles.
    var _vqProfiles = [];
    var _vqSelectedId = 0;

    function _vqSelected() {
        for (var i = 0; i < _vqProfiles.length; i++) {
            if (_vqProfiles[i].id === _vqSelectedId) return _vqProfiles[i];
        }
        return _vqProfiles[0] || null;
    }

    function renderProfileBar() {
        var sel = document.querySelector('[data-vq-profile-select]');
        if (!sel) return;
        sel.innerHTML = _vqProfiles.map(function (p) {
            return '<option value="' + p.id + '"' + (p.id === _vqSelectedId ? ' selected' : '') + '>' +
                String(p.name).replace(/&/g, '&amp;').replace(/</g, '&lt;') + '</option>';
        }).join('');
        var named = _vqSelectedId > 0;
        var nameIn = document.querySelector('[data-vq-profile-name]');
        if (nameIn) {
            nameIn.classList.toggle('hidden', !named);
            if (named) nameIn.value = (_vqSelected() || {}).name || '';
        }
        var del = document.querySelector('[data-vq-profile-delete]');
        if (del) del.classList.toggle('hidden', !named);
    }

    function loadQuality() {
        fetch(QUALITY_URL + '/profiles', { headers: { 'Accept': 'application/json' } })
            .then(function (r) { return r.ok ? r.json() : null; })
            .then(function (d) {
                if (!d || !Array.isArray(d.profiles) || !d.profiles.length) return;
                _vqProfiles = d.profiles;
                var cur = _vqSelected();
                _vqSelectedId = cur ? cur.id : 0;
                _videoQuality = (cur || d.profiles[0]).profile;
                renderProfileBar();
                renderQuality();
                wireProfileBar();
                loadFormats();
            })
            .catch(function () { /* ignore */ });
    }

    // ── custom formats (arr-parity P3) ───────────────────────────────────────
    var _vqFormats = [];

    function loadFormats() {
        fetch(QUALITY_URL + '/formats', { headers: { 'Accept': 'application/json' } })
            .then(function (r) { return r.ok ? r.json() : null; })
            .then(function (d) {
                if (!d) return;
                _vqFormats = d.formats || [];
                renderFormats();
                renderPreferredGroups();
                wireFormats();
                wireGroups();
            })
            .catch(function () { /* ignore */ });
    }

    function renderFormats() {
        var host = document.getElementById('vq-format-rows');
        if (!host) return;
        var overrides = (_videoQuality && _videoQuality.format_scores) || {};
        host.innerHTML = _vqFormats.map(function (f) {
            var ov = overrides[String(f.id)];
            return '<div class="vq-fmt-row" data-vq-fmt="' + f.id + '" data-vq-fmt-kind="' + escA(f.kind || 'custom') + '">' +
                '<input class="vq-fmt-in" data-vq-fmt-f="name" value="' + escA(f.name) + '" placeholder="Name">' +
                '<input class="vq-fmt-in" data-vq-fmt-f="include" value="' + escA((f.include || []).join(', ')) + '" placeholder="match: term, /regex/">' +
                '<input class="vq-fmt-in" data-vq-fmt-f="exclude" value="' + escA((f.exclude || []).join(', ')) + '" placeholder="never: term, /regex/">' +
                '<input class="vq-fmt-in vq-fmt-num" data-vq-fmt-f="score" type="number" value="' + f.score + '" title="Default score">' +
                '<input class="vq-fmt-in vq-fmt-num" data-vq-fmt-f="override" type="number" value="' + (ov == null ? '' : ov) + '" placeholder="—" title="Score for the selected profile (blank = default)">' +
                '<button class="vq-fmt-del" type="button" data-vq-fmt-del="' + f.id + '" title="Delete format">✕</button>' +
                '</div>';
        }).join('') || '<div class="settings-hint" style="padding:6px 0;">No custom formats yet — releases rank purely by the ladder + tie-breakers.</div>';
        var minIn = document.getElementById('vq-min-format-score');
        if (minIn) minIn.value = (_videoQuality && _videoQuality.min_format_score) || 0;
    }

    function escA(s) { return String(s == null ? '' : s).replace(/&/g, '&amp;').replace(/"/g, '&quot;').replace(/</g, '&lt;'); }

    function _fmtFromRow(row) {
        var val = function (k) { var el = row.querySelector('[data-vq-fmt-f="' + k + '"]'); return el ? el.value : ''; };
        var split = function (s) { return s.split(',').map(function (x) { return x.trim(); }).filter(Boolean); };
        return { id: parseInt(row.getAttribute('data-vq-fmt'), 10),
                 name: val('name'), include: split(val('include')), exclude: split(val('exclude')),
                 score: parseInt(val('score'), 10) || 0, kind: row.getAttribute('data-vq-fmt-kind') || 'custom' };
    }

    // ── preferred groups (thin quick-add over custom formats) ────────────────
    // A 'group' format is a normal custom format tagged kind:'group' — this
    // panel just filters the SAME _vqFormats list, so add/remove here and
    // editing the row in the full Custom Formats table below stay in sync.
    function renderPreferredGroups() {
        var host = document.getElementById('vq-group-rows');
        if (!host) return;
        var groups = _vqFormats.filter(function (f) { return f.kind === 'group'; });
        host.innerHTML = groups.map(function (f) {
            return '<div class="vq-fmt-row vq-group-row" data-vq-group="' + f.id + '">' +
                '<span class="vq-group-name">' + escA((f.include || [])[0] || f.name) + '</span>' +
                '<button class="vq-fmt-del" type="button" data-vq-group-del="' + f.id + '" title="Remove">✕</button>' +
                '</div>';
        }).join('') || '<div class="settings-hint" style="padding:6px 0;">No preferred groups yet.</div>';
    }

    function wireGroups() {
        var addBtn = document.querySelector('[data-vq-group-add]');
        if (addBtn && !addBtn._vqWired) {
            addBtn._vqWired = true;
            var doAdd = function () {
                var input = document.getElementById('vq-group-add-input');
                var name = input ? input.value.trim() : '';
                if (!name) return;
                fetch(QUALITY_URL + '/formats', {
                    method: 'POST', headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ name: 'Prefer ' + name, include: [name], exclude: [], score: 50, kind: 'group' })
                }).then(function (r) { return r.ok ? r.json() : null; })
                  .then(function (res) {
                      if (!res || !res.success) { toast('Couldn’t add that group', 'error'); return; }
                      _vqFormats.push({ id: res.id, name: res.name, include: res.include, exclude: res.exclude,
                                        score: res.score, kind: res.kind });
                      if (input) input.value = '';
                      renderPreferredGroups();
                      renderFormats();
                  })
                  .catch(function () { toast('Couldn’t add that group', 'error'); });
            };
            addBtn.addEventListener('click', doAdd);
            var input = document.getElementById('vq-group-add-input');
            if (input) input.addEventListener('keydown', function (e) { if (e.key === 'Enter') { e.preventDefault(); doAdd(); } });
        }
        var groupHost = document.getElementById('vq-group-rows');
        if (groupHost && !groupHost._vqWired) {
            groupHost._vqWired = true;
            groupHost.addEventListener('click', function (e) {
                var del = e.target.closest('[data-vq-group-del]');
                if (!del) return;
                var fid = del.getAttribute('data-vq-group-del');
                fetch(QUALITY_URL + '/formats/' + fid, { method: 'DELETE' })
                    .then(function (r) { return r.ok ? r.json() : null; })
                    .then(function (res) {
                        if (!res || !res.success) throw new Error();
                        _vqFormats = _vqFormats.filter(function (f) { return String(f.id) !== fid; });
                        renderPreferredGroups();
                        renderFormats();
                    })
                    .catch(function () { toast('Couldn’t remove that group', 'error'); });
            });
        }
    }

    function wireFormats() {
        var host = document.getElementById('vq-format-rows');
        if (!host || host._vqWired) return;
        host._vqWired = true;
        host.addEventListener('change', function (e) {
            var row = e.target.closest('[data-vq-fmt]');
            if (!row) return;
            if (e.target.matches('[data-vq-fmt-f="override"]')) {
                // per-profile score override lives on the SELECTED profile
                if (!_videoQuality) return;
                var fs = _videoQuality.format_scores || (_videoQuality.format_scores = {});
                var v = e.target.value.trim();
                if (v === '') delete fs[row.getAttribute('data-vq-fmt')];
                else fs[row.getAttribute('data-vq-fmt')] = parseInt(v, 10) || 0;
                saveQuality(true);
                return;
            }
            var f = _fmtFromRow(row);
            fetch(QUALITY_URL + '/formats', {
                method: 'POST', headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify(f)
            }).then(function (r) { return r.ok ? r.json() : null; })
              .then(function (res) {
                  if (!res || !res.success) { toast('A format needs a name and at least one term', 'error'); return; }
                  for (var i = 0; i < _vqFormats.length; i++) {
                      if (_vqFormats[i].id === res.id) {
                          _vqFormats[i] = { id: res.id, name: res.name, include: res.include, exclude: res.exclude,
                                            score: res.score, kind: res.kind };
                      }
                  }
                  renderPreferredGroups();
              })
              .catch(function () { toast('Couldn’t save the format', 'error'); });
        });
        host.addEventListener('click', function (e) {
            var del = e.target.closest('[data-vq-fmt-del]');
            if (!del) return;
            var fid = del.getAttribute('data-vq-fmt-del');
            fetch(QUALITY_URL + '/formats/' + fid, { method: 'DELETE' })
                .then(function (r) { return r.ok ? r.json() : null; })
                .then(function (res) {
                    if (!res || !res.success) throw new Error();
                    _vqFormats = _vqFormats.filter(function (f) { return String(f.id) !== fid; });
                    renderFormats();
                    renderPreferredGroups();
                })
                .catch(function () { toast('Couldn’t delete the format', 'error'); });
        });
        var add = document.querySelector('[data-vq-format-add]');
        if (add && !add._vqWired) {
            add._vqWired = true;
            add.addEventListener('click', function () {
                fetch(QUALITY_URL + '/formats', {
                    method: 'POST', headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ name: 'New format', include: ['REPLACE-ME'], score: 10 })
                }).then(function (r) { return r.ok ? r.json() : null; })
                  .then(function (res) {
                      if (!res || !res.success) throw new Error();
                      _vqFormats.push({ id: res.id, name: res.name, include: res.include, exclude: res.exclude,
                                        score: res.score, kind: res.kind });
                      renderFormats();
                  })
                  .catch(function () { toast('Couldn’t add a format', 'error'); });
            });
        }
        var minIn = document.getElementById('vq-min-format-score');
        if (minIn && !minIn._vqWired) {
            minIn._vqWired = true;
            minIn.addEventListener('change', function () {
                if (!_videoQuality) return;
                _videoQuality.min_format_score = parseInt(minIn.value, 10) || 0;
                saveQuality(true);
            });
        }
    }

    function wireProfileBar() {
        var sel = document.querySelector('[data-vq-profile-select]');
        if (!sel || sel._vqWired) return;
        sel._vqWired = true;
        sel.addEventListener('change', function () {
            _vqSelectedId = parseInt(sel.value, 10) || 0;
            var cur = _vqSelected();
            if (cur) { _videoQuality = cur.profile; renderProfileBar(); renderQuality(); renderFormats(); }
        });
        var nameIn = document.querySelector('[data-vq-profile-name]');
        if (nameIn) nameIn.addEventListener('change', function () { saveQuality(true); });
        var nb = document.querySelector('[data-vq-profile-new]');
        if (nb) nb.addEventListener('click', function () {
            fetch(QUALITY_URL + '/profiles', {
                method: 'POST', headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ name: 'New profile', profile: _videoQuality })
            }).then(function (r) { return r.ok ? r.json() : null; })
              .then(function (res) {
                  if (!res || !res.success) throw new Error();
                  _vqSelectedId = res.id;
                  toast('Profile created — rename it, tweak it, then assign it from a title’s Manage panel', 'success');
                  loadQuality();
              })
              .catch(function () { toast('Couldn’t create the profile', 'error'); });
        });
        var db = document.querySelector('[data-vq-profile-delete]');
        if (db) db.addEventListener('click', function () {
            if (_vqSelectedId <= 0) return;
            var doDelete = function () {
                fetch(QUALITY_URL + '/profiles/' + _vqSelectedId, { method: 'DELETE' })
                    .then(function (r) { return r.ok ? r.json() : null; })
                    .then(function (res) {
                        if (!res || !res.success) throw new Error();
                        _vqSelectedId = 0;
                        toast('Profile deleted — titles using it fall back to Default', 'success');
                        loadQuality();
                    })
                    .catch(function () { toast('Couldn’t delete the profile', 'error'); });
            };
            if (typeof showConfirmDialog === 'function') {
                showConfirmDialog({
                    title: 'Delete this quality profile?',
                    message: 'Titles assigned to it will use the Default profile instead.',
                    confirmText: 'Delete', destructive: true,
                }).then(function (ok) { if (ok) doDelete(); });
            } else { doDelete(); }
        });
    }

    function _vqSizeLabel(id, v) {
        var lab = document.getElementById(id);
        if (lab) lab.textContent = v ? (v + ' GB') : 'No limit';
    }

    function _vqSeg(id, attr, value) {
        var seg = document.getElementById(id);
        if (!seg) return;
        Array.prototype.forEach.call(seg.querySelectorAll('[' + attr + ']'), function (b) {
            b.classList.toggle('active', b.getAttribute(attr) === value);
        });
    }

    function renderQuality() {
        var p = _videoQuality;
        if (!p) return;
        var tiers = Array.isArray(p.tiers) ? p.tiers : [];

        // Quality ladder — ranked, toggleable (same .hybrid-source-item styling as Download Source).
        var host = document.getElementById('vq-tier-rows');
        if (host) {
            host.innerHTML = tiers.map(function (t, i) {
                return '<div class="hybrid-source-item' + (t.enabled ? '' : ' disabled') + '">' +
                    '<span class="hybrid-source-arrows">' +
                    '<button type="button" class="hybrid-arrow-btn" data-vq-tier-move="' + t.key + '" data-dir="-1"' + (i === 0 ? ' disabled' : '') + ' title="Move up">▲</button>' +
                    '<button type="button" class="hybrid-arrow-btn" data-vq-tier-move="' + t.key + '" data-dir="1"' + (i === tiers.length - 1 ? ' disabled' : '') + ' title="Move down">▼</button>' +
                    '</span>' +
                    '<span class="hybrid-source-name">' + (TIER_LABEL[t.key] || t.key) + '</span>' +
                    '<span class="hybrid-source-priority">' + (i + 1) + '</span>' +
                    '<label class="hybrid-source-toggle"><input type="checkbox" data-vq-tier-toggle="' + t.key + '"' + (t.enabled ? ' checked' : '') + '><span class="toggle-track"></span></label>' +
                    '</div>';
            }).join('');
        }

        // Cutoff — a loose resolution target (static <option>s); "" = always upgrade.
        var cut = document.getElementById('vq-cutoff');
        if (cut) cut.value = (p.cutoff_resolution == null ? '1080p' : p.cutoff_resolution);

        // Hard rejects — toggle chips (on = blocked).
        var rj = document.getElementById('vq-rejects');
        if (rj) {
            var set = Array.isArray(p.rejects) ? p.rejects : [];
            rj.innerHTML = REJECT_ORDER.map(function (k) {
                var on = set.indexOf(k) !== -1;
                return '<button type="button" class="vq-chip' + (on ? ' on' : '') + '" data-vq-reject="' + k + '">' + (REJECT_LABEL[k] || k) + '</button>';
            }).join('');
        }

        // Soft preferences.
        _vqSeg('vq-codec', 'data-vq-codec', p.prefer_codec);
        _vqSeg('vq-hdr', 'data-vq-hdr', p.prefer_hdr);
        _vqSeg('vq-audio', 'data-vq-audio', p.prefer_audio);
        var rep = document.getElementById('vq-prefer-repack'); if (rep) rep.checked = !!p.prefer_repack;

        // Size guard — split by runtime so a movie and an episode aren't judged the same.
        var mv = document.getElementById('vq-movie-size'); if (mv) mv.value = p.max_movie_gb || 0;
        var ep = document.getElementById('vq-episode-size'); if (ep) ep.value = p.max_episode_gb || 0;
        _vqSizeLabel('vq-movie-label', p.max_movie_gb || 0);
        _vqSizeLabel('vq-episode-label', p.max_episode_gb || 0);
    }

    function moveTier(k, dir) {
        var p = _videoQuality; if (!p || !Array.isArray(p.tiers)) return;
        var arr = p.tiers, i = -1;
        for (var n = 0; n < arr.length; n++) { if (arr[n].key === k) { i = n; break; } }
        var j = i + dir;
        if (i < 0 || j < 0 || j >= arr.length) return;
        var tmp = arr[i]; arr[i] = arr[j]; arr[j] = tmp;   // swap
        renderQuality(); saveQuality(true);
    }

    function toggleReject(k) {
        var p = _videoQuality; if (!p) return;
        if (!Array.isArray(p.rejects)) p.rejects = [];
        var i = p.rejects.indexOf(k);
        if (i === -1) p.rejects.push(k); else p.rejects.splice(i, 1);
        renderQuality(); saveQuality(true);
    }

    function saveQuality(silent) {
        if (!_videoQuality) return Promise.resolve();
        if (_vqSelectedId > 0) {
            // a NAMED profile — routed through the profiles endpoint (P2)
            var nameIn = document.querySelector('[data-vq-profile-name]');
            var cur = _vqSelected() || {};
            return fetch(QUALITY_URL + '/profiles', {
                method: 'POST', headers: { 'Content-Type': 'application/json', 'Accept': 'application/json' },
                body: JSON.stringify({ id: _vqSelectedId,
                                       name: (nameIn && nameIn.value.trim()) || cur.name || 'Unnamed profile',
                                       profile: _videoQuality })
            }).then(function (r) { return r.ok ? r.json() : null; })
              .then(function (d) {
                  if (d && d.profile) { _videoQuality = d.profile; cur.name = d.name; cur.profile = d.profile; }
                  renderProfileBar();
                  if (!silent) toast('Quality profile saved', 'success');
              })
              .catch(function () { /* ignore */ });
        }
        return fetch(QUALITY_URL, {
            method: 'POST', headers: { 'Content-Type': 'application/json', 'Accept': 'application/json' },
            body: JSON.stringify(_videoQuality)
        }).then(function (r) { return r.ok ? r.json() : null; })
          .then(function (d) { if (d) _videoQuality = d; if (!silent) toast('Quality profile saved', 'success'); })
          .catch(function () { /* ignore */ });
    }

    // Delegated handlers for the quality profile (rows re-render, so delegate).
    function wireQuality() {
        var sec = document.getElementById('vq-tier-rows');
        if (!sec) return;
        var card = sec.closest('.settings-group');
        if (!card || card._vqWired) return;
        card._vqWired = true;
        card.addEventListener('click', function (e) {
            var tm = e.target.closest('[data-vq-tier-move]');
            if (tm) { moveTier(tm.getAttribute('data-vq-tier-move'), parseInt(tm.getAttribute('data-dir'), 10)); return; }
            var rj = e.target.closest('[data-vq-reject]');
            if (rj) { toggleReject(rj.getAttribute('data-vq-reject')); return; }
            if (!_videoQuality) return;
            var cd = e.target.closest('[data-vq-codec]');
            if (cd) { _videoQuality.prefer_codec = cd.getAttribute('data-vq-codec'); renderQuality(); saveQuality(true); return; }
            var hd = e.target.closest('[data-vq-hdr]');
            if (hd) { _videoQuality.prefer_hdr = hd.getAttribute('data-vq-hdr'); renderQuality(); saveQuality(true); return; }
            var au = e.target.closest('[data-vq-audio]');
            if (au) { _videoQuality.prefer_audio = au.getAttribute('data-vq-audio'); renderQuality(); saveQuality(true); return; }
        });
        card.addEventListener('change', function (e) {
            if (!_videoQuality) return;
            var tt = e.target.closest('[data-vq-tier-toggle]');
            if (tt) {
                var key = tt.getAttribute('data-vq-tier-toggle');
                var arr = _videoQuality.tiers || [];
                for (var n = 0; n < arr.length; n++) { if (arr[n].key === key) { arr[n].enabled = tt.checked; break; } }
                renderQuality(); saveQuality(true); return;
            }
            if (e.target.id === 'vq-cutoff') { _videoQuality.cutoff_resolution = e.target.value; saveQuality(true); return; }
            if (e.target.id === 'vq-prefer-repack') { _videoQuality.prefer_repack = e.target.checked; saveQuality(true); return; }
            if (e.target.id === 'vq-movie-size') { _videoQuality.max_movie_gb = parseInt(e.target.value, 10) || 0; saveQuality(true); return; }
            if (e.target.id === 'vq-episode-size') { _videoQuality.max_episode_gb = parseInt(e.target.value, 10) || 0; saveQuality(true); return; }
        });
        card.addEventListener('input', function (e) {
            if (e.target.id === 'vq-movie-size') { _vqSizeLabel('vq-movie-label', parseInt(e.target.value, 10) || 0); return; }
            if (e.target.id === 'vq-episode-size') { _vqSizeLabel('vq-episode-label', parseInt(e.target.value, 10) || 0); return; }
        });
    }

    // ── YouTube quality (separate, smaller yt-dlp profile) ────────────────────
    function loadYtQuality() {
        fetch(YT_QUALITY_URL, { headers: { 'Accept': 'application/json' } })
            .then(function (r) { return r.ok ? r.json() : null; })
            .then(function (d) { if (d) { _videoYtQuality = d; renderYtQuality(); } })
            .catch(function () { /* ignore */ });
    }

    function renderYtQuality() {
        var p = _videoYtQuality;
        if (!p) return;
        var res = document.getElementById('yq-resolution'); if (res) res.value = p.max_resolution || '1080p';
        _vqSeg('yq-codec', 'data-yq-codec', p.video_codec);
        _vqSeg('yq-container', 'data-yq-container', p.container);
        var fps = document.getElementById('yq-60fps'); if (fps) fps.checked = !!p.prefer_60fps;
        var hdr = document.getElementById('yq-hdr'); if (hdr) hdr.checked = !!p.allow_hdr;
    }

    function saveYtQuality(silent) {
        if (!_videoYtQuality) return Promise.resolve();
        return fetch(YT_QUALITY_URL, {
            method: 'POST', headers: { 'Content-Type': 'application/json', 'Accept': 'application/json' },
            body: JSON.stringify(_videoYtQuality)
        }).then(function (r) { return r.ok ? r.json() : null; })
          .then(function (d) { if (d) _videoYtQuality = d; if (!silent) toast('YouTube quality saved', 'success'); })
          .catch(function () { /* ignore */ });
    }

    function wireYtQuality() {
        var seg = document.getElementById('yq-codec');
        if (!seg) return;
        var card = seg.closest('.settings-group');
        if (!card || card._yqWired) return;
        card._yqWired = true;
        card.addEventListener('click', function (e) {
            if (!_videoYtQuality) return;
            var cd = e.target.closest('[data-yq-codec]');
            if (cd) { _videoYtQuality.video_codec = cd.getAttribute('data-yq-codec'); renderYtQuality(); saveYtQuality(true); return; }
            var ct = e.target.closest('[data-yq-container]');
            if (ct) { _videoYtQuality.container = ct.getAttribute('data-yq-container'); renderYtQuality(); saveYtQuality(true); return; }
        });
        card.addEventListener('change', function (e) {
            if (!_videoYtQuality) return;
            if (e.target.id === 'yq-resolution') { _videoYtQuality.max_resolution = e.target.value; saveYtQuality(true); return; }
            if (e.target.id === 'yq-60fps') { _videoYtQuality.prefer_60fps = e.target.checked; saveYtQuality(true); return; }
            if (e.target.id === 'yq-hdr') { _videoYtQuality.allow_hdr = e.target.checked; saveYtQuality(true); return; }
        });
    }

    function saveKeys(silent) {
        var t = document.getElementById('tmdb-api-key');
        var v = document.getElementById('tvdb-api-key');
        var o = document.getElementById('omdb-api-key');
        var fa = document.getElementById('fanart-api-key');
        var sub = document.getElementById('opensubtitles-api-key');
        var trakt = document.getElementById('trakt-api-key');
        var ryd = document.getElementById('video-ryd-enabled');
        var sb = document.getElementById('video-sponsorblock-enabled');
        var dea = document.getElementById('video-dearrow-enabled');
        var tvm = document.getElementById('video-tvmaze-enabled');
        var anl = document.getElementById('video-anilist-enabled');
        var wkd = document.getElementById('video-wikidata-enabled');
        return fetch(CONFIG_URL, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json', 'Accept': 'application/json' },
            body: JSON.stringify({
                tmdb_api_key: t ? t.value : '', tvdb_api_key: v ? v.value : '',
                omdb_api_key: o ? o.value : '',
                fanart_api_key: fa ? fa.value : '',
                opensubtitles_api_key: sub ? sub.value : '',
                trakt_api_key: trakt ? trakt.value : '',
                mdblist_api_key: (document.getElementById('mdblist-api-key') || {}).value || '',
                ryd_enabled: ryd ? ryd.checked : true,
                sponsorblock_enabled: sb ? sb.checked : true,
                dearrow_enabled: dea ? dea.checked : true,
                tvmaze_enabled: tvm ? tvm.checked : true,
                anilist_enabled: anl ? anl.checked : false,
                wikidata_enabled: wkd ? wkd.checked : true,
            })
        }).then(function () { if (!silent) toast('API keys saved', 'success'); })
          .catch(function () { /* ignore */ });
    }

    function toast(msg, type) {
        if (typeof showToast === 'function') showToast(msg, type);  // shared shell helper
    }

    // Mirrors music's testConnection(): save the key, then hit the test
    // endpoint, then toast the result. Isolated -> /api/video/enrichment/<svc>/test.
    function testConnection(svc) {
        var name = svc.toUpperCase();
        toast('Testing ' + name + ' connection…', 'info');
        saveKeys(true).then(function () {
            return fetch('/api/video/enrichment/' + svc + '/test',
                { method: 'POST', headers: { 'Accept': 'application/json' } });
        }).then(function (r) { return r.json(); }).then(function (res) {
            if (res && res.success) toast(res.message || (name + ' connection successful'), 'success');
            else toast(name + ' connection failed: ' + ((res && res.error) || 'unknown'), 'error');
        }).catch(function () { toast('Failed to test ' + name + ' connection', 'error'); });
    }

    // ── Library Organization (naming templates + post-process toggles) ───────
    var ORG_URL = '/api/video/organization';
    var _videoOrg = null;
    // TRaSH-recommended schemes (trash-guides.info), offered as a one-click preset.
    // Not the default: switching an existing library's naming silently would make
    // every file on disk non-conforming until renamed.
    var _TRASH_PRESETS = {
        movie: "{Movie CleanTitle} {(Release Year)} {edition-{Edition Tags}} {[Custom Formats]}"
             + "{[Quality Full]}{[MediaInfo VideoDynamicRangeType]}"
             + "{[Mediainfo AudioCodec}{ Mediainfo AudioChannels]}{[Mediainfo VideoCodec]}"
             + "{-Release Group}",
        episode: "{Series CleanTitleWithoutYear} {(Series Year)}/Season {season:00}/"
               + "{Series CleanTitleWithoutYear} {(Series Year)} - S{season:00}E{episode:00} - "
               + "{Episode CleanTitle:90} {[Custom Formats]}{[Quality Full]}"
               + "{[Mediainfo AudioCodec}{ Mediainfo AudioChannels]}"
               + "{[MediaInfo VideoDynamicRangeType]}{[Mediainfo VideoCodec]}{-Release Group}"
    };

    // The preview renders SERVER-SIDE, through the same code that names files on
    // import. This used to be a JavaScript re-implementation of the template
    // engine kept "in lockstep" by hand — which the {Token} scheme's optional
    // groups, padding and truncation would have made a second engine to maintain
    // and a second place to be wrong. One renderer, so the preview cannot lie.
    var _orgPreviewTimer = null;
    var _orgTokenCache = null;

    function renderOrgPreview() {
        if (_orgPreviewTimer) clearTimeout(_orgPreviewTimer);
        _orgPreviewTimer = setTimeout(_fetchOrgPreview, 250);
    }

    function _fetchOrgPreview() {
        var get = function (id) { var el = document.getElementById(id); return el ? (el.value || el.placeholder) : null; };
        var body = { movie_template: get('vo-movie-template'),
                     episode_template: get('vo-episode-template'),
                     youtube_template: get('vo-youtube-template') };
        fetch(ORG_URL + '/naming/preview', {
            method: 'POST', headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(body)
        }).then(function (r) { return r.ok ? r.json() : null; }).then(function (d) {
            if (!d) return;
            _orgTokenCache = d.tokens || _orgTokenCache;
            [['movie', 'vo-movie-preview'], ['episode', 'vo-episode-preview'],
             ['youtube', 'vo-youtube-preview']].forEach(function (pair) {
                var el = document.getElementById(pair[1]);
                if (!el) return;
                // A template the renderer refused is shown as an error rather than
                // a stale success — a preview that keeps displaying the last good
                // render while the box holds something broken is worse than none.
                el.textContent = d[pair[0]] || (d[pair[0] + '_error'] ? 'Invalid template' : '…');
            });
        }).catch(function () { /* preview is a nicety; never block the page */ });
    }

    function _toggleTokenList(scope) {
        var box = document.getElementById('vo-' + scope + '-tokens');
        if (!box) return;
        if (!box.hidden) { box.hidden = true; return; }
        var names = (_orgTokenCache || {})[scope] || [];
        box.innerHTML = names.length
            ? names.map(function (n) {
                return '<code class="vo-token" data-vo-insert="' + escA(scope) + '" title="Click to insert">{'
                     + esc(n) + '}</code>';
              }).join(' ')
            : '<small class="settings-hint">Token list unavailable — check the connection.</small>';
        box.hidden = false;
    }

    // Bumped by every edit to a template box. A load that was already in flight
    // when an edit happened must not paint over it — see loadOrganization.
    var _orgEditSeq = 0;
    var _orgLoadSeq = 0;

    function fillOrg(keepTemplates) {
        if (!_videoOrg) return;
        var set = function (id, v) { var el = document.getElementById(id); if (el) el.value = v; };
        var chk = function (id, v) { var el = document.getElementById(id); if (el) el.checked = !!v; };
        if (!keepTemplates) {
            set('vo-movie-template', _videoOrg.movie_template || '');
            set('vo-episode-template', _videoOrg.episode_template || '');
            set('vo-youtube-template', _videoOrg.youtube_template || '');
        }
        set('vo-transfer-mode', _videoOrg.transfer_mode || 'copy');
        chk('vo-verify', _videoOrg.verify_with_ffprobe);
        chk('vo-replace', _videoOrg.replace_existing);
        chk('vo-rename-first', _videoOrg.rename_before_import);
        chk('vo-subs', _videoOrg.carry_subtitles);
        chk('vo-artwork', _videoOrg.save_artwork);
        chk('vo-nfo', _videoOrg.write_nfo);
        chk('vo-subs-dl', _videoOrg.download_subtitles);
        set('vo-sub-langs', _videoOrg.subtitle_langs || 'en');
        chk('vo-recycle', _videoOrg.recycle_deletes);
        set('vo-recycle-days', _videoOrg.recycle_keep_days || 7);
        set('vo-recycle-path', _videoOrg.recycle_path || '');
        set('vo-min-free', _videoOrg.min_free_disk_gb || 0);
        set('vo-yt-follow-count', _videoOrg.youtube_follow_count == null ? 5 : _videoOrg.youtube_follow_count);
        set('vo-sponsorblock', _videoOrg.youtube_sponsorblock || 'off');
        chk('vo-yt-subs', _videoOrg.youtube_embed_subs);
        renderOrgPreview();
    }
    function loadOrganization() {
        // Showing this page fires a dozen loads at once, and this GET has been
        // measured landing ~900ms after the page appears. Anything the user did
        // in that window used to be painted over by the response — their edit
        // vanished from the box, which reads as "it didn't save" even when it
        // did. A response only fills the templates if no edit beat it here, and
        // a superseded response is dropped entirely.
        var seq = ++_orgLoadSeq;
        var editsAtStart = _orgEditSeq;
        fetch(ORG_URL, { headers: { 'Accept': 'application/json' } })
            .then(function (r) { return r.ok ? r.json() : null; })
            .then(function (d) {
                if (!d || seq !== _orgLoadSeq) return;
                _videoOrg = d;
                fillOrg(_orgEditSeq !== editsAtStart);
            })
            .catch(function () { /* ignore */ });
    }
    function collectOrg() {
        var val = function (id) { var el = document.getElementById(id); return el ? el.value : ''; };
        var on = function (id) { var el = document.getElementById(id); return !!(el && el.checked); };
        return {
            movie_template: val('vo-movie-template'),
            episode_template: val('vo-episode-template'),
            youtube_template: val('vo-youtube-template'),
            transfer_mode: val('vo-transfer-mode'),
            verify_with_ffprobe: on('vo-verify'),
            replace_existing: on('vo-replace'),
            rename_before_import: on('vo-rename-first'),
            carry_subtitles: on('vo-subs'),
            save_artwork: on('vo-artwork'),
            write_nfo: on('vo-nfo'),
            download_subtitles: on('vo-subs-dl'),
            subtitle_langs: val('vo-sub-langs'),
            recycle_deletes: on('vo-recycle'),
            recycle_keep_days: val('vo-recycle-days'),
            recycle_path: val('vo-recycle-path'),
            min_free_disk_gb: val('vo-min-free'),
            youtube_follow_count: val('vo-yt-follow-count'),
            youtube_sponsorblock: val('vo-sponsorblock'),
            youtube_embed_subs: on('vo-yt-subs')
        };
    }
    function saveOrganization(silent) {
        // Resolves true only when the server actually took it. A save that failed
        // used to toast success anyway, which on this card is the worst possible
        // lie — the field keeps showing the value while the library keeps naming
        // files the old way. A FAILED save always speaks, silent or not.
        return fetch(ORG_URL, {
            method: 'POST', headers: { 'Content-Type': 'application/json', 'Accept': 'application/json' },
            body: JSON.stringify(collectOrg())
        }).then(function (r) { return r.ok ? r.json() : null; })
          .then(function (d) {
              if (!d) { toast('Could not save library organization', 'error'); return false; }
              _videoOrg = d;
              if (!silent) toast('Library organization saved', 'success');
              return true;
          })
          .catch(function () { toast('Could not save library organization', 'error'); return false; });
    }

    // Commit a template the CODE wrote into the box (a token chip, the TRaSH
    // preset). Those never fire 'change' — browsers raise it only for edits the
    // user typed — so the change-listener autosave below never ran for them and
    // the template was silently lost on the next page load, which is exactly
    // "the naming template doesn't save after leaving the page". Debounced, so
    // clicking five tokens in a row is one save rather than five.
    var _orgSaveTimer = null;
    function _commitOrgNow(message) {
        // collectOrg() reads EVERY field, so saving before the initial load has
        // populated the form would write the markup's blank toggles over real
        // settings. Wait for it rather than dropping the edit.
        if (!_videoOrg) { _orgSaveTimer = setTimeout(function () { _commitOrgNow(message); }, 200); return; }
        saveOrganization(true).then(function (ok) {
            if (ok) toast(message || 'Library organization saved', 'success');
        });
    }
    function commitTemplateEdit(message) {
        _orgEditSeq++;
        renderOrgPreview();
        if (_orgSaveTimer) clearTimeout(_orgSaveTimer);
        _orgSaveTimer = setTimeout(function () { _commitOrgNow(message); }, 400);
    }
    function wireOrganization() {
        var anchor = document.getElementById('vo-movie-template');
        if (!anchor) return;
        var card = anchor.closest('.settings-group');
        if (!card || card._voWired) return;
        card._voWired = true;
        ['vo-movie-template', 'vo-episode-template', 'vo-youtube-template'].forEach(function (id) {
            var el = document.getElementById(id);
            if (!el) return;
            // 'input' marks the box edited so an in-flight load can't paint over
            // what is being typed; 'change' (blur) commits it.
            el.addEventListener('input', function () { _orgEditSeq++; renderOrgPreview(); });
            el.addEventListener('change', function () { commitTemplateEdit(); });
        });
        ['vo-transfer-mode', 'vo-verify', 'vo-replace', 'vo-subs', 'vo-artwork', 'vo-nfo',
            'vo-subs-dl', 'vo-sub-langs', 'vo-recycle', 'vo-recycle-days', 'vo-recycle-path',
            'vo-sponsorblock', 'vo-yt-subs', 'vo-min-free', 'vo-yt-follow-count'].forEach(function (id) {
            var el = document.getElementById(id);
            if (el) el.addEventListener('change', function () { saveOrganization(false); });
        });
        // Token reference, click-to-insert, and the TRaSH preset. Delegated so the
        // token chips (rendered on demand) need no wiring of their own.
        card.addEventListener('click', function (e) {
            var show = e.target.closest('[data-vo-tokens]');
            if (show) { _toggleTokenList(show.getAttribute('data-vo-tokens')); return; }
            var chip = e.target.closest('[data-vo-insert]');
            if (chip) {
                var box = document.getElementById('vo-' + chip.getAttribute('data-vo-insert') + '-template');
                if (box) {
                    // Insert at the caret, not at the end — a token belongs where
                    // the user was typing.
                    var at = box.selectionStart == null ? box.value.length : box.selectionStart;
                    var end = box.selectionEnd == null ? at : box.selectionEnd;
                    box.value = box.value.slice(0, at) + chip.textContent + box.value.slice(end);
                    box.focus();
                    box.selectionStart = box.selectionEnd = at + chip.textContent.length;
                    commitTemplateEdit();
                }
                return;
            }
            var preset = e.target.closest('[data-vo-preset]');
            if (preset) {
                var scope = preset.getAttribute('data-vo-preset');
                var target = document.getElementById('vo-' + scope + '-template');
                if (target && _TRASH_PRESETS[scope]) {
                    target.value = _TRASH_PRESETS[scope];
                    // Saved, like every other edit on this card. It used to be
                    // left for the user to "commit by clicking away", which was
                    // advice that could not work: a value the code wrote raises
                    // no 'change', so clicking away saved nothing and the scheme
                    // vanished on the next page load.
                    commitTemplateEdit('TRaSH scheme applied — see the example below');
                }
            }
        });
        var reset = document.getElementById('vo-reset');
        if (reset) reset.addEventListener('click', function () {
            // POST *only* the blank templates. normalize() starts from DEFAULTS and
            // overrides solely the keys present in the body, so an absent key IS the
            // default — restating the toggles here only creates a second copy of the
            // defaults that drifts (it did: save_artwork/write_nfo were posted false
            // against a True default, so "reset" silently disabled the sidecars).
            // min_free_disk_gb is deliberately omitted for a second reason: it is the
            // app-wide settings.min_free_disk_gb shared with music, and save() rewrites
            // that shared key whenever the body carries it — resetting the video naming
            // card must not drop music's disk floor to 0.
            fetch(ORG_URL, {
                method: 'POST', headers: { 'Content-Type': 'application/json', 'Accept': 'application/json' },
                body: JSON.stringify({ movie_template: '', episode_template: '', youtube_template: '' })
            }).then(function (r) { return r.ok ? r.json() : null; })
              .then(function (d) { if (d) { _videoOrg = d; fillOrg(); toast('Reset to the standard layout', 'success'); } });
        });
    }

    function onPageShown(e) {
        if (e && e.detail !== PAGE_ID) return;
        loadServer();
        loadConn();
        load();
        loadKeys();
        loadDownloads();
        loadImportLists();
        loadNotify();
        wireDownloads();
        loadQuality();
        wireQuality();
        loadYtQuality();
        wireYtQuality();
        loadSlskd();
        wireSlskd();
        loadOrganization();
        wireOrganization();
    }

    function init() {
        // Save on a checkbox toggle OR a label/path edit committing (text inputs'
        // native 'change' fires on blur, never per keystroke, so typing a path
        // never fires a save per character). Delegate from the containers (not
        // the rows) since they're rebuilt on every load().
        var groups = document.querySelectorAll('[data-video-lib-group]');
        for (var i = 0; i < groups.length; i++) {
            groups[i].addEventListener('change', function (e) {
                if (e.target && (e.target.type === 'checkbox' || e.target.type === 'text')) save();
            });
        }
        // Enrichment keys save on blur/change (turns the workers on).
        ['tmdb-api-key', 'tvdb-api-key', 'omdb-api-key',
            'fanart-api-key', 'opensubtitles-api-key', 'trakt-api-key',
            'video-ryd-enabled', 'video-sponsorblock-enabled', 'video-dearrow-enabled',
            'video-tvmaze-enabled', 'video-anilist-enabled',
            'video-wikidata-enabled'].forEach(function (id) {
            var el = document.getElementById(id);
            if (el) el.addEventListener('change', function () { saveKeys(); });
        });
        var autoplay = document.getElementById('video-billboard-autoplay');
        if (autoplay) autoplay.addEventListener('change', function () { savePrefs(); });
        var region = document.getElementById('video-watch-region');
        if (region) region.addEventListener('change', function () { savePrefs(); });
        // Server toggle (Plex/Jellyfin) — select + reveal that server's config.
        var toggles = document.querySelectorAll('[data-video-server-toggle]');
        for (var t = 0; t < toggles.length; t++) {
            (function (b) {
                b.addEventListener('click', function () {
                    pickServer(b.getAttribute('data-video-server-toggle'));
                });
            })(toggles[t]);
        }
        // Server Connection (video's own creds) — save on change, test on click.
        var connInputs = document.querySelectorAll('[data-video-conn]');
        for (var c = 0; c < connInputs.length; c++) {
            connInputs[c].addEventListener('change', function () { saveConn(); });
        }
        var connTests = document.querySelectorAll('[data-video-conn-test]');
        for (var d = 0; d < connTests.length; d++) {
            (function (b) {
                b.addEventListener('click', function () {
                    testConn(b.getAttribute('data-video-conn-test'));
                });
            })(connTests[d]);
        }
        // Jellyfin user pick → store it + refresh that user's libraries.
        var userSel = document.querySelector('[data-video-jellyfin-user]');
        if (userSel) userSel.addEventListener('change', function () { selectJellyfinUser(userSel.value); });
        // Per-connection Test buttons (same behaviour as music's testConnection).
        var testBtns = document.querySelectorAll('[data-video-test-service]');
        for (var k = 0; k < testBtns.length; k++) {
            (function (b) {
                b.addEventListener('click', function () {
                    testConnection(b.getAttribute('data-video-test-service'));
                });
            })(testBtns[k]);
        }
        // The shared "Save Settings" button belongs to MUSIC (it runs music's
        // saveSettings, which would fire a FULL music-config write from the video
        // page — including active_media_server). On the video side we intercept it
        // (capture phase, before music's bubble listener), block music's handler,
        // flush all video settings, and toast. Music side: this does nothing, so
        // its behaviour is unchanged.
        //
        // saveSharedSettings() covers the data-shared sections the video page
        // legitimately shows (Prowlarr, torrent/usenet client, appearance,
        // security, db workers). Those are music-config-backed, so before it
        // existed this intercept silently DISCARDED every edit to them — the
        // field worked on Music and did nothing on Video. It posts only those
        // sections, and never active_media_server, so it can't reach into the
        // music server pointer.
        document.addEventListener('click', function (e) {
            if (document.body.getAttribute('data-side') !== 'video') return;
            if (!e.target.closest('#save-settings')) return;
            e.preventDefault();
            e.stopImmediatePropagation();
            // Bare identifier, not a global-object lookup: this module attaches
            // nothing to the global scope and is asserted to stay that way by
            // test_video_settings_module_referenced_and_isolated. typeof on an
            // undeclared name is safe, so this degrades quietly if settings.js
            // ever stops shipping the helper.
            var shared = (typeof saveSharedSettings === 'function')
                ? saveSharedSettings(true)
                : Promise.resolve(true);
            Promise.all([saveConn(true), save(true), saveKeys(true), savePrefs(true),
                         saveDownloads(true), saveQuality(true), saveYtQuality(true), saveSlskd(true),
                         shared])
                .then(function () { toast('Settings saved', 'success'); })
                .catch(function () { toast('Some settings could not be saved', 'error'); });
        }, true);
        document.addEventListener('soulsync:video-page-shown', onPageShown);
    }

    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', init);
    } else {
        init();
    }
})();
