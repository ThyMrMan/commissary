/**
 * Commissary: tell a manual video search WHICH title it is searching for.
 *
 * Upstream's search requests carry only scope/title/season/episode, so the
 * server could judge releases against nothing but the bare title: an alias, an
 * air-date name or an anime absolute number ('[SubsPlease] Show - 07') was
 * rejected exactly when a person searched by hand. The server now resolves the
 * full identity from a media_id + media_source PAIR
 * (core/commissary/search_identity.py); this adds that pair to the request.
 *
 * The pair must agree: 'tmdb' means media_id is a TMDB id, anything else a
 * movies/shows row id. Shows keep their state on the container's _dl (row id on
 * mediaId with its source, the TMDB id on tvId), movies on _opts; the manual
 * search overlay keeps it on its inner [data-vgm-dl-content] element.
 */
(function () {
    'use strict';

    function stateOf(container) {
        if (!container) return {};
        var o = container._opts || container._dl;
        if (!o && container.querySelector) {
            var inner = container.querySelector('[data-vgm-dl-content]');
            if (inner) o = inner._opts || inner._dl;
        }
        return o || {};
    }

    // The id together with the source that reads it — never one id with
    // another's source (a TMDB id labelled 'library' resolves to nothing).
    function mediaRef(o) {
        o = o || {};
        if (o.mediaId != null) return { id: o.mediaId, source: o.mediaSource || o.source || 'library' };
        if (o.id != null) return { id: o.id, source: o.source || 'library' };
        if (o.tvId != null) return { id: o.tvId, source: 'tmdb' };
        return null;
    }

    function apply(container, params) {
        if (!params || params.media_id != null) return params;
        var ref = mediaRef(stateOf(container));
        if (ref) {
            params.media_id = ref.id;
            params.media_source = ref.source;
        }
        return params;
    }

    window.CommissarySearchIdentity = { apply: apply, mediaRef: mediaRef, stateOf: stateOf };
})();
