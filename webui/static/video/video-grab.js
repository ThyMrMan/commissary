/*
 * Commissary — headless video acquisition helper (VideoGrab).
 *
 * Powers the TV detail page's inline per-episode / per-season buttons. Runs the
 * SAME backend contract the download modal uses — search/start → poll →
 * pick the best accepted release → /downloads/grab — but with NO results UI:
 * it auto-grabs the best hit and resolves, leaving the caller to render its own
 * row status.
 *
 * The modal keeps its own interactive, results-streaming grab; both hit the same
 * endpoints. Kept separate for now so wiring the detail page can't regress the
 * working modal — a later cleanup could fold the modal onto this core.
 *
 *   VideoGrab.episode({ title, source, season, episode, mediaId, mediaSource, year, poster, rootFolderId })
 *       → Promise<{ ok:boolean, id?:string, error?:string }>
 *   VideoGrab.season({ title, source, season, episodes:[num...], mediaId, mediaSource, year, poster, rootFolderId }, onEp)
 *       // onEp(episodeNumber, 'searching' | 'grabbing' | 'none') fired per episode
 *       → Promise<{ grabbed:number, total:number }>
 *   VideoGrab.wishlistEpisodes(show, episodes) → Promise<boolean>
 *   VideoGrab.pickSource() → Promise<string>
 */
(function () {
    'use strict';

    function postJSON(url, body) {
        // Parse the body even on a non-2xx status — the backend's {ok:false, error:'…'}
        // detail lives there too; discarding it on !r.ok left every failure falling
        // back to the generic 'grab failed' text instead of the real reason.
        return fetch(url, { method: 'POST', headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(body) }).then(function (r) { return r.json().catch(function () { return null; }); })
            .catch(function () { return null; });
    }
    function getJSON(url) {
        return fetch(url).then(function (r) { return r.ok ? r.json() : null; }).catch(function () { return null; });
    }

    // First configured download source — mirrors the modal's sourcesFromConfig.
    var _srcCache;
    function pickSource() {
        if (_srcCache !== undefined) return Promise.resolve(_srcCache);
        return getJSON('/api/video/downloads/config').then(function (c) {
            c = c || {};
            var list;
            if (c.download_mode === 'hybrid' && Array.isArray(c.hybrid_order) && c.hybrid_order.length) list = c.hybrid_order;
            else if (c.download_mode) list = [c.download_mode];
            else list = ['soulseek'];
            _srcCache = list[0] || 'soulseek';
            return _srcCache;
        });
    }

    // search/start → poll (until the results plateau) → return the accepted rows.
    // Mirrors the modal's _pollSearch heuristic, minus the DOM streaming.
    function runSearch(params) {
        return postJSON('/api/video/downloads/search/start', params).then(function (d) {
            if (!d || d.error) return [];
            if (!d.id) return d.results || [];   // immediate / mock
            return pollSearch(d.id, params, d.poll_ms);
        });
    }
    function pollSearch(id, params, pollMs) {
        return new Promise(function (resolve) {
            var started = Date.now(), lastN = -1, stable = 0;
            var MAX_MS = Math.min(80000, pollMs || 60000);
            function tick() {
                var qs = '?id=' + encodeURIComponent(id) + '&scope=' + encodeURIComponent(params.scope || 'episode') +
                    '&title=' + encodeURIComponent(params.title || '') +
                    (params.season != null ? '&season=' + params.season : '') +
                    (params.episode != null ? '&episode=' + params.episode : '');
                getJSON('/api/video/downloads/search/poll' + qs).then(function (d) {
                    var rows = (d && d.results) || [];
                    if (rows.length === lastN) { stable++; } else { stable = 0; lastN = rows.length; }
                    var elapsed = Date.now() - started;
                    var done = elapsed >= MAX_MS || rows.length >= 25 || (rows.length > 0 && elapsed > 20000 && stable >= 6);
                    if (done) resolve(rows);
                    else setTimeout(tick, 1500);
                });
            }
            tick();
        });
    }

    function bestRow(rows) {
        for (var i = 0; i < rows.length; i++) {
            if (rows[i] && rows[i].accepted && rows[i].username) return rows[i];
        }
        return null;
    }

    // Auto-grab one episode: search → pick the best accepted release → grab it.
    function episode(opts) {
        opts = opts || {};
        var src = opts.source || 'soulseek';
        // rootFolderId rides along so the grab lands in the show's OWN Library
        // (an Anime show keeps its anime folder/category instead of falling back
        // to the primary TV Library) and so the search gets that Library's
        // preferred trackers. The backend re-derives it from mediaId when absent,
        // so an older caller that omits it still routes correctly.
        var params = { scope: 'episode', title: opts.title, season: opts.season,
            episode: opts.episode, source: src, root_folder_id: opts.rootFolderId || null,
            media_id: opts.mediaId, media_source: opts.mediaSource };
        return runSearch(params).then(function (rows) {
            var best = bestRow(rows);
            if (!best) return { ok: false, error: 'no release found' };
            var payload = {
                kind: 'show', title: opts.title, release_title: best.title,
                source: src, size_bytes: best.size_bytes, quality_label: best.quality_label,
                media_id: opts.mediaId, media_source: opts.mediaSource, year: opts.year, poster_url: opts.poster,
                root_folder_id: opts.rootFolderId || null,
                search_ctx: { scope: 'episode', title: opts.title, year: opts.year,
                    season: opts.season, episode: opts.episode }
            };
            if (src === 'soulseek') {
                // The other accepted hits become the auto-retry pool (same as the modal).
                payload.username = best.username; payload.filename = best.filename;
                payload.candidates = rows.filter(function (x) { return x.accepted && x.username && x.filename !== best.filename; })
                    .map(function (x) { return { username: x.username, filename: x.filename, size_bytes: x.size_bytes,
                        quality_label: x.quality_label, title: x.title }; });
            } else {
                // torrent / usenet — the magnet/NZB carriers the backend hands to the client
                payload.download_url = best.download_url; payload.protocol = best.protocol;
                payload.indexer_id = best.indexer_id; payload.guid = best.guid;
                payload.username = best.username; payload.filename = best.filename || best.title;
                payload.candidates = [];
            }
            return postJSON('/api/video/downloads/grab', payload).then(function (res) {
                if (res && res.ok) {
                    document.dispatchEvent(new CustomEvent('soulsync:video-download-started'));
                    return { ok: true, id: res.id };
                }
                return { ok: false, error: (res && res.error) || 'grab failed' };
            });
        });
    }

    // Grab a season as ONE PACK: a single release covering the whole season,
    // which the import fans out per episode (run_season_import) exactly the way
    // the automation does.
    //
    // This used to run the per-episode auto-grab N times. That is N searches and
    // N grabs for one season, it hammers the indexers, and it routinely assembles
    // a season from a dozen unrelated releases at different qualities.
    //
    // Packs ONLY, deliberately: when no pack exists it reports that instead of
    // quietly reverting to per-episode grabbing. Auto on each episode row still
    // does exactly that, and a button doing something other than what it says is
    // worse than a button that declines.
    function season(opts, onEp) {
        opts = opts || {};
        var eps = (opts.episodes || []).slice().sort(function (a, b) { return a - b; });
        var total = eps.length;
        if (!total) return Promise.resolve({ grabbed: 0, total: 0, pack: false });
        var src = opts.source || 'soulseek';
        if (onEp) eps.forEach(function (en) { onEp(en, 'searching'); });

        var params = { scope: 'season', title: opts.title, season: opts.season, source: src,
            root_folder_id: opts.rootFolderId || null,
            media_id: opts.mediaId, media_source: opts.mediaSource };
        return runSearch(params).then(function (rows) {
            var best = bestRow(rows);
            if (!best) {
                if (onEp) eps.forEach(function (en) { onEp(en, 'none'); });
                return { grabbed: 0, total: total, pack: false, error: 'no season pack found' };
            }
            // A soulseek pack is a FOLDER of files (grab-pack fans it out
            // server-side); a torrent/usenet pack is one release the download
            // monitor unpacks on completion.
            if (src === 'soulseek' && best.files && best.files.length > 1) {
                return postJSON('/api/video/downloads/grab-pack', {
                    username: best.username, files: best.files, title: opts.title,
                    quality_label: best.quality_label, media_id: opts.mediaId,
                    media_source: opts.mediaSource, year: opts.year, poster_url: opts.poster,
                    root_folder_id: opts.rootFolderId || null,
                }).then(function (res) {
                    var ok = !!(res && res.ok);
                    if (onEp) eps.forEach(function (en) { onEp(en, ok ? 'grabbing' : 'none'); });
                    if (ok) document.dispatchEvent(new CustomEvent('soulsync:video-download-started'));
                    return { grabbed: ok ? (res.started || total) : 0, total: total, pack: true,
                             error: ok ? null : ((res && res.error) || 'grab failed') };
                });
            }
            var payload = {
                kind: 'show', title: opts.title, release_title: best.title,
                source: src, size_bytes: best.size_bytes, quality_label: best.quality_label,
                media_id: opts.mediaId, media_source: opts.mediaSource, year: opts.year,
                poster_url: opts.poster, root_folder_id: opts.rootFolderId || null,
                // scope 'season' is what makes the monitor treat the finished
                // download as a pack and unpack it (_is_pack → run_season_import).
                search_ctx: { scope: 'season', title: opts.title, year: opts.year,
                    season: opts.season, episode: null }
            };
            if (src === 'soulseek') {
                payload.username = best.username; payload.filename = best.filename;
                payload.candidates = [];
            } else {
                payload.download_url = best.download_url; payload.protocol = best.protocol;
                payload.indexer_id = best.indexer_id; payload.guid = best.guid;
                payload.username = best.username; payload.filename = best.filename || best.title;
                payload.candidates = [];
            }
            return postJSON('/api/video/downloads/grab', payload).then(function (res) {
                var ok = !!(res && res.ok);
                if (onEp) eps.forEach(function (en) { onEp(en, ok ? 'grabbing' : 'none'); });
                if (ok) document.dispatchEvent(new CustomEvent('soulsync:video-download-started'));
                return { grabbed: ok ? total : 0, total: total, pack: true,
                         error: ok ? null : ((res && res.error) || 'grab failed') };
            });
        });
    }

    // Wishlist a set of episodes (episode = 1 item, season = N items).
    function wishlistEpisodes(show, episodes) {
        if (!show || !show.tmdb_id || !episodes || !episodes.length) return Promise.resolve(false);
        return postJSON('/api/video/wishlist/add', { show: show, episodes: episodes }).then(function (d) {
            var ok = !!(d && d.success);
            if (ok) document.dispatchEvent(new CustomEvent('soulsync:video-wishlist-changed'));
            return ok;
        });
    }

    window.VideoGrab = { episode: episode, season: season,
        wishlistEpisodes: wishlistEpisodes, pickSource: pickSource };
})();
