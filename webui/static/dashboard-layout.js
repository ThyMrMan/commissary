/*
 * Per-user dashboard layout — drag to reorder, drag the right edge to resize.
 *
 * Available to EVERY user: this is a personal view preference, not a
 * permission. The admin's hide/show policy (dashboard-widgets.js) is a
 * separate thing and still wins — a card an admin has hidden is invisible to
 * this module entirely: never shown, never draggable, never a drop target.
 * That is why this file must load AFTER dashboard-widgets.js, whose
 * applyWidgetPolicy() stamps the dataset.widgetHidden flag we filter on.
 *
 * Saved in localStorage, so it is per-browser rather than per-account — the
 * same convention as every other UI preference here (soulsync-accent,
 * navSections, the Purchased collapse state...).
 *
 * Two constraints worth knowing before editing this file:
 *
 *   1. Spans are applied as a data-span ATTRIBUTE, never as an inline
 *      style.gridColumn. The grid drops to 2 columns at 1499px and 1 at 699px,
 *      where the CSS resets spans to `auto` — and a media query cannot
 *      override an inline style. Writing the span inline would silently break
 *      every narrow viewport for anyone who resized a card.
 *
 *   2. Cards are REORDERED in place, never rebuilt. init.js's cursor-glow FX
 *      caches the card elements in an array; moving nodes keeps those
 *      references valid, replacing them would not.
 */

const DASH_LAYOUT_KEY = 'soulsync-dashboard-layout';
const DASH_SPAN_MIN = 1;
const DASH_SPAN_MAX = 3;

const DASH_GRID_ROOTS = {
    music: '#dashboard-page',
    video: '[data-video-subpage="video-dashboard"]',
};

// ── storage ─────────────────────────────────────────────────────────────────
// Only DELTAS are stored: a card left at its markup position and default width
// contributes nothing. A card added in a future release then slots into its
// shipped position instead of being dropped by a stale saved order.

function _readDashLayout() {
    try {
        const raw = JSON.parse(localStorage.getItem(DASH_LAYOUT_KEY) || '{}');
        return (raw && typeof raw === 'object') ? raw : {};
    } catch (e) {
        return {};   // corrupt or unavailable storage must never blank the dashboard
    }
}

function _writeDashLayout(all) {
    try {
        localStorage.setItem(DASH_LAYOUT_KEY, JSON.stringify(all));
    } catch (e) {
        /* storage full or blocked — the layout still applies for this session */
    }
}

function getDashLayout(side) {
    const entry = _readDashLayout()[side] || {};
    return {
        order: Array.isArray(entry.order) ? entry.order.filter(id => typeof id === 'string') : [],
        spans: (entry.spans && typeof entry.spans === 'object') ? entry.spans : {},
    };
}

function _saveDashLayout(side, layout) {
    const all = _readDashLayout();
    all[side] = { order: layout.order || [], spans: layout.spans || {} };
    _writeDashLayout(all);
}

// ── DOM helpers ─────────────────────────────────────────────────────────────

function _dashGrid(side) {
    const root = document.querySelector(DASH_GRID_ROOTS[side] || DASH_GRID_ROOTS.music);
    return root ? root.querySelector('.dash-grid') : null;
}

/** Cards this user may actually arrange — admin-hidden ones are excluded. */
function _layoutCards(grid) {
    if (!grid) return [];
    return Array.from(grid.querySelectorAll('.dash-card[data-card]'))
        .filter(el => el.dataset.widgetHidden !== '1');
}

function _cardId(el) { return el ? el.dataset.card : null; }

// ── applying ────────────────────────────────────────────────────────────────

/**
 * Put the saved order and widths into effect. Idempotent — safe to call on
 * every dashboard activation.
 */
function applyDashboardLayout(side) {
    const grid = _dashGrid(side);
    if (!grid) return;
    _stampMarkupOrder(grid);   // before anything moves, so Reset has a target
    const layout = getDashLayout(side);

    // Order: append each saved id in turn, then leave everything else where it
    // already sits. appendChild MOVES an existing node, so nothing is rebuilt.
    const byId = new Map();
    _layoutCards(grid).forEach(el => byId.set(_cardId(el), el));
    layout.order.forEach(id => {
        const el = byId.get(id);
        if (el) grid.appendChild(el);
    });
    // Cards absent from the saved order (a new release's additions) trail the
    // saved ones rather than vanishing.
    byId.forEach((el, id) => {
        if (!layout.order.includes(id)) grid.appendChild(el);
    });

    for (const el of _layoutCards(grid)) {
        const span = parseInt(layout.spans[_cardId(el)], 10);
        if (span >= DASH_SPAN_MIN && span <= DASH_SPAN_MAX) {
            el.setAttribute('data-span', String(span));
        } else {
            el.removeAttribute('data-span');
        }
    }
}

function _persistOrder(side) {
    const grid = _dashGrid(side);
    if (!grid) return;
    const layout = getDashLayout(side);
    layout.order = _layoutCards(grid).map(_cardId).filter(Boolean);
    _saveDashLayout(side, layout);
}

function _persistSpan(side, cardId, span) {
    const layout = getDashLayout(side);
    if (span && span !== _defaultSpanOf(side, cardId)) layout.spans[cardId] = span;
    else delete layout.spans[cardId];
    _saveDashLayout(side, layout);
}

/** The width the card ships with, so "back to default" stores no delta. */
function _defaultSpanOf(side, cardId) {
    const grid = _dashGrid(side);
    const el = grid ? grid.querySelector(`.dash-card[data-card="${cardId}"]`) : null;
    if (!el) return 1;
    if (el.classList.contains('dash-card--full')) return 3;
    if (el.classList.contains('dash-card--wide')) return 2;
    return 1;
}

// ── edit mode ───────────────────────────────────────────────────────────────

const _dashEditing = new Set();

function isDashboardEditing(side) { return _dashEditing.has(side); }

function toggleDashboardEditMode(side) {
    if (_dashEditing.has(side)) _exitDashEdit(side);
    else _enterDashEdit(side);
}

function _enterDashEdit(side) {
    const grid = _dashGrid(side);
    if (!grid) return;
    _dashEditing.add(side);
    grid.classList.add('dash-grid--editing');

    for (const el of _layoutCards(grid)) {
        el.draggable = true;
        el.addEventListener('dragstart', _onDashDragStart);
        el.addEventListener('dragend', _onDashDragEnd);
        el.addEventListener('dragover', _onDashDragOver);
        el.addEventListener('dragleave', _onDashDragLeave);
        el.addEventListener('drop', _onDashDrop);
        if (!el.querySelector('.dash-card__resize-grip')) {
            const grip = document.createElement('div');
            grip.className = 'dash-card__resize-grip';
            grip.tabIndex = 0;
            grip.setAttribute('role', 'separator');
            grip.setAttribute('aria-orientation', 'vertical');
            grip.setAttribute('aria-label', 'Resize card — drag, or use the left and right arrow keys');
            grip.addEventListener('pointerdown', _onGripPointerDown);
            grip.addEventListener('keydown', _onGripKeyDown);
            el.appendChild(grip);
        }
    }
    _renderDashEditBar(side, grid);
    _setCustomizeButtonState(side, true);
}

function _exitDashEdit(side) {
    const grid = _dashGrid(side);
    if (!grid) return;
    _dashEditing.delete(side);
    grid.classList.remove('dash-grid--editing');

    for (const el of Array.from(grid.querySelectorAll('.dash-card[data-card]'))) {
        el.draggable = false;
        el.removeEventListener('dragstart', _onDashDragStart);
        el.removeEventListener('dragend', _onDashDragEnd);
        el.removeEventListener('dragover', _onDashDragOver);
        el.removeEventListener('dragleave', _onDashDragLeave);
        el.removeEventListener('drop', _onDashDrop);
        el.classList.remove('dragging', 'drag-over');
        const grip = el.querySelector('.dash-card__resize-grip');
        if (grip) grip.remove();
    }
    const bar = grid.parentElement && grid.parentElement.querySelector('.dash-edit-bar');
    if (bar) bar.remove();
    _setCustomizeButtonState(side, false);
}

function _setCustomizeButtonState(side, editing) {
    const root = document.querySelector(DASH_GRID_ROOTS[side] || DASH_GRID_ROOTS.music);
    const btn = root ? root.querySelector('[data-dash-customize]') : null;
    if (!btn) return;
    btn.classList.toggle('active', !!editing);
    btn.setAttribute('aria-pressed', String(!!editing));
    const label = btn.querySelector('.dash-customize-label');
    if (label) label.textContent = editing ? 'Done' : 'Customize';
}

function _renderDashEditBar(side, grid) {
    if (grid.parentElement.querySelector('.dash-edit-bar')) return;
    const bar = document.createElement('div');
    bar.className = 'dash-edit-bar';
    bar.innerHTML = `
        <span class="dash-edit-bar__hint">Drag a card to move it. Drag its right edge — or focus the handle and press &larr;/&rarr; — to change its width.</span>
        <button type="button" class="test-button dash-edit-bar__reset">Reset layout</button>`;
    bar.querySelector('.dash-edit-bar__reset')
       .addEventListener('click', () => resetDashboardLayout(side));
    grid.parentElement.insertBefore(bar, grid);
}

// ── drag to reorder (mirrors buildHybridSourceList in settings.js) ───────────

let _dashDragSide = null;

function _sideOfCard(el) {
    return el.closest('[data-video-subpage="video-dashboard"]') ? 'video' : 'music';
}

function _onDashDragStart(e) {
    const el = e.currentTarget;
    _dashDragSide = _sideOfCard(el);
    e.dataTransfer.effectAllowed = 'move';
    e.dataTransfer.setData('text/plain', _cardId(el));
    el.classList.add('dragging');
}

function _onDashDragEnd(e) {
    e.currentTarget.classList.remove('dragging');
    const grid = e.currentTarget.parentElement;
    if (grid) grid.querySelectorAll('.dash-card').forEach(c => c.classList.remove('drag-over'));
}

function _onDashDragOver(e) {
    e.preventDefault();
    e.dataTransfer.dropEffect = 'move';
    e.currentTarget.classList.add('drag-over');
}

function _onDashDragLeave(e) { e.currentTarget.classList.remove('drag-over'); }

function _onDashDrop(e) {
    e.preventDefault();
    const target = e.currentTarget;
    target.classList.remove('drag-over');
    const draggedId = e.dataTransfer.getData('text/plain');
    const targetId = _cardId(target);
    if (!draggedId || draggedId === targetId) return;

    const side = _dashDragSide || _sideOfCard(target);
    const grid = _dashGrid(side);
    const dragged = grid && grid.querySelector(`.dash-card[data-card="${draggedId}"]`);
    if (!dragged) return;

    // Dropping onto a card LATER in the grid puts the dragged card after it,
    // earlier puts it before — what the pointer implies either way.
    const cards = _layoutCards(grid);
    const movingForward = cards.indexOf(dragged) < cards.indexOf(target);
    grid.insertBefore(dragged, movingForward ? target.nextSibling : target);
    _persistOrder(side);
}

// ── resize ──────────────────────────────────────────────────────────────────

function _spanOf(el, side) {
    const attr = parseInt(el.getAttribute('data-span'), 10);
    return (attr >= DASH_SPAN_MIN && attr <= DASH_SPAN_MAX)
        ? attr : _defaultSpanOf(side, _cardId(el));
}

function _setSpan(el, side, span) {
    const clamped = Math.max(DASH_SPAN_MIN, Math.min(DASH_SPAN_MAX, span));
    // Attribute, NOT style.gridColumn — see the file header.
    el.setAttribute('data-span', String(clamped));
    _persistSpan(side, _cardId(el), clamped);
    return clamped;
}

/** How many grid columns the viewport currently has (3 / 2 / 1). */
function _gridColumnCount(grid) {
    const tpl = getComputedStyle(grid).gridTemplateColumns || '';
    const n = tpl.trim().split(/\s+/).filter(Boolean).length;
    return n > 0 ? n : DASH_SPAN_MAX;
}

function _onGripPointerDown(e) {
    const grip = e.currentTarget;
    const card = grip.closest('.dash-card');
    const grid = card && card.parentElement;
    if (!grid) return;
    e.preventDefault();
    grip.setPointerCapture(e.pointerId);

    const side = _sideOfCard(card);
    const columns = _gridColumnCount(grid);
    const gap = parseFloat(getComputedStyle(grid).columnGap) || 0;
    const step = (grid.getBoundingClientRect().width - gap * (columns - 1)) / columns + gap;
    const startX = e.clientX;
    const startSpan = _spanOf(card, side);

    const onMove = (ev) => {
        const delta = Math.round((ev.clientX - startX) / step);
        const want = startSpan + delta;
        if (want !== _spanOf(card, side)) _setSpan(card, side, want);
    };
    const onUp = () => {
        grip.removeEventListener('pointermove', onMove);
        grip.removeEventListener('pointerup', onUp);
        grip.removeEventListener('pointercancel', onUp);
    };
    grip.addEventListener('pointermove', onMove);
    grip.addEventListener('pointerup', onUp);
    grip.addEventListener('pointercancel', onUp);
}

function _onGripKeyDown(e) {
    if (e.key !== 'ArrowLeft' && e.key !== 'ArrowRight') return;
    const card = e.currentTarget.closest('.dash-card');
    if (!card) return;
    e.preventDefault();
    const side = _sideOfCard(card);
    _setSpan(card, side, _spanOf(card, side) + (e.key === 'ArrowRight' ? 1 : -1));
}

// ── reset ───────────────────────────────────────────────────────────────────

function resetDashboardLayout(side) {
    const all = _readDashLayout();
    delete all[side];
    _writeDashLayout(all);

    const grid = _dashGrid(side);
    if (!grid) return;
    grid.querySelectorAll('.dash-card[data-span]').forEach(el => el.removeAttribute('data-span'));
    // Markup order is the source of truth for "default", and the cards were
    // only ever moved, never replaced — so re-appending in document order
    // restores exactly what shipped.
    const inMarkupOrder = Array.from(grid.querySelectorAll('.dash-card[data-card]'))
        .sort((a, b) => (a.dataset.markupIndex | 0) - (b.dataset.markupIndex | 0));
    inMarkupOrder.forEach(el => grid.appendChild(el));
}

/** Remember where each card started so Reset has something to return to.
 *  Idempotent, and must run before the first reorder — hence the call at the
 *  top of applyDashboardLayout rather than only at startup. */
function _stampMarkupOrder(grid) {
    Array.from(grid.querySelectorAll('.dash-card[data-card]')).forEach((el, i) => {
        if (el.dataset.markupIndex === undefined) el.dataset.markupIndex = String(i);
    });
}

function initDashboardLayout() {
    applyDashboardLayout('music');
    applyDashboardLayout('video');
}

// Both Customize buttons are wired by DELEGATION rather than an inline
// onclick: the video side forbids inline handlers (it would break its
// script-split integrity contract), and one listener covers both sides.
document.addEventListener('click', (e) => {
    const btn = e.target.closest && e.target.closest('[data-dash-customize]');
    if (!btn) return;
    e.preventDefault();
    toggleDashboardEditMode(btn.getAttribute('data-dash-customize') || 'music');
});
