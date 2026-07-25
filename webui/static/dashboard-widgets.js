/*
 * Dashboard widget visibility.
 *
 * The admin picks, in Settings, which dashboard cards non-admin profiles see
 * (config: dashboard_widgets.member_hidden). Admins always see everything.
 *
 * Two things make this work:
 *   - Every card on both dashboards already carries data-card="...", so there
 *     is nothing to re-tag.
 *   - The policy rides along on GET /api/profiles/current, which members CAN
 *     read (/api/settings is admin-only), and which is awaited before any page
 *     renders — so cards never flash in and then vanish.
 *
 * IDs are namespaced by side on purpose: "stats", "library" and "tools" each
 * exist as a data-card value on BOTH dashboards, so an unscoped selector would
 * hide the wrong card. Every lookup below is rooted at its side's page element.
 *
 * Must stay in sync with VALID_WIDGET_IDS in web_server.py —
 * tests/test_dashboard_widgets.py asserts both directions, and also that this
 * list matches the data-card attributes actually present in index.html.
 */

const MUSIC_DASH_ROOT = '#dashboard-page';
const VIDEO_DASH_ROOT = '[data-video-subpage="video-dashboard"]';

// Everything in .header-actions except the Manage Workers button, which is its
// own widget. .em-manage-btn is the only non-container child on either side.
const HEADER_ICONS_SELECTOR = '.dashboard-header .header-actions > *:not(.em-manage-btn)';
const MANAGE_WORKERS_SELECTOR = '.dashboard-header .header-actions .em-manage-btn';

// `kind` groups the checkboxes in Settings. `page` ties a nav entry to its page
// id so hiding it also BLOCKS navigation (isPageHiddenByPolicy, consumed by
// isPageAllowed) — otherwise the URL would still work and the toggle would be
// decorative. `global: true` means the element lives outside the dashboards
// (the sidebar), so it is looked up document-wide instead of per-side; those
// selectors name their side explicitly.
const DASHBOARD_WIDGETS = [
    // --- Music dashboard -------------------------------------------------
    { id: 'music.services',         side: 'music', kind: 'card', card: 'services',         label: 'Service Status' },
    { id: 'music.stats',            side: 'music', kind: 'card', card: 'stats',            label: 'System Stats' },
    { id: 'music.library',          side: 'music', kind: 'card', card: 'library',          label: 'Library' },
    { id: 'music.syncs',            side: 'music', kind: 'card', card: 'syncs',            label: 'Recent Syncs' },
    { id: 'music.tools',            side: 'music', kind: 'card', card: 'tools',            label: 'Quick Actions' },
    { id: 'music.activity',         side: 'music', kind: 'card', card: 'activity',         label: 'Recent Activity' },
    { id: 'music.active-downloads', side: 'music', kind: 'card', card: 'active-downloads', label: 'Active Downloads' },
    { id: 'music.enrichment',       side: 'music', kind: 'card', card: 'enrichment',       label: 'Enrichment Services' },

    // --- Music dashboard header ------------------------------------------
    // The Watchlist/Wishlist quick-nav is a SIBLING of .header-actions, so it
    // is never caught by either of these.
    { id: 'music.header-enrich',   side: 'music', kind: 'header', label: 'Enrichment, Repair & SoulID icons', selector: HEADER_ICONS_SELECTOR },
    { id: 'music.manage-workers',  side: 'music', kind: 'header', label: 'Manage Workers button',            selector: MANAGE_WORKERS_SELECTOR },

    // --- Music sidebar: the System group ---------------------------------
    { id: 'music.nav-automations', side: 'music', kind: 'nav', page: 'automations', label: 'Automations', selector: '.nav-button[data-page="automations"]', global: true },
    { id: 'music.nav-chat',        side: 'music', kind: 'nav', page: 'chat',        label: 'Chat',        selector: '.nav-button[data-page="chat"]',        global: true },
    { id: 'music.nav-tools',       side: 'music', kind: 'nav', page: 'tools',       label: 'Tools',       selector: '.nav-button[data-page="tools"]',       global: true },

    // --- Video dashboard -------------------------------------------------
    { id: 'video.recent',           side: 'video', kind: 'card', card: 'recent',           label: 'Recently Added' },
    { id: 'video.stats',            side: 'video', kind: 'card', card: 'stats',            label: 'System Stats' },
    { id: 'video.library',          side: 'video', kind: 'card', card: 'library',          label: 'Library' },
    { id: 'video.upcoming',         side: 'video', kind: 'card', card: 'upcoming',         label: 'Upcoming' },
    { id: 'video.tools',            side: 'video', kind: 'card', card: 'tools',            label: 'Quick Actions' },
    { id: 'video.studios',          side: 'video', kind: 'card', card: 'studios',          label: 'Studios' },

    // --- Video dashboard header ------------------------------------------
    { id: 'video.header-enrich',   side: 'video', kind: 'header', label: 'Enrichment icons',      selector: HEADER_ICONS_SELECTOR },
    { id: 'video.manage-workers',  side: 'video', kind: 'header', label: 'Manage Workers button', selector: MANAGE_WORKERS_SELECTOR },

    // --- Video sidebar: the System group ---------------------------------
    // No video.nav-automations: video Automations is ALREADY admin-only
    // (VIDEO_ADMIN_ONLY in init.js), so a checkbox for it would do nothing.
    { id: 'video.nav-chat',  side: 'video', kind: 'nav', page: 'video-chat',  label: 'Chat',  selector: '.video-nav .nav-button[data-video-page="video-chat"]',  global: true },
    { id: 'video.nav-tools', side: 'video', kind: 'nav', page: 'video-tools', label: 'Tools', selector: '.video-nav .nav-button[data-video-page="video-tools"]', global: true },

    // --- App chrome (neither side — floats over both) ---------------------
    // helper.js only ever toggles CLASSES on this button (active, menu-open,
    // undiscovered, has-badge), never style.display, so hiding it here can't
    // be fought back. The What's New changelog has its own version button and
    // is unaffected.
    { id: 'shared.help-button', side: 'shared', kind: 'chrome', label: 'Interactive Help (? button)', selector: '#helper-float-btn', global: true },
];

// Populated from the /api/profiles/current payload. `null` means "not loaded
// yet" and is deliberately distinct from `[]` ("loaded, nothing hidden") —
// see isWidgetVisible, which fails OPEN while this is null.
let dashboardWidgetsHidden = null;
let dashboardWidgetsIsAdmin = true;

function setDashboardWidgetPolicy(hiddenIds, isAdmin) {
    dashboardWidgetsHidden = Array.isArray(hiddenIds) ? hiddenIds : [];
    dashboardWidgetsIsAdmin = !!isAdmin;
}

/**
 * Should this widget render for the current user?
 *
 * Fails open on purpose: an unknown id, an admin, or a policy that hasn't
 * arrived yet all yield `true`. A failed profile fetch degrades to today's
 * behavior (everything visible) rather than an empty dashboard.
 */
function isWidgetVisible(widgetId) {
    if (dashboardWidgetsIsAdmin) return true;
    if (!dashboardWidgetsHidden) return true;
    return !dashboardWidgetsHidden.includes(widgetId);
}

/**
 * Is this PAGE hidden by the policy? Consumed by isPageAllowed() so a hidden
 * nav entry also blocks navigation — without it the page stays reachable by
 * URL and the toggle is purely decorative.
 */
function isPageHiddenByPolicy(pageId) {
    const widget = DASHBOARD_WIDGETS.find(w => w.page === pageId);
    return !!widget && !isWidgetVisible(widget.id);
}

function widgetElements(widget) {
    const selector = widget.selector || `[data-card="${widget.card}"]`;
    if (widget.global) return document.querySelectorAll(selector);
    const root = document.querySelector(widget.side === 'video' ? VIDEO_DASH_ROOT : MUSIC_DASH_ROOT);
    return root ? root.querySelectorAll(selector) : [];
}

/**
 * Apply the policy to the DOM. Idempotent — safe to call on every dashboard
 * activation as well as once at startup.
 */
function applyWidgetPolicy() {
    for (const widget of DASHBOARD_WIDGETS) {
        const visible = isWidgetVisible(widget.id);
        for (const el of widgetElements(widget)) {
            if (visible) {
                // Only undo OUR hiding. Active Downloads ships with an inline
                // display:none and is revealed by its own JS when a download
                // starts — clearing that here would force an empty card open.
                if (el.dataset.widgetHidden === '1') {
                    delete el.dataset.widgetHidden;
                    el.style.display = el.dataset.widgetPrevDisplay || '';
                    delete el.dataset.widgetPrevDisplay;
                }
            } else if (el.dataset.widgetHidden !== '1') {
                el.dataset.widgetPrevDisplay = el.style.display || '';
                el.dataset.widgetHidden = '1';
                el.style.display = 'none';
            }
        }
    }
}
