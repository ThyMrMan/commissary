/*
 * Commissary — Fix Match in the Discovery Pool and the Wing It Pool searches
 * every metadata source, not only Spotify (old fork 2.3.9).
 *
 * The pools' Fix dialog asked /api/spotify/search_tracks alone, which answers
 * 401 without a Spotify connection - so an iTunes or Deezer user could never
 * fix a pool entry. This tries the same sources, in the same order, as the
 * Sync page's React Fix dialog (webui/src/routes/sync/-sync.fix.ts): the active
 * metadata source first, then Spotify, Deezer, iTunes, MusicBrainz; a source
 * that fails is skipped and the first one with results answers.
 */
(function () {
    'use strict';
    var SOURCES = [
        { key: 'spotify', endpoint: '/api/spotify/search_tracks', label: 'Spotify' },
        { key: 'deezer', endpoint: '/api/deezer/search_tracks', label: 'Deezer' },
        { key: 'itunes', endpoint: '/api/itunes/search_tracks', label: 'iTunes' },
        { key: 'musicbrainz', endpoint: '/api/musicbrainz/search_tracks', label: 'MusicBrainz' },
    ];

    function activeKey() {
        try {
            var source = typeof getActiveMetadataSource === 'function' ? getActiveMetadataSource() : '';
            return String(source || '').toLowerCase();
        } catch (e) {
            return '';
        }
    }

    function ordered() {
        var active = activeKey();
        var first = SOURCES.filter(function (s) { return active && active.indexOf(s.key) !== -1; });
        return first.concat(SOURCES.filter(function (s) { return first.indexOf(s) === -1; }));
    }

    /**
     * Search every source in turn. Resolves to {tracks, source} on the first
     * source with results, {tracks: [], errors} when none had any.
     */
    async function search(params) {
        var errors = [];
        var sources = ordered();
        for (var i = 0; i < sources.length; i++) {
            try {
                var res = await fetch(sources[i].endpoint + '?' + params.toString());
                var data = await res.json().catch(function () { return {}; });
                if (!res.ok || data.error) {
                    errors.push(sources[i].label + ': ' + (data.error || res.statusText || res.status));
                    continue;
                }
                if ((data.tracks || []).length) return { tracks: data.tracks, source: sources[i].label };
            } catch (err) {
                errors.push(sources[i].label + ': ' + (err && err.message ? err.message : err));
            }
        }
        return { tracks: [], errors: errors };
    }

    window.commissaryPoolFixSearch = search;
})();
