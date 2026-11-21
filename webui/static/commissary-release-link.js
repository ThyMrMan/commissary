/* Commissary: a manual-search release title links to its page at the source
 * (the indexer's or EXT.to's details page), as the legacy fork did (1.8.6).
 *
 * The server sends `source_url` only after core/commissary/tracker_link.py
 * checked it (http(s), no download link, no indexer key); it is checked for
 * http(s) again here because it ends up as an href. A result without one
 * (Soulseek, an indexer that gives no page) keeps upstream's plain title.
 *
 * Hooked from webui/static/video/video-download-view.js (two title spans).
 */
(function () {
    'use strict';

    function esc(v) {
        return String(v == null ? '' : v)
            .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
            .replace(/"/g, '&quot;').replace(/'/g, '&#39;');
    }

    function safeUrl(v) {
        var s = String(v || '').trim();
        return /^https?:\/\/[^\s]+$/i.test(s) ? s : '';
    }

    function titleHTML(r) {
        var title = esc(r && r.title);
        var url = safeUrl(r && r.source_url);
        if (!url) return '<span class="vdl-r-title" title="' + title + '">' + title + '</span>';
        return '<a class="vdl-r-title commissary-release-link" href="' + esc(url) +
            '" target="_blank" rel="noopener noreferrer" title="' + title +
            ' (opens its page at the source)">' + title + '</a>';
    }

    // a click on the link opens the page only: the season-pack header around it
    // toggles on click, and the view's own handlers listen on the container
    document.addEventListener('click', function (e) {
        var a = e.target && e.target.closest && e.target.closest('a.commissary-release-link');
        if (a) e.stopPropagation();
    }, true);

    var css = document.createElement('style');
    css.textContent =
        'a.commissary-release-link { color: inherit; text-decoration: none; cursor: pointer; }' +
        'a.commissary-release-link:hover, a.commissary-release-link:focus-visible { text-decoration: underline; }';
    (document.head || document.documentElement).appendChild(css);

    globalThis.CommissaryReleaseLink = { titleHTML: titleHTML, safeUrl: safeUrl };
})();
