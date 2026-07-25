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

const DASHBOARD_WIDGETS = [
    // --- Music dashboard -------------------------------------------------
    { id: 'music.services',         side: 'music', card: 'services',         label: 'Service Status' },
    { id: 'music.stats',            side: 'music', card: 'stats',            label: 'System Stats' },
    { id: 'music.library',          side: 'music', card: 'library',          label: 'Library' },
    { id: 'music.syncs',            side: 'music', card: 'syncs',            label: 'Recent Syncs' },
    { id: 'music.tools',            side: 'music', card: 'tools',            label: 'Quick Actions' },
    { id: 'music.activity',         side: 'music', card: 'activity',         label: 'Recent Activity' },
    { id: 'music.active-downloads', side: 'music', card: 'active-downloads', label: 'Active Downloads' },
    { id: 'music.enrichment',       side: 'music', card: 'enrichment',       label: 'Enrichment Services' },
    // Not a dash-card: the operator controls in the page header (enrichment
    // triggers, Library Repair, SoulID, Manage Workers). The Watchlist/Wishlist
    // quick-nav is a SIBLING of .header-actions, so it is never hidden by this.
    {
        id: 'music.header-enrich', side: 'music', label: 'Header enrichment controls',
        selector: '.dashboard-header .header-actions',
    },

    // --- Video dashboard -------------------------------------------------
    { id: 'video.recent',           side: 'video', card: 'recent',           label: 'Recently Added' },
    { id: 'video.stats',            side: 'video', card: 'stats',            label: 'System Stats' },
    { id: 'video.library',          side: 'video', card: 'library',          label: 'Library' },
    { id: 'video.upcoming',         side: 'video', card: 'upcoming',         label: 'Upcoming' },
    { id: 'video.tools',            side: 'video', card: 'tools',            label: 'Quick Actions' },
    { id: 'video.studios',          side: 'video', card: 'studios',          label: 'Studios' },
    {
        id: 'video.header-enrich', side: 'video', label: 'Header enrichment controls',
        selector: '.dashboard-header .header-actions',
    },
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

function widgetElement(widget) {
    const root = document.querySelector(widget.side === 'video' ? VIDEO_DASH_ROOT : MUSIC_DASH_ROOT);
    if (!root) return null;
    return root.querySelector(widget.selector || `[data-card="${widget.card}"]`);
}

/**
 * Apply the policy to the DOM. Idempotent — safe to call on every dashboard
 * activation as well as once at startup.
 */
function applyWidgetPolicy() {
    for (const widget of DASHBOARD_WIDGETS) {
        const el = widgetElement(widget);
        if (!el) continue;

        if (isWidgetVisible(widget.id)) {
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
