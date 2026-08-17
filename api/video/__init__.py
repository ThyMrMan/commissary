"""Commissary — VIDEO side API package (isolated).

A SEPARATE Flask blueprint from the music API (api_v1). It reads only
database/video_library.db via VideoDatabase and imports nothing from the music
API or music database layer. Registered in web_server.py with a single additive
line at url_prefix '/api/video', so music routing is untouched.
"""

from __future__ import annotations

import threading

from flask import Blueprint

from utils.logging_config import get_logger

logger = get_logger("video_api")

# Lazily-created, process-wide VideoDatabase handle. VideoDatabase itself guards
# schema init once-per-path, so this just avoids re-opening the wrapper.
_video_db = None
_video_db_lock = threading.Lock()


def get_video_db():
    """Return the shared VideoDatabase instance (created on first use)."""
    global _video_db
    if _video_db is None:
        with _video_db_lock:
            if _video_db is None:
                from database.video_database import VideoDatabase
                _video_db = VideoDatabase()
    return _video_db


def acting_profile_id():
    """The profile making this request, stamped onto rows it creates. None when
    there is no profile context (background/automation callers), which is exactly
    what marks a row as nobody's personal wish."""
    from flask import g
    try:
        return int(getattr(g, "profile_id", None))
    except (TypeError, ValueError):
        return None


def wishlist_owner_filter():
    """Whose wishlist rows this request is allowed to remove.

    None = no restriction (admin, or a profile with can_download): the shared
    wishlist is theirs to manage. Otherwise the profile id it is limited to, so a
    member can take back a title they asked for and nothing else. Lives here
    rather than in each route file because two copies of this rule is how the
    TMDB and YouTube wishlists end up disagreeing about who owns what."""
    from flask import g
    if bool(getattr(g, "is_admin", getattr(g, "profile_id", 1) == 1)):
        return None
    if getattr(g, "can_download", True):
        return None
    pid = acting_profile_id()
    # No profile context and no download rights — own nothing rather than
    # everything. 0 is never a real profile id, so this matches no rows.
    return 0 if pid is None else pid


def create_video_blueprint() -> Blueprint:
    """Build the isolated /api/video blueprint with all video sub-routes."""
    bp = Blueprint("video_api", __name__)

    # Profile permission guards behind the frontend gating. Uses flask.g (set
    # app-wide by web_server's before_request: profile_id — 1==admin — and
    # can_download) so this stays isolated from the music DB. Parity with the
    # music side's @admin_only on Settings-class endpoints:
    #   • Overlay Studio / Import / Collections / Repair (management) → admin
    #   • Settings that EXPOSE or MUTATE tokens, API keys, server / slskd /
    #     download config → admin for BOTH reads and writes (a GET here returns
    #     raw keys — e.g. /enrichment/config, /downloads/slskd)
    #   • Config the Settings page WRITES but content views legitimately READ
    #     (library paths, quality tiers, server presence) → admin for WRITES only
    #   • download-triggering actions → require can_download (mirrors music)
    @bp.before_request
    def _video_perm_gate():
        from flask import request, g, jsonify
        path = request.path or ""
        writing = request.method in ("POST", "PUT", "PATCH", "DELETE")

        def _p(*prefixes):
            return any(path.startswith(x) for x in prefixes)

        # Admin = the profile's REAL is_admin flag (web_server stashes g.is_admin;
        # music supports secondary admins, and the frontend gates on the same
        # flag — a profile-1-only check here split-brained against it). Fallback
        # keeps the old convention when g wasn't populated (tests, edge callers).
        is_admin = bool(getattr(g, "is_admin", getattr(g, "profile_id", 1) == 1))

        # Per-profile side access: a music-only profile gets NOTHING from the
        # video blueprint (its whole UI is hidden for them — any request here is
        # a deep link or a probe). Admins always have both sides.
        if not is_admin and getattr(g, "allowed_sides", "both") == "music":
            return jsonify({"error": "Video access is disabled for this profile."}), 403

        # Management surfaces + credential/settings-only endpoints — admin for ANY
        # method (their GETs leak raw tokens/keys or expose server config, and are
        # only ever hit by the admin-only Settings page).
        admin = _p("/api/video/overlays", "/api/video/import", "/api/video/collections",
                   "/api/video/repair",
                   "/api/video/server-config", "/api/video/jellyfin",
                   "/api/video/organization", "/api/video/downloads/slskd",
                   # the indexer inventory is Settings-class data (and indexer
                   # URLs leaking to the browser was a real bug once)
                   "/api/video/downloads/indexers",
                   # the write probe returns filesystem paths and touches the
                   # disk. /libraries itself is admin only on WRITE (its GET is
                   # the tab bar), so this needs naming here or it would inherit
                   # that GET's openness — and non-admins are deliberately not
                   # shown library paths at all.
                   "/api/video/libraries/probe",
                   "/api/video/enrichment/config", "/api/video/enrichment/priority",
                   "/api/video/notifications",   # P11: GETs return webhook URLs/bot tokens
                   "/api/video/backups")         # P10: restore/download the whole database
        # Config the Settings page WRITES but content views (download modal / grab)
        # legitimately READ — gate the writes, leave the GETs open.
        if writing:
            # /libraries GET is the Library page's tab bar + the download
            # destination picker — content views, not settings. Only the POST
            # (the Settings editor saving the registry) is admin.
            admin = admin or _p("/api/video/libraries",
                                 "/api/video/server", "/api/video/downloads/config",
                                 "/api/video/downloads/quality",
                                 "/api/video/downloads/youtube-quality",
                                 "/api/video/enrichment")   # all enrichment mutations
            # Library / metadata MANAGEMENT — parity with music's @admin_only library edits
            # (delete/sync/clear-match/delete-batch) + the download blocklist config. Content
            # views only READ metadata (the detail GET stays open); these MUTATE the library.
            admin = admin or _p("/api/video/bulk", "/api/video/monitor",
                                 "/api/video/poster/set", "/api/video/downloads/blocklist") \
                or path.endswith(("/metadata", "/lock", "/refresh-art", "/aka", "/library",
                                  "/rescan-episodes", "/episode-source",
                                  # per-title acquisition settings (P2/P8) — management,
                                  # same as the metadata edits above
                                  "/quality-profile", "/series-type",
                                  # per-show Synchronize — mutates library rows
                                  "/sync"))
        if admin and not is_admin:
            return jsonify({"error": "Admin only."}), 403

        # /watchlist/add is deliberately ABSENT here: a profile without download
        # rights may follow a show, but the follow is filed awaiting approval
        # (video_watchlist.approved=0) and no acquisition path acts on it until an
        # admin clears it. The endpoint itself stamps approved from can_download,
        # so relaxing the gate here cannot start a download.
        # /wishlist/add and /youtube/wishlist/add are deliberately ABSENT, same
        # reasoning as /watchlist/add above: asking for something is not
        # acquiring it. A member adds a title to the shared wishlist and the
        # ADMIN's automation (or the admin, by hand) decides whether it is
        # actually fetched. Blocking the add only meant members had no way to
        # ask, which is the opposite of the point.
        #
        # '/wishlist/clear', '/wishlist/remove' and '/youtube/wishlist/remove' are
        # deliberately absent too, for a different reason: all three enforce
        # OWNERSHIP in the route instead. A member can take back the titles they
        # asked for — one at a time or all at once — without being able to touch
        # anyone else's (or automation's). Gating the bulk one while leaving the
        # per-item one open would only have meant more clicks for the same result.
        if writing and not getattr(g, "can_download", True) and _p(
                # '/downloads/grab' also covers '/downloads/grab-pack' by prefix
                "/api/video/downloads/grab", "/api/video/downloads/retry",
                "/api/video/youtube/download",
                # 'Search now' / 'Search all' on the wishlist START REAL GRABS —
                # their own docstrings say so ("the downloads page / badge shows
                # what it grabs"). They were behind no gate at all, so any signed-in
                # profile with video access could download from the wishlist.
                # The prefix covers /wishlist/search-all too.
                "/api/video/wishlist/search",
                # approving IS acquisition — admin-only, checked in the route too
                "/api/video/watchlist/approve",
                # same for the wishlist's queue: releasing a pending wish is what
                # lets it be fetched. '/wishlist/approve' also covers the deny
                # sibling by name below.
                "/api/video/wishlist/approve", "/api/video/wishlist/deny",
                # Destructive siblings of the above. These were unreachable while
                # every non-download profile was music-only; now that a Plex sign-in
                # gets video access by default, an un-gated one would let a member
                # cancel the admin's downloads or empty the shared wishlist.
                "/api/video/downloads/cancel", "/api/video/downloads/history",
                # empties the finished rows off the SHARED queue for everyone —
                # destructive, and it was sitting behind no gate at all
                "/api/video/downloads/clear"):
            return jsonify({"error": "Downloads are disabled for this profile."}), 403

    from .dashboard import register_routes as reg_dashboard
    from .scan import register_routes as reg_scan
    from .library import register_routes as reg_library
    from .libraries import register_routes as reg_libraries
    from .poster import register_routes as reg_poster
    from .enrichment import register_routes as reg_enrichment
    from .detail import register_routes as reg_detail
    from .search import register_routes as reg_search
    from .discover import register_routes as reg_discover
    from .calendar import register_routes as reg_calendar
    from .watchlist import register_routes as reg_watchlist
    from .wishlist import register_routes as reg_wishlist
    from .youtube import register_routes as reg_youtube
    from .downloads import register_routes as reg_downloads
    from .manual_import import register_routes as reg_manual_import
    from .automations import register_routes as reg_automations
    from .overlays import register_routes as reg_overlays
    from .collections import register_routes as reg_collections
    from .bulk import register_routes as reg_bulk
    from .repair import register_routes as reg_repair
    from .issues import register_routes as reg_issues
    from .requests import register_routes as reg_requests
    from .notifications import register_routes as reg_notifications
    from .backups import register_routes as reg_backups
    reg_dashboard(bp)
    reg_scan(bp)
    reg_library(bp)
    reg_libraries(bp)
    reg_poster(bp)
    reg_enrichment(bp)
    reg_detail(bp)
    reg_search(bp)
    reg_discover(bp)
    reg_calendar(bp)
    reg_watchlist(bp)
    reg_wishlist(bp)
    reg_youtube(bp)
    reg_downloads(bp)
    reg_manual_import(bp)
    reg_automations(bp)
    reg_overlays(bp)
    reg_collections(bp)
    reg_bulk(bp)
    reg_repair(bp)
    reg_issues(bp)
    reg_requests(bp)
    reg_notifications(bp)
    reg_backups(bp)

    return bp
