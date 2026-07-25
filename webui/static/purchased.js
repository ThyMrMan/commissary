// PURCHASED PAGE FUNCTIONALITY
// ===============================
// Durable purchase history, grouped by album — a track/album lands here once
// mark_tracks_purchased() runs (see library.js: _tbpMarkPurchased,
// _toggleTrackToBePurchased, markAlbumPurchased). Separate from the "to be
// purchased" shopping list (still driven by library.js's openToBePurchasedModal) —
// this page is the permanent record of what you've actually bought.

const purchasedPageState = {
    isInitialized: false,
    page: 1,
    search: '',
    totalPages: 1,
    debounceTimer: null,
    // Album ids whose track list is collapsed. Held here (not read off the DOM)
    // because every unmark re-fetches and re-renders the whole list — reading
    // the old DOM would lose the state on exactly the action most likely to
    // follow a collapse. Mirrored to localStorage so it survives a reload.
    collapsed: new Set(),
};

const PURCHASED_COLLAPSED_KEY = 'purchasedCollapsedAlbums';

function _loadPurchasedCollapsed() {
    try {
        const raw = JSON.parse(localStorage.getItem(PURCHASED_COLLAPSED_KEY) || '[]');
        if (Array.isArray(raw)) purchasedPageState.collapsed = new Set(raw.map(String));
    } catch (e) {
        // Corrupt or unavailable storage is not worth failing the page over.
    }
}

function _savePurchasedCollapsed() {
    try {
        // Cap it: an album collapsed once and never seen again would otherwise
        // sit in storage forever.
        const ids = Array.from(purchasedPageState.collapsed).slice(-500);
        localStorage.setItem(PURCHASED_COLLAPSED_KEY, JSON.stringify(ids));
    } catch (e) {
        /* storage full or blocked — collapsing still works for this session */
    }
}

function _setPurchasedCollapsed(albumId, collapsed) {
    const id = String(albumId);
    if (collapsed) purchasedPageState.collapsed.add(id);
    else purchasedPageState.collapsed.delete(id);
    _savePurchasedCollapsed();
}

function _applyPurchasedCollapseState(cardEl, collapsed) {
    cardEl.classList.toggle('purchased-album-card--collapsed', collapsed);
    const toggle = cardEl.querySelector('.purchased-album-toggle');
    if (toggle) {
        toggle.setAttribute('aria-expanded', String(!collapsed));
        toggle.setAttribute('title', collapsed ? 'Show tracks' : 'Hide tracks');
    }
}

// Are all the albums currently rendered collapsed? Drives the header button's
// label, so it always offers the action that would actually change something.
function _updatePurchasedCollapseAllBtn() {
    const btn = document.getElementById('purchased-collapse-all-btn');
    if (!btn) return;
    const cards = document.querySelectorAll('#purchased-albums-list .purchased-album-card');
    if (!cards.length) { btn.classList.add('hidden'); return; }
    btn.classList.remove('hidden');
    const allCollapsed = Array.from(cards).every(c => c.classList.contains('purchased-album-card--collapsed'));
    btn.textContent = allCollapsed ? 'Expand all' : 'Collapse all';
    btn.dataset.action = allCollapsed ? 'expand' : 'collapse';
}

async function initializePurchasedPage() {
    if (!purchasedPageState.isInitialized) {
        const searchInput = document.getElementById('purchased-search-input');
        if (searchInput) {
            searchInput.addEventListener('input', (e) => {
                clearTimeout(purchasedPageState.debounceTimer);
                purchasedPageState.debounceTimer = setTimeout(() => {
                    purchasedPageState.search = e.target.value.trim();
                    purchasedPageState.page = 1;
                    _loadPurchasedAlbums();
                }, 300);
            });
        }
        const prevBtn = document.getElementById('purchased-prev-btn');
        const nextBtn = document.getElementById('purchased-next-btn');
        if (prevBtn) prevBtn.addEventListener('click', () => {
            if (purchasedPageState.page > 1) { purchasedPageState.page--; _loadPurchasedAlbums(); }
        });
        if (nextBtn) nextBtn.addEventListener('click', () => {
            if (purchasedPageState.page < purchasedPageState.totalPages) { purchasedPageState.page++; _loadPurchasedAlbums(); }
        });

        const collapseAllBtn = document.getElementById('purchased-collapse-all-btn');
        if (collapseAllBtn) {
            collapseAllBtn.addEventListener('click', () => {
                _togglePurchasedAll(collapseAllBtn.dataset.action !== 'expand');
            });
        }

        _loadPurchasedCollapsed();
        purchasedPageState.isInitialized = true;
    }
    await _loadPurchasedAlbums();
}

async function _loadPurchasedAlbums() {
    const listEl = document.getElementById('purchased-albums-list');
    const emptyEl = document.getElementById('purchased-empty-state');
    const paginationEl = document.getElementById('purchased-pagination');
    const countEl = document.getElementById('purchased-album-count');
    if (!listEl) return;

    listEl.innerHTML = '<div class="purchased-loading">Loading…</div>';
    if (emptyEl) emptyEl.classList.add('hidden');
    if (paginationEl) paginationEl.classList.add('hidden');

    try {
        const params = new URLSearchParams({ page: purchasedPageState.page, limit: 25 });
        if (purchasedPageState.search) params.set('search', purchasedPageState.search);
        const res = await fetch('/api/library/purchased?' + params.toString());
        const data = await res.json();
        if (!data.success) throw new Error(data.error || 'Failed to load');

        const albums = data.albums || [];
        purchasedPageState.totalPages = data.pagination.total_pages || 1;
        if (countEl) countEl.textContent = String(data.pagination.total_count || 0);

        if (!albums.length) {
            listEl.innerHTML = '';
            _updatePurchasedCollapseAllBtn();
            if (emptyEl) {
                emptyEl.classList.remove('hidden');
                const heading = emptyEl.querySelector('h3');
                if (heading) heading.textContent = purchasedPageState.search ? 'No matches' : 'Nothing purchased yet';
            }
        } else {
            listEl.innerHTML = albums.map(_purchasedAlbumCardHtml).join('');
            _wirePurchasedRowButtons(listEl);
            _updatePurchasedCollapseAllBtn();
        }

        if (paginationEl && purchasedPageState.totalPages > 1) {
            paginationEl.classList.remove('hidden');
            const indicator = document.getElementById('purchased-page-indicator');
            if (indicator) indicator.textContent = `Page ${purchasedPageState.page} / ${purchasedPageState.totalPages}`;
            const prevBtn = document.getElementById('purchased-prev-btn');
            const nextBtn = document.getElementById('purchased-next-btn');
            if (prevBtn) prevBtn.disabled = !data.pagination.has_prev;
            if (nextBtn) nextBtn.disabled = !data.pagination.has_next;
        }
    } catch (err) {
        listEl.innerHTML = '<div class="purchased-loading">Could not load purchased music: ' + escapeHtml(err.message || String(err)) + '</div>';
    }
}

function _purchasedAlbumCardHtml(album) {
    const thumb = album.thumb_url
        ? `<img class="purchased-album-thumb" src="${escapeHtml(album.thumb_url)}" alt="">`
        : `<div class="purchased-album-thumb purchased-album-thumb-placeholder">🎵</div>`;
    const fullyPurchased = album.purchased_count >= album.total_track_count;
    const badgeCls = fullyPurchased ? 'purchased-album-badge purchased-album-badge--full' : 'purchased-album-badge';
    const lastDate = album.last_purchased_at ? _formatPurchasedDate(album.last_purchased_at) : '';
    const trackIds = (album.tracks || []).map(t => t.id);
    const canUnmark = _purchasedCanUnmark();

    const trackRows = (album.tracks || []).map(t => `
        <div class="purchased-track-row">
            <span class="purchased-track-num">${t.track_number || ''}</span>
            <span class="purchased-track-title">${escapeHtml(t.title || 'Untitled')}</span>
            <span class="purchased-track-date">${_formatPurchasedDate(t.purchased_at)}</span>
            ${canUnmark ? `<button class="btn btn--secondary purchased-track-unmark-btn" data-track-id="${escapeHtml(String(t.id))}" title="Undo this purchase record">Unmark</button>` : ''}
        </div>
    `).join('');

    const albumId = String(album.album_id);
    const isCollapsed = purchasedPageState.collapsed.has(albumId);
    const cardCls = 'purchased-album-card' + (isCollapsed ? ' purchased-album-card--collapsed' : '')
        + (canUnmark ? '' : ' purchased-album-card--readonly');
    const trackCount = (album.tracks || []).length;

    return `
        <div class="${cardCls}" data-album-id="${escapeHtml(albumId)}">
            <div class="purchased-album-header">
                <button class="purchased-album-toggle" type="button"
                        aria-expanded="${String(!isCollapsed)}"
                        aria-label="Toggle ${escapeHtml(String(trackCount))} track(s)"
                        title="${isCollapsed ? 'Show tracks' : 'Hide tracks'}">
                    <span class="purchased-album-chevron" aria-hidden="true">▾</span>
                </button>
                ${thumb}
                <div class="purchased-album-info">
                    <div class="purchased-album-title">${escapeHtml(album.album_title || 'Unknown Album')}</div>
                    <div class="purchased-album-artist">${escapeHtml(album.artist_name || 'Unknown Artist')}</div>
                </div>
                <span class="${badgeCls}">${album.purchased_count}/${album.total_track_count} purchased</span>
                <span class="purchased-album-date">${lastDate}</span>
                ${canUnmark ? `<button class="btn btn--secondary purchased-album-unmark-btn" data-album-id="${escapeHtml(String(album.album_id))}" data-track-ids="${escapeHtml(trackIds.join(','))}" title="Undo the purchase record for every track shown below">Unmark Album</button>` : ''}
            </div>
            <div class="purchased-track-rows">${trackRows}</div>
        </div>
    `;
}

// Unmarking is admin-only (POST /api/library/tracks/unmark-purchased returns
// 403 otherwise): it destroys purchase history nothing else can rebuild.
// Both the per-track and the whole-album button hit that ONE endpoint, so
// there is no coherent "albums only" restriction — unmarking each track in
// turn is the same act. Defaults to allowed, matching the server's default
// for single-profile installs.
function _purchasedCanUnmark() {
    return typeof currentProfile === 'undefined' || !currentProfile
        || currentProfile.is_admin !== false;
}

function _formatPurchasedDate(isoLike) {
    if (!isoLike) return '';
    try {
        const d = new Date(String(isoLike).replace(' ', 'T') + (String(isoLike).includes('Z') ? '' : 'Z'));
        if (isNaN(d.getTime())) return '';
        return d.toLocaleDateString(undefined, { year: 'numeric', month: 'short', day: 'numeric' });
    } catch (e) {
        return '';
    }
}

function _wirePurchasedRowButtons(container) {
    container.querySelectorAll('.purchased-track-unmark-btn').forEach(btn => {
        btn.addEventListener('click', () => _purchasedUnmark([btn.dataset.trackId], btn.closest('.purchased-album-card')));
    });
    container.querySelectorAll('.purchased-album-unmark-btn').forEach(btn => {
        btn.addEventListener('click', () => {
            const ids = (btn.dataset.trackIds || '').split(',').filter(Boolean);
            _purchasedUnmark(ids, btn.closest('.purchased-album-card'));
        });
    });

    // Collapse/expand: the whole header is the hit target (the chevron alone is
    // a small one), but a click that landed on a real control inside it —
    // Unmark Album — must do that instead of toggling.
    container.querySelectorAll('.purchased-album-header').forEach(header => {
        header.addEventListener('click', (e) => {
            if (e.target.closest('button:not(.purchased-album-toggle)')) return;
            if (e.target.closest('a')) return;
            _togglePurchasedAlbum(header.closest('.purchased-album-card'));
        });
    });
}

function _togglePurchasedAlbum(cardEl) {
    if (!cardEl) return;
    const collapsed = !cardEl.classList.contains('purchased-album-card--collapsed');
    _setPurchasedCollapsed(cardEl.dataset.albumId, collapsed);
    _applyPurchasedCollapseState(cardEl, collapsed);
    _updatePurchasedCollapseAllBtn();
}

function _togglePurchasedAll(collapse) {
    document.querySelectorAll('#purchased-albums-list .purchased-album-card').forEach(card => {
        _setPurchasedCollapsed(card.dataset.albumId, collapse);
        _applyPurchasedCollapseState(card, collapse);
    });
    _updatePurchasedCollapseAllBtn();
}

async function _purchasedUnmark(trackIds, cardEl) {
    if (!trackIds.length) return;
    try {
        const res = await fetch('/api/library/tracks/unmark-purchased', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ track_ids: trackIds })
        });
        const data = await res.json();
        if (!data.success) throw new Error(data.error || 'Failed to update');
        if (typeof showToast === 'function') showToast(`Unmarked ${data.updated || trackIds.length} track(s)`, 'success');
        if (typeof loadToBePurchasedCount === 'function') loadToBePurchasedCount();
        // Simplest correct refresh: re-fetch — partial in-place DOM patching
        // for "some tracks in this card unmarked" isn't worth the complexity
        // for a page that's a historical record, not a hot editing surface.
        await _loadPurchasedAlbums();
    } catch (err) {
        if (typeof showToast === 'function') showToast('Could not unmark: ' + (err.message || err), 'error');
    }
}
