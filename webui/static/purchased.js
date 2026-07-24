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
};

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
            if (emptyEl) {
                emptyEl.classList.remove('hidden');
                const heading = emptyEl.querySelector('h3');
                if (heading) heading.textContent = purchasedPageState.search ? 'No matches' : 'Nothing purchased yet';
            }
        } else {
            listEl.innerHTML = albums.map(_purchasedAlbumCardHtml).join('');
            _wirePurchasedRowButtons(listEl);
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

    const trackRows = (album.tracks || []).map(t => `
        <div class="purchased-track-row">
            <span class="purchased-track-num">${t.track_number || ''}</span>
            <span class="purchased-track-title">${escapeHtml(t.title || 'Untitled')}</span>
            <span class="purchased-track-date">${_formatPurchasedDate(t.purchased_at)}</span>
            <button class="btn btn--secondary purchased-track-unmark-btn" data-track-id="${escapeHtml(String(t.id))}" title="Undo this purchase record">Unmark</button>
        </div>
    `).join('');

    return `
        <div class="purchased-album-card" data-album-id="${escapeHtml(String(album.album_id))}">
            <div class="purchased-album-header">
                ${thumb}
                <div class="purchased-album-info">
                    <div class="purchased-album-title">${escapeHtml(album.album_title || 'Unknown Album')}</div>
                    <div class="purchased-album-artist">${escapeHtml(album.artist_name || 'Unknown Artist')}</div>
                </div>
                <span class="${badgeCls}">${album.purchased_count}/${album.total_track_count} purchased</span>
                <span class="purchased-album-date">${lastDate}</span>
                <button class="btn btn--secondary purchased-album-unmark-btn" data-album-id="${escapeHtml(String(album.album_id))}" data-track-ids="${escapeHtml(trackIds.join(','))}" title="Undo the purchase record for every track shown below">Unmark Album</button>
            </div>
            <div class="purchased-track-rows">${trackRows}</div>
        </div>
    `;
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
