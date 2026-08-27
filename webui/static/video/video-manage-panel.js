/*
 * Commissary — Manage panel (per-item metadata editor).
 *
 *   VideoManage.open({kind, id})  — from "Manage" on a movie/show detail page.
 *
 * A right-hand slide-over: title / sort title / year / content rating / genres /
 * tagline / summary, plus watched + monitored toggles and a poster shortcut.
 * Saving PUTs /api/video/detail/<kind>/<id>/metadata — the edit is written
 * locally, pushed to Plex/Jellyfin (with the server's own field locks set) and
 * LOCKED here: scans and metadata refreshes won't overwrite it. Locked fields
 * wear a small badge; clicking it releases the field back to the server.
 * Self-contained (own styles), mirrors the poster-manager module pattern.
 */
(function () {
    'use strict';

    function esc(s) {
        return String(s == null ? '' : s)
            .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;');
    }
    function toast(msg, type) { if (typeof showToast === 'function') showToast(msg, type); }
    function confirmDlg(opts) {
        if (typeof showConfirmDialog === 'function') return showConfirmDialog(opts);
        return Promise.resolve(true);   // headless fallback (never window.confirm)
    }

    var RATING_HINTS = {
        movie: ['G', 'PG', 'PG-13', 'R', 'NC-17', 'NR'],
        show: ['TV-Y', 'TV-Y7', 'TV-G', 'TV-PG', 'TV-14', 'TV-MA'],
    };
    var LOCK_TIP = 'Yours — scans and metadata refreshes won’t change it. Click to release.';

    // ── one-time styles ──────────────────────────────────────────────────────
    function ensureStyles() {
        if (document.getElementById('vmg-styles')) return;
        var A = 'var(--accent-rgb, 88 101 242)';
        var css =
            '.vmg-overlay{position:fixed;inset:0;z-index:9100;background:rgba(5,5,8,.55);backdrop-filter:blur(4px);' +
                'opacity:0;transition:opacity .22s ease;}' +
            '.vmg-overlay.vmg-open{opacity:1;}' +
            '.vmg-panel{position:absolute;top:0;right:0;bottom:0;width:min(430px,calc(100vw - 20px));display:flex;' +
                'flex-direction:column;background:#101015;border-left:1px solid rgba(255,255,255,.09);' +
                'box-shadow:-40px 0 110px rgba(0,0,0,.6);transform:translateX(26px);opacity:.6;' +
                'transition:transform .26s cubic-bezier(.2,.7,.2,1),opacity .2s ease;}' +
            '.vmg-open .vmg-panel{transform:none;opacity:1;}' +
            // header
            '.vmg-head{padding:22px 24px 16px;border-bottom:1px solid rgba(255,255,255,.07);position:relative;}' +
            '.vmg-kick{display:flex;align-items:center;gap:8px;font-size:11px;font-weight:800;text-transform:uppercase;' +
                'letter-spacing:.09em;color:rgb(' + A + ');}' +
            '.vmg-kick-dot{width:7px;height:7px;border-radius:50%;background:rgb(' + A + ');box-shadow:0 0 10px rgb(' + A + ');}' +
            '.vmg-title{font-size:20px;font-weight:900;letter-spacing:-.02em;color:#fff;margin:8px 0 2px;' +
                'white-space:nowrap;overflow:hidden;text-overflow:ellipsis;padding-right:150px;}' +
            '.vmg-sub{font-size:12.5px;color:rgba(255,255,255,.5);line-height:1.5;}' +
            '.vmg-close{position:absolute;top:18px;right:18px;width:34px;height:34px;border-radius:50%;' +
                'border:1px solid rgba(255,255,255,.16);background:rgba(0,0,0,.4);color:#fff;font-size:20px;line-height:1;' +
                'cursor:pointer;display:flex;align-items:center;justify-content:center;transition:all .15s;}' +
            '.vmg-close:hover{background:rgba(0,0,0,.7);border-color:rgba(255,255,255,.36);}' +
            // body
            '.vmg-body{flex:1;overflow-y:auto;padding:18px 24px 22px;display:flex;flex-direction:column;gap:16px;}' +
            '.vmg-body::-webkit-scrollbar{width:8px;}.vmg-body::-webkit-scrollbar-thumb{background:rgba(255,255,255,.12);border-radius:4px;}' +
            '.vmg-sect{font-size:11px;font-weight:800;text-transform:uppercase;letter-spacing:.07em;' +
                'color:rgba(255,255,255,.42);margin:6px 0 -8px;}' +
            '.vmg-field{display:flex;flex-direction:column;gap:6px;min-width:0;}' +
            '.vmg-aka{resize:vertical;min-height:52px;line-height:1.4;}' +
            '.vmg-hint{font-size:11.5px;line-height:1.4;color:rgba(255,255,255,.42);}' +
            '.vmg-field [data-vmg-aka-save]{align-self:flex-start;}' +
            '.vmg-label{display:flex;align-items:center;gap:8px;font-size:12px;font-weight:700;color:rgba(255,255,255,.6);}' +
            '.vmg-row2{display:grid;grid-template-columns:1fr 1fr;gap:12px;}' +
            '.vmg-input,.vmg-area{width:100%;box-sizing:border-box;padding:10px 12px;border-radius:10px;font-size:13.5px;' +
                'font-family:inherit;background:rgba(255,255,255,.05);border:1px solid rgba(255,255,255,.1);color:#eef1f7;' +
                'outline:none;transition:border .15s,box-shadow .15s;}' +
            '.vmg-input:focus,.vmg-area:focus{border-color:rgba(' + A + ',.6);box-shadow:0 0 0 3px rgba(' + A + ',.14);}' +
            '.vmg-area{resize:vertical;min-height:104px;line-height:1.55;}' +
            // lock badge
            '.vmg-lock{display:inline-flex;align-items:center;gap:5px;padding:2px 9px;border-radius:999px;cursor:pointer;' +
                'font-size:9.5px;font-weight:800;text-transform:uppercase;letter-spacing:.05em;border:1px solid rgba(' + A + ',.45);' +
                'background:rgba(' + A + ',.16);color:rgb(' + A + ');transition:all .13s;}' +
            '.vmg-lock:hover{background:rgba(' + A + ',.3);}' +
            '.vmg-lock svg{width:9px;height:11px;fill:currentColor;}' +
            // genres
            '.vmg-chips{display:flex;flex-wrap:wrap;gap:7px;align-items:center;padding:9px 10px;border-radius:10px;' +
                'background:rgba(255,255,255,.05);border:1px solid rgba(255,255,255,.1);min-height:22px;}' +
            '.vmg-chips:focus-within{border-color:rgba(' + A + ',.6);box-shadow:0 0 0 3px rgba(' + A + ',.14);}' +
            '.vmg-chip{display:inline-flex;align-items:center;gap:6px;padding:3px 10px;border-radius:999px;font-size:12px;' +
                'font-weight:700;background:rgba(' + A + ',.16);border:1px solid rgba(' + A + ',.4);color:#eef1f7;}' +
            '.vmg-chip button{all:unset;cursor:pointer;font-size:13px;line-height:1;color:rgba(255,255,255,.55);}' +
            '.vmg-chip button:hover{color:#fff;}' +
            '.vmg-chip-in{flex:1;min-width:90px;background:none;border:none;outline:none;color:#eef1f7;' +
                'font-size:12.5px;font-family:inherit;padding:3px 2px;}' +
            // poster + toggles
            '.vmg-poster-row{display:flex;align-items:center;gap:14px;padding:12px;border-radius:12px;' +
                'background:rgba(255,255,255,.035);border:1px solid rgba(255,255,255,.07);}' +
            '.vmg-poster-img{width:52px;aspect-ratio:2/3;border-radius:7px;object-fit:cover;background:#1b1b22;flex:0 0 auto;}' +
            '.vmg-poster-txt{flex:1;min-width:0;font-size:12.5px;color:rgba(255,255,255,.55);line-height:1.45;}' +
            '.vmg-btn-ghost{padding:8px 14px;border-radius:10px;font-size:12.5px;font-weight:700;cursor:pointer;' +
                'background:rgba(255,255,255,.07);border:1px solid rgba(255,255,255,.14);color:#eef1f7;transition:all .13s;white-space:nowrap;}' +
            '.vmg-btn-ghost:hover{background:rgba(255,255,255,.13);}' +
            '.vmg-toggles{display:grid;grid-template-columns:1fr 1fr;gap:12px;}' +
            '.vmg-toggles--one{grid-template-columns:1fr;margin-top:12px;}' +
            '.vmg-hint--lock{margin-top:6px;}' +
            '.vmg-toggle{display:flex;align-items:center;justify-content:space-between;gap:10px;padding:11px 13px;' +
                'border-radius:12px;background:rgba(255,255,255,.035);border:1px solid rgba(255,255,255,.07);cursor:pointer;' +
                'font-size:13px;font-weight:700;color:#eef1f7;transition:border .13s;}' +
            '.vmg-toggle:hover{border-color:rgba(255,255,255,.16);}' +
            '.vmg-sw{position:relative;width:34px;height:19px;border-radius:999px;background:rgba(255,255,255,.14);' +
                'transition:background .16s;flex:0 0 auto;}' +
            '.vmg-sw::after{content:"";position:absolute;top:2px;left:2px;width:15px;height:15px;border-radius:50%;' +
                'background:#fff;transition:transform .16s;}' +
            '.vmg-toggle--on .vmg-sw{background:rgb(' + A + ');}' +
            '.vmg-toggle--on .vmg-sw::after{transform:translateX(15px);}' +
            // matches (per-service re-match editor)
            '.vmg-matches{display:flex;flex-direction:column;gap:8px;}' +
            '.vmg-match-row{display:flex;align-items:center;gap:9px;padding:9px 12px;border-radius:11px;' +
                'background:rgba(255,255,255,.035);border:1px solid rgba(255,255,255,.07);font-size:12.5px;}' +
            '.vmg-match-svc{font-weight:800;color:#eef1f7;min-width:44px;}' +
            '.vmg-match-chip{padding:2px 9px;border-radius:999px;font-size:10px;font-weight:800;text-transform:uppercase;' +
                'letter-spacing:.05em;border:1px solid transparent;}' +
            '.vmg-match-chip--ok{background:rgba(76,207,133,.14);border-color:rgba(76,207,133,.4);color:#6fdd9d;}' +
            '.vmg-match-chip--no{background:rgba(255,99,99,.12);border-color:rgba(255,99,99,.38);color:#ff8484;}' +
            '.vmg-match-chip--wait{background:rgba(255,255,255,.06);border-color:rgba(255,255,255,.16);color:rgba(255,255,255,.55);}' +
            '.vmg-match-id{color:rgba(255,255,255,.45);font-size:11.5px;overflow:hidden;text-overflow:ellipsis;' +
                'white-space:nowrap;flex:1;min-width:0;}' +
            '.vmg-match-btn{all:unset;cursor:pointer;padding:4px 11px;border-radius:999px;font-size:11px;font-weight:800;' +
                'border:1px solid rgba(' + A + ',.4);background:rgba(' + A + ',.12);color:rgb(' + A + ');transition:background .13s;}' +
            '.vmg-match-btn:hover{background:rgba(' + A + ',.24);}' +
            '.vmg-match-btn--danger{border-color:rgba(255,99,99,.35);background:rgba(255,99,99,.08);color:#ff8484;}' +
            '.vmg-match-btn--danger:hover{background:rgba(255,99,99,.18);}' +
            '.vmg-match-imdb-in{width:118px;padding:5px 9px;border-radius:8px;font-size:12px;font-family:inherit;' +
                'background:rgba(0,0,0,.3);border:1px solid rgba(255,255,255,.12);color:#eef1f7;outline:none;}' +
            '.vmg-match-imdb-in:focus{border-color:rgba(' + A + ',.6);}' +
            // matches: inline search sub-view
            '.vmg-msearch{display:flex;flex-direction:column;gap:9px;padding:11px;border-radius:12px;' +
                'background:rgba(255,255,255,.035);border:1px solid rgba(' + A + ',.3);}' +
            '.vmg-msearch-row{display:flex;gap:8px;}' +
            '.vmg-msearch-in{flex:1;min-width:0;padding:8px 11px;border-radius:9px;font-size:12.5px;font-family:inherit;' +
                'background:rgba(0,0,0,.3);border:1px solid rgba(255,255,255,.12);color:#eef1f7;outline:none;}' +
            '.vmg-msearch-in:focus{border-color:rgba(' + A + ',.6);}' +
            '.vmg-mresult{display:flex;gap:10px;align-items:flex-start;padding:8px;border-radius:10px;' +
                'background:rgba(0,0,0,.22);border:1px solid rgba(255,255,255,.06);}' +
            '.vmg-mresult img{width:38px;aspect-ratio:2/3;border-radius:5px;object-fit:cover;background:#1b1b22;flex:0 0 auto;}' +
            '.vmg-mresult-tt{font-size:12.5px;font-weight:800;color:#fff;}' +
            '.vmg-mresult-ov{font-size:11px;color:rgba(255,255,255,.45);line-height:1.4;display:-webkit-box;' +
                '-webkit-line-clamp:2;-webkit-box-orient:vertical;overflow:hidden;}' +
            '.vmg-mresult-body{flex:1;min-width:0;display:flex;flex-direction:column;gap:3px;}' +
            '.vmg-msearch-hint{font-size:11.5px;color:rgba(255,255,255,.4);}' +
            // save (in the header, clear of the app's floating bell/help orbs)
            '.vmg-hint{font-size:11.5px;color:rgba(255,255,255,.4);line-height:1.5;' +
                'padding:2px 0 30px;}' +
            '.vmg-save{position:absolute;top:18px;right:60px;padding:8px 18px;border-radius:999px;' +
                'font-size:12.5px;font-weight:800;cursor:pointer;border:none;' +
                'background:rgb(' + A + ');color:#fff;box-shadow:0 6px 18px rgba(' + A + ',.35);transition:all .15s;}' +
            '.vmg-save:hover:not(:disabled){filter:brightness(1.12);}' +
            '.vmg-save:disabled{opacity:.38;cursor:default;box-shadow:none;}' +
            '@media (prefers-reduced-motion: reduce){.vmg-overlay,.vmg-panel{transition:none;}}';
        var el = document.createElement('style');
        el.id = 'vmg-styles';
        el.textContent = css;
        document.head.appendChild(el);
    }

    // ── state ────────────────────────────────────────────────────────────────
    var state = null;   // {kind, id, data, genres[], locked{}, overlay, saving}

    function lockSvg() {
        return '<svg viewBox="0 0 10 12"><path d="M5 0a3 3 0 0 0-3 3v2H1v7h8V5H8V3a3 3 0 0 0-3-3zm0 1.4c.9 0 1.6.7 1.6 1.6v2H3.4V3c0-.9.7-1.6 1.6-1.6z"/></svg>';
    }

    function lockBadge(field) {
        if (!state || state.locked.indexOf(field) === -1) return '';
        return '<span class="vmg-lock" data-vmg-release="' + esc(field) + '" title="' + esc(LOCK_TIP) + '">' +
            lockSvg() + 'yours</span>';
    }

    function fieldHtml(field, label, control) {
        return '<div class="vmg-field"><div class="vmg-label"><span>' + esc(label) + '</span>' +
            lockBadge(field) + '</div>' + control + '</div>';
    }

    function inputHtml(field, value, extra) {
        return '<input class="vmg-input" data-vmg-f="' + esc(field) + '" value="' + esc(value == null ? '' : value) +
            '"' + (extra || '') + '>';
    }

    // ── panel ────────────────────────────────────────────────────────────────
    // A title that isn't in your library has no local row and no server item, so
    // every editor here — metadata, field locks, quality profile, series type,
    // monitored/watched, Matches — has nothing to write to. "Also known as" is
    // the exception: it's stored against the TMDB id, precisely so it can be set
    // for something you don't own yet (which is when a release gets rejected as
    // a wrong title). Rendering just that beats a panel of dead controls.
    // Per-show season-pack preference. Beats the global setting in BOTH
    // directions, so a show can be packs-only while packs are off globally, or
    // stay on singles while they are on. A subtractive-only override could not
    // say "always get this one as packs", which is the reason to come here.
    var SEASON_PACK_CHOICES = [
        ['', 'Follow the global setting'],
        ['prefer', 'Prefer season packs, fall back to episodes'],
        ['only', 'Season packs only — wait rather than use episodes'],
        ['never', 'Never use season packs for this show']
    ];

    function seasonPackField(d) {
        if (d.kind !== 'show') return '';
        var cur = d.season_pack_mode || '';
        return '<div class="vmg-field"><label>Season packs</label>' +
            '<select class="vmg-input" data-vmg-season-pack-mode>' +
            SEASON_PACK_CHOICES.map(function (c) {
                return '<option value="' + c[0] + '"' + (c[0] === cur ? ' selected' : '') + '>' +
                    c[1] + '</option>';
            }).join('') + '</select>' +
            '<div class="vmg-hint">Whether a season with several missing episodes is ' +
                'grabbed as one release instead of episode by episode. ' +
                '&ldquo;Season packs only&rdquo; applies to seasons that have finished ' +
                'airing &mdash; a season still going out weekly is grabbed normally, so ' +
                'nothing stalls waiting for a pack that cannot exist yet.</div>' +
          '</div>';
    }

    function tmdbOnlyBodyHtml(d) {
        // Series type decides HOW episodes are hunted (SxxExx / air date /
        // absolute number), so it's needed while you're still acquiring the show.
        // Stored as an override against the tmdb id; the library row takes over
        // once the show exists.
        var st = (d.kind === 'show')
            ? '<div class="vmg-field"><label>Series type</label>' +
                '<select class="vmg-input" data-vmg-series-type>' +
                ['standard', 'daily', 'anime'].map(function (t) {
                    var cur = d.series_type || 'standard';
                    return '<option value="' + t + '"' + (t === cur ? ' selected' : '') + '>' +
                        t.charAt(0).toUpperCase() + t.slice(1) +
                        (t === 'daily' ? ' (releases by air date)'
                            : t === 'anime' ? ' (absolute numbering)' : '') + '</option>';
                }).join('') + '</select>' +
                '<div class="vmg-hint">How episode releases are searched for. Anime and ' +
                    'daily shows are named differently from standard SxxExx releases.</div>' +
              '</div>' + seasonPackField(d)
            : '';
        return (
            '<div class="vmg-sect">Matching</div>' + st +
            '<div class="vmg-field"><label>Also known as</label>' +
                '<textarea class="vmg-input vmg-aka" data-vmg-aka rows="3" ' +
                    'placeholder="One title per line — releases named this way will match"' +
                    '>' + esc((d.aka_titles || []).join('\n')) + '</textarea>' +
                '<div class="vmg-hint">Used only for matching downloads. Add the name ' +
                    'releases actually use if it differs from the one shown above.</div>' +
                '<button class="vmg-btn-ghost" type="button" data-vmg-aka-save>Save titles</button>' +
            '</div>' +
            '<div class="vmg-hint" style="margin-top:14px;">' +
                'The rest of Manage — metadata, artwork, quality profile — needs the title ' +
                'to be in your library. It appears once you have downloaded it.' +
            '</div>'
        );
    }

    function bodyHtml(d) {
        if (d._tmdbOnly) return tmdbOnlyBodyHtml(d);
        var isShow = d.kind === 'show';
        var brandField = isShow ? 'network' : 'studio';
        var ratings = RATING_HINTS[d.kind] || [];
        var dl = '<datalist id="vmg-ratings">' + ratings.map(function (r) {
            return '<option value="' + esc(r) + '">';
        }).join('') + '</datalist>';
        var posterSrc = d.has_poster ? '/api/video/poster/' + d.kind + '/' + d.id : '';
        return (
            '<div class="vmg-sect">Identity</div>' +
            fieldHtml('title', 'Title', inputHtml('title', d.title)) +
            fieldHtml('sort_title', 'Sort title', inputHtml('sort_title', d.sort_title,
                ' placeholder="derived from title"')) +
            '<div class="vmg-row2">' +
                fieldHtml('year', 'Year', inputHtml('year', d.year, ' inputmode="numeric"')) +
                fieldHtml('content_rating', 'Content rating',
                    inputHtml('content_rating', d.content_rating, ' list="vmg-ratings"')) + dl +
            '</div>' +
            fieldHtml(brandField, isShow ? 'Network' : 'Studio', inputHtml(brandField, d[brandField])) +
            fieldHtml('genres', 'Genres',
                '<div class="vmg-chips" data-vmg-chips>' +
                    '<input class="vmg-chip-in" data-vmg-chip-in list="vmg-genre-dl" placeholder="Add genre…">' +
                '</div><datalist id="vmg-genre-dl"></datalist>') +
            '<div class="vmg-sect">Story</div>' +
            fieldHtml('tagline', 'Tagline', inputHtml('tagline', d.tagline)) +
            fieldHtml('overview', 'Summary',
                '<textarea class="vmg-area" data-vmg-f="overview">' + esc(d.overview) + '</textarea>') +
            '<div class="vmg-sect">Artwork &amp; state</div>' +
            '<div class="vmg-poster-row">' +
                (posterSrc ? '<img class="vmg-poster-img" src="' + esc(posterSrc) + '" alt="">'
                           : '<div class="vmg-poster-img"></div>') +
                '<div class="vmg-poster-txt">Posters flow through the Poster Manager — picked art is pushed to the server and kept.</div>' +
                (d.tmdb_id && window.VideoPoster
                    ? '<button class="vmg-btn-ghost" type="button" data-vmg-poster>Change…</button>' : '') +
            '</div>' +
            '<div class="vmg-toggles">' +
                '<div class="vmg-toggle' + (d.watched ? ' vmg-toggle--on' : '') + '" data-vmg-watched role="switch" ' +
                    'aria-checked="' + (d.watched ? 'true' : 'false') + '" tabindex="0"><span>Watched</span><span class="vmg-sw"></span></div>' +
                '<div class="vmg-toggle' + (d.monitored ? ' vmg-toggle--on' : '') + '" data-vmg-monitored role="switch" ' +
                    'aria-checked="' + (d.monitored ? 'true' : 'false') + '" tabindex="0"><span>Monitored</span><span class="vmg-sw"></span></div>' +
            '</div>' +
            // Lock automatic edits. Unattended imports into this title are refused
            // outright — an upgrade, a replacement and a brand-new episode alike —
            // so a release whose name or season was mis-parsed cannot damage
            // content you have already curated. It fails as import_failed naming
            // the lock; placing it by hand still works, and is the check the lock
            // exists to demand.
            '<div class="vmg-toggles vmg-toggles--one">' +
                '<div class="vmg-toggle' + (d.import_locked ? ' vmg-toggle--on' : '') + '" data-vmg-import-lock role="switch" ' +
                    'aria-checked="' + (d.import_locked ? 'true' : 'false') + '" tabindex="0">' +
                    '<span>Lock automatic edits</span><span class="vmg-sw"></span></div>' +
            '</div>' +
            '<div class="vmg-hint vmg-hint--lock">Refuses every automatic import for this ' +
                (isShow ? 'show' : 'movie') + ' — new episodes included. A download that targets ' +
                'it stops at placement and says so, instead of replacing what you already have. ' +
                'Manual import still works.' +
                (isShow ? ' Seasons can be locked on their own from the season list.' : '') + '</div>' +
            // Per-title quality profile (arr-parity P2): which ladder/cutoff this
            // title is grabbed + upgraded under. Options fill in async.
            '<div class="vmg-field"><label>Quality profile</label>' +
                '<select class="vmg-input" data-vmg-quality-profile>' +
                '<option value="0">Default</option></select></div>' +
            // Which configured Library this title is filed under. Decides where
            // FUTURE work lands (wishlist drain, RSS instant-grab, upgrades all
            // resolve their destination from it) — so this is the fix when a
            // title ends up in the wrong tree. Options fill in async.
            '<div class="vmg-field"><label>Library</label>' +
                '<select class="vmg-input" data-vmg-library>' +
                '<option value="">Default for ' + (isShow ? 'TV shows' : 'movies') + '</option></select>' +
                '<div class="vmg-hint">Where new downloads and upgrades for this title go. ' +
                    'Changing it does not move files already on disk.</div></div>' +
            // Series type (arr-parity P8, shows only): how episode releases are
            // hunted — SxxExx (standard), air date (daily), absolute number (anime).
            (d.kind === 'show'
                ? '<div class="vmg-field"><label>Series type</label>' +
                    '<select class="vmg-input" data-vmg-series-type>' +
                    ['standard', 'daily', 'anime'].map(function (t) {
                        var cur = d.series_type || 'standard';
                        return '<option value="' + t + '"' + (t === cur ? ' selected' : '') + '>' +
                            t.charAt(0).toUpperCase() + t.slice(1) +
                            (t === 'daily' ? ' (releases by air date)' : t === 'anime' ? ' (absolute numbering)' : '') +
                            '</option>';
                    }).join('') + '</select></div>' + seasonPackField(d) +
                  // Re-read the episode list from TMDB on demand. A show is
                  // cascaded once and then never revisited, so episodes TMDB
                  // gains later (a season still airing, a late-added batch)
                  // stay invisible — and nothing else in the app forces it:
                  // "Sync show now" reconciles against Plex, and the page's
                  // lazy refresh skips a show that already has its art.
                  '<div class="vmg-field"><label>Episode list</label>' +
                      // Provider-agnostic on purpose. All three buttons below act on
                      // whichever database owns this show's numbering (the box above),
                      // so naming one here would be wrong for any show using the other
                      // — and would be a second place to keep in step.
                      '<button class="vmg-btn-ghost" type="button" data-vmg-rescan-eps>' +
                          'Re-scan episode list</button>' +
                      '<div class="vmg-hint" data-vmg-rescan-note>Reads every season again ' +
                          'from the database above. Use this when it lists episodes Commissary ' +
                          'is missing.</div>' +
                      // Your media server and TMDB can split a long-running show into
                      // seasons differently (Plex files Bleach's newer run as S2 where
                      // TMDB calls it S17). Those are separate rows, so the episode is
                      // listed twice — once owned, once missing. Two clicks: look, then
                      // agree, because this deletes rows.
                      '<button class="vmg-btn-ghost" type="button" data-vmg-dupe-eps ' +
                          'style="margin-top:8px;">Check for duplicate episodes</button>' +
                      '<div class="vmg-hint" data-vmg-dupe-note>Finds episodes listed twice ' +
                          'twice because the same episode is filed under two different ' +
                          'season numbers. Compares your library against itself — no ' +
                          'database is consulted.</div>' +
                      // TVDB numbers some long-running shows' seasons differently from
                      // TMDB (TMDB's Bleach S2 is the 2005 arc; TVDB's is the 2022+
                      // run). The metadata gap-fill used to insert under TMDB's number,
                      // filing episodes in a season they never belonged to. Same
                      // two-click shape: look, then agree.
                      // Which database supplies this show's episode LIST. Episodes
                      // are keyed by your media server's season numbers, so a
                      // provider that splits the show differently can only file
                      // them wrongly. Auto compares both against your server.
                      '<div class="vmg-field" style="margin-top:10px;">' +
                          '<label>Episode numbering</label>' +
                          '<select class="vmg-input" data-vmg-episode-source>' +
                          [['auto', 'Auto — match my server'], ['tmdb', 'TMDB'], ['tvdb', 'TVDB']]
                              .map(function (o) {
                                  var cur = d.episode_source || 'auto';
                                  return '<option value="' + o[0] + '"' +
                                      (o[0] === cur ? ' selected' : '') + '>' + o[1] + '</option>';
                              }).join('') +
                          '</select>' +
                          '<div class="vmg-hint" data-vmg-episode-source-note>Checking which ' +
                              'database matches your server…</div>' +
                      '</div>' +
                      '<button class="vmg-btn-ghost" type="button" data-vmg-unlisted-eps ' +
                          'style="margin-top:8px;">Check for out-of-place episodes</button>' +
                      '<div class="vmg-hint" data-vmg-unlisted-note>Finds episodes filed ' +
                          'under a season the database above doesn\'t list them in — they ' +
                          'can never be matched by a search.</div>' +
                  '</div>'
                : '') +
            // Also known as (matching aid): extra titles the release-title gate will
            // accept for this title. TMDB's alias coverage is patchy — most visibly
            // for anime, where fansub groups release under a translation of the
            // original title while TMDB lists a different localised name. Local only;
            // never pushed to Plex/Jellyfin.
            '<div class="vmg-field"><label>Also known as</label>' +
                '<textarea class="vmg-input vmg-aka" data-vmg-aka rows="2" ' +
                    'placeholder="One title per line — releases named this way will match"' +
                    '>' + esc((d.aka_titles || []).join('\n')) + '</textarea>' +
                '<div class="vmg-hint">Used only for matching downloads. Add the name ' +
                    'releases actually use if it differs from the one shown here.</div>' +
                '<button class="vmg-btn-ghost" type="button" data-vmg-aka-save>Save titles</button>' +
            '</div>' +
            '<div class="vmg-sect">Matches</div>' +
            '<div class="vmg-matches" data-vmg-matches>' +
                '<div class="vmg-msearch-hint">Loading matches…</div>' +
            '</div>' +
            (window.VideoIssues
                ? '<button class="vmg-btn-ghost vmg-report" type="button" data-vmg-report>⚑ Report an issue</button>'
                : '')
        );
    }

    // ── matches (per-service re-match editor) ────────────────────────────────
    var MATCH_LABELS = { tmdb: 'TMDB', tvdb: 'TVDB', imdb: 'IMDb' };

    function matchChip(status) {
        if (status === 'matched') return '<span class="vmg-match-chip vmg-match-chip--ok">matched</span>';
        if (status === 'not_found') return '<span class="vmg-match-chip vmg-match-chip--no">not found</span>';
        if (status === 'error') return '<span class="vmg-match-chip vmg-match-chip--no">error</span>';
        return '<span class="vmg-match-chip vmg-match-chip--wait">pending</span>';
    }

    function renderMatches(matches) {
        var host = state && state.overlay.querySelector('[data-vmg-matches]');
        if (!host) return;
        state.matches = matches;
        host.innerHTML = matches.map(function (m) {
            var label = MATCH_LABELS[m.service] || m.service;
            if (m.service === 'imdb') {
                return '<div class="vmg-match-row"><span class="vmg-match-svc">' + label + '</span>' +
                    matchChip(m.status) +
                    '<input class="vmg-match-imdb-in" data-vmg-imdb-in value="' + esc(m.id || '') + '" ' +
                        'placeholder="tt0944947" spellcheck="false">' +
                    '<button class="vmg-match-btn" type="button" data-vmg-imdb-save>Set</button>' +
                    (m.id ? '<button class="vmg-match-btn vmg-match-btn--danger" type="button" ' +
                        'data-vmg-match-clear="imdb">Clear</button>' : '') +
                '</div>';
            }
            return '<div class="vmg-match-row"><span class="vmg-match-svc">' + label + '</span>' +
                matchChip(m.status) +
                '<span class="vmg-match-id">' + (m.id != null ? '#' + esc(m.id) : '—') + '</span>' +
                '<button class="vmg-match-btn" type="button" data-vmg-match-fix="' + esc(m.service) + '">' +
                    (m.id != null ? 'Fix…' : 'Find…') + '</button>' +
                (m.id != null ? '<button class="vmg-match-btn vmg-match-btn--danger" type="button" ' +
                    'data-vmg-match-clear="' + esc(m.service) + '">Clear</button>' : '') +
            '</div>';
        }).join('') +
        '<div class="vmg-msearch-hint">Re-pointing a match clears the old data and re-enriches in the background.</div>';
    }

    function loadMatches() {
        if (!state) return;
        fetch('/api/video/enrichment/matches/' + state.kind + '/' + state.id)
            .then(function (r) { return r.ok ? r.json() : null; })
            .then(function (res) {
                if (!state) return;
                if (res && res.matches && res.matches.length) renderMatches(res.matches);
                else { var h = state.overlay.querySelector('[data-vmg-matches]'); if (h) h.innerHTML = ''; }
            })
            .catch(function () { /* section is a nicety — leave the loading hint */ });
    }

    function applyMatch(service, externalId, doneLabel) {
        return fetch('/api/video/enrichment/matches/' + state.kind + '/' + state.id + '/apply', {
            method: 'POST', headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ service: service, external_id: externalId }),
        }).then(function (r) { return r.json().then(function (b) { return { ok: r.ok, body: b }; }); })
        .then(function (res) {
            if (!res.ok || !res.body || !res.body.success) {
                throw new Error((res.body && res.body.error) || 'update failed');
            }
            toast(doneLabel || 'Match updated — re-enriching in the background', 'success');
            loadMatches();
            document.dispatchEvent(new CustomEvent('soulsync:video-meta-changed', {
                detail: { kind: state.kind, id: state.id },
            }));
        }).catch(function (e) {
            toast(e && e.message ? e.message : 'Couldn’t update the match', 'error');
        });
    }

    function clearMatch(service) {
        var label = MATCH_LABELS[service] || service;
        confirmDlg({
            title: 'Clear ' + label + ' match?',
            message: service === 'imdb'
                ? 'Removes the IMDb id — ratings and IMDb-based extras will re-resolve when a new id lands.'
                : 'The item reverts to "not found" on ' + label + ' and its data from that match is cleared.',
            confirmText: 'Clear', cancelText: 'Keep', destructive: true,
        }).then(function (yes) {
            if (!yes || !state) return;
            applyMatch(service, null, label + ' match cleared');
        });
    }

    function openMatchSearch(service) {
        var host = state && state.overlay.querySelector('[data-vmg-matches]');
        if (!host) return;
        var label = MATCH_LABELS[service] || service;
        host.innerHTML =
            '<div class="vmg-msearch" data-vmg-msearch="' + esc(service) + '">' +
                '<div class="vmg-msearch-row">' +
                    '<input class="vmg-msearch-in" data-vmg-msearch-in value="' + esc(state.data.title || '') + '" ' +
                        'placeholder="Search ' + esc(label) + '…" spellcheck="false">' +
                    '<button class="vmg-match-btn" type="button" data-vmg-msearch-go>Search</button>' +
                    '<button class="vmg-match-btn" type="button" data-vmg-msearch-back>Back</button>' +
                '</div>' +
                '<div data-vmg-msearch-results><div class="vmg-msearch-hint">Pick the correct title — its metadata replaces the current match.</div></div>' +
            '</div>';
        var input = host.querySelector('[data-vmg-msearch-in]');
        if (input) { input.focus(); input.select(); }
        runMatchSearch(service);   // auto-search with the item's own title
    }

    function runMatchSearch(service) {
        var host = state && state.overlay.querySelector('[data-vmg-matches]');
        if (!host) return;
        var input = host.querySelector('[data-vmg-msearch-in]');
        var results = host.querySelector('[data-vmg-msearch-results]');
        var q = input ? input.value.trim() : '';
        if (!q || !results) return;
        results.innerHTML = '<div class="vmg-msearch-hint">Searching…</div>';
        fetch('/api/video/enrichment/matches/' + state.kind + '/' + state.id + '/search', {
            method: 'POST', headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ service: service, query: q }),
        }).then(function (r) { return r.json().then(function (b) { return { ok: r.ok, body: b }; }); })
        .then(function (res) {
            if (!state) return;
            if (!res.ok) throw new Error((res.body && res.body.error) || 'search failed');
            var items = (res.body && res.body.results) || [];
            if (!items.length) {
                results.innerHTML = '<div class="vmg-msearch-hint">No results — try another spelling.</div>';
                return;
            }
            results.innerHTML = items.map(function (it) {
                return '<div class="vmg-mresult">' +
                    (it.poster_url ? '<img src="' + esc(it.poster_url) + '" alt="" loading="lazy">' : '<img alt="">') +
                    '<div class="vmg-mresult-body">' +
                        '<div class="vmg-mresult-tt">' + esc(it.title) + (it.year ? ' (' + it.year + ')' : '') + '</div>' +
                        (it.overview ? '<div class="vmg-mresult-ov">' + esc(it.overview) + '</div>' : '') +
                    '</div>' +
                    '<button class="vmg-match-btn" type="button" data-vmg-muse="' + esc(it.id) + '">Use</button>' +
                '</div>';
            }).join('');
        }).catch(function (e) {
            if (results) results.innerHTML = '<div class="vmg-msearch-hint">' +
                esc(e && e.message ? e.message : 'Search failed') + '</div>';
        });
    }

    function panelHtml(d) {
        return (
            '<div class="vmg-panel" role="dialog" aria-modal="true" aria-label="Manage metadata">' +
                '<div class="vmg-head">' +
                    '<div class="vmg-kick"><span class="vmg-kick-dot"></span>Manage</div>' +
                    '<div class="vmg-title">' + esc(d.title) + '</div>' +
                    '<div class="vmg-sub">' + (d._tmdbOnly
                        ? 'Not in your library yet — only download matching can be set.'
                        : 'Edits are saved here, pushed to your server, and locked against scans.') + '</div>' +
                    // Save lives in the header — the app's notification/help orbs
                    // float over the bottom-right corner (z 999999, by design),
                    // so a footer button there would sit underneath them.
                    // Save drives the metadata editor, which a not-in-library title
                    // has none of — its one control saves itself.
                    (d._tmdbOnly ? ''
                        : '<button class="vmg-save" type="button" data-vmg-save disabled>Save</button>') +
                    '<button class="vmg-close" type="button" data-vmg-close aria-label="Close">×</button>' +
                '</div>' +
                '<div class="vmg-body">' + bodyHtml(d) +
                    (d._tmdbOnly ? ''
                        : '<div class="vmg-hint">Locked fields wear a badge — click it to hand one back to the server.</div>') +
                '</div>' +
            '</div>'
        );
    }

    // ── genres chips ─────────────────────────────────────────────────────────
    function renderChips() {
        var wrap = state.overlay.querySelector('[data-vmg-chips]');
        if (!wrap) return;
        var input = wrap.querySelector('[data-vmg-chip-in]');
        wrap.querySelectorAll('.vmg-chip').forEach(function (c) { c.remove(); });
        state.genres.forEach(function (g, i) {
            var chip = document.createElement('span');
            chip.className = 'vmg-chip';
            chip.innerHTML = esc(g) + '<button type="button" aria-label="Remove ' + esc(g) + '" data-vmg-chip-rm="' + i + '">×</button>';
            wrap.insertBefore(chip, input);
        });
    }

    function addGenre(raw) {
        var g = String(raw || '').trim();
        if (!g) return;
        var dupe = state.genres.some(function (x) { return x.toLowerCase() === g.toLowerCase(); });
        if (!dupe) { state.genres.push(g); renderChips(); markDirty(); }
    }

    function loadGenreSuggestions(kind) {
        fetch('/api/video/collections/fields?media_type=' + encodeURIComponent(kind))
            .then(function (r) { return r.ok ? r.json() : null; })
            .then(function (res) {
                var names = (res && res.suggestions && res.suggestions.genre) || [];
                var dl = state && state.overlay && state.overlay.querySelector('#vmg-genre-dl');
                if (dl) dl.innerHTML = names.map(function (n) { return '<option value="' + esc(n) + '">'; }).join('');
            }).catch(function () { /* suggestions are a nicety */ });
    }

    // ── dirty tracking + save ────────────────────────────────────────────────
    function currentValues() {
        var vals = {};
        state.overlay.querySelectorAll('[data-vmg-f]').forEach(function (el) {
            vals[el.getAttribute('data-vmg-f')] = el.value.trim();
        });
        vals.genres = state.genres.slice();
        return vals;
    }

    function dirtyChanges() {
        var d = state.data, vals = currentValues(), changes = {};
        Object.keys(vals).forEach(function (f) {
            if (f === 'genres') {
                var was = (d.genres || []).slice().sort().join(' ');
                var now = vals.genres.slice().sort().join(' ');
                if (was !== now) changes.genres = vals.genres;
            } else if (f === 'year') {
                var wasY = d.year == null ? '' : String(d.year);
                if (vals.year !== wasY) changes.year = vals.year;
            } else {
                var wasV = d[f] == null ? '' : String(d[f]);
                if (vals[f] !== wasV) changes[f] = vals[f];
            }
        });
        return changes;
    }

    function markDirty() {
        var btn = state.overlay.querySelector('[data-vmg-save]');
        if (btn) btn.disabled = Object.keys(dirtyChanges()).length === 0 || state.saving;
    }

    function save() {
        var changes = dirtyChanges();
        if (!Object.keys(changes).length || state.saving) return;
        if ('title' in changes && !changes.title) { toast('Title can’t be empty', 'error'); return; }
        state.saving = true;
        var btn = state.overlay.querySelector('[data-vmg-save]');
        if (btn) { btn.disabled = true; btn.textContent = 'Saving…'; }
        fetch('/api/video/detail/' + state.kind + '/' + state.id + '/metadata', {
            method: 'PUT', headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ changes: changes }),
        }).then(function (r) { return r.json().then(function (b) { return { ok: r.ok, body: b }; }); })
        .then(function (res) {
            if (!res.ok) throw new Error((res.body && res.body.error) || 'save failed');
            toast(res.body.pushed ? 'Saved & pushed to your server'
                                  : 'Saved — server not reached, will hold locally', 'success');
            document.dispatchEvent(new CustomEvent('soulsync:video-meta-changed', {
                detail: { kind: state.kind, id: state.id },
            }));
            close(true);
        }).catch(function (e) {
            if (state) {
                state.saving = false;
                if (btn) { btn.textContent = 'Save'; }
                markDirty();
            }
            toast(e && e.message ? e.message : 'Save failed', 'error');
        });
    }

    function releaseLock(field) {
        var labels = { sort_title: 'Sort title', content_rating: 'Content rating', overview: 'Summary' };
        var label = labels[field] || (field.charAt(0).toUpperCase() + field.slice(1));
        confirmDlg({
            title: 'Release ' + label + '?',
            message: 'This hands the field back to your media server — the next library scan re-adopts the server’s value.',
            confirmText: 'Release', cancelText: 'Keep mine',
        }).then(function (yes) {
            if (!yes || !state) return;
            fetch('/api/video/detail/' + state.kind + '/' + state.id + '/lock', {
                method: 'POST', headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ field: field, locked: false }),
            }).then(function (r) { return r.ok ? r.json() : null; })
            .then(function (res) {
                if (!res || !state) { toast('Couldn’t release the lock', 'error'); return; }
                state.locked = res.locked || [];
                var badge = state.overlay.querySelector('[data-vmg-release="' + field + '"]');
                if (badge) badge.remove();
                toast(label + ' released — next scan takes the server’s value', 'info');
                document.dispatchEvent(new CustomEvent('soulsync:video-meta-changed', {
                    detail: { kind: state.kind, id: state.id },
                }));
            }).catch(function () { toast('Couldn’t release the lock', 'error'); });
        });
    }

    // The profile this title INHERITS when it carries none of its own — the
    // default set on its Library, by name. Empty when the title has no Library,
    // or that Library sets no default, in which case "Default" means the global
    // Default profile and is already saying the truth.
    function inheritedProfileName(d, libRes, profiles) {
        var rid = d && d.root_folder_id;
        if (!rid || !libRes) return '';
        var conf = libRes.configured || {};
        var rows = (state.kind === 'show' ? conf.tv : conf.movies) || [];
        for (var i = 0; i < rows.length; i++) {
            if (String(rows[i].id) !== String(rid)) continue;
            var pid = parseInt(rows[i].default_quality_profile_id, 10) || 0;
            if (!pid) return '';
            for (var j = 0; j < profiles.length; j++) {
                if (profiles[j].id === pid) return profiles[j].name;
            }
            return '';
        }
        return '';
    }

    // Per-title quality profile (P2): fill the picker with the real profile
    // list + the title's current assignment; change persists immediately.
    //
    // Leaving a title unassigned is INHERITANCE, not an absence: its Library can
    // carry a default profile of its own. A picker reading "Default" while the
    // title is actually being judged at 4K would be lying about the very setting
    // it is showing, so option 0 names what it will really resolve to. Both
    // lists are needed to say that, hence the pair.
    function loadQualityProfiles(d) {
        var sel = state.overlay && state.overlay.querySelector('[data-vmg-quality-profile]');
        if (!sel) return;
        var get = function (url) {
            return fetch(url, { headers: { 'Accept': 'application/json' } })
                .then(function (r) { return r.ok ? r.json() : null; })
                .catch(function () { return null; });
        };
        Promise.all([get('/api/video/downloads/quality/profiles'), get('/api/video/libraries')])
            .then(function (both) {
                var res = both[0];
                if (!res || !sel.isConnected) return;
                var profiles = res.profiles || [];
                var cur = parseInt(d.quality_profile_id, 10) || 0;
                var inherited = inheritedProfileName(d, both[1], profiles);
                sel.innerHTML = profiles.map(function (p) {
                    var name = (p.id === 0 && inherited)
                        ? 'Default — this Library uses ' + inherited : p.name;
                    return '<option value="' + p.id + '"' + (p.id === cur ? ' selected' : '') + '>' +
                        esc(name) + '</option>';
                }).join('') || '<option value="0">Default</option>';
            })
            .catch(function () { /* picker keeps its Default option */ });
    }

    // Which configured Library this title is filed under. Reads the REGISTRY
    // (d.configured), not the live server-section discovery — the registry is
    // what root_folder_id points at, and it is the half a non-admin can read.
    function loadLibraries(d) {
        var sel = state.overlay && state.overlay.querySelector('[data-vmg-library]');
        if (!sel) return;
        fetch('/api/video/libraries', { headers: { 'Accept': 'application/json' } })
            .then(function (r) { return r.ok ? r.json() : null; })
            .then(function (res) {
                if (!res || !sel.isConnected) return;
                var conf = (res.configured || {});
                var rows = (state.kind === 'show' ? conf.tv : conf.movies) || [];
                var cur = d.root_folder_id;
                // Keep the blank option: clearing the assignment is a real choice
                // (falls back to the primary Library for this kind).
                var head = '<option value=""' + (cur == null ? ' selected' : '') + '>Default for ' +
                    (state.kind === 'show' ? 'TV shows' : 'movies') + '</option>';
                sel.innerHTML = head + rows.map(function (l) {
                    return '<option value="' + l.id + '"' + (String(l.id) === String(cur) ? ' selected' : '') +
                        '>' + esc(l.label || l.path || ('Library ' + l.id)) + '</option>';
                }).join('');
            })
            .catch(function () { /* picker keeps its Default option */ });
    }

    function setLibrary(sel) {
        var raw = sel.value;
        fetch('/api/video/detail/' + state.kind + '/' + state.id + '/library', {
            method: 'PUT', headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ root_folder_id: raw === '' ? null : parseInt(raw, 10) }) })
            .then(function (r) {
                return r.json().catch(function () { return {}; }).then(function (b) {
                    if (!r.ok) throw new Error(b.error || '');
                    if (state.data) {
                        state.data.root_folder_id = b.root_folder_id;
                        // A different Library can carry a different default, so the
                        // profile picker's "Default" option now means something else.
                        loadQualityProfiles(state.data);
                    }
                    toast(b.root_folder_id == null
                        ? 'Library cleared — new downloads use the default for this type'
                        : 'Library updated — new downloads and upgrades go there', 'success');
                });
            })
            .catch(function (e) {
                toast((e && e.message) || 'Couldn’t change the Library', 'error');
            });
    }

    // Re-read the episode list from TMDB. Long-running for a show with many
    // seasons (one API call per season), so the button reports progress and the
    // hint becomes the result — "added 12" is the answer the user actually wants,
    // not a green tick that leaves them counting rows.
    function rescanEpisodes(btn) {
        if (btn.disabled) return;
        var note = state.overlay && state.overlay.querySelector('[data-vmg-rescan-note]');
        var orig = btn.textContent;
        btn.disabled = true;
        btn.textContent = 'Re-scanning…';
        if (note) note.textContent = 'Reading every season — this can take a moment.';
        fetch('/api/video/detail/show/' + state.id + '/rescan-episodes', {
            method: 'POST', headers: { Accept: 'application/json' } })
            .then(function (r) {
                return r.json().catch(function () { return {}; }).then(function (b) {
                    if (!r.ok || !b.ok) throw new Error(b.error || 'Re-scan failed');
                    return b;
                });
            })
            .then(function (b) {
                var msg = b.added
                    ? 'Added ' + b.added + ' episode' + (b.added === 1 ? '' : 's') +
                      ' — ' + b.total + ' now known'
                    : 'No new episodes — ' + b.total + ' known';
                if (note) note.textContent = msg;
                toast(msg, 'success');
                if (b.added) document.dispatchEvent(new CustomEvent('soulsync:video-episodes-changed'));
            })
            .catch(function (e) {
                if (note) note.textContent = (e && e.message) || 'Re-scan failed';
                toast((e && e.message) || 'Re-scan failed', 'error');
            })
            .then(function () { btn.disabled = false; btn.textContent = orig; });
    }

    // Two-step on purpose: the first click only LOOKS, the second agrees to the
    // delete. No modal — the button itself becomes the confirmation, and it names
    // the exact number so "remove 12 duplicate rows" is what you agree to, not a
    // generic yes. State lives on the button so re-opening the panel resets it.
    function duplicateEpisodes(btn) {
        if (btn.disabled) return;
        var note = state.overlay && state.overlay.querySelector('[data-vmg-dupe-note]');
        var armed = btn.getAttribute('data-armed') === '1';
        btn.disabled = true;
        btn.textContent = armed ? 'Removing…' : 'Checking…';
        var req = armed
            ? fetch('/api/video/repair/duplicate-episodes', {
                method: 'POST', headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ show_id: state.id }) })
            : fetch('/api/video/repair/duplicate-episodes?show_id=' + state.id,
                { headers: { Accept: 'application/json' } });
        req.then(function (r) {
            return r.json().catch(function () { return {}; }).then(function (b) {
                if (!r.ok || !b.ok) throw new Error(b.error || 'Request failed');
                return b;
            });
        }).then(function (b) {
            if (armed) {
                btn.removeAttribute('data-armed');
                btn.textContent = 'Check for duplicate episodes';
                var msg = 'Removed ' + b.removed + ' duplicate row' + (b.removed === 1 ? '' : 's');
                if (note) note.textContent = msg + '. Nothing on disk was touched.';
                toast(msg, 'success');
                if (b.removed) document.dispatchEvent(new CustomEvent('soulsync:video-episodes-changed'));
                return;
            }
            if (!b.count) {
                btn.textContent = 'Check for duplicate episodes';
                if (note) note.textContent = 'No duplicates found for this show.';
                toast('No duplicate episodes found', 'info');
                return;
            }
            // Arm the confirm, and say exactly what would go.
            btn.setAttribute('data-armed', '1');
            btn.textContent = 'Remove ' + b.count + ' duplicate row' + (b.count === 1 ? '' : 's');
            var eg = (b.items || []).slice(0, 3).map(function (it) {
                return 'S' + it.season_number + 'E' + it.episode_number +
                       ' (you have it as S' + it.owned_season + 'E' + it.owned_episode + ')';
            }).join(', ');
            if (note) {
                note.textContent = b.count + ' episode(s) listed twice: ' + eg +
                    (b.count > 3 ? ', …' : '') + '. Removing only clears the duplicate ' +
                    'listing — no files are deleted.';
            }
        }).catch(function (e) {
            btn.removeAttribute('data-armed');
            btn.textContent = 'Check for duplicate episodes';
            if (note) note.textContent = (e && e.message) || 'Check failed';
            toast((e && e.message) || 'Check failed', 'error');
        }).then(function () { btn.disabled = false; });
    }

    // Same two-step contract as duplicateEpisodes: look, then agree to a named
    // count. Separate from it because the RULE is different — that one pairs a
    // row against an episode you own under another season; this one asks the
    // show's numbering database whether the season lists that number at all.
    function unlistedEpisodes(btn) {
        if (btn.disabled) return;
        var note = state.overlay && state.overlay.querySelector('[data-vmg-unlisted-note]');
        var armed = btn.getAttribute('data-armed') === '1';
        var idle = 'Check for out-of-place episodes';
        btn.disabled = true;
        btn.textContent = armed ? 'Removing…' : 'Checking…';
        var req = armed
            ? fetch('/api/video/repair/unlisted-episodes', {
                method: 'POST', headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ show_id: state.id }) })
            : fetch('/api/video/repair/unlisted-episodes?show_id=' + state.id,
                { headers: { Accept: 'application/json' } });
        req.then(function (r) {
            return r.json().catch(function () { return {}; }).then(function (b) {
                if (!r.ok || !b.ok) throw new Error(b.error || 'Request failed');
                return b;
            });
        }).then(function (b) {
            // Which database the server judged against. Named in every outcome:
            // "nothing found" and "asked the wrong one" are otherwise the same
            // message, and telling them apart is the whole point of reporting it.
            var src = String(b.source || 'tmdb').toUpperCase();
            if (armed) {
                btn.removeAttribute('data-armed');
                btn.textContent = idle;
                var msg = 'Removed ' + b.removed + ' out-of-place row' + (b.removed === 1 ? '' : 's');
                if (note) note.textContent = msg + '. Nothing on disk was touched.';
                toast(msg, 'success');
                if (b.removed) document.dispatchEvent(new CustomEvent('soulsync:video-episodes-changed'));
                return;
            }
            if (!b.count) {
                btn.textContent = idle;
                if (note) note.textContent = 'Checked against ' + src +
                    ': every episode is filed under a season it lists.';
                toast('No out-of-place episodes found (checked ' + src + ')', 'info');
                return;
            }
            btn.setAttribute('data-armed', '1');
            btn.textContent = 'Remove ' + b.count + ' out-of-place row' + (b.count === 1 ? '' : 's');
            var eg = (b.items || []).slice(0, 3).map(function (it) {
                return 'S' + it.season_number + 'E' + it.episode_number +
                       (it.air_date ? ' (' + it.air_date + ')' : '');
            }).join(', ');
            if (note) {
                note.textContent = b.count + ' episode(s) ' + src + ' doesn\'t list in that season: ' +
                    eg + (b.count > 3 ? ', …' : '') + '. None are on disk — removing only ' +
                    'clears the listing.';
            }
        }).catch(function (e) {
            btn.removeAttribute('data-armed');
            btn.textContent = idle;
            if (note) note.textContent = (e && e.message) || 'Check failed';
            toast((e && e.message) || 'Check failed', 'error');
        }).then(function () { btn.disabled = false; });
    }

    // Name the provider that's ACTUALLY in force, and why. 'Auto' is otherwise
    // unfalsifiable: when a re-scan reports nothing there's no way to tell
    // whether auto chose what you expected or quietly kept the default because
    // a probe failed. Also relabels the re-scan button, which used to claim
    // TMDB regardless of where it really reads from.
    function loadEpisodeSource() {
        var note = state.overlay && state.overlay.querySelector('[data-vmg-episode-source-note]');
        if (!note) return;
        fetch('/api/video/detail/show/' + state.id + '/episode-source',
              { headers: { Accept: 'application/json' } })
            .then(function (r) { return r.json().catch(function () { return {}; }); })
            .then(function (d) {
                if (!d.success) {
                    note.textContent = d.error || 'Could not check the databases just now.';
                    return;
                }
                var name = String(d.source || 'tmdb').toUpperCase();
                if (d.override) {
                    note.textContent = 'Pinned to ' + name + '. Auto would use ' +
                        (d.tvdb_score > d.tmdb_score ? 'TVDB' : 'TMDB') + '.';
                    return;
                }
                var miss = (d.source === 'tvdb' ? d.missing_from_tmdb : d.missing_from_tvdb) || [];
                note.textContent = 'Using ' + name + ' — it covers ' +
                    Math.round((d.source === 'tvdb' ? d.tvdb_score : d.tmdb_score) * 100) +
                    '% of your server\'s seasons' +
                    (miss.length ? ' (the other is missing season' + (miss.length === 1 ? ' ' : 's ') +
                        miss.slice(0, 6).join(', ') + (miss.length > 6 ? '…' : '') + ')' : '') + '.';
            })
            .catch(function () { note.textContent = 'Could not check the databases just now.'; });
    }

    // Which provider supplies the episode LIST. Changing it rewrites nothing on
    // its own — the next re-scan is what applies it — so the toast says so
    // rather than implying the library just changed.
    function setEpisodeSource(sel) {
        fetch('/api/video/detail/show/' + state.id + '/episode-source', {
            method: 'PUT', headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ episode_source: sel.value }),
        }).then(function (r) { return r.json().catch(function () { return {}; }); })
            .then(function (b) {
                if (!b.success) { toast(b.error || 'Could not save', 'error'); return; }
                toast(sel.value === 'auto'
                    ? 'Episode numbering back to auto — re-scan to apply'
                    : 'Episode numbering set to ' + sel.value.toUpperCase() + ' — re-scan to apply',
                    'success');
            })
            .catch(function () { toast('Could not save', 'error'); });
    }

    function setSeriesType(sel) {
        fetch('/api/video/detail/show/' + state.id + '/series-type', {
            method: 'PUT', headers: { 'Content-Type': 'application/json' },
            // Not in the library → state.id is the tmdb id, stored as an override.
            body: JSON.stringify({ series_type: sel.value,
                                   source: state.tmdbOnly ? 'tmdb' : 'library' }) })
            .then(function (r) {
                if (!r.ok) throw new Error();
                toast('Series type updated — episode searches follow it', 'success');
            })
            .catch(function () { toast('Couldn’t update the series type', 'error'); });
    }

    function setSeasonPackMode(sel) {
        fetch('/api/video/detail/show/' + state.id + '/season-pack-mode', {
            method: 'PUT', headers: { 'Content-Type': 'application/json' },
            // Not in the library → state.id is the tmdb id, stored as an override.
            body: JSON.stringify({ season_pack_mode: sel.value,
                                   source: state.tmdbOnly ? 'tmdb' : 'library' }) })
            .then(function (r) {
                if (!r.ok) throw new Error();
                toast(sel.value
                    ? 'Season pack preference saved for this show'
                    : 'This show follows the global season pack setting again', 'success');
            })
            .catch(function () { toast('Couldn’t save the season pack preference', 'error'); });
    }

    function saveAkaTitles(btn) {
        var box = document.querySelector('[data-vmg-aka]');
        if (!box) return;
        btn.disabled = true;
        fetch('/api/video/detail/' + state.kind + '/' + state.id + '/aka', {
            method: 'PUT', headers: { 'Content-Type': 'application/json' },
            // For a not-in-library title state.id IS the tmdb id, and the endpoint
            // has to be told so — it resolves a library row id by default.
            body: JSON.stringify({ titles: box.value,
                                   source: state.tmdbOnly ? 'tmdb' : 'library' }) })
            .then(function (r) { return r.ok ? r.json() : null; })
            .then(function (d) {
                btn.disabled = false;
                if (!d || !d.ok) { toast('Couldn’t save those titles', 'error'); return; }
                // Echo back what was actually stored (deduped, blanks dropped) so the
                // box shows the truth rather than what was typed.
                box.value = (d.aka_titles || []).join('\n');
                toast(d.aka_titles.length
                    ? 'Saved — releases using ' + (d.aka_titles.length === 1 ? 'that name' : 'those names') + ' will match'
                    : 'Cleared — only the title above will match', 'success');
            })
            .catch(function () { btn.disabled = false; toast('Couldn’t save those titles', 'error'); });
    }

    function setQualityProfile(sel) {
        var pid = parseInt(sel.value, 10) || 0;
        fetch('/api/video/detail/' + state.kind + '/' + state.id + '/quality-profile', {
            method: 'PUT', headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ profile_id: pid }) })
            .then(function (r) {
                if (!r.ok) throw new Error();
                toast('Quality profile updated — grabs and upgrades follow it', 'success');
            })
            .catch(function () { toast('Couldn’t update the quality profile', 'error'); });
    }

    // "Lock automatic edits" — its own endpoint rather than a case in toggle()
    // above, because it is neither watch state nor the monitored flag: it decides
    // whether the unattended importer may write into this title at all.
    function toggleImportLock(el) {
        var on = !el.classList.contains('vmg-toggle--on');
        el.classList.toggle('vmg-toggle--on', on);
        el.setAttribute('aria-checked', on ? 'true' : 'false');
        fetch('/api/video/detail/' + state.kind + '/' + state.id + '/import-lock', {
            method: 'POST', headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ locked: on }),
        })
            .then(function (r) {
                if (!r.ok) throw new Error();
                if (state.data) state.data.import_locked = on;
                toast(on ? 'Locked — automatic imports for this title will be refused'
                         : 'Unlocked — automatic imports can write to this title again',
                      'success');
                document.dispatchEvent(new CustomEvent('soulsync:video-meta-changed', {
                    detail: { kind: state.kind, id: state.id, quiet: true },
                }));
            })
            .catch(function () {
                el.classList.toggle('vmg-toggle--on', !on);
                el.setAttribute('aria-checked', !on ? 'true' : 'false');
                toast('Couldn’t change the lock', 'error');
            });
    }

    function toggle(which, el) {
        var url = which === 'watched'
            ? '/api/video/detail/' + state.kind + '/' + state.id + '/watched'
            : '/api/video/monitor';
        var on = !el.classList.contains('vmg-toggle--on');
        var body = which === 'watched'
            ? { watched: on }
            : { kind: state.kind, id: state.id, monitored: on };
        el.classList.toggle('vmg-toggle--on', on);
        el.setAttribute('aria-checked', on ? 'true' : 'false');
        fetch(url, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) })
            .then(function (r) {
                if (!r.ok) throw new Error();
                state.data[which] = on;
                document.dispatchEvent(new CustomEvent('soulsync:video-meta-changed', {
                    detail: { kind: state.kind, id: state.id, quiet: true },
                }));
            })
            .catch(function () {
                el.classList.toggle('vmg-toggle--on', !on);
                el.setAttribute('aria-checked', !on ? 'true' : 'false');
                toast('Couldn’t update ' + which, 'error');
            });
    }

    // ── open / close ─────────────────────────────────────────────────────────
    function close(force) {
        if (!state) return;
        var finish = function () {
            var ov = state && state.overlay;
            state = null;
            if (!ov) return;
            ov.classList.remove('vmg-open');
            setTimeout(function () { ov.remove(); }, 230);
            document.removeEventListener('keydown', onKey, true);
        };
        if (!force && Object.keys(dirtyChanges()).length) {
            confirmDlg({
                title: 'Discard changes?', message: 'You have unsaved edits.',
                confirmText: 'Discard', cancelText: 'Keep editing', destructive: true,
            }).then(function (yes) { if (yes) finish(); });
            return;
        }
        finish();
    }

    function onKey(e) {
        if (e.key === 'Escape' && state) { e.stopPropagation(); close(); }
    }

    function wire() {
        var ov = state.overlay;
        ov.addEventListener('click', function (e) {
            if (e.target === ov) { close(); return; }
            if (e.target.closest('[data-vmg-close]')) { close(); return; }
            if (e.target.closest('[data-vmg-save]')) { save(); return; }
            var rel = e.target.closest('[data-vmg-release]');
            if (rel) { releaseLock(rel.getAttribute('data-vmg-release')); return; }
            var rm = e.target.closest('[data-vmg-chip-rm]');
            if (rm) {
                state.genres.splice(parseInt(rm.getAttribute('data-vmg-chip-rm'), 10), 1);
                renderChips(); markDirty(); return;
            }
            if (e.target.closest('[data-vmg-report]')) {
                if (window.VideoIssues) {
                    VideoIssues.openReport({ entityType: state.kind, entityId: state.id,
                        name: state.data.title || '',
                        meta: state.data.year ? String(state.data.year) : '' });
                }
                return;
            }
            if (e.target.closest('[data-vmg-poster]')) {
                if (window.VideoPoster) {
                    VideoPoster.open({ kind: state.kind, tmdbId: state.data.tmdb_id, libraryId: state.id,
                        title: state.data.title || '', year: state.data.year || null });
                }
                return;
            }
            // Matches section (re-match editor)
            var mfix = e.target.closest('[data-vmg-match-fix]');
            if (mfix) { openMatchSearch(mfix.getAttribute('data-vmg-match-fix')); return; }
            var mclear = e.target.closest('[data-vmg-match-clear]');
            if (mclear) { clearMatch(mclear.getAttribute('data-vmg-match-clear')); return; }
            var mgo = e.target.closest('[data-vmg-msearch-go]');
            if (mgo) {
                var msv = state.overlay.querySelector('[data-vmg-msearch]');
                if (msv) runMatchSearch(msv.getAttribute('data-vmg-msearch'));
                return;
            }
            if (e.target.closest('[data-vmg-msearch-back]')) { loadMatches(); return; }
            var muse = e.target.closest('[data-vmg-muse]');
            if (muse) {
                var ms2 = state.overlay.querySelector('[data-vmg-msearch]');
                if (ms2) {
                    muse.disabled = true;
                    applyMatch(ms2.getAttribute('data-vmg-msearch'),
                               parseInt(muse.getAttribute('data-vmg-muse'), 10));
                }
                return;
            }
            if (e.target.closest('[data-vmg-imdb-save]')) {
                var iin = state.overlay.querySelector('[data-vmg-imdb-in]');
                var iv = iin ? iin.value.trim() : '';
                if (!/^tt\d{5,10}$/.test(iv)) { toast('An IMDb id looks like tt0944947', 'error'); return; }
                applyMatch('imdb', iv, 'IMDb id set — ratings will refresh');
                return;
            }
            var akaBtn = e.target.closest('[data-vmg-aka-save]');
            if (akaBtn) { saveAkaTitles(akaBtn); return; }
            var rsc = e.target.closest('[data-vmg-rescan-eps]');
            if (rsc) { rescanEpisodes(rsc); return; }
            var dup = e.target.closest('[data-vmg-dupe-eps]');
            if (dup) { duplicateEpisodes(dup); return; }
            var unl = e.target.closest('[data-vmg-unlisted-eps]');
            if (unl) { unlistedEpisodes(unl); return; }
            var tw = e.target.closest('[data-vmg-watched]');
            if (tw) { toggle('watched', tw); return; }
            var tm = e.target.closest('[data-vmg-monitored]');
            if (tm) { toggle('monitored', tm); return; }
            var chips = e.target.closest('[data-vmg-chips]');
            if (chips) { var ci = chips.querySelector('[data-vmg-chip-in]'); if (ci) ci.focus(); }
        });
        ov.addEventListener('input', function (e) {
            if (e.target.closest('[data-vmg-f]')) markDirty();
        });
        ov.addEventListener('change', function (e) {
            var qp = e.target.closest('[data-vmg-quality-profile]');
            if (qp) setQualityProfile(qp);
            var lk = e.target.closest('[data-vmg-import-lock]');
            if (lk) { toggleImportLock(lk); return; }
            var st = e.target.closest('[data-vmg-series-type]');
            if (st) setSeriesType(st);
            var spm = e.target.closest('[data-vmg-season-pack-mode]');
            if (spm) setSeasonPackMode(spm);
            var lib = e.target.closest('[data-vmg-library]');
            if (lib) setLibrary(lib);
            var es = e.target.closest('[data-vmg-episode-source]');
            if (es) setEpisodeSource(es);
        });
        ov.addEventListener('keydown', function (e) {
            var msin = e.target.closest('[data-vmg-msearch-in]');
            if (msin && e.key === 'Enter') {
                e.preventDefault();
                var msv = state.overlay.querySelector('[data-vmg-msearch]');
                if (msv) runMatchSearch(msv.getAttribute('data-vmg-msearch'));
                return;
            }
            var ci = e.target.closest('[data-vmg-chip-in]');
            if (ci) {
                if (e.key === 'Enter' || e.key === ',') { e.preventDefault(); addGenre(ci.value); ci.value = ''; }
                else if (e.key === 'Backspace' && !ci.value && state.genres.length) {
                    state.genres.pop(); renderChips(); markDirty();
                }
                return;
            }
            var sw = e.target.closest('[data-vmg-watched],[data-vmg-monitored]');
            if (sw && (e.key === 'Enter' || e.key === ' ')) {
                e.preventDefault();
                toggle(sw.hasAttribute('data-vmg-watched') ? 'watched' : 'monitored', sw);
            }
        });
        // Genre picked from the datalist (input fires without a key event on click).
        var ci = ov.querySelector('[data-vmg-chip-in]');
        if (ci) {
            ci.addEventListener('change', function () { addGenre(ci.value); ci.value = ''; });
        }
        document.addEventListener('keydown', onKey, true);
    }

    // opts: { kind, id, source?, detail?, akaTitles? }
    //   source 'tmdb'  → `id` is a TMDB id for a title NOT in the library. There
    //                    is no row to fetch, so the caller passes what it already
    //                    has and only the matching section renders.
    //   anything else  → `id` is a library row id; unchanged behaviour.
    function open(opts) {
        if (!opts || !opts.kind || opts.id == null) return;
        // Every save in this panel is admin-only server-side (/metadata, /lock,
        // /aka, /library, /quality-profile, /series-type, /rescan-episodes,
        // /episode-source). Defense in depth behind the hidden launcher, same as
        // the Overlay and Collection studios — a panel that can only 403 should
        // not open at all.
        if (typeof currentProfile !== 'undefined' && currentProfile && !currentProfile.is_admin) return;
        if (state) close(true);
        ensureStyles();

        if (String(opts.source || '').toLowerCase() === 'tmdb') {
            var src = opts.detail || {};
            // The overrides live against the tmdb id, so they're readable with no
            // library row — fetch them rather than trusting whatever the caller had.
            fetch('/api/video/detail/aka/' + encodeURIComponent(opts.kind) +
                  '/' + encodeURIComponent(opts.id), { headers: { Accept: 'application/json' } })
                .then(function (r) { return r.ok ? r.json() : null; })
                .then(function (a) {
                    _mount({ kind: opts.kind, id: opts.id, title: src.title || '',
                             aka_titles: (a && a.aka_titles) || opts.akaTitles || [],
                             series_type: (a && a.series_type) || src.series_type || 'standard',
                             season_pack_mode: (a && a.season_pack_mode) || '',
                             _tmdbOnly: true }, true);
                })
                .catch(function () {
                    _mount({ kind: opts.kind, id: opts.id, title: src.title || '',
                             aka_titles: opts.akaTitles || [],
                             series_type: src.series_type || 'standard',
                             _tmdbOnly: true }, true);
                });
            return;
        }

        fetch('/api/video/detail/' + encodeURIComponent(opts.kind) + '/' + encodeURIComponent(opts.id))
            .then(function (r) { return r.ok ? r.json() : null; })
            .then(function (d) {
                if (!d) { toast('Couldn’t load item', 'error'); return; }
                _mount(d, false);
            })
            .catch(function () { toast('Couldn’t load item', 'error'); });
    }

    function _mount(d, tmdbOnly) {
        var ov = document.createElement('div');
        ov.className = 'vmg-overlay';
        ov.innerHTML = panelHtml(d);
        document.body.appendChild(ov);
        state = { kind: d.kind, id: d.id, data: d, saving: false,
                  genres: (d.genres || []).slice(), locked: (d.locked_fields || []).slice(),
                  overlay: ov, tmdbOnly: !!tmdbOnly };
        wire();
        if (!tmdbOnly) {
            // All row-backed: genre chips, TMDB/IMDb matches, per-title profiles.
            renderChips();
            loadGenreSuggestions(d.kind);
            loadMatches();
            if (d.kind === 'show') loadEpisodeSource();
            loadQualityProfiles(d);
            loadLibraries(d);
        }
        requestAnimationFrame(function () { ov.classList.add('vmg-open'); });
    }

    window.VideoManage = { open: open };
})();
