// ===============================
// INTERACTIVE CONTEXTUAL HELP SYSTEM V2
// ===============================

// ── State ────────────────────────────────────────────────────────────────

const HelperState = {
    mode: null,           // null | 'info' | 'tour' | 'search' | 'shortcuts' | 'setup' | 'whats-new' | 'troubleshoot'
    menuOpen: false,
    tourStep: 0,
    tourId: null,
    setupData: null,
};

let helperModeActive = false;
let _helperPopover = null;
let _helperHighlighted = null;
let _helperMenu = null;
let _tourOverlay = null;
let _setupPanel = null;
let _shortcutsOverlay = null;
let _helperSearchPanel = null;
let _troubleshootActive = false;

// ── Content Database ─────────────────────────────────────────────────────
// Keys: CSS selectors matched via element.matches()
// Values: { title, description, tips[], docsId (optional — links to help page section) }

const HELPER_CONTENT = {

    // ─── SIDEBAR NAVIGATION ─────────────────────────────────────────

    '.nav-button[data-page="dashboard"]': {
        title: 'System Dashboard',
        description: 'Your central command center for monitoring system health, managing background operations, and running maintenance tools. Service connections, download stats, and system resources are all visible at a glance.',
        tips: [
            'Service cards show real-time connection status with response times',
            'Tools run database updates, quality scans, backups, and more',
            'Activity feed tracks every operation in real-time via WebSocket'
        ],
        docsId: 'dashboard'
    },
    '.nav-button[data-page="sync"]': {
        title: 'Playlist Sync',
        description: 'Mirror playlists from Spotify, YouTube, Tidal, Deezer, ListenBrainz, and Beatport. Commissary matches each track to your download sources and downloads what\'s missing from your library.',
        tips: [
            'Select playlists from the left panel to begin syncing',
            'Real-time progress shows matched, pending, and failed tracks',
            'Synced playlists are monitored for changes on future syncs'
        ],
        docsId: 'sync'
    },
    '.nav-button[data-page="downloads"]': {
        title: 'Music Search & Downloads',
        description: 'Search for music across all your configured metadata sources and download from Soulseek, YouTube, Tidal, Qobuz, HiFi, or Deezer. Enhanced Search shows categorized results; Basic Search gives raw Soulseek results with filters.',
        tips: [
            'Enhanced Search: click an album to download, click a track to search sources',
            'Multi-source tabs let you compare results across Spotify, iTunes, and Deezer',
            'Play button previews tracks from your download source before committing'
        ],
        docsId: 'search'
    },
    '.nav-button[data-page="discover"]': {
        title: 'Discover New Music',
        description: 'Personalized music discovery through genre exploration, similar artists, seasonal picks, curated playlists, and recommendations based on your library and listening habits.',
        tips: [
            'Genre Explorer combines data from all your metadata sources',
            'Similar artists are generated from your watchlist artists',
            'Time Machine lets you browse music by decade'
        ],
        docsId: 'discover'
    },
    '.nav-button[data-page="artists"]': {
        title: 'Artist Browser',
        description: 'Search for any artist and explore their full discography — albums, singles, and EPs with one-click download. View rich artist profiles with bio, stats, genres, and service links.',
        tips: [
            'Click any album card to open the download modal with track selection',
            'Similar artists appear below the discography for discovery',
            'Add artists to your Watchlist for automatic new release monitoring'
        ],
        docsId: 'artists'
    },
    '.nav-button[data-page="automations"]': {
        title: 'Automation Hub',
        description: 'Build automated workflows with a visual builder: WHEN something happens → DO an action → THEN notify. Schedule tasks, chain operations with signals, and get alerts via Discord, Pushbullet, Telegram, or Gotify.',
        tips: [
            'Signals let you chain multiple automations together',
            'Schedule automations daily, weekly, or triggered by events',
            'Built-in actions include library scans, watchlist checks, and quality scans'
        ],
        docsId: 'automations'
    },
    '.nav-button[data-page="library"]': {
        title: 'Music Library',
        description: 'Browse your complete collection organized by artists. Click any artist to see their albums with ownership stats. Enhanced view enables inline metadata editing, tag writing, and bulk operations.',
        tips: [
            'Enhanced view toggle on artist detail pages enables advanced management',
            'Write tags directly to audio files (MP3, FLAC, OGG, M4A)',
            'Bulk select tracks across albums for batch operations'
        ],
        docsId: 'library'
    },
    '.nav-button[data-page="active-downloads"]': {
        title: 'Downloads',
        description: 'Centralized view of every download across the entire app. Shows live status for all tracks from Sync, Discover, Artists, Search, and Wishlist in one place.',
        tips: [
            'Filter by status: Active, Queued, Completed, Failed',
            'Badge on the nav button shows active download count from any page',
            'Clear Completed button removes finished items from the list'
        ]
    },
    '.nav-button[data-page="playlist-explorer"]': {
        title: 'Playlist Explorer',
        description: 'Visual exploration tool for playlists. Browse album art grids or full discographies from any playlist source. Select tracks to add to wishlist or download directly.',
        tips: [
            'Toggle between Albums view and Full Discog view',
            'Select multiple tracks across albums for batch operations',
            'Works with Spotify, Tidal, Deezer, and ListenBrainz playlists'
        ]
    },
    '.nav-button[data-page="stats"]': {
        title: 'Library Statistics',
        description: 'Detailed analytics — genre breakdowns, format distribution, quality analysis, collection growth, and enrichment coverage across all metadata services.',
        docsId: 'dashboard'
    },
    '.nav-button[data-page="import"]': {
        title: 'Music Import',
        description: 'Import music files from your import folder. Commissary identifies tracks using AcoustID fingerprinting, matches them to metadata, and organizes them into your library with proper tagging.',
        docsId: 'import'
    },
    '.nav-button[data-page="settings"]': {
        title: 'Settings',
        description: 'Configure everything — service credentials, download sources, quality profiles, file organization templates, processing options, and media server connections.',
        tips: [
            'Connect your metadata source (Spotify, iTunes, or Deezer) first',
            'Set up your media server (Plex, Jellyfin, or Navidrome)',
            'Quality Profile controls which audio formats and bitrates are preferred'
        ],
        docsId: 'settings'
    },
    '.nav-button[data-page="issues"]': {
        title: 'Issues & Repair',
        description: 'Automated library health scanner that finds and fixes problems — dead files, missing covers, duplicates, incomplete albums, metadata gaps, and more. Each finding can be auto-fixed or dismissed.',
        tips: [
            'The nav badge shows pending issue count',
            'Run individual repair jobs or scan everything at once',
            'Auto-fix handles most issues; manual review for edge cases'
        ]
    },
    '.nav-button[data-page="help"]': {
        title: 'Help & Documentation',
        description: 'Comprehensive documentation covering every feature, complete API reference, workflow guides, and troubleshooting. Fully searchable.',
        docsId: 'getting-started'
    },

    // ─── SIDEBAR: PLAYER & STATUS ───────────────────────────────────

    '#media-player': {
        title: 'Media Player',
        description: 'Stream music directly from your media server. Play tracks from search results, library, or discovery playlists. Supports play/pause, seek, volume, and queue management.',
        tips: [
            'Click any track\'s play button anywhere in the app to start streaming',
            'Queue tracks from the Enhanced Library view or search results',
            'Integrates with your OS media controls (lock screen, system tray)'
        ],
        docsId: 'player'
    },
    '.version-button': {
        title: 'Version & Changelog',
        description: 'Shows the current Commissary version. Click to see the full release notes, changelog, and what\'s new.',
    },
    '.support-button': {
        title: 'Support & Community',
        description: 'Links to the Commissary community Discord, GitHub issues for bug reports, and documentation resources.',
    },
    '#metadata-source-indicator': {
        title: 'Metadata Source',
        description: 'Connection status of your primary metadata source. This service provides artist, album, and track information for searches, enrichment, and discovery.',
        tips: [
            'Green dot = connected and responding',
            'Red dot = disconnected or erroring',
            'iTunes and Deezer work without authentication; Spotify requires OAuth'
        ],
        docsId: 'gs-connecting'
    },
    '#media-server-indicator': {
        title: 'Media Server',
        description: 'Connection to your music server where your library lives. Commissary reads your collection from here and triggers scans after new downloads.',
        tips: [
            'Supports Plex, Jellyfin, and Navidrome',
            'Configure in Settings → Media Server Setup',
            'Auto-scans your library after every successful download'
        ],
        docsId: 'set-media'
    },
    '#soulseek-indicator': {
        title: 'Download Source',
        description: 'Status of your active download source. Shows the primary source in your configuration — Soulseek, YouTube, Tidal, Qobuz, HiFi, or Deezer.',
        tips: [
            'Hybrid mode tries multiple sources in priority order',
            'Each streaming source has independent quality settings',
            'Configure source priority via drag-and-drop in Settings'
        ],
        docsId: 'search-sources'
    },

    // ─── DASHBOARD: HEADER BUTTONS ──────────────────────────────────

    '#watchlist-button': {
        title: 'Watchlist',
        description: 'Artists you\'re following for new releases. Commissary periodically scans for new albums and singles from these artists and adds them to your Wishlist for download.',
        tips: [
            'Add artists from the Artists page or Library page',
            'Badge shows total watched artist count',
            'New releases trigger the "New Watchlist Release" automation event',
            'Watchlist scans also build the Discovery Pool for recommendations'
        ],
        docsId: 'art-watchlist'
    },
    '#wishlist-button': {
        title: 'Wishlist',
        description: 'Tracks queued for download. Failed downloads, watchlist new releases, and manually added tracks all land here. Process the wishlist to retry downloads.',
        tips: [
            'Badge shows total wishlist track count',
            'Click to open the wishlist modal with all pending tracks',
            'Process All starts downloading every wishlist item',
            'Tracks can be added manually or arrive from failed batch downloads'
        ],
        docsId: 'art-wishlist'
    },
    '#import-button': {
        title: 'Quick Import',
        description: 'Shortcut to the Import page. Drop music files in your import folder and import them into your library with metadata matching and tagging.',
        docsId: 'import'
    },

    // ─── DASHBOARD: SERVICE CARDS ───────────────────────────────────

    '#metadata-source-service-card': {
        title: 'Metadata Source Status',
        description: 'Detailed connection info for your active metadata source. Shows connection state, response latency, and allows manual connection testing.',
        tips: [
            '"Test Connection" verifies the API is responding',
            'Response time indicates network latency to the service',
            'If stuck on "Checking...", the service may be rate-limited'
        ],
        docsId: 'gs-connecting',
        actions: [
            { label: 'Open Settings', onClick: () => navigateToPage('settings') },
            { label: 'View Docs', onClick: () => _navigateToDocsSection('gs-connecting') }
        ]
    },
    '#media-server-service-card': {
        title: 'Media Server Status',
        description: 'Detailed connection info for your media server. Verifies Commissary can communicate with Plex, Jellyfin, or Navidrome for library scanning and audio streaming.',
        tips: [
            '"Test Connection" verifies the server URL and credentials',
            'Select your Music Library in Settings after first connecting',
            'Navidrome auto-detects new files — no scan trigger needed'
        ],
        docsId: 'set-media',
        actions: [
            { label: 'Open Settings', onClick: () => navigateToPage('settings') },
            { label: 'View Docs', onClick: () => _navigateToDocsSection('set-media') }
        ]
    },
    '#soulseek-service-card': {
        title: 'Download Source Status',
        description: 'Connection status of your primary download source. For Soulseek, this checks the slskd API; for streaming sources, it verifies authentication.',
        tips: [
            '"Test Connection" confirms the source is ready for downloads',
            'Soulseek requires a running slskd instance with API key',
            'Streaming sources (Tidal, Qobuz) need active subscriptions'
        ],
        docsId: 'search-sources',
        actions: [
            { label: 'Open Settings', onClick: () => { navigateToPage('settings'); setTimeout(() => typeof switchSettingsTab === 'function' && switchSettingsTab('downloads'), 400); } },
            { label: 'View Docs', onClick: () => _navigateToDocsSection('search-sources') }
        ]
    },

    // ─── DASHBOARD: SYSTEM STATS ────────────────────────────────────

    '#active-downloads-card': {
        title: 'Active Downloads',
        description: 'Tracks currently being downloaded across all configured sources — Soulseek P2P transfers, YouTube audio extraction, and streaming source downloads.',
    },
    '#finished-downloads-card': {
        title: 'Finished Downloads',
        description: 'Completed downloads this session. These tracks have been processed through the full pipeline — verification, tagging, cover art, file organization, and media server scan.',
    },
    '#download-speed-card': {
        title: 'Download Speed',
        description: 'Aggregate download throughput across all active transfers. Speed depends on your sources — Soulseek varies by peer; streaming sources are typically consistent.',
    },
    '#active-syncs-card': {
        title: 'Active Syncs',
        description: 'Playlist sync operations currently in progress. Each sync matches tracks against your library, searches download sources for missing ones, and downloads them.',
    },
    '#uptime-card': {
        title: 'System Uptime',
        description: 'Time since last Commissary restart. Background workers (metadata enrichment, watchlist scanner, repair jobs) run continuously during uptime.',
    },
    '#memory-card': {
        title: 'Memory Usage',
        description: 'RAM consumed by the Commissary process. Includes web server, all background workers, metadata caches, and WebSocket connections.',
    },

    // ─── DASHBOARD: TOOL CARDS ──────────────────────────────────────

    '#db-updater-card': {
        title: 'Database Updater',
        description: 'Syncs your media server\'s library into Commissary\'s database. Three modes: Incremental (fast, new content only), Full Refresh (rebuilds everything), and Deep Scan (finds stale entries).',
        tips: [
            'Run after adding music outside of Commissary',
            'Incremental runs in seconds; Full Refresh takes longer',
            'Deep Scan removes tracks deleted from your media server'
        ],
        docsId: 'dashboard'
    },
    '#metadata-updater-card': {
        title: 'Metadata Enrichment',
        description: 'Background workers that enrich your library with data from 9 services — Spotify, MusicBrainz, Deezer, Last.fm, iTunes, AudioDB, Genius, Tidal, and Qobuz. Adds genres, bios, cover art, IDs, and more.',
        tips: [
            'Runs automatically at the configured interval',
            'Each service enriches different metadata fields',
            'Check coverage per-artist in the Library\'s Enhanced view'
        ],
        docsId: 'dashboard'
    },
    '#duplicate-cleaner-card': {
        title: 'Duplicate Cleaner',
        description: 'Scans your library for duplicate tracks by comparing title, artist, album, and file characteristics. Reviews duplicates before taking any action.',
        tips: [
            'Shows total space savings from cleanup',
            'Nothing is deleted without your review',
            'Safe to run regularly'
        ],
        docsId: 'dashboard'
    },
    '#discovery-pool-card': {
        title: 'Discovery Pool',
        description: 'Collection of tracks from similar artists discovered during watchlist scans. Matched tracks feed the Discover page\'s personalized playlists and genre browser. Failed matches can be fixed manually.',
        tips: [
            'Click "Open Discovery Pool" to review matched and failed tracks',
            '"Rematch" button on matched tracks lets you pick a different match',
            'Search filter helps find specific tracks in large pools'
        ],
        docsId: 'discover'
    },
    '#retag-tool-card': {
        title: 'Retag Tool',
        description: 'Queue of tracks needing metadata corrections. When enrichment detects better metadata than what\'s in your files, corrections appear here for batch review.',
        tips: [
            'Groups corrections by artist for efficient processing',
            'Preview all changes before applying',
            'Writes corrected tags directly to audio files'
        ]
    },
    '#media-scan-card': {
        title: 'Media Server Scan',
        description: 'Manually trigger a library scan on your media server. Commissary auto-scans after downloads, but this is useful after bulk imports or external changes.',
        tips: [
            'Plex: triggers partial scan of music library section',
            'Jellyfin: triggers full library refresh task',
            'Navidrome: auto-detects changes, manual scan rarely needed'
        ]
    },
    '#backup-manager-card': {
        title: 'Backup Manager',
        description: 'Create and manage database backups. The backup includes all library metadata, settings, enrichment data, automation configs, and profiles — everything except audio files.',
        tips: [
            'Backup before major updates or settings changes',
            'Download backups for off-site copies',
            'Backups are stored in the database folder'
        ]
    },
    '#metadata-cache-card': {
        title: 'Metadata Cache Browser',
        description: 'Browse all cached API responses from metadata searches. Every artist, album, and track looked up across all services is stored here, speeding up future lookups and reducing API calls.',
        tips: [
            'Filter by source (Spotify, iTunes, Deezer) and entity type',
            'Cache grows automatically as you search and enrichment runs',
            'Feeds the Genre Explorer and other Discover page features'
        ]
    },

    // ─── WATCHLIST MODAL ──────────────────────────────────────────────

    '#watchlist-modal .playlist-modal-header': {
        title: 'Watchlist Header',
        description: 'Shows total watched artists and countdown to the next automatic scan. Auto-scans run on the interval configured in Automations.',
        tips: [
            'Artist count updates when you add/remove artists',
            'Auto timer resets after each completed scan'
        ],
        docsId: 'art-watchlist'
    },
    '#scan-watchlist-btn': {
        title: 'Scan for New Releases',
        description: 'Starts scanning all watchlisted artists for new albums, EPs, and singles. New releases are added to your Wishlist for download. Also updates the Discovery Pool with similar artist data.',
        tips: [
            'Scan checks each artist against your metadata source',
            'Live activity shows current artist and recently found tracks',
            'New releases trigger the "New Watchlist Release" automation event'
        ],
        docsId: 'art-watchlist'
    },
    '#cancel-watchlist-scan-btn': {
        title: 'Cancel Scan',
        description: 'Stops the current watchlist scan. Any releases found so far are kept — only remaining artists are skipped.',
    },
    '#update-similar-artists-btn': {
        title: 'Update Similar Artists',
        description: 'Refreshes the similar artist database for all watched artists. This data powers the Discovery Pool, genre explorer, and personalized playlists on the Discover page.',
        tips: [
            'Queries metadata sources for artists related to your watchlist',
            'Results appear in the Discovery Pool and feed Discover page features',
            'Runs automatically during watchlist scans, but this forces a refresh'
        ],
        docsId: 'discover'
    },
    '#watchlist-global-settings-btn': {
        title: 'Global Watchlist Settings',
        description: 'Override download preferences for ALL watchlisted artists at once. When enabled, these settings replace individual artist configurations. Useful for applying the same release type and content filters across your entire watchlist.',
        tips: [
            'Button shows "Global Override ON" when active',
            'Overrides individual artist settings while enabled',
            'Disable to return to per-artist configurations'
        ]
    },
    '.watchlist-artist-card': {
        title: 'Watched Artist',
        description: 'An artist on your watchlist. Commissary monitors this artist for new releases and adds them to your Wishlist. Click the gear icon to configure which release types to monitor.',
        tips: [
            'Gear icon opens per-artist download preferences',
            'Configure which release types (Albums, EPs, Singles) to monitor',
            'Content filters control whether live, remix, acoustic versions are included'
        ]
    },

    // ─── WATCHLIST ARTIST CONFIG MODAL ──────────────────────────────

    '#watchlist-artist-config-modal .config-section:first-child': {
        title: 'Download Preferences',
        description: 'Choose which types of releases to watch for this artist. Checked types will be monitored during scans and added to your Wishlist when found.',
        tips: [
            'Albums: Full-length studio albums',
            'EPs: Extended plays (4-6 tracks)',
            'Singles: Individual tracks and 2-3 track releases'
        ]
    },
    '#watchlist-artist-config-modal .config-section:nth-child(2)': {
        title: 'Content Filters',
        description: 'Control which types of content to include or exclude when scanning for new releases. By default, live, remix, acoustic, compilation, and instrumental versions are all excluded — check the ones you want.',
        tips: [
            'Unchecked = excluded from scans (won\'t be added to wishlist)',
            'These filters apply during watchlist scans only',
            'Global Settings can override these per-artist filters'
        ]
    },
    '#config-include-live': {
        title: 'Include Live Versions',
        description: 'When checked, live performances, concert recordings, and live album versions will be included in watchlist scans. Default: excluded.',
    },
    '#config-include-remixes': {
        title: 'Include Remixes',
        description: 'When checked, remix versions, edits, and reworked tracks will be included. Default: excluded.',
    },
    '#config-include-compilations': {
        title: 'Include Compilations',
        description: 'When checked, greatest hits, best-of collections, and compilation albums will be included. Default: excluded.',
    },
    '#config-include-acoustic': {
        title: 'Include Acoustic Versions',
        description: 'When checked, acoustic, stripped-back, and unplugged versions will be included in watchlist scans. Default: excluded.',
    },
    '#config-include-instrumentals': {
        title: 'Include Instrumentals',
        description: 'When checked, instrumental, karaoke, and backing track versions will be included. Default: excluded.',
    },
    '#watchlist-linked-provider-section': {
        title: 'Linked Artist',
        description: 'Shows which metadata provider artist is linked to this watchlist entry. Commissary uses this link to look up releases. If the wrong artist is linked, the scan will find incorrect releases.',
        tips: [
            'The linked artist is matched automatically when you add to watchlist',
            'If releases look wrong, the link may point to the wrong artist',
            'Remove and re-add the artist to force a fresh match'
        ]
    },
    '#save-artist-config-btn': {
        title: 'Save Preferences',
        description: 'Saves this artist\'s download preferences. Changes take effect on the next watchlist scan.',
    },

    // ─── WATCHLIST GLOBAL CONFIG MODAL ──────────────────────────────

    '#watchlist-global-config-modal': {
        title: 'Global Watchlist Settings',
        description: 'When global override is enabled, these settings apply to ALL watched artists, replacing their individual configurations. Useful for uniform preferences across your entire watchlist.',
        tips: [
            'Toggle "Enable Global Override" at the top to activate',
            'Same options as per-artist: release types + content filters',
            'Disable override to return to individual artist settings'
        ]
    },

    // ─── WISHLIST MODAL ───────────────────────────────────────────────

    '#wishlist-overview-modal .playlist-modal-header': {
        title: 'Wishlist Header',
        description: 'Shows total track count across all categories and countdown to the next automatic processing cycle. The wishlist alternates between Albums/EPs and Singles each cycle.',
        tips: [
            '"Next Auto" shows which category processes next and when',
            'Cycles alternate: Albums/EPs → Singles → Albums/EPs → ...',
            'Auto-processing is triggered by the Watchlist automation'
        ],
        docsId: 'art-wishlist'
    },
    '.wishlist-category-card[data-category="albums"]': {
        title: 'Albums & EPs',
        description: 'Tracks from full albums and EPs waiting to be downloaded. Click to view and manage individual tracks. "Next in Queue" means this category will be processed in the next automatic cycle.',
        tips: [
            'Click to see all album/EP tracks in the wishlist',
            'Mosaic background shows cover art from queued items',
            'Select individual tracks or use "Select All" for batch operations'
        ],
        docsId: 'art-wishlist'
    },
    '.wishlist-category-card[data-category="singles"]': {
        title: 'Singles',
        description: 'Individual tracks and single releases waiting to be downloaded. These come from failed single-track downloads, manual additions, or watchlist new release scans.',
        tips: [
            'Click to see all single tracks in the wishlist',
            'Singles are processed in alternating cycles with Albums/EPs',
            'Failed downloads from search automatically land here'
        ],
        docsId: 'art-wishlist'
    },
    '.wishlist-back-btn': {
        title: 'Back to Categories',
        description: 'Return to the category selection view showing Albums/EPs and Singles cards.',
    },
    '#wishlist-select-all-btn': {
        title: 'Select All',
        description: 'Toggle selection on all tracks in the current category. Selected tracks can be batch-removed or batch-downloaded.',
    },
    '#wishlist-batch-bar': {
        title: 'Batch Actions',
        description: 'Appears when tracks are selected. Shows selection count and provides batch operations like removing selected tracks from the wishlist.',
    },
    '.wishlist-batch-remove-btn': {
        title: 'Remove Selected',
        description: 'Removes all selected tracks from the wishlist. They will no longer be queued for download unless re-added.',
    },
    '#wishlist-download-btn': {
        title: 'Download Selection',
        description: 'Start downloading all tracks in the currently visible category. Uses your configured download sources with quality profile and fallback settings.',
        tips: [
            'Downloads use the same pipeline as manual searches',
            'Each track goes through post-processing (tagging, cover art, organization)',
            'Failed downloads return to the wishlist for retry'
        ]
    },
    '.playlist-modal-btn-danger': {
        title: 'Clear Wishlist',
        description: 'Removes ALL tracks from the wishlist across all categories. This action requires confirmation and cannot be undone.',
    },
    '.playlist-modal-btn-warning': {
        title: 'Cleanup Wishlist',
        description: 'Removes tracks that already exist in your library. Useful after manual imports or when tracks were downloaded outside of Commissary.',
    },

    // ─── WISHLIST: TRACK LIST VIEW ─────────────────────────────────

    '.wishlist-category-header': {
        title: 'Category Header',
        description: 'Navigation and selection controls for the current wishlist category. Use the back button to return to the overview, or Select All to batch-manage tracks.',
    },
    '.wishlist-album-card': {
        title: 'Wishlist Album',
        description: 'An album with tracks waiting to be downloaded. Click the header to expand/collapse the track list. Use the checkbox to select all tracks in this album, or the trash icon to remove the entire album from the wishlist.',
        tips: [
            'Expand to see individual tracks and their status',
            'Checkbox selects all tracks in this album for batch operations',
            'Trash icon removes all of this album\'s tracks from the wishlist'
        ]
    },
    '.wishlist-track-item': {
        title: 'Wishlist Track',
        description: 'An individual track queued for download. Select with the checkbox for batch operations, or remove individually with the trash icon.',
    },

    // ─── DOWNLOAD MODAL (used across the entire app) ────────────────

    '.download-missing-modal-hero': {
        title: 'Download Modal',
        description: 'Shows album/playlist info and real-time download statistics. The stats update live as tracks are analyzed and downloaded.',
        tips: [
            'Total: number of tracks in this batch',
            'Found: tracks already in your library (skipped)',
            'Missing: tracks that need to be downloaded',
            'Downloaded: successfully completed downloads'
        ]
    },
    '.stat-total': {
        title: 'Total Tracks',
        description: 'Total number of tracks in this download batch. Includes both tracks already in your library and ones that need downloading.',
    },
    '.stat-found': {
        title: 'Found in Library',
        description: 'Tracks that already exist in your media server library. These are skipped — no need to download them again.',
    },
    '.stat-missing': {
        title: 'Missing Tracks',
        description: 'Tracks not found in your library that will be searched and downloaded from your configured sources.',
    },
    '.stat-downloaded': {
        title: 'Downloaded',
        description: 'Tracks successfully downloaded, processed, and added to your library in this session.',
    },
    '.download-tracks-title': {
        title: 'Track Analysis & Status',
        description: 'Detailed per-track breakdown showing library match status, download progress, and available actions for each track.',
        tips: [
            'Library Match: shows if the track already exists in your library',
            'Download Status: real-time progress for each track',
            'Actions: cancel individual downloads or view download candidates'
        ]
    },
    '.track-select-all': {
        title: 'Select/Deselect All',
        description: 'Toggle selection for all tracks. Deselected tracks will be skipped during download. Useful for downloading only specific tracks from an album.',
    },
    'tr[data-track-index]': {
        title: 'Track Row',
        description: 'A single track in the download batch. Shows track number, name, artist, duration, library match status, download progress, and available actions.',
        tips: [
            'Checkbox on the left: deselect to skip this track during download',
            'Library Match: green "Found" means it\'s already in your library, red "Missing" means it needs downloading',
            'Download Status updates in real-time: Searching → Downloading → Processing → Complete',
            'Actions column: cancel an active download or view alternative download candidates if the first choice fails'
        ]
    },
    '.track-match-status': {
        title: 'Library Match',
        description: 'Shows whether this track was found in your media server library. "Found" means it\'s already there; "Missing" means it needs to be downloaded.',
    },
    '.track-download-status': {
        title: 'Download Status',
        description: 'Real-time status for this track: Pending → Searching → Downloading → Processing → Complete or Failed.',
    },
    '.force-download-toggle': {
        title: 'Download Options',
        description: '"Force Download All" skips the library check and downloads every track regardless of whether it already exists. "Organize by Playlist" puts files in a playlist-named folder instead of the normal artist/album structure.',
        tips: [
            'Force Download: useful for re-downloading with different quality settings',
            'Playlist folder: creates Downloads/PlaylistName/Artist - Track.ext structure'
        ]
    },
    '[id^="begin-analysis-btn"]': {
        title: 'Begin Analysis',
        description: 'Starts the download process: first checks your library for existing tracks, then searches your download sources for missing ones, and downloads them with full post-processing.',
        tips: [
            'Analysis runs through every track in order',
            'Found tracks are marked green and skipped',
            'Missing tracks are searched and queued for download',
            'Post-processing includes tagging, cover art, and file organization'
        ]
    },

    '[id^="add-to-wishlist-btn"]': {
        title: 'Add to Wishlist',
        description: 'Adds all missing tracks from this batch to your Wishlist for later download. Useful when you want to queue tracks but not download them right now.',
        tips: [
            'Only missing tracks are added (already-owned tracks are skipped)',
            'Tracks appear in the Wishlist modal under the appropriate category',
            'The Wishlist auto-processes on a schedule via the Automations system'
        ]
    },
    '.download-control-btn.primary': {
        title: 'Download / Analyze',
        description: 'The main action button — starts library analysis and downloads missing tracks. Changes label based on current state (Begin Analysis → Download Missing → Complete).',
    },

    // ─── SYNC PAGE ───────────────────────────────────────────────────

    // Tabs
    '.sync-tab-button[data-tab="spotify"]': {
        title: 'Spotify Playlists',
        description: 'Your Spotify playlists. Select one or more and click "Start Sync" to download missing tracks. Requires Spotify OAuth connection in Settings.',
        tips: ['Click a playlist card to open the detail/download modal', 'Checkbox selects playlists for batch sync', 'Green badge = fully synced, blue = in progress'],
        docsId: 'sync-spotify'
    },
    '.sync-tab-button[data-tab="spotify-public"]': {
        title: 'Spotify Public Links',
        description: 'Load any public Spotify playlist or album by URL — no Spotify account needed. Paste the URL and click Load.',
        tips: ['Works with playlist and album URLs', 'No OAuth credentials required', 'Previously loaded URLs appear in the history bar'],
        docsId: 'sync-spotify-public'
    },
    '.sync-tab-button[data-tab="tidal"]': {
        title: 'Tidal Playlists',
        description: 'Your Tidal playlists. Import and sync playlists from your Tidal account. Requires Tidal authentication in Settings.',
        docsId: 'sync-tidal'
    },
    '.sync-tab-button[data-tab="deezer"]': {
        title: 'Deezer Playlists',
        description: 'Import Deezer playlists by URL. Paste a playlist URL, load it, then discover and sync tracks.',
        docsId: 'sync-deezer'
    },
    '.sync-tab-button[data-tab="youtube"]': {
        title: 'YouTube Playlists',
        description: 'Import YouTube Music playlists by URL. Tracks go through the discovery pipeline to match official metadata before downloading.',
        tips: ['Paste any YouTube Music playlist URL', 'Discovery matches video titles to official tracks', 'Unmatched tracks can be fixed manually'],
        docsId: 'sync-youtube'
    },
    '.sync-tab-button[data-tab="beatport"]': {
        title: 'Beatport Charts',
        description: 'Browse Beatport charts, genres, and curated playlists. Find electronic music by genre, chart type, or editorial picks.',
        tips: ['Browse 12+ electronic genres', 'Top 100 and Hype charts with full track listings', 'Tracks can be matched to Spotify for metadata'],
        docsId: 'sync-beatport'
    },
    '.sync-tab-button[data-tab="import-file"]': {
        title: 'Import from File',
        description: 'Import track lists from CSV, TSV, M3U/M3U8, or plain text files. Drag and drop or browse for a file, map columns, then create a playlist for sync.',
        tips: ['Supports CSV, TSV, M3U/M3U8, and plain text (one track per line)', 'M3U/M3U8 is read automatically (artist, title, duration from #EXTINF)', 'Column mapping for CSV/TSV files', 'Creates a mirrored playlist for persistent state'],
        docsId: 'sync-import-file'
    },
    '.sync-tab-button[data-tab="mirrored"]': {
        title: 'Mirrored Playlists',
        description: 'All imported playlists from every source, saved persistently. Shows discovery status, download progress, and allows re-syncing.',
        tips: ['Every parsed playlist is automatically mirrored here', 'Cards show live state: Discovering, Discovered, Syncing, Complete', 'Re-parsing the same URL updates the existing mirror'],
        docsId: 'sync-mirrored'
    },
    '.sync-tab-button[data-tab="server"]': {
        title: 'Server Playlists',
        description: 'View and manage playlists from your connected media server (Plex, Jellyfin, or Navidrome). Compare server-side playlists with source playlists to find differences.',
        tips: [
            'Two-column layout: source playlist vs server playlist',
            'Disambiguation overlay helps match tracks when names differ',
            'Useful for verifying sync completeness against your media server'
        ]
    },
    '.sync-tab-button[data-tab="listenbrainz"]': {
        title: 'ListenBrainz Playlists',
        description: 'Import playlists from ListenBrainz — community-generated playlists, weekly discoveries, and your own ListenBrainz playlists.',
        tips: ['Paste any ListenBrainz playlist URL', 'Supports weekly exploration and community playlists', 'Tracks are resolved via MusicBrainz recording IDs'],
    },

    // Sync page header & history
    '.sync-history-btn': {
        title: 'Sync History',
        description: 'View a log of all sync operations — playlist syncs, album downloads, and wishlist processing. Shows timestamps, track counts, and completion status.',
        docsId: 'sync-history'
    },
    '.sync-header': {
        title: 'Playlist Sync',
        description: 'Import and sync playlists from multiple sources. Select playlists, match tracks to your library, and download what\'s missing.',
        docsId: 'sync-overview'
    },

    // Spotify tab elements
    '#spotify-refresh-btn': {
        title: 'Refresh Playlists',
        description: 'Reload your Spotify playlists from the API. Use when you\'ve created or modified playlists in Spotify and they\'re not showing here.',
    },
    '.playlist-card': {
        title: 'Playlist Card',
        description: 'A playlist from your connected account. Click to open the detail view with track listing and download options. Use the checkbox to select for batch sync.',
        tips: ['Status badge shows sync state (synced, in progress, new)', 'Click the card to open the download modal', 'Select multiple with checkboxes, then click Start Sync'],
    },

    // URL input sections
    '#youtube-url-input': {
        title: 'YouTube URL Input',
        description: 'Paste a YouTube Music playlist URL here. Click "Parse Playlist" or press Enter to import the tracks.',
        docsId: 'sync-youtube'
    },
    '#deezer-url-input': {
        title: 'Deezer URL Input',
        description: 'Paste a Deezer playlist URL here. Click "Load Playlist" or press Enter to import the tracks.',
        docsId: 'sync-deezer'
    },
    '#spotify-public-url-input': {
        title: 'Spotify Public URL',
        description: 'Paste any public Spotify playlist or album URL. No Spotify account needed — works with share links.',
        docsId: 'sync-spotify-public'
    },

    // Playlist card action buttons
    '.playlist-card-action-btn': {
        title: 'Playlist Action',
        description: 'The action depends on the playlist state: "Discover" matches tracks to metadata, "Sync" downloads missing tracks, "Download" processes the playlist.',
    },
    '.youtube-playlist-card': {
        title: 'Imported Playlist',
        description: 'An imported playlist card. Shows track count, discovery status, and sync progress. Click the action button to advance to the next step.',
        tips: ['Progress shows: total tracks / matched / failed / percentage', 'Phase colors: gray=fresh, blue=discovering, green=discovered, orange=syncing'],
    },

    // Sidebar
    '.sync-sidebar': {
        title: 'Sync Actions',
        description: 'Select playlists from the left panel, then use these controls to start syncing. Progress and logs appear below.',
        docsId: 'sync-overview'
    },
    '#start-sync-btn': {
        title: 'Start Sync',
        description: 'Begin downloading missing tracks from all selected playlists. Playlists are processed sequentially — each one completes before the next starts.',
        tips: ['Select playlists first using checkboxes on the cards', 'Progress bar and log update in real-time', 'Button is disabled until at least one playlist is selected'],
    },
    '#sync-log-area': {
        title: 'Sync Log',
        description: 'Live log of sync operations. Shows each track as it\'s matched, downloaded, or skipped. Auto-scrolls to show the latest activity.',
    },

    // Import file elements
    '#import-file-dropzone': {
        title: 'File Drop Zone',
        description: 'Drag and drop a CSV, TSV, or text file here, or click to browse. The file will be parsed and previewed before importing.',
        docsId: 'sync-import-file'
    },
    '#import-file-import-btn': {
        title: 'Import as Playlist',
        description: 'Creates a mirrored playlist from the parsed file. Give it a name and click Import — the playlist will appear in the Mirrored tab for discovery and sync.',
    },

    // Beatport elements
    '.beatport-chart-item': {
        title: 'Beatport Chart',
        description: 'A Beatport chart or playlist. Click to view tracks and download. Charts are cached and refreshed daily.',
        docsId: 'sync-beatport'
    },
    '.beatport-genre-item': {
        title: 'Beatport Genre',
        description: 'Click to explore this genre\'s charts, top tracks, staff picks, and new releases.',
        docsId: 'sync-beatport'
    },
    '#beatport-top100-btn': {
        title: 'Beatport Top 100',
        description: 'Load the Beatport Top 100 overall chart — the most popular tracks across all genres.',
    },

    // Mirrored tab
    '.pool-trigger-btn': {
        title: 'Discovery Pool',
        description: 'Open the Discovery Pool to view matched and failed track discoveries across all mirrored playlists. Fix failed matches manually.',
        docsId: 'sync-discovery'
    },
    '#mirrored-refresh-btn': {
        title: 'Refresh Mirrored',
        description: 'Reload all mirrored playlists from the database.',
    },

    // ─── DISCOVERY MODAL (used by YouTube, Tidal, Deezer, Beatport, ListenBrainz, Mirrored) ───

    '.youtube-discovery-modal .modal-header': {
        title: 'Discovery Modal Header',
        description: 'Shows the playlist name, track count, and current phase description. The discovery pipeline matches raw track titles from the source to official metadata on your configured metadata service.',
        docsId: 'sync-discovery'
    },
    '.progress-section': {
        title: 'Discovery Progress',
        description: 'Real-time progress of the track matching process. Each track from the source playlist is compared against your metadata service (Spotify, iTunes, or Deezer) using fuzzy matching with a 0.7 confidence threshold.',
        tips: [
            'Green progress = tracks successfully matched',
            'Progress text shows matched/total count',
            'Matching runs server-side — you can close the modal and it continues'
        ],
        docsId: 'sync-discovery'
    },
    '.discovery-table-container': {
        title: 'Discovery Results Table',
        description: 'Shows each source track alongside its matched metadata result. Green rows = matched, red = failed, gray = pending. Failed matches can be fixed manually.',
        tips: [
            'Source columns show the original track/artist from the playlist',
            'Matched columns show the official metadata found',
            'Status shows confidence score for each match',
            'Actions column: "Fix Match" lets you manually search for the correct track'
        ]
    },
    '.discovery-fix-modal-overlay': {
        title: 'Fix Track Match',
        description: 'Manually search for the correct track when automatic matching fails. Edit the track name and artist, search, then select the right result.',
        tips: [
            'Edit the search terms to improve results',
            'Results come from your active metadata source',
            'Selecting a match updates the discovery cache for future use'
        ]
    },
    '[id^="youtube-discovery-modal"] .modal-footer': {
        title: 'Discovery Actions',
        description: 'Action buttons change based on the current phase. "Start Discovery" begins matching, "Sync to Wishlist" queues matched tracks for download, "Download Missing" starts downloading immediately.',
        tips: [
            'Discovery: matches source tracks to official metadata',
            'Sync: adds matched tracks to your wishlist',
            'Download: searches your download sources and downloads missing tracks',
            'You can close the modal — operations continue in the background'
        ]
    },

    // ─── SEARCH / DOWNLOADS PAGE ────────────────────────────────────

    // Header & Mode Toggle
    '.downloads-header': {
        title: 'Music Downloads',
        description: 'Search for music across your configured metadata sources and download from Soulseek, YouTube, Tidal, Qobuz, HiFi, or Deezer.',
        docsId: 'search'
    },
    '#enh-source-row': {
        title: 'Search Source Icons',
        description: 'Each icon is a metadata source. The highlighted one is what your next search will target — defaults to your configured primary source on page load. Click a different icon to search or switch to that source; a small dot on the icon marks sources that already have cached results for the current query.',
        tips: [
            'Typing searches only the highlighted source — no more silent fan-out across every provider',
            'Switching to an already-cached source is instant, no re-fetch',
            'The Soulseek icon routes to the raw-file search (same as the old Basic Search)',
            'Music Videos queries YouTube for downloadable music video files',
            'An amber border on a source means the backend fell back to a different provider for you (usually because Spotify is rate-limited)'
        ],
        docsId: 'search-enhanced'
    },

    // Enhanced Search
    '.enhanced-search-input-wrapper': {
        title: 'Search Bar',
        description: 'Type an artist, album, or track name. Results appear in categorized sections: Library Artists, Artists, Albums, Singles & EPs, and Tracks. Only the source highlighted in the icon row above is queried — click another icon to switch.',
        tips: [
            'Click an album to open the download modal',
            'Click a track to search your download source',
            'Play button previews tracks from your download source',
            'Switch sources via the icon row above — results are cached per query'
        ],
        docsId: 'search-enhanced'
    },
    '#enh-db-artists-section': {
        title: 'Library Artists',
        description: 'Artists from your local music library that match the search. Click to view their collection on the Library page.',
    },
    '#enh-spotify-artists-section': {
        title: 'Artists',
        description: 'Artists from your metadata source matching the search. Click one to open their discography.',
    },
    '#enh-albums-section': {
        title: 'Albums',
        description: 'Full-length albums matching the search. Click to open the download modal where you can select tracks and start downloading. "In Library" badge means you already own it.',
        docsId: 'search-downloading'
    },
    '#enh-singles-section': {
        title: 'Singles & EPs',
        description: 'Singles and EPs matching the search. Same as albums — click to open the download modal.',
        docsId: 'search-downloading'
    },
    '#enh-tracks-section': {
        title: 'Tracks',
        description: 'Individual tracks matching the search. Click to search your download source for that specific track. Play button streams a preview. "In Library" badge means it\'s already in your collection.',
        docsId: 'search-downloading'
    },

    // Basic Search
    '#basic-search-section .search-bar-container': {
        title: 'Basic Search',
        description: 'Direct search query sent to Soulseek. Enter artist name, song title, or any keywords. Results show raw P2P file listings.',
        docsId: 'search-basic'
    },
    '#filter-toggle-btn': {
        title: 'Filters',
        description: 'Toggle the filter panel to narrow results by type (Albums/Singles), format (FLAC/MP3/OGG/AAC/WMA), and sort order.',
        docsId: 'search-basic'
    },
    '#filter-content': {
        title: 'Search Filters',
        description: 'Filter and sort Soulseek results. Type filters hide non-matching results. Format filters show only specific audio formats. Sort reorders by relevance, quality, bitrate, size, speed, or name.',
        tips: [
            'Type: All, Albums (grouped results), or Singles (individual files)',
            'Format: FLAC for lossless, MP3 for compressed, or specific formats',
            'Sort: Relevance uses the matching engine score; Quality uses bitrate density'
        ],
        docsId: 'search-basic'
    },
    '.search-status-container': {
        title: 'Search Status',
        description: 'Shows the current search state — ready, searching, or results count. The spinner animates while Soulseek is being queried.',
    },
    '#search-results-area': {
        title: 'Search Results',
        description: 'Raw Soulseek results grouped by album or listed individually. Each result shows filename, format, bitrate, quality score, file size, uploader name, upload speed, and availability.',
        tips: [
            'Click a result to start downloading',
            'Album results group files from the same folder',
            'Quality score combines format, bitrate, peer speed, and availability',
            'Green = high quality, Yellow = medium, Red = low'
        ],
        docsId: 'search-basic'
    },

    // (Download Manager side-panel was retired — see the dedicated Downloads page)

    // ─── DISCOVER PAGE ────────────────────────────────────────────────

    // Hero
    '.discover-hero': {
        title: 'Featured Artists',
        description: 'Rotating showcase of recommended artists from your watchlist and discovery pool. Navigate with arrows or dot indicators.',
        tips: [
            '"View Discography" opens the artist on the Artists page',
            '"Add to Watchlist" monitors them for new releases',
            '"Watch All" adds all featured artists to your watchlist at once',
            '"View Recommended" opens a full list of recommended artists'
        ],
        docsId: 'disc-hero'
    },
    '#discover-hero-discography': {
        title: 'View Discography',
        description: 'Navigate to the Artists page and load this artist\'s full album, single, and EP discography for browsing and downloading.',
    },
    '#discover-hero-add': {
        title: 'Add to Watchlist',
        description: 'Add this artist to your Watchlist. Commissary will scan for their new releases and add them to your Wishlist for download.',
    },
    '#discover-hero-watch-all': {
        title: 'Watch All',
        description: 'Add ALL featured artists from the hero slider to your Watchlist in one click.',
    },
    '#discover-hero-view-all': {
        title: 'View Recommended',
        description: 'Open a modal showing all recommended artists — not just the ones in the hero slider. Browse, add to watchlist, or view discographies.',
    },

    // Recent Releases
    '#recent-releases-carousel': {
        title: 'Recent Releases',
        description: 'New albums and singles from artists you follow. These are found during watchlist scans. Click any release to open the download modal.',
        docsId: 'disc-hero'
    },

    // Seasonal
    '#seasonal-albums-section': {
        title: 'Seasonal Albums',
        description: 'Albums curated for the current season based on mood, genre, and release timing. Refreshes with each season change.',
        docsId: 'disc-seasonal'
    },
    '#seasonal-playlist-section': {
        title: 'Seasonal Mix',
        description: 'A curated playlist of tracks matching the current season\'s vibe. Download missing tracks or sync to your media server.',
        docsId: 'disc-seasonal'
    },

    // Personalized Playlists
    '#personalized-popular-picks': {
        title: 'Popular Picks',
        description: 'Trending tracks from your discovery pool artists. These are the most popular songs from artists similar to the ones you follow.',
        tips: ['Download or Sync buttons queue tracks for your library', 'Tracks come from the discovery pool (built during watchlist scans)'],
        docsId: 'disc-playlists'
    },
    '#personalized-hidden-gems': {
        title: 'Hidden Gems',
        description: 'Rare and deeper cuts from your discovery pool artists. Lower popularity tracks that you might not find on mainstream playlists.',
        docsId: 'disc-playlists'
    },
    '#personalized-discovery-shuffle': {
        title: 'Discovery Shuffle',
        description: 'Random tracks from your entire discovery pool — different every time you load. A surprise mix for when you want something new.',
        docsId: 'disc-playlists'
    },

    // Curated Playlists
    '#release-radar-playlist': {
        title: 'Fresh Tape',
        description: 'New releases from recent additions to your library and discovery pool. Refreshes regularly with the latest drops.',
        docsId: 'disc-playlists'
    },
    '#discovery-weekly-playlist': {
        title: 'The Archives',
        description: 'Curated selection from your full collection — a weekly-style playlist that highlights tracks across your library.',
        docsId: 'disc-playlists'
    },

    // Build a Playlist — section container and all inner elements
    '.build-playlist-container': {
        title: 'Build a Playlist',
        description: 'Create a custom playlist by selecting seed artists. Commissary finds similar artists, pulls their albums, and assembles a 50-track playlist mixing your picks with new discoveries.',
        tips: [
            'Search and select 1-5 seed artists',
            'Hit Generate for a fresh playlist every time',
            'The more seed artists, the more variety in the playlist'
        ],
        docsId: 'disc-build'
    },
    '#bp-info-panel': {
        title: 'How Build a Playlist Works',
        description: 'Search for seed artists → Commissary finds similar artists → pulls their albums → picks random tracks → creates a 50-track playlist. More seed artists = more variety.',
        docsId: 'disc-build'
    },
    '#build-playlist-search': {
        title: 'Artist Search',
        description: 'Search for artists to include in your custom playlist. Select multiple artists and generate a playlist of their top tracks.',
        tips: [
            'Search and click artists to add them to your selection',
            'Selected artists appear below the search with remove buttons',
            'Click "Generate Playlist" when you\'ve chosen your artists'
        ],
        docsId: 'disc-build'
    },
    '#build-playlist-generate-btn': {
        title: 'Generate Playlist',
        description: 'Creates a playlist from top tracks of all your selected artists. The playlist can then be downloaded or synced to your media server.',
    },
    '#build-playlist-results-wrapper': {
        title: 'Generated Playlist',
        description: 'Your custom-built playlist. Download missing tracks or sync to your media server. Tracks are sorted by popularity across the selected artists.',
    },

    // Cache-based Discovery Sections
    '#cache-genre-explorer': {
        title: 'Genre Explorer',
        description: 'Browse music by genre across all your metadata sources. Click any genre pill to open a deep dive with artists, albums, tracks, and related genres.',
        tips: [
            'Genres are weighted: library and discovery pool count more than cache',
            '"New" badge means this genre isn\'t in your library yet',
            'Data comes from Spotify, iTunes, and Deezer caches combined'
        ],
        docsId: 'discover'
    },
    '#cache-undiscovered': {
        title: 'Undiscovered Albums',
        description: 'Albums from cached artists that you don\'t have in your library. A great way to find new music from artists you\'ve already searched for.',
    },
    '#cache-genre-releases': {
        title: 'Genre New Releases',
        description: 'Recently released albums matching your top library genres. Found in the metadata cache from recent searches.',
    },
    '#cache-label-explorer': {
        title: 'Label Explorer',
        description: 'Albums grouped by record label. Discover new music from labels whose artists you already enjoy.',
    },
    '#cache-deep-cuts': {
        title: 'Deep Cuts',
        description: 'Low-popularity tracks from artists in your metadata cache. These are the album tracks that never became singles — often the most interesting finds.',
    },

    // ListenBrainz — match both the tabs container and the parent section
    '#listenbrainz-tabs': {
        title: 'ListenBrainz Playlists',
        description: 'Playlists from your ListenBrainz account. Three categories: "Created For You" (algorithmic), "Your Playlists" (manually created), and "Collaborative" (shared).',
        tips: [
            'Requires ListenBrainz connection in Settings',
            'Click any playlist to view tracks and download',
            'Refresh button reloads from ListenBrainz API'
        ],
        docsId: 'sync-listenbrainz'
    },
    '#listenbrainz-tab-content': {
        title: 'ListenBrainz Playlist Content',
        description: 'Track listings for the selected ListenBrainz playlist. Click a track to download or stream it.',
        docsId: 'sync-listenbrainz'
    },
    '#listenbrainz-refresh-btn': {
        title: 'Refresh ListenBrainz',
        description: 'Reload playlists from your ListenBrainz account. Fetches the latest "Created For You", personal, and collaborative playlists.',
    },
    '.listenbrainz-tab': {
        title: 'ListenBrainz Tab',
        description: 'Switch between playlist categories: "Created For You" (algorithm-generated), "Your Playlists" (manually created), and "Collaborative" (shared with others).',
    },

    // Time Machine — match tabs, tab contents, and individual tabs
    '#decade-tabs': {
        title: 'Time Machine',
        description: 'Browse music by decade — from the 1950s to the 2020s. Each tab shows top tracks from your discovery pool artists active in that era.',
        tips: [
            'Download or Sync buttons queue decade tracks for your library',
            'Tracks come from discovery pool artists with releases in that decade'
        ],
        docsId: 'disc-timemachine'
    },
    '#decade-tab-contents': {
        title: 'Decade Tracks',
        description: 'Tracks from the selected decade. Download missing tracks or sync them to your media server.',
        docsId: 'disc-timemachine'
    },
    '.decade-tab': {
        title: 'Decade Tab',
        description: 'Click to browse music from this decade. Shows top tracks from your discovery pool artists who released music in this era.',
        docsId: 'disc-timemachine'
    },

    // Browse by Genre (discovery pool tabs)
    '#genre-tabs': {
        title: 'Browse by Genre',
        description: 'Genre-filtered playlists from your discovery pool. Each tab shows tracks matching that genre from artists in your discovery pool.',
        tips: [
            'Genres are consolidated from Spotify/iTunes categories',
            'Download or Sync buttons queue genre tracks for download',
            'Requires discovery pool data (run a watchlist scan first)'
        ],
        docsId: 'discover'
    },
    '#genre-tab-contents': {
        title: 'Genre Tracks',
        description: 'Tracks from the selected genre. Download or sync to add them to your library.',
    },
    '.genre-tab': {
        title: 'Genre Tab',
        description: 'Click to browse tracks in this genre from your discovery pool.',
    },

    // Playlist Sync/Download buttons (generic — matches all discover playlist sections)
    '.discover-section-actions .action-button.primary': {
        title: 'Sync to Media Server',
        description: 'Start syncing this playlist — matches tracks to your library, searches download sources for missing ones, and downloads them. Progress shows matched, pending, and failed counts.',
    },
    '.discover-section-actions .action-button.secondary': {
        title: 'Download Missing',
        description: 'Opens the download modal for this playlist. Review tracks, select which ones to download, and start the download process.',
    },

    // Daily Mixes
    '#daily-mixes-grid': {
        title: 'Daily Mixes',
        description: 'Personalized mixes generated from your listening patterns. Each mix focuses on a different aspect of your taste — genre clusters, mood, or artist groups.',
    },

    // ─── ARTIST DETAIL PAGE ───────────────────────────────────────────
    // (The standalone /artist-detail page is the unified destination for
    // both library and metadata-source artists. The inline /artists page
    // was retired in the unification project.)

    '.album-card': {
        title: 'Release Card',
        description: 'An album, single, or EP from this artist. Click to open the download modal with track selection, library matching, and download controls.',
        tips: [
            'Big-photo cover art fills the card with title and year overlaid at the bottom',
            'Completion badge (top-right) shows ownership status: ✓ Owned / N/M / Missing',
            'Library artists check ownership in the background — badge starts as "Checking…" then resolves'
        ]
    },
    '.completion-overlay': {
        title: 'Completion Badge',
        description: 'Top-right badge showing ownership state for library artists. ✓ Owned = full match, N/M = partial (owned/total tracks), Missing = no match. Source artists don\'t show this badge.',
    },
    '#ad-similar-artists-section': {
        title: 'Similar Artists',
        description: 'Artists with a similar sound, fetched from MusicMap by name. Works for both library and source artists. Click any bubble to navigate to that artist\'s detail page.',
        tips: [
            'Bubbles load progressively',
            'Click navigates to the standalone artist-detail page'
        ],
        docsId: 'art-detail'
    },
    '.similar-artist-bubble': {
        title: 'Similar Artist',
        description: 'An artist similar to the one you\'re viewing. Click to load their discography and browse their releases.',
    },
    // (Search source picker annotation lives under `#enh-source-row` above —
    //  the old `.search-source-picker-container` dropdown is gone.)

    // ─── AUTOMATIONS PAGE ─────────────────────────────────────────────

    // List View
    '#automations-list-view': {
        title: 'Automations List',
        description: 'All your automations — system and custom. Each card shows the trigger → action → then flow, run status, and controls.',
        docsId: 'auto-overview'
    },
    '.auto-new-btn': {
        title: 'New Automation',
        description: 'Open the visual builder to create a new automation. Choose a trigger (WHEN), an action (DO), and optional notifications (THEN).',
        docsId: 'auto-builder'
    },
    '#auto-filter-search': {
        title: 'Search Automations',
        description: 'Filter the list by name, trigger type, or action type. Matches are highlighted as you type.',
    },
    '#auto-filter-trigger': {
        title: 'Filter by Trigger',
        description: 'Show only automations with a specific trigger type (Schedule, Daily, Weekly, Event-based, Signal).',
    },
    '#auto-filter-action': {
        title: 'Filter by Action',
        description: 'Show only automations with a specific action type (Library Scan, Watchlist Scan, Process Wishlist, etc.).',
    },
    '#automations-stats': {
        title: 'Automation Stats',
        description: 'Quick overview: total active automations, system automations (built-in), and custom automations you\'ve created.',
    },

    // Automation Cards
    '.automation-card': {
        title: 'Automation',
        description: 'A single automation showing its trigger → action → notification flow. Use the controls on the right to run, edit, enable/disable, duplicate, or delete.',
        tips: [
            'Green dot = enabled and running on schedule',
            'Gray dot = disabled',
            'Blue dot = currently executing',
            'Click the run count to view execution history'
        ],
        docsId: 'auto-overview'
    },
    '.automation-flow': {
        title: 'Automation Flow',
        description: 'Visual representation of this automation: WHEN (trigger) → DO (action) → THEN (notification/signal). Each step shows its type and configuration.',
    },
    '.automation-run-btn': {
        title: 'Run Now',
        description: 'Execute this automation immediately, regardless of its schedule. The automation runs as if its trigger just fired.',
    },
    '.automation-toggle': {
        title: 'Enable/Disable',
        description: 'Toggle this automation on or off. Disabled automations keep their configuration but won\'t trigger.',
    },
    '.automation-edit-btn': {
        title: 'Edit',
        description: 'Open this automation in the visual builder to modify its trigger, action, or notification settings.',
    },
    '.automation-dupe-btn': {
        title: 'Duplicate',
        description: 'Create a copy of this automation with all the same settings. Useful for creating variations of existing workflows.',
    },
    '.automation-delete-btn': {
        title: 'Delete',
        description: 'Permanently delete this automation. Requires confirmation. Cannot be undone.',
    },
    '.auto-runs-link': {
        title: 'Run History',
        description: 'Click to view the execution history for this automation — timestamps, duration, status, and detailed logs for each run.',
        docsId: 'auto-history'
    },
    '.auto-group-btn': {
        title: 'Group',
        description: 'Assign this automation to a group for organization. Groups appear as collapsible sections in the list. Create new groups or assign to existing ones.',
    },

    // Automation Hub
    '#auto-section-hub': {
        title: 'Automation Hub',
        description: 'Guides, recipes, and reference material for building automations. Pipelines are pre-built workflow templates, recipes are common patterns, and guides explain concepts.',
        docsId: 'auto-overview'
    },
    '.auto-hub-tab[data-tab="pipelines"]': {
        title: 'Pipelines',
        description: 'Pre-built multi-step workflow templates. Each pipeline deploys several linked automations that work together — like a complete "new release → download → notify" chain.',
    },
    '.auto-hub-tab[data-tab="recipes"]': {
        title: 'Recipes',
        description: 'Single-automation patterns for common tasks. Quick one-click creation of popular automations.',
    },
    '.auto-hub-tab[data-tab="guides"]': {
        title: 'Guides',
        description: 'Step-by-step walkthroughs explaining how to build specific workflows and use advanced features like signals and conditions.',
    },
    '.auto-hub-tab[data-tab="tips"]': {
        title: 'Tips & Tricks',
        description: 'Best practices, performance tips, and common pitfalls when building automations.',
    },
    '.auto-hub-tab[data-tab="reference"]': {
        title: 'Reference',
        description: 'Complete list of all available triggers, actions, and then-actions with their configuration options.',
        docsId: 'auto-triggers'
    },

    // Builder View
    '#automations-builder-view': {
        title: 'Automation Builder',
        description: 'Visual editor for creating and editing automations. Drag blocks from the sidebar into the WHEN → DO → THEN flow slots.',
        docsId: 'auto-builder'
    },
    '#builder-name': {
        title: 'Automation Name',
        description: 'Give your automation a descriptive name. This appears in the list view and notifications.',
    },
    '#builder-group-name': {
        title: 'Group',
        description: 'Optionally assign this automation to a group. Groups organize automations into collapsible sections.',
    },
    '#builder-sidebar': {
        title: 'Block Library',
        description: 'Available triggers, actions, and then-actions. Drag a block to the canvas, or click to place it in the next empty slot.',
        tips: [
            'Triggers (WHEN): Schedule, Daily Time, Weekly Time, Events, Signals',
            'Actions (DO): Library Scan, Watchlist Scan, Process Wishlist, and more',
            'Then (THEN): Discord, Pushbullet, Telegram, Gotify, Fire Signal'
        ],
        docsId: 'auto-triggers'
    },
    '#slot-when': {
        title: 'WHEN — Trigger',
        description: 'Drop a trigger here to define WHEN this automation fires. Options: on a schedule, at a specific time, when an event occurs, or when a signal is received.',
        docsId: 'auto-triggers'
    },
    '#slot-do': {
        title: 'DO — Action',
        description: 'Drop an action here to define WHAT happens when the trigger fires. Options: scan library, check watchlist, process wishlist, refresh playlists, and more.',
        docsId: 'auto-actions'
    },
    '[id^="slot-then"]': {
        title: 'THEN — Notification/Signal',
        description: 'Drop a then-action here to define what happens AFTER the action completes. Send notifications via Discord, Pushbullet, Telegram, or fire a signal to chain automations.',
        tips: [
            'Up to 3 THEN actions per automation',
            'Signals let you chain automations together',
            'Message templates support variables: {time}, {name}, {status}'
        ],
        docsId: 'auto-then'
    },
    '.block-item': {
        title: 'Automation Block',
        description: 'A trigger, action, or notification type. Drag to a flow slot, or click to auto-place. The ? button shows detailed help for each block type.',
    },
    '.placed-block': {
        title: 'Placed Block',
        description: 'A configured block in the flow. Click the X to remove it. Configure options using the fields below the block.',
    },
    '.btn-save': {
        title: 'Save Automation',
        description: 'Save this automation. It will appear in the list view and start running according to its trigger configuration.',
    },

    // History Modal
    '.automation-history-modal': {
        title: 'Execution History',
        description: 'Detailed log of every time this automation ran. Shows timestamp, duration, status (success/error), and expandable logs with step-by-step details.',
        docsId: 'auto-history'
    },

    // ─── LIBRARY PAGE ─────────────────────────────────────────────────

    // Library Grid View
    '#library-page .library-controls': {
        title: 'Library Controls',
        description: 'Search, filter, and navigate your music library. Find artists by name, filter by watchlist status, or jump to a letter.',
        docsId: 'lib-standard'
    },
    '#library-search-input': {
        title: 'Search Library',
        description: 'Search your library by artist name. Results filter in real-time as you type.',
    },
    '#watchlist-filter': {
        title: 'Watchlist Filter',
        description: 'Filter artists by watchlist status: All shows everyone, Watched shows only artists you follow, Unwatched shows artists not on your watchlist.',
    },
    '#alphabet-selector': {
        title: 'Alphabet Jump',
        description: 'Jump to artists starting with a specific letter. Click "All" to reset. "#" shows artists starting with numbers.',
    },
    '#library-artists-grid': {
        title: 'Artist Grid',
        description: 'Your music library organized by artist. Each card shows the artist photo, name, track count, and service badges. Click any card to view their collection.',
        docsId: 'lib-standard'
    },
    '.library-artist-card': {
        title: 'Library Artist',
        description: 'An artist in your library. Click to view their full collection with albums, EPs, and singles. Service badges show which metadata sources have enriched this artist.',
        tips: [
            'Badge icons link to the artist on external services',
            'Eye icon toggles watchlist status',
            'Track count shows total tracks in your library for this artist'
        ]
    },
    '#library-pagination': {
        title: 'Pagination',
        description: 'Navigate through pages of artists. Your library shows 75 artists per page.',
    },

    // Artist Detail — Hero Section
    '#artist-hero-section': {
        title: 'Artist Profile',
        description: 'Full artist profile with image, name, service badges, genres, bio, listening stats, and collection overview. Data is enriched from up to 9 metadata services.',
        docsId: 'lib-standard'
    },
    '#artist-detail-name': {
        title: 'Artist Name',
        description: 'The artist\'s name as it appears in your library.',
    },
    '#artist-hero-badges': {
        title: 'Service Badges',
        description: 'Links to this artist on external platforms. Each badge indicates which services have matched and enriched this artist with metadata.',
        tips: [
            'Click any badge to open the artist on that platform',
            'More badges = more complete metadata enrichment',
            'Run the Metadata Updater on the dashboard to enrich more artists'
        ],
        docsId: 'lib-matching'
    },
    '#artist-genres': {
        title: 'Genres',
        description: 'Genre tags from Spotify, Last.fm, and other metadata sources. Merged and deduplicated across all enrichment sources.',
    },
    '#artist-hero-bio': {
        title: 'Artist Biography',
        description: 'Biography from Last.fm. Click "Read more" to expand. Populated by the Last.fm enrichment worker.',
    },
    '#artist-hero-listeners': {
        title: 'Listeners',
        description: 'Total unique listeners on Last.fm. Shows global popularity of this artist.',
    },
    '#artist-hero-playcount': {
        title: 'Play Count',
        description: 'Total plays on Last.fm across all listeners worldwide.',
    },
    '.collection-overview': {
        title: 'Collection Overview',
        description: 'Progress bars showing how complete your collection is for this artist — Albums, EPs, and Singles separately. Numbers show owned/total from the metadata source.',
    },
    '#artist-enrichment-coverage': {
        title: 'Enrichment Coverage',
        description: 'Animated rings showing metadata enrichment percentage per service. Each ring represents one metadata source — higher percentage means more tracks have been enriched by that service.',
        docsId: 'lib-matching'
    },

    // Artist Detail — Action Buttons
    '#library-artist-watchlist-btn': {
        title: 'Watchlist',
        description: 'Add or remove this artist from your Watchlist for new release monitoring.',
        docsId: 'art-watchlist'
    },
    '#library-artist-enhance-btn': {
        title: 'Enhance Quality',
        description: 'Scan your collection for this artist and find higher-quality versions of tracks you own. Compares bitrate and format against available sources.',
    },
    '#library-artist-radio-btn': {
        title: 'Artist Radio',
        description: 'Generate and play a radio mix of this artist\'s tracks from your library. Streams directly from your media server.',
    },

    // Discography Filters
    '#discography-filters': {
        title: 'Discography Filters',
        description: 'Filter the artist\'s releases by category, content type, and ownership status. Multiple filters can be combined.',
        tips: [
            'Category: toggle Albums, EPs, Singles on/off',
            'Content: show/hide Live, Compilations, Featured releases',
            'Ownership: All, Owned (in library), or Missing (not in library)'
        ],
        docsId: 'lib-standard'
    },
    '.discography-filter-btn[data-filter="ownership"][data-value="missing"]': {
        title: 'Missing Releases',
        description: 'Show only releases NOT in your library. Great for finding what to download next.',
    },
    '.discography-filter-btn[data-filter="ownership"][data-value="owned"]': {
        title: 'Owned Releases',
        description: 'Show only releases you already have in your library.',
    },

    // View Toggle
    '.enhanced-view-toggle-btn[data-view="standard"]': {
        title: 'Standard View',
        description: 'Card grid view of releases. Click any card to open the download modal.',
        docsId: 'lib-standard'
    },
    '.enhanced-view-toggle-btn[data-view="enhanced"]': {
        title: 'Enhanced View',
        description: 'Advanced management mode with accordion layout, inline editing, tag writing, and bulk operations. Admin-only feature.',
        tips: [
            'Expand albums to see track tables with editable fields',
            'Select tracks across albums for batch operations',
            'Write tags directly to audio files',
            'Reorganize files with the album reorganize tool'
        ],
        docsId: 'lib-enhanced'
    },

    // Discography Sections
    '#albums-section': {
        title: 'Albums',
        description: 'Full-length studio albums. Shows owned and missing counts in the header. Click any release card to download.',
    },
    '#eps-section': {
        title: 'EPs',
        description: 'Extended plays (4-6 tracks). Shows owned and missing counts.',
    },
    '#singles-section': {
        title: 'Singles',
        description: 'Single tracks and 2-3 track releases. Shows owned and missing counts.',
    },
    '.release-card': {
        title: 'Release Card',
        description: 'An album, EP, or single in the discography. Shows cover art, title, year, track count, and ownership status. Click to open the download modal.',
    },

    // Enhanced View
    '#enhanced-view-container': {
        title: 'Enhanced Library Manager',
        description: 'Accordion layout with expandable albums showing track tables. Edit metadata inline, write tags to files, and perform bulk operations across albums.',
        docsId: 'lib-enhanced'
    },
    '.enhanced-track-checkbox': {
        title: 'Track Selection',
        description: 'Select tracks for bulk operations. Hold Ctrl+Click for range selection. Selected tracks appear in the bulk actions bar at the bottom.',
        docsId: 'lib-bulk'
    },

    // Bulk Actions Bar
    '#enhanced-bulk-bar': {
        title: 'Bulk Actions',
        description: 'Appears when tracks are selected. Edit metadata for all selected tracks at once, write tags to files, or clear the selection.',
        tips: [
            'Edit Selected: opens a modal to change metadata fields for all selected tracks',
            'Write Tags: writes database metadata to the actual audio files',
            'Clear Selection: deselects all tracks'
        ],
        docsId: 'lib-bulk'
    },

    // Tag Preview Modal
    '#tag-preview-overlay': {
        title: 'Tag Preview',
        description: 'Compare current file tags against database metadata before writing. Shows a diff table highlighting what will change. Choose whether to embed cover art and sync to your media server.',
        docsId: 'lib-tags'
    },
    '#batch-tag-preview-overlay': {
        title: 'Batch Tag Preview',
        description: 'Preview tag changes for multiple tracks at once. Each track shows its own diff table. Write all tags in one batch operation.',
        docsId: 'lib-tags'
    },

    // Reorganize Modal
    '#reorganize-overlay': {
        title: 'Reorganize Album',
        description: 'Move and rename files in an album to match your file organization template. Preview the changes before applying.',
    },

    // ─── STATS PAGE ──────────────────────────────────────────────────

    '#stats-container': {
        title: 'Listening Stats',
        description: 'Analytics dashboard showing your listening activity, top artists/albums/tracks, genre breakdown, library health, and storage usage. Data syncs from your media server.',
    },
    '#stats-time-range': {
        title: 'Time Range',
        description: 'Filter all stats by time period: 7 Days, 30 Days, 12 Months, or All Time. Charts and rankings update instantly.',
    },
    '#stats-sync-btn': {
        title: 'Sync Now',
        description: 'Manually sync listening data from your media server. Pulls the latest play history, scrobbles, and library changes.',
    },
    '#stats-overview': {
        title: 'Overview Cards',
        description: 'Key metrics at a glance: Total Plays, Listening Time, unique Artists, Albums, and Tracks played in the selected time range.',
    },
    '#stats-timeline-chart': {
        title: 'Listening Activity',
        description: 'Chart showing your listening activity over time. Each bar represents plays in that time period. Helps visualize listening patterns and trends.',
    },
    '#stats-genre-chart': {
        title: 'Genre Breakdown',
        description: 'Pie/donut chart showing the genre distribution of your listening. Based on genre tags from your library\'s metadata enrichment.',
    },
    '#stats-recent-plays': {
        title: 'Recently Played',
        description: 'Your most recent listening history from the media server. Shows track, artist, album, and when it was played.',
    },
    '#stats-top-artists': {
        title: 'Top Artists',
        description: 'Your most-played artists in the selected time range, ranked by play count.',
    },
    '#stats-top-albums': {
        title: 'Top Albums',
        description: 'Your most-played albums in the selected time range, ranked by play count.',
    },
    '#stats-top-tracks': {
        title: 'Top Tracks',
        description: 'Your most-played individual tracks in the selected time range.',
    },
    '#stats-library-health': {
        title: 'Library Health',
        description: 'Overview of your library\'s format distribution, unplayed tracks, total duration, and track count. The format bar shows FLAC vs MP3 vs other formats.',
    },
    '#stats-enrichment-coverage': {
        title: 'Enrichment Coverage',
        description: 'How thoroughly your library has been enriched by each metadata service. Higher percentages mean more complete metadata.',
    },
    '#stats-db-storage-chart': {
        title: 'Database Storage',
        description: 'Breakdown of your Commissary database size by category: library data, metadata cache, discovery pool, settings, and more.',
    },

    // ─── IMPORT PAGE ────────────────────────────────────────────────

    '.import-page-container': {
        title: 'Import Music',
        description: 'Import audio files from your import folder into your library. Match files to album metadata, tag them, and organize into your collection.',
        docsId: 'import'
    },
    '.import-page-refresh-btn': {
        title: 'Refresh',
        description: 'Re-scan your import folder for new audio files. Use after dropping new files in.',
    },
    '#import-staging-bar': {
        title: 'Import Folder',
        description: 'Shows your configured import folder path and the number of audio files found. Set the import path in Settings → Download Settings.',
        docsId: 'imp-setup'
    },
    '#import-page-queue': {
        title: 'Processing Queue',
        description: 'Shows albums and singles currently being processed. Each job goes through matching, tagging, cover art embedding, and file organization.',
    },
    '#import-page-tab-album': {
        title: 'Albums Tab',
        description: 'Import complete albums. Search for an album, match import files to tracks, then process. Suggestions appear automatically from your import folder.',
        docsId: 'imp-workflow'
    },
    '#import-page-tab-singles': {
        title: 'Singles Tab',
        description: 'Import individual audio files as single tracks. Select files, and Commissary identifies them using AcoustID fingerprinting or filename matching.',
        docsId: 'imp-singles'
    },
    '#import-page-suggestions-grid': {
        title: 'Suggestions',
        description: 'Albums automatically detected from your import folder based on folder names and file metadata. Click a suggestion to start the matching process.',
    },
    '#import-page-album-search-input': {
        title: 'Album Search',
        description: 'Search your metadata source for an album to match against import files. Enter the album name or artist + album.',
    },
    '#import-page-album-match-section': {
        title: 'Track Matching',
        description: 'Match your import files to album tracks. Drag files from the unmatched pool onto tracks, or let auto-matching do it. Green = matched, red = unmatched.',
        tips: [
            'Drag and drop files from the unmatched pool to track slots',
            '"Re-match Automatically" re-runs the matching algorithm',
            '"Back to Search" returns to the album search view'
        ],
        docsId: 'imp-matching'
    },
    '#import-page-unmatched-pool': {
        title: 'Unmatched Files',
        description: 'Audio files in your import folder that haven\'t been matched to an album track yet. Drag them onto the correct track slot above.',
        docsId: 'imp-matching'
    },
    '#import-page-album-process-btn': {
        title: 'Process Album',
        description: 'Start processing the matched album. Tags files with metadata, embeds cover art, renames and organizes files into your library, then triggers a media server scan.',
    },
    '#import-page-singles-list': {
        title: 'Singles List',
        description: 'Individual audio files in your import folder. Select files and click "Process Selected" to identify and import them as single tracks.',
        docsId: 'imp-singles'
    },
    '#import-page-singles-process-btn': {
        title: 'Process Singles',
        description: 'Identify and import selected singles. Uses AcoustID fingerprinting to match files to tracks, then tags and organizes them.',
    },

    // ─── SETTINGS PAGE ────────────────────────────────────────────────

    // Tabs
    '.stg-tab[data-tab="connections"]': {
        title: 'Connections',
        description: 'Configure credentials for metadata sources (Spotify, Tidal, Last.fm, etc.) and media server connections (Plex, Jellyfin, Navidrome).',
        docsId: 'set-services'
    },
    '.stg-tab[data-tab="downloads"]': {
        title: 'Downloads',
        description: 'Configure download sources, paths, quality profiles, and hybrid mode priority order.',
        docsId: 'set-download'
    },
    '.stg-tab[data-tab="library"]': {
        title: 'Library',
        description: 'File organization templates, post-processing options, tag embedding, lossy copy, listening stats, and content filtering.',
        docsId: 'set-processing'
    },
    '.stg-tab[data-tab="appearance"]': {
        title: 'Appearance',
        description: 'Customize the accent color, sidebar visualizer style, and UI effects like particles and worker orbs.',
    },
    '.stg-tab[data-tab="advanced"]': {
        title: 'Advanced',
        description: 'Database workers, discovery pool settings, API key management, developer mode, and logging configuration.',
    },

    // Connections — API Services
    '.api-test-buttons': {
        title: 'Test Connections',
        description: 'Test each configured service to verify credentials are working. Green = connected, Red = failed.',
        docsId: 'set-services'
    },

    // Connections — Media Server
    '#plex-container': {
        title: 'Plex Configuration',
        description: 'Connect your Plex server. Enter the URL and token, then select your Music Library. Commissary reads your library from Plex and triggers scans after downloads.',
        tips: [
            'URL format: http://IP:32400 (or your custom port)',
            'Token: find in Plex settings or browser URL bar while logged in',
            'Select the correct Music Library after connecting'
        ],
        docsId: 'set-media'
    },
    '#jellyfin-container': {
        title: 'Jellyfin Configuration',
        description: 'Connect your Jellyfin server. Enter URL, API key, then select a user and music library.',
        docsId: 'set-media'
    },
    '#navidrome-container': {
        title: 'Navidrome Configuration',
        description: 'Connect your Navidrome server. Enter URL, username, password, then select the music folder. Navidrome auto-detects new files.',
        docsId: 'set-media'
    },

    // Downloads — Source & Paths
    '#download-source-mode': {
        title: 'Download Source Mode',
        description: 'Choose your primary download source. Hybrid mode tries multiple sources in priority order with automatic fallback.',
        tips: [
            'Soulseek: P2P network via slskd — best for lossless and rare music',
            'YouTube: audio extraction via yt-dlp',
            'Tidal/Qobuz/HiFi/Deezer: streaming source downloads',
            'Hybrid: tries sources in your configured priority order'
        ],
        docsId: 'set-download'
    },
    '#hybrid-settings-container': {
        title: 'Hybrid Source Priority',
        description: 'Drag and drop to reorder your download source priority. The first source is tried first; if it fails or finds nothing, the next source is tried.',
        docsId: 'set-download'
    },
    '#soulseek-settings-container': {
        title: 'Soulseek Settings',
        description: 'Configure your slskd connection (URL + API key), search timeout, peer speed limits, queue limits, and download timeout.',
        docsId: 'set-download'
    },
    '#tidal-download-settings-container': {
        title: 'Tidal Download Settings',
        description: 'Quality selection for Tidal downloads. Authenticate with your Tidal account. "Allow quality fallback" controls whether lower quality is accepted when preferred isn\'t available.',
        docsId: 'set-download'
    },
    '#qobuz-settings-container': {
        title: 'Qobuz Settings',
        description: 'Quality selection and authentication for Qobuz downloads. Sign in with your Qobuz account credentials.',
        docsId: 'set-download'
    },
    '#hifi-download-settings-container': {
        title: 'HiFi Settings',
        description: 'Quality selection for HiFi downloads. No authentication needed — uses community API instances. Test connection to verify availability.',
        docsId: 'set-download'
    },
    '#deezer-download-settings-container': {
        title: 'Deezer Download Settings',
        description: 'Quality selection and ARL token for Deezer downloads. FLAC requires HiFi subscription. Paste your ARL cookie from the browser.',
        docsId: 'set-download'
    },
    '#youtube-settings-container': {
        title: 'YouTube Settings',
        description: 'Browser cookies selection for bot detection bypass and download delay between requests.',
    },

    // Quality Profile
    '#quality-profile-section': {
        title: 'Quality Profile',
        description: 'Configure which audio formats and bitrates are preferred for Soulseek downloads. Quick presets or custom per-format settings with bitrate ranges.',
        tips: [
            'Audiophile: FLAC only, strict — fails if no lossless found',
            'Balanced: FLAC preferred, MP3 320 fallback (default)',
            'Space Saver: MP3 preferred, smallest files',
            'FLAC bit depth: choose 16-bit, 24-bit, or any',
            'Fallback toggle: when off, only downloads at preferred quality'
        ],
        docsId: 'set-quality'
    },
    '.preset-button': {
        title: 'Quality Preset',
        description: 'One-click quality configuration. Presets set all format enables, priorities, and bitrate ranges at once.',
    },
    '.ranked-targets-editor': {
        title: 'Quality Priority List',
        description: 'Ordered list of acceptable qualities (1st = most preferred). Each source is checked top-down; the first target it can satisfy wins. Lossless matches on bit depth + sample rate; MP3/AAC use a minimum bitrate (≥) so VBR/mono files aren\'t falsely rejected. Drag to reorder.',
        docsId: 'set-quality'
    },
    '#quality-fallback-enabled': {
        title: 'Allow Lossy Fallback',
        description: 'When enabled, accepts any quality if no preferred formats are found. When disabled, downloads fail rather than grabbing lower quality — use for strict lossless libraries.',
        docsId: 'set-quality'
    },

    // Library — File Organization
    '#file-organization-enabled': {
        title: 'File Organization',
        description: 'When enabled, downloaded files are renamed and moved to your transfer path using customizable templates. Separate templates for albums, singles, and playlists.',
        tips: [
            'Variables: $artist, $album, $title, $track, $year, $quality, $albumtype, $disc',
            '$albumtype resolves to Album, Single, EP, or Compilation',
            'Multi-disc albums auto-create Disc N subfolders'
        ],
        docsId: 'set-processing'
    },

    // Library — Post-Processing
    '#metadata-enabled': {
        title: 'Post-Processing',
        description: 'Master toggle for all post-download processing: metadata tagging, cover art embedding, lyrics, and tag embedding from external services.',
        docsId: 'set-processing'
    },
    '#post-processing-options': {
        title: 'Post-Processing Options',
        description: 'Configure which metadata to embed in downloaded files. Per-service toggle controls whether that service\'s IDs and data are written to file tags.',
        tips: [
            'Album art: embeds cover art directly in the audio file',
            'LRC lyrics: fetches synced lyrics from LRClib',
            'Per-service tags: embed Spotify IDs, MusicBrainz IDs, etc.'
        ],
        docsId: 'set-processing'
    },

    // Library — Lossy Copy
    '#lossy-copy-enabled': {
        title: 'Lossy Copy',
        description: 'Create a lower-bitrate copy of every downloaded file alongside the original. Useful for syncing to mobile devices or bandwidth-limited streaming.',
        docsId: 'set-processing'
    },

    // Library — Listening Stats
    '#listening-stats-enabled': {
        title: 'Listening Stats',
        description: 'Track your listening activity from your media server. When enabled, Commissary periodically syncs play history for the Stats page.',
    },

    // Advanced — API Keys
    '#api-keys-list': {
        title: 'API Keys',
        description: 'Manage API keys for external access to Commissary\'s REST API. Generate keys with labels for different integrations.',
    },

    // Advanced — Discovery Pool
    '#discovery-lookback-period': {
        title: 'Discovery Lookback',
        description: 'How far back to look for new releases during watchlist scans. Shorter periods find only recent releases; longer periods catch older missed ones.',
    },
    '#discovery-hemisphere': {
        title: 'Hemisphere',
        description: 'Your geographic hemisphere for seasonal content. Affects which seasonal playlists and albums appear on the Discover page.',
    },

    // Appearance
    '#accent-preset': {
        title: 'Accent Color',
        description: 'Choose a color theme for the entire app. Affects buttons, badges, highlights, and interactive elements throughout Commissary.',
    },
    '#sidebar-visualizer-type': {
        title: 'Sidebar Visualizer',
        description: 'Audio visualization style in the sidebar player. Choose from bars, wave, spectrum, mirror, equalizer, or none.',
    },

    // Save Button
    '.save-settings': {
        title: 'Save Settings',
        description: 'Save all settings changes. Some changes take effect immediately; others require a restart.',
    },

    // ─── DASHBOARD: ENRICHMENT SERVICES ────────────────────────────

    '#enrichment-pills-section': {
        title: 'Enrichment Service Workers',
        description: 'Per-service enrichment workers that run in the background to enrich your library metadata. Each button shows the worker status and lets you start/stop individual services.',
        tips: [
            'Green = running, grey = stopped, red = error',
            'Click a service pill to toggle its worker on/off',
            'Workers process tracks in batches — hover for detailed stats'
        ]
    },
    '#musicbrainz-button': {
        title: 'MusicBrainz Enrichment',
        description: 'Looks up recording IDs, release groups, and artist MBIDs from MusicBrainz. Provides canonical identifiers used by other services.',
    },
    '#audiodb-button': {
        title: 'AudioDB Enrichment',
        description: 'Adds artist bios, band member info, genre tags, and high-res artwork from TheAudioDB.',
    },
    '#deezer-button': {
        title: 'Deezer Enrichment',
        description: 'Enriches tracks with Deezer IDs, BPM data, and genre information from the Deezer catalog.',
    },
    '#spotify-enrich-button': {
        title: 'Spotify Enrichment',
        description: 'Links tracks to Spotify IDs for popularity scores, audio features, and cross-referencing. Requires Spotify OAuth connection.',
    },
    '#itunes-enrich-button': {
        title: 'iTunes Enrichment',
        description: 'Matches tracks to the Apple Music/iTunes catalog for genre tags and iTunes IDs.',
    },
    '#lastfm-enrich-button': {
        title: 'Last.fm Enrichment',
        description: 'Adds Last.fm listener/play counts and community genre tags to your library tracks.',
    },
    '#genius-enrich-button': {
        title: 'Genius Enrichment',
        description: 'Links tracks to Genius for lyrics availability and song descriptions.',
    },
    '#tidal-enrich-button': {
        title: 'Tidal Enrichment',
        description: 'Matches tracks to the Tidal catalog for Tidal IDs and lossless availability info.',
    },
    '#qobuz-enrich-button': {
        title: 'Qobuz Enrichment',
        description: 'Links tracks to Qobuz for Hi-Res availability data and Qobuz IDs.',
    },
    '#discogs-button': {
        title: 'Discogs Enrichment',
        description: 'Enriches with Discogs data — detailed genre/style taxonomy (400+ tags), label info, catalog numbers, and community ratings.',
    },

    // ─── DASHBOARD: RECENT SYNCS & RATE MONITOR ──────────────────────

    '#sync-history-cards': {
        title: 'Recent Syncs',
        description: 'Quick view of your most recent playlist sync operations. Shows playlist name, track counts, and completion status.',
    },
    '#rate-monitor-section': {
        title: 'API Rate Monitor',
        description: 'Live view of API rate limit usage across all metadata services. Shows remaining quota, cooldown timers, and ban status.',
    },
    '#repair-button': {
        title: 'Library Maintenance',
        description: 'Open the maintenance panel to run repair jobs — detect orphan files, fix missing covers, clean live recordings, reorganize files, and more.',
    },
    '#soulid-button': {
        title: 'SoulID Generator',
        description: 'Generate unique fingerprint IDs for your audio files using AcoustID. Useful for deduplication and cross-referencing.',
    },
    '#blacklist-card': {
        title: 'Download Blacklist',
        description: 'Sources that have been blocked from future downloads. Tracks from blacklisted sources will be skipped during search and matching.',
    },

    // ─── DASHBOARD: ACTIVITY FEED ───────────────────────────────────

    '#dashboard-activity-feed': {
        title: 'Activity Feed',
        description: 'Live stream of system events — downloads started/completed, sync progress, enrichment updates, automation triggers, errors, and more. Updates in real-time via WebSocket.',
        tips: [
            'Newest events appear at the top',
            'Events are timestamped and categorized by type',
            'The feed persists across page navigation within the session'
        ]
    },

    // ─── ACTIVE DOWNLOADS PAGE ──────────────────────────────────────

    '.adl-container': {
        title: 'Downloads',
        description: 'Live view of every download happening across the app. Tracks from Search, Sync, Discover, Artists, and Wishlist all appear here in one unified list.',
    },
    '#adl-filter-pills': {
        title: 'Download Filters',
        description: 'Filter downloads by status. "All" shows everything, "Active" shows currently downloading/searching tracks, "Queued" shows waiting tracks, "Completed" and "Failed" show finished items.',
    },
    '#adl-list': {
        title: 'Download List',
        description: 'Each row shows track title, artist, album, which batch it belongs to (playlist name or album), and current status. Active downloads show a spinner, completed show green, failed show red with error details.',
        tips: [
            'Track position (e.g. "3 of 19") shows progress within album/playlist batches',
            'Section headers group downloads by status category',
            'List updates every 2 seconds while you\'re on this page'
        ]
    },
    '#adl-clear-btn': {
        title: 'Clear Completed',
        description: 'Remove all completed, failed, and cancelled downloads from the list. Only affects the tracker display — does not delete any downloaded files.',
    },

    // ─── PLAYLIST EXPLORER PAGE ──────────────────────────────────────

    '#playlist-explorer-page': {
        title: 'Playlist Explorer',
        description: 'Visual exploration tool for deep-diving into playlists. Browse album art grids, explore full artist discographies, and batch-select tracks for download or wishlist.',
        tips: [
            'Pick a playlist source (Spotify, Tidal, Deezer, ListenBrainz) and select a playlist',
            'Albums view shows album art cards; Full Discog view shows complete artist discographies',
            'Select tracks across multiple albums, then use the action bar to download or wishlist them all'
        ]
    },
    '#explorer-playlist-picker': {
        title: 'Playlist Picker',
        description: 'Choose which playlist to explore. Select a source tab, then pick a playlist from the dropdown.',
    },
    '.explorer-mode-btn': {
        title: 'View Mode Toggle',
        description: 'Switch between Albums view (grouped by album with artwork) and Full Discog view (complete discography for each artist in the playlist).',
    },
    '#explorer-build-btn': {
        title: 'Explore Playlist',
        description: 'Load the selected playlist and build the visual explorer view. Fetches album art and track listings from your metadata source.',
    },
    '#explorer-action-bar': {
        title: 'Selection Action Bar',
        description: 'Appears when tracks are selected. Shows selection count and provides batch actions — add to wishlist or download all selected tracks.',
    },

    // ─── ISSUES PAGE ────────────────────────────────────────────────

    '.issues-header': {
        title: 'Issues & Findings',
        description: 'Library health scanner results. Each finding is a detected problem — missing files, duplicate tracks, incomplete albums, bad metadata, and more.',
    },
    '#issues-filters': {
        title: 'Issue Filters',
        description: 'Filter findings by category (Missing Files, Duplicates, Metadata Gaps, etc.), severity, or job type. Helps focus on the most important issues first.',
    },
    '#issues-list': {
        title: 'Findings List',
        description: 'Each row is a detected issue with details, severity, and available actions. Click "Fix" to auto-repair, "Dismiss" to hide, or expand for more details.',
        tips: [
            'Green "Fix" button applies the suggested repair automatically',
            'Dismissed findings are hidden but can be restored from filters',
            'Run repair jobs from Settings > Maintenance to generate new findings'
        ]
    },

    // ─── DISCOVER PAGE: ADDITIONAL ─────────────────────────────────

    '#your-artists-section': {
        title: 'Your Artists',
        description: 'Carousel of artists from your watchlist. Quick access to view their latest releases, discography, or manage watchlist settings.',
    },

    '#your-albums-section': {
        title: 'Your Albums',
        description: 'Albums you\'ve saved or liked across connected services (Spotify, Tidal, Deezer). Shows which are already in your library and lets you download missing ones.',
    },

    // ─── PERSONAL SETTINGS ─────────────────────────────────────────

    '#personal-settings-btn': {
        title: 'My Settings',
        description: 'Personal settings for your profile — accent color, home page preference, notification preferences, and other per-user customizations.',
    },
};

// ── Docs Navigation Helper ───────────────────────────────────────────────

function _navigateToDocsSection(docsId) {
    dismissHelperPopover();
    toggleHelperMode();
    navigateToPage('help');

    // Wait for docs page to initialize, then simulate a nav click
    setTimeout(() => {
        // Try clicking the nav section title first (top-level like 'dashboard', 'sync')
        const navTitle = document.querySelector(`.docs-nav-section-title[data-target="${docsId}"]`);
        if (navTitle) {
            navTitle.click();
            return;
        }

        // Try clicking a child nav item (subsections like 'gs-connecting', 'set-media')
        const navChild = document.querySelector(`.docs-nav-child[data-target="${docsId}"]`);
        if (navChild) {
            // Expand parent section first
            const parentSection = navChild.closest('.docs-nav-section');
            if (parentSection) {
                const parentTitle = parentSection.querySelector('.docs-nav-section-title');
                if (parentTitle && !parentTitle.classList.contains('expanded')) {
                    parentTitle.click();
                }
            }
            setTimeout(() => navChild.click(), 200);
            return;
        }

        // Fallback: scroll to element by ID
        const el = document.getElementById(docsId) || document.getElementById('docs-' + docsId);
        if (el) {
            const docsContent = document.getElementById('docs-content');
            if (docsContent) {
                el.scrollIntoView({ behavior: 'smooth', block: 'start' });
            }
        }
    }, 600);
}

// ═══════════════════════════════════════════════════════════════════════════
// HELPER MENU & MODE SYSTEM
// ═══════════════════════════════════════════════════════════════════════════

const HELPER_MENU_ITEMS = [
    { id: 'info',         icon: '🎯', label: 'Element Info',    desc: 'Click any element to learn about it' },
    { id: 'tour',         icon: '🚶', label: 'Guided Tour',     desc: 'Step-by-step walkthrough' },
    { id: 'search',       icon: '🔍', label: 'Search Help',     desc: 'Find answers fast' },
    { id: 'shortcuts',    icon: '⌨️', label: 'Shortcuts',       desc: 'Keyboard reference' },
    { id: 'setup',        icon: '📋', label: 'Setup Progress',  desc: 'Onboarding checklist' },
    { id: 'whats-new',    icon: '✨', label: "What's New",      desc: 'Latest features' },
    { id: 'troubleshoot', icon: '🔧', label: 'Troubleshoot',    desc: 'Fix common issues' },
];

function toggleHelperMode() {
    // If a mode is active, deactivate everything
    if (HelperState.mode) {
        exitHelperMode();
        return;
    }
    // If menu is open, close it
    if (HelperState.menuOpen) {
        closeHelperMenu();
        return;
    }
    // Otherwise, open the menu
    openHelperMenu();
}

// Map page IDs → tour IDs (only where they differ)
const PAGE_TOUR_MAP = {
    'dashboard':   'dashboard',
    'sync':        'sync-playlist',
    'search':      'first-download',
    'downloads':   'first-download',  // legacy id — the Search page used to be called 'downloads'
    'discover':    'discover',
    'automations': 'automations',
    'library':     'library',
    'stats':       'stats',
    'import':      'import-music',
    'settings':    'settings-tour',
    'issues':      'issues-tour',
};

function openHelperMenu() {
    closeHelperMenu();
    HelperState.menuOpen = true;

    const floatBtn = document.getElementById('helper-float-btn');
    if (!floatBtn) return;

    // User has discovered the help system — stop the idle glow permanently
    floatBtn.classList.remove('undiscovered');
    localStorage.setItem('soulsync_helper_discovered', '1');
    floatBtn.classList.add('menu-open');

    // Detect current page for contextual tour suggestion
    const currentPage = document.querySelector('.page.active')?.id?.replace('-page', '') || '';
    const suggestedTourId = PAGE_TOUR_MAP[currentPage];
    const suggestedTour = suggestedTourId ? HELPER_TOURS[suggestedTourId] : null;

    const menu = document.createElement('div');
    menu.className = 'helper-menu';

    let contextualBtn = '';
    if (suggestedTour) {
        contextualBtn = `
            <button class="helper-menu-item helper-menu-contextual" onclick="closeHelperMenu();HelperState.mode='tour';document.getElementById('helper-float-btn')?.classList.add('active');startTour('${suggestedTourId}')" style="animation-delay:0s">
                <span class="helper-menu-icon">${suggestedTour.icon}</span>
                <span class="helper-menu-label">${suggestedTour.title}</span>
                <span class="helper-menu-badge">${suggestedTour.steps.length} steps</span>
            </button>
            <div class="helper-menu-divider"></div>
        `;
    }

    const offset = suggestedTour ? 1 : 0;
    menu.innerHTML = contextualBtn + HELPER_MENU_ITEMS.map((item, i) => `
        <button class="helper-menu-item" onclick="activateHelperMode('${item.id}')" style="animation-delay:${(i + offset) * 0.04}s">
            <span class="helper-menu-icon">${item.icon}</span>
            <span class="helper-menu-label">${item.label}</span>
        </button>
    `).join('');

    document.body.appendChild(menu);
    _helperMenu = menu;

    // Position above the float button
    const btnRect = floatBtn.getBoundingClientRect();
    menu.style.right = (window.innerWidth - btnRect.right) + 'px';
    menu.style.bottom = (window.innerHeight - btnRect.top + 8) + 'px';

    requestAnimationFrame(() => menu.classList.add('visible'));

    // Close on click outside
    setTimeout(() => {
        document.addEventListener('click', _helperMenuOutsideClick);
    }, 10);
}

function _helperMenuOutsideClick(e) {
    const floatBtn = document.getElementById('helper-float-btn');
    if (_helperMenu && !_helperMenu.contains(e.target) && !(floatBtn && floatBtn.contains(e.target))) {
        closeHelperMenu();
    }
}

function closeHelperMenu() {
    document.removeEventListener('click', _helperMenuOutsideClick);
    if (_helperMenu) {
        _helperMenu.remove();
        _helperMenu = null;
    }
    HelperState.menuOpen = false;
    const floatBtn = document.getElementById('helper-float-btn');
    if (floatBtn) floatBtn.classList.remove('menu-open');
}

function activateHelperMode(mode) {
    closeHelperMenu();
    HelperState.mode = mode;

    const floatBtn = document.getElementById('helper-float-btn');
    if (floatBtn) floatBtn.classList.add('active');

    switch (mode) {
        case 'info':
            helperModeActive = true;
            document.body.classList.add('helper-mode-active');
            break;
        case 'tour':        openTourSelector(); break;
        case 'search':      openHelperSearch(); break;
        case 'shortcuts':   openShortcutsOverlay(); break;
        case 'setup':       openSetupPanel(); break;
        case 'whats-new':   openWhatsNew(); break;
        case 'troubleshoot': activateTroubleshootMode(); break;
    }
}

function exitHelperMode() {
    helperModeActive = false;
    HelperState.mode = null;
    document.body.classList.remove('helper-mode-active');
    dismissHelperPopover();
    dismissTour();
    closeSetupPanel();
    closeShortcutsOverlay();
    closeHelperSearch();
    closeTroubleshootMode();

    const floatBtn = document.getElementById('helper-float-btn');
    if (floatBtn) floatBtn.classList.remove('active');
}

// ═══════════════════════════════════════════════════════════════════════════
// GUIDED TOUR ENGINE
// ═══════════════════════════════════════════════════════════════════════════

const HELPER_TOURS = {
    'dashboard': {
        title: 'Dashboard Tour',
        description: 'Learn what each section of the dashboard does.',
        icon: '📊',
        steps: [
            // Header area (top of page)
            { page: 'dashboard', selector: '.dashboard-header', title: 'Welcome to Commissary', description: 'This is your Music Dashboard — the central hub for monitoring your music system. Let\'s walk through everything from top to bottom.' },
            { page: 'dashboard', selector: '.dashboard-header .header-actions', title: 'Enrichment Worker Orbs', description: 'Each orb is a live metadata worker — MusicBrainz, AudioDB, Deezer, Spotify, iTunes, Last.fm, Genius and friends. They pulse while enriching your library; hover one for its current status and progress.' },
            { page: 'dashboard', selector: '#watchlist-button', title: 'Watchlist', description: 'Artists you follow for new releases. Click to manage watched artists, run scans, and configure per-artist download preferences.' },
            { page: 'dashboard', selector: '#wishlist-button', title: 'Wishlist', description: 'Tracks queued for download. Failed downloads, watchlist discoveries, and manual additions all land here for retry.' },

            // Main content — top to bottom
            { page: 'dashboard', selector: '.service-status-grid', title: 'Service Status', description: 'Your three core connections at a glance: metadata source (Spotify/iTunes/Deezer), media server (Plex/Jellyfin/Navidrome), and download source. Each card shows live status, response time, and a Test button.' },
            { page: 'dashboard', selector: '.stats-grid-dashboard', title: 'System Stats', description: 'Real-time metrics: active downloads, speed, sync operations, uptime, and memory usage. Updates live via WebSocket.' },
            { page: 'dashboard', selector: '#library-status-card', title: 'Library', description: 'Your library at a glance — artists, albums, tracks, and total size — plus the scan buttons. Incremental scan picks up new content fast; Deep Scan re-reads everything and clears out stale entries.' },
            { page: 'dashboard', selector: '#sync-history-cards', title: 'Recent Syncs', description: 'Your latest playlist sync runs — what matched, what downloaded, what failed. Click one to jump into the details.' },
            { page: 'dashboard', selector: '.dash-card--quick-actions', title: 'Quick Actions', description: 'One-click shortcuts to the things you do most — start a sync, open the tool pages, jump to search. The bigger Tools collection lives on its own pages under the System section of the sidebar.' },
            { page: 'dashboard', selector: '#dashboard-activity-feed', title: 'Recent Activity', description: 'Live stream of system events — downloads, syncs, enrichment updates, errors. Newest at the top, updates in real-time via WebSocket.' },
            { page: 'dashboard', selector: '#enrichment-pills-section', title: 'Enrichment Services', description: 'Per-service enrichment coverage — how much of your library each metadata service has processed, with controls to manage priorities and intervals.' },

            // The shell around every page
            { page: 'dashboard', selector: '.side-toggle', title: 'Music / Video Toggle', description: 'Commissary has two whole sides. This switch flips between the MUSIC app and the VIDEO app (movies + TV) — each has its own pages, library, and settings.' },
            { page: 'dashboard', selector: '#profile-indicator', title: 'Your Profile', description: 'Who\'s signed in. Click to switch profiles; the small icons open My Accounts (per-profile streaming logins) and My Settings.' },
            { page: 'dashboard', selector: '.nav-section-label[data-section="find"]', title: 'Find', description: 'Discovery lives here — Search, Discover, and the Artist Map. Section headers collapse if you like a tidy sidebar.' },
            { page: 'dashboard', selector: '.nav-section-label[data-section="music"]', title: 'Music', description: 'Your collection: Library, Playlists & Sync, Downloads, and Import for files you already have.' },
            { page: 'dashboard', selector: '.nav-section-label[data-section="system"]', title: 'System', description: 'The machinery: Automations, Tools, Stats, Issues, and Settings.' },
            { page: 'dashboard', selector: '.version-button', title: 'Version', description: 'Click the version number for release notes — it glows when an update is available (green routine, yellow major, red critical). That\'s the dashboard! 🎉' },
        ]
    },
    'first-download': {
        title: 'Your First Download',
        description: 'Step-by-step guide to downloading your first album.',
        icon: '⬇️',
        steps: [
            { page: 'search', selector: '#enh-source-row', title: 'Pick a Search Source', description: 'Each icon is a metadata source. The highlighted one is where your next search goes — defaults to your configured primary source. Click a different icon to switch to Spotify, Apple Music, Deezer, Discogs, Hydrabase, MusicBrainz, Music Videos, or Soulseek (raw P2P files). A small dot marks sources you\'ve already searched for the current query.' },
            { page: 'search', selector: '.enhanced-search-input-wrapper', title: 'Search for Music', description: 'Type an artist or album name here. Results appear in categorized sections — Artists, Albums, Singles/EPs, and Tracks. Try searching for your favorite artist now!' },
            { page: 'search', selector: '#enhanced-results-container', title: 'Search Results', description: 'After searching, results appear organized by type: Artists at the top as cards, then Albums, Singles/EPs, and individual Tracks. "In Library" badges mark items you already own.' },
            { page: 'search', selector: '.enhanced-search-input-wrapper', title: 'Downloading an Album', description: 'Click any album card to open the download modal. You\'ll see the tracklist, quality options, and a big "Download Album" button. Individual tracks have a play button to preview before downloading.' },
            { page: 'search', selector: '.enhanced-search-input-wrapper', title: 'That\'s It!', description: 'Search, click, download. Albums go to your configured download path, get tagged with metadata, and sync to your media server automatically. Active downloads live on the dedicated Downloads page.' },
        ]
    },
    'sync-playlist': {
        title: 'Sync a Playlist',
        description: 'Import and download playlists from streaming services.',
        icon: '🔄',
        steps: [
            // Header
            { page: 'sync', selector: '.sync-header', title: 'Playlist Sync', description: 'Import playlists from any streaming service, match tracks to your download sources, and sync them to your media server. Everything happens from this page.' },
            { page: 'sync', selector: '.sync-history-btn', title: 'Sync History', description: 'View a log of all past sync operations — when they ran, how many tracks matched, and which ones failed. Useful for tracking down missing tracks.' },

            // Source tabs (left to right)
            { page: 'sync', selector: '.sync-tab-button[data-tab="spotify"]', title: 'Spotify Playlists', description: 'If Spotify is connected, click "Refresh" to load all your playlists. Select ones you want, then hit Start Sync in the sidebar.' },
            { page: 'sync', selector: '.sync-tab-button[data-tab="spotify-public"]', title: 'Spotify Link', description: 'Don\'t have a Spotify account? Paste any public Spotify playlist or album URL here to import it without authentication.' },
            { page: 'sync', selector: '.sync-tab-button[data-tab="tidal"]', title: 'Tidal Playlists', description: 'Same as Spotify — connect Tidal in Settings, refresh to load your playlists, then sync.' },
            { page: 'sync', selector: '.sync-tab-button[data-tab="deezer"]', title: 'Deezer', description: 'Paste a Deezer playlist URL to import. No account needed — just the public URL.' },
            { page: 'sync', selector: '.sync-tab-button[data-tab="youtube"]', title: 'YouTube Music', description: 'Paste a YouTube Music playlist URL. The parser extracts track titles and artists, then matches them against your metadata source.' },
            { page: 'sync', selector: '.sync-tab-button[data-tab="beatport"]', title: 'Beatport', description: 'For electronic music — paste a Beatport playlist URL to import DJ sets and charts.' },
            { page: 'sync', selector: '.sync-tab-button[data-tab="import-file"]', title: 'File Import', description: 'Import a playlist from a local file — M3U, CSV, or plain text. Map columns to track/artist/album fields.' },
            { page: 'sync', selector: '.sync-tab-button[data-tab="mirrored"]', title: 'Mirrored Playlists', description: 'Every imported playlist is saved here permanently. Re-sync anytime to catch new additions, check match status, or view the Discovery Pool for unmatched tracks.' },

            // Sidebar
            { page: 'sync', selector: '.sync-sidebar', title: 'Sync Controls', description: 'The command center. Select playlists with checkboxes on the left, then click "Start Sync" here. Progress bars, match counts, and logs update in real-time. That\'s the sync flow! 🎉' },
        ]
    },
    // 'artists-browse' tour retired — the Artists sidebar entry was replaced by the
    // unified Search page (see the first-download tour for the new flow).
    'automations': {
        title: 'Build an Automation',
        description: 'Create automated workflows with triggers and actions.',
        icon: '🤖',
        steps: [
            // List view (visible on load)
            { page: 'automations', selector: '#automations-list-view', title: 'Automations Overview', description: 'All your automations live here, organized into System (built-in), Custom groups, and My Automations. Each card shows its WHEN trigger, DO action, and THEN notifications.' },
            { page: 'automations', selector: '#automations-stats', title: 'Stats Bar', description: 'Quick counts of total automations, how many are active, paused, and custom. Also shows system automations running background tasks like enrichment and watchlist scanning.' },
            { page: 'automations', selector: '.auto-new-btn', title: 'Create New Automation', description: 'Opens the visual builder. Choose a trigger (WHEN), an action (DO), and optional notifications (THEN). Triggers include schedules, events (download complete, new release), and signals from other automations.' },

            // Builder (describe since it requires clicking)
            { page: 'automations', selector: '.auto-new-btn', title: 'The Builder', description: 'The builder has a sidebar with draggable blocks and a canvas. Drag a WHEN block (e.g., "Every 6 hours"), a DO block (e.g., "Run Watchlist Scan"), and optionally a THEN block (e.g., "Send Discord notification").' },
            { page: 'automations', selector: '.auto-new-btn', title: 'Signals & Chains', description: 'Advanced: automations can fire "signals" that trigger other automations, creating chains. Example: Watchlist scan → fires "new_release" signal → Download automation picks it up. Max chain depth is 5.' },

            // Hub section
            { page: 'automations', selector: '#auto-section-hub', title: 'Automation Hub', description: 'Pre-built templates, pipeline recipes, quick-start guides, and reference docs. Browse Pipelines for ready-made multi-step workflows, or check Recipes for common automation patterns. Great starting point! 🎉' },
        ]
    },
    'library': {
        title: 'Library Management',
        description: 'Browse and manage your music collection.',
        icon: '📚',
        steps: [
            // Header
            { page: 'library', selector: '.library-header', title: 'Music Library', description: 'Your complete music collection synced from your media server. The header shows your total artist count. Everything here comes from your last Database Updater run.' },

            // Controls
            { page: 'library', selector: '#library-search-input', title: 'Search Artists', description: 'Type to filter your library by artist name. Results update instantly as you type.' },
            { page: 'library', selector: '#watchlist-filter', title: 'Watchlist Filter', description: 'Filter by watchlist status: All, Watched (artists you follow for new releases), or Unwatched. The "Watch All Unwatched" button adds every remaining artist to your watchlist in one click.' },
            { page: 'library', selector: '#alphabet-selector', title: 'Alphabet Jump', description: 'Click any letter to jump directly to artists starting with that letter. Great for navigating large libraries.' },

            // Grid
            { page: 'library', selector: '#library-artists-grid', title: 'Artist Grid', description: 'Your artists as cards with photos, track counts, and service badges (Spotify, MusicBrainz, etc.). Click any card to open their artist detail page with full discography.' },

            // Pagination
            { page: 'library', selector: '#library-pagination', title: 'Pagination', description: 'Shows 75 artists per page. Use Previous/Next to browse, or combine with the alphabet selector and search to find artists faster.' },

            // Artist detail (describe what they'll see)
            { page: 'library', selector: '#library-artists-grid', title: 'Artist Detail View', description: 'Clicking an artist opens their detail page. From there you can view/download their discography, toggle "Enhanced Management" mode for inline tag editing, bulk operations, and writing tags to files. 🎉' },
        ]
    },
    'discover': {
        title: 'Discover Music',
        description: 'Explore personalized playlists, genre browsing, and new music.',
        icon: '🔮',
        steps: [
            // Hero section
            { page: 'discover', selector: '.discover-hero', title: 'Featured Artists', description: 'The hero slideshow showcases recommended artists based on your library. Use the arrows to browse, or click "View Discography" to explore their music. "Add to Watchlist" starts monitoring them for new releases.' },
            { page: 'discover', selector: '#discover-hero-view-all', title: 'View All Recommendations', description: 'Opens a modal with all recommended artists at once. "Watch All" adds every recommended artist to your watchlist in one click.' },

            // Content sections (top to bottom)
            { page: 'discover', selector: '#recent-releases-carousel', title: 'Recent Releases', description: 'New music from artists in your watchlist. Album cards show cover art — click any to open the download modal. Updates automatically when watchlist scans find new releases.' },
            { page: 'discover', selector: '#seasonal-albums-section', title: 'Seasonal Content', description: 'Season-aware sections that appear automatically — Christmas albums in December, summer vibes in July. Includes curated albums and a Seasonal Mix playlist you can sync to your server.' },

            // Playlists
            { page: 'discover', selector: '#release-radar-playlist', title: 'Fresh Tape', description: 'A playlist of brand-new tracks from recent releases. Each has Download and Sync buttons — sync sends the playlist directly to your media server as a new playlist.' },
            { page: 'discover', selector: '#discovery-weekly-playlist', title: 'The Archives', description: 'Curated tracks from your existing collection. Every playlist section has Download (grab missing tracks) and Sync (push to media server) buttons.' },

            // Build a playlist
            { page: 'discover', selector: '.build-playlist-container', title: 'Build a Playlist', description: 'Create custom playlists from seed artists. Search and select 1-5 artists, hit Generate, and get a 50-track playlist mixing your picks with similar artist discoveries. Download or sync the result.' },

            // ListenBrainz
            { page: 'discover', selector: '.listenbrainz-tabs', title: 'ListenBrainz Playlists', description: 'If ListenBrainz is connected, algorithmic playlists generated from your listening history appear here — weekly jams, exploration picks, and more.' },

            // Time Machine & Genre
            { page: 'discover', selector: '#decade-tabs', title: 'Time Machine', description: 'Browse music by decade — click a decade tab to see tracks from that era in your library. Great for rediscovering older music.' },
            { page: 'discover', selector: '#genre-tabs', title: 'Browse by Genre', description: 'Explore your library organized by genre. Click a genre pill to see artists and tracks in that category. Genres come from all your metadata sources. 🎉' },
        ]
    },
    'stats': {
        title: 'Listening Stats',
        description: 'Understand your listening habits and library health.',
        icon: '📊',
        steps: [
            // Header controls
            { page: 'stats', selector: '#stats-time-range', title: 'Time Range', description: 'Switch between 7 Days, 30 Days, 12 Months, and All Time. All charts and rankings below update to reflect the selected period.' },
            { page: 'stats', selector: '#stats-sync-btn', title: 'Sync Now', description: 'Pulls the latest listening data from your media server (Plex, Jellyfin, or Navidrome). Data syncs automatically, but you can force a refresh here.' },

            // Overview cards
            { page: 'stats', selector: '#stats-overview', title: 'Overview Cards', description: 'At-a-glance metrics: Total Plays, Listening Time, unique Artists, Albums, and Tracks you\'ve listened to in the selected time range.' },

            // Charts (left column)
            { page: 'stats', selector: '#stats-timeline-chart', title: 'Listening Activity', description: 'A timeline chart showing your listening pattern over time. Spot trends — are you listening more on weekends? Did you binge a new album last week?' },
            { page: 'stats', selector: '#stats-genre-chart', title: 'Genre Breakdown', description: 'Pie chart showing which genres you listen to most. The legend shows exact percentages. Useful for understanding your taste profile.' },
            { page: 'stats', selector: '#stats-recent-plays', title: 'Recently Played', description: 'A live feed of your most recent plays with timestamps, artist, and album info.' },

            // Rankings (right column)
            { page: 'stats', selector: '#stats-top-artists', title: 'Top Artists', description: 'Your most-played artists ranked by play count. The visual bar chart at the top shows relative listening time.' },
            { page: 'stats', selector: '#stats-top-albums', title: 'Top Albums', description: 'Most-played albums in the selected time range. Click any to navigate to the artist detail page.' },
            { page: 'stats', selector: '#stats-top-tracks', title: 'Top Tracks', description: 'Your most-played individual tracks. Great for building playlists from your actual favorites.' },

            // Library health
            { page: 'stats', selector: '#stats-library-health', title: 'Library Health', description: 'Technical metrics about your collection: audio format breakdown (FLAC vs MP3 vs others), unplayed tracks count, total duration, and total track count.' },
            { page: 'stats', selector: '#stats-enrichment-coverage', title: 'Enrichment Coverage', description: 'Shows how much of your library has been enriched with metadata from external services. Higher coverage means better search results and recommendations.' },

            // Storage
            { page: 'stats', selector: '#stats-db-storage-chart', title: 'Database Storage', description: 'A donut chart showing how your database space is used — metadata, cache, enrichment data, settings, etc. Helps you understand what\'s using disk space. 🎉' },
        ]
    },
    'import-music': {
        title: 'Import Music',
        description: 'Import existing audio files into your organized library.',
        icon: '📥',
        steps: [
            // Header
            { page: 'import', selector: '#import-page', title: 'Import Music', description: 'Import audio files from your import folder into your organized library. Files are matched to album metadata, tagged, and moved to the correct location.' },
            { page: 'import', selector: '#import-page-staging-path', title: 'Import Folder', description: 'Shows your configured import folder path and stats (file count, total size). This is where you drop audio files before importing — the refresh arrow re-scans it after you add files. Configure the path in Settings → Downloads.' },

            // Queue
            { page: 'import', selector: '#import-page-queue', title: 'Processing Queue', description: 'When you process albums or singles, jobs appear here with progress indicators. "Clear finished" removes completed jobs from the list.' },

            // Tabs
            { page: 'import', selector: '#import-page-tab-album', title: 'Albums vs Singles', description: 'Two modes: Albums tab matches full albums to metadata (cover art, track numbers, disc info). Singles tab processes individual files one at a time.' },

            // Album workflow
            { page: 'import', selector: '#import-page-suggestions', title: 'Album Suggestions', description: 'The importer analyzes your import files and suggests album matches based on embedded tags. Click a suggestion to start the matching process.' },
            { page: 'import', selector: '#import-page-album-search-input', title: 'Album Search', description: 'If suggestions don\'t match, search manually. Type an album name, click Search, and select the correct result.' },
            { page: 'import', selector: '#import-page-album-search-input', title: 'Track Matching', description: 'After selecting an album, you\'ll see a track matching table. Files are auto-matched to tracks by name/number. Drag unmatched files from the pool to the correct track slot, then click "Process Album".' },

            // Singles workflow
            { page: 'import', selector: '#import-page-tab-singles', title: 'Singles Import', description: 'The Singles tab lists all individual audio files. Select files with checkboxes (or "Select All"), then click "Process Selected" to tag and move them into your library. 🎉' },
        ]
    },
    'settings-tour': {
        title: 'Settings Walkthrough',
        description: 'Configure services, downloads, and preferences.',
        icon: '⚙️',
        steps: [
            // Tab bar
            { page: 'settings', selector: '.stg-tabbar', title: 'Settings Tabs', description: 'Settings are organized into 5 tabs: Connections (API keys, server setup), Downloads (sources, paths, quality), Library (file organization, post-processing), Appearance (theme, colors), and Advanced.' },

            // Connections
            { page: 'settings', selector: '.stg-tab[data-tab="connections"]', title: 'Connections Tab', description: 'This is where you connect all your services. API keys for Spotify, Tidal, Last.fm, Genius, AcoustID, and your metadata source preference. Plus your media server (Plex, Jellyfin, or Navidrome).' },
            { page: 'settings', selector: '.api-service-frame', title: 'API Configuration', description: 'Each service has its own frame with credential fields and an Authenticate/Test button. Spotify needs a Client ID + Secret from the Developer Dashboard. Last.fm needs an API key for scrobbling and stats.' },
            { page: 'settings', selector: '.server-toggle-container', title: 'Media Server', description: 'Toggle on your media server — Plex, Jellyfin, or Navidrome. Enter the server URL and token/API key. This is where your music library lives and where downloads get synced to.' },

            // Downloads
            { page: 'settings', selector: '.stg-tab[data-tab="downloads"]', title: 'Downloads Tab', description: 'Configure where music comes from and where it goes. Set your download source (Soulseek, YouTube, Tidal, Qobuz, HiFi, Deezer, or Hybrid mode), download paths, and quality preferences.' },
            { page: 'settings', selector: '.stg-tab[data-tab="downloads"]', title: 'Quality Profiles', description: 'Quality profiles control what files are acceptable — format (FLAC, MP3, etc.), minimum bitrate, bit depth preference, and peer speed requirements. The waterfall filter tries your preferred format first, then falls back.' },

            // Library
            { page: 'settings', selector: '.stg-tab[data-tab="library"]', title: 'Library Tab', description: 'File organization templates (folder structure, naming), post-processing rules (auto-tag, convert formats), M3U playlist export settings, and content filtering options.' },

            // Appearance
            { page: 'settings', selector: '.stg-tab[data-tab="appearance"]', title: 'Appearance Tab', description: 'Customize the UI — accent color picker to theme the entire interface to your taste.' },

            // Advanced
            { page: 'settings', selector: '.stg-tab[data-tab="advanced"]', title: 'Advanced Tab', description: 'Power-user settings, logging configuration, and system-level options. Most users won\'t need to touch this.' },

            // Save
            { page: 'settings', selector: '.save-button', title: 'Save Settings', description: 'Don\'t forget to save! Changes aren\'t applied until you click this button. Some settings (like download source changes) take effect immediately after saving. 🎉' },
        ]
    },
    'issues-tour': {
        title: 'Issues Tracker',
        description: 'Track and resolve problems in your library.',
        icon: '🐛',
        steps: [
            { page: 'issues', selector: '.issues-header', title: 'Issues Tracker', description: 'A built-in issue tracker for your music library. Report wrong tracks, bad metadata, missing albums, audio quality problems, and more. Issues are tracked through open → in progress → resolved.' },
            { page: 'issues', selector: '#issues-filters', title: 'Filters', description: 'Filter by status (Open, In Progress, Resolved, Dismissed) and category (Wrong Track, Wrong Artist, Audio Quality, Missing Tracks, Incomplete Album, etc.).' },
            { page: 'issues', selector: '#issues-stats', title: 'Stats Bar', description: 'Quick count of issues by status. Helps you see at a glance how many open issues need attention.' },
            { page: 'issues', selector: '#issues-list', title: 'Issues List', description: 'All issues matching your current filters. Click any issue to see details, add notes, change status, or take action (like re-downloading a track). 🎉' },
        ]
    },
};

function openTourSelector() {
    dismissHelperPopover();
    const popover = document.createElement('div');
    popover.className = 'helper-popover helper-tour-selector';
    popover.innerHTML = `
        <div class="helper-popover-header">
            <div class="helper-popover-title">Choose a Tour</div>
            <button class="helper-popover-close" onclick="exitHelperMode()">&times;</button>
        </div>
        <div class="helper-tour-list">
            ${Object.entries(HELPER_TOURS).map(([id, tour]) => `
                <button class="helper-tour-option" onclick="startTour('${id}')">
                    <span class="helper-tour-option-icon">${tour.icon || '🚶'}</span>
                    <div class="helper-tour-option-body">
                        <div class="helper-tour-option-title">${tour.title}</div>
                        <div class="helper-tour-option-desc">${tour.description}</div>
                    </div>
                    <div class="helper-tour-option-steps">${tour.steps.length} steps</div>
                </button>
            `).join('')}
        </div>
    `;
    document.body.appendChild(popover);
    _helperPopover = popover;

    // Position near the float button
    const floatBtn = document.getElementById('helper-float-btn');
    if (floatBtn) {
        const btnRect = floatBtn.getBoundingClientRect();
        popover.style.right = (window.innerWidth - btnRect.right) + 'px';
        popover.style.bottom = (window.innerHeight - btnRect.top + 8) + 'px';
        popover.style.left = 'auto';
        popover.style.top = 'auto';
    }
    requestAnimationFrame(() => popover.classList.add('visible'));
}

function startTour(tourId) {
    const tour = HELPER_TOURS[tourId];
    if (!tour) return;

    dismissHelperPopover();
    HelperState.tourId = tourId;
    HelperState.tourStep = 0;

    showTourStep();
}

function showTourStep() {
    const tour = HELPER_TOURS[HelperState.tourId];
    if (!tour) return;

    const step = tour.steps[HelperState.tourStep];
    if (!step) { dismissTour(); return; }

    dismissHelperPopover();
    removeTourOverlay();

    // Navigate to the correct page if needed
    if (step.page) {
        const currentPage = document.querySelector('.page.active')?.id?.replace('-page', '') || '';
        if (currentPage !== step.page) {
            navigateToPage(step.page);
        }
    }
    // Resolve the anchor with RETRIES — pages render async (React mounts,
    // fetch-then-render lists), and the old fixed 350ms wait was the "box
    // jumps to a corner and lives there" bug: the selector missed once and
    // every later step rendered against nothing.
    _resolveTourTarget(step.selector, (target) => {
        // The user may have advanced/exited while we were resolving.
        if (HelperState.tourId && tour.steps[HelperState.tourStep] === step) {
            _renderTourStep(tour, step, target);
        }
    });
}

// Poll for a VISIBLE anchor (display:none / unmounted elements don't count),
// then give up honestly after ~2s so the step centers itself instead of
// anchoring to a hidden element's garbage rect.
function _resolveTourTarget(selector, cb, attempt = 0) {
    const el = selector ? document.querySelector(selector) : null;
    const visible = el && el.offsetParent !== null && el.getClientRects().length > 0;
    if (visible) { cb(el); return; }
    if (attempt >= 8) { cb(null); return; }
    setTimeout(() => _resolveTourTarget(selector, cb, attempt + 1), 250);
}

function _renderTourStep(tour, step, target) {

    // Spotlight scrim: FOUR panels around a real hole. The old single
    // overlay + z-index-raise-the-target trick failed for any target inside
    // an ancestor stacking context (transform/backdrop-filter — most
    // dashboard cards), which is why highlighted elements stayed dimmed
    // and blurred behind the overlay.
    _tourOverlay = document.createElement('div');
    _tourOverlay.className = 'helper-tour-overlay';
    for (let i = 0; i < 4; i++) {
        const panel = document.createElement('div');
        panel.className = 'helper-tour-scrim';
        panel.addEventListener('click', () => dismissTour());
        _tourOverlay.appendChild(panel);
    }
    document.body.appendChild(_tourOverlay);

    // Highlight target — scroll INSTANTLY so every rect below is final
    // (smooth scrolling made the hole + popover anchor to mid-animation
    // positions, another way the box ended up stranded).
    if (target) {
        target.classList.add('helper-tour-target');
        _helperHighlighted = target;
        target.scrollIntoView({ behavior: 'auto', block: 'center' });
    }
    _updateTourSpotlight(target);

    // Build tour popover
    const stepNum = HelperState.tourStep + 1;
    const totalSteps = tour.steps.length;
    const isFirst = stepNum === 1;
    const isLast = stepNum === totalSteps;
    const progressPct = (stepNum / totalSteps * 100).toFixed(0);

    const popover = document.createElement('div');
    popover.className = 'helper-popover helper-tour-popover';
    popover.innerHTML = `
        <div class="helper-popover-arrow"></div>
        <div class="helper-tour-progress-bar">
            <div class="helper-tour-progress-fill" style="width:${progressPct}%"></div>
        </div>
        <div class="helper-tour-step-counter">Step ${stepNum} of ${totalSteps}</div>
        <div class="helper-popover-header">
            <div class="helper-popover-title">${step.title}</div>
        </div>
        <div class="helper-popover-desc">${step.description}</div>
        <div class="helper-tour-nav">
            ${!isFirst ? '<button class="helper-tour-btn" onclick="prevTourStep()">← Back</button>' : '<div></div>'}
            <button class="helper-tour-btn helper-tour-btn-skip" onclick="dismissTour()">Exit Tour</button>
            ${!isLast ? '<button class="helper-tour-btn helper-tour-btn-next" onclick="nextTourStep()">Next →</button>'
                       : '<button class="helper-tour-btn helper-tour-btn-next" onclick="dismissTour()">Done ✓</button>'}
        </div>
    `;
    document.body.appendChild(popover);
    _helperPopover = popover;

    // Position near target with smooth animation
    if (target) {
        requestAnimationFrame(() => {
            setTimeout(() => positionPopover(popover, target), 100);
        });
    } else {
        // Target genuinely not on this page — center the popover
        popover.style.left = '50%';
        popover.style.top = '40%';
        popover.style.transform = 'translate(-50%, -50%)';
        requestAnimationFrame(() => popover.classList.add('visible'));
    }

    // Keep the box AND the spotlight hole attached: re-anchor on resize and
    // on any scroll while this step is up (scrollIntoView + window changes
    // used to strand the box mid-screen with the hole elsewhere).
    _tourRepositionHandler = () => {
        if (_helperPopover !== popover) return;
        _updateTourSpotlight(target && document.body.contains(target) ? target : null);
        if (target && document.body.contains(target)) {
            positionPopover(popover, target);
        }
    };
    window.addEventListener('resize', _tourRepositionHandler);
    document.addEventListener('scroll', _tourRepositionHandler, true);
}

let _tourRepositionHandler = null;

function _removeTourReposition() {
    if (_tourRepositionHandler) {
        window.removeEventListener('resize', _tourRepositionHandler);
        document.removeEventListener('scroll', _tourRepositionHandler, true);
        _tourRepositionHandler = null;
    }
}

// Geometry of the four scrim panels: everything EXCEPT the target's padded
// rect is dimmed; the rect itself is a genuine hole (no covering element),
// so no stacking context can keep the target dimmed. No target → one panel
// covers the whole viewport.
function _updateTourSpotlight(target) {
    if (!_tourOverlay) return;
    const panels = _tourOverlay.children;
    if (panels.length < 4) return;
    const W = window.innerWidth, H = window.innerHeight, PAD = 8;
    let top = 0, bottom = 0, left = 0, right = 0, x1 = 0, x2 = 0;
    if (target) {
        const r = target.getBoundingClientRect();
        top = Math.max(0, r.top - PAD);
        bottom = Math.min(H, r.bottom + PAD);
        x1 = Math.max(0, r.left - PAD);
        x2 = Math.min(W, r.right + PAD);
        left = x1;
        right = W - x2;
    } else {
        top = H;        // "top" panel covers everything…
        bottom = H;     // …and the other three collapse to zero
        x1 = 0; x2 = 0; left = 0; right = 0;
    }
    const set = (el, t, l, w, h) => {
        el.style.top = t + 'px'; el.style.left = l + 'px';
        el.style.width = Math.max(0, w) + 'px'; el.style.height = Math.max(0, h) + 'px';
    };
    set(panels[0], 0, 0, W, top);                       // above
    set(panels[1], bottom, 0, W, H - bottom);           // below
    set(panels[2], top, 0, left, bottom - top);         // left of hole
    set(panels[3], top, x2, right, bottom - top);       // right of hole
}

function nextTourStep() {
    const tour = HELPER_TOURS[HelperState.tourId];
    if (!tour) return;
    if (HelperState.tourStep < tour.steps.length - 1) {
        HelperState.tourStep++;
        showTourStep();
    } else {
        dismissTour();
    }
}

function prevTourStep() {
    if (HelperState.tourStep > 0) {
        HelperState.tourStep--;
        showTourStep();
    }
}

function dismissTour() {
    HelperState.tourId = null;
    HelperState.tourStep = 0;
    removeTourOverlay();
    dismissHelperPopover();
    if (HelperState.mode === 'tour') {
        HelperState.mode = null;
        const floatBtn = document.getElementById('helper-float-btn');
        if (floatBtn) floatBtn.classList.remove('active');
    }
}

function removeTourOverlay() {
    _removeTourReposition();
    if (_tourOverlay) {
        _tourOverlay.remove();
        _tourOverlay = null;
    }
    // Clean up ALL tour targets (not just the tracked one — page nav can lose reference)
    document.querySelectorAll('.helper-tour-target').forEach(el => el.classList.remove('helper-tour-target'));
    document.querySelectorAll('.helper-highlight').forEach(el => el.classList.remove('helper-highlight'));
    _helperHighlighted = null;
}

// ═══════════════════════════════════════════════════════════════════════════
// CLICK INTERCEPTION (Element Info mode)
// ═══════════════════════════════════════════════════════════════════════════

document.addEventListener('click', function(e) {
    if (!helperModeActive) return;

    // Allow clicking helper UI elements
    const floatBtn = document.getElementById('helper-float-btn');
    if (floatBtn && (e.target === floatBtn || floatBtn.contains(e.target))) return;
    if (_helperPopover && _helperPopover.contains(e.target)) return;
    if (_helperMenu && _helperMenu.contains(e.target)) return;

    e.preventDefault();
    e.stopPropagation();

    // Walk up the DOM tree to find a matching element
    let target = e.target;
    while (target && target !== document.body) {
        for (const selector of Object.keys(HELPER_CONTENT)) {
            try {
                if (target.matches(selector)) {
                    showHelperPopover(target, HELPER_CONTENT[selector]);
                    return;
                }
            } catch (err) { /* invalid selector */ }
        }
        target = target.parentElement;
    }

    dismissHelperPopover();
}, true);

// ── Keyboard Navigation ──────────────────────────────────────────────────

document.addEventListener('keydown', function(e) {
    if (e.key === 'Escape') {
        if (_helperPopover) { dismissHelperPopover(); return; }
        if (HelperState.tourId) { dismissTour(); return; }
        if (HelperState.mode) { exitHelperMode(); return; }
        if (HelperState.menuOpen) { closeHelperMenu(); return; }
    }
    // Arrow keys for tour navigation
    if (HelperState.tourId) {
        if (e.key === 'ArrowRight' || e.key === 'ArrowDown') { e.preventDefault(); nextTourStep(); }
        if (e.key === 'ArrowLeft' || e.key === 'ArrowUp') { e.preventDefault(); prevTourStep(); }
    }
    // ? opens helper menu (when not typing in an input)
    if (e.key === '?' && !e.ctrlKey && !e.metaKey) {
        const tag = document.activeElement?.tagName;
        if (tag === 'INPUT' || tag === 'TEXTAREA' || tag === 'SELECT') return;
        if (document.activeElement?.isContentEditable) return;
        e.preventDefault();
        toggleHelperMode();
    }
    // Ctrl+K / Cmd+K opens helper search
    if (e.key === 'k' && (e.ctrlKey || e.metaKey)) {
        e.preventDefault();
        if (HelperState.mode === 'search') { exitHelperMode(); return; }
        if (HelperState.mode) exitHelperMode();
        activateHelperMode('search');
    }
});

// ═══════════════════════════════════════════════════════════════════════════
// POPOVER DISPLAY
// ═══════════════════════════════════════════════════════════════════════════

function showHelperPopover(targetEl, content) {
    dismissHelperPopover();

    targetEl.classList.add('helper-highlight');
    _helperHighlighted = targetEl;

    const popover = document.createElement('div');
    popover.className = 'helper-popover';

    let tipsHtml = '';
    if (content.tips && content.tips.length > 0) {
        tipsHtml = `<div class="helper-popover-tips">
            ${content.tips.map(t => `<div class="helper-popover-tip">${t}</div>`).join('')}
        </div>`;
    }

    let docsLink = '';
    if (content.docsId) {
        docsLink = `<div class="helper-popover-docs">
            <a href="#" onclick="event.preventDefault();_navigateToDocsSection('${content.docsId}')">
                View full documentation &rarr;
            </a>
        </div>`;
    }

    let actionsHtml = '';
    if (content.actions && content.actions.length) {
        actionsHtml = `<div class="helper-popover-actions">
            ${content.actions.map(a => `<button class="helper-action-btn">${a.label}</button>`).join('')}
        </div>`;
    }

    popover.innerHTML = `
        <div class="helper-popover-arrow"></div>
        <div class="helper-popover-header">
            <div class="helper-popover-title">${content.title}</div>
            <button class="helper-popover-close" onclick="dismissHelperPopover()">&times;</button>
        </div>
        <div class="helper-popover-desc">${content.description}</div>
        ${tipsHtml}
        ${actionsHtml}
        ${docsLink}
    `;

    // Bind action click handlers
    if (content.actions && content.actions.length) {
        popover.querySelectorAll('.helper-action-btn').forEach((btn, i) => {
            btn.addEventListener('click', () => {
                exitHelperMode();
                content.actions[i].onClick();
            });
        });
    }

    document.body.appendChild(popover);
    _helperPopover = popover;
    requestAnimationFrame(() => positionPopover(popover, targetEl));
}

function positionPopover(popover, targetEl) {
    const rect = targetEl.getBoundingClientRect();
    const popRect = popover.getBoundingClientRect();
    const margin = 14;
    const arrowEl = popover.querySelector('.helper-popover-arrow');

    let left = rect.right + margin;
    let top = rect.top + (rect.height / 2) - (popRect.height / 2);
    let arrowSide = 'left';

    if (left + popRect.width > window.innerWidth - 20) {
        left = rect.left - popRect.width - margin;
        arrowSide = 'right';
    }
    if (left < 20) {
        left = rect.left + (rect.width / 2) - (popRect.width / 2);
        top = rect.bottom + margin;
        arrowSide = 'top';
    }

    left = Math.max(12, Math.min(left, window.innerWidth - popRect.width - 12));
    top = Math.max(12, Math.min(top, window.innerHeight - popRect.height - 12));

    popover.style.left = left + 'px';
    popover.style.top = top + 'px';

    if (arrowEl) arrowEl.className = 'helper-popover-arrow arrow-' + arrowSide;

    popover.classList.add('visible');
}

function dismissHelperPopover() {
    if (_helperPopover) {
        _helperPopover.remove();
        _helperPopover = null;
    }
    if (_helperHighlighted) {
        _helperHighlighted.classList.remove('helper-highlight');
        _helperHighlighted = null;
    }
}

// ═══════════════════════════════════════════════════════════════════════════
// SETUP PROGRESS TRACKER (Phase 2)
// ═══════════════════════════════════════════════════════════════════════════

const SETUP_STEPS = [
    { id: 'metadata-source', label: 'Connect Metadata Source',      desc: 'Spotify, iTunes, or Deezer for album/artist info',   icon: '🎵', page: 'settings' },
    { id: 'media-server',    label: 'Connect Media Server',         desc: 'Plex, Jellyfin, or Navidrome',                       icon: '🖥️', page: 'settings' },
    { id: 'download-source', label: 'Set Up Download Source',       desc: 'Soulseek, YouTube, Tidal, Qobuz, HiFi, or Deezer',  icon: '⬇️', page: 'settings', settingsTab: 'downloads' },
    { id: 'download-paths',  label: 'Configure Download Paths',     desc: 'Where music is saved and organized',                 icon: '📁', page: 'settings', settingsTab: 'downloads' },
    { id: 'first-scan',      label: 'Run First Library Scan',       desc: 'Import your existing collection from media server',  icon: '🔍', page: 'dashboard', selector: '#db-updater-card' },
    { id: 'first-download',  label: 'Download Your First Track',    desc: 'Search for and download something',                  icon: '🎶', page: 'search' },
    { id: 'watchlist',       label: 'Add an Artist to Watchlist',   desc: 'Monitor for new releases automatically',             icon: '👁️', page: 'library' },
    { id: 'automation',      label: 'Create an Automation',         desc: 'Schedule tasks and build workflows',                 icon: '🤖', page: 'automations' },
];

function _getSetupCompletion() {
    return JSON.parse(localStorage.getItem('soulsync_setup') || '{}');
}

function _markSetupComplete(stepId) {
    const stored = _getSetupCompletion();
    stored[stepId] = Date.now();
    localStorage.setItem('soulsync_setup', JSON.stringify(stored));
}

async function _checkSetupStatus() {
    const completion = _getSetupCompletion();
    const results = { ...completion };

    // ── /status — checks metadata_source, media_server, soulseek ────────
    try {
        const resp = await fetch('/status');
        if (resp.ok) {
            const data = await resp.json();
            // Metadata source is available when status reports a source.
            if (data.metadata_source?.source) {
                results['metadata-source'] = results['metadata-source'] || Date.now();
                _markSetupComplete('metadata-source');
            }
            // Media server: single object, not per-server keys
            if (data.media_server?.connected) {
                results['media-server'] = results['media-server'] || Date.now();
                _markSetupComplete('media-server');
            }
            // Download source
            if (data.soulseek?.connected) {
                results['download-source'] = results['download-source'] || Date.now();
                _markSetupComplete('download-source');
            }
        }
    } catch (e) { /* API unavailable — use cached */ }

    // ── /api/settings — checks download paths (nested under soulseek.*) ─
    try {
        const resp = await fetch('/api/settings');
        if (resp.ok) {
            const cfg = await resp.json();
            if (cfg.soulseek?.download_path || cfg.soulseek?.transfer_path) {
                results['download-paths'] = results['download-paths'] || Date.now();
                _markSetupComplete('download-paths');
            }
        }
    } catch (e) { /* skip */ }

    // ── /api/library/artists — checks if library has been scanned ────────
    if (!results['first-scan']) {
        try {
            const resp = await fetch('/api/library/artists?page=1&limit=1');
            if (resp.ok) {
                const data = await resp.json();
                if (data.total_count > 0 || (data.artists && data.artists.length > 0)) {
                    results['first-scan'] = Date.now();
                    _markSetupComplete('first-scan');
                }
            }
        } catch (e) { /* skip */ }
    }

    // ── /api/watchlist/count — checks if any artist is watched ───────────
    if (!results['watchlist']) {
        try {
            const resp = await fetch('/api/watchlist/count');
            if (resp.ok) {
                const data = await resp.json();
                if (data.count > 0) {
                    results['watchlist'] = Date.now();
                    _markSetupComplete('watchlist');
                }
            }
        } catch (e) { /* skip */ }
    }

    // ── /api/automations — checks if any custom automations exist ────────
    if (!results['automation']) {
        try {
            const resp = await fetch('/api/automations');
            if (resp.ok) {
                const autos = await resp.json();
                // Filter to custom (non-system) automations
                const custom = Array.isArray(autos) ? autos.filter(a => !a.is_system) : [];
                if (custom.length > 0) {
                    results['automation'] = Date.now();
                    _markSetupComplete('automation');
                }
            }
        } catch (e) { /* skip */ }
    }

    // ── first-download: check dashboard stat card or finished queue ────────
    if (!results['first-download']) {
        // Dashboard stat card shows "X Completed this session"
        const finishedCard = document.querySelector('#finished-downloads-card .stat-card-value');
        const finishedVal = finishedCard ? parseInt(finishedCard.textContent) : 0;
        if (finishedVal > 0) {
            results['first-download'] = Date.now();
            _markSetupComplete('first-download');
        }
        // (The legacy #finished-queue side-panel was retired; the dashboard stat card
        // above is now the single source of truth for the first-download milestone.)
    }

    return results;
}

async function openSetupPanel() {
    closeSetupPanel();

    // Show loading state immediately
    const loader = document.createElement('div');
    loader.className = 'helper-setup-panel visible';
    loader.innerHTML = `
        <div class="helper-setup-header">
            <div class="helper-setup-title-row">
                <h3 class="helper-setup-title">Setup Progress</h3>
                <button class="helper-popover-close" onclick="exitHelperMode()">&times;</button>
            </div>
        </div>
        <div class="helper-setup-loading">
            <div class="loading-spinner"></div>
            <span>Checking your setup...</span>
        </div>
    `;
    document.body.appendChild(loader);
    _setupPanel = loader;

    const status = await _checkSetupStatus();

    // Replace loader with real panel
    if (_setupPanel) _setupPanel.remove();
    const completedCount = SETUP_STEPS.filter(s => status[s.id]).length;
    const totalCount = SETUP_STEPS.length;
    const pct = Math.round((completedCount / totalCount) * 100);

    const panel = document.createElement('div');
    panel.className = 'helper-setup-panel';
    panel.innerHTML = `
        <div class="helper-setup-header">
            <div class="helper-setup-title-row">
                <h3 class="helper-setup-title">Setup Progress</h3>
                <button class="helper-popover-close" onclick="exitHelperMode()">&times;</button>
            </div>
            <div class="helper-setup-ring-row">
                <div class="helper-setup-ring">
                    <svg viewBox="0 0 36 36" class="helper-setup-ring-svg">
                        <path d="M18 2.0845 a 15.9155 15.9155 0 0 1 0 31.831 a 15.9155 15.9155 0 0 1 0 -31.831"
                              fill="none" stroke="rgba(255,255,255,0.06)" stroke-width="3"/>
                        <path d="M18 2.0845 a 15.9155 15.9155 0 0 1 0 31.831 a 15.9155 15.9155 0 0 1 0 -31.831"
                              fill="none" stroke="rgb(var(--accent-rgb))" stroke-width="3"
                              stroke-dasharray="${pct}, 100" stroke-linecap="round"
                              class="helper-setup-ring-progress"/>
                    </svg>
                    <span class="helper-setup-ring-text">${pct}%</span>
                </div>
                <div class="helper-setup-summary">
                    <span class="helper-setup-count">${completedCount} of ${totalCount}</span>
                    <span class="helper-setup-label">steps complete</span>
                </div>
            </div>
        </div>
        <div class="helper-setup-list">
            ${SETUP_STEPS.map(step => {
                const done = !!status[step.id];
                return `
                    <div class="helper-setup-item ${done ? 'done' : ''}" data-step="${step.id}">
                        <div class="helper-setup-check">${done ? '✓' : step.icon}</div>
                        <div class="helper-setup-body">
                            <div class="helper-setup-item-label">${step.label}</div>
                            <div class="helper-setup-item-desc">${step.desc}</div>
                        </div>
                        ${!done ? `<button class="helper-setup-go" onclick="setupGoTo('${step.id}')">Start →</button>` : ''}
                    </div>`;
            }).join('')}
        </div>
        ${pct === 100 ? '<div class="helper-setup-done">All set! Commissary is fully configured. 🎉</div>' : ''}
    `;

    document.body.appendChild(panel);
    _setupPanel = panel;
    requestAnimationFrame(() => panel.classList.add('visible'));
}

function setupGoTo(stepId) {
    const step = SETUP_STEPS.find(s => s.id === stepId);
    if (!step) return;
    exitHelperMode();
    navigateToPage(step.page);
    if (step.settingsTab) {
        setTimeout(() => typeof switchSettingsTab === 'function' && switchSettingsTab(step.settingsTab), 400);
    }
    if (step.selector) {
        setTimeout(() => {
            const el = document.querySelector(step.selector);
            if (el) el.scrollIntoView({ behavior: 'smooth', block: 'center' });
        }, 500);
    }
}

function closeSetupPanel() {
    if (_setupPanel) { _setupPanel.remove(); _setupPanel = null; }
}

// ═══════════════════════════════════════════════════════════════════════════
// KEYBOARD SHORTCUT OVERLAY (Phase 4)
// ═══════════════════════════════════════════════════════════════════════════

const KEYBOARD_SHORTCUTS = [
    // Global
    { key: '?',     desc: 'Open helper menu',             scope: 'Global' },
    { key: 'Ctrl+K', desc: 'Search help topics',          scope: 'Global' },
    { key: 'Esc',   desc: 'Close modal / Exit helper',    scope: 'Global' },

    // Player
    { key: 'Space', desc: 'Play / Pause',                 scope: 'Player' },
    { key: '←',     desc: 'Skip back 5 seconds',          scope: 'Player' },
    { key: '→',     desc: 'Skip forward 5 seconds',       scope: 'Player' },
    { key: '↑',     desc: 'Volume up 5%',                 scope: 'Player' },
    { key: '↓',     desc: 'Volume down 5%',               scope: 'Player' },
    { key: 'M',     desc: 'Mute / Unmute',                scope: 'Player' },

    // Helper
    { key: '←/→',   desc: 'Navigate tour steps',          scope: 'Helper Tours' },

    // Forms
    { key: 'Enter', desc: 'Submit / Confirm / Search',    scope: 'Forms & Search' },
    { key: 'Esc',   desc: 'Cancel edit / Close search',   scope: 'Forms & Search' },
];

let _shortcutsCloseHandler = null;

function openShortcutsOverlay() {
    closeShortcutsOverlay();

    // Group by scope
    const groups = {};
    KEYBOARD_SHORTCUTS.forEach(s => {
        if (!groups[s.scope]) groups[s.scope] = [];
        groups[s.scope].push(s);
    });

    const overlay = document.createElement('div');
    overlay.className = 'helper-shortcuts-overlay';
    overlay.innerHTML = `
        <div class="helper-shortcuts-panel">
            <div class="helper-shortcuts-header">
                <h3>Keyboard Shortcuts</h3>
                <span class="helper-shortcuts-hint">Press any key to dismiss</span>
            </div>
            <div class="helper-shortcuts-grid">
                ${Object.entries(groups).map(([scope, shortcuts]) => `
                    <div class="helper-shortcuts-group">
                        <div class="helper-shortcuts-scope">${scope}</div>
                        ${shortcuts.map(s => `
                            <div class="helper-shortcut-row">
                                <kbd class="helper-kbd">${s.key}</kbd>
                                <span class="helper-shortcut-desc">${s.desc}</span>
                            </div>
                        `).join('')}
                    </div>
                `).join('')}
            </div>
        </div>
    `;

    overlay.addEventListener('click', (e) => {
        if (e.target === overlay) exitHelperMode();
    });
    document.body.appendChild(overlay);
    _shortcutsOverlay = overlay;
    requestAnimationFrame(() => overlay.classList.add('visible'));

    // Dismiss on any keypress (except the initial ?)
    _shortcutsCloseHandler = (e) => {
        if (e.key === '?') return; // ignore the key that opened us
        exitHelperMode();
    };
    setTimeout(() => document.addEventListener('keydown', _shortcutsCloseHandler), 200);
}

function closeShortcutsOverlay() {
    if (_shortcutsCloseHandler) {
        document.removeEventListener('keydown', _shortcutsCloseHandler);
        _shortcutsCloseHandler = null;
    }
    if (_shortcutsOverlay) { _shortcutsOverlay.remove(); _shortcutsOverlay = null; }
}

// ═══════════════════════════════════════════════════════════════════════════
// SEARCH WITHIN HELPER (Phase 5)
// ═══════════════════════════════════════════════════════════════════════════

function openHelperSearch() {
    closeHelperSearch();

    const panel = document.createElement('div');
    panel.className = 'helper-search-panel';
    panel.innerHTML = `
        <div class="helper-search-header">
            <div class="helper-search-input-wrap">
                <span class="helper-search-icon">🔍</span>
                <input type="text" class="helper-search-input" placeholder="Search help topics..." autofocus>
            </div>
            <button class="helper-popover-close" onclick="exitHelperMode()">&times;</button>
        </div>
        <div class="helper-search-results">
            <div class="helper-search-hint">Type to search 200+ help topics, tours, and shortcuts...</div>
        </div>
    `;

    document.body.appendChild(panel);
    _helperSearchPanel = panel;

    const input = panel.querySelector('.helper-search-input');
    const resultsContainer = panel.querySelector('.helper-search-results');

    input.addEventListener('input', () => {
        const q = input.value.trim().toLowerCase();
        if (q.length < 2) {
            resultsContainer.innerHTML = '<div class="helper-search-hint">Type to search 200+ help topics, tours, and shortcuts...</div>';
            return;
        }

        const matches = [];

        // Search HELPER_CONTENT
        for (const [selector, content] of Object.entries(HELPER_CONTENT)) {
            const haystack = (content.title + ' ' + content.description + ' ' + (content.tips || []).join(' ')).toLowerCase();
            const idx = haystack.indexOf(q);
            if (idx !== -1) {
                matches.push({ type: 'content', selector, title: content.title, desc: content.description, score: idx });
            }
        }

        // Search HELPER_TOURS
        for (const [id, tour] of Object.entries(HELPER_TOURS)) {
            const haystack = (tour.title + ' ' + tour.description).toLowerCase();
            const idx = haystack.indexOf(q);
            if (idx !== -1) {
                matches.push({ type: 'tour', tourId: id, title: tour.icon + ' ' + tour.title, desc: tour.description + ` (${tour.steps.length} steps)`, score: idx });
            }
        }

        // Search KEYBOARD_SHORTCUTS
        for (const shortcut of KEYBOARD_SHORTCUTS) {
            const haystack = (shortcut.key + ' ' + shortcut.desc + ' ' + shortcut.scope).toLowerCase();
            const idx = haystack.indexOf(q);
            if (idx !== -1) {
                matches.push({ type: 'shortcut', title: shortcut.key + ' — ' + shortcut.desc, desc: 'Scope: ' + shortcut.scope, score: idx + 100 });
            }
        }

        // Sort: title matches first, then by position
        matches.sort((a, b) => a.score - b.score);

        if (matches.length === 0) {
            resultsContainer.innerHTML = '<div class="helper-search-hint">No results found for "' + q.replace(/</g, '&lt;') + '"</div>';
            return;
        }

        resultsContainer.innerHTML = matches.slice(0, 20).map((m, i) => {
            const typeIcon = m.type === 'tour' ? '🚶' : m.type === 'shortcut' ? '⌨️' : '🎯';
            const typeLabel = m.type === 'tour' ? 'Tour' : m.type === 'shortcut' ? 'Shortcut' : 'Help';
            return `
                <button class="helper-search-result" data-idx="${i}">
                    <span class="helper-search-result-type" title="${typeLabel}">${typeIcon}</span>
                    <div class="helper-search-result-body">
                        <div class="helper-search-result-title">${_highlightMatch(m.title, q)}</div>
                        <div class="helper-search-result-desc">${m.desc.slice(0, 120)}${m.desc.length > 120 ? '...' : ''}</div>
                    </div>
                </button>`;
        }).join('');

        // Bind click handlers
        const displayedMatches = matches.slice(0, 20);
        resultsContainer.querySelectorAll('.helper-search-result').forEach((btn, i) => {
            btn.addEventListener('click', () => _handleSearchResultClick(displayedMatches[i]));
        });
    });

    // Position near float button
    const floatBtn = document.getElementById('helper-float-btn');
    if (floatBtn) {
        const btnRect = floatBtn.getBoundingClientRect();
        panel.style.right = (window.innerWidth - btnRect.right) + 'px';
        panel.style.bottom = (window.innerHeight - btnRect.top + 8) + 'px';
    }

    requestAnimationFrame(() => {
        panel.classList.add('visible');
        input.focus();
    });
}

function _highlightMatch(text, query) {
    const idx = text.toLowerCase().indexOf(query.toLowerCase());
    if (idx === -1) return text;
    return text.slice(0, idx) + '<mark>' + text.slice(idx, idx + query.length) + '</mark>' + text.slice(idx + query.length);
}

function _handleSearchResultClick(match) {
    if (match.type === 'tour') {
        exitHelperMode();
        setTimeout(() => {
            HelperState.mode = 'tour';
            const floatBtn = document.getElementById('helper-float-btn');
            if (floatBtn) floatBtn.classList.add('active');
            startTour(match.tourId);
        }, 100);
    } else if (match.type === 'content') {
        exitHelperMode();

        // Try to find the element on the current page first
        let el = document.querySelector(match.selector);
        if (el && el.offsetParent !== null) {
            el.scrollIntoView({ behavior: 'smooth', block: 'center' });
            setTimeout(() => showHelperPopover(el, HELPER_CONTENT[match.selector]), 300);
            return;
        }

        // Element not visible — try to detect which page it's on from the selector
        const pageHint = _guessPageFromSelector(match.selector);
        if (pageHint) {
            navigateToPage(pageHint);
            setTimeout(() => {
                const el2 = document.querySelector(match.selector);
                if (el2) {
                    el2.scrollIntoView({ behavior: 'smooth', block: 'center' });
                    setTimeout(() => showHelperPopover(el2, HELPER_CONTENT[match.selector]), 300);
                }
            }, 400);
        }
    } else if (match.type === 'shortcut') {
        exitHelperMode();
        setTimeout(() => activateHelperMode('shortcuts'), 100);
    }
}

function _guessPageFromSelector(selector) {
    // Map well-known selector prefixes/patterns to pages
    const pageHints = {
        'sync':        ['sync-tab', 'sync-header', 'sync-sidebar', 'playlist-header', 'spotify-refresh', 'tidal-refresh', 'deezer-url', 'youtube-url', 'spotify-public', 'import-file-icon', 'mirrored'],
        'downloads':   ['enh-', 'enhanced-search', 'search-mode', 'download-manager', 'toggle-download-manager'],
        'discover':    ['discover-', 'spotify-library', 'recent-releases', 'seasonal', 'release-radar', 'discovery-weekly', 'build-playlist', 'listenbrainz', 'decade-tabs', 'genre-tabs', 'daily-mixes', 'personalized-'],
        'artists':     ['artists-search', 'artists-hero', 'artist-detail', 'similar-artists'],
        'automations': ['automations-', 'auto-', 'builder-'],
        'library':     ['library-', 'alphabet-selector', 'watchlist-filter'],
        'stats':       ['stats-'],
        'import':      ['import-page-'],
        'settings':    ['settings-', 'stg-tab', 'api-service', 'server-toggle', 'save-button', 'spotify-client', 'soulseek-url', 'quality-profile'],
        'issues':      ['issues-'],
        'dashboard':   ['dashboard-', 'service-card', 'watchlist-button', 'wishlist-button', 'db-updater', 'metadata-updater', 'duplicate-cleaner', 'discovery-pool-card', 'retag-tool', 'media-scan', 'backup-manager', 'metadata-cache'],
    };

    const selectorLower = selector.toLowerCase();
    for (const [page, patterns] of Object.entries(pageHints)) {
        for (const pattern of patterns) {
            if (selectorLower.includes(pattern.toLowerCase())) {
                return page;
            }
        }
    }
    return null;
}

function closeHelperSearch() {
    if (_helperSearchPanel) { _helperSearchPanel.remove(); _helperSearchPanel = null; }
}

// ═══════════════════════════════════════════════════════════════════════════
// WHAT'S NEW (Phase 6)
// ═══════════════════════════════════════════════════════════════════════════

// Entries tagged with `unreleased: true` are accumulating under a version label
// but won't display until the build version catches up — used for in-progress
// projects that span multiple commits before shipping. Strip the flag at
// release time and add a real `date:` line at the top of the version block.
const WHATS_NEW = {
    // Convention: keep only the CURRENT release here, plus a single brief
    // "Earlier versions" summary entry. Don't accumulate old per-version blocks.
    // Versions are this fork's own (see _SOULSYNC_BASE_VERSION in web_server.py);
    // 1.0.0 was the baseline, carrying upstream's 3.1.5 feature set, and 2.0.0
    // is the rename to Commissary.
    //
    // NOTE for future editors: entries below 2.0.0 describe work that shipped
    // while the app was called SoulSync, and now read "Commissary" throughout.
    // That is deliberate — it is the same app's own history. References to
    // UPSTREAM, however, must keep saying SoulSync, or the changelog starts
    // claiming this fork wrote the thing it forked.
    '2.0.9': [
        { date: 'August 2026 · 2.0.9' },
        { title: "Set a quality profile for a whole Library, not one title at a time", desc: "quality profiles could only be assigned per TITLE. That is fine for the occasional exception and useless for the thing people actually want to say — <em>everything in my 4K Library is judged at 4K, everything in Anime is judged at 1080p</em> — which had to be repeated once per title, forever, and again for every show or film added afterwards. Each Library now carries its own default, set once in Settings → Libraries." },
        { title: "It fixes the moment that mattered most: the very first grab", desc: "a title your library has never seen carries no profile at all, so there was nothing to read — which meant a brand-new show was always judged by the global Default, no matter which Library you were explicitly sending it to. That first grab is the one that decides what actually lands on disk, so it was precisely the wrong moment to have no opinion. Pick a destination in the download window and it is now judged by that Library's profile before anything is fetched." },
        { title: "Per-title profiles still win", desc: "nothing about the existing per-title picker changes — a profile set on one show or film still overrides its Library, which is what makes it an override rather than a second setting doing the same job. The order is simply: the title's own profile, then its Library's, then the global Default." },
        { title: "And the per-title picker stopped calling inheritance 'Default'", desc: "leaving a title unassigned means it INHERITS, so a picker reading <em>Default</em> while the show was actually being judged at 4K was lying about the very setting it was showing. It now names what it will really resolve to — <em>Default — this Library uses 4K</em> — and updates the moment you move the title to a different Library." },
        { title: "Deleting a profile releases the Libraries using it", desc: "rather than leaving them pointing at something that no longer exists. Those Libraries fall back to the global Default, exactly as titles pointing at a deleted profile already did." },
    ],
    '2.0.8': [
        { date: 'August 2026 · 2.0.8' },
        { title: "The video side now says where it put a file, and why", desc: "this release adds no features and changes no behaviour. It closes two silences that made real problems impossible to investigate — both found while trying and failing to explain a show that had been filed as the wrong series." },
        { title: "Every video import now logs its destination", desc: "the video import code contained <strong>no logging at all</strong> — not one line, anywhere in the file. So nothing ever recorded where a downloaded episode or film was placed, while the music side has logged its destination on every single import for years. When a show turned up filed under the wrong name, the only way to investigate was to read the database afterwards and work backwards, and that only tells you where it ENDED, never what it decided or why." },
        { title: "And on whose authority it chose that name", desc: "the line says whether the name came from the title's own library entry (with the ids and year it used), from an existing copy already on disk, from a manual placement, or from the download request alone because the title is not in your library yet. That last case is the one that creates a folder your media server then has to guess the identity of — so it is worth being able to see it happen, rather than discovering it a week later." },
        { title: "And when a download client refuses a release, it now says what it was handed", desc: "a refused grab said only <em>the torrent client didn't accept the release</em>. In eight days of logs one title produced that message <strong>324 times</strong> — twice an hour, every hour — while the download-client code logged nothing whatsoever, so there was no way to tell whether it had been handed a magnet link, a .torrent file, or nothing at all. Those three fail for completely different reasons and need completely different fixes." },
        { title: "It also names the likely cause", desc: "a client that takes a magnet and returns nothing is most often telling you the torrent is <strong>already in it</strong> — a stalled or errored copy sitting there means every retry is rejected as a duplicate, forever, which is exactly what an hourly retry loop that never succeeds looks like. That, a malformed magnet, and a client that cannot reach the swarm are now spelled out in the log rather than left to guesswork." },
    ],
    '2.0.7': [
        { date: 'August 2026 · 2.0.7' },
        { title: "Four fixes carried over from upstream, each verified as a real fault here first", desc: "Commissary forked from SoulSync 3.1.5 and has diverged a long way, so most of what lands upstream either does not apply or was already solved differently. These four were checked against this code before being taken." },
        { title: "Fixed: a magnet was preferred over the real .torrent file, and could stall forever", desc: "when an indexer offers both, Commissary was taking the magnet. A magnet hands your download client an info-hash and nothing else — it has to find the swarm itself, and one that cannot parks on <em>downloading metadata</em> with zero size and zero peers indefinitely. Commissary can fetch the actual .torrent server-side and hand the client the file, which needs no swarm discovery at all, and that path could never be reached." },
        { title: "The magnet is still carried, which is the point", desc: "simply flipping the preference would trade one failure for another: if Commissary cannot reach your indexer but your download client can, a magnet that worked would be lost. So the magnet travels alongside the URL the whole way and is used the moment the file handoff is refused. This affected music single-track grabs and both video paths — the album flow had already been fixed." },
        { title: "Fixed: a stalled download's clock was reset by every restart", desc: "a download that stops moving is given half an hour before Commissary gives up on it — but that clock was kept in memory, so restarting wiped it and handed every stuck download a fresh half hour. The perverse result is that the longer something had been stuck, the more restarts it had survived and the <strong>less</strong> likely it was ever to be caught. Upstream saw six torrents sit at the same percentage for over three hours against a thirty-minute timeout. The clock is now stored with the download and measured in real time, so it survives restarts." },
        { title: "And two things that clock never noticed", desc: "a download that <em>finished</em> but whose file Commissary then could not find was not tracked at all — it sat at 100% forever. That is what a path-mapping problem looks like, and it now says so rather than telling you there was no progress, which would send you hunting seeders that were never the problem. Separately, a torrent re-checking itself briefly reports a LOWER percentage; that used to count as movement and renew the grace period every time it happened." },
        { title: "Fixed: Quality Check upgrades failed with \"No matched track in finding\"", desc: "the tool would find plenty to upgrade and then refuse every single one. Two causes. The scanner records no library-track link for a file it could not match, and the fix handler was gated on exactly that link — so for those findings it never even looked at the finding's own details, which carry the title and artist perfectly well. And a full database refresh renumbers every track, orphaning findings written beforehand; those were discarded too. Both closed." },
        { title: "Fixed: folders differing only in capitalisation became two albums", desc: "every destination folder is built from metadata, so when the metadata's capitalisation differs from the folder already on disk you got a second one. On Linux — which is what Docker runs — that is two real directories and the album shows up twice. On Windows or macOS the file lands in the first folder but the path recorded is not how the folder is actually spelled, so later lookups miss it. Each folder now resolves to the spelling already on disk." },
        { title: "It steers new writes; it does not merge", desc: "two folders that have already split stay split until something moves their files — running a Reorganize is what merges them, and every track will now resolve to the same surviving folder. Filenames are never case-folded: two tracks differing only in case are two different files, and folding them would overwrite one with the other." },
    ],
    '2.0.6': [
        { date: 'August 2026 · 2.0.6' },
        { title: "Fixed: every Opus download was labelled with no quality at all", desc: "in the library this was found in, <strong>507 of 521</strong> YouTube downloads had recorded no quality and no bitrate, while the handful of M4A ones beside them were labelled perfectly. One missing attribute explains all of it: the tag library Commissary uses genuinely cannot read a bitrate out of an Opus file — the field does not exist in that format's header — and the code asked for it anyway. The error was swallowed, and what came back was an empty string, which everything downstream reads as <em>unknown file</em> rather than <em>unreadable field</em>." },
        { title: "Three answers, tried in order", desc: "the tag header when there is one; then the <strong>source's own claim</strong> from before the download — the format YouTube said it was sending — but only while the file on disk is still that format; and finally the honest arithmetic of size divided by playing time. Carried over from upstream, where it was part of the YouTube quality work." },
        { title: "The claim is never copied onto a re-encoded file", desc: "if you have Commissary convert downloads to MP3, the original stream's bitrate says nothing about the file that was actually written. Copying it across would have replaced a blank label with a confident wrong one, which is worse." },
        { title: "And an Opus bitrate is now shown as the average it is", desc: "Opus has no single bitrate — it varies through the track — so a figure like <em>137 kbps</em> was always an average being presented as a precise number. Library rows that stored nothing for Opus now get that average filled in, and the formats whose bitrate is inherently an average are marked as such rather than quoted like a constant." },
        { title: "One implementation instead of two", desc: "this logic existed twice, once in the import path and once inside the web server, and the copies had already drifted apart: both were wrong about Opus in the same way, but only one of them knew about leftover download containers or an .ogg file that actually holds Opus. There is one now." },
        { title: "What it does not do", desc: "nothing here changes what gets downloaded or its quality — it changes what Commissary knows and reports about it. Files already in your library keep their bitrate; the estimate fills in rows that had nothing rather than overwriting anything." },
    ],
    '2.0.5': [
        { date: 'August 2026 · 2.0.5' },
        { title: "Three fixes carried over from upstream SoulSync 3.2.1 and 3.2.2", desc: "Commissary forked from SoulSync 3.1.5 and has diverged a long way since, so most of what lands upstream either does not apply here or was already solved differently. These three were checked against this code first and found to be real, present faults." },
        { title: "Fixed: a library scan could slowly corrupt where your files are recorded as living", desc: "for Navidrome and other Subsonic servers. When the API leaves the file path out of a response — which it does transiently, during its own library rescan or a network blip — Commissary invented a bare filename from the track title, something like <code>My Song.flac</code> with no folder in front of it. That name matches nothing on disk, and the next scan wrote it straight over the correct value. Each pass damaged another slice of the library and it accumulated. Nothing is invented now, and the update keeps what it already has when a scan says nothing — the same protection the columns beside it have always had. Plex libraries were never exposed to this." },
        { title: "Fixed: punctuation stuck to a search word buried the track you were looking for", desc: "searching for <em>Would've, Could've, Should've</em> returned other songs called <em>Should've</em> and put the real one third. The commas were the culprit, though not where you would guess: the broad fallback search — the one that runs when the exact search finds nothing, which is precisely when your file is tagged a little differently from the source — split on spaces only, so it asked for <code>would've,</code> <em>with the comma</em>. A file tagged without them matched on the last word alone and scored the same as anything else sharing that one word. Punctuation is now trimmed from the ends of each word, and only the ends: N.W.A, P!nk and AC/DC survive intact. The same applied to artist names, which is not exotic either — Crosby, Stills & Nash." },
        { title: "Fixed: \"replace the original file\" did nothing if you imported the file yourself", desc: "re-identify a track with that box ticked and it staged the replacement correctly, but the instruction was only ever read by the automatic importer. Import the staged file by hand from the Import page and nothing looked for it, so the old file and its library entry stayed exactly where they were. It is honoured on both paths now." },
        { title: "And it refuses rather than guesses", desc: "the replacement only happens after the import has actually succeeded — a file that failed its checks and went to quarantine never causes the original to be deleted. If Commissary cannot work out where the new file landed, it keeps the original and says so, because a duplicate you can delete and a missing file you cannot. Re-identifying a track onto the release it was already in deletes nothing at all." },
    ],
    '2.0.4': [
        { date: 'August 2026 · 2.0.4' },
        { title: "Fixed: a short track title made its own finished download invisible", desc: "reported against Kanaria's <em>Dec.</em>, which downloaded perfectly and then never finished — no import, no library entry, the FLAC just sitting in the download folder. Commissary matches a completed file by comparing what it asked for against what it finds on disk, and it was comparing a bare title against a filename that still had <code>.flac</code> on the end. The extension was the only difference, and it was enough: 0.839 against a 0.85 threshold, so the file sitting right there was reported missing." },
        { title: "Which is why it only ever happened to SHORT titles", desc: "the extension is a fixed handful of characters, so how much damage it does depends entirely on how much name there is to dilute it. <em>All Time Low - Monsters (feat. Demi Lovato & blackbear)</em> shrugged it off; <em>Kanaria - Dec.</em> could not. Anything under about fifteen characters could never be found, which is why this looked random rather than systematic. The extension now comes off both sides before they are compared." },
        { title: "Fixed: upgrading a track's format added a second copy instead of replacing the old one", desc: "every replace and upgrade decision hung on whether a file already existed at the exact path the new one was headed for — extension included. But an upgrade is precisely a change of format, so that test was false every single time: replacing a 130 kbps <code>.opus</code> with a FLAC works out <code>Track.flac</code>, does not find <code>Track.opus</code>, and files the new one neatly beside the old. Both then sit in your library and your media server lists the track twice." },
        { title: "The library that reported it had nine such pairs", desc: "two of them show it with nothing else in the way — <code>01 - STAY.opus</code> next to <code>01 - STAY.flac</code>, and the same for <code>01 - Courage</code>. Identical names; the extension alone kept them apart. Commissary now recognises the copy you already own whatever container it is in, and replaces it." },
        { title: "It looks in one folder, and that is deliberate", desc: "only the folder the incoming file is going into — never a search of your library. A track downloaded as a <strong>single</strong> is filed in its own album folder, so it can never reach into an album's folder and overwrite that album's copy. Downloading the single of a track you own as part of an album leaves the album alone, by construction rather than by rule." },
        { title: "And it would rather leave a duplicate than delete the wrong thing", desc: "a leading track number in the existing name is allowed for (a library organised as <code>01 - Title</code> while the current template writes a bare title), but only when the number is provably this track's — otherwise importing track 5 of an album whose track 1 is called <em>Intro</em> could delete track 1. If two files in the folder could both be the track, neither is touched. A duplicate you can delete; a file you cannot get back." },
        { title: "One setting decides whether you get the upgrade", desc: "with <strong>replace lower quality</strong> switched off, Commissary treats a track you already own as a reason to discard the new download. That is unchanged, and it now applies only when the new file was headed for exactly the same path — a better copy in a different format is placed alongside instead, exactly as before this release. So this can upgrade your library or leave it as it was, and never delete something it used to keep. If you want format upgrades to replace, that switch is what asks for them." },
        { title: "Existing duplicates are left where they are", desc: "this stops new ones appearing; it does not go through your library tidying up the pairs already there. Those are yours to remove — Commissary will not delete files it did not just replace." },
    ],
    '2.0.3': [
        { date: 'August 2026 · 2.0.3' },
        { title: "Fixed: a new episode was filed into a second folder, and your server read it as a different show", desc: "reported as <em>Kitchen Nightmares (US) keeps downloading new episodes as Kitchen Nightmares: Road to Super Bowl LIX</em>. The download itself was right every week; what went wrong was where it was put. Commissary worked the destination folder out from the <strong>request</strong> that started the download, while everything else — the rename tool, the naming-conformance repair, the library scan — works it out from the show's own library row. For an airing show those two disagree, so the episode landed in a folder <em>beside</em> the show instead of inside it, and Plex or Jellyfin scanned that as a brand-new series whose identity it then had to guess." },
        { title: "Three facts, all wrong, all in the same place", desc: "the <strong>year</strong> came from the episode's air date rather than the year the series began, so <code>Futurama (1999)</code> was written as <code>Futurama (2026)</code>. The <strong>TMDB id</strong> was never filled in for an episode at all, so a template asking for it wrote an empty <code>(tmdb-)</code> and left the server nothing to match on. And the <strong>TVDB id</strong> was filled with whichever id the download happened to carry — a TMDB id, or an internal row number, never a TVDB one — so the folder asserted an id that was flatly false. That is worse than a missing one, because the server believes it." },
        { title: "It was not one show", desc: "in the library this was diagnosed against, <strong>36 of the last 37</strong> episode downloads had written to a folder the library did not use. The single exception was a show that lived in the malformed folder itself, having never been acquired any other way. The damage already on disk included a second <em>Its Always Sunny in Philadelphia</em> with no ids holding two episodes, three separate folders for one anime, and a Futurama episode filed under an unrelated series." },
        { title: "Why the rename-before-import step added in 2.0.2 could never win", desc: "that step exists precisely so a show cannot end up split across two namings, and it was working exactly as designed. It corrected the show's existing files to the library's name — and then the import created its own differently-named folder alongside them. Every week, for every airing show. The two halves are now computed from the same facts, so they agree." },
        { title: "A brand-new show now arrives identifiable", desc: "a show you do not own yet has no library row to read, and that is the very download which <strong>creates</strong> the folder your server will identify the show by forever. It now writes the TMDB id it already had from the request, instead of an empty <code>(tmdb-)</code> for the server to guess at. Placing a file by hand gets the same treatment: the Place dialog has no field for a TVDB or IMDb id, so it reads them from the library too." },
        { title: "Also: $tmdbid and $imdbid now work in episode templates", desc: "they had only ever worked in the <code>{TmdbId}</code> brace form. The same scheme written with <code>$tokens</code> silently dropped them, which is a quiet way to lose the one part of a folder name your server matches on." },
        { title: "What this does not do", desc: "folders that are <strong>already</strong> split are left where they are. This stops new ones appearing; it does not merge what has already happened, because rearranging a library you have curated is not something an upgrade should do behind your back. To bring an existing show onto one naming, use <strong>Manage → Rename</strong> on that show, then remove any phantom series your media server invented." },
    ],
    '2.0.2': [
        { date: 'August 2026 · 2.0.2' },
        { title: 'New: lock a show, a season or a movie so automatic downloads cannot touch it', desc: 'a <strong>Lock automatic edits</strong> switch, on the Manage panel for a show or movie and on each season of a show. While it is on, nothing unattended may write to that title — not a replacement, not an upgrade, not a brand-new episode. A download that targets it still runs and still tells you what it found; it simply stops at the last step and reports why, instead of changing anything.' },
        { title: 'What it protects you from', desc: 'when a finished download is placed, Commissary works out where it belongs from the request that started it, and it can only argue with a release whose name actually spells out S01E07. A release that names no episode at all — fansub numbering, a bare title, a mis-titled scene release — is filed wherever the request claimed. If that claim was wrong, and the release happens to score better than what you already have, the existing file is <strong>deleted and replaced</strong> with the wrong episode. That is the sequence this stops.' },
        { title: 'Season locks are narrower than show locks', desc: 'lock a whole show and every season is sealed. Lock a single season and only that one is — so a finished season can be closed off for good while the season currently airing keeps downloading normally. A season pack spanning both imports the half it is allowed to and refuses the rest.' },
        { title: 'Manual import is the way through', desc: 'the lock is aimed squarely at unattended work. Placing a file by hand still does exactly what you tell it, because that IS the check the lock is asking for: it stops the automation from deciding, not you. A refused download is left where it is with its reason attached, so you can look at it and place it yourself if it was right after all.' },
        { title: 'New: existing files are renamed to match before a new episode is imported', desc: 'naming templates only ever applied at import time, so changing one quietly split a show in two: the next episode arrived named the new way, in a folder named the new way, while every earlier season kept the name it was first imported under — and Plex or Jellyfin then sees two shows. The manual Rename tool could always fix it, but only if you remembered to run it before the next episode landed, and for an airing show that is a weekly race you will eventually lose. The rename now runs on the way in.' },
        { title: 'Scoped, quiet, and on by default', desc: 'only the title being imported into is touched — an import is not the moment to start a library-wide job. A library that already matches its template renames nothing, which is why this is safe to leave on: for most people it will never do anything at all. There is a switch in <strong>Settings → Library</strong> if you would rather it did not. If a rename cannot be done — a name already taken, a file offline — it is logged and the import goes ahead regardless, because a tidy-up should never cost you a download.' },
        { title: 'And a refused release is not blacklisted', desc: 'being pointed at locked content says nothing about the release itself — it may be perfectly good and simply aimed at the wrong title. It is left eligible, so it can still be picked for whatever it actually belongs to. Locking is admin-only, and nothing is locked by default: this changes nothing until you switch it on.' },
    ],
    '2.0.1': [
        { date: 'August 2026 · 2.0.1' },
        { title: 'Fixed: anime searches found nothing, because the tracker numbers episodes instead of using S01E07', desc: 'reported against <code>[SubsPlease] Oh Boy, Was I Wrong About Her - 07</code>, which came back <strong>✗ NO EPISODE NUMBER IN THE RELEASE NAME</strong> on both the automatic and the manual search. Fansub groups number episodes straight through — <code>- 07</code>, not <code>S01E07</code> — and Commissary already knew how to read that. What it needed was to be told <em>which</em> number to look for, and three separate things stopped that reaching it. The release was one step from being accepted the entire time, which is exactly why it looked like the name was unreadable.' },
        { title: 'A brand-new show could never be told', desc: 'the worst of the three, because it hit the grab that matters most. The wanted episode number was only worked out for shows already tagged as anime, and only by counting episodes already in your library. A show you have just started following has neither — no library row to tag, nothing to count — so the <strong>first</strong> episode of every new anime, the download that decides which Library the show lives in forever, was the one download that could never match. For a first season the answer needs no library at all: episode 7 is absolute number 7, for every show ever made.' },
        { title: 'And the Soulseek search threw the answer away', desc: 'the search request carried everything needed; the follow-up requests that actually collect the results did not. Soulseek results arrive over about a minute, in batches — so a search that <em>started</em> knowing what it was looking for judged every result it received without that knowledge. Torrent searches return in one shot and were unaffected, which is a good part of why this looked inconsistent.' },
        { title: 'Deliberately still cautious', desc: 'the episode number is only ever used to <em>accept</em> a release, never to reject one, so a wrong guess is worse than none. It is worked out for the first season of any show, and beyond that only for shows you have actually marked as anime — for an ordinary show, <code>Show - 04</code> is far more likely to be season one\'s episode 4 than the episode you asked for. Daily shows are untouched; they match on air date.' },
        { title: 'You can also read the release name now', desc: 'reported alongside the above: the name was cut off in Manual Search with no way to see the rest. Hovering did not help either — for results from a tracker the tooltip had been spent on "open this on the indexer", which the link already made obvious. Release names now get two lines instead of one, and the tooltip carries the full name. An anime release keeps its episode number in the middle of a long name, so this was hiding the one field you open a manual search to check.' },
    ],
    '2.0.0': [
        { date: 'August 2026 · 2.0.0' },
        { title: 'SoulSync is now Commissary', desc: 'the app has a name of its own. This fork branched from <strong>SoulSync 3.1.5</strong> and has been diverging for 130-odd releases — multi-user profiles with request approval, multiple libraries per content kind, the Purchased page, a security pass, and a video side that has gone its own way — while still answering to someone else\'s name and pointing at someone else\'s bug tracker. Nothing about how it works changed in this release. It is a rename, and a major version because the published image moved with it.' },
        { title: 'Nothing you have configured needs touching', desc: 'this was scoped to the <em>name</em>, not your install. The Docker volume, the compose service, the config path, the <code>SOULSYNC_*</code> environment variables and the standalone-server value stored against your library rows all keep their original names — renaming any of those would point a working install at an empty database or orphan rows nothing could read again. Upgrade normally; there is no migration.' },
        { title: 'One thing to change: the image you pull', desc: 'the image is now <code>ghcr.io/thymrman/commissary</code>. Edit the <code>image:</code> line in your <code>docker-compose.yml</code> and run <code>docker-compose pull && docker-compose up -d</code>. On Unraid the template moved to <code>commissary.xml</code> and the container\'s Repository field needs the same change. The old name is not being updated any more.' },
        { title: 'Your Navidrome player is still called SoulSync', desc: 'and that is on purpose. Navidrome creates a player entry per client name and hangs settings off it — including <strong>Report Real Path</strong>, which is what lets Commissary resolve the file behind a track it streams. Renaming the client would have quietly registered a second, unconfigured player and broken playback paths on every existing install. The setting you already enabled keeps working, and the help text still names the entry you will actually see in the list.' },
        { title: 'Bug reports come here now', desc: 'the in-app links and the Copy Debug Info footer point at <strong>this fork\'s</strong> issue tracker rather than upstream\'s. Upstream cannot reproduce changes made here, and several subsystems now behave differently on purpose — reporting a Commissary bug there wastes everyone\'s time. Upstream keeps all the credit for the foundation and none of the blame for anything since.' },
        { title: 'The README says what this actually is now', desc: 'it had been describing upstream\'s feature set, upstream\'s two Docker release channels and upstream\'s branch workflow, none of which are true here — this fork publishes one image, manually, from a single branch. It now opens with the fork relationship, what has changed since 3.1.5, and an install section that matches reality.' },
    ],
    '1.9.23': [
        { date: 'August 2026 · 1.9.23' },
        { title: 'Fixed: the first download of a new Anime show decided its home — wrongly, and forever', desc: 'diagnosed against your actual library. A show you do not own yet has no library row, so nothing could say which shelf it belonged on: the wishlist entry the airing automation creates carried no Library, resolution found nothing, and it fell back to the <strong>primary</strong> TV Library. It downloaded there, Plex scanned it into TV Shows, and the next library scan stamped that Library onto the show permanently — so every later episode resolved "correctly" to the wrong place. One wrong first grab, cemented.' },
        { title: 'You can now choose the Library when you FOLLOW a show', desc: 'the wishlist has had a Library picker for a while, but a wishlist row is created by the airing automation minutes before the grab — there was never a moment a person could set one. The watchlist is where the decision can actually be made, weeks ahead, and it was the one link in the chain with nowhere to record it. Choosing a Library also moves any episodes already queued and the show row itself, so a title can never end up split across two shelves.' },
        { title: 'Where to find it', desc: 'Watchlist → Shows. Every followed show you do not own yet carries a small Library button in the corner of its poster; it opens a short list of your TV Libraries plus <strong>Default (primary)</strong>. That is the only window in which the choice exists — a show\'s shelf is decided by its <strong>first</strong> download, and before that grab there is no show and no detail page to correct. The moment you follow something is the moment you are actually there to say where it belongs.' },
        { title: 'And it shows what it is set to', desc: 'hover the button and it names the Library the show is heading for, or tells you it will use the default. A control that always looks unset is indistinguishable from one that quietly fails to save, so the watchlist now returns the stored choice alongside everything else on the card.' },
        { title: 'Deliberately narrow', desc: 'the button appears only where the decision is still open: shows you do not already own (an owned show is settled, and its detail page moves it for real, files included), and only when you have two or more TV Libraries configured — with one there is nothing to choose. It is also admin-only, which is not a policy decision so much as an honest one: this endpoint sits behind the same gate as the other library-assignment controls, so for anyone else it would have been a button that failed on every press.' },
        { title: 'And every path that queues a download now resolves the Library itself', desc: 'nine code paths create wishlist entries and exactly <strong>one</strong> of them — the manual add — ever passed a Library. The daily airing automation, the watchlist scans, import lists, member requests, the re-queue-on-failure hook and two repair jobs all passed nothing, and nothing meant "primary". That is now resolved inside the add itself, so all nine are fixed at once and the next one cannot forget.' },
        { title: 'Libraries can declare what kind of show they hold', desc: 'a new <strong>Shows here are…</strong> setting on each TV Library (Settings → Libraries). Series type decides how a release is searched for — anime by absolute number (<code>Show 1071</code>), dailies by air date, everything else by S01E01 — and it was per-show, buried, and in practice never set: <strong>565 of the 571 shows</strong> in your Anime library had no type at all, so their releases were being hunted as standard S01E01. Setting it on the Library applies it to every show there that has no type of its own; a per-show setting always wins.' },
        { title: 'And the fallback stops being silent', desc: 'landing in the primary Library because nothing said otherwise was an invisible decision — indistinguishable in the log from landing there correctly. It now says so, by name, at the moment it happens.' },
    ],
    '1.9.22': [
        { date: 'August 2026 · 1.9.22' },
        { title: 'Fixed: a manually matched track could vanish for good', desc: 'when you link a track to a file in your library by hand, Commissary stores that as a row in a table — and then treated the row\'s <em>existence</em> as proof the file was still there. Delete or move that file and the track entered a closed trap: the download was skipped as "already matched", the wishlist drain removed it as a <strong>success</strong>, and adding it back was silently refused for the same reason. Three separate places, each individually reasonable, together making the song impossible to re-acquire. A match is now checked against the library before it counts, and a match whose id has gone stale is re-resolved by file path rather than thrown away.' },
        { title: 'Fixed: albums starting with a dot became invisible', desc: 'the name sanitiser trimmed dots from the <em>end</em> of a folder name, because that is what Windows rejects. Nothing trimmed the front, which is the Unix rule — a leading dot makes the entry hidden. So <code>...And Justice for All</code> imported perfectly into a folder your file manager, your terminal and your media server\'s scanner all skip. The same gap existed on the video side. Interior dots are untouched, so <code>Mr. Bungle</code> is still <code>Mr. Bungle</code>.' },
        { title: 'Fixed: AcoustID took the first answer, not the best one', desc: 'fingerprint lookups were read from position zero of the results, but the results came back in whatever order the API sent them while the best score was tracked in a completely separate variable. So a remix, a live cut or a compilation entry listed ahead of the real recording had its metadata written to your file. Results are now sorted best-first, and — new — a match has to clear a confidence bar of 0.80 to be used at all. Below that Commissary declines and leaves your existing tags alone, which for an ambiguous fingerprint is the right answer.' },
        { title: 'Fixed: the wishlist processing API endpoint never worked', desc: '<code>POST /api/wishlist/process</code> called its own setup helper with an argument that helper has never accepted. Every single call raised a TypeError, which the route caught and returned as a generic 500 — so it failed identically whether or not the wishlist was busy, and looked like a conflict rather than a permanent break. Nothing in the UI used it; if you script against the API, it works now.' },
    ],
    '1.9.21': [
        { date: 'August 2026 · 1.9.21' },
        { title: 'You can finally say "not this artist" from the track itself', desc: 'hover any track on the Discover page — in a mix, in Hidden Gems, in Discovery Shuffle, in a decade or genre list — and a 🚫 appears on the right. One click blocks that artist from every discovery playlist, and the track disappears from the list you are looking at. Until now the only way to block an artist was to open the blocked-artists panel and type their name in from memory, having already left the song that prompted it.' },
        { title: 'Why it was missing', desc: 'it wasn\'t, quite. The block action, its API, its database table and even its <em>styling</em> — a red circle that fades in on row hover — had all shipped. The one thing nobody ever added was the button itself, so the function sat in the code with no way to call it. Every layer was individually complete and the feature was unreachable, which is a shape of bug no test was ever going to catch.' },
        { title: 'A note on what blocking does', desc: 'it is a hard filter, not a preference. A blocked artist is removed from every discovery playlist outright — nothing about the block feeds back into how tracks are ranked, so blocking three metal artists will not steer recommendations away from metal. The dial for <em>that</em> is Settings → Discovery → adventurousness, which trades genre-safety against novelty and unpopularity. There is still no way to mark a single <strong>track</strong> as disliked.' },
    ],
    '1.9.20': [
        { date: 'August 2026 · 1.9.20' },
        { title: 'Fixed: songs downloaded for a playlist never joined the playlist', desc: 'reported as "a server playlist created in Commissary doesn\'t sync properly with Plex — the songs get downloaded but don\'t get matched onto the playlist without manual intervention", and the log had it to the minute. A sync matches your library <em>at that moment</em>, writes the server playlist, and hands whatever is left to the wishlist. So the downloads start <strong>after</strong> the playlist is already written. Your log: a 50-track playlist synced with 3 matches at 09:06, 41 tracks downloaded and imported by 09:13, the library database caught up at 09:20 — and then nothing, for the remaining hour. The playlist still held 3 tracks while all 41 songs sat correctly in the library.' },
        { title: 'The chain now has its last link', desc: 'a finished download already triggers a media-server scan, and a finished scan already triggers a library database update. That chain then just... stopped, leaving the playlist to wait for whenever you next synced it by hand. A new <strong>Auto-Sync Playlists After Database Update</strong> automation closes it: the instant newly imported tracks become matchable, every playlist whose last sync came up short is re-synced. Playlists that already matched in full are left alone. A schedule could never have covered this — there are roughly 14 minutes between "sync queues the downloads" and "the downloads exist as far as a sync is concerned", so a periodic re-sync mostly lands inside that window and just re-confirms the tracks are missing.' },
        { title: 'And a sync that changes nothing now touches nothing', desc: 'the default <em>replace</em> mode deleted and recreated your Plex playlist on every sync, re-keying it and churning a "… Backup" copy each time, even when the result was identical to what was already there. Harmless at one sync a day; not harmless now that the chain above re-syncs after every database update. A playlist that already holds exactly the right tracks in the right order is now left untouched. Membership or order actually differing still rewrites, exactly as before.' },
        { title: 'One less red herring in the log', desc: 'every single successful playlist creation logged <code>ERROR — CreatePlaylist failed: Must include items to add when creating new playlist</code>. The retry on the very next line always succeeded, so nothing was ever wrong — but it sat at ERROR level right beside the real playlist problems, in exactly the file you would read to diagnose them. Demoted to debug; the rest of the fallback chain stays loud, because reaching those genuinely does mean something failed.' },
    ],
    '1.9.19': [
        { date: 'August 2026 · 1.9.19' },
        { title: 'Adapted from upstream 3.2.0: album torrents that stalled for hours', desc: 'the biggest fix in this batch, and six separate faults behind one symptom — an album grab sitting at 0% until the deadline, then failing with "no audio files found". The album flow preferred the <strong>magnet</strong> whenever the indexer offered both, and a magnet the client cannot resolve sits on "downloading metadata" forever; the stall timeout you had configured was wired into the per-track poll and <strong>never consulted for albums</strong>; nothing enforced a seeder floor, so a release with a dead swarm was still picked; and a stalled torrent was left running in your client, untracked, to be grabbed again as a duplicate next time.' },
        { title: 'What that means in practice', desc: 'Commissary now fetches the .torrent server-side and hands the file to your client the way Sonarr and Radarr do, keeping the magnet as a fallback. A stalled album gives up on your configured timeout and cleans up after itself, and — new — falls back to the per-track flow instead of ending the whole batch, so one dead release no longer sinks an album. There is a new <strong>minimum seeders</strong> setting (default 1) that drops releases known to have a dead swarm, while leaving alone anything whose seeder count the indexer simply does not report.' },
        { title: 'And the "no audio files found" at the end of it', desc: 'staging walked the save path plus the torrent\'s display name, but the folder on disk routinely differs from that name and the save path is shared with every other download running. It now asks qBittorrent directly where the release actually landed. A single-file torrent stages that one file rather than sweeping its parent — which was the shared download root, and could pull in a neighbouring download\'s audio. When a path genuinely cannot be read, the error now says so and names the path-mapping setting instead of claiming the release was empty.' },
        { title: 'Fixed: settings could vanish after a crash or a busy moment', desc: 'the configuration loader could not tell "this row is unreadable" from "there is no row" — and the no-row path regenerates defaults and writes them <strong>over your real settings</strong>. One locked database at startup, one I/O blip, and everything was gone. Absence now has to be positively established before anything overwrites; an unreadable row is retried, then the app runs on your config file with the stored row <em>protected</em> until a restart can read it. A corrupt row is copied aside to <code>config.corrupt-…json</code> the moment it is seen, rather than replaced.' },
        { title: 'Saving settings is now one write, not hundreds', desc: 'a single Save click wrote the entire configuration once per field — hundreds of encrypt-and-commit cycles for one form. That is what created the database contention that pushed saves onto the fallback file in the first place. And that fallback file was written with a truncate-on-open, so a crash mid-write left it empty; it is now written to a temp file and swapped into place, so the old contents survive until the new ones are safely on disk.' },
        { title: 'Two performance fixes with numbers behind them', desc: 'idle enrichment workers were re-counting the database every 2 seconds for as long as a tab was open — measured here at <strong>5,400 scans reduced to 360</strong> over a ten-minute idle session, with running workers still updating every tick and any start/pause showing immediately. Separately, TV shows had no index on their TMDB id where movies have had one since day one, so every discover rail, watchlist check and calendar lookup was a full table scan — now a <strong>20× faster</strong> indexed lookup. Calls to slskd are also bounded now, so an unresponsive Soulseek can no longer tie up the server\'s request threads.' },
    ],
    '1.9.18': [
        { date: 'August 2026 · 1.9.18' },
        { title: 'Fixed: the corner buttons covered the Rename Files panel', desc: 'Server Activity, the notification bell and the Interactive Help button float in the bottom-right above everything, which is right until something else owns that corner. The video <strong>Rename Files</strong> panel slides in against the right edge for the full height — 680px wide on a 1280px screen — and the buttons sit at x1156–1256, landing squarely on its Preview and Apply controls. They now step aside while the panel is open and come straight back when it closes.' },
        { title: 'And Server Activity was missing from two older rules', desc: 'the same thing was already handled for the Now Playing modal and the download-missing modal — but the Server Activity button had never been added to either list, so it kept covering both surfaces it was supposed to yield to. All three rules now name all four floating elements, with a test that fails if a new corner button or a new full-height surface is added without joining them.' },
    ],
    '1.9.17': [
        { date: 'August 2026 · 1.9.17' },
        { title: 'Manual search, straight from the download modal', desc: 'the Download Missing Tracks modal could only ever hand a track to the automatic cascade or drop it on the wishlist. There is now a <strong>🔎 Manual Search</strong> button beside Add to Wishlist that opens the multi-source picker for the track you have ticked — every configured source searched at once, candidates listed, you choose the file. It is deliberately one track at a time: the picker is a per-track decision, so with several ticked it says so rather than quietly searching whichever came first.' },
        { title: 'And the manual search box fills itself in', desc: 'the picker\'s header already named the song you were looking for — and the search box underneath it sat empty, with the Search button greyed out telling you to "type at least 2 characters". So the dialog showed you the query and then made you type it. The box now opens pre-filled with the artist and title, ready to run, and still fully editable — being able to change it is the entire point of a manual search.' },
    ],
    '1.9.16': [
        { date: 'August 2026 · 1.9.16' },
        { title: 'Fixed: a replacement download could hang its whole album', desc: 'reported as "music download replacements seem to get stuck in a Downloading state", and your log had it exactly. A 9-track album: three tracks failed the integrity check on duration and went back for a better copy — and sixteen seconds after the first retry, one worker slot leaked and never came back. A batch cannot finish until its active count reaches zero, so a slot reserved for a track that quietly finished holds the <strong>entire album</strong> open. The log shows <code>reported=3, actual=2</code> on every pass for 80 seconds, then "all 9 task(s) finished but the batch never completed".' },
        { title: 'The accounting is now enforced, not remembered', desc: 'post-processing has a dozen ways out, and four of them deliberately don\'t finish the track because it is going around again. That only worked while every single exit remembered which kind it was — one that forgets costs a hung album. Now a hand-off has to be <em>declared</em>; any other way out of that code releases the slot, exactly once, including routes that don\'t return normally at all. If a future change ever does leak one, the log says which track and the batch still finishes instead of hanging.' },
        { title: 'And something is finally watching replacements', desc: 'the 90-second stall detector — the thing that should have caught this — never ran once. In 41,902 lines of your log the phrase it logs appears <strong>zero</strong> times. It bailed out immediately for any track with no recorded source, which is precisely the state a replacement passes through: fetching a better copy deliberately clears the old source first. So an ordinary download that stalls was rescued in 90 seconds while a replacement in the same state was invisible forever. A track that claims to be downloading with nothing to download from is now stalled by definition, and gets the same timeout and retry ladder as everything else.' },
    ],
    '1.9.15': [
        { date: 'August 2026 · 1.9.15' },
        { title: 'Fixed: Naming Conformance found nothing after a naming change', desc: 'reported as "changed the naming scheme but the tool finds no episodes that need renaming". Two faults, and both of them looked identical to a library that already conforms. First, the job stood down entirely on any template mentioning a token it could not work out for an existing file — and <code>{Custom Formats}</code> was on that list, which is in the TRaSH scheme this app installs with a one-click button. So adopting the recommended naming <strong>silently switched the tool off</strong>.' },
        { title: 'Custom formats are no longer a mystery to a rename', desc: 'they are matched against a release NAME, and a file already in your library still has one — the name it is called right now. So they are worked out the same way at rename time as at import, from the file\'s own name and your own format definitions. A format whose terms are not in the name is never invented. That takes the token off the unavailable list altogether, so the TRaSH scheme just works.' },
        { title: 'And the job was computing a poorer name than the rename preview', desc: 'the second fault, and the one that could actually have cost you something. This job\'s query never got the column widening its sibling gained in 1.9.13: episodes carried <strong>no series year at all</strong>, and neither movies nor episodes carried audio codec, channel layout or dynamic range. So for the same file, Naming Conformance worked out a shorter name than Rename Files did — and approving that "fix" would have stripped the year, the audio detail and the release group off disk. Both paths now render the identical name.' },
        { title: 'When something really cannot be reproduced, it says so', desc: 'a few tokens genuinely only exist at import — bit depth, audio languages, the original release name. Those files are no longer hidden from you. The finding is raised as a <strong>warning</strong> naming the tokens involved, so you see current → new side by side and decide. Nothing here has ever renamed anything without your approval, so refusing to look was the wrong trade: it left "0 findings" as the only thing the tool could say, whether your library was perfect or the job had simply declined to run.' },
    ],
    '1.9.14': [
        { date: 'August 2026 · 1.9.14' },
        { title: 'HiFi stops re-dialling instances it already knows are down', desc: 'the public HiFi instances are volunteer-run and outages are normal — but Commissary had no memory of them. Every search walked all seven hosts and paid the full connection timeout on each, every time. In one 12-hour log that came to <strong>4,094 "all instances exhausted" errors and around 23,500 warnings</strong> — between them 47% of that log\'s errors and 80% of its warnings — with one search burning 16 seconds on a host the app had <em>already</em> declared dead moments earlier.' },
        { title: 'What changes', desc: 'each instance now gets a cooldown after it fails, and is skipped without opening a connection until that elapses. When every instance is cooling, HiFi is skipped instantly and your search falls through to your other sources. Measured on a fully dead pool: the first search still tries all seven and learns, the next ten make <strong>no network calls at all</strong>.' },
        { title: 'It recovers by itself', desc: 'the cooldown starts at a minute and doubles for an instance that keeps failing, capped at fifteen. When it elapses that host gets one probe, and a single success clears its record completely — a host that works is not "less broken", it is working. Editing your instance list or hitting Restore Defaults clears every cooldown immediately, so a change you make takes effect at once.' },
        { title: 'And the log says it once', desc: 'a whole-pool outage is now one warning per cooldown window telling you roughly how long HiFi will sit out, instead of thousands of identical lines burying it. The HiFi status endpoint also reports which instances are cooling and for how long, so a source that gets skipped can say why.' },
        { title: 'Fixed: a video naming template would not stick', desc: 'reported as "Library video naming templates do not save after leaving the page". Typing a template and clicking away did save — but the two controls that write the box <em>for</em> you did not. A value set by code raises no change event, and the change event was the only thing that triggered the save, so <strong>clicking a token to insert it, or loading the TRaSH scheme, never reached the server</strong>. The box showed your new template until you left the page, then it was gone. The preset even said "click away to save", which could not work for exactly that reason.' },
        { title: 'And a slow load could eat what you just typed', desc: 'opening Settings → Library fires about a dozen requests at once, and the organization one was measured landing <strong>924ms after the page appeared</strong> — then writing the stored template back over the box. Anything you did in that window vanished in front of you, which reads as "it didn\'t save" even when it had. A response that loses the race no longer touches a field you have edited, and a save can no longer fire before the form has loaded, which could write blank checkboxes over your real post-processing settings.' },
        { title: 'Fixed: "Reset to the standard layout" did not restore the standard layout', desc: 'found next door to the above. That button posted its own copy of every default, written in JavaScript a directory away from the real ones — and it had drifted: it turned <strong>off</strong> the NFO and artwork sidecars, which the actual defaults turn on. It also posted the minimum-free-disk value, which is not the video side\'s to set — that setting is shared app-wide, so resetting a video <em>naming</em> card dropped the <strong>music</strong> side\'s disk floor to zero and disabled its guard. Reset now sends blank templates and lets the server fill the rest from the real defaults, which is what it always claimed to do.' },
        { title: 'Also in 1.9.13', desc: 'renaming an existing file no longer strips its audio, dynamic range and release group.' },
        { title: 'Fixed: renaming a file could strip its audio and release group', desc: 'reported as "the Rename Files variable picker doesn\'t show the new tokens" — which turned out to be the visible edge of something worse. Only the <em>import</em> path knew about the new <code>{Token}</code> values, so for a file already in your library the rename preview and the Naming Conformance job both worked out its name <strong>without</strong> its audio codec, channels, dynamic range or release group. Conformance would flag a correctly-named file as wrong, and approving that fix would rename it to the shorter version — deleting that detail from the filename.' },
        { title: 'All three now agree', desc: 'renames and the conformance check read the audio codec, channel layout and dynamic range the scan already recorded, and recover the release group and edition from the file\'s current name. For the same file, the importer, the rename preview and the conformance job now produce identical results.' },
        { title: 'And a rename can no longer lose what it cannot rebuild', desc: 'a few tokens only exist at import — bit depth, audio languages, custom formats, the original release name. If your template uses one of those, the Naming Conformance job now stands down for those files and says so in the log, instead of proposing a rename that would quietly drop them.' },
        { title: 'The picker matches Settings', desc: 'the Rename Files variable list now offers the same vocabulary the Settings page does — 43 entries for a show — split into <strong>Variables</strong> and <strong>Sonarr/Radarr tokens</strong>, each with a description and the value it would take for the title you are looking at. Tokens that only exist at import are shown dimmed with an explanation rather than hidden, so a template using one explains itself.' },
        { title: 'Also in 1.9.12', desc: 'Sonarr/Radarr <code>{Token}</code> naming arrived, so a scheme copied from the TRaSH guides works as-is.' },
        { title: 'Sonarr/Radarr naming, including the TRaSH schemes', desc: 'video naming templates now understand Sonarr and Radarr\'s <code>{Token}</code> names alongside the existing <code>$variables</code> — so a format string copied straight out of the <a href="https://trash-guides.info/Sonarr/Sonarr-recommended-naming-scheme/" target="_blank" rel="noopener noreferrer">TRaSH guides</a> works as-is, no translation. There is a one-click button for the recommended scheme in <strong>Settings → Library Organization</strong>, and a browsable list of every token (24 for movies, 31 for episodes) that inserts at your cursor.' },
        { title: 'Optional groups are what make those schemes work', desc: 'wrapping a token in braces makes the whole group conditional: <code>{[Quality Full]}</code> renders <code>[WEBDL-1080p]</code>, or disappears completely — brackets included — when there is nothing to put there. That is what keeps empty <code>[]</code>, stray dashes and orphaned punctuation out of your filenames when a release has no edition, no HDR, or no known group. <code>{season:00}</code> zero-pads and <code>{Episode CleanTitle:90}</code> caps a length, exactly as they do in Sonarr.' },
        { title: 'New: what the file actually contains', desc: 'audio channels (<strong>5.1</strong>, <strong>7.1</strong>), video bit depth (<strong>10bit</strong>), audio languages and the real dynamic range type (<strong>DV</strong>, <strong>HDR10</strong>, <strong>HLG</strong>) are now read out of the container by ffprobe and available as tokens. They come from the file, never from what the release name claims — which is the point, since a name asserting "HDR" proves nothing. Edition detection (<strong>Directors Cut</strong>, <strong>IMAX</strong>, <strong>Extended</strong>) and <code>{Custom Formats}</code> are wired up too.' },
        { title: 'Your current naming has not changed', desc: 'the defaults are untouched and every <code>$variable</code> keeps working exactly as before — adopting a new scheme is something you choose, because switching silently would leave every file already on disk mis-named until renamed. The two styles can even be mixed in one template. The example under each box is now rendered by the real naming code rather than a copy of it, so what it shows is what you get.' },
        { title: 'Also in 1.9.11', desc: 'four bugs found in the logs: a downloaded episode filed under the wrong show, Deezer dying silently, the wishlist retrying the same failures forever, and healthy download batches reported as broken.' },
        { title: 'Fixed: a failing track could be retried forever, never backing off', desc: 'the wishlist has had a retry ladder for a while — a track that keeps failing earns a growing cooldown (4 hours, then a day, then weekly) instead of burning a search every cycle. In one 12-hour log it <strong>never engaged once</strong>. Re-adding a failed track created a second, duplicate wishlist row for the same album, and nothing ever recorded an attempt against that copy — so its counter sat at zero, it looked freshly-added on every pass, and it was retried indefinitely. The re-add now recognises a track it already has.' },
        { title: 'What that was costing you', desc: 'in the same log, <strong>34 files were downloaded and quarantined again and again — one of them 132 times</strong>. Each cycle re-fetched a file that had already failed its integrity check, threw it away for the same reason, and queued it up to do it again. Duplicate rows already in your wishlist are cleaned up once on upgrade; a track from a genuinely different album still gets its own entry, which is what that mechanism was for.' },
        { title: 'Downloads stopped reporting healthy batches as broken', desc: 'the batch self-check counted every finished track as an "orphaned task", so a perfectly normal download batch was declared damaged every 30 seconds for its entire life — one log had 1,499 of these warnings across 31 batches, with the "orphan" count simply climbing in step with progress. It now looks for the two things that are actually wrong: a batch whose tasks have all finished without it completing, and a queued task whose record has gone missing.' },
        { title: 'Fixed: Deezer would stop working and never start again', desc: 'the gateway hands out a session token when you authenticate, and Commissary kept that one token for as long as the app was running. Deezer expires it after a while — and from that moment every single Deezer download failed, forever, until you restarted. One log covering half a day showed <strong>669 consecutive failures, about 20 an hour</strong>, all of them the same rejected token. The client now notices that specific rejection, renews the session from your saved ARL, and retries the call.' },
        { title: 'Why it was invisible', desc: 'nothing marked the source as broken, so the connection light stayed green and Deezer stayed first in the download chain — being handed tracks it could only fail. Worse, the error you saw was <em>"impl returned None"</em>, while the only line that named a cause was a warning buried further up. A failed session now drops the source out of the chain honestly, and the real reason travels with the failure.' },
        { title: 'This is what jams the wishlist', desc: 'if your wishlist has been reporting the same number of failures every run and never shrinking, this is why. With Deezer first in the chain and silently dead, every track failed and went straight back on the list, so the next run tried the identical set and failed identically. The same log showed 22 consecutive runs of exactly 20 tracks, all failing.' },
        { title: 'Wishlist runs are logged honestly now', desc: 'the completion summary was written at ERROR level for every run — including runs where <em>nothing failed at all</em> — and read as "20 added to wishlist, 20 failed", which looks like 40 tracks with half of them fine. It is the same 20 tracks counted twice: the ones that failed, going back on the list. A clean run is now an ordinary info line, and a run with failures says so once.' },
        { title: 'Video grabs name the show that was refused', desc: 'a refused TV grab logged "refused for None" — 49 times in one log — because episode entries carry the show name in a different field from movies, and both messages only read the movie one. Same for the out-of-disk-space skip.' },
        { title: 'Fixed: a downloaded episode could be filed under the wrong show entirely', desc: 'a wishlist grab would fetch exactly the right episode, and the show it belonged to would then be matched to an unrelated title. The cause was in how a show gets identified: when your media server knew the show by its TVDB id but had no TMDB id for it, the app searched TMDB for the show\'s <em>name</em> and accepted the first result without ever checking it was the right one. One reported case matched <strong>Silo</strong> to a completely different series.' },
        { title: 'Why it did more damage than a wrong poster', desc: 'everything downstream of that id came from the other show — summary, status, ratings, and the <strong>season list</strong>. A wished S03 then had no season to belong to, so the episode never reconciled against your library, the wishlist row was never satisfied, and the drain grabbed the same episode again on the next tick. The row was marked "matched", so nothing ever re-examined it.' },
        { title: 'Identity is now established, not guessed', desc: 'if your server supplied a TMDB id, that is used. Failing that, any other id the show already carries — TVDB or IMDb — resolves the correct TMDB entry <em>exactly</em>, in one call. Only when there is no id of any kind does it fall back to searching by name, and that search now has to prove the result actually carries that title before it counts. When nothing does, it records "not found" and retries later rather than picking something.' },
        { title: 'A wrong year no longer buries the right show', desc: 'the name search filtered by year, so a show whose stored year was off had the real entry excluded from its own results — leaving only same-named strangers to choose between. The year is now dropped and the search retried before the app will give up.' },
        { title: 'Shows already filed wrong get corrected on their own', desc: 'nothing would have revisited them, so each show carrying both a TMDB and a TVDB id is now checked once, in the background, against the one question that settles it — which TMDB entry does this show\'s TVDB id belong to. A show that agrees is marked verified and never checked again. A show that <em>disagrees</em> is re-pointed at the right title and the wrong show\'s metadata is cleared so it refills correctly. One lookup per show, once, then never again.' },
        { title: 'Also in 1.9.10', desc: 'playlist discovery results can be filtered by match quality.' },
        { title: 'Filter playlist discovery by how good the match was', desc: 'importing a playlist matches every track against your library, and on a couple of hundred tracks the handful that need you are buried among the ones that matched cleanly. The results table now has filter chips with counts — <strong>Perfect</strong>, <strong>Low confidence</strong>, <strong>Wing It</strong>, <strong>Not found</strong>, <strong>Error</strong> — so you can jump straight to the rows worth a second look.' },
        { title: 'What "Low confidence" means', desc: 'the line is drawn at 0.9, the strictest bar any discovery source applies before it will call something a match. Playlist discovery itself accepts down to 0.7, so "low" means <em>accepted, but by a looser rule than the strictest source would have used</em> — matched, plausibly correct, worth your eyes. "Wing It" is different again: a placeholder the app invented because it found nothing, so those are never counted as matches.' },
        { title: 'It only changes what you see', desc: 'filtering narrows the table and nothing else. Selection and downloading still operate on the whole result set, so hiding a group can never quietly change what a later button does. Your chosen filter also survives the table refreshing as discovery streams in, rather than resetting mid-triage. Buckets with nothing in them are not offered at all.' },
        { title: 'Also in 1.9.9', desc: 'Auto-Import\'s confidence threshold moved from 90% to 45%, because 90% on a product of three fractions was close to unreachable.' },
        { title: 'Auto-Import will actually import things now', desc: 'the confidence threshold defaulted to 90%, which sounds strict and was in fact close to unreachable. The score is a <em>product</em> of three fractions — how sure we are which album this is, how well the track titles agree, and what fraction of the tracklist is present — so it falls away much faster than a percentage suggests. A perfectly tagged album with slightly imperfect identification scored about 83% and was refused. The default is now 45%.' },
        { title: 'It has not been made careless', desc: 'the same arithmetic is what keeps it safe. A folder holding 3 of 12 tracks scores about 12%; an album whose titles disagree with the release it was matched to scores about 14%; a folder the app could not confidently identify scores about 26%. All three are still refused, comfortably. What changed is that a complete, correctly identified folder no longer waits for you to confirm what the matcher already got right.' },
        { title: 'Where to change it', desc: 'Import → Auto-Import, the confidence slider. Your own setting always wins over the default — this only moves the value used when you have never touched it.' },
        { title: 'Also in 1.9.8', desc: 'the source picker became the default search action, failed batch tracks gained a Sources button, torrent results name their tracker, and the Torrent Client category is respected again.' },
        { title: 'Clicking a search result now asks you which source', desc: 'the multi-source picker arrived in 1.9.0, but clicking a track still opened the old analyse-and-grab modal — so the picker was there and almost nobody met it. Searching every source and choosing is the point of the search page, and it is now what a click does. The automatic download is still one click away as <strong>Auto</strong>, deliberately quieter, for when you would rather not choose.' },
        { title: 'Failed tracks in a batch download can be picked by hand', desc: 'Begin Analysis runs unattended across a whole playlist — it cannot stop and ask you about track 34 of 60 — so anything it could not place just sat there marked failed. Those rows now carry a <strong>🔍 Sources</strong> button that opens the picker for that one track. The picker could always reach them (the status cell has been clickable all along), but nothing said so, which made the wishlist the only route anyone found.' },
        { title: 'Torrent results say which tracker they came from', desc: 'Prowlarr hides many indexers behind one "torrent" source, which is no help when you are choosing between two releases. Every torrent and usenet result in the picker now names its tracker, and — when the indexer publishes one — links straight to that release\'s page so you can check the comments, freeleech status or file list before committing. The link opens in a new tab, so clicking it never navigates you away mid-download.' },
        { title: 'Fixed: torrents ignored your Torrent Client category', desc: 'set the category to <em>Music</em> and every music torrent still arrived tagged <strong>soulsync</strong>. The clients were reading the setting correctly — but the function that hands a release over declared its category default as the literal "soulsync", and each client resolves <em>the value it was given, or the configured one</em>. A literal is always "given", so the configured value could never win. Music now sends nothing and lets your setting apply. Video was never affected: it passes its per-Library category explicitly, which is exactly why this went unnoticed.' },
        { title: 'What has not changed', desc: 'Begin Analysis still downloads automatically from your source list, in order. That is the right behaviour for a batch, and the per-track picker is the escape hatch for the handful it gets wrong — not a prompt for all sixty.' },
        { title: 'Also in 1.9.7', desc: 'albums named "Album 01 Title" match again, and hand-assigned files no longer claim to be 100% matches.' },
        { title: 'Fixed: albums named "Album 01 Title" matched nothing', desc: 'a whole album could arrive correctly named and auto-match not one track, leaving you to drag all eleven into place by hand. When a file has no title tag the matcher reads the filename, and it only knew how to strip a track number from the <em>front</em> — so with the album name sitting in front of the number, it scored the entire string "Blue Blood 01 Blue Blood" against the track "Blue Blood". That came to 0.34 against a 0.40 threshold: near enough that the naming looked supported and silently wasn\'t. It now also reads the name without the album prefix and keeps whichever fits better.' },
        { title: 'A file you assign by hand no longer claims to be a 100% match', desc: 'manual assignments were stamped with full confidence and displayed as <strong>100%</strong> — so the row you were looking at precisely <em>because</em> matching failed was the one boasting the matcher was certain. It now reads <strong>manual</strong>.' },
        { title: 'It has not been made less strict', desc: 'the album prefix is only removed when the file has no title tag (a real tag stays authoritative), only when the album name ends on a word boundary, and never when removing it would leave nothing — a track genuinely named after its album keeps its title. The threshold is unchanged, so a wrong track still does not match.' },
        { title: 'Also in 1.9.6', desc: 'download sources collapsed from four settings into one ordered list.' },
        { title: 'Your download sources are one ordered list now', desc: 'they were four settings that all described the same thing — a mode, a hybrid order, and a legacy primary/secondary pair left over from when there were only two. Whether a source was used, and in what order, depended on which of the four a given piece of code happened to read. They are now a single ordered list: the first entry is preferred, the rest are fallbacks, and "hybrid" simply means you listed more than one.' },
        { title: 'Why that was worth doing', desc: 'the settings could genuinely disagree. Album downloads defaulted the mode to <em>hybrid</em> while the album-bundle dispatcher defaulted the same setting to <em>soulseek</em>, so one install could take two different views of its own configuration depending on which path a download took. There is one derivation now, and every consumer asks it the same question.' },
        { title: 'Nothing to reconfigure', desc: 'your existing settings are read exactly as before — including installs old enough to still be on the primary/secondary pair — so an install that never opens Settings behaves identically. Saving Settings once writes the new collapsed form. The old keys are still written alongside it, so downgrading to 1.9.5 finds its configuration intact.' },
        { title: 'Dead Soulseek-era search code removed', desc: 'the Search page still carried a single-source result renderer from before the multi-source picker replaced it in 1.9.0 — 134 lines that nothing called, including its own Download button wired to the old one-click path.' },
        { title: 'Also in 1.9.5', desc: 'video Libraries gained the writability check that Music Libraries got in 1.9.4.' },
        { title: 'Video Libraries are checked for writability too', desc: '1.9.4 added this to Music Libraries, after an album imported into a folder the server had no permission to write to and reported every track as imported. The video side has the same destinations, the same failure and the same silence — a grab lands, the import fails, and the Library folder just stays empty. Settings → Libraries now marks any Library the server cannot write into with <strong>NOT WRITABLE</strong>, and hovering it explains what to check.' },
        { title: 'One probe, both sides', desc: 'the check moved to a shared module rather than being written twice — a copy that drifts is how two pages end up disagreeing about whether the same folder works. It tests by creating and removing a folder, not by reading permission bits, because bits give the wrong answer under container UID remapping, NFS root-squash and ACLs, which is exactly where this breaks. Two Libraries pointing at the same folder probe it once.' },
        { title: 'Also in 1.9.4', desc: 'a failed import can no longer report itself as a success, and Music Libraries gained the same writability check.' },
        { title: 'Fixed: imports that failed reported themselves as successful', desc: 'the one that matters. Post-processing catches any error, re-queues the file and returns quietly so a download can be retried later — but a manual import has nothing watching it, so it read that quiet return as success. A user imported an eleven-track album into a folder the server had no permission to write to: every track logged <em>Permission denied</em>, every track notified "Track Imported", and not one file moved. A failed import now says so, <strong>and says why</strong> — the permission error appears in the UI instead of only in app.log.' },
        { title: 'Music Libraries are checked for writability', desc: 'the failure above was invisible until someone read the log, because a destination the server cannot write to looks exactly like a destination nothing has been sent to yet. Settings → Paths & Organization now marks any Music Library the server cannot write into with <strong>NOT WRITABLE</strong>, and hovering it explains what to check. It tests by creating and removing a folder, not by reading permission bits — bits are the wrong answer under container UID remapping, NFS root-squash and ACLs, which is where this actually goes wrong.' },
        { title: 'If you see NOT WRITABLE', desc: 'the folder exists but the user Commissary runs as cannot create anything inside it. Compare the folder\'s owner with the PUID/PGID your container runs as — on Unraid, shares are usually <em>nobody:users</em> (99:100) while the image defaults to 1000:1000. A folder created by a different user or another container is the usual cause. Note that video working on the same base folder proves nothing: permissions are per-folder.' },
        { title: 'Your Music Library Folder repairs itself on start-up', desc: '1.9.3 fixed editing that field, but only from the next save onwards — anyone already caught by the 1.9.2 bug stayed broken, with the field showing the right path and imports still going somewhere else, and no reason to ever re-save. Commissary now re-aligns the two when it starts, keeps your label, and writes the old path to the log so you can find anything already misfiled.' },
        { title: 'Also in 1.9.3', desc: 'the Music Library Folder regression, honest per-track import messages, and Deep Scan learning about libraries.' },
        { title: 'Fixed: changing your Music Library Folder did nothing', desc: 'a 1.9.2 regression, and the worst kind — nothing looked broken. Music Library Folder and the first entry under Music Libraries are the same setting shown twice, and the importer reads the entry. Editing the folder in Settings left the entry on the old path, so downloads kept landing where you had moved away from while Settings insisted otherwise. Saving Settings now moves the library with it. <strong>If an album went missing after updating to 1.9.2, look at the first path under Music Libraries — that is where it went.</strong>' },
        { title: 'Fixed: "Album Imported (1/1 tracks)" on an 11-track album', desc: 'the Import page submits an album one track per request, so that message fired once per track and each one claimed to be the whole album. An eleven-track import looked like it had found a single file. It now says "Track Imported — <em>track</em> — <em>album</em>", and a track that failed says so instead of announcing an import that did not happen.' },
        { title: 'Deep Scan now covers every Music Library', desc: 'it only ever looked at the one original folder, so anything in a second library was invisible to it. It now scans them all — and scores each one separately, which is the part that matters.' },
        { title: 'Why separately, and not all at once', desc: 'Deep Scan relocates files it cannot find in the database, and refuses when the untracked share of a folder is implausibly large — the signature of a database out of sync rather than a pile of new arrivals. Pooling libraries defeats that: a library you just added is 100% untracked by definition, and measured against an existing large library that share falls under the threshold. Adding a library full of music you already own would have moved all of it into Staging. Scored per library, it is left alone.' },
        { title: 'Smaller Deep Scan fixes alongside', desc: 'a moved file now keeps its folder structure relative to the library it came from rather than the first one; a library that is not mounted is skipped instead of being read as "every file in it has vanished"; and if any library trips the out-of-sync guard, no stale database rows are deleted anywhere — the database does not record which library a row belongs to, so a bad reading taints all of it.' },
        { title: 'Also in 1.9.2', desc: 'music gained more than one library.' },
        { title: 'Music can have more than one library', desc: 'music has had exactly one output folder since the beginning — everything you downloaded went to the Music Library Folder and nowhere else, while the video side has been able to file a film into whichever library you chose for years. Settings → Paths & Organization now has a <strong>Music Libraries</strong> list: add as many as you like, and the first one is the default destination.' },
        { title: 'Nothing changes until you add a second one', desc: 'your existing library becomes the first entry automatically, so an install that never opens the new setting writes files to exactly where it wrote them yesterday. There is no migration to run and nothing to configure.' },
        { title: 'Each library can name files its own way', desc: 'and use its own quality profile. Leave both blank — which is how they start — and the library inherits your global settings. A library profile governs the whole pipeline for files going there, not just search ranking: the quality gate, the fingerprint check, deep verify, replace-lower, downsampling.' },
        { title: 'Reorganize keeps files where they live', desc: 'worth calling out because the opposite would be quiet and destructive: reorganizing a file that sits in one library re-files it <em>within that library</em>. It does not pull everything back into the default one. Moving something between libraries is still possible — it just has to be asked for.' },
        { title: 'Your library folder and the first entry are the same thing', desc: 'Music Library Folder above the list is the first library, and editing either updates the other. Two settings quietly disagreeing about where music goes is exactly the kind of thing that wastes an afternoon.' },
        { title: 'The old extras list is now called Additional Read-Only Paths', desc: 'it was "Additional Music Libraries", which now reads like it means destinations — it never did. Those are folders Commissary reads but never files anything into, and the section says so.' },
        { title: 'Also in 1.9.1', desc: 'importing from any of your download folders instead of only the Import folder.' },
        { title: 'Import from any of your download folders', desc: 'Import only ever read one folder — the Import folder in Settings — so anything sitting where your download client left it had to be moved there by hand before it could be imported at all. There is now a "Change folder" button on the Import page: browse your download, import and library folders, pick the one you want, and the scan reads that instead.' },
        { title: 'It opens where your downloads land', desc: 'no path typing. The picker starts on your download folder with shortcuts to every configured root, and tells you how many audio files are directly in whatever folder you are looking at before you commit to it — a folder with none can still be the right pick, since subfolders are scanned too.' },
        { title: 'Your choice sticks while you work', desc: 'switching between the Albums, Singles and Auto-Import tabs keeps the folder you chose. The header says "Scanning: …" instead of "Import: …" whenever you are somewhere other than the configured Import folder, so an empty result is never mistaken for your Import folder having broken. "Back to Import folder" returns you to the default.' },
        { title: 'Where it will and will not go', desc: 'the browser is limited to the folders Commissary already knows about — your download folder, the torrent and usenet completed paths, the Import folder, your music library and any extra library paths. It will not walk the rest of the machine, and it stops offering "up" at the edge of those rather than leading you somewhere the scan would then refuse. Admin only, like the rest of the Import page.' },
        { title: 'Changing folder clears the matching you had in progress', desc: 'deliberately. A selected album and its per-track matches name files in the folder you just left, so carrying them across would let you import a match built against files that are no longer on screen.' },
        { title: 'Also in 1.9.0', desc: 'every connected source became searchable, and albums got a release picker.' },
        { title: 'Every source you have connected is now searchable', desc: 'the source list behind manual search was filtered by your download mode: in Hybrid it offered only the sources in your fallback chain, and in single-source mode it offered exactly one. But that setting is about which source the AUTOMATIC cascade downloads from — it was never a statement that the others do not work. A source you had connected and could download from was simply invisible unless you also re-ordered your fallback chain to see it. Every configured source is now offered; your chain order still leads the list, it just no longer excludes.' },
        { title: 'Choose a release for a whole album', desc: 'a new button on album and single/EP results. It asks every source that indexes whole albums what it has, shows you the releases side by side — format, track count, size, seeders — and downloads the exact one you choose instead of guessing for you. Your pick overrides your configured download source: choosing a torrent release while running Soulseek-only does what you would expect, because the mode setting was only ever answering "what may claim a whole album unattended?".' },
        { title: 'Whole-album sources are kept separate from track results', desc: 'torrent and usenet index releases, not tracks, so a hit from them is an entire album. Those results now sit under their own heading in the track picker with a note saying what picking one actually does: the full release downloads, the matching track is kept, and if the release turns out not to contain it the download fails rather than importing the wrong file.' },
        { title: 'Results are grouped by source', desc: 'so you can see that one source has it in FLAC while another only has a low-bitrate rip — instead of one merged list that hides which of your sources is actually serving you well. Sources that came back with nothing say so, and one that errored is marked rather than silently missing: "who has this?" is only answered properly if the zeroes are visible too.' },
        { title: 'A search that finds nothing now tells you where it looked', desc: 'it used to replace the whole panel with one line. "Nothing anywhere" and "three sources failed, the rest genuinely do not have it" are different answers, and only one of them means you should stop looking.' },
        { title: 'Also in 1.8.19', desc: 'searching every source by hand and picking a track, on Search, album rows and the wishlist.' },
        { title: 'Search every source yourself and pick what downloads', desc: 'a new button on the Search page, on album tracks and on each wishlist row. It searches every download source you have configured, shows you what each one is offering, and lets you choose the copy you want. Until now the only route to a track was to add it to the wishlist and wait for the automation to have a go.' },
        { title: 'Picking by hand keeps every safety net', desc: 'your choice goes down the same path an automatic download does, so the fingerprint check and the quality quarantine still apply. It also tells the auto-retry to leave your choice alone: it tries the file you picked and reports back, rather than quietly going off and grabbing something else if it fails.' },
        { title: 'Where the button is', desc: 'Search results get "Sources" next to Stream and Download. Album tracks get it whether you own them or not — for a missing track it is a way to fill the gap, for one you own it is a way to swap in a better copy. And every wishlist row gets one, which is the useful one when something has been sitting there not downloading.' },
        { title: 'It gives you a way out, not an explanation', desc: 'worth being straight about: this lets you rescue a stuck item by hand, but it still does not tell you why it was stuck. If something has been on your wishlist a long time, check the Ignored list first — removing a track or cancelling its download quietly stops the automation re-adding it for thirty days, and that is invisible from the wishlist itself.' },
        { title: 'Also in 1.8.18', desc: 'downloads filed into category folders now import, and manual placement stopped reporting failures for files it had placed.' },
        { title: 'Downloads sorted into category folders now import', desc: 'if your download client files finished downloads into folders — the usual "complete/Movies/…" or "complete/TV/…" — Commissary could not find them. It looked exactly one folder below each download root, and a category layout puts the release two down, so the download simply sat there and never imported. It now looks three levels deep.' },
        { title: 'Still bounded, so it stays quick', desc: 'the old one-level limit existed to stop a download root turning into a directory crawl, which is a fair worry. The search is capped by depth and by how many folders it will look at, so it reaches your releases without wandering. Adjustable with download_source.import_search_depth if your layout is deeper.' },
        { title: '"Couldn\'t place the file" — when it had, in fact, placed the file', desc: 'manual import placement copied the file while your browser waited, and a large file over a network share takes minutes. If anything along the way gave up waiting, you got an error — while the server quietly finished the copy perfectly. The page then kept the item on screen as though nothing had happened, and trying again gave a different error still.' },
        { title: 'Placement now happens in the background', desc: 'small files finish instantly and behave exactly as before. A big one hands you back the page and reports progress, so nothing can time out and misreport it. And the page never calls a placement failed without checking what actually happened first.' },
        { title: 'Trying again is safe now', desc: 'asking a second time for something already placed says so instead of erroring, and asking while a copy is still running joins the one in flight rather than starting a second copy of the same file on top of it.' },
        { title: 'Also in 1.8.17', desc: 'deselecting a tracker now actually stops it being used.' },
        { title: 'Deselecting a tracker now actually stops it being used', desc: 'the tracker checkboxes on each Library only ever nudged the ranking — a ticked tracker\'s releases scored higher, but every tracker was still searched. So unticking one removed a bonus and changed nothing about where downloads came from. Ticked trackers are now the only ones searched for that Library, on automatic searches as well as manual ones.' },
        { title: 'The setting never said that, because the label kept disappearing', desc: 'there was a note explaining it was only a preference, but it lived on a text box that gets hidden the moment the tracker checkboxes appear. So the one sentence describing the behaviour vanished exactly when the control it described showed up, leaving an unlabelled list of trackers that reads as "search these". Both tracker settings now carry a caption you can actually see.' },
        { title: 'Two more places were ignoring your tracker choices', desc: 'found while fixing the first. The RSS pass — the job that watches for new releases — polled every indexer even when "Restrict to indexer IDs" named a few, so a release from an excluded tracker could be picked up and grabbed unattended. And a manual search worked out which Library you had picked only AFTER searching, so it could re-order results but never limit where they came from.' },
        { title: 'You can tick the indexers instead of typing their numbers', desc: 'Settings → Indexers listed your indexers with their IDs, above a box asking you to type those same IDs in by hand. The list is now clickable and fills the box for you. The box stays visible and in sync rather than being hidden — that is the mistake that caused the confusion above.' },
        { title: 'How the two settings combine', desc: 'the global "Restrict to indexer IDs" is the outer limit; a Library\'s own choice narrows it further and can never widen it. If you pick trackers a Library is globally barred from, nothing is left to search — rather than quietly falling back to searching everything, Commissary now tells you the two settings contradict each other.' },
        { title: 'Worth a look after updating', desc: 'if you have ticked trackers on a Library expecting a preference, that selection now binds. A Library whose ticked trackers have no results will come up empty instead of falling back to the others.' },
        { title: 'Also in 1.8.16', desc: 'renaming files from a show\'s own page, plus two download fixes.' },
        { title: 'Rename a show or film’s files from its own page', desc: 'a Rename Files button on any show or movie you own. It shows the naming template, every $variable you can use — each with the value it takes for that title, so $episodetitle reads "Pilot" rather than a generic legend — and a live list of every file with its current name and the name it would get. Nothing moves until you confirm.' },
        { title: 'Type a name and watch it update', desc: 'edit the template and the preview re-renders as you type; click a variable to insert it. The template you type here is a one-off for that rename — your saved naming template in Settings is not changed.' },
        { title: 'It was already possible, just not findable', desc: 'the rename engine existed but only ran across your entire library from the Tools page, with no way to see the variables or aim it at one title. Same engine underneath: sidecars still travel with the file, a name that is already taken is skipped rather than overwritten, and the database follows the move.' },
        { title: 'Torrents are no longer imported while still being written', desc: 'reaching 100% means the bytes are in, not that the client has finished putting them where Commissary is about to read. qBittorrent reports 100% while "moving" a finished download from the incomplete folder to the complete one, and the import could read a file mid-copy. Usenet repair and unpack have the same shape — both write long after the download says it is done.' },
        { title: 'It now waits for the writing to stop', desc: 'the import holds until the file reads identically twice in a row, about three seconds apart. Checking the download state alone could not fix this: clients report "moving" and "finished, queued to seed" as the same thing, and refusing to import on that would strand every seeding torrent at 100% forever.' },
        { title: 'YouTube: the last download retry no longer fetches the video', desc: 'the music downloader has always taken audio only — but its third retry deliberately switched to a combined video+audio stream, downloading the whole video so ffmpeg could throw the picture away. That fallback was redundant as well as wasteful, and it is gone.' },
        { title: 'And you can now keep YouTube’s original audio', desc: 'Settings → Downloads → YouTube Audio Format. MP3 320 stays the default, but YouTube serves Opus at roughly 130–160kbps, so converting it is a lossy-to-lossy transcode: a bigger file that sounds slightly worse. "Original" keeps the stream untouched — smaller and better. Note that a quality profile targeting MP3 will stop matching YouTube if you switch, because the file genuinely will not be an MP3.' },
        { title: 'Also in 1.8.15', desc: 'the Wishlist Audit maintenance job would run and clear nothing.' },
        { title: 'Wishlist Audit ran, found nothing, and told you nothing', desc: 'the maintenance job that clears wishlist entries for things you already have. It would run happily and leave downloaded, imported shows sitting on the wishlist. Three separate causes, all ending in a scan that reported nothing.' },
        { title: 'Cleaning something up once made it invisible forever', desc: 'the main one. Approving a finding deletes the wishlist row it names — but the job treated a finding it had already fixed as proof it had reported that title before, and refused to raise it again. So the same show landing back on your wishlist and being downloaded again was never flagged a second time. It worked once per title, then went quiet. Dismissing a finding still silences it for good: "leave this one alone" should stick.' },
        { title: 'Copies your server has but TMDB never matched were not counted', desc: 'the job only recognised something as owned by its TMDB id. A show in your library that never got matched looked un-owned to it — which is precisely the thing you have downloaded, imported, and can see on your server while the audit insists there is nothing to clean. It now also follows the link the wishlist row already carries to the library entry.' },
        { title: 'And it now says when it left things alone on purpose', desc: 'a wishlist row is kept deliberately when the copy you have is below your quality cutoff — that is an upgrade the downloader is still hunting for. Correct, but the log said only "0 new findings", which reads as broken. It now reads "scanned 12, 0 new findings, 12 deliberately left alone".' },
        { title: 'New: Include below cutoff', desc: 'if your cutoff is 4K, or set to "always chase the best", nothing you download ever counts as finished and the audit will never clean anything. Turn this on in the job\'s settings and it flags every owned row regardless of quality. It is off by default because turning it on ends the upgrade hunt for those titles.' },
        { title: 'Nothing here touches your files', desc: 'unchanged, and worth repeating: approving a Wishlist Audit finding removes the wishlist row and nothing else.' },
        { title: 'Also in 1.8.14', desc: 'signing in survives closing the browser — it never did before.' },
        { title: 'Signing in now survives closing the browser', desc: 'it never did. Nothing had ever configured how long a sign-in lasts, so the browser threw it away the moment you closed it. That went unnoticed while the account picker let anyone click straight back into any profile — once 1.8.13 required actually having signed in, it meant redoing the whole Plex link every time you reopened your browser.' },
        { title: 'Thirty days, and it renews as you use it', desc: 'keep using Commissary and you stay signed in indefinitely; stop, and it lapses after a month. Adjustable with security.session_days if you want it shorter.' },
        { title: 'So "Log out" had to start meaning it', desc: 'logging out only forgot which profile you were using — it kept the record of every account you had signed into on that device. That did no harm while everything vanished on browser close. With a sign-in that lasts a month it would have meant logging out on a shared computer still handing the next person your accounts. It now clears the lot.' },
        { title: 'Nothing changes for HTTPS or plain-http installs', desc: 'the cookie is still marked Secure only when you have turned on reverse-proxy mode, exactly as before. Forcing it on a normal home install would stop the browser sending it at all — which is this same bug, permanently.' },
        { title: 'Also in 1.8.13', desc: 'the account switcher listed every profile on the server and let you into any of them; it now shows only accounts signed in on that device. Plus two Manage Profiles fixes.' },
        { title: 'The account switcher showed everyone — and let you in', desc: 'the swap-account screen listed every profile on the server, and clicking one signed you into it. A profile only asked for a PIN if it happened to have one, and an account created by "Sign in with Plex" has neither PIN nor password — so anyone at that screen could walk into any Plex user\'s account. The same was true of any local profile whose owner never set a PIN.' },
        { title: 'It now shows only the accounts signed in on this device', desc: 'and, more importantly, refuses the rest: switching to a profile this browser has not signed in as is turned down by the server, not merely hidden. Signing in — with Plex, a password, or the profile\'s PIN — adds it, and from then on you can swap between them freely.' },
        { title: 'You cannot lock yourself out', desc: 'your admin profile is always listed and always selectable, because an install whose admin never set a PIN or password must not be able to shut itself out. Its own PIN, if it has one, is still required.' },
        { title: 'After updating, everyone signs in once more', desc: 'existing sessions carry no record of what they signed into, so the switcher will show only the admin profile until each person signs in again. Plex users just press "Sign in with Plex" once. That is the upgrade behaving correctly, not a fault.' },
        { title: 'The full profile list is now admin-only', desc: 'it enumerates every account on the server including the Plex username behind each one, so it is no longer readable by anyone who asks. Manage Profiles and Settings → Users still use it.' },
        { title: 'Fixed: Side Access always showed "Music only"', desc: 'in Manage Profiles, editing anyone showed Music only selected no matter what they actually had. The setting was correct in the database and correct everywhere it was enforced — the edit form was simply never handed the value, so it fell back to the most restrictive option. Saving without noticing would have applied that fallback.' },
        { title: 'And you can now make someone an admin', desc: 'a checkbox in the profile editor, where before it needed an API call. It only appears when you are editing someone else, so you cannot demote yourself out of the screen; the last admin still cannot be removed. Ticking it explains that Side Access and page choices stop applying, since admins always have everything.' },
        { title: 'Also in 1.8.12', desc: 'a request needing approval can now reach you on Discord, Telegram or any URL.' },
        { title: 'Get told when someone is waiting on you', desc: 'a request that needs your approval can now send you a Discord message — or a Telegram message, or a POST to any URL you like. Settings → Notifications, add a connection, tick "Needs approval". Without it the only way to find a pending request was to go and look at the page.' },
        { title: 'It covers the Watchlist too — which never had this', desc: 'follows have been landing in an approval queue since 1.6.7, and nothing has ever been able to tell you. If standard users have been following shows, there may be entries waiting for you right now; the Watchlist page marks them "Awaiting approval".' },
        { title: 'Why a new alert rather than the existing one', desc: 'there was already a "Wishlisted" notification, but it fires for everything added — including your own, and everything the hourly jobs add, which can be dozens at once — and it never said who asked or whether it needed you. Subscribing to it to catch requests meant being buried in things you already knew about. The new one fires only for a request that is actually waiting.' },
        { title: 'One message per request, not one per episode', desc: 'asking for a whole season sends a single "Breaking Bad (24 items · asked by Member)", not twenty-four separate pings. And asking again for something already requested stays quiet, so a refreshed page does not re-alert you.' },
        { title: 'Or build your own rule', desc: 'Automations has it as a trigger — "Video Request Needs Approval" — so you can send different channels different things, filter by who asked, or by whether it was the wishlist or the watchlist.' },
        { title: 'Also in 1.8.11', desc: 'a correction: 1.8.9 hid unconfigured search sources for admins only, and standard/Plex users still saw the full row.' },
        { title: 'A correction to 1.8.9', desc: 'that release hid search sources you have not set up — but only for admins. Standard and Plex users still saw the full row. The page asked an admin-only endpoint which of your connections were configured; for anyone else it came back "not allowed", and the page treats an unanswered lookup as "assume everything is configured" and hides nothing.' },
        { title: 'Why it failed that way round', desc: 'assuming everything is set up is the right answer when the lookup fails for an ordinary reason — a moment offline should never leave you staring at an empty picker with no way to search. It is the wrong answer for "you are not allowed to ask", and the page could not tell the two apart. It now asks a question it is allowed to ask.' },
        { title: 'What the new lookup will tell a standard user', desc: 'only whether each of the ten SEARCH sources has credentials — nothing else. No keys, no addresses, no settings, and nothing about your Plex, Jellyfin, slskd, Tidal, Qobuz or Last.fm connections. It is the same thing the row of icons shows them anyway; they can now work it out correctly instead of being shown everything.' },
        { title: 'The Settings page is unchanged', desc: 'the Connections indicator still reads the full admin-only picture. Only the search picker moved, and it moved to a smaller question rather than the admin gate being loosened.' },
        { title: 'Also in 1.8.10', desc: 'a wishlist request from someone without download rights now waits for your approval instead of being fetched unattended.' },
        { title: 'A wishlist request now waits for you', desc: 'the Watchlist has worked this way since 1.6.7; the wishlist did not. A title added by someone without download rights went straight into the hourly automation and was fetched unattended. It now lands as "Awaiting approval" — visible on the wishlist immediately, so the person who asked can see they asked, while nothing goes looking for it until you say yes.' },
        { title: 'Approve or Decline on the card', desc: 'admins see both buttons with the requester\'s name. Approving a show releases every pending episode under it at once. Declining removes the request; nothing re-adds a wish on its own, so it stays gone until somebody asks again.' },
        { title: 'Every route that could fetch it is covered', desc: 'that is the part that matters — one missed path and the automation quietly downloads something nobody approved. The hourly drain, RSS matching, "Search now", "Search all missing" and the YouTube worker all read the wishlist through the same four places, and all four skip anything still waiting. Pressing a different button cannot get round it.' },
        { title: 'Nothing you already had stops downloading', desc: 'approval defaults to granted, so your own wishes, everything your automation added, and every row that predates this release carry on exactly as before. Only new requests from profiles without download rights wait.' },
        { title: 'And a member can still change their mind', desc: 'they can remove their own pending request without needing you — asking and then thinking better of it should not need an approval too.' },
        { title: 'Also in 1.8.9', desc: 'controls that could only fail were hidden, three genuinely open endpoints were closed, wishlist items became removable only by whoever added them, and the search picker stopped showing connections you have not set up.' },
        { title: 'Buttons that could only fail are gone', desc: 'the follow-up to 1.8.8. Standard and Plex users were still being shown a row of controls that answered "not allowed" when clicked — Retry on a failed download, Block release, Clear, the download-history actions, Search now on an individual episode. All of them are hidden for profiles that cannot use them.' },
        { title: 'Three of them were not just cosmetic', desc: 'clearing the finished downloads on both the music and video sides, and every part of the music Import page, were behind no permission check at all. Those are not dead buttons — they work, on shared data. Any signed-in profile could empty your download history or import files into your library. All of them are now checked on the server, which is the part that counts.' },
        { title: 'And cancelling a music download is checked too', desc: 'the music side had the same gap the video side had in 1.8.8: all four cancel routes were open, so a standard profile could stop your in-flight downloads. One of them also re-added the cancelled track to the shared wishlist, so a single call both stopped a download and changed shared state.' },
        { title: 'You can remove your own wishlist items — and only your own', desc: 'the wishlist is one shared list, so until now removing was all-or-nothing: either a profile could clear anyone\'s requests or it could not remove even its own. Each entry now remembers who asked for it. A member can take back what they added; everything else is refused with a reason rather than silently doing nothing.' },
        { title: 'Clear all follows the same rule', desc: 'for you it still empties the tab. For a member it clears the titles they added and leaves the rest, and says so — "Cleared 3 movies you added — 2 from other people left in place". The confirmation says which it is going to do before you commit, since "Remove ALL" would otherwise read like it wipes everyone\'s.' },
        { title: 'Re-adding cannot take over someone else\'s entry', desc: 'ownership is recorded when a title is first added and never reassigned. Otherwise wishing for something already on the list would hand you the other person\'s entry to delete. Items added by your automation belong to nobody and stay yours alone to clear.' },
        { title: 'Manage and Manage Poster are admin-only', desc: 'on a movie or show, these open the metadata editor and the artwork picker. Everything they save was already admin-only on the server, so for anyone else they opened a panel where every save came back refused. Synchronize went with them, for the same reason.' },
        { title: 'The music Import page is admin-only', desc: 'it stages files off your disk and writes them into the shared library. It had been a per-profile page toggle that was ON by default, so every standard and Plex user could see it — while the same page on the video side has always been admin-only.' },
        { title: 'The search source picker only shows what you have set up', desc: 'sources with no credentials were shown greyed out with a "set up in Settings" tooltip — a row of buttons that cannot answer a search. They are hidden now. If you have not configured anything at all the full row comes back, since that is the one time those tooltips are the point.' },
        { title: 'Music wishlist buttons were already per-user', desc: 'worth saying, since it came up: Ignored, Cleanup and Clear All on the music wishlist have always acted only on your own list. The music wishlist is stored per profile, so you never see anyone else\'s tracks to begin with — which is why it needed no equivalent fix.' },
        { title: 'Also in 1.8.8', desc: 'members could start downloads from the wishlist and cancel yours, while being blocked from the one thing they should be able to do — adding to it.' },
        { title: 'Members could start downloads from the wishlist', desc: 'the "Search now" and "Search all" buttons on the wishlist start real downloads, and they were behind no permission check at all — so anyone signed in with access to the video side could set them off. Every other download action was already checked; these two were missed. They now need download permission, like the rest.' },
        { title: 'And they can no longer cancel your downloads', desc: 'cancelling was already checked, but a standard profile you created started with download permission switched ON, so it inherited the ability. That default has changed.' },
        { title: 'New profiles now match their role', desc: 'a new admin can download; a new standard user cannot, until you say so. Previously every new profile could download unless you noticed and turned it off. Existing profiles are left exactly as they are — quietly taking away something someone relies on would be its own kind of bug.' },
        { title: 'Check who has it today', desc: 'the permission is per profile in Manage Profiles. There is also a small script, tools/audit_download_permission.py, that lists every profile and flags any non-admin who can currently start or cancel downloads.' },
        { title: 'Adding to the wishlist works again for everyone', desc: 'the opposite problem: a profile without download permission was blocked from ADDING to the wishlist, so members had no way to ask for anything. Asking is not downloading — a member adds a title and your automation, or you, decides whether it is actually fetched.' },
        { title: 'A clear button on the search fields', desc: 'the Library, Music Library and Purchased search boxes have an × once you type in them. Escape clears too. Whatever filtering the page already does runs exactly as if you had deleted the text by hand.' },
        { title: 'Also in 1.8.7', desc: 'a torrent that had been added stopped reporting as rejected, Manual Search stopped returning nothing until you searched in Prowlarr first, and Grab season now fetches a single season pack.' },
        { title: 'Fixed: a torrent that WAS added reported as rejected', desc: 'the grab said "the torrent client didn\'t accept the release" while the torrent sat there downloading. Commissary worked out the new torrent\'s identity by listing the client\'s torrents before and after and spotting the difference, waiting about five seconds. qBittorrent often needs longer — resolving a magnet, or simply busy — and when the wait ran out a perfectly good add was called a failure.' },
        { title: 'Why that mattered more than the message', desc: 'a grab recorded as failed is never watched, so when the download finished nothing imported it. The file arrived and Commissary did not know it existed. It now works out the torrent\'s identity from the magnet or the torrent file itself, before adding it, so there is nothing to race. Re-adding something the client already has now resolves properly too, instead of looking like a rejection.' },
        { title: 'Fixed: Manual Search finding nothing until you searched in Prowlarr first', desc: 'a first search across many indexers is slow — Prowlarr queries each one and some have to log in — and Commissary gave up after fifteen seconds, the same short limit it uses for quick status checks. Worse, it could not tell a search that timed out from a search that found nothing, so it reported "No matching releases found". Searching in Prowlarr first made its cache answer instantly, which is why that appeared to fix it.' },
        { title: 'Searches now get their own, much longer limit', desc: 'ninety seconds by default, adjustable. And a search that fails now says what went wrong instead of claiming there are no releases — being told nothing exists is worse than being told it was slow.' },
        { title: 'Grab season now looks for a season pack', desc: 'it used to search for and grab every missing episode separately: a dozen searches and a dozen downloads for one season, hard on the indexers, and it often ended up assembling a season from a dozen unrelated releases at different qualities. It now finds one release covering the whole season and grabs that; the import splits it into episodes exactly as before.' },
        { title: 'And it tells you when there isn\'t one', desc: 'if no season pack exists it says so and grabs nothing, rather than quietly going back to downloading episodes one by one. Auto on an individual episode still does that whenever you want it.' },
        { title: 'Also in 1.8.6', desc: 'Manual Search stopped discarding results it had already ranked, release names link to the indexer page they came from, results can be filtered, and manual import can take a whole season folder.' },
        { title: 'Fixed: "Check for out-of-place episodes" threw an error', desc: 'a mistake introduced in 1.8.5. The button reported "src is not defined" and did nothing at all. One edit added a piece of code that used a value, while the edit meant to create that value never saved — so the button referred to something that was not there.' },
        { title: 'And a check so that particular slip cannot ship again', desc: 'the older parts of the interface get no automatic linting, and a syntax check does not catch this kind of fault — the code is perfectly well-formed, it just refers to a name nothing defines. There is now a test that scans for exactly that.' },
        { title: 'Manual import can take a whole season folder', desc: 'the automatic side has unpacked season packs since 1.8.0, but manual import could only take one file at a time — so a pack that arrived any other way had to be placed episode by episode, answering "which show is this?" on every one. Point it at the folder instead and answer once.' },
        { title: 'It shows you what it will do first', desc: 'browsing into a folder with two or more numbered episodes offers "Import this whole folder". The Place dialog then lists every file and the episode it read from each name, before you commit. Each file keeps its own episode number — the dialog only supplies the show.' },
        { title: 'Manual Search was quietly discarding results', desc: 'it kept the best 40 releases and 15 rejected ones, and threw the rest away after they had already been found and ranked. That is why a popular title only ever showed so many. Now 100 and 40, and both are configurable, along with how many results are asked for in the first place.' },
        { title: 'Click a release name to see where it came from', desc: 'the title now links to the indexer\'s own page for that release, when the indexer provides one. Links open in a new tab and are checked before being shown — a tracker cannot use one to run anything inside Commissary.' },
        { title: 'And you can filter the results', desc: 'by name, quality, source, minimum seeders, or only those that meet your quality profile. Filtering happens instantly on results already fetched, and the header always says how many rows are being hidden so a short list is never a mystery.' },
        { title: 'Also in 1.8.5', desc: 'the unattended background jobs stopped re-creating episode rows the clean-up had just removed, and the episode buttons no longer name a database they might not be using.' },
        { title: 'The background passes no longer undo your clean-up', desc: 'the important fix here. Two unattended jobs — the one that matches a show and the one that fills in full episode lists — always took their season numbers from TMDB, whatever a show was actually set to use. On a show using TVDB numbering they would quietly re-create the exact rows the out-of-place check had just removed. The repair looked like it worked, then reverted with nothing on screen to explain it.' },
        { title: 'It errs towards writing, not withholding', desc: 'if that check cannot be worked out for any reason, the episode list is still written. A list that should not have been written is visible and fixable; one that was never written is a silent hole in your library.' },
        { title: 'The buttons no longer name a database they might not be using', desc: '"Re-scan episodes from TMDB" said TMDB even on a show reading from TVDB. All three buttons are now worded generically, and the database in use is named in one place — under the Episode numbering box — rather than repeated in three labels that can drift apart.' },
        { title: 'And the results say which database they asked', desc: '"Checked against TVDB: every episode is filed under a season it lists." Without that, "nothing found" could equally mean "I asked the wrong one", which is exactly what happened before.' },
        { title: 'The duplicate check never asked a database at all', desc: 'it compares your library against itself, pairing episodes by air date. Its description used to blame "your server and TMDB", which was wrong twice over. It now says what it actually does.' },
        { title: 'TVDB episode lists were being read one page deep', desc: 'that endpoint returns the whole series in pages. For a show as long as Bleach — over 400 episodes — later seasons fell past the first page and came back empty, and an empty season reads exactly like "this season has no episodes", so everything downstream quietly did nothing.' },
        { title: 'Manage shows which database is in use, and why', desc: 'under the Episode numbering box: "Using TVDB — it covers 100% of your server\'s seasons (the other is missing seasons 3, 4, 5…)". Auto was previously impossible to check — when a re-scan did nothing there was no way to tell whether it chose what you expected or fell back to the default because a lookup failed.' },
        { title: 'A deeper diagnostic', desc: 'tools/diagnose_show.py --check runs Commissary\'s own resolution against your real API keys and prints what each database returned, the decision and its scores, and per season what the clean-up would remove — flagging any season that came back empty.' },
        { title: 'Commissary now uses the episode numbering your server uses', desc: 'the actual cause of the Bleach problem, found by dumping the rows instead of reasoning about them. TMDB has Bleach as three seasons — specials, the 366-episode original run, and Thousand-Year Blood War. TVDB has seventeen, which is what Plex reports. Commissary always took its season numbers from TMDB, so TMDB\'s "season 2" (the 2022 run) was written on top of the 2005 season.' },
        { title: 'The evidence was exact', desc: 'every season with invented rows was a season TMDB has (0, 1 and 2). Every season with none was one TMDB does not have (3 to 17). Seasons 3 to 16 were untouched the whole time.' },
        { title: 'It is also why Season 17 never filled', desc: 'TMDB has no season 17, so nothing could ever add episodes to the season where your library actually keeps that run. Not a separate problem — the same one. Commissary can now fill it, because it takes the episode list from the database whose seasons match yours.' },
        { title: 'How it decides', desc: 'it scores each database on how much of your server\'s season structure it can actually serve, and only switches when the difference is decisive. A show both agree on — nearly all of them — carries on using TMDB exactly as before. Nothing is rearranged quietly.' },
        { title: 'And you can overrule it', desc: 'Manage on a TV show has an "Episode numbering" box: Auto, TMDB or TVDB. An explicit choice is obeyed even if the automatic guess disagrees, which is the entire point of having it. Re-scan afterwards to apply it.' },
        { title: 'The out-of-place check now asks the right database', desc: 'it was comparing against TMDB, which for a show like this considers your correct season wrong and the invented rows right — exactly backwards, and why it reported nothing to do. It now checks against whichever database owns that show\'s numbering.' },
        { title: 'A correction to 1.8.3', desc: 'that release blamed TVDB and stopped it adding episodes. It was the wrong database: TVDB is the one that agrees with Plex here, and blocking it removed the only source that could fill Season 17 while leaving the real cause running. Reverted.' },
        { title: 'Also in 1.8.3', desc: 'a clean-up for episodes filed under a season they do not belong to, and a read-only diagnostic script (tools/diagnose_show.py) that dumps a show\'s rows — it is what found the real cause.' },
        { title: 'Episodes filed under a season they never belonged to', desc: 'the real cause of the Bleach problem, and it was not what 1.8.2 assumed. TMDB calls Bleach season 2 the 2005 arc; TVDB calls its season 2 the 2022 Thousand-Year Blood War run. Commissary read the season list from TMDB and then asked TVDB for those same season numbers — so TVDB\'s season 2 was written into TMDB\'s, putting seventeen 2023-2026 episodes inside a 21-episode season from 2005.' },
        { title: 'Why it stopped things being found', desc: 'an episode filed under a season number no release uses can never be matched. That is why the missing episodes were hunted for months and never turned up — Commissary was searching for season 2, episode 41, which does not exist anywhere.' },
        { title: 'TVDB now enriches, never invents', desc: 'TVDB is still used, and still valuable — it is often first with titles and synopses for episodes that just aired. It can now fill in details for an episode TMDB already lists. It can no longer decide which episodes exist, because a season number does not mean the same thing in two different databases.' },
        { title: 'A clean-up for episodes already filed wrongly', desc: 'Manage on a TV show gains "Check for out-of-place episodes". It asks TMDB which episode numbers belong to each season and offers to remove rows TMDB does not list. Two clicks, like the other one, and it only ever touches rows with no file and nothing from your server.' },
        { title: 'It refuses if it cannot check', desc: 'if TMDB cannot be reached, it removes nothing at all. An empty answer would make every episode you do not own look out of place, which would be the worst possible time to start deleting.' },
        { title: 'A correction to 1.8.2', desc: 'the duplicate-episode tool from 1.8.2 read this situation backwards for shows like Bleach, and removed correctly-numbered rows instead of the misfiled ones. It never touched a file or anything your server reported, so nothing was lost — but if you ran it on a show whose seasons looked wrong, run the new out-of-place check and then "Re-scan episodes from TMDB" to rebuild the listing properly.' },
        { title: 'Also in 1.8.2', desc: 'every Wishlist card gained a Library box, so a title you do not own yet can be pointed at the right Library instead of always landing in the primary one.' },
        { title: 'Send a wished title to the right Library', desc: 'every card on the Wishlist now has a Library box. Until now a title you did not already own had nowhere to record where it belonged, so anything the automation grabbed landed in your primary Movies or TV folder regardless. Pick a Library and the next grab goes there. Nothing on disk moves.' },
        { title: 'It shows where a title is actually headed', desc: 'not just whether someone has set it. A show already filed under Anime reads "Anime" rather than "Default". The box only appears when you have more than one Library of that kind — with one there is no choice to make.' },
        { title: 'Episodes listed twice under two season numbers', desc: 'reported on Bleach: the newest episodes appeared under Season 2 AND again under Season 17, and the ones being hunted were never found. Plex files that run as S2, TMDB calls it S17 — both are right, and Commissary was storing both as separate episodes. It no longer creates the second copy when your server already has that episode under another season number.' },
        { title: 'A tool for the duplicates you already have', desc: 'the fix above only helps from now on, and a re-scan never removes them. Manage on a TV show now has "Check for duplicate episodes". It shows you what it found — "S17E1 (you have it as S2E1)" — and only removes anything on a second click.' },
        { title: 'It refuses far more than it removes', desc: 'a row only goes if your server has that same episode under a different season number and the air date identifies exactly one of them. Anything ambiguous — a streaming season sharing one date, an episode with no date, two candidates on the same day — is left alone. Nothing on disk is ever touched: these are placeholders for episodes you do not have.' },
        { title: 'Also in 1.8.1', desc: 'search stopped stopping at the first page of TMDB results, typing a year no longer returns nothing at all, and TV shows gained a "Re-scan episodes from TMDB" button for seasons that look short.' },
        { title: 'Search looks past the first page of results', desc: 'it only ever read the first twenty matches TMDB returned, ordered by popularity — so a title sharing its name with something better known was simply unfindable. It now reads further down the list.' },
        { title: 'Typing a year works instead of returning nothing', desc: 'searching "Another World 2025" used to come back completely empty, because the year was sent as part of the title. If what you type finds nothing and it ends in a year, Commissary now searches the name on its own and puts that year\'s match at the top. Titles that genuinely end in a year, like Blade Runner 2049, are unaffected.' },
        { title: 'Re-scan a show\'s episodes from TMDB', desc: 'a new button in Manage for TV shows. Commissary reads a show\'s episode list once and never looks again, so episodes added to TMDB later — a season still airing, a late batch — stayed invisible with no way to ask for them. This reads every season again and tells you how many it added.' },
        { title: 'What that button is NOT', desc: '"Sync show now" checks your Plex/Jellyfin server, so it can never find episodes your server does not have. This one asks TMDB. If a season looks short compared to TMDB, this is the button.' },
        { title: 'Also in 1.8.0', desc: 'season packs now unpack and file every episode instead of stalling after the download, and Manage gained a Library box so a movie or show filed in the wrong place can be corrected.' },
        { title: 'Season packs now actually finish', desc: 'you could already search for and grab a whole season — but it would download and then stop, asking to be imported by hand. Commissary now unpacks it: every episode in the pack is renamed and filed individually, exactly as if you had downloaded them one at a time.' },
        { title: 'A part-full pack is still a win', desc: 'a pack labelled as a full season that only ships some episodes, or one where you already own a few at better quality, imports what is useful and tells you what it did. Samples, extras and trailers are left behind.' },
        { title: 'The hourly automation can use packs too', desc: 'when several episodes of a season are missing it can grab one pack instead of chasing them one by one. Off until you turn it on, in Settings — one pack can be tens of gigabytes and the automation runs unattended.' },
        { title: 'Put a movie in the right Library', desc: 'Manage now has a Library box for movies and TV shows alike, so a title filed in the wrong place can be corrected. New downloads and upgrades for it go to the Library you pick; files already on disk stay where they are.' },
        { title: 'Before you open Commissary to the internet', desc: 'turn on Settings → Security → Require login. Without it Commissary treats anyone who can reach it as the admin, which is fine on your home network and not fine on a public address. Put it behind HTTPS too, and switch on "Trust reverse proxy".' },
        { title: 'Earlier versions', desc: '1.7.2 and 1.7.1 quietened the login screen — it had been firing dozens of requests a minute at parts of Commissary you are not signed in to yet, and now sends the two the sign-in page actually needs — and fixed Sign in with Plex appearing to do nothing with "Require login" on (the panel showing your plex.tv code was hidden behind the lock screen). 1.7.0 was a security release: requests from other websites are refused while you are signed in (with a switch in Settings → Security), a first-run shortcut that could hand out an API key without asking who you were now needs a signed-in admin, the artwork fetcher no longer follows any address it is given, and the Stop button in Server Activity is shown only to admins who could actually use it. 1.6.13 opened Manage for shows not yet in Plex (so Series type and "Also known as" can be set before the first episode arrives) and fixed watchlist posters that would sometimes stay blank, routing that art through Commissary\'s own cache. 1.6.12 added an "Also known as" box on any movie or show for the names releases actually use, feeding the hourly automation, instant-grab and manual searches alike, without ever writing those names to Plex or Jellyfin. 1.6.11 stopped anime and daily episodes being rejected in manual searches — releases with no S01E03 in the name failed while the automation grabbed them fine — and replaced the misleading "Not a single episode" message. 1.6.10 turned Preferred trackers into a pick-list of your real indexers — it stored ID numbers the app never showed you, so typing a tracker name saved as blank — and made manual searches match alternative and original titles the way the automation already did. 1.6.9 compressed everything the browser downloads (3.90 MB of text became 0.64 MB), stopped the page itself being re-downloaded on every visit, cached posters on disk instead of re-fetching them from Plex/TMDB each time, and deferred off-screen images. 1.6.8 replaced typing a full file path in manual import with a folder browser that opens on your download folder. 1.6.7 let users without download rights follow a show — it lands on the Watchlist as "Awaiting approval" until an admin approves — kept the monitor choice they asked for, gave Plex sign-ins the video side with downloads still off, and tightened the endpoints that opened up. 1.6.6 made downloads land in the Library the title actually lives in (an Anime episode was saving into the standard TV folder), stopped releases carrying a work\'s full colon-subtitle name being rejected as wrong titles, gave new wishlist titles their own Library, stopped saving Libraries from deleting the YouTube folder, and scoped the library filter dropdowns to the tab you are on. 1.6.5 stopped Library tabs reading as duplicates (All Movies / All TV / All Shows), gave each per-Library tab a count, and fixed Place this file never finding shows. 1.6.4 added Preferred Groups and per-Library preferred trackers, moved the Wishlist/Download History Library filters into the tab strips (they had rendered blank and only worked for admins), gave the dashboard Library widget one tile per Library with its own disk usage, and fixed the app version having been stuck at 1.6.1 since 1.6.2 — which had also been silently hiding the release notes. 1.6.3 added manual import for any on-disk file, fixed fansub-style anime releases being rejected as title mismatches, and root-caused dashboard customisation being dead on the Video side. 1.6.2 made unattended grabs respect each title\'s own Library. 1.6.1 fixed torrent/usenet downloads stuck at 100% when the release was a single bare file. 1.6.0 folded in 38 fixes from upstream SoulSync (through 3.1.8), led by a security fix for indexer URLs leaking to the browser. 1.5.0 removed the Soulseek chat feature and the Support button, and gave Plex a stable device identity. 1.4.0 let every user drag and resize their own dashboard cards, and accepted more than one completed-downloads folder. 1.3.2 made your video Libraries drive the health checks, recycle bin, moved-file resolver and naming-repair job. 1.3.1 added collapsible albums in Purchased. 1.3.0 extended the standard-user policy to the sidebar. 1.2.0 let admins choose which dashboard cards standard users see. 1.1.0 brought the Purchased page. 1.0.0 was this fork\'s baseline, carrying upstream SoulSync 3.1.5.' },
    ],
};

// ═══════════════════════════════════════════════════════════════════════════
// VERSION MODAL — curated highlight reel
// ═══════════════════════════════════════════════════════════════════════════
//
// `WHATS_NEW` above is the per-version detailed log used by the "What's New"
// helper-popover panel — short one-liners, internal page links, every entry
// shown on every browse-back through versions.
//
// `VERSION_MODAL_SECTIONS` (this block) is the curated highlight reel shown
// when the user clicks the version button in the sidebar. It's NOT a
// mechanical view of WHATS_NEW — it's editorial curation: bigger-picture
// sections, bullet-list expansions, optional "usage" hints at the bottom.
// Some sections aggregate across multiple WHATS_NEW entries ("Recent Fixes",
// "Earlier in v2.3"); some don't have a 1:1 WHATS_NEW counterpart at all.
//
// Both consts live here so a release editor only opens one file. At release
// time:
//   1. Add the per-version block to `WHATS_NEW` (one entry per shipped item).
//   2. Promote any items worth a modal-section into `VERSION_MODAL_SECTIONS`
//      at the top of the array (latest highlights lead).
//   3. Roll older sections down or merge them into a "Recent Fixes" /
//      "Earlier in vX.Y" aggregator section as they age out of the spotlight.
//
// Section shape: { title, description, features: [bullet strings],
//                  usage_note?: 'optional hint shown at the bottom' }
const VERSION_MODAL_SECTIONS = [
    {
        title: "2.0.9: a quality profile per Library",
        description: "Profiles were per-title only, so \"everything in my 4K Library is judged at 4K\" had to be repeated once per title, forever - and a title the library had never seen had nowhere to say it at all.",
        features: [
            "each Library carries its own default quality profile, set once in Settings - Libraries, for film and TV Libraries alike",
            "a title your library has never seen is now judged by the Library it is being sent to, instead of always falling back to the global Default",
            "that is the first grab - the one that decides what actually lands on disk - so it was the worst possible moment to have no opinion",
            "per-title profiles still win: the order is the title's own profile, then its Library's, then the global Default",
            "the per-title picker now names what \"Default\" will really resolve to, and updates when you move a title to another Library",
            "deleting a profile releases any Library using it rather than leaving it pointing at nothing",
        ],
        usage_note: "Nothing changes until you set one - every Library starts on \"no Library default\", which is exactly today's behaviour. Set profiles up in the Quality section first, then pick one per Library. A show or film with a profile of its own is untouched.",
    },
    {
        title: "2.0.8: the video side stops being silent",
        description: "No features, no behaviour changes. Two silences closed, both found while trying and failing to explain a show that had been filed as the wrong series - the log simply did not contain the answer.",
        features: [
            "the video import code had NO logging at all - not one line in the file - so nothing ever recorded where a downloaded episode or film was placed, while the music side has logged its destination on every import for years",
            "every video import now logs its destination, and whether the name came from the title's library entry (with the ids and year used), an existing copy on disk, a manual placement, or the download request alone",
            "that last case is the one that creates a folder your media server has to guess the identity of - now visible as it happens rather than a week later",
            "a refused grab said only 'the torrent client didn't accept the release' - logged 324 times for one title in eight days while the download-client code logged nothing at all",
            "it now names what was handed over: a magnet, a .torrent URL, or an empty one. Those fail for different reasons and need different fixes",
            "and names the likeliest cause - a client that takes a magnet and returns nothing is usually saying the torrent is ALREADY in it, so every retry is rejected as a duplicate forever",
        ],
        usage_note: "Nothing to configure and nothing changes about what gets downloaded or where. If a grab has been quietly retrying for days, the log will now tell you what it tried and what the client said - check your download client for a stalled or errored copy of the same release first.",
    },
    {
        title: "2.0.7: four upstream fixes, each verified here first",
        description: "Commissary forked from SoulSync 3.1.5 and has diverged, so most of what lands upstream either does not apply or was already solved differently. These four were confirmed as real, present faults in this code before being taken.",
        features: [
            "a magnet was preferred over the real .torrent when an indexer offered both - a magnet gives your client an info-hash and nothing else, and one that cannot find the swarm parks on 'downloading metadata' forever",
            "the magnet is still carried alongside and used the moment the file handoff is refused, so this cannot lose a magnet that would have worked in a split setup",
            "a stalled download's thirty-minute clock was kept in memory, so every restart wiped it - the longer something was stuck, the more restarts it survived and the LESS likely it was ever caught",
            "that clock also never noticed a download that FINISHED but whose file could not be found: it sat at 100% forever, and now says it is a path problem rather than 'no progress'",
            "a torrent re-checking itself briefly reports a lower percentage, which used to count as movement and renew the grace period",
            "Quality Check upgrades failed with 'No matched track in finding' every time - the handler was gated on a library link the scanner deliberately leaves empty, and a database refresh orphaned findings written before it",
            "folders differing only in capitalisation became two albums: two real directories on Linux, and on Windows/macOS a recorded path that is not how the folder is spelled",
        ],
        usage_note: "Nothing to configure. The folder fix steers new writes only - two folders that have already split stay split until a Reorganize moves their files together. Filenames are never case-folded, because two tracks differing only in case are two different files.",
    },
    {
        title: "2.0.6: Opus downloads stop reporting no quality at all",
        description: "507 of 521 YouTube downloads in the reporting library had recorded no quality and no bitrate, while the M4A ones beside them labelled fine. The tag library cannot read a bitrate out of an Opus file - the field is not in that format's header - and the code asked for it anyway, swallowed the error, and returned an empty string.",
        features: [
            "three answers now, in order: the tag header, then the source's own pre-download claim while the file is still that format, then size divided by playing time",
            "the claim is never copied onto a re-encoded file - if you convert downloads to MP3, the original stream's bitrate says nothing about what was written, and a confident wrong number is worse than a blank one",
            "an Opus bitrate is an average by nature, so it is now marked as one instead of quoted like a constant - and library rows that stored nothing for Opus get that average filled in",
            "this logic existed twice, in the import path and inside the web server, and the copies had drifted - both wrong about Opus, only one aware of leftover download containers or an .ogg holding Opus. There is one now",
        ],
        usage_note: "Nothing to configure, and nothing changes about what gets downloaded or at what quality - only what Commissary knows and reports about it. Existing library rows keep their bitrate; the estimate fills in rows that had none rather than overwriting anything.",
    },
    {
        title: "2.0.5: three fixes carried over from upstream",
        description: "Commissary forked from SoulSync 3.1.5 and has diverged a long way, so most of what lands upstream either does not apply or was already solved differently here. These three were checked against this code first and found to be real, present faults - taken from upstream 3.2.1 and 3.2.2.",
        features: [
            "a library scan could slowly corrupt where your files are recorded as living: when a Subsonic/Navidrome server left the path out of a response, Commissary invented a bare filename from the title and the next scan wrote it over the correct value - damage that accumulated with every pass (Plex libraries were never exposed)",
            "searching for 'Would've, Could've, Should've' returned other 'Should've' songs and buried the real one, because the broad fallback search asked for the words WITH their commas attached - a file tagged without them matched on one word and scored no better than anything else sharing it",
            "punctuation now comes off the ends of a search word only, so N.W.A, P!nk and AC/DC stay intact - and the same applied to artist names, which is not exotic either (Crosby, Stills & Nash)",
            "'replace the original file' did nothing when you imported the staged file by hand - the instruction was only ever read by the automatic importer",
            "it now refuses rather than guesses: a quarantined import never deletes the original, an unknown landing place keeps it, and re-identifying onto the release a track was already in deletes nothing",
        ],
        usage_note: "Nothing to configure. Paths already damaged by an earlier scan are not repaired by this - it stops the erosion rather than undoing it; a Navidrome library that has been drifting may want one Refresh with the server healthy so the correct paths are re-read.",
    },
    {
        title: "2.0.4: downloads that finish, and upgrades that actually replace",
        description: "two separate faults behind the same complaint - that re-downloading a track for better quality never seems to finish, and never overwrites what is already there. One stopped short-titled downloads being found at all; the other meant a format upgrade could only ever add a second copy.",
        features: [
            "a finished download was matched by comparing a bare title against a filename that still had '.flac' on the end - Kanaria's 'Dec.' scored 0.839 against a 0.85 threshold and was reported missing while sitting in the download folder",
            "the extension is a fixed few characters, so only SHORT titles were affected: anything under about fifteen characters could never be found, which is why it looked random",
            "separately, every replace decision tested for a file at the exact destination path, extension and all - and an upgrade IS a change of extension, so .opus becoming .flac never matched and the new file was filed beside the old one",
            "the reporting library had nine such pairs, including '01 - STAY.opus' next to '01 - STAY.flac' - identical names, the extension alone keeping them apart",
            "the copy you already own is now recognised whatever container it is in, and replaced",
            "it looks in ONE folder - the one the incoming file is going into - so a track downloaded as a single can never overwrite the copy you own inside an album",
            "and it would rather leave a duplicate than delete the wrong file: a leading track number must provably match, and if two files could both be the track, neither is touched",
        ],
        usage_note: "Whether you get the upgrade depends on 'replace lower quality' in your quality profile. With it off, a better copy in a different format is now placed alongside rather than discarded - so this release can upgrade your library or leave it exactly as it was, and never deletes something it previously kept. Duplicates already on disk are left alone; Commissary will not remove files it did not just replace.",
    },
    {
        title: "2.0.3: new episodes stop being filed as a different show",
        description: "reported as 'Kitchen Nightmares (US) keeps downloading new episodes as Kitchen Nightmares: Road to Super Bowl LIX'. The download was right every week; the folder it went into was not. Commissary named that folder from the request that started the download, while the rest of the app names it from the show's library row — and for an airing show those disagree.",
        features: [
            "the year came from the EPISODE's air date, not the year the series began: Futurama (1999) was written as Futurama (2026)",
            "the TMDB id was never filled in for an episode, so a template asking for it wrote an empty (tmdb-) and gave the server nothing to match on",
            "the TVDB id was filled with whichever id the download carried - a TMDB id or an internal row number, never a TVDB one - asserting an identity that was false rather than missing, which the server then believes",
            "the result is a second folder beside the show, which Plex or Jellyfin scans as a brand-new series and has to guess the identity of - in the library this was diagnosed against, 36 of the last 37 episode grabs had done it",
            "it is also why the rename-before-import step added in 2.0.2 could never win: it corrected the existing files while the import kept creating a new folder next to them",
            "a show you do not own yet now arrives carrying the TMDB id from its request, so the folder that decides the show's identity forever is identifiable from the first episode",
            "ALSO: $tmdbid and $imdbid now work in episode templates (they had only ever worked in the {TmdbId} brace form), and a hand-placed file is named the same way an automatic one is",
        ],
        usage_note: "Nothing to configure. Folders already split are deliberately left alone - this stops new ones appearing rather than rearranging a library you have curated. To bring an existing show onto one naming, use Manage → Rename on that show, then remove any phantom series your media server invented from its own library.",
    },
    {
        title: "2.0.2: lock a title so automatic downloads cannot touch it",
        description: "a new 'Lock automatic edits' switch on shows, movies and individual seasons. While it is on, nothing unattended may write to that title - it stops at placement and says why, instead of changing what you already have.",
        features: [
            "protects against the real sequence: a release that names no episode is filed wherever the request claimed, and if the request was wrong and the release scores better, your existing file is deleted and replaced with the wrong episode",
            "blunt on purpose - a replacement, an upgrade and a brand-new episode are all refused, because a release that mis-identified itself cannot be trusted about being new either",
            "season locks are narrower than show locks: seal a finished season while the airing one keeps downloading, and a pack spanning both imports only the half it is allowed to",
            "manual import still works - that is the check the lock exists to demand, not something it should block",
            "a refused release is not blacklisted: being aimed at locked content says nothing about the release, so it stays eligible for the title it actually belongs to",
            "ALSO: a title's existing files are now renamed to the current naming template before a new episode is imported into it, so changing a template no longer splits a show across two folders (Settings → Library, on by default, scoped to that one title)",
            "a rename that cannot be done is logged and the import proceeds anyway - a tidy-up should never cost you a download",
        ],
        usage_note: "Shows and movies: Manage → Lock automatic edits. Seasons: the switch above the episode list on the season you are viewing. Admin-only, and off everywhere until you turn it on - nothing changes for an existing library. A refused download waits on the Downloads page with its reason, and can be placed by hand from there.",
    },
    {
        title: "2.0.1: anime searches stop coming up empty",
        description: "reported against '[SubsPlease] Oh Boy, Was I Wrong About Her - 07', rejected as having no episode number on both the automatic and the manual search. Fansub groups number episodes straight through instead of using S01E07, and Commissary could already read that — it just had to be told which number to look for, and three separate things stopped that reaching it.",
        features: [
            "a brand-new show could never be told: the number was worked out only for shows already tagged as anime, and only by counting episodes already in your library — a show you just started following has neither, so the FIRST episode of every new anime was the one download that could never match",
            "for a first season the answer needs no library at all — episode 7 is absolute number 7, for every show ever made",
            "Soulseek searches threw the answer away: the results arrive in batches over about a minute, and those follow-up requests were judging every release without it (torrent searches return in one shot and were unaffected)",
            "still cautious on purpose — the number can only ever accept a release, never reject one, so beyond season one it is used only for shows you have actually marked as anime",
            "and the release name is readable: two lines instead of one, with the full name on hover (tracker results had spent that tooltip on 'open this on the indexer')",
        ],
        usage_note: "Nothing to configure, and nothing to re-tag. If a show has been sitting on your wishlist failing to find anything, the next hourly run should pick it up; a manual search will find it now too. Marking a show as anime (Manage → Series type, or the per-Library default from 1.9.23) still helps for later seasons, where the absolute number genuinely cannot be derived without it.",
    },
    {
        title: "2.0.0: SoulSync is now Commissary",
        description: "a rename, and nothing else. This fork branched from SoulSync 3.1.5 and has diverged steadily since — it was overdue a name of its own, and a bug tracker of its own. No behaviour changed in this release.",
        features: [
            "the app, its window title, its PWA entry and every screen inside it now say Commissary",
            "the published image is ghcr.io/thymrman/commissary — the one thing you have to change on upgrade",
            "in-app bug reporting and the Copy Debug Info footer point at this fork's issues instead of upstream's",
            "your Docker volume, compose service, config path and SOULSYNC_* environment variables are UNCHANGED — renaming those would point a working install at an empty database",
            "the value stored against standalone library rows is still 'soulsync', so nothing you have imported needs re-scanning",
            "your Navidrome player entry is still called SoulSync, deliberately — renaming it would have registered a second player without your 'Report Real Path' setting and broken stream paths",
            "the README now describes this fork rather than upstream: what changed since 3.1.5, one manual image channel instead of upstream's two, and a single-branch workflow",
        ],
        usage_note: "Edit the image: line in your docker-compose.yml to ghcr.io/thymrman/commissary:latest, then docker-compose pull && docker-compose up -d. On Unraid, change the container's Repository field to the same. Everything else — config, database, libraries, profiles — carries over untouched; there is no migration step.",
    },
    {
        title: "1.9.23: Anime and TV stop leaking into each other",
        description: "diagnosed against your real video_library.db. Nothing was mis-filed at rest — the bug fires exactly once per show, on its FIRST download, and the library scan then makes that mistake permanent.",
        features: [
            "a show you do not own yet had nowhere to record which Library it belongs to — the watchlist had no such field, so every new show's first grab went to the primary TV Library",
            "new Library button on followed shows you don't own yet (Watchlist → Shows): pick the Library before the first episode is ever grabbed, which also moves anything already queued and the show row, so a title can't be split across two shelves",
            "the button names the Library it's set to rather than only offering to change it, and 'Default (primary)' is a real option — picking it clears a choice you made earlier",
            "hidden where there's no decision left: owned shows (already filed; their detail page moves them for real) and installs with fewer than two TV Libraries",
            "all nine wishlist-creating paths now resolve the Library themselves (only the manual add ever passed one; the airing automation — the one that matters — did not)",
            "new per-Library 'Shows here are…' default series type: 565 of the 571 shows in your Anime library had none, so their releases were searched as standard S01E01 instead of by absolute number",
            "falling back to the primary Library is now logged by name instead of being silent",
        ],
        usage_note: "Set 'Shows here are… anime' on your Anime library in Settings → Libraries and save — it applies to every show already there that has no type of its own, immediately. Shows already filed on the wrong shelf are not moved: this stops it happening again, and a show's Library can be corrected from its detail page. The new Watchlist button is admin-only, matching the gate the endpoint already sits behind.",
    },
    {
        title: "1.9.22: four more fixes adapted from upstream 3.2.0",
        description: "the remaining tier of that release, each one checked against this fork's code before being taken. Four of the eight were already fixed here or never applied; one was left deliberately.",
        features: [
            "a manual library match to a deleted file no longer makes the track vanish — three call sites treated the match row as proof the file existed, and together they made the song un-downloadable, un-wishlistable and un-re-addable",
            "a stale match id is now re-resolved via the stored file path instead of discarded, so a media-server metadata refresh doesn't cost you the link",
            "album and track names starting with a dot no longer become hidden folders (the sanitiser trimmed trailing dots for Windows, never leading ones for Unix) — video titles too",
            "AcoustID now sorts results best-first instead of reading position zero, and declines anything below 0.80 confidence rather than writing an uncertain guess into your tags",
            "POST /api/wishlist/process no longer 500s on every call — it passed its own setup helper an argument that helper never accepted",
        ],
        usage_note: "Nothing to configure. If a track disappeared after you deleted its manually-matched file, it can be wishlisted again now. Not adapted: upstream's fix for FLAC arriving under an MP3-only profile — our quality fallback deliberately ignores format when nothing matches your ranked targets, and has its own on/off switch, so that one is a preference rather than a bug. Say if you'd rather it constrained format too.",
    },
    {
        title: "1.9.21: block an artist from the track in front of you",
        description: "the block action, its API, its table and its hover styling had all shipped — the button that would let anyone press it never did, so the function had zero call sites and the feature was unreachable.",
        features: [
            "hover any Discover track and a 🚫 appears: one click blocks that artist from every discovery playlist",
            "wired into both track renderers — the mix modals (Hidden Gems, Discovery Shuffle, Popular Picks, Fresh Tape, The Archives, Last.fm, ListenBrainz) and the decade / genre browsers",
            "the blocked artist's rows vanish from the list you are looking at, not just from the two sections that happen to reload",
            "tracks with no identified artist get no button rather than one that blocks the literal string \"Unknown Artist\"",
        ],
        usage_note: "Blocking is a hard filter — the artist is removed from discovery entirely, and it does not otherwise influence ranking. To steer the recommendations themselves, use Settings → Discovery → adventurousness. Blocked artists are still managed (and unblocked) from the 🚫 panel in the Discover header.",
    },
    {
        title: "1.9.20: downloaded songs now reach the playlist they were downloaded for",
        description: "a sync matches your library at that moment, writes the server playlist, and only then hands the leftovers to the wishlist to download — so the tracks land minutes after the playlist was already written, and nothing went back for them. The post-download chain ended one link early.",
        features: [
            "a finished download already triggered a media-server scan, and a finished scan already triggered a library database update — but nothing then re-synced the playlist, so the tracks stayed missing until matched by hand",
            "new 'Auto-Sync Playlists After Database Update' automation: the moment newly imported tracks become matchable, every playlist whose last sync came up short is re-synced",
            "deliberately narrow — a playlist that already matched in full is left alone, and a playlist that has never been synced is not touched at all",
            "a sync whose result is identical to what is already on the server no longer deletes and recreates the Plex playlist (which re-keyed it and churned a backup copy every time); differing membership or order still rewrites",
            "the ERROR logged on every successful playlist creation ('Must include items to add when creating new playlist') is now debug — the retry beside it always succeeded, and it was sitting in app.log next to the real playlist problems",
        ],
        usage_note: "Nothing to configure — the new automation is created enabled, alongside the two existing post-download ones on the Automations page. If a playlist was already left short, it will fill in after the next download finishes; or re-sync it once by hand to catch up immediately.",
    },
    {
        title: "1.9.19: fixes adapted from upstream 3.2.0",
        description: "upstream shipped ~650 commits, most of it a React rewrite of pages this fork has customised heavily — that part is not adaptable. These are the four backend fixes that are, each verified as still broken here before being taken.",
        features: [
            "album torrents no longer stall for hours then lose the file — six faults behind one symptom: the magnet was preferred over the .torrent, your stall timeout was never consulted for albums, no seeder floor existed, and a stalled torrent was left running in your client to be re-grabbed as a duplicate",
            "a dead release now falls back to the per-track flow instead of ending the whole album batch, and there is a new minimum-seeders setting (default 1) that only drops what is positively known dead",
            "the \"no audio files found\" at the end of a completed torrent: staging now asks qBittorrent where the release actually landed, and a single-file torrent stages that file rather than sweeping the shared download root",
            "settings could vanish after a crash — an unreadable config row was mistaken for an absent one, and the absent path writes defaults over your real settings; absence must now be proven first, and a corrupt row is copied aside rather than replaced",
            "saving settings is one database write instead of one per field, and the fallback config file is written atomically so a crash cannot leave it empty",
            "idle enrichment workers stopped re-counting the database every 2 seconds — measured 5,400 scans down to 360 over a ten-minute idle session",
            "TV shows gained the TMDB-id index movies have always had: a 20x faster lookup where every discover rail and calendar check used to scan the whole table",
        ],
        usage_note: "Nothing to configure. The new minimum-seeders setting lives in Settings → Downloads and defaults to 1; set it to 0 to switch the check off.",
    },
    {
        title: "1.9.18: the corner buttons no longer cover Rename Files",
        description: "Server Activity, the notification bell and the Interactive Help button float above everything in the bottom-right — right up until something else owns that corner.",
        features: [
            "the video Rename Files panel is pinned to the right edge for the full height, so those buttons landed on its Preview and Apply controls",
            "they now step aside while it is open, and come straight back when it closes",
            "hidden rather than re-stacked: the panel is opaque, so \"behind it\" and \"gone\" look the same, and this is what the two existing cases already did",
            "Server Activity had never been added to those two older rules, so it kept covering the Now Playing modal and the download-missing modal too — fixed in the same pass",
            "a test now fails if a new corner button, or a new full-height surface, is added without joining all three rules",
        ],
    },
    {
        title: "1.9.17: manual search from the download modal",
        description: "the Download Missing Tracks modal could only hand a track to the automatic cascade or drop it on the wishlist. Now you can pick the file yourself without leaving it.",
        features: [
            "a 🔎 Manual Search button beside Add to Wishlist opens the multi-source picker for the ticked track",
            "every configured source is searched at once and the candidates are listed for you to choose from",
            "one track at a time on purpose — the picker is a per-track decision, so with several ticked it says so instead of quietly searching whichever came first",
            "both footer buttons read the ticked rows through the same code, so they can never disagree about what you selected",
            "the picker's search box now opens pre-filled with the artist and title it is about — it used to sit empty under a header naming the exact song, with Search greyed out telling you to type at least 2 characters",
            "still fully editable: being able to change the query is the whole point of a manual search",
        ],
    },
    {
        title: "1.9.16: a replacement download could hang its whole album",
        description: "reported as \"music download replacements seem to get stuck in a Downloading state\". When a downloaded track fails its integrity check, Commissary goes back for a better copy — and one of those replacements could quietly finish without telling the batch it belonged to, which holds the entire album open.",
        features: [
            "from the reported log: a 9-track album, three tracks retried on a duration mismatch, and sixteen seconds later one worker slot leaked and never came back",
            "\"reported=3, actual=2\" every pass for 80 seconds, then \"all 9 task(s) finished but the batch never completed\" — a batch cannot finish until its active count reaches zero",
            "post-processing has a dozen exits and four of them deliberately don't finish the track, because it is going around again; that only worked while every exit remembered which kind it was",
            "a hand-off now has to be declared, and every other way out releases the slot exactly once — including routes that never return normally",
            "if a future change ever leaks one anyway, the log names the track and the batch still finishes instead of hanging",
            "the 90-second stall detector never ran once in 41,902 lines: it bailed out for any track with no recorded source, which is exactly the state a replacement passes through",
            "so an ordinary stalled download was rescued in 90 seconds while a stalled replacement was invisible forever — both are now on the same timeout and retry ladder",
        ],
        usage_note: "Nothing to configure. An album that looks stuck part-way should now finish or fail on its own rather than waiting on the 5-minute healer.",
    },
    {
        title: "1.9.15: Naming Conformance found nothing after a naming change",
        description: "reported as \"changed the naming scheme but the tool finds no episodes that need renaming\". Two separate faults, and both of them looked exactly like a library that already conforms — \"0 findings\" was the only thing the tool could say either way.",
        features: [
            "the job stood down completely on any template naming a token it could not work out for an existing file, and {Custom Formats} was on that list — which is in the TRaSH scheme this app installs with one click, so adopting the recommended naming silently switched the tool off",
            "custom formats are matched against a release name, and a library file still has one, so they are now worked out from the file's own name and your format definitions — a format whose terms are not in the name is never invented",
            "the second fault could have cost you something: this job's query never got the widening its sibling gained in 1.9.13, so episodes carried no series year at all and neither scope carried audio codec, channels or dynamic range",
            "that meant Naming Conformance computed a SHORTER name than Rename Files did for the very same file, and approving the \"fix\" would have stripped the year, the audio detail and the release group off disk",
            "both paths now render an identical name, pinned by a test that runs them against one file",
            "for the few tokens that really do only exist at import, the finding is now raised as a warning naming them, instead of the file being hidden from you",
        ],
        usage_note: "If you changed your naming scheme and the tool reported nothing, run it again — it will report properly now. Findings are still a preview: nothing is renamed until you approve it.",
    },
    {
        title: "1.9.14: a video naming template would not stick",
        description: "reported as \"Library video naming templates do not save after leaving the page\". Typing one and clicking away did save — but the two controls that fill the box for you did not, because a value written by code raises no change event, and that event was the only thing that triggered a save.",
        features: [
            "clicking a token to insert it now saves, instead of showing you a template that was gone on the next page load",
            "so does the TRaSH preset — it used to say \"click away to save\", which could not work for the same reason",
            "several tokens clicked in a row are one save, not one each",
            "opening Settings → Library fires ~a dozen requests at once; this one was measured landing 924ms later and writing the stored template back over the box, so anything typed in that window disappeared",
            "a response that loses that race no longer touches a field you have edited",
            "a save can no longer fire before the form has loaded — which could write blank checkboxes over your real post-processing settings",
            "a save that fails now says so, instead of reporting success while the library keeps naming files the old way",
            "and \"Reset to the standard layout\" now restores the standard layout — it had drifted into switching OFF the NFO and artwork sidecars, and into resetting the disk-space floor that the music side shares",
        ],
        usage_note: "Nothing to configure. If you set a naming template before and found it reverted, set it again — it will hold now.",
    },
    {
        title: "1.9.14: HiFi stops re-dialling instances it already knows are down",
        description: "the public HiFi instances are volunteer-run and outages are normal — but Commissary kept no memory of them, so every search walked all seven hosts and paid the full timeout on each. One 12-hour log: 4,094 \"all instances exhausted\" errors and ~23,500 warnings, which was 47% of its errors and 80% of its warnings.",
        features: [
            "each instance gets a cooldown after it fails and is skipped without opening a connection until that elapses",
            "when every instance is cooling, HiFi is skipped instantly and the search falls through to your other sources",
            "measured on a fully dead pool: the first search tries all seven and learns, the next ten make no network calls at all",
            "the cooldown starts at a minute and doubles for a host that keeps failing, capped at fifteen — then one probe, and a single success clears its record entirely",
            "editing your instance list or hitting Restore Defaults clears every cooldown at once, so your change takes effect immediately",
            "a whole-pool outage is one warning per cooldown window saying how long HiFi will sit out, instead of thousands of identical lines burying it",
            "the HiFi status endpoint reports which instances are cooling and for how long, so a skipped source can say why",
        ],
        usage_note: "Nothing to configure. If HiFi shows as unavailable and recovers a minute later, that is the breaker doing its job — it is not retrying a host it has just watched fail.",
    },
    {
        title: "1.9.13: a rename could strip detail out of a filename",
        description: "reported as a short variable list in the Rename Files picker. The list was the visible edge: only the import path knew the new {Token} values, so for a file already in your library the rename preview and the Naming Conformance job computed its name without the audio, dynamic range and release group.",
        features: [
            "conformance would flag a correctly-named file as wrong, and approving that fix would rename it to the shorter version — deleting that detail from the name",
            "renames and the conformance check now read the audio codec, channel layout and dynamic range the scan already recorded, and recover the release group and edition from the file's current name",
            "for the same file, importer, rename preview and conformance job now produce identical results",
            "a few tokens only exist at import (bit depth, audio languages, custom formats, original release name) — if your template uses one, conformance stands down for those files and logs why, instead of proposing a lossy rename",
            "the picker now offers the same vocabulary as Settings — 43 entries for a show — grouped into Variables and Sonarr/Radarr tokens, each with a description and this title's own value",
            "import-only tokens are shown dimmed with an explanation rather than hidden",
        ],
        usage_note: "If you adopted a {Token} scheme in 1.9.12 and ran Naming Conformance, check its findings before approving any that predate this release — they were computed against the shorter name.",
    },
    {
        title: "1.9.12: Sonarr/Radarr naming — paste a TRaSH scheme and it works",
        description: "video naming templates now understand {Token} names alongside the existing $variables, so a format string copied out of the TRaSH guides renders exactly as documented. Nothing about your current naming changes unless you choose a new scheme.",
        features: [
            "one-click button for the TRaSH recommended scheme, and a browsable list of every token — 24 for movies, 31 for episodes — that inserts at your cursor",
            "optional groups are what make those schemes work: {[Quality Full]} renders [WEBDL-1080p] or vanishes entirely, brackets included, so nothing leaves empty [] or a trailing dash behind",
            "{season:00} zero-pads, {Episode CleanTitle:90} caps a length, and token names ignore case and spacing — the guides mix 'Mediainfo' and 'MediaInfo' in a single line",
            "new file facts read by ffprobe: audio channels, video bit depth, audio languages and the real dynamic range type (DV / HDR10 / HLG) — from the container, never from what the release name claims",
            "edition detection (Directors Cut, IMAX, Extended) and {Custom Formats} are wired in",
            "defaults untouched and every $variable still works; the two styles can be mixed in one template",
            "the example under each template box is now rendered by the real naming code instead of a JavaScript copy of it, so the preview cannot drift from what lands on disk",
        ],
        usage_note: "Settings → Library Organization. Load the preset, watch the example update, then click away to save. {Preferred Words} is the one guide token not supported — Commissary has no equivalent setting, so it is left visible rather than silently dropped.",
    },
    {
        title: "1.9.11: the wishlist stops retrying the same failures forever",
        description: "a retry ladder has existed for a while — repeated failures earn a growing cooldown instead of a search every cycle. A 12-hour log shows it never engaged once, because re-adding a failed track quietly created a duplicate row that no attempt was ever recorded against.",
        features: [
            "the duplicate looked freshly added on every pass, so it was always \"due\" and could never earn a cooldown",
            "the cost: 34 files downloaded and quarantined again and again — one of them 132 times — each cycle re-fetching a file that had already failed its integrity check",
            "a re-add now recognises a track it already holds; the same track from a genuinely different album still gets its own entry, which is what that mechanism was for",
            "duplicate rows already sitting in your wishlist are swept once on upgrade",
            "separately, the batch self-check counted every finished track as an \"orphaned task\", declaring healthy download batches broken every 30 seconds — 1,499 such warnings across 31 batches in one log",
            "it now looks for the two real faults: all tasks finished but the batch never completed, and a queued task whose record has vanished",
        ],
        usage_note: "Nothing to configure. A track that keeps failing now goes quiet on its own — 4 hours, then a day, then weekly — and the Failing filter plus a manual search stay the way to push one through.",
    },
    {
        title: "1.9.11: Deezer could die silently and stay dead",
        description: "the gateway session token was fetched once at login and cached for the life of the app. When Deezer expired it, every download failed from that moment until a restart — one log showed 669 consecutive failures over half a day, roughly 20 an hour, all the same rejected token.",
        features: [
            "the client now recognises that specific rejection, renews the session from your saved ARL, and retries the call",
            "a renewal that fails drops the source out of the chain instead of leaving the connection light green on a session that cannot download",
            "the real reason travels with the failure — the error used to read \"impl returned None\" while the only line naming a cause was a warning further up",
            "concurrent downloads that hit the same expiry share one renewal rather than firing a login each",
            "if your wishlist has been failing the same tracks every run and never shrinking, this is the cause: Deezer sits first in the chain, so everything failed and went straight back on the list",
        ],
        usage_note: "Nothing to reconfigure — it renews from the ARL you already saved. If the renewal itself fails, the log says so plainly and asks you to re-enter the ARL in Settings.",
    },
    {
        title: "1.9.11: a show is identified now, not guessed at",
        description: "a wishlist grab could fetch exactly the right episode and then have its show matched to an unrelated title — because when your server knew a show by TVDB id but not TMDB, the app searched TMDB for the name and took the first result on faith. One reported case put Silo under a completely different series.",
        features: [
            "the damage went past a wrong poster: summary, status, ratings and the season list all came from the other show, so a wished S03 had no season to land in and the wishlist re-grabbed the same episode forever",
            "identity now comes from an id wherever one exists — your server's TMDB id first, otherwise the show's TVDB or IMDb id resolved to the exact TMDB entry",
            "a name search is the last resort, and it has to prove the result carries that title; when nothing does it records \"not found\" and retries rather than picking something",
            "a wrong stored year used to exclude the real show from its own search results, leaving only same-named strangers — the year is now dropped and the search retried before giving up",
            "shows already filed wrong are corrected automatically: each one carrying both a TMDB and a TVDB id is checked once in the background, and only a genuine contradiction re-points it",
            "the same rule applies to TVDB matching, which had the identical flaw",
        ],
        usage_note: "Nothing to configure. The background re-check is one lookup per show, once, and shows that agree are never checked again. A correction is logged as \"was matched to TMDB X, but its TVDB id resolves to TMDB Y\".",
    },
    {
        title: "1.9.10: sort the playlist matches that need you from the ones that don't",
        description: "importing a playlist matches every track against your library. On a few hundred tracks, the handful needing a decision are lost among the ones that matched cleanly — so the results table can now be filtered by how good the match was.",
        features: [
            "filter chips with live counts: Perfect, Low confidence, Wing It, Not found, Error",
            "the Perfect/Low line is 0.9 — the strictest bar any discovery source applies before calling something a match. Playlist discovery accepts down to 0.7, so \"low\" means accepted by a looser rule than the strictest source would have used",
            "\"Wing It\" is not a match at all: it is a placeholder the app invents when it finds nothing, so those are never counted as matched",
            "a manually matched row always counts as Perfect — you already decided, and a stale score should not demote that",
            "filtering changes only what is displayed; selection and downloading still act on the full result set",
            "your filter survives the table refreshing as discovery streams in, and buckets with nothing in them are not shown",
        ],
        usage_note: "The chips appear above the results table once discovery has produced answers. Start with Low confidence and Wing It — those are the rows where the app guessed and could be wrong.",
    },
    {
        title: "1.9.9: Auto-Import's threshold was set to a value it could not reach",
        description: "the default confidence was 90%. That reads as \"only import when very sure\", but the score is a product of three fractions, so 90% was close to unattainable — even a perfectly tagged album usually fell short. The default is now 45%.",
        features: [
            "the score is identification × title agreement × how much of the tracklist is present, so it decays far faster than a percentage implies",
            "a tagged album with slightly imperfect identification scored ~0.83 and was refused; the only reason tagged albums imported at all was a separate \"any one track matched very strongly\" bypass",
            "what 90% really gated was untagged folders, whose titles come from filenames and top out around 0.5 per track",
            "still refused at the new default: a folder with 3 of 12 tracks (~0.12), an album whose titles disagree with what it matched (~0.14), and anything the app could not confidently identify (~0.26)",
            "the default now lives in one constant that the settings screen reads, so the number shown can never be one the worker would not use",
        ],
        usage_note: "Import → Auto-Import has the slider. A value you have set yourself always wins; this only changes the default for installs that never touched it. If you want the old behaviour, set it to 90%.",
    },
    {
        title: "1.9.8: the source picker is finally the default",
        description: "the multi-source picker shipped in 1.9.0, but clicking a search result still ran the old automatic download — so the feature existed and almost nobody met it. Clicking now opens the picker, and batch downloads gain a per-track escape hatch.",
        features: [
            "clicking a track in search opens the picker: every configured source searched, results grouped by source, full releases listed separately, and only what you choose gets downloaded",
            "the automatic download survives as \"Auto\", styled quieter — it is still the fastest path when you don't care which copy you get",
            "a failed track inside a Begin Analysis batch now has its own Sources button, so the handful the cascade gets wrong can be picked by hand without going via the wishlist",
            "that recovery path already existed — the failed status cell has been clickable all along — but nothing indicated it, so effectively it did not",
            "Begin Analysis itself still downloads automatically in source order: it runs across a whole playlist and cannot stop to ask about track 34 of 60",
            "torrent and usenet results now name the tracker that served them, linked to that release's page where the indexer publishes one — Prowlarr puts every indexer behind one \"torrent\" source, which tells you nothing when comparing two releases",
            "fixed: music torrents ignored the category in Torrent Client Settings and always arrived tagged \"soulsync\" — the handover function declared that literal as its default, and a literal always beats the \"or use the configured one\" fallback beneath it",
        ],
        usage_note: "If you preferred the old behaviour, \"Auto\" on any result is exactly what clicking used to do. If your torrents have been landing in a soulsync category, existing ones keep that tag — only new downloads pick up your configured category.",
    },
    {
        title: "1.9.7: albums named \"Album 01 Title\" match again",
        description: "a correctly named album could auto-match zero tracks, leaving every one to be dragged into place by hand. And once you did, the row claimed the match was 100% certain.",
        features: [
            "when a file has no title tag the matcher reads the filename, but only knew how to strip a track number from the front — with the album name in front of the number, it scored the whole string \"Blue Blood 01 Blue Blood\" against the track \"Blue Blood\"",
            "that scored 0.34 against a 0.40 threshold: close enough that the naming looked supported and silently was not. It now reads the name without the album prefix too and keeps whichever fits better",
            "a file you assign yourself now shows \"manual\" instead of 100% — that number was a placeholder meaning \"you chose it\", displayed as though the matcher had been certain about a pairing it never made",
            "not looser: the prefix is only dropped when there is no title tag, only when the album name ends on a word boundary, and never when it would leave the title empty",
            "so a track genuinely named after its album keeps its title, and a wrong track still fails the threshold",
        ],
        usage_note: "If an album still matches nothing, check whether its files carry title tags — a tagged file is matched on the tag and ignores the filename entirely. The Auto-Import tab has its own separate confidence threshold (0.9 by default), which is stricter than the manual Import page's 0.4.",
    },
    {
        title: "1.9.6: download sources become one ordered list",
        description: "four settings described the same thing — a mode, a hybrid order, and a legacy primary/secondary pair — and which one won depended on which piece of code was reading. They collapse into a single ordered list of sources.",
        features: [
            "the first source in the list is preferred and the rest are fallbacks; \"hybrid\" now just means you listed more than one, rather than being a separate mode you also had to set",
            "the settings could disagree with each other: album downloads defaulted the mode to 'hybrid' while the album-bundle dispatcher defaulted the same setting to 'soulseek', so one install could take two views of its own configuration depending on the path a download took",
            "your existing configuration is read exactly as before — including installs still on the pre-hybrid_order primary/secondary pair — so nothing needs reconfiguring",
            "the old keys keep being written alongside the new one, so downgrading to 1.9.5 still finds its configuration",
            "removed 134 lines of dead Soulseek-era search rendering that nothing had called since the multi-source picker replaced it in 1.9.0",
        ],
        usage_note: "Settings → Downloads still edits the same list you already had. Saving once writes the collapsed form; until you do, the legacy keys are read and behaviour is unchanged.",
    },
    {
        title: "1.9.5: the writability check reaches video Libraries",
        description: "1.9.4 gave Music Libraries a check for destinations the server cannot write into. Video has the same destinations and the same failure mode — a grab succeeds, the import fails, and the Library folder stays empty — so it now has the same check.",
        features: [
            "Settings → Libraries marks any video Library the server cannot write into with NOT WRITABLE, with the reason on hover",
            "the check is one shared module used by both sides rather than two copies that drift apart — a folder cannot be judged working on one page and broken on the other",
            "it creates and removes a folder instead of reading permission bits, which give the wrong answer under container UID remapping, NFS root-squash and ACLs",
            "two Libraries pointing at the same folder probe it once, rather than creating and removing a folder there twice per page load",
            "the endpoint is admin-only: it returns filesystem paths, which the Library tab bar's own payload deliberately withholds from non-admins",
        ],
        usage_note: "NOT WRITABLE means the folder exists but the user Commissary runs as cannot create anything inside it. Compare the folder's owner against your container's PUID/PGID — Unraid shares are usually nobody:users (99:100) while the image defaults to 1000:1000. One Library working does not vouch for another; permissions are per-folder.",
    },
    {
        title: "1.9.4: a failed import can no longer call itself a success",
        description: "an import that could not write to its destination reported every track as imported. The files never moved. This release makes that failure impossible to miss — and adds a check so you can see the problem in Settings before an import runs into it.",
        features: [
            "post-processing swallows errors on purpose so a download can be retried later, and returns quietly. A manual import has nothing watching it, so it counted that quiet return as a success — an eleven-track album reported eleven imports and moved zero files",
            "a failed import now reports the actual reason, so \"Permission denied: /media/completed/listening/music/IVE\" reaches you instead of sitting in app.log",
            "Music Libraries the server cannot write into are marked NOT WRITABLE in Settings, with the reason on hover",
            "the check creates and removes a folder rather than reading permission bits — bits give the wrong answer under container UID remapping, NFS root-squash and ACLs, which is exactly where this breaks. It probes with a folder, not a file, because creating the artist folder is the step that fails",
            "the Music Library Folder / default library mismatch from 1.9.2 now repairs itself when Commissary starts, instead of waiting for a settings save you have no reason to perform",
        ],
        usage_note: "NOT WRITABLE means the folder exists but the user Commissary runs as cannot create anything in it. Compare the folder's owner against your container's PUID/PGID — Unraid shares are usually nobody:users (99:100) and the image defaults to 1000:1000. Video working on the same base folder does not rule this out; permissions are per-folder.",
    },
    {
        title: "1.9.3: fixes for 1.9.2, and Deep Scan learns about libraries",
        description: "one real regression from 1.9.2 — changing your Music Library Folder quietly did nothing — plus an import message that made a full album look like it had imported one track, and Deep Scan finally knowing that more than one library exists.",
        features: [
            "Music Library Folder and the first entry under Music Libraries are the same setting shown twice, and the importer reads the entry. Editing the folder left the entry on the old path, so downloads kept landing where you'd moved away from. Saving Settings now moves the library with it",
            "if an album went missing after updating to 1.9.2, the first path under Music Libraries is where it actually went",
            "the Import page submits an album one track per request, so \"Album Imported (1/1 tracks)\" fired once per track and each claimed to be the whole album. It now names the track, and a failure says so rather than announcing an import that didn't happen",
            "Deep Scan scans every Music Library, and scores each one separately — pooling them would defeat its own safety guard, because a library you just added is 100% untracked by definition and adding one full of music you already own would have relocated all of it",
            "a relocated file keeps its structure relative to its own library; an unmounted library is skipped rather than read as \"everything vanished\"; and one library tripping the out-of-sync guard now stops stale-row deletion everywhere",
        ],
        usage_note: "Worth checking after updating: Settings → Paths & Organization → Music Libraries. If the first path isn't your Music Library Folder, that mismatch is the 1.9.2 bug — saving Settings once realigns them.",
    },
    {
        title: "1.9.2: music can have more than one library",
        description: "music has had exactly one output folder since the beginning, while the video side has been able to file a title into whichever library you picked for years. Music now has the same: a list of labelled destinations, each with its own naming and quality settings if you want them.",
        features: [
            "Settings → Paths & Organization → Music Libraries. Add as many as you like; the first is the default destination, and reordering changes which",
            "your existing library becomes the first entry automatically — an install that never opens the setting writes files exactly where it did before, with no migration and nothing to configure",
            "each library can override the naming template and the quality profile; blank inherits your global settings, which is how they all start",
            "a library's quality profile governs the whole pipeline for files going there — the quality gate, the fingerprint check, deep verify, replace-lower, downsampling — not just search ranking",
            "reorganizing a file re-files it WITHIN the library it lives in rather than pulling everything into the default one; moving between libraries is still possible, it just has to be asked for",
            "Music Library Folder above the list is the first library — editing either updates the other, so the two can't disagree about where music goes",
        ],
        usage_note: "The old \"Additional Music Libraries\" section is now \"Additional Read-Only Paths\" — it always meant folders Commissary reads but never writes to, and that name would now read like it meant destinations.",
    },
    {
        title: "1.9.1: import from any of your download folders",
        description: "Import read one folder and only one — the Import folder in Settings. Anything a download client left elsewhere had to be moved there by hand first. Now you can point the scan at any of your download, import or library folders.",
        features: [
            "a Change folder button on the Import page opens a browser over the folders Commissary already knows about — download folder, torrent and usenet completed paths, Import folder, music library, extra library paths",
            "it starts on your download folder, so the usual case needs no path typing, and tells you how many audio files are directly in the folder you're looking at before you commit",
            "a folder with no audio directly in it can still be the right pick — subfolders are scanned too, which is exactly the 'complete/Artist - Album/' case",
            "the folder you choose survives switching between the Albums, Singles and Auto-Import tabs",
            "the header reads 'Scanning: …' rather than 'Import: …' whenever you're somewhere other than the configured folder, so an empty result can't be mistaken for a broken Import folder",
            "changing folder clears the matching work in progress on purpose — a selected album and its per-track matches name files in the folder you just left",
        ],
        usage_note: "It won't walk the rest of the machine: the browser is bounded to your configured folders, and stops offering 'up' at their edge rather than leading you somewhere the scan would refuse. Admin only, like the rest of Import.",
    },
    {
        title: "1.9.0: every source is searchable, and albums get a release picker",
        description: "1.8.19 let you search your sources by hand for a track. This opens that up — every source you have connected is now offered, not just the ones in your fallback chain — and gives albums their own picker so you choose the release instead of Commissary guessing.",
        features: [
            "your download mode filtered which sources manual search would even offer: Hybrid showed only your fallback chain, single-source showed exactly one. That setting governs the AUTOMATIC cascade — it was never a claim that your other sources don't work. Every configured source is now searchable; your chain order still leads the list, it just no longer excludes",
            "albums and singles get a Sources button of their own. It asks every source that indexes whole albums what it has — format, track count, size, seeders — and downloads the exact release you pick",
            "an album pick overrides your configured download source, because 'which source may claim a whole album unattended' stops being the right question once you have named the release you want",
            "torrent and usenet index releases, not tracks, so in the track picker their results now sit under their own heading with a note saying what picking one does: the whole release downloads, the matching track is kept, and if it isn't in there the download fails rather than importing the wrong file",
            "sources that returned nothing say so, and one that errored is marked — 'who has this?' is only answered properly when the zeroes are visible too",
            "a search that finds nothing now shows where it looked, instead of collapsing to a single line: 'nothing anywhere' and 'three sources failed' should not look identical",
        ],
        usage_note: "Two sources can show you albums but can't be pinned to a specific release — Amazon and Lidarr have no whole-album download flow. Their rows say 'Use this source' and are dimmed, so the picker isn't claiming precision it doesn't have.",
    },
    {
        title: "1.8.19: search every source yourself and pick",
        description: "the only route to a track used to be adding it to the wishlist and waiting for the automation to have a go. Now you can search all your download sources directly and choose the copy you want.",
        features: [
            "a Sources button on the Search page, on album tracks, and on every wishlist row — it queries every configured download source and shows what each one is offering",
            "results are grouped by source, so you can see one has it in FLAC while another only has a low-bitrate rip, rather than a merged list that hides which source is serving you well",
            "picking by hand keeps every safety net: your choice goes down the same path an automatic download does, so the fingerprint check and quality quarantine still apply, and the auto-retry leaves your choice alone instead of quietly grabbing something else",
            "album tracks get it whether you own them or not — fill a gap, or swap in a better copy",
            "the wishlist placement is the useful one when something has been sitting there refusing to download",
        ],
        usage_note: "It gives you a way out, not an explanation. If something has been stuck a while, check the wishlist's Ignored list — removing a track or cancelling its download stops the automation re-adding it for thirty days, invisibly.",
    },
    {
        title: "1.8.18: downloads in category folders finally import",
        description: "two import fixes: a release filed under a category folder was never found, and manual placement reported failure for files it had actually placed.",
        features: [
            "if your download client sorts finished downloads into folders — 'complete/Movies/…' and the like — Commissary could not find them. It looked exactly one folder below each download root, and a category layout puts the release two down, so the download sat there and never imported. It now looks three levels deep",
            "still bounded by depth and by how many folders it will examine, so a download root can't turn into a directory crawl. Adjustable with download_source.import_search_depth for a deeper layout",
            "manual placement copied the file while your browser waited, and a large file over a network share takes minutes — so if anything gave up waiting you got an error while the server quietly finished the copy. It now runs in the background, and the page checks what actually happened before calling anything failed",
            "small placements still finish instantly and behave exactly as before; only a slow copy hands the page back and reports progress",
            "trying again is safe: asking for something already placed says so, and asking mid-copy joins the one running rather than starting a second copy on top of it",
        ],
        usage_note: "Seen that error before? The file was probably placed. Refresh the Import page — if the item is gone, it worked.",
    },
    {
        title: "1.8.17: deselecting a tracker now stops it being used",
        description: "the tracker checkboxes on each Library only nudged the ranking — every tracker was still searched, so unticking one changed nothing about where downloads came from.",
        features: [
            "ticked trackers are now the only ones searched for that Library, on automatic searches as well as manual ones",
            "the setting never said otherwise because the note explaining it lived on a text box that gets hidden the moment the checkboxes appear — so the explanation vanished exactly when the control showed up. Both tracker settings now carry a caption you can see",
            "two more places were ignoring your choices: the RSS pass polled every indexer even when Restrict to indexer IDs named a few, and manual search resolved the picked Library only after searching",
            "Settings → Indexers: the indexer list is clickable now and fills the Restrict box for you, instead of listing IDs you had to type in by hand",
            "the global Restrict setting is the outer limit and a Library narrows it, never widens it. Pick trackers a Library is globally barred from and Commissary says the two contradict each other rather than quietly searching everything",
        ],
        usage_note: "If you ticked trackers expecting a preference, that selection now binds — a Library whose trackers have no results will come up empty rather than falling back.",
    },
    {
        title: "1.8.16: rename files from a show's own page",
        description: "plus two download fixes: torrents are no longer imported while the client is still writing them, and the YouTube downloader's last retry no longer fetches the music video.",
        features: [
            "a Rename Files button on any show or movie you own — the naming template, every $variable with the value it takes for THAT title, and a live preview of every file's current and proposed name. Nothing moves until you confirm",
            "the template you type is a one-off for that rename; your saved template in Settings is untouched. Same engine as the library-wide rename, so sidecars travel with the file and an occupied name is skipped rather than overwritten",
            "torrents now wait until the file stops changing before importing. Reaching 100% means the bytes are in, not that the client has finished moving them — qBittorrent reports 100% while relocating a finished download, and usenet repair and unpack both write after that point",
            "the YouTube music downloader's third retry used to switch to a combined video+audio stream and discard the picture. It always took audio only otherwise; that one wasteful fallback is gone",
            "new YouTube Audio Format setting: keep YouTube's original audio instead of transcoding to MP3 320. Smaller and better, since converting Opus to MP3 is lossy-to-lossy — but a quality profile targeting MP3 will stop matching YouTube if you switch",
        ],
        usage_note: "Rename Files is admin-only, and appears on titles you actually own.",
    },
    {
        title: "1.8.15: Wishlist Audit never cleared anything",
        description: "the maintenance job that removes wishlist entries for things you already have would run, report nothing, and leave downloaded and imported shows sitting there. Three separate causes, all ending in a silent scan.",
        features: [
            "cleaning something up once made it invisible forever — the main one. Approving a finding deletes the wishlist row it names, but the job treated an already-fixed finding as proof it had reported that title before and refused to raise it again. It worked once per title, then went quiet",
            "copies your server has but TMDB never matched were not counted as owned, which is exactly the thing you have downloaded, imported, and can see on your server while the audit insists there is nothing to clean",
            "it now says when it left things alone on purpose: \"scanned 12, 0 new findings, 12 deliberately left alone\" rather than a bare zero. A row below your quality cutoff is kept on purpose — that is an upgrade the downloader is still hunting",
            "new Include below cutoff option on the job. If your cutoff is 4K, or set to \"always chase the best\", nothing you download ever counts as finished and the audit can never clean anything. Off by default, because turning it on ends the upgrade hunt for those titles",
            "dismissing a finding still silences it permanently — only findings you actually fixed can come back",
        ],
        usage_note: "Approving a Wishlist Audit finding removes the wishlist row and nothing else. Your files are never touched.",
    },
    {
        title: "1.8.14: signing in survives closing the browser",
        description: "it never did. Nothing had ever configured how long a sign-in lasts, so the browser discarded it on close — invisible while the picker let anyone click back into any profile, and painful once 1.8.13 required having actually signed in.",
        features: [
            "a sign-in now lasts thirty days and renews as you use it: keep using Commissary and you stay signed in, stop and it lapses after a month. Adjustable with security.session_days",
            "Plex users no longer redo the Plex link every time they reopen their browser — the reason this surfaced at all",
            "\"Log out\" had to start meaning it. It previously forgot only which profile you were using and kept the record of every account signed in on that device. Harmless when everything vanished on browser close; with a month-long sign-in it would have handed the next person your accounts on a shared computer. It now clears the lot",
            "nothing changes for HTTPS or plain-http installs: the cookie is marked Secure only when reverse-proxy mode is on, exactly as before. Forcing it on a normal home install would stop the browser sending it at all — the same bug, permanently",
        ],
        usage_note: "Shared computer? Use Log out rather than just closing the window.",
    },
    {
        title: "1.8.13: the account switcher let anyone in",
        description: "the swap-account screen listed every profile on the server, and clicking one signed you into it. A profile was only asked for a PIN if it happened to have one — and an account made by \"Sign in with Plex\" has neither PIN nor password.",
        features: [
            "so anyone at that screen could walk into any Plex user's account without authenticating. The same applied to any local profile whose owner never set a PIN, so this was never Plex-specific",
            "the switcher now shows only the accounts signed in on this device — and refuses the rest: switching to a profile this browser has not signed in as is turned down by the server, not merely hidden",
            "signing in (Plex, a password, or the profile's PIN) adds it, and from then on you can swap between them freely",
            "you cannot lock yourself out: your admin profile is always listed and selectable, since an install whose admin set no PIN or password must not be able to shut itself out. Its own PIN, if set, is still required",
            "the full profile list is now admin-only — it enumerates every account including the Plex username behind each one",
        ],
        usage_note: "After updating, everyone signs in once more — existing sessions carry no record of what they signed into. That is the upgrade working, not a fault.",
    },
    {
        title: "Manage Profiles: two fixes",
        description: "Side Access lied about what a profile had, and there was no way to make someone an admin without an API call.",
        features: [
            "editing anyone showed \"Music only\" selected regardless of their real access. The setting was right in the database and right everywhere it was enforced — the edit form was simply never handed the value and fell back to the most restrictive option. Saving without noticing would have applied that fallback",
            "an Administrator checkbox now sits in the profile editor. It appears only when you are editing someone else, so you cannot demote yourself out of the screen you are on, and the last admin still cannot be removed",
            "ticking it explains that Side Access and the page choices stop applying — admins always have both sides and every page",
        ],
    },
    {
        title: "1.8.12: get told when someone is waiting on you",
        description: "a request that needs your approval can now reach you on Discord, Telegram, or any URL you like — instead of sitting on a page until you happen to look.",
        features: [
            "Settings → Notifications, add a connection, tick \"🙋 Needs approval\". That is the whole setup",
            "it covers the Watchlist as well, which never had this: follows have been landing in an approval queue since 1.6.7 with nothing able to tell you. If standard users have been following shows, entries may be waiting for you right now",
            "there was already a \"Wishlisted\" alert, but it fires for everything added — your own, and everything the hourly jobs add, dozens at a time — and never said who asked or whether it needed you. This one fires only for a request that is actually waiting",
            "one message per request: a whole season sends a single \"Breaking Bad (24 items · asked by Member)\", not twenty-four pings. Asking again for something already requested stays quiet",
            "Automations has it as a trigger too — \"Video Request Needs Approval\" — so you can route by who asked, or by whether it was the wishlist or the watchlist",
        ],
        usage_note: "Settings → Notifications. The message names the title, how many, who asked, and which list to open.",
    },
    {
        title: "1.8.11: a correction to 1.8.9",
        description: "1.8.9 hid search sources you have not set up — but only for admins. Standard and Plex users still saw the full row, which is exactly what that change was meant to stop.",
        features: [
            "the page asked an admin-only endpoint which connections were configured. For anyone else it came back \"not allowed\", and an unanswered lookup is treated as \"assume everything is set up\" — so nothing was hidden",
            "that assumption is right when the lookup fails for an ordinary reason: a moment offline should never leave you staring at an empty picker with no way to search. It is wrong for \"you are not allowed to ask\", and the page could not tell the two apart. It now asks a question it is allowed to ask",
            "the new lookup tells a standard user only whether each of the ten SEARCH sources has credentials. No keys, no addresses, no settings, and nothing about your Plex, Jellyfin, slskd, Tidal, Qobuz or Last.fm connections",
            "Settings → Connections is unchanged and still reads the full admin-only picture. Only the search picker moved, and it moved to a smaller question rather than the admin gate being loosened",
        ],
    },
    {
        title: "1.8.10: a wishlist request waits for you",
        description: "the Watchlist has needed approval since 1.6.7. The wishlist did not — a title added by someone without download rights went straight into the hourly automation and was fetched unattended.",
        features: [
            "a request now lands as \"Awaiting approval\": on the wishlist immediately, so the person who asked can see they asked, while nothing goes looking for it until you say yes",
            "admins get Approve and Decline on the card with the requester's name. Approving a show releases every pending episode under it at once; declining removes the request",
            "every route that could fetch it is covered — the hourly drain, RSS matching, \"Search now\", \"Search all missing\" and the YouTube worker. One missed path would mean the automation quietly downloading something nobody approved, so they all read the wishlist through the same four places and all four skip anything still waiting",
            "nothing you already had stops downloading. Approval defaults to granted, so your own wishes, everything your automation added and every row predating this release carry on exactly as before",
            "a member can still remove their own pending request without asking you — thinking better of it should not need an approval as well",
        ],
        usage_note: "Wishlist → pending cards carry Approve / Decline. Manage Profiles → 'Can download' decides who needs approval.",
    },
    {
        title: "1.8.9: what you can see is what you can do",
        description: "the follow-up to 1.8.8. Standard and Plex users were still shown controls that answered \"not allowed\" when clicked — and a few of those controls were not dead at all, they worked, on shared data.",
        features: [
            "Retry, Block release, Clear, the download-history actions and Search now on an individual episode are hidden for profiles that cannot use them, on both sides",
            "three were not merely cosmetic: clearing the finished downloads (music AND video) and every part of the music Import page had no permission check at all. Any signed-in profile could empty your download history or import files into your library. They are now checked on the server, which is the part that counts",
            "cancelling a music download was open too — the same gap the video side had in 1.8.8. One of the four routes also re-added the cancelled track to the shared wishlist, so a single call both stopped a download and changed shared state",
            "Manage, Manage Poster and Synchronize on a movie or show are admin-only. Everything they save already was, so for anyone else they opened a panel where every save came back refused",
            "the music Import page is admin-only. It stages files off your disk into the shared library, and had been a per-profile toggle that was ON by default — while the same page on the video side has always been admin-only",
        ],
        usage_note: "Manage Profiles → per-profile 'Can download'. Import and Manage follow the admin flag.",
    },
    {
        title: "Your wishlist items are yours to remove",
        description: "the wishlist is one shared list, so removing used to be all-or-nothing: either a profile could clear anyone's requests or it could not remove even its own.",
        features: [
            "each entry now remembers who asked for it. A member can take back what they added; anything else is refused with a reason rather than silently doing nothing",
            "Clear all follows the same rule — for you it empties the tab, for a member it clears their own and says what it left: \"Cleared 3 movies you added — 2 from other people left in place\". The confirmation says which it will do before you commit",
            "ownership is set when a title is first added and never reassigned, or wishing for something already on the list would hand you someone else's entry to delete",
            "items added by your automation belong to nobody and stay yours alone to clear",
            "the ⚠ Failing filter is hidden for members: it narrows the list down to what keeps failing so you can re-search or drop each one, and a member has none of those actions",
        ],
    },
    {
        title: "The search picker shows only what you have set up",
        description: "sources with no credentials were shown greyed out with a \"set up in Settings\" tooltip — a row of buttons that cannot answer a search.",
        features: [
            "unconfigured sources are simply absent now, on the Search page and in the global search box alike",
            "if you have not configured anything at all the full row comes back — that is the one time those tooltips are the point, and an empty picker would leave nothing to click and no explanation",
            "the source you are currently searching is never hidden, even if its credentials go away",
        ],
    },
    {
        title: "1.8.8: members can ask, not fetch",
        description: "a permission fix. Standard and Plex users could start downloads from the wishlist, and a standard profile could cancel yours — while the one thing they SHOULD be able to do, adding to the wishlist, was blocked.",
        features: [
            "\"Search now\" and \"Search all\" on the wishlist start real downloads and were behind no permission check at all. Every other download action was already covered; these two were missed. They now require download permission",
            "cancelling was already checked — but a standard profile you created started with download permission ON, so it inherited the ability. A new profile now matches its role: an admin can download, a standard user cannot until you say so",
            "EXISTING profiles are left exactly as they are. Quietly revoking something someone relies on would be its own kind of bug — tools/audit_download_permission.py lists every profile and flags any non-admin who can currently start or cancel downloads",
            "adding to the wishlist works for everyone again. It had been blocked for exactly the people meant to use it, so members had no way to ask for anything. Asking is not downloading: your automation, or you, still decides what is actually fetched",
            "the buttons that would be refused are hidden for those profiles too — but that is only so nothing offers an action that fails. The server is still the check",
        ],
        usage_note: "Manage Profiles → per-profile 'Can download'.",
    },
    {
        title: "A clear button on the search fields",
        description: "the Library, Music Library and Purchased search boxes now have an × once there is something to clear.",
        features: [
            "Escape clears the field too, though only when it has text in it — so it still closes a dialog on an empty box",
            "clearing runs whatever filtering the page already does, exactly as if you had deleted the text by hand, rather than being a second way of filtering that could behave differently",
        ],
    },
    {
        title: "Earlier in 1.8.7 — two failures pretending to be answers",
        description: "both of these reported something confidently untrue: a torrent that had been added was called a rejection, and a search that never finished was called an empty result.",
        features: [
            "the grab said \"the torrent client didn't accept the release\" while the torrent was downloading. Commissary identified a new torrent by listing the client's torrents before and after and spotting the difference, waiting about five seconds — and qBittorrent frequently needs longer. It now derives the torrent's identity from the magnet or the torrent file BEFORE adding it, so there is no race to lose",
            "that mattered more than the wording: a grab recorded as failed is never watched, so the finished download was never imported. The file arrived and Commissary did not know it existed",
            "re-adding a torrent the client already has now resolves correctly as well — it produces no new entry to spot, so it always looked like a failure",
            "Manual Search returned nothing until you searched for the same title in Prowlarr first. A cold search across many indexers takes longer than the fifteen seconds Commissary allowed, and a timeout was indistinguishable from \"no releases exist\" — so it said there were none. Searching in Prowlarr warmed its cache and the next attempt answered in time",
            "searches now get ninety seconds by default (adjustable), and a failed search says what went wrong rather than reporting an empty result",
        ],
    },
    {
        title: "Grab season fetches one pack",
        description: "it used to search for and grab every missing episode separately — a dozen searches and a dozen downloads for one season.",
        features: [
            "one release covering the whole season is found and grabbed; the import splits it into episodes exactly as it already did for automatic pack downloads",
            "kinder to indexers, faster, and it stops a season being assembled from a dozen unrelated releases at different qualities and from different groups",
            "if no season pack exists it says so and grabs NOTHING, rather than quietly reverting to one-by-one downloads. Auto on an individual episode is still there for that",
            "every missing episode row still lights up while it works, so a season grab does not look like nothing happened",
        ],
        usage_note: "Open a show → a season → Grab season.",
    },
    {
        title: "Earlier in 1.8.6 — Manual Search opens up",
        description: "results were being thrown away after they had already been found, a release name told you nothing about where it came from, and a long list could not be narrowed down.",
        features: [
            "the search kept the best 40 releases and 15 rejected ones and discarded the rest — after ranking them. That is why a popular title only ever showed so many. Now 100 and 40, both configurable, along with how many results are requested from Prowlarr in the first place",
            "the release name links to the indexer's own page for it. Links open in a new tab, and the address is checked before it is shown — a tracker cannot use one to run anything inside Commissary, and the tracker learns nothing about your install from the click",
            "filter by name, quality, source, minimum seeders, or only releases that meet your quality profile. It works on results already fetched, so it is instant, and it survives a search still streaming in underneath it",
            "the header always says how many rows are hidden, so a short list is never a mystery",
        ],
        usage_note: "Manual search → the filter bar sits directly above the results.",
    },
    {
        title: "Manual import takes a whole season folder",
        description: "the automatic side has unpacked season packs since 1.8.0. Manual import could only take one file, so a pack that arrived any other way had to be placed episode by episode.",
        features: [
            "browsing into a folder holding two or more numbered episodes offers \"Import this whole folder\". The folder becomes ONE queued item and ONE identity choice",
            "the Place dialog lists every file and the episode it read from each name before you commit — a pack whose names parse wrongly is far cheaper to spot there than to unpick from the library afterwards",
            "each file keeps its own episode number; the dialog only supplies the show. Applying one episode number to every file would file the whole season on top of itself",
            "samples, extras and anything without an episode number are skipped, by the same rule the automatic import already uses",
        ],
        usage_note: "Import page → Add → browse into the pack folder.",
    },
    {
        title: "A fix for 1.8.5",
        description: "\"Check for out-of-place episodes\" threw \"src is not defined\" and did nothing. Mine, and it reached you because I checked the file parsed instead of clicking the button.",
        features: [
            "one edit added code that used a value while the edit meant to create that value never saved, so the button referred to a name nothing defined",
            "a syntax check cannot catch this — the code is well-formed, it just refers to something that isn't there — and the older parts of the interface get no automatic linting. There is now a test that scans for exactly this shape",
        ],
    },
    {
        title: "Earlier in 1.8.5 — the repair no longer undoes itself",
        description: "1.8.4 taught Commissary to take episode numbering from the database your media server agrees with. This closes the paths that were still ignoring that — including two unattended jobs that would quietly put the bad rows back.",
        features: [
            "the show-match pass and the full episode-list sync both took their season numbers from TMDB regardless of what a show was set to use. On a TVDB-numbered show they re-created exactly the rows the out-of-place check had just removed — unattended, so the repair appeared to work and then reverted with nothing on screen to explain it",
            "that check errs towards writing: if it cannot be resolved the episode list is still written. A list that should not exist is visible and fixable; one that was never written is a silent hole",
            "all three buttons are worded generically now — a button saying \"from TMDB\" while reading TVDB is a lie about what it did. The database in use is named once, under the Episode numbering box, instead of in three labels that can drift apart",
            "results still name the database they consulted (\"Checked against TVDB: …\"), because there \"nothing found\" and \"I asked the wrong one\" are otherwise indistinguishable — which is precisely what happened",
            "the duplicate check consults no database at all: it pairs episodes by air date within your own library. Its description had blamed \"your server and TMDB\", wrong twice over",
            "TVDB episode lists were read one page deep. On a 400-plus-episode show the later seasons fell past that page and returned empty — and an empty season reads exactly like a season with no episodes, so everything downstream did nothing and reported success",
        ],
        usage_note: "Manage on a TV show → Episode numbering shows which database is in use and why.",
    },
    {
        title: "Earlier in 1.8.4 — episode numbering follows your server",
        description: "the real cause, found by dumping the rows rather than reasoning about them. Two databases split Bleach differently, and Commissary had always taken the numbering from the one that disagrees with Plex.",
        features: [
            "TMDB has Bleach as three seasons — specials, the 366-episode 2004-2012 run, and Thousand-Year Blood War. TVDB has seventeen, which is what Plex reports. Commissary used TMDB's numbers, so TMDB's \"season 2\" (the 2022 run) was written on top of the 2005 season",
            "the evidence was exact: every season carrying invented rows was a season TMDB has (0, 1, 2); every season with none was one it does not (3 to 17)",
            "the same fault is why Season 17 never filled — TMDB has no season 17, so nothing could ever add episodes to the season where the library actually keeps that run. It can fill now",
            "Commissary scores each database on how much of your server's season structure it can serve, and switches only when the difference is decisive. A show both agree on — nearly all of them — keeps using TMDB exactly as before",
            "Manage → \"Episode numbering\" (Auto / TMDB / TVDB) overrules it per show. An explicit choice is obeyed even when the automatic guess disagrees",
            "the out-of-place check now asks whichever database owns that show's numbering. It had been asking TMDB, which for a show like this treats your correct season as the wrong one",
        ],
        usage_note: "Open the show → Manage → Check for out-of-place episodes, then Re-scan episodes from TMDB.",
    },
    {
        title: "A correction to 1.8.3",
        description: "that release named TVDB as the culprit and stopped it adding episodes. It was the wrong database.",
        features: [
            "TVDB is the one that AGREES with Plex on this show. Blocking it removed the only source that could ever fill Season 17, while the database actually causing the problem carried on writing",
            "reverted. TVDB supplies episodes again whenever its structure is the one your server matches",
            "1.8.3's clean-up tool and the read-only diagnostic script it shipped are both kept — the diagnostic is what found this",
        ],
    },
    {
        title: "Earlier in 1.8.3 — the first attempt",
        description: "1.8.2 shipped a theory about why a show's episodes were listed under two different season numbers. A dump of the actual rows showed the theory was wrong, and the real cause was one line handing one database's season numbers to a different database.",
        features: [
            "TMDB's Bleach season 2 is the 2005 arc. TVDB's season 2 is the 2022 Thousand-Year Blood War run. Commissary read its season list from TMDB, then asked TVDB for episodes using those same numbers — so TVDB's season 2 landed inside TMDB's, putting seventeen episodes dated 2023-2026 inside a season from 2005",
            "that is why the missing episodes were never found: they were being searched for as season 2 episode 41, a thing no release will ever be labelled. Meanwhile the library already had the whole run correctly under season 17",
            "TVDB is still used and still valuable — it is usually first with titles and synopses for a just-aired episode. It may now enrich an episode TMDB already listed. It may not decide which episodes exist",
            "Manage → \"Check for out-of-place episodes\" clears rows already written this way. It asks TMDB which episode numbers belong to each season, shows you what it found, and removes nothing until a second click — and nothing that has a file or came from your server",
            "if TMDB cannot be reached it deletes nothing, rather than treating an empty answer as \"none of these belong\"",
        ],
        usage_note: "Open the show → Manage → Check for out-of-place episodes, then Re-scan episodes from TMDB.",
    },
    {
        title: "A correction to 1.8.2's duplicate tool",
        description: "the clean-up added in 1.8.2 read this situation backwards. It assumed your media server's numbering was the odd one out; for Bleach the server was right all along and the invented rows were the ones it kept.",
        features: [
            "it only ever removed rows with no file, nothing on disk, and nothing your media server had reported — so no episode you own was affected and no file was touched",
            "it did remove correctly-numbered rows on shows in this situation, which entrenched the wrong listing rather than fixing it",
            "if you ran it on a show whose seasons looked wrong, run the new out-of-place check and then \"Re-scan episodes from TMDB\" — that rebuilds the season listing from the authoritative source",
        ],
    },
    {
        title: "Earlier in 1.8.2 — where a wished title goes",
        description: "one long-standing gap and one confusing report. A wished title had nowhere to record which Library it belonged to, so everything the automation grabbed went to the primary folder — and a show your server and TMDB number differently was being stored as two sets of episodes.",
        features: [
            "every Wishlist card now has a Library box. Until a title exists on disk there was no row to hold that choice, which is why unattended grabs all landed in All Movies / All TV however your Libraries were set up. Pick one and the next grab goes there — nothing already on disk moves",
            "the box shows where a title is actually headed, not merely whether someone set it: a show already filed under Anime reads \"Anime\". It appears only when you have more than one Library of that kind",
            "Bleach's newer run is Season 2 to Plex and Season 17 to TMDB. Both are right, and Commissary kept both — so the episodes being hunted were filed under a number no release uses, and never matched. It no longer creates the second copy when your server already has that episode elsewhere",
            "for libraries already carrying them: Manage → \"Check for duplicate episodes\". It reports what it found in your terms (\"S17E1 (you have it as S2E1)\") and removes nothing until you click again",
            "the clean-up is deliberately timid. A row goes only when the air date pairs it to exactly one episode you own under a different season. A streaming season sharing one date, a missing date, two candidates on one day — all left alone. It touches no files: these rows are placeholders for episodes you do not have",
        ],
        usage_note: "Wishlist → the Library box on any card. Duplicates: open a show → Manage → Check for duplicate episodes.",
    },
    {
        title: "Earlier in 1.8.1 — finding things, and asking TMDB again",
        description: "two reports that turned out to be three separate causes — a search that could not reach past its first page, a year that made searching worse instead of better, and an episode list nothing in the app could refresh.",
        features: [
            "search read ONE page of TMDB results — twenty, ordered by popularity. Any title sharing its name with something more famous was unreachable, however exactly you typed it. It now reads further down",
            "typing a year returned NOTHING at all, because TMDB's combined search has no year field and got it as part of the title. Now, if what you typed finds nothing and ends in a year, the name is searched on its own and that year's match is floated to the top. This only happens when the original search came back empty, so a real title ending in a year — Blade Runner 2049 — is never touched",
            "a new \"Re-scan episodes from TMDB\" button on TV shows. A show's episode list was read once and never revisited, so episodes TMDB gained afterwards were invisible with no way to ask for them. This reads every season again and reports how many it added",
            "that is a different question from \"Sync show now\", which checks your media server and so can never discover episodes your server has not got. If a season looks short next to TMDB, the new button is the one",
        ],
        usage_note: "Open a show → Manage → Re-scan episodes from TMDB.",
    },
    {
        title: "Earlier in 1.8.0 — season packs, start to finish",
        description: "grabbing a whole season already half-worked — Commissary could find a pack and match it correctly, then download it and stop, because the import step only knew how to place a single file. It now unpacks one.",
        features: [
            "every episode inside the pack is renamed and filed on its own, using the same import that a single episode goes through — so naming, quality upgrades, subtitles, the recycle bin and torrent seeding all behave exactly as they already did. Nothing about packs is a special case once they are open",
            "a pack that ships fewer episodes than it claims, or one where you already own some at better quality, imports what is useful rather than failing. Samples, extras and trailers are recognised and left behind",
            "the hourly automation can grab a pack when several episodes of a season are missing, instead of chasing them individually. It is OFF until you enable it: one pack can be tens of gigabytes and that job runs unattended, so an existing setup should not start spending disk because it updated",
            "a season already downloading now blocks its own episodes from being grabbed separately — without that, the next hourly run would have fetched all of them again alongside the pack",
        ],
        usage_note: "Open a show, pick a season, and search it — pack results appear alongside single episodes. For the automation: Settings → season packs.",
    },
    {
        title: "Earlier in 1.8.0 — put a title in the right Library",
        description: "the Libraries you configure decide where downloads land, but nothing let you CORRECT a title that ended up in the wrong one — for movies or TV shows. Manage now has a Library box.",
        features: [
            "pick the Library a movie or show belongs to, and its future downloads and upgrades go there",
            "it does not move anything already on disk. That is a much bigger and riskier operation, and this is the small reversible half that fixes the actual problem — everything from here on going to the right place",
            "a movie cannot be filed under a TV Library or vice versa. That mistake would have been silent, and every later grab for the title would have gone into the wrong tree",
            "leaving it on \"Default\" is a real setting, not an empty one — the title falls back to your primary Library for its type",
        ],
        usage_note: "Open any movie or show → Manage → Library.",
    },
    {
        title: "Earlier in 1.7.2 — the rest of the login-screen noise",
        description: "1.7.1 stopped the repeating background polling on the sign-in screen. This finishes the job — the one-off burst every module fires the instant the page loads.",
        features: [
            "about fifteen modules each fetch something once at boot — the video dashboard, libraries, scan status, issue and watchlist counts, YouTube channels, search sources. None of them can know yet whether you are signed in, because that answer only arrives with the first reply from the server, so with Require login on they all went out and were all refused. They now wait for that answer",
            "they WAIT rather than fail. That distinction is the whole point: on an install without Require login, auth is briefly unknown too, so failing fast would have made those same requests give up and paint an empty state that never refills. Deferring means an ordinary install sees no change at all — verified, twenty-five requests, all returning real data",
            "if the answer never comes, the wait ends after ten seconds and everything proceeds as it used to. A bug in this cannot leave Commissary unable to talk to itself",
            "measured on the sign-in screen: seventeen requests before, two after — and those two are the ones the screen genuinely needs",
        ],
    },
    {
        title: "Earlier in 1.7.1 — the login screen behaves itself",
        description: "two follow-ups to 1.7.0, both on the sign-in screen, both reported from real use.",
        features: [
            "\"Sign in with Plex\" looked dead once Require login was on. It was never broken — the request went out, Plex returned a real code, and the panel showing that code was hidden by the lock screen's own blanking rule. The panel is now excepted from it, like the other two lock screens",
            "the sign-in page had been starting the whole app's background polling before you were signed in: fifteen enrichment services, system stats, repair status, download activity, all on timers, all refused. Measured on the login screen it was dozens of requests a minute and a console full of red. Those now wait for sign-in — the same page over 46 seconds makes seventeen requests, none repeating",
        ],
    },
    {
        title: "Earlier in 1.7.0 — ready to be reachable from outside your network",
        description: "a security pass over the whole app, done before opening it up to people outside the house. Three real holes closed and one control that disagreed with its own rule. Nothing here changes how Commissary works day to day.",
        features: [
            "another website can no longer act as you. While you were signed in, any page you visited could make your browser fire commands at Commissary — add downloads, change settings — and Commissary would carry them out, because your session cookie went along for the ride. Requests coming from somewhere other than Commissary are now refused",
            "a first-run shortcut used to hand out an API key to anyone who asked, without signing in, even with \"Require login\" on — and that key could then turn login back off. It now needs an admin who is actually signed in",
            "the artwork fetcher had a safety check that let everything through, so it could be aimed at devices on your home network instead of at posters. It now reaches only your own media servers and public artwork sites; Plex and Jellyfin art on a LAN address is unaffected",
            "the Stop button in Server Activity was shown to every user, though the server had always refused anyone but an admin. Non-admins no longer see a button that only fails",
            "checked and found clean: how passwords are stored, protection against password guessing, database query safety, and the permission rules that keep standard users out of the admin pages",
        ],
        usage_note: "Before putting Commissary on a public address: turn on Settings → Security → Require login. Without it, Commissary trusts anyone who can reach it as the admin — fine at home, not fine on the internet. Then put it behind HTTPS and turn on \"Trust reverse proxy\".",
    },
    {
        title: "Earlier in 1.6.13 — set a show up before you own it",
        description: "the settings that decide how a title downloads were locked behind already having downloaded it. Manage only appeared for things already in Plex, which put Series type and \"Also known as\" — the two fields that make anime releases match at all — on the wrong side of the problem they solve.",
        features: [
            "Manage opens for any show or movie you can see, owned or not. It opens in the same place it always did, so nothing moved for titles you already have",
            "Series type can be set before the first episode arrives. Tagging a show as Anime is what lets a release numbered \"- 03\" with no season match, so needing an episode in hand first was backwards",
            "settings made this way are keyed to the title itself, not to a library row that does not exist yet. When the show is scanned in, what you set is already there — you do not set it twice",
            "the same applies to \"Also known as\" from 1.6.12: add the name a fansub group uses the moment you decide to follow something",
        ],
        usage_note: "Open any show — including one you have not downloaded — and click Manage.",
    },
    {
        title: "Earlier in 1.6.13 — watchlist posters that would not load",
        description: "reported as posters \"sometimes\" staying blank. Three separate causes producing one symptom, which is why it looked random.",
        features: [
            "a follow stored a SNAPSHOT of its poster. Following a show you own saved a link to that library entry, and those links break when Plex re-keys an item during a scan — so the art silently died some time after you followed it. Posters now come from the live library entry, re-resolved on every load, which also fixes the status and episode counts that were going blank alongside them",
            "following a show you did not own yet saved no art at all, and nothing ever filled it in once the show arrived. It does now",
            "some posters were loaded by your BROWSER straight from TMDB, depending on which page you clicked Follow from — the detail page went through Commissary, the search and discover cards did not. Those failed on any device that could reach Commissary but not TMDB. All of it now goes through Commissary and is cached on disk",
            "when a poster did fail, the card hid the broken image and left an empty tile rather than falling back to its placeholder — which is what made a missing poster look like a broken card",
        ],
    },
    {
        title: "Earlier in 1.6.12 — tell it what a show is also called",
        description: "the deterministic answer to releases being rejected as a wrong title. Everything before this depended on TMDB happening to list the right alias; this does not depend on anything.",
        features: [
            "an \"Also known as\" box on any movie or show, in the manage panel beside Quality profile and Series type. One name per line. A release named any of them passes the title check",
            "it feeds BOTH title resolvers — the hourly drain, RSS instant-grab and the monitor's retries, and searches you run by hand. Wiring only one of those would have recreated the exact bug shape of the last three releases, where the automation could do something the manual path could not",
            "deliberately not part of the metadata editor, which pushes edits to Plex/Jellyfin and locks the field there. Your media server has no concept of a matching alias, so these stay local and never touch it",
            "names are shared across every copy of a title. A show mirrored on two servers is two rows internally, and it would be pointless if which one you happened to open decided whether your alias worked",
            "movies have it too — foreign films hit the same problem as anime",
        ],
        usage_note: "Open a movie or show, Manage, then \"Also known as\". If a release keeps getting rejected as a wrong title, paste the name it uses.",
    },
    {
        title: "Earlier in 1.6.11 — episodes without a season number in the name",
        description: "a follow-up to 1.6.10, and the same shape of bug: something the hourly automation could do that a search you ran yourself could not.",
        features: [
            "searching by hand for an episode whose release carries no S01E03 — fansub anime numbered just \"- 03\", or a daily show named by air date — always failed with \"Not a single episode\". The automation grabbed the identical release without complaint, because it passes the episode's air date and absolute number and the search did not. All four search entry points now pass them",
            "those hints are worked out for every show, not only ones tagged as anime. They can only ever ACCEPT a release, never reject one, so computing them always is safe — and it means a show with the wrong series type set doesn't quietly break search",
            "the message itself was misleading. \"Not a single episode\" sounded like the file was a season pack; it actually meant no episode number could be found in the name at all. It now says that, so the next one is self-explanatory",
        ],
        usage_note: "Nothing to configure. If a release still gets skipped, the reason now tells you which check it failed.",
    },
    {
        title: "Earlier in 1.6.10 — trackers you can pick, titles that actually match",
        description: "two reported bugs, both of which turned out to be the app asking you for information it never gave you.",
        features: [
            "\"Preferred trackers\" appeared not to save. It stores Prowlarr indexer IDS, and nothing in Commissary ever displayed those ids — so the natural thing, typing a tracker's name, was silently discarded and the field redrew empty. It's a pick-list of your real indexers now. The stored value is still ids, so nothing downstream changed",
            "a manual search compared releases against ONE title, while the hourly drain compared against the full alternative-title list. That's backwards — the manual path is where you're watching and expecting it to work. All four search entry points now use the same list",
            "a show's ORIGINAL title is now part of that list. It was never included, which mattered most for anime released under a translation of the original rather than the name shown here",
            "every fallback keeps the previous behaviour: no Prowlarr means the old ID box, and an unreachable TMDB means matching on the primary title alone — never on nothing, which would have disabled the check entirely",
        ],
        usage_note: "Preferred trackers live in Settings → Connections → Libraries. Alternative titles come from TMDB, so they need a TMDB key configured.",
    },
    {
        title: "Earlier in 1.6.9 — less to download, and posters kept locally",
        description: "a measured pass over what the browser actually pulls down. The headline finding was that nothing was compressed at all.",
        features: [
            "gzip on text responses. Measured on the wire across one page load, the text assets went from 3.90 MB to 0.64 MB — style.css alone from 1.94 MB to 296 KB. Images are deliberately skipped: they are already compressed, so gzipping them costs CPU to make them fractionally bigger",
            "the app shell (~1 MB of markup) had no cache validator at all and was re-downloaded in full on every visit. It now revalidates and gets a 304",
            "video posters, backdrops and stills go through the on-disk image cache the music side already used — the video proxy simply was not wired to it, so every request went back out to Plex or TMDB. They are also served with a validator now, so a repeat view is a 304 rather than a full refetch. A failed refresh serves the cached copy rather than a hole",
            "that also takes load off the server: artwork was fetched with a blocking call per image on a pool of eight threads, so a grid of posters could crowd out ordinary page requests",
            "off-screen images defer until their page is opened",
        ],
        usage_note: "Nothing to configure. The poster cache lives under storage/image_cache and honours the existing image cache settings.",
    },
    {
        title: "Earlier in 1.6.8 — browse to the file, don't type its path",
        description: "manual import used to mean typing a full absolute path by hand, every time. It now opens where your downloads land and lets you walk to the file.",
        features: [
            "the Add a file dialog opens on your download folder already listed — folders and video files, with sizes. Click a folder to go in, Parent folder to come back out, a file to select it",
            "shortcut buttons across the top jump straight to your Soulseek download folder, each torrent and usenet folder, and every configured Library. Folders that aren't currently mounted are left out rather than offered as dead links",
            "only video files are listed, using the same check the import itself applies — so nothing the browser shows you can be rejected at the next step. Dotfiles and stray .txt/.nfo files are filtered out",
            "the text field stays: pasting a path you already have is still quicker than clicking, and selecting a file just fills it in",
            "browsing the server's folders is admin-only",
        ],
        usage_note: "Import → Add file. If it opens somewhere unexpected, that's your Download folder from Settings → Downloads.",
    },
    {
        title: "Earlier in 1.6.7 — everyone can ask, you decide",
        description: "shared installs get a middle ground between \"full download rights\" and \"no access at all\" — a user can put a show on the Watchlist, and an admin approves before anything is acquired.",
        features: [
            "follow a show without download rights and it lands on the Watchlist marked \"Awaiting approval\" — visible immediately, so the person who asked can see it — while every acquisition path skips it. Admins get Approve and Decline on the card, with the requester's name",
            "the monitor policy chosen at follow time (all seasons, first season, latest season, pilot) is stored with the request and expanded on APPROVAL, so approving gives the back catalogue that was actually asked for rather than silently downgrading it to future episodes only",
            "there are three separate routes by which a follow becomes a download — the expansion at follow time, the daily airing job, and the people/studio scans — and all three are gated. Following a PERSON auto-wishlists their filmography, so that one matters as much as shows",
            "Sign in with Plex now grants the video side on first sign-in. Downloads stay off: that is the actual boundary, and every download-triggering endpoint is still behind it",
            "which meant tightening endpoints that were unreachable while Plex users were music-only — cancelling downloads, clearing download history and emptying the wishlist now need download rights, and a user can only withdraw their own pending request, not un-follow the shared Watchlist",
            "existing follows are untouched: everything already on your Watchlist stays approved and keeps acquiring",
        ],
        usage_note: "Admins: the Watchlist page is where you approve. To let someone ask, give their profile video access with downloads off in Settings → Users.",
    },
    {
        title: "Earlier in 1.6.6 — downloads go where the title actually lives",
        description: "a sweep through everything the move from one Movies + one TV Shows library to real multi-library support left behind — plus the release-title gate that was refusing anime.",
        features: [
            "an episode of a show filed under Anime downloaded into the standard TV folder. Two separate causes: the per-episode ⬇ buttons on the detail page sent no Library at all, and the Get modal's Library dropdown pre-selected the first entry and then sent THAT explicitly — which is worse than sending nothing, because it overrides the fallback that would have got it right. The dropdown now opens on the Library the title is already in, and the server infers it regardless of what the page sends",
            "releases carrying a work's full official title were rejected as wrong titles. TMDB stores \"The Frontier Lord Begins with Zero Subjects\"; the release says \"…Zero Subjects: Tales of Blue Dias and the Onikin Alna\". Titles are now also compared on the part before the colon — guarded so \"Dune: Part Two\" still cannot satisfy a search for \"Dune\", and the wanted title is never split (nothing named \"Mission: Impossible\" will match a search for \"Mission\")",
            "the wishlist had no Library of its own — it could only infer one from a title you ALREADY owned. A show you had just added had none, so it drained into the primary Library no matter which one you meant. It records its own now",
            "saving Settings → Libraries deleted every YouTube Library row, because that page only sends Movies and TV. The YouTube root silently vanished from health checks, the recycle bin and moved-file resolution",
            "the Library page's genre and quality dropdowns showed every value across all Libraries of a kind, so on an Anime tab you could choose a genre that matched nothing. They follow the tab now",
            "placing a file by hand falls back to the Library the chosen title already lives in, instead of the primary",
        ],
        usage_note: "Nothing to configure — but if you had set a Destination Folder or category on a Library and downloads were ignoring it, they will start honouring it now.",
    },
    {
        title: "Earlier in 1.6.5 — the Library tabs read properly",
        description: "follow-ups to 1.6.4's multi-library work, all reported from a real multi-library setup: tabs that looked like duplicates, tabs with no counts, and a search that could never find a show.",
        features: [
            "a Library named \"Movies\" under a tab named \"Movies\" read as the same entry listed twice — so did \"TV\" next to \"TV Shows\". The kind tab now names itself as the union (All Movies, All TV, All Shows) whenever its own Libraries are listed beside it. With only one Library for a kind there's nothing to split, so the plain label stays",
            "per-Library tabs finally carry a count, the way the Movies/TV tabs always have. A show Library counts DISTINCT SHOWS rather than episodes, matching what the All TV total counts — so three wished episodes of one anime series read as 1 under All TV 1, not 3",
            "the badge and the list it labels are computed by the same code, so they can't drift apart. A Library with a blank destination path counts zero rather than claiming the whole history",
            "Place this file could never find a series: searching a show name only ever returned it under the Movie tab. The picker was checking fields the search endpoint doesn't send (media_type / type / first_air_date), so every result fell through to a \"movie\" default. It reads the actual kind now, and shows appear under Episode",
            "the Wishlist deliberately offers no per-Library YouTube tabs — that list has no Library filter behind it, so such a tab would have quietly shown everything. Download History keeps its YouTube Library tabs, where the filter does work",
        ],
        usage_note: "Nothing to configure. The tabs are on the Wishlist page, the Download History modal, and Manage Workers.",
    },
    {
        title: "Earlier in 1.6.4 — pick your groups, your trackers, your Libraries",
        description: "two new download preferences, plus a sweep through the multi-library surfaces added in 1.6.3 — several of which turned out to be reading the wrong list entirely, which is why they showed blank options and only ever worked for admins.",
        features: [
            "Preferred Groups (Settings → Downloads → Quality): type a release group — FLUX, NTb — and releases from it score higher. It creates a normal Custom Format under the hood, so you can open the table below and fine-tune the score or swap in a regex whenever you want",
            "Preferred Trackers, per Library: each Library gets its own field, so an Anime library can favour one indexer while standard TV favours another. Both preferences are SOFT — Prowlarr still searches every indexer you allow, a preferred hit just ranks higher, so a Library whose favourite tracker has nothing this week never comes up empty",
            "they apply everywhere a release gets picked: the hourly wishlist drain, RSS instant-grab and the manual search modal all rank through the same code, so what you see in the modal is what the automation would have chosen",
            "Wishlist and Download History: the per-Library filter is now a TAB next to Movies/TV/YouTube instead of a dropdown, and it works for every profile. Both were reading the live server-section list — admin-only, and carrying no Library id — instead of your configured Libraries, so they rendered blank entries that filtered nothing",
            "Enrichment Workers: the per-Library tabs all displayed as the word \"Library\" and clicking one lit up several at once, because they shared one identical value. Same root cause, same fix",
            "the Library dashboard widget stops adding your Libraries together: Movies, Shows and Disk Size were single totals spanning every Library of that kind. Each Library now gets its own tile with its own count and its own disk usage",
            "manual import files to the right place: a file you added by hand was always recorded as a movie, so the Place dialog opened on the Movie tab and an episode landed in the movie destination. It now reads the kind from the filename — including fansub naming like \"[SubsPlease] Show - 40\", which carries no season anywhere — and the dialog lets you choose which Library it lands in",
        ],
        usage_note: "Preferred Groups is in Settings → Downloads → Quality, just above Custom Formats. Preferred Trackers is on each Library in Settings → Connections → Libraries, and takes Prowlarr indexer IDs (e.g. 1,3) — leave it blank for no preference.",
    },
    {
        title: "Earlier in 1.6.3 — import anything, filter by Library, and a dashboard that finally responds",
        description: "manual import no longer needs Commissary to have failed a download first, two bugs surfaced while chasing a failed grab got fixed, and dashboard customisation on the Video side turned out to have never worked at all.",
        features: [
            "Add file… on the Import page queues ANY video file on disk for placement — one moved in by hand, or left over from another tool — through the same place/dismiss queue, with no prior download needed",
            "fixed a real risk that surfaced alongside it: after a copy-mode import, Commissary reclaimed (deleted) the source file. A file you added yourself is your own copy, not a download client's temp file — placing it now never deletes your original",
            "fansub anime releases stopped being rejected: '[SubsPlease] Title - 40 [1080p]' was thrown out as a title mismatch because the group tag and the glued-on episode number leaked into the parsed title. Scoped narrowly to bracket-tagged releases, so ordinary names that legitimately end in a number — 'Moana 2' — are untouched",
            "grab failures say why: every one of the four video download helpers threw away the backend's error message on a failed request, so every cause produced the same blank \"Grab failed\" toast",
            "Download History, Wishlist and the Library page gained per-Library filtering, and shows gained an Airing / Ended / Upcoming filter",
            "dashboard customisation was dead on the Video side — dragging and resizing did nothing. The video stylesheet pinned every card to a fixed CSS order, which always beats the DOM reordering the drag actually performs; the move was being saved, just never shown. The markup now carries the intended layout and the conflicting CSS is gone",
        ],
        usage_note: "Import is in the Video sidebar; the Add file… button takes a full path to the file. Customize is in the top-right of the dashboard.",
    },
    {
        title: "Earlier in 1.6.2 — unattended grabs respect your Libraries",
        description: "automatic downloads always routed to the primary Library for the kind, ignoring which Library the title was actually filed under. So an Anime show already sitting in your Anime library had its new episodes dropped into standard TV.",
        features: [
            "all four unattended paths are covered: the wishlist drain, RSS instant-grab, \"Search now\", and repair-job upgrades",
            "the cause was that unattended grabs resolved ONE destination for a whole batch and reused it for every item. The interactive Get flow already did this correctly — this brings the automation up to the same standard",
            "the torrent-client category follows the same rule, so a grab's destination folder and its category always come from the same Library",
            "titles not yet assigned to any Library still fall back to the primary one, exactly as before",
        ],
        usage_note: "Nothing to configure — it reads the Library each title is already filed under.",
    },
    {
        title: "Earlier in 1.6.1 — stuck downloads now finish",
        description: "a torrent or usenet release that landed as a single file — a bluray remux, a lone episode — directly in a category folder, with no folder of its own, could sit at 100% forever and never import. No error, no log, just silence.",
        features: [
            "path resolution required a DIRECTORY at every step, so a release that was just one bare file (no folder wrapping it) never matched, even when it sat exactly at the configured Completed Downloads Path with the exact right name",
            "it now accepts a matching FILE the same way it already accepted a matching folder — checked one level into category subfolders too, same as folders",
            "affects both the torrent/usenet video pipeline and the equivalent music path; nothing to reconfigure",
        ],
        usage_note: "Downloads already stuck at 100% pick this up on their next poll (every few seconds) once you're on this version — no need to re-add them.",
    },
    {
        title: "Earlier in 1.6.0 — upstream's fixes, folded in",
        description: "this fork branched from upstream SoulSync 3.1.5, which has since shipped three releases. 38 of their fixes are now here — a security fix, several that stop half-written downloads being imported, and a long tail of tagging and organising corrections. None of their chat work came with it.",
        features: [
            "SECURITY: torrent and usenet search results used to carry the raw indexer download URL — API key included — all the way into the browser, where it lived in DevTools and history, and the download endpoint would accept any URL sent back to it. Results now carry an opaque server-side token, so a client can no longer make Commissary forward an arbitrary URL to SABnzbd, NZBGet or qBittorrent",
            "half-written imports: a client reporting \"finished\" doesn't mean it has stopped writing — unpack and repair can still be working in the staging folder. Commissary now waits for that folder to actually stop changing before importing, and video files land in the library atomically and size-verified (this is the cause of files that played back skipping)",
            "tagging stopped overwriting you: simple downloads now only fill blank or placeholder tags instead of replacing real ones, and two rounds of false-positive retag warnings are gone",
            "the reorganiser behaves: no more cosmetic casing churn on files it already organised, your own album year survives instead of being replaced by the source's, featured-artist credits stay in the title and filename, and single-disc albums stop getting a bogus disc prefix",
            "Plex deep scans survive a slow library: one slow page used to hit a hard 15-second timeout and zero the whole scan, reported as \"zero artists\". Now 30 seconds, configurable, with retries on the bulk fetches",
            "smaller ones: HiFi sources fail over on 429/403 instead of stalling, SABnzbd category handling matches what actually gets submitted, music videos file under the real artist rather than the uploader's channel, Discover caches its genre deep dives instead of re-fetching for 30 seconds on every click, and a 160-line block that had been pasted twice into the wishlist filters is gone",
        ],
        usage_note: "Nothing to configure. The Plex scan timeout and retry count are plex.request_timeout_seconds and plex.scan_retries if you ever need to raise them.",
    },
    {
        title: "Earlier in 1.5.0 — less to carry",
        description: "the Soulseek chat feature and the upstream donation button are removed, and Plex now recognises Commissary across restarts instead of announcing a new device every time the container starts.",
        features: [
            "chat removed completely: the rooms and private-message page, the nav entries on both the Music and Video sidebars, the \"message this user on Soulseek\" buttons on search results and uploader credits, and the stored message archive — the database sheds that table on upgrade",
            "your Soulseek connection is unaffected. Search, browsing and transfers ride the same slskd link they always have; only the chat endpoints are gone",
            "the Support Commissary button and its donation links are removed from the sidebar, the README and the Unraid template. Docker image names in the install docs are untouched — those are pull commands, not donations",
            "Plex device identity is now stable: Commissary previously identified itself with the machine's MAC address and hostname, which Docker regenerates on every container start — so Plex saw a brand-new device after each reboot and mailed you about it, filling your device list with anonymous \"Linux\" entries. It now mints one identifier, keeps it in your config, and reports itself as \"Commissary\"",
            "expect one final new-device notification the first time you start this version — after that Plex recognises it",
        ],
        usage_note: "Nothing to configure. If you want to tidy up, the old anonymous Linux entries can be removed from plex.tv → Settings → Authorized Devices.",
    },
    {
        title: "Earlier in 1.4.0 — a dashboard you arrange yourself",
        description: "the dashboard's layout was baked into the markup — same order, same widths, for everyone. Now any user can drag their cards around and resize them. Plus: torrent and usenet completed-downloads folders accept more than one path, fixing imports that never happened because the client sorted downloads into category folders.",
        features: [
            "Customize (dashboard header): drag a card to reorder it, drag its right edge — or focus the handle and press ←/→ — to set it 1, 2 or 3 columns wide. Reset restores the shipped arrangement. Music and Video keep separate layouts, and it's saved per browser",
            "available to EVERY user, not just admins — this is a personal view preference, not a permission. The admin's hide/show policy still applies on top: a card an admin has hidden can't be moved or resized because it isn't there",
            "widths survive narrow screens properly: a card you widened on a desktop clamps to the row on a 2-column layout and goes full-width on a phone, instead of overflowing the grid",
            "multiple completed-downloads paths (torrent AND usenet): clients sort finished downloads into category folders like /downloads/complete/Movies and …/TV-Shows, but only one folder could be configured and only one level was searched — so the release sat there finished and never imported. Add a row per folder, or just name the parent and let Commissary look one level inside it, so a category you add later still resolves",
            "the deeper search is still content-checked and exactly one level deep — it won't grab a same-named folder from the wrong category, and it won't crawl your whole download disk",
        ],
        usage_note: "Customize is in the top-right of each dashboard, next to Watchlist/Wishlist. The download paths are in Settings → Downloads, in the Torrent and Usenet client sections.",
    },
    {
        title: "Earlier in 1.3.2 — settings that describe what they actually do",
        description: "video destination folders were configured in two places with no explanation of how they related — and four subsystems quietly read only one of them. Your Libraries are now the source of truth everywhere, the Downloads boxes say plainly that they're fallbacks, and undoing a purchase becomes an admin action.",
        features: [
            "your video Libraries drive everything now: free-space health checks, the recycle bin, the moved-file resolver and the naming-repair job all read the Destination Folder on each Library in Settings → Connections. They previously read ONLY the flat Movies/TV boxes under Downloads — so setting your paths per library and leaving Downloads blank meant no health checks, a recycle bin that couldn't recycle (every delete permanent) and a naming job that skipped every file",
            "two libraries of the same kind are both covered — a single flat path could only ever describe one of them, so an \"Anime Movies\" alongside \"Movies\" was never health-checked or recycled from",
            "the Downloads folder boxes are relabelled: Movies and TV Shows are fallbacks that apply when a Library has no Destination Folder of its own. YouTube is flagged as the exception — it isn't a fallback, it's where followed-channel downloads always land, because Libraries cover movies and shows only",
            "undoing a purchase is admin-only, enforced on the server instead of by hiding a button. Recording one stays open to every profile. Both Unmark buttons hit the same call, so the per-track one goes too for standard profiles — unmarking each track in turn was the same act",
            "the floating Interactive Help ? button can be hidden from standard users, in a new \"Both Sides\" group since it isn't a Music or a Video element",
        ],
        usage_note: "Per-library Destination Folders are in Settings → Connections → Libraries; the fallbacks are in Settings → Downloads. The help-button toggle is with the rest under Settings → Users → Standard User Interface.",
    },
    {
        title: "Earlier in 1.3.1 — the sidebar joins in",
        description: "1.2.0 let you trim the dashboard for standard users. This extends the same switch to the sidebar's System group and splits Manage Workers into its own toggle — so a standard profile can be given a genuinely reduced app, not just a reduced home page. Plus: albums in the Purchased list now fold away.",
        features: [
            "collapse albums in Purchased (1.3.1): click an album header or its chevron to fold its tracks away, with Collapse all / Expand all next to the search box. Collapsed albums stay collapsed through the refresh that follows an unmark, and across reloads",
            "Automations, Chat and Tools under System can be hidden from standard profiles, on both the Music and Video sidebars. Chat previously had no way to hide it at all, and Tools had server-side support with no checkbox ever offering it",
            "hiding a sidebar entry blocks the PAGE, not just the link — a hidden Chat or Tools redirects to the profile's home page instead of loading, so it can't be reached from a bookmark or a typed URL",
            "Manage Workers is now its own checkbox on each dashboard, separate from the enrichment/repair icons it used to be bundled with. An install that hid the combined 1.2.0 control keeps Manage Workers hidden — the setting is carried across on upgrade",
            "no checkbox for Video Automations: it already requires admin, so offering one would be a control that does nothing",
            "this is one global policy covering every standard profile, so a Plex user who signs in tomorrow inherits it with no per-person setup. The per-profile page list under Manage Profiles still applies on top — hidden by either means hidden, and nothing you've already configured loosens",
        ],
        usage_note: "Settings → Users → Standard User Interface (renamed from Standard User Dashboard, since it now covers the sidebar and header too). Admins always see everything. The Purchased collapse controls are on the Purchased page itself, under Music.",
    },
    {
        title: "Earlier in 1.2.0 — a dashboard that fits the person looking at it",
        description: "the dashboard showed every card to everyone. A standard user — usually someone who signed in with Plex — got Service Status, System Stats and Quick Actions: operator tooling they can't use, and the only way to remove it was to take the whole page away. You now pick, card by card, what standard profiles see. Admins always see everything.",
        features: [
            "Settings → Users → Standard User Interface: one checkbox per card, covering BOTH the Music dashboard (Service Status, System Stats, Library, Recent Syncs, Quick Actions, Recent Activity, Active Downloads, Enrichment Services) and the Video one (Recently Added, System Stats, Library, Upcoming, Quick Actions, Studios), plus the enrichment controls in each dashboard header",
            "admins are exempt by design — this only shapes what standard profiles see, so you can hand someone a trimmed dashboard instead of hiding the page from them entirely",
            "hidden means hidden, not just invisible: a hidden card's fetches and refresh timers never start, and the live push updates it would have rendered are dropped. The dashboard runs a 2-second activity poll and two 10-second polls, so a standard user with those turned off stops making roughly two thousand pointless requests an hour",
            "nothing changes on upgrade — every card starts visible, exactly as before, until you uncheck something",
            "fixed alongside: every visit to the dashboard started another set of refresh timers without stopping the last, so polling quietly accelerated the longer a session stayed open",
        ],
        usage_note: "Settings → Users → Standard User Interface. The setting is one policy covering both dashboards, so it reads and saves the same from either the Music or Video side. Service Status is hidden from the dashboard but still feeds the sidebar indicator every user sees.",
    },
    {
        title: "Earlier in 1.1.0 — purchases, and settings that mean it",
        description: "marking music purchased finally keeps a record — a new Purchased page, and whole albums in one action. On the settings side: the fields on the Video page that silently discarded your edits now save, every configured Video Library is visible to non-admins again, settings that existed twice are down to one, and four controls that did nothing are fixed or gone.",
        features: [
            "Purchased (Music sidebar): marking a track bought files it permanently instead of just clearing a flag, grouped by album with per-track and whole-album unmark. Mark an ENTIRE album purchased in one action — including tracks that were never on the to-buy list. The drill-down cart icon now cycles neutral → to-buy → purchased",
            "video settings that save: the Video side shows the same settings page as Music, but its Save button flushed only the video endpoints and auto-save was off there — so Prowlarr, the torrent/usenet clients and the whole Appearance, Security and Database tabs discarded every edit made from Video while working from Music. They now persist from either side, and editing a video-only field still can't touch the music config",
            "every Video Library visible to members: the endpoint feeding the Library tab bar was admin-only and the page swallowed the refusal, so non-admins — notably Plex sign-ins — only ever saw the default Movies and TV Shows tabs and had no download destination picker. Members get the configured library list; live server discovery and filesystem paths remain admin-only",
            "one setting per thing: torrent seeding goals and the minimum-free-disk floor each existed on BOTH sides for the same physical resource — two sweeps could push conflicting share limits at the same torrent client, and the disk floor had different defaults (5 GB vs off). Now single shared settings, with existing pairs merged toward whichever value deletes less",
            "video Plex/Jellyfin credentials are encrypted at rest like the music ones, instead of sitting in plain text, and existing ones are migrated out of the clear",
            "four dead controls: the 'Where to watch' region was read from a key nothing wrote (the streaming overlay was stuck on US), the audio preference never reached the release scorer, the YouTube Libraries editor wrote rows nothing read while claiming the first was your default destination, and the YouTube cookie fields — used for video downloads too — couldn't be reached from the Video side",
        ],
        usage_note: "Purchased is in the Music sidebar under Wishlist; mark a whole album from its header in the library drill-down. The now-shared settings (seeding goals, minimum free disk, Prowlarr, torrent/usenet, appearance, security) show the same value whichever side you open them from.",
    },
    {
        title: "Earlier in 1.0.0 — the chat + discography release",
        description: "chat goes best-in-class (any public room, user shares, history search), you choose which source paints your discographies — and see what the others know — the wishlist learns artists and smarter retries, Fix All runs in the background, and multi-user gets a security hardening pass.",
        features: [
            "chat, best in class: join ANY public soulseek room via a rooms rail + full room browser, a real user list (roles, sorting, local mute), browse any user's shared files and download them right from chat, search your message history, copy any message, and a redesigned composer",
            "choose your discography source (thanks ragnarlotus, #1068): a Library Discography Source setting — primary, automatic fallback, or a specific source — decides what paints library artists' discographies, and an artist a source genuinely doesn't know no longer reads as an error",
            "see what other sources know (#1067): an 'Other sources' view option appends releases your current view is missing, slotted into the real Albums/EPs/Singles sections with their source marked — each downloadable, Download Discography includes them, off by default and purely additive",
            "wishlist: select/download/remove a whole artist's entries at once (#1065), real attempt counts, progressive retry backoff instead of hammering every cycle, and a configurable auto-ignore TTL (thanks javiavid)",
            "search by musicbrainz id (thanks Jordan H): paste a bare MBID and it resolves straight to the release, lidarr-style",
            "tools: Fix All runs in the background with live progress + a Stop button so a 5000-finding retag can't time out the page (thanks pertti), Album Tag Consistency explains exactly which albums it excluded and why + warns when files weren't readable from soulsync's side (thanks clouddead89), adjustable findings page size, and Genre Tag Cleanup scans the whole library (#1066)",
            "multi-user hardening: profile-scoped APIs verify ownership, deleting a profile sweeps every referencing table, and socket rooms derive from the session — one profile can't see or touch another's data",
            "reported fixes: singles/EPs no longer file as Albums when the source has no type signal (#1064), the artist photo picker works on Navidrome/Jellyfin (#1069), enabling Usenet in source priority survives reload (thanks Fl3m), Retry All Failed on the music workers modal, and video's 'block release and retry' actually retries with another release",
        ],
        usage_note: "the discography source lives in Settings → Metadata; 'Other sources' is a toggle in the artist page's filter row. rooms + user shares are on the Chat page. wishlist artist tools appear when you group by artist.",
    },
    {
        title: "Earlier in 3.1.4 — the tools + requests release",
        description: "two new library-maintenance jobs (comma artist splitter + genre cleanup), ReplayGain loudness targets, the video Requests page grown up, seed limits your torrent client can enforce itself, every logo shipping with the app, and a big stack of reported fixes.",
        features: [
            "comma artist splitter (thanks jadux): a Tools job that finds fake combined artists like 'Camellia, Toby Fox' and splits their tags safely — real comma artists like 'Tyler, The Creator' are recognized via the metadata APIs and left alone, every part must be a known artist before anything is flagged, and each finding shows exactly how it will split with clickable chips to the real artists. approving re-tags the files with a proper multi-artist tag and your server dissolves the dummy on its next scan",
            "replaygain target loudness (#1060): set the reference (default -18 LUFS) every RG write analyzes against, plus an opt-in re-run over tracks computed against a different target; genre tag cleanup (#1057): re-check genres stored before strict filtering was enabled, removal-only",
            "fix all actually fixes all: the Tools bulk-fix silently skipped some finding types its own counter included ('fixed 0 of N') — the fixable set is now derived from the fix handlers; artist pictures on findings click through to the artist's page",
            "video requests, best in class: approved requests show 'Acquiring…' until the title lands in your library then flip to 'In library', status tabs with counts, removable history + a Clear-resolved sweep, and no more success toast while the row still says Approve",
            "seed limits your client enforces (thanks TheHomeGuy): an 'Enforced by' toggle (music + video) writes ratio/time goals into the torrent as native share limits so the client stops seeding on its own even if Commissary is down — and stall-pause works on qBittorrent 5.x",
            "the wishlist failing hub (thanks LiveLeak): a '⚠ Failing' filter chip on the video wishlist, a manual release picker on every movie/season/episode, and music's 'Search manually' now lands on the actual soulseek search prefilled",
            "every logo ships with the app: ~86 hotlinked images from 10+ external CDNs now load from your own server — no more broken logos from rate limits, dead URLs, or LAN-only installs",
            "reported fixes: downloads freezing mid-batch + a metadata identity guard (jadux), re-releases finally download — analysis respects release years (5BILLION), deep scan removes artists on an empty Navidrome (5BILLION), unchecking chat auto-join actually leaves (popwaffle9000), $year renders for TV renames (musicagine), a source-search timeout knob (#1056), airing shows catch up missed days",
            "community: enrichment workers idle-backoff their polling (#1054, thegabriele97), discographies fall through the provider chain when the primary source is down (#1032, ragnarlotus)",
        ],
        usage_note: "the new jobs live on the Tools page (Comma Artist Splitter and Genre Tag Cleanup are report-only until you approve findings). seed enforcement and the timeout knob are under Settings → Downloads / Soulseek.",
    },
    {
        title: "Earlier in 3.1.3 — follow record labels",
        description: "follow a record label the same way you follow an artist and Commissary watches it for new releases — plus music torrents can now seed on a leash, and two reported fixes (multi-disc display + write-tags efficiency).",
        features: [
            "follow record labels: search finds labels, and each label gets a real refreshable page showing its whole catalog newest-first in album cards, with an ownership overlay for what you already have, filters and sort, and every release linking through to the real artist (never the label)",
            "the watchlist page gets a Labels tab with follow / backlog controls, and the normal watchlist scan now checks your followed artists AND labels in one pass with one live display — the scheduled watchlist automation included. follow no labels and nothing changes",
            "seed music torrents on a leash: set a seed ratio and/or time goal in Settings → Downloads and a completed grab is removed from your torrent client once it hits the goal (the client's own copy only — your imported library file is separate and untouched). strictly opt-in, both goals default to off",
            "multi-disc albums display right (#1051, thanks Tacobell444): an album whose tags all say disc 1 no longer drops or misplaces disc-2 tracks in the enhanced view (rows were keyed by disc+track and collided), and Disc # is now editable inline like Track # / Title so you can fix bad disc tags and write them to the file",
            "Write Tags only touches the files that changed (#1052, thanks Tacobell444): the batch write diffs each file against the DB first (the same comparison the preview shows) and skips the ones already correct instead of rewriting every file — server sync only pushes what changed too",
        ],
        usage_note: "labels: search a label name, open it, and hit follow. seeding goals and torrent settings live under Settings → Downloads.",
    },
    {
        title: "Earlier in 3.1.2 — Commissary gets a chat",
        description: "a full Soulseek chat page — the community 'Commissary' room + private messages, Discord-class — plus the artist photo picker finally works, SoundCloud links resolve anywhere, and two long-standing reported bugs die.",
        features: [
            "Chat (System section, both sides): the community 'Commissary' room and private messages, proxied through slskd. rich messages other Soulseek clients can't read (bold / code / spoilers / emoji, image + YouTube embeds, Commissary deep links), @mentions with autocomplete, replies, reactions, GIF search, a local archive that survives slskd restarts, and an auto-responder for anti-leech bots. sending is admin-only by default",
            "the artist photo picker actually delivers photos now (Deezer, Spotify authed OR free, iTunes, AudioDB, Discogs, plus paste-a-URL), and one transient source hiccup no longer sticks 'no photos found' for 15 minutes",
            "SoundCloud links resolve wherever you paste them, including unlisted/private share links (#865 follow-up); deep scan removes artists after switching to an empty Navidrome library, and re-releases stop showing as owned on the library page (both from 5BILLION's reports)",
        ],
    },
    {
        title: "Earlier in 3.1.1 — continue watching + the reported-bugs sweep",
        description: "the video detail pages learn everything your server knows about what you've watched, and a stack of reported music bugs — re-releases showing owned, playlist sync leaving tracks behind, force download not forcing — all die.",
        features: [
            "continue watching: per-episode watch state scanned from plex/jellyfin — checkmarks, progress bars, a Next Up highlight, the hero button becomes 'Resume S2 E4 on Plex' deep-linking the episode, shows open on the season you're actually in, and a Mark watched/unwatched toggle pushes played state back to your server",
            "detail pages surface what soulsync already knew: the 🏆 awards line, an after-credits-scene tag, NEW badges on freshly-landed episodes, digital release dates, your file's ranked quality name, and 4K · HDR · DV · Atmos · 7.1 format badges on owned movies (real stream data on jellyfin, release-name parsing on plex)",
            "re-releases no longer show as owned: owning the original doesn't claim the remaster/anniversary edition anymore — album matching respects the release year on both sides",
            "playlist sync stops leaving tracks behind (#1047): matches in the 0.70–0.79 band were found then thrown away, stale plex ratingKeys failed silently, and big playlist writes partially landed unchecked — all three fixed, writes now chunked and verified against what the server stored",
            "deep scan finally removes artists that left your library, reads the server fresh instead of a stale cache, and refuses to mass-delete on a failed server call — a plex hiccup can't wipe your artist list",
            "repeatedly-failing wishlist downloads get an attempt counter, a failing badge, a see-only-failing filter, and a jump straight into manual search (music + video, thanks LiveLeak); force download actually replaces the file on disk (#1045)",
            "per-show Synchronize: a deep scan scoped to one show that reconciles episodes right now, survives plex re-keys, and refreshes the airing schedule — and vanished episodes demote to 'missing' instead of being erased",
            "the request flood is gone: duplicate api GET bursts dedupe to one wire request, enrichment status hydrates in one bundled call instead of ~28, and steady-state pollers slow down + skip hidden tabs",
            "smaller fixes: digit-named artists (311) open again, the whole-library m3u reports itself in the scan summary (#1041), video genres/keywords/where-to-watch are real links (#1042), mass rename previews big libraries in the background with live progress, youtube episode numbering trusts the real upload date",
        ],
    },
    {
        title: "Earlier in 3.1.0 — the video side grows up",
        description: "the video side gets a full Sonarr/Radarr-class acquisition stack, a best-in-class pass over every page, and a wave of reported bugs (storage bleeding, torrents not moving, the wrong song downloading) all die.",
        features: [
            "video acquisition, Sonarr/Radarr parity in eleven pieces: RSS instant grabs (a wanted release lands minutes after it hits your indexers, not at the next hourly sweep), per-title quality profiles + monitor policies, custom formats (scored release-name matchers), an in-app requests system, torrent seeding lifecycle, import lists (Trakt/TMDB/IMDb/Plex watchlist), mass rename with preview, daily/anime series types + multi-episode files, per-title history, video backups + staged restore, and Discord/Telegram/webhook notifications",
            "every video page rebuilt best-in-class: calendar (movie lane, agenda view, iCal subscribe, moved to Find), wishlist (Search Now, honest status, far snappier poster art), downloads (live speed + ETA), library (size-on-disk, missing/quality filters, Largest sort), search (recent chips), discover (filter collapse toggle), and Letterboxd + per-episode external links on detail pages (#1039)",
            "version glow (Kazimir): the version number glows green for a routine update, yellow for a major release, red for critical, checking real GitHub releases and naming the version, not a commit hash",
            "notification history (Kazimir): every toast is journaled server-side so a Clear All loses nothing, with a type filter on the bell panel and a searchable History page",
            "config migration (Kazimir): export every setting for both sides as one JSON bundle to move to a new install, or import one; secrets redacted by default, credentials export gated behind login mode",
            "the downloads folder no longer bleeds storage (Kazimir's 10GB leak): failed youtube matches were cancelled while still landing recordless files, fixed at the source plus a reaper for the orphans",
            "torrents move to your configured folder (TheHomeGuy): qBittorrent reports its own container path and soulsync now verifies the release is actually there before trusting it; youtube stops grabbing the wrong song (Kazimir's 'We're Shameless'); HiFi 30-second preview clips can no longer replace real library files on upgrade (sella), with a cleanup tool for ones already in a library",
            "guided tours rebuilt against the current UI, the #1038 Library crashes fixed, and the #1040 layout bugs (sidebar bleed, artist-column clipping, orb overflow) dead",
        ],
    },
    {
        title: "Earlier in 3.0.5: the community-reports release",
        description: "eight user requests and bug reports, all shipped: imports learn exact-ID identification, lyrics travel with tracks, and a stack of 'why is this wrong' reports turned out to be real bugs.",
        features: [
            "import identifies albums by exact IDs: the spotify link in a file's comment tag resolves 1:1, and ISRC tags resolve by folder consensus (the album containing most of the folder's codes wins, so a compilation can't hijack the import), fixing text-search failures on japanese releases",
            "a track's .lrc lyrics sidecar moves with it on imports and downloads, renamed to match",
            "fix a wrong artist photo everywhere at once from the library page, tidal playlists over ~20 all load (#1035), musicbrainz same-name artists resolve correctly (#1036), and paste-cookies.txt applies to the video side too",
        ],
    },
    {
        title: "Earlier in 3.0.4 — discover 2.0 + profile side access",
        description: "the video discover page becomes a netflix-class browse, profiles can be scoped to one side of the app, and a stack of reported bugs die — including the tidal restart loss that survived two releases.",
        features: [
            "discover 2.0: a billboard hero with real title logo art, one clean header with a preferences popover, browse-by-genre tiles, live filters, and a feed that's actually endless — view more pages forever, grids respect hide-owned, and the page lazy-renders so it stays fast",
            "profile side access: each profile can be music-only, video-only, or both (new profiles default to music-only). single-side profiles never see the music/video switcher and blocked-side page options disable automatically",
            "wishlist state on every card: search results, discover cards, the hero button, more-like-this cards and the get modal all show wishlisted / in-library state instead of offering to re-add",
            "tidal download source survives restarts (#1002): a startup ordering bug wiped the saved session from memory on every docker boot before verification could run — nothing failed, so nothing logged. boot network blips retry now instead of dropping a valid session. re-add tidal to your hybrid order once after updating",
            "torrent grabs work in split-container setups: soulsync downloads the .torrent itself and hands your client the file (like sonarr/radarr) instead of passing a prowlarr url the client may not resolve — all four clients, music and video",
            "amazon music works again (#1033): t2tunes changed their api format — search, downloads and file tags (track/disc numbers, covers, dates) all read the new format, old format still supported",
            "owned artists respect the source you clicked (#1026), playlist sync no longer matches the wrong same-artist track with high confidence (#769), music + video share one slskd search budget instead of doubling it, and the websocket push loops idle completely when no browser is open (thanks thegabriele97, #1030)",
        ],
    },
    {
        title: "Earlier in 3.0.3 — quality of life across both sides",
        description: "whole-show wishlisting + a match editor on video, global automation pause toggles, and four reported music bugs dead — including the corrupt file detector that scanned nothing.",
        features: [
            "wishlist a whole show in one click: 'Wishlist Missing' on the show detail page grabs every missing aired episode across all seasons (loading ones you never browsed), and the Get Missing modal gets a matching 'Select all missing' button",
            "fix a wrong match without deleting anything: movie/show Manage panels get a Matches section — per-service rows (TMDB, TVDB, IMDb) with search, re-point, and clear. re-pointing wipes what the wrong match filled in and re-enriches by the new id; locked fields and art are never touched",
            "shows stuck with no status heal themselves: an old bug could mark a show's TMDB details done even when the call failed, leaving it with no airing info and no watchlist button forever (the 90 Day Fiancé report). a one-time migration re-queues them",
            "a global automation pause per side: one master toggle on each Automations page that gates whether anything runs without touching your individual switches. music defaults on, video defaults off — flip it on once if you use video automations",
            "Corrupt File Detector actually finds files (#1000): the scan silently skipped every file on docker/NAS setups because the path resolver had no search directories. fixed (ReplayGain Filler had the same hole), the summary reports what was really decoded, and flac ships in the docker image for the md5-verifying check",
            "manual match sticks now: two playlist entries matched to the same server track no longer silently lose the second pairing forever, and reorganize no longer quarantines your own files for being a different master than the metadata source's tracklist",
            "the now-playing modal no longer clips its controls on short/zoomed windows, and unresolvable-path warnings now repeat and name the actual filesystem error — so a dead NFS/bind mount diagnoses itself instead of masquerading as missing files",
        ],
    },
    {
        title: "Earlier in 3.0.2 — the follow-up polish release",
        description: "video downloading gets sonarr-parity round 2, the entire video side goes mobile, and three music-side bugs are dead (library reorganize works again).",
        features: [
            "smarter video searching: daily shows match by air date ('The Daily Show 2026.07.08' style releases), soulseek results parse their share paths the way the music matcher does, releases with no quality token get size-inferred quality, and the wishlist run log now names any source that was skipped (like prowlarr not being configured) instead of silently degrading",
            "download history now tracks tv episodes and youtube grabs (youtube gets its own tab), and you can blacklist an uploader straight from a completed download — searches, retries and requeries all skip them from then on",
            "the entire video side is responsive: dashboard, search, discover, library, watchlist, wishlist, downloads, calendar, detail pages, and both studios (overlay + collection) work on phones and tablets. desktop unchanged",
            "library reorganize actually reorganizes: after a template change it reported every album as 'already organized' — the keep-albums-together folder reuse was answering with the very folder you were moving out of. destinations now come from your current template alone. thanks TheHomeGuy for the report",
            "manual imports skip the quality profile (#1017): a hand-matched file is your call — acoustid, integrity and silence checks still run, but the profile no longer vetoes it",
            "basic search results no longer vanish on short or zoomed-out windows (#1024), and canonical-version repair findings can actually be applied now (#1022, thanks @sam-coodu)",
        ],
    },
    {
        title: "Earlier in 3.0.1 — soulsync does video now",
        description: "the big one: a whole video side (movies, tv, youtube) plus a tautulli-style live server activity view, with Radarr/Sonarr-parity download matching.",
        features: [
            "the video side is a fully isolated app (its own database, dashboard, search, calendar and download pipeline) for plex and jellyfin that never touches the music side. library scanning, tmdb/tvdb/omdb enrichment plus 10 backfill workers, source-agnostic movie/show/person/studio detail pages, and a progressive netflix-feel search",
            "follow shows, actors, directors, studios (with family presets like disney = pixar + marvel + lucasfilm) and youtube channels/playlists, then let the wishlist-to-download pipeline grab them: soulseek + prowlarr + yt-dlp, with radarr/sonarr-class quality profiles, a download history, a recycle bin and a release blocklist",
            "smarter download matching: the search now gates on the release TITLE (not just the year), so 'Paradox (2017)' can't grab 'The Cloverfield Paradox (2018)' anymore — and it matches TMDB alternate/original-language titles too, so an aka-named release still gets found. wrong grabs out, missed grabs out",
            "kometa-style overlay studio (paint template overlays onto your posters) and collection manager (build plex collections / jellyfin boxsets from imdb/tmdb/trakt/mdblist ranked lists in true rank order), both with nightly automations",
            "Server Activity: a tautulli-style live now-playing drawer for plex + jellyfin, with websocket streams, click-to-open-inside-soulsync, a history tab, a stats tab, and terminate-a-stream-with-a-message",
            "the nightly TV refresh only re-pulls the current season now (not every season of every airing show, every night), the help/docs mobile nav works again (thanks @bluejorts), the dashboard header reads 'music dashboard', and there's a github sponsor button",
        ],
    },
    {
        title: "Earlier in 2.8.9",
        description: "a bug-fix + quality-of-life release: box sets keep their disc folders, the Server Playlists compare view stopped taking 15 seconds, and a new matching preference for explicit versions.",
        features: [
            "#1009 — downloading a multi-disc album was collapsing every disc into one folder (and the Track Number Repair job mangled $disc$track filenames like 0213 into 133, flagging correct box sets as broken). both fixed: disc folders follow your template, repairs keep your naming convention, and approving a repair finding applies exactly the change it shows",
            "#1005 — the Server Playlists compare view loads big synced playlists in a few seconds instead of 15+, the missing/matched filter actually filters after a reload, and syncing a single song updates that row in place instead of reloading everything",
            "#923 — new 'prefer explicit versions' sub-setting under the explicit content toggle: explicit-marked soulseek files rank up, clean/censored/radio-edit files rank down, and nothing is ever skipped — a clean version still downloads when it's all that exists",
            "the status endpoint could 500 when several tabs polled it at once (thread race, now locked), and on mobile the floating bell/help buttons no longer sit on top of the album modal's buttons (#1007)",
            "under the hood: unified React page headers, a webui CI gate (lint, build, vitest), and a new e2e route sweep at desktop+mobile that caught the status race — all thanks to @bluejorts (#1008, #1010, #1012)",
        ],
    },
    {
        title: "Earlier in 2.8.8",
        description: "no more corrupted FLACs, Bandcamp, and atomic album publishing.",
        features: [
            "#1000 — a tag write could damage a FLAC's audio on some setups. every tag write now goes to a temp copy, verifies the audio byte-for-byte, and only then swaps the file in. plus the Corrupt File Detector repair job for finding + re-downloading already-damaged files",
            "Bandcamp — a new experimental enrichment source (thanks @shkarlsson), and opt-in atomic album publishing so Plex/Jellyfin never sees a half-loaded album mid-download",
            "downloads unjammed (batch-slot leak + deadlocks), Tidal sessions survive restarts (#1002), compilations stay together under Compilations/ (#1003), and a big UI + mobile polish pass (thanks @bluejorts)",
        ],
    },
    {
        title: "Earlier in 2.8.7",
        description: "the Commissary discovery playlists become first-class Auto-Sync items, plus a credential-wipe fix.",
        features: [
            "the Commissary discovery playlists (Time Machine, Genre, Seasonal, Daily Mix, Popular Picks / Hidden Gems / The Archives / Fresh Tape / Discovery Shuffle) now schedule straight from Auto-Sync — turn one on and it generates itself on the first run and keeps syncing on your interval",
            "#992 — a settings-save could wipe a stored API secret (surfaced as Spotify \"invalid_client\", and could clear Last.fm / Genius / Discogs keys too); a save can no longer blank a saved secret",
            "#993 — mirrored playlists push their cover art to Navidrome on sync; and artist discography hides non-studio releases (live, compilations, singles) by default",
        ],
    },
    {
        title: "Earlier in 2.8.6",
        description: "a focused fix release across search, import, library, and playlists.",
        features: [
            "Spotify search without a connected account — picking \"Spotify\" as your search source now works even if you haven't authenticated, using the no-auth Spotify Free source; and a connected account whose official search returns empty falls back to Free instead of a blank page",
            "#986 — a follow-up to the 2.8.5 black-screen fix: some Docker setups still loaded Import & Stats blank because the JS module bundle was served with a non-JS content type. we force the correct type at the HTTP layer now, so the module scripts always run",
            "#990 — a wrong-shaped playlist refresh could overwrite a mirror with thousands of empty rows and still report success; it accepts the Spotify track shape directly now and validates before deleting, so a malformed payload is rejected and your existing mirror is left intact",
            "#988 — browsing an artist could surface a completely different artist's tracks (e.g. The Outfield showing Beatles) because a Deezer name-search accepted the first result on a poor match; it requires a real name match now",
            "#989 — iTunes singles could file and tag under \"Unknown Artist\" when the album-artist came back empty; they fall back to the real track artist now",
            "#985 — Library Reorganize left the old, now-empty disc/album folders behind after moving files; it prunes them now, safely (never climbing to the artist or library root)",
        ],
    },
    {
        title: "Earlier in 2.8.4",
        description: "2.8.4 was the Artist Web + Quality Profiles release.",
        features: [
            "Artist Web — an interactive WebGL map of your whole library, laid out by how artists relate, in three lenses (Taste Map, Communities, Discovery Web); the Discovery Web grows out to similar artists you don't own, and you can play artist radio / 30s previews right from the graph",
            "Quality Profiles (#974, thanks @nick2000713) — the single global quality setting becomes named, editable profiles (targets, upgrade behavior, AcoustID strictness, downsampling, lossy-copy), with an \"upgrade until target\" cutoff and a per-profile Auto-Import option",
            "the Adventurousness dial went from cosmetic to actually reshaping your recs — deeper the further you push it, genre-diverse, freshly rotated, with \"off your usual path\" chips",
            "fixes: repair stop button actually cancels (#970), playlists no longer stuck \"syncing\" (#972), JioSaavn worker no longer wedges (#964), safer duplicate cleanup; contributor PRs for JioSaavn enrichment (#964, HellRa1SeR) and unicode/Japanese dedup matching (#965/#967, bluejorts)",
        ],
    },
    {
        title: "Earlier in 2.8.3",
        description: "2.8.3 was a full Discover rebuild.",
        features: [
            "a Spotify-level Discover redesign — consistent cards, \"mix\" cards that open into full track-list modals, year/decade mixes, Last.fm Radio + ListenBrainz, a 2-column layout",
            "a real recommendation engine — both rec rows scored on genre affinity + novelty + a dial-driven popularity penalty, \"why this rec\" chips, and self-filling artist-popularity data (Spotify Free → Last.fm → Deezer)",
            "fixes: Lyrics Filler .lrc false-missing (#955), import re-scan caching + match timeout (#957), exact-title matching over remixes (#958/#960); contributor PRs for a shared import matcher (#954) and experimental JioSaavn metadata (#956)",
        ],
    },
    {
        title: "Earlier in 2.8.2",
        description: "2.8.2 was a stability + performance release.",
        features: [
            "Spotify Docker boot hang fixed (#949) — deferred auth probes so a slow Spotify can't block startup; \"re-auth didn't stick\" + Sync to Spotify fixed too",
            "the \"slow after update\" fix (#948) — it was browser password managers, not soulsync; non-credential fields are now marked so they skip them, plus a new Max Performance mode",
            "large-library imports no longer time out (#947) — the staging scan runs in the background with live \"Scanning N of M…\" progress",
        ],
    },
    {
        title: "Earlier in 2.8.1",
        description: "2.8.1 was a features + reliability release.",
        features: [
            "playlist export to Spotify & Deezer (#945) — send a mirrored playlist back to your streaming account, resolving IDs from the discovery cache + your library",
            "Rename-only Library Reorganize (#875), broader lossless + DSD handling (#941/#939), a pile of download/search fixes, and a refined reduce-visual-effects pass",
        ],
    },
    {
        title: "Earlier in 2.8.0",
        description: "2.8.0 was a quality + reliability release.",
        features: [
            "the Unverified review queue stopped inflating and self-heals — the AcoustID scan no longer duplicates rows, a startup reconcile clears the backlog, and a 🧹 Clean orphaned button sweeps dead rows (#934, thanks @nick2000713 for #938)",
            "Preview Clip Cleanup (a Tools job that finds ~30s preview clips and re-wishlists the real version); Album Completeness handles split albums (#936, thanks @ragnarlotus)",
            "dashboard performance + bounded memory growth that could lock up big libraries (#935 / #802)",
        ],
    },
    {
        title: "Earlier in 2.7.9",
        description: "2.7.9 was a big features release.",
        features: [
            "best-quality downloads + a ranked-target quality profile (drag to order every format; pools candidates across every source and grabs the best copy that meets your profile)",
            "quarantine folded into the Downloads page; Discover \"Based On Your Listening\" + a playable \"Your Listening Mix\"; the Wing It Pool; the horizontal-lane Auto-Sync redesign",
            "#927 — multi-disc albums no longer show disc-2 tracks as \"missing\" (the scan now reads the real disc number; re-scan once to backfill)",
        ],
    },
    {
        title: "Earlier in 2.7.8",
        description: "2.7.8 was about playlist order + a couple of reported fixes.",
        features: [
            "Align playlists — reorder the server playlist to match the source (Plex/Navidrome/Jellyfin), with an \"out of order\" badge; order-only, never adds missing tracks",
            "re-add a missed track to the wishlist straight from Recent Syncs → details, with the exact same context the sync used",
            "#922 — import label said \"Deezer\" for Spotify Free users (now reads \"Spotify\"); #918 — iTunes albums over 50 tracks self-heal from a stale 50-track cache",
        ],
    },
    {
        title: "Earlier in 2.7.7 / 2.7.6 / 2.7.5",
        description: "2.7.7 was fix-heavy (downloads tag + path right the first time #915, the listening-recs foundation #913, a big reported-issue sweep). 2.7.6 exported playlists TO listenbrainz (#903) + youtube liked-music sync (#902); 2.7.5 was matching & artwork accuracy plus quality-of-life features.",
        features: [
            "#915 — post-processing + redownload pull the full album from your PRIMARY metadata source, so $year / release date / album type land right the first time",
            "HiFi 30-second previews disguised as full songs are caught and rejected (#895); special-edition cover art, deezer track numbers, the \"The\" duplicate fix",
            "import M3U / M3U8 playlists (#893), ignore-list management (#897), Unraid template fixes (#899), and the rest of the #905–#918 batch",
        ],
    },
    {
        title: "Earlier in 2.7.4 / 2.7.3 / 2.7.2 / 2.7.1 / 2.7.0",
        description: "2.7.4 added re-identify (re-file an imported track under the right release without re-downloading) plus library/import cleanups (#889/#890/#891). 2.7.3 added the Quality Upgrade Finder and the wishlist ignore-list (#874); 2.7.2 brought playlist-folder mirroring + server-playlist M3U export and ReplayGain / Empty-Folder maintenance jobs; 2.7.1 added download verification (acoustid checks every download) + a review queue and closed the websocket login-bypass (#852); 2.7.0 made multi-user real — per-profile streaming accounts, opt-in login, reverse-proxy support.",
        features: [],
    },
];

function _getCurrentVersion() {
    const btn = document.querySelector('.version-button');
    return btn ? btn.textContent.trim().replace('v', '') : '2.4.0';
}

// Compare two semver-ish strings ("2.4.0" vs "2.4.1" vs "2.39"). Returns
// negative if a < b, positive if a > b, 0 if equal. Strips any +sha suffix
// before parsing. Missing components are treated as 0 so "2.4" sorts as
// "2.4.0". Replaces the old parseFloat() approach which collapsed any
// 3-part version to its first two components — making 2.4.0 and 2.4.1
// indistinguishable.
function _compareVersions(a, b) {
    const parse = (s) => String(s || '0').split('+')[0].split('.').map(n => parseInt(n, 10) || 0);
    const pa = parse(a);
    const pb = parse(b);
    const len = Math.max(pa.length, pb.length);
    for (let i = 0; i < len; i++) {
        const diff = (pa[i] || 0) - (pb[i] || 0);
        if (diff !== 0) return diff;
    }
    return 0;
}

function _getLatestWhatsNewVersion() {
    // Only surface entries whose version number is <= the current build. Entries
    // sitting at higher versions are unreleased work-in-progress and shouldn't
    // flag as "new" in the helper badge until the build catches up.
    const buildVer = _getCurrentVersion();
    const versions = Object.keys(WHATS_NEW)
        .filter(v => _compareVersions(v, buildVer) <= 0)
        .sort((a, b) => _compareVersions(b, a));
    return versions[0] || '2.6.1';
}

function openWhatsNew() {
    dismissHelperPopover();
    const latestVersion = _getLatestWhatsNewVersion();
    const notes = WHATS_NEW[latestVersion];

    // Mark as seen
    localStorage.setItem('soulsync_helper_version_seen', latestVersion);
    _updateHelperBadge();

    if (!notes || !notes.length) {
        // Fall back to existing version modal
        exitHelperMode();
        const versionBtn = document.querySelector('.version-button');
        if (versionBtn) versionBtn.click();
        return;
    }

    const panel = document.createElement('div');
    panel.className = 'helper-popover helper-whats-new-panel';
    panel.innerHTML = `
        <div class="helper-popover-header">
            <div class="helper-popover-title">What's New in v${latestVersion}</div>
            <button class="helper-popover-close" onclick="exitHelperMode()">&times;</button>
        </div>
        <div class="helper-whats-new-list">
            ${notes.map(h => {
                if (h.date) return `<div class="helper-whats-new-date">${h.date}</div>`;
                const hasTarget = !!(h.selector || h.page);
                const linkText = h.selector ? 'Show me →' : h.page ? 'Go to page →' : '';
                return `
                <div class="helper-whats-new-item ${hasTarget ? 'clickable' : ''}"
                     ${h.selector ? `data-selector="${h.selector}"` : ''} ${h.page ? `data-page="${h.page}"` : ''}>
                    <div class="helper-whats-new-title">${h.title}</div>
                    <div class="helper-whats-new-desc">${h.desc}</div>
                    ${linkText ? `<span class="helper-whats-new-show">${linkText}</span>` : ''}
                </div>`;
            }).join('')}
        </div>
        <div class="helper-whats-new-footer">
            <button class="helper-tour-btn" onclick="_openFullChangelog()">Full Changelog</button>
            ${Object.keys(WHATS_NEW).length > 1 ? `<button class="helper-tour-btn" onclick="_showOlderNotes()">Older Versions</button>` : ''}
        </div>
    `;

    // "Show me" click handlers
    panel.querySelectorAll('.helper-whats-new-item.clickable').forEach(item => {
        item.addEventListener('click', () => {
            const page = item.getAttribute('data-page');
            const sel = item.getAttribute('data-selector');
            exitHelperMode();
            if (page) navigateToPage(page);
            if (sel) {
                setTimeout(() => {
                    const el = document.querySelector(sel);
                    if (el) {
                        el.scrollIntoView({ behavior: 'smooth', block: 'center' });
                        el.classList.add('helper-highlight');
                        setTimeout(() => el.classList.remove('helper-highlight'), 3000);
                    }
                }, page ? 400 : 50);
            }
        });
    });

    document.body.appendChild(panel);
    _helperPopover = panel;

    const floatBtn = document.getElementById('helper-float-btn');
    if (floatBtn) {
        const btnRect = floatBtn.getBoundingClientRect();
        panel.style.right = (window.innerWidth - btnRect.right) + 'px';
        panel.style.bottom = (window.innerHeight - btnRect.top + 8) + 'px';
        panel.style.left = 'auto';
        panel.style.top = 'auto';
    }
    requestAnimationFrame(() => panel.classList.add('visible'));
}

function _openFullChangelog() {
    exitHelperMode();
    const versionBtn = document.querySelector('.version-button');
    if (versionBtn) versionBtn.click();
}

function _showOlderNotes() {
    // Cycle to next older version in the what's new panel (skip unreleased entries)
    const buildVer = _getCurrentVersion();
    const versions = Object.keys(WHATS_NEW)
        .filter(v => _compareVersions(v, buildVer) <= 0)
        .sort((a, b) => _compareVersions(b, a));
    const panel = _helperPopover;
    if (!panel) return;
    const currentTitle = panel.querySelector('.helper-popover-title');
    const currentVer = currentTitle?.textContent.match(/v([\d.]+)/)?.[1] || versions[0];
    const currentIdx = versions.indexOf(currentVer);
    const nextIdx = (currentIdx + 1) % versions.length;
    const nextVer = versions[nextIdx];

    // Rebuild the list content
    const notes = WHATS_NEW[nextVer];
    if (currentTitle) currentTitle.textContent = `What's New in v${nextVer}`;
    const listEl = panel.querySelector('.helper-whats-new-list');
    if (listEl && notes) {
        listEl.innerHTML = notes.map(h => {
            const hasTarget = !!(h.selector || h.page);
            const linkText = h.selector ? 'Show me →' : h.page ? 'Go to page →' : '';
            return `
            <div class="helper-whats-new-item ${hasTarget ? 'clickable' : ''}"
                 ${h.selector ? `data-selector="${h.selector}"` : ''} ${h.page ? `data-page="${h.page}"` : ''}>
                <div class="helper-whats-new-title">${h.title}</div>
                <div class="helper-whats-new-desc">${h.desc}</div>
                ${linkText ? `<span class="helper-whats-new-show">${linkText}</span>` : ''}
            </div>`;
        }).join('');

        // Rebind click handlers
        listEl.querySelectorAll('.helper-whats-new-item.clickable').forEach(item => {
            item.addEventListener('click', () => {
                const page = item.getAttribute('data-page');
                const sel = item.getAttribute('data-selector');
                exitHelperMode();
                if (page) navigateToPage(page);
                if (sel) {
                    setTimeout(() => {
                        const el = document.querySelector(sel);
                        if (el) {
                            el.scrollIntoView({ behavior: 'smooth', block: 'center' });
                            el.classList.add('helper-highlight');
                            setTimeout(() => el.classList.remove('helper-highlight'), 3000);
                        }
                    }, page ? 400 : 50);
                }
            });
        });
    }
}

function _updateHelperBadge() {
    const floatBtn = document.getElementById('helper-float-btn');
    if (!floatBtn) return;
    const seen = localStorage.getItem('soulsync_helper_version_seen');
    const latest = _getLatestWhatsNewVersion();
    if (seen !== latest) {
        floatBtn.classList.add('has-badge');
    } else {
        floatBtn.classList.remove('has-badge');
    }
}

// ═══════════════════════════════════════════════════════════════════════════
// TROUBLESHOOT MODE (Phase 7)
// ═══════════════════════════════════════════════════════════════════════════

const TROUBLESHOOT_RULES = [
    {
        selector: '#metadata-source-service-card .service-card-indicator.disconnected, #metadata-source-service-card .service-card-indicator.error',
        title: 'Metadata Source Disconnected',
        steps: [
            'Go to Settings → Connections and verify your API credentials',
            'Click "Authenticate" to re-connect to Spotify',
            'If rate limited, wait for the countdown timer to expire',
            'Try switching to iTunes (no authentication required) as a fallback'
        ],
        action: { label: 'Open Settings', fn: () => navigateToPage('settings') }
    },
    {
        selector: '#media-server-service-card .service-card-indicator.disconnected, #media-server-service-card .service-card-indicator.error',
        title: 'Media Server Disconnected',
        steps: [
            'Check that your media server (Plex/Jellyfin/Navidrome) is running',
            'Verify the server URL and API token in Settings → Connections',
            'Ensure the server is accessible from the Commissary host machine',
            'Try clicking "Test Connection" on the service card'
        ],
        action: { label: 'Open Settings', fn: () => navigateToPage('settings') }
    },
    {
        selector: '#soulseek-service-card .service-card-indicator.disconnected, #soulseek-service-card .service-card-indicator.error',
        title: 'Download Source Disconnected',
        steps: [
            'Verify your Soulseek/download client is running and reachable',
            'Check the API URL and credentials in Settings → Downloads',
            'For streaming sources (Tidal, Qobuz), verify your subscription is active',
            'Try restarting the download client application'
        ],
        action: { label: 'Configure Downloads', fn: () => { navigateToPage('settings'); setTimeout(() => typeof switchSettingsTab === 'function' && switchSettingsTab('downloads'), 400); } }
    },
    {
        selector: '.spotify-rate-limit-modal:not(.hidden), .rate-limit-banner',
        title: 'Spotify Rate Limited',
        steps: [
            'Spotify has temporarily blocked API requests due to too many calls',
            'Wait for the countdown timer to expire — requests auto-resume',
            'Avoid running multiple bulk operations (enrichment + search) simultaneously',
            'Consider switching to iTunes temporarily to continue working'
        ]
    },
];

function activateTroubleshootMode() {
    closeTroubleshootMode();
    _troubleshootActive = true;

    // We need to be on the dashboard to scan service cards
    const currentPage = document.querySelector('.page.active')?.id?.replace('-page', '') || '';
    if (currentPage !== 'dashboard') {
        navigateToPage('dashboard');
        setTimeout(() => _runTroubleshootScan(), 400);
    } else {
        _runTroubleshootScan();
    }
}

function _runTroubleshootScan() {
    const issues = [];

    TROUBLESHOOT_RULES.forEach(rule => {
        const selectors = rule.selector.split(',').map(s => s.trim());
        selectors.forEach(sel => {
            try {
                const els = document.querySelectorAll(sel);
                els.forEach(el => {
                    if (el.offsetParent !== null || el.offsetWidth > 0) {
                        issues.push({ el, rule });
                        el.classList.add('helper-troubleshoot-target');
                    }
                });
            } catch (e) { /* invalid selector */ }
        });
    });

    // Deduplicate by rule title
    const seen = new Set();
    const uniqueIssues = issues.filter(i => {
        if (seen.has(i.rule.title)) return false;
        seen.add(i.rule.title);
        return true;
    });

    if (uniqueIssues.length === 0) {
        // All clear!
        const panel = document.createElement('div');
        panel.className = 'helper-popover helper-troubleshoot-panel';
        panel.innerHTML = `
            <div class="helper-popover-header">
                <div class="helper-popover-title">System Health Check</div>
                <button class="helper-popover-close" onclick="exitHelperMode()">&times;</button>
            </div>
            <div class="helper-troubleshoot-clear">
                <div class="helper-troubleshoot-clear-icon">✅</div>
                <div class="helper-troubleshoot-clear-text">All Clear!</div>
                <div class="helper-troubleshoot-clear-desc">All services are connected and running normally. No issues detected.</div>
            </div>
        `;
        document.body.appendChild(panel);
        _helperPopover = panel;
        _positionPanelNearFloatBtn(panel);
        return;
    }

    // Show issues
    const panel = document.createElement('div');
    panel.className = 'helper-popover helper-troubleshoot-panel';
    panel.innerHTML = `
        <div class="helper-popover-header">
            <div class="helper-popover-title">⚠️ ${uniqueIssues.length} Issue${uniqueIssues.length > 1 ? 's' : ''} Found</div>
            <button class="helper-popover-close" onclick="exitHelperMode()">&times;</button>
        </div>
        <div class="helper-troubleshoot-list">
            ${uniqueIssues.map((issue, i) => `
                <div class="helper-troubleshoot-issue">
                    <div class="helper-troubleshoot-issue-title">${issue.rule.title}</div>
                    <div class="helper-troubleshoot-steps">
                        ${issue.rule.steps.map(s => `<div class="helper-troubleshoot-step">• ${s}</div>`).join('')}
                    </div>
                    ${issue.rule.action ? `<button class="helper-action-btn" data-tshoot-idx="${i}">${issue.rule.action.label}</button>` : ''}
                </div>
            `).join('')}
        </div>
    `;

    // Action click handlers
    panel.querySelectorAll('[data-tshoot-idx]').forEach(btn => {
        const idx = parseInt(btn.getAttribute('data-tshoot-idx'));
        btn.addEventListener('click', () => {
            exitHelperMode();
            if (uniqueIssues[idx]?.rule.action?.fn) uniqueIssues[idx].rule.action.fn();
        });
    });

    document.body.appendChild(panel);
    _helperPopover = panel;
    _positionPanelNearFloatBtn(panel);
}

function _positionPanelNearFloatBtn(panel) {
    const floatBtn = document.getElementById('helper-float-btn');
    if (floatBtn) {
        const btnRect = floatBtn.getBoundingClientRect();
        panel.style.right = (window.innerWidth - btnRect.right) + 'px';
        panel.style.bottom = (window.innerHeight - btnRect.top + 8) + 'px';
        panel.style.left = 'auto';
        panel.style.top = 'auto';
    }
    requestAnimationFrame(() => panel.classList.add('visible'));
}

function closeTroubleshootMode() {
    _troubleshootActive = false;
    document.querySelectorAll('.helper-troubleshoot-target').forEach(el => el.classList.remove('helper-troubleshoot-target'));
}

// ═══════════════════════════════════════════════════════════════════════════
// FIRST-LAUNCH & PAGE-LOAD HOOKS
// ═══════════════════════════════════════════════════════════════════════════

document.addEventListener('DOMContentLoaded', () => {
    setTimeout(() => {
        // First-launch welcome prompt
        const hasSetup = localStorage.getItem('soulsync_setup');
        const hasDismissed = localStorage.getItem('soulsync_setup_welcome_dismissed');
        if (!hasSetup && !hasDismissed) {
            const floatBtn = document.getElementById('helper-float-btn');
            if (floatBtn) {
                floatBtn.classList.add('first-launch-pulse');
                const tip = document.createElement('div');
                tip.className = 'helper-first-launch-tip';
                tip.textContent = 'New here? Click for setup help!';
                tip.addEventListener('click', () => {
                    tip.remove();
                    floatBtn.classList.remove('first-launch-pulse');
                    localStorage.setItem('soulsync_setup_welcome_dismissed', '1');
                    activateHelperMode('setup');
                });
                document.body.appendChild(tip);

                // Auto-dismiss after 12 seconds
                setTimeout(() => {
                    if (tip.parentElement) {
                        tip.classList.add('fading');
                        setTimeout(() => tip.remove(), 500);
                        floatBtn.classList.remove('first-launch-pulse');
                    }
                }, 12000);
            }
        }

        // What's New badge
        _updateHelperBadge();

        // Idle glow for undiscovered help button
        if (!localStorage.getItem('soulsync_helper_discovered')) {
            const btn = document.getElementById('helper-float-btn');
            if (btn) btn.classList.add('undiscovered');
        }
    }, 2500);
});
