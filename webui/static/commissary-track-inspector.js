/*
 * Commissary — search every source for a track in the Download Missing Tracks
 * window (core/commissary/track_inspector.py).
 *
 * Every album (artist pages, Discover, the Search page) and every single track
 * from Search opens that window. Each row gets a compass button beside upstream's
 * play / add-to-playlist buttons (same modal-track-play-btn class); it opens
 * upstream's Candidate Inspector for that track, and a pick downloads exactly
 * that file. The button only renders once the inspector is mounted.
 */
(function () {
    'use strict';

    function trackFor(playlistId, trackIndex) {
        var process = (typeof activeDownloadProcesses !== 'undefined') ? activeDownloadProcesses[playlistId] : null;
        var cache = (typeof playlistTrackCache !== 'undefined') ? playlistTrackCache[playlistId] : null;
        var track = (process && process.tracks && process.tracks[trackIndex]) || (cache && cache[trackIndex]);
        if (!track) return null;
        var copy = Object.assign({}, track);
        // An artist page's album tracks often carry no album of their own; the
        // window knows which album it is, and tagging needs it.
        var album = process && process.album;
        var hasAlbum = copy.album && (typeof copy.album === 'string' || copy.album.name);
        if (!hasAlbum && album && album.name) copy.album = album;
        if ((!copy.artists || !copy.artists.length) && process && process.artist && process.artist.name) {
            copy.artists = [{ name: process.artist.name }];
        }
        return copy;
    }

    function inspectModalTrack(playlistId, trackIndex) {
        var track = trackFor(playlistId, trackIndex);
        if (!track) {
            if (typeof showToast === 'function') showToast('Track is no longer available in this window', 'error');
            return;
        }
        if (typeof window.openCommissaryTrackInspector !== 'function') {
            if (typeof showToast === 'function') showToast('Still loading, try again in a moment', 'error');
            return;
        }
        window.openCommissaryTrackInspector(track);
    }

    function inspectButton(playlistId, trackIndex) {
        if (typeof window.openCommissaryTrackInspector !== 'function') return '';
        var id = (typeof escapeForInlineJs === 'function') ? escapeForInlineJs(playlistId) : String(playlistId);
        return '<button class="modal-track-play-btn commissary-inspect-btn" onclick="event.stopPropagation(); '
            + 'commissaryInspectModalTrack(\'' + id + '\', ' + Number(trackIndex) + ')" '
            + 'title="Search every source" aria-label="Search every source">'
            + '<svg viewBox="0 0 24 24" width="13" height="13" aria-hidden="true"><path fill="currentColor" '
            + 'd="M12 2a10 10 0 1 0 0 20 10 10 0 0 0 0-20zm0 18a8 8 0 1 1 0-16 8 8 0 0 1 0 16zm-5.5-2.5 7.5-3.5 '
            + '3.5-7.5-7.5 3.5-3.5 7.5zm5.5-6.6a1.1 1.1 0 1 1 0 2.2 1.1 1.1 0 0 1 0-2.2z"/></svg></button>';
    }

    // A failed / not found / cancelled row's Actions cell: a Sources button
    // opening upstream's own search modal for that task (the same one a click
    // on the status cell opens - nothing showed that it could be clicked).
    function sourcesButton(task, quarantined) {
        if (!task || quarantined || !task.task_id) return '';
        if (['failed', 'not_found', 'cancelled'].indexOf(task.status) === -1) return '';
        if (typeof window.showCandidatesModal !== 'function') return '';
        var id = (typeof escapeForInlineJs === 'function') ? escapeForInlineJs(task.task_id) : String(task.task_id);
        return '<button class="approve-quarantine-inline-btn commissary-sources-btn" '
            + 'onclick="event.stopPropagation(); window.showCandidatesModal(\'' + id + '\')" '
            + 'title="Search the sources and pick a file by hand">Sources</button>';
    }

    window.commissaryInspectButton = inspectButton;
    window.commissarySourcesButton = sourcesButton;
    window.commissaryInspectModalTrack = inspectModalTrack;
})();
