/*
 * Commissary — choose which release of an album downloads
 * (core/commissary/release_picker.py).
 *
 * The album Download Missing Tracks window gets a "Choose Release" button beside
 * Begin Analysis. It asks every source that downloads whole albums (torrent,
 * usenet, Soulseek folders) what it has, side by side, and starts the download
 * with the one you pick: the batch carries the pick, and the album flow takes
 * exactly that release instead of choosing one itself. "Let SoulSync choose"
 * is Begin Analysis. Built on upstream's candidates-modal classes.
 */
(function () {
    'use strict';
    var OVERLAY_ID = 'commissary-release-overlay';

    function esc(s) {
        return String(s == null ? '' : s).replace(/&/g, '&amp;').replace(/</g, '&lt;')
            .replace(/>/g, '&gt;').replace(/"/g, '&quot;');
    }
    function toast(msg, type) { if (typeof showToast === 'function') showToast(msg, type); }
    function size(bytes) {
        if (!bytes) return '-';
        var gb = bytes / 1073741824;
        return gb >= 1 ? gb.toFixed(2) + ' GB' : (bytes / 1048576).toFixed(0) + ' MB';
    }
    function isAlbum(playlistId) {
        return typeof isAlbumContextPlaylistId === 'function' && isAlbumContextPlaylistId(playlistId);
    }
    function processFor(playlistId) {
        return (typeof activeDownloadProcesses !== 'undefined') ? activeDownloadProcesses[playlistId] : null;
    }

    function chooseReleaseButton(playlistId) {
        if (!isAlbum(playlistId)) return '';
        var id = (typeof escapeForInlineJs === 'function') ? escapeForInlineJs(playlistId) : String(playlistId);
        return '<button class="download-control-btn secondary commissary-choose-release-btn" '
            + 'id="commissary-choose-release-' + esc(playlistId) + '" '
            + 'onclick="commissaryOpenReleasePicker(\'' + id + '\')" '
            + 'title="See every whole-album release your sources have and pick the one to download">'
            + '💿 Choose Release</button>';
    }

    function close() {
        var overlay = document.getElementById(OVERLAY_ID);
        if (overlay) overlay.remove();
        if (window._commissaryReleaseAbort) {
            try { window._commissaryReleaseAbort.abort(); } catch (e) { /* already done */ }
            window._commissaryReleaseAbort = null;
        }
    }

    function row(rel, key) {
        var detail = rel.source === 'soulseek'
            ? (rel.track_count ? rel.track_count + ' tracks' : '') + (rel.username ? ' · ' + esc(rel.username) : '')
            : (rel.seeders != null ? rel.seeders + ' seeders' : (rel.grabs != null ? rel.grabs + ' grabs' : ''))
              + (rel.indexer ? ' · ' + esc(rel.indexer) : '');
        var badge = rel.quality
            ? '<span class="candidates-quality-badge candidates-quality-' + esc(String(rel.quality).toLowerCase()) + '">'
              + esc(String(rel.quality).toUpperCase()) + '</span>'
            : '';
        var auto = rel.auto_pick ? '<span class="candidates-source-badge" title="What SoulSync would pick">auto</span> ' : '';
        return '<tr class="commissary-release-row">'
            // wraps: a release name's tail ("(2016) [FLAC 24-96]") is what tells them apart
            + '<td class="candidates-col-file" style="white-space:normal;overflow-wrap:anywhere" title="'
            + esc(rel.folder_path || rel.title) + '">' + auto + esc(rel.title) + '</td>'
            + '<td class="candidates-col-quality">' + badge + '</td>'
            + '<td class="candidates-col-size">' + size(rel.size) + '</td>'
            + '<td class="candidates-col-user">' + detail + '</td>'
            + '<td class="candidates-col-action"><button class="candidates-download-btn" data-release="' + key
            + '" title="Download this release">⬇</button></td></tr>';
    }

    function pinOf(rel) {
        if (rel.source === 'soulseek') {
            return { source: 'soulseek', username: rel.username, folder_path: rel.folder_path, title: rel.title };
        }
        return { source: rel.source, token: rel.token, title: rel.title };
    }

    function start(playlistId, pin) {
        var process = processFor(playlistId);
        if (!process) { toast('This album window is gone - open it again', 'error'); return; }
        if (process.status && process.status !== 'idle') {
            toast('This album is already downloading', 'error');
            return;
        }
        if (pin) process.commissaryPinnedRelease = pin;
        else delete process.commissaryPinnedRelease;
        close();
        if (typeof startMissingTracksProcess === 'function') startMissingTracksProcess(playlistId);
    }

    function open(playlistId) {
        var process = processFor(playlistId);
        var album = process && process.album && process.album.name;
        var artist = process && process.artist && process.artist.name;
        if (!album || !artist) { toast('No album to choose a release for', 'error'); return; }
        if (process.status && process.status !== 'idle') { toast('This album is already downloading', 'error'); return; }
        close();

        var releases = [];
        var overlay = document.createElement('div');
        overlay.id = OVERLAY_ID;
        overlay.className = 'candidates-modal-overlay';
        overlay.onclick = function (e) { if (e.target === overlay) close(); };
        overlay.innerHTML = '<div class="candidates-modal">'
            + '<div class="candidates-modal-header"><div>'
            + '<h2 class="candidates-modal-title">Choose Release</h2>'
            + '<div class="candidates-modal-subtitle">' + esc(album) + ' — ' + esc(artist) + '</div></div>'
            + '<button class="candidates-modal-close" data-commissary-close>&#x2715;</button></div>'
            + '<div class="candidates-modal-body">'
            + '<div class="candidates-count" data-commissary-status>Asking your sources for whole-album releases…</div>'
            + '<div class="candidates-table-wrapper"><table class="candidates-table"><thead><tr>'
            + '<th>Release</th><th>Quality</th><th>Size</th><th>Details</th><th></th></tr></thead>'
            + '<tbody data-commissary-rows></tbody></table></div>'
            + '<div class="candidates-manual-search-hint">The whole release downloads; only this album\'s missing tracks are kept.</div>'
            + '<div style="display:flex;justify-content:flex-end;margin-top:12px">'
            + '<button class="download-control-btn primary" data-commissary-auto>Let SoulSync choose</button></div>'
            + '</div></div>';
        document.body.appendChild(overlay);
        requestAnimationFrame(function () { overlay.classList.add('visible'); });

        var tbody = overlay.querySelector('[data-commissary-rows]');
        var status = overlay.querySelector('[data-commissary-status]');
        overlay.querySelector('[data-commissary-close]').onclick = close;
        overlay.querySelector('[data-commissary-auto]').onclick = function () { start(playlistId, null); };
        tbody.addEventListener('click', function (e) {
            var btn = e.target.closest('[data-release]');
            if (btn) start(playlistId, pinOf(releases[Number(btn.getAttribute('data-release'))]));
        });

        function addSource(line) {
            var html = '<tr class="commissary-release-source"><td colspan="5" class="candidates-manual-search-header">'
                + esc(line.label || line.source) + '</td></tr>';
            if (line.error) {
                html += '<tr><td colspan="5" class="candidates-manual-search-error">' + esc(line.error) + '</td></tr>';
            } else if (!line.releases || !line.releases.length) {
                html += '<tr><td colspan="5" class="candidates-manual-search-empty">No whole-album releases</td></tr>';
            } else {
                line.releases.forEach(function (rel) { html += row(rel, releases.push(rel) - 1); });
            }
            tbody.insertAdjacentHTML('beforeend', html);
        }

        var abort = new AbortController();
        window._commissaryReleaseAbort = abort;
        fetch('/api/commissary/album-releases', {
            method: 'POST', headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ album: album, artist: artist }), signal: abort.signal,
        }).then(function (resp) {
            if (!resp.ok || !resp.body) {
                return resp.json().catch(function () { return {}; }).then(function (d) {
                    throw new Error(d.error || ('Request failed (' + resp.status + ')'));
                });
            }
            var reader = resp.body.getReader();
            var decoder = new TextDecoder();
            var buffer = '';
            function pump() {
                return reader.read().then(function (chunk) {
                    if (chunk.done) return;
                    buffer += decoder.decode(chunk.value, { stream: true });
                    var lines = buffer.split('\n');
                    buffer = lines.pop();
                    lines.forEach(function (text) {
                        if (!text.trim()) return;
                        var line = JSON.parse(text);
                        if (line.type === 'header' && !line.sources.length) {
                            status.textContent = 'None of your download sources fetches whole albums (torrent, usenet or Soulseek).';
                        } else if (line.type === 'source') {
                            addSource(line);
                        } else if (line.type === 'done') {
                            status.textContent = releases.length + ' release' + (releases.length === 1 ? '' : 's') + ' found';
                        }
                    });
                    return pump();
                });
            }
            return pump();
        }).catch(function (err) {
            if (err && err.name === 'AbortError') return;
            status.textContent = 'Could not list releases: ' + (err && err.message ? err.message : err);
        });
    }

    window.commissaryChooseReleaseButton = chooseReleaseButton;
    window.commissaryOpenReleasePicker = open;
    window.commissaryCloseReleasePicker = close;
})();
