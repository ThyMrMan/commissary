"""SoulSync — VIDEO side database (database/video_library.db).

ISOLATION CONTRACT: this module owns a SEPARATE SQLite file from the music
library and imports NOTHING from the music database layer. Music code never
imports this; this never imports music. A migration bug, corruption, or reset
here cannot touch music data, and the two never contend for the same write lock.

Conventions mirror database/music_database.py on purpose (so the two feel the
same operationally) — WAL journal, foreign keys ON, a 30s busy timeout, Row
factory, a once-per-process init guard, and PRAGMA user_version as a schema
backstop — but the implementations are independent.

The schema itself lives alongside this file in video_schema.sql and is executed
verbatim on first init.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import sqlite3
import threading
from datetime import datetime, timedelta, timezone
from pathlib import Path

from utils.logging_config import get_logger

logger = get_logger("video_database")


def _publish_video_event(event_type: str, data: dict) -> None:
    """Relay a library change to the video automation event bus. The DB layer is
    the one spine every wishlist/watchlist write flows through, so publishing
    here means no caller can forget. Lazy import; a missing forwarder (tests,
    early startup) makes it a no-op — never disturbs the write itself."""
    try:
        from core.video.download_events import publish
        publish(event_type, data)
    except Exception:   # noqa: BLE001
        logger.debug("video event publish failed for %s", event_type, exc_info=True)

# Bump when video_schema.sql changes in a way worth recording. Stored in
# PRAGMA user_version as a backstop indicator (nothing gates on it yet).
SCHEMA_VERSION = 47   # v47: per-title user overrides (AKA titles, series type) keyed by tmdb_id in video_title_overrides, not per library row; v46: media_files format facts (channels/HDR/Atmos badges); v45: per-episode watch state + resume offsets (Continue Watching); v44: video_wishlist.search_attempts/last_search_at

_DEFAULT_DB_PATH = "database/video_library.db"
_SCHEMA_FILE = Path(__file__).resolve().parent / "video_schema.sql"

# Init runs once per database path per process (same guard style as music).
_init_lock = threading.Lock()
_initialized_paths: set[str] = set()

# Sort/letter key: title without a leading article, lowercased (so "The Matrix"
# files under M, like music's library).
_ARTICLE_RE = re.compile(r"^(the|a|an)\s+", re.IGNORECASE)


def _sort_title(title) -> str:
    return _ARTICLE_RE.sub("", (title or "").strip()).lower()


# Enrichment plumbing (parallels music's per-source columns). Maps a service +
# content kind to (table, id_col, match_status_col, last_attempted_col).
_ENRICH = {
    "tmdb": {
        "movie": ("movies", "tmdb_id", "tmdb_match_status", "tmdb_last_attempted"),
        "show": ("shows", "tmdb_id", "tmdb_match_status", "tmdb_last_attempted"),
    },
    "tvdb": {
        "show": ("shows", "tvdb_id", "tvdb_match_status", "tvdb_last_attempted"),
    },
}

# CROSS-REFERENCE ids a matcher may resolve BY when the service's own id is
# missing. A row that carries any of these can be identified EXACTLY (TMDB's
# /find, TVDB's /search/remoteid) instead of guessed at from its title — which
# is what a title search is, and what once filed 'Silo' under an unrelated show
# because it took TMDB's first search result on faith. The service's own id
# column is filtered out at query time (it is already ``known_id``).
_ENRICH_XREF_COLS = {
    "movies": ("tmdb_id", "imdb_id"),
    "shows": ("tvdb_id", "tmdb_id", "imdb_id"),
}

# Whitelist of metadata columns enrichment may write per table (guards against
# arbitrary keys; backfill semantics applied by the caller).
_ENRICH_META_COLS = {
    "movies": {"overview", "backdrop_url", "logo_url", "release_date", "status", "content_rating",
               "runtime_minutes", "studio", "tagline", "rating", "rating_critic",
               "tmdb_collection_id", "tmdb_collection_name",
               "imdb_id", "tmdb_id", "root_folder_id"},
    "shows": {"overview", "backdrop_url", "logo_url", "status", "network", "content_rating",
              "tagline", "rating", "first_air_date", "last_air_date", "airs_time",
              "imdb_id", "tmdb_id", "tvdb_id", "root_folder_id"},
}

# Fields a USER may edit (Manage sidebar) per table. 'genres' is a pseudo-field
# routed to the link tables. An edited field is recorded in the row's
# ``locked_fields`` JSON list and from then on belongs to the user: scans
# (every mode, including FULL) and enrichment leave it alone until released.
_USER_EDITABLE = {
    "movies": {"title", "sort_title", "year", "overview", "tagline", "content_rating",
               "studio", "genres"},
    "shows": {"title", "sort_title", "year", "overview", "tagline", "content_rating",
              "network", "genres"},
}

# Backfill-worker plumbing (parallels _ENRICH, but for services that enrich an
# already-identified library item BY id rather than matching a title). Maps a
# service + kind to (table, status_col, attempted_col, where_has_required_id).
_BACKFILL = {
    "fanart": {
        "movie": ("movies", "fanart_status", "fanart_attempted",
                  "(tmdb_id IS NOT NULL OR imdb_id IS NOT NULL)"),
        "show": ("shows", "fanart_status", "fanart_attempted", "tvdb_id IS NOT NULL"),
    },
    "opensubtitles": {
        "movie": ("movies", "subs_status", "subs_attempted",
                  "(imdb_id IS NOT NULL OR tmdb_id IS NOT NULL)"),
        "show": ("shows", "subs_status", "subs_attempted",
                 "(imdb_id IS NOT NULL OR tmdb_id IS NOT NULL)"),
    },
    "trakt": {
        "movie": ("movies", "trakt_status", "trakt_attempted", "imdb_id IS NOT NULL"),
        "show": ("shows", "trakt_status", "trakt_attempted", "imdb_id IS NOT NULL"),
    },
    "tvmaze": {  # TV-only: TVmaze has no movie database
        "show": ("shows", "tvmaze_status", "tvmaze_attempted",
                 "(imdb_id IS NOT NULL OR tvdb_id IS NOT NULL)"),
    },
    "anilist": {  # anime-only, matched by title (shows only)
        "show": ("shows", "anilist_status", "anilist_attempted",
                 "title IS NOT NULL AND title <> ''"),
    },
    "wikidata": {  # official website lookup by imdb id (movies + shows)
        "movie": ("movies", "wikidata_status", "wikidata_attempted", "imdb_id IS NOT NULL"),
        "show": ("shows", "wikidata_status", "wikidata_attempted", "imdb_id IS NOT NULL"),
    },
    "streaming": {  # TMDB watch providers (movies + shows), by tmdb id
        "movie": ("movies", "streaming_status", "streaming_attempted", "tmdb_id IS NOT NULL"),
        "show": ("shows", "streaming_status", "streaming_attempted", "tmdb_id IS NOT NULL"),
    },
    "mediastinger": {  # TMDB keywords -> after/during-credits scene, by tmdb id
        "movie": ("movies", "mediastinger_status", "mediastinger_attempted", "tmdb_id IS NOT NULL"),
        "show": ("shows", "mediastinger_status", "mediastinger_attempted", "tmdb_id IS NOT NULL"),
    },
    "awards": {  # OMDb Awards string -> oscar/winner flag, by imdb id
        "movie": ("movies", "awards_status", "awards_attempted", "imdb_id IS NOT NULL"),
        "show": ("shows", "awards_status", "awards_attempted", "imdb_id IS NOT NULL"),
    },
}
# Columns each backfill service may gap-fill (whitelist; never clobbers server data).
# A worker visits each item once (status IS NULL), so these NULL columns are written
# on that single pass.
_BACKFILL_COLS = {
    "fanart": {"logo_url", "backdrop_url", "poster_url", "clearart_url", "banner_url"},
    "opensubtitles": {"subtitle_langs"},
    "trakt": {"trakt_rating", "trakt_votes"},
    "tvmaze": {"tvmaze_rating"},
    "anilist": {"anilist_score"},
    "wikidata": {"wikidata_url"},
    "streaming": {"streaming"},
    "mediastinger": {"mediastinger"},
    "awards": {"awards"},
}

# Columns ensured on existing DBs (ALTER TABLE ADD COLUMN; idempotent).
_COLUMN_MIGRATIONS = [
    # Continue Watching (v45): per-episode watch state + resume offsets, scanned
    # from the server (Plex viewCount/viewOffset, Jellyfin UserData) — powers
    # episode checkmarks, progress bars, and the Resume/Next-Up CTA.
    # v46: scanned format facts for the detail-page badges (4K · HDR · Atmos · 5.1)
    ("media_files", "audio_channels", "INTEGER"),
    ("media_files", "dynamic_range", "TEXT"),
    ("media_files", "atmos", "INTEGER"),
    ("episodes", "play_count", "INTEGER"),
    ("episodes", "last_viewed_at", "TEXT"),
    ("episodes", "view_offset_ms", "INTEGER"),
    ("movies", "view_offset_ms", "INTEGER"),
    # video_wishlist — consecutive fruitless drain searches per row, so the UI can
    # mark repeatedly-failing items (#liveleak-failing-hub). Reset on a grab.
    ("video_wishlist", "search_attempts", "INTEGER DEFAULT 0"),
    ("video_wishlist", "last_search_at", "TEXT"),
    # video_downloads — media identity for the Downloads page cards (poster + open).
    ("video_downloads", "media_id", "TEXT"),
    ("video_downloads", "media_source", "TEXT"),
    ("video_downloads", "year", "INTEGER"),
    ("video_downloads", "poster_url", "TEXT"),
    # video_downloads — auto-retry state (remaining candidates + requery context).
    ("video_downloads", "candidates", "TEXT"),
    ("video_downloads", "search_ctx", "TEXT"),
    ("video_downloads", "tried_queries", "TEXT"),
    ("video_downloads", "tried_files", "TEXT"),
    ("video_downloads", "attempts", "INTEGER"),
    # live import sub-phase + progress so the card shows 'Merging…' then 'Moving X%'
    # instead of a blank 100% spinner during post-download processing.
    ("video_downloads", "import_phase", "TEXT"),
    ("video_downloads", "import_progress", "REAL DEFAULT 0"),
    # torrent/usenet client tracking id (qBittorrent hash / SAB nzo_id) so the monitor can
    # poll the shared torrent/usenet client for a non-Soulseek grab's progress + completion.
    ("video_downloads", "client_ref", "TEXT"),
    # the ratingKey of the overlay poster we last uploaded to Plex, so a re-apply can delete
    # THAT one before uploading the new render (Plex accumulates uploads otherwise).
    ("overlay_apply", "plex_poster_key", "TEXT"),
    ("movies", "tmdb_match_status", "TEXT"),
    ("movies", "tmdb_last_attempted", "TEXT"),
    ("shows", "tmdb_match_status", "TEXT"),
    ("shows", "tmdb_last_attempted", "TEXT"),
    # Has this show's TMDB id been CONFIRMED against a second provider, rather
    # than guessed from its title? Existing rows default to 0, which is what
    # makes the one-time re-check sweep find the shows an early title search
    # mis-identified (see VideoEnrichmentWorker._verify_identity_one).
    ("shows", "tmdb_identity_verified", "INTEGER DEFAULT 0"),
    ("shows", "tvdb_match_status", "TEXT"),
    ("shows", "tvdb_last_attempted", "TEXT"),
    # "capture everything" — richer metadata from the server.
    ("movies", "tagline", "TEXT"),
    ("movies", "rating", "REAL"),
    ("movies", "rating_critic", "REAL"),
    ("movies", "tmdb_collection_id", "INTEGER"),
    ("movies", "tmdb_collection_name", "TEXT"),
    ("shows", "tagline", "TEXT"),
    ("shows", "rating", "REAL"),
    ("shows", "first_air_date", "TEXT"),
    ("shows", "last_air_date", "TEXT"),
    ("episodes", "still_url", "TEXT"),
    ("episodes", "rating", "REAL"),
    ("episodes", "added_at", "TEXT"),   # server add-date, so a show ranks "recently added" on a new episode
    ("movies", "logo_url", "TEXT"),
    ("shows", "logo_url", "TEXT"),
    ("shows", "episodes_synced", "INTEGER NOT NULL DEFAULT 0"),
    ("movies", "imdb_rating", "REAL"), ("movies", "rt_rating", "INTEGER"),
    ("movies", "metacritic", "INTEGER"),
    ("shows", "imdb_rating", "REAL"), ("shows", "rt_rating", "INTEGER"),
    ("shows", "metacritic", "INTEGER"),
    ("movies", "ratings_synced", "INTEGER NOT NULL DEFAULT 0"),
    ("shows", "ratings_synced", "INTEGER NOT NULL DEFAULT 0"),
    ("shows", "airs_time", "TEXT"),   # TVDB show air time, e.g. "21:00" (network local)
    ("video_watchlist", "state", "TEXT NOT NULL DEFAULT 'follow'"),  # follow | mute (tombstone)
    ("video_wishlist", "release_date", "TEXT"),   # movie release date — gate: don't search until near release
    # live transfer telemetry (slskd averageSpeed / torrent client dlspeed+eta),
    # refreshed by the monitor each poll while a row is downloading
    ("video_downloads", "speed_bps", "INTEGER"),
    ("video_downloads", "eta_seconds", "INTEGER"),
    # per-title quality profiles (arr-parity P2): NULL/0 = the Default profile;
    # >=1 = a named profile in video_settings['quality_profiles']. The download
    # row carries the id it was GRABBED under so the monitor's cutoff/requery
    # judgments match the grab even if the title is reassigned mid-flight.
    ("movies", "quality_profile_id", "INTEGER"),
    ("shows", "quality_profile_id", "INTEGER"),
    ("video_wishlist", "quality_profile_id", "INTEGER"),
    ("video_downloads", "quality_profile_id", "INTEGER"),
    # seeding lifecycle (P5): 1 = the sweep finished with this torrent (goals
    # met + removed from the client, or the client no longer knows it)
    ("video_downloads", "seed_released", "INTEGER"),
    ("video_watchlist", "lookback_years", "INTEGER"),   # per-person back-catalog window: NULL/0=forward-only, N=years, -1=everything
    # series type (P8, Sonarr parity): standard | daily | anime — drives how the
    # drain QUERIES for episodes (SxxExx vs air date vs absolute number).
    ("shows", "series_type", "TEXT"),
    ("video_wishlist", "still_url", "TEXT"),   # episode still thumbnail (captured at add time)
    ("video_wishlist", "season_poster_url", "TEXT"),   # the episode's season poster
    ("video_wishlist", "episode_overview", "TEXT"),    # episode synopsis
    # rich movie detail captured at add time (backdrop, overview, genres, runtime, rating,
    # top cast, director, release_date, + provenance) so the wishlist renders a full card
    # without re-fetching. JSON blob; NULL on rows added before this / by lean paths.
    ("video_wishlist", "detail_json", "TEXT"),
    # generic source bridge (YouTube channels/videos ride the existing tables)
    ("video_watchlist", "source", "TEXT NOT NULL DEFAULT 'tmdb'"),
    ("video_watchlist", "source_id", "TEXT"),
    ("video_wishlist", "source", "TEXT NOT NULL DEFAULT 'tmdb'"),
    ("video_wishlist", "source_id", "TEXT"),
    ("video_wishlist", "parent_source_id", "TEXT"),   # owning channel youtube id (video rows)
    # Who asked for this. A member without download rights may remove their OWN
    # wishes and nothing else, so the row has to remember who added it. NULL =
    # added before this column existed, or added by automation (the watchlist
    # scan, collections, RSS) — nobody's personal wish, so only a profile with
    # can_download may remove it. Set on INSERT only: re-adding a title someone
    # else already wished must not transfer ownership of their row.
    ("video_wishlist", "added_by_profile_id", "INTEGER"),
    # Wishlist approval gate (see schema.sql), mirroring video_watchlist.
    # approved defaults to 1 so admin wishes, automation rows and every row that
    # predates this column stay live; a wish from a profile without download
    # rights lands 0 and every acquisition path skips it until approved.
    ("video_wishlist", "approved", "INTEGER NOT NULL DEFAULT 1"),
    ("video_wishlist", "requested_by", "INTEGER"),
    ("video_wishlist", "requested_by_name", "TEXT"),
    # which source produced a channel's dates — NULL on legacy (pre-InnerTube) rows
    # so they re-enrich once and upgrade to the full InnerTube catalog.
    ("youtube_channel_enrichment", "method", "TEXT"),
    # per-video duration + approximate view count on the remembered catalog
    ("youtube_channel_videos", "duration", "TEXT"),
    ("youtube_channel_videos", "view_count", "INTEGER"),
    # YouTube retention: owning channel + upload date (group + age episodes) and a prune
    # marker — retention deletes the FILE but keeps the row (so the scan won't re-download it).
    ("video_download_history", "channel_id", "TEXT"),
    ("video_download_history", "published_at", "TEXT"),
    ("video_download_history", "pruned_at", "TEXT"),
    # fanart.tv artwork backfill (gap-fill only; logo/backdrop/poster live already)
    ("movies", "clearart_url", "TEXT"), ("movies", "banner_url", "TEXT"),
    ("movies", "fanart_status", "TEXT"), ("movies", "fanart_attempted", "TEXT"),
    ("shows", "clearart_url", "TEXT"), ("shows", "banner_url", "TEXT"),
    ("shows", "fanart_status", "TEXT"), ("shows", "fanart_attempted", "TEXT"),
    # OpenSubtitles availability backfill (which languages exist for a title)
    ("movies", "subtitle_langs", "TEXT"),            # JSON array of language codes
    ("movies", "subs_status", "TEXT"), ("movies", "subs_attempted", "TEXT"),
    ("shows", "subtitle_langs", "TEXT"),
    ("shows", "subs_status", "TEXT"), ("shows", "subs_attempted", "TEXT"),
    # Trakt community rating backfill (by imdb id) — a distinct audience score + vote count
    ("movies", "trakt_rating", "REAL"), ("movies", "trakt_votes", "INTEGER"),
    ("movies", "trakt_status", "TEXT"), ("movies", "trakt_attempted", "TEXT"),
    ("shows", "trakt_rating", "REAL"), ("shows", "trakt_votes", "INTEGER"),
    ("shows", "trakt_status", "TEXT"), ("shows", "trakt_attempted", "TEXT"),
    # TVmaze community rating backfill (TV only)
    ("shows", "tvmaze_rating", "REAL"),
    ("shows", "tvmaze_status", "TEXT"), ("shows", "tvmaze_attempted", "TEXT"),
    # AniList anime average score backfill (TV only, 0-100)
    ("shows", "anilist_score", "INTEGER"),
    ("shows", "anilist_status", "TEXT"), ("shows", "anilist_attempted", "TEXT"),
    # Wikidata official-website backfill (movies + shows)
    ("movies", "wikidata_url", "TEXT"),
    ("movies", "wikidata_status", "TEXT"), ("movies", "wikidata_attempted", "TEXT"),
    ("shows", "wikidata_url", "TEXT"),
    ("shows", "wikidata_status", "TEXT"), ("shows", "wikidata_attempted", "TEXT"),
    # Streaming provider (TMDB watch providers) for the Streaming overlay badge
    ("movies", "streaming", "TEXT"),
    ("movies", "streaming_status", "TEXT"), ("movies", "streaming_attempted", "TEXT"),
    ("shows", "streaming", "TEXT"),
    ("shows", "streaming_status", "TEXT"), ("shows", "streaming_attempted", "TEXT"),
    # Mediastinger (TMDB keywords → after/during-credits scene) for its overlay badge
    ("movies", "mediastinger", "INTEGER"),
    ("movies", "mediastinger_status", "TEXT"), ("movies", "mediastinger_attempted", "TEXT"),
    ("shows", "mediastinger", "INTEGER"),
    ("shows", "mediastinger_status", "TEXT"), ("shows", "mediastinger_attempted", "TEXT"),
    # Aspect ratio (from the media file / server), for the Aspect overlay badge
    ("media_files", "aspect", "TEXT"),
    # Awards (OMDb Awards string → oscar/winner flag) for the Awards overlay badge
    ("movies", "awards", "TEXT"),
    ("movies", "awards_status", "TEXT"), ("movies", "awards_attempted", "TEXT"),
    ("shows", "awards", "TEXT"),
    ("shows", "awards_status", "TEXT"), ("shows", "awards_attempted", "TEXT"),
    # DeArrow crowd-sourced better titles for cached YouTube videos
    ("youtube_video_stats", "dearrow_title", "TEXT"),
    ("youtube_video_stats", "dearrow_status", "TEXT"),
    # Seasonal collection windows (MM-DD): in-window syncs, out-of-window removes ours
    ("collection_definitions", "window_start", "TEXT"),
    ("collection_definitions", "window_end", "TEXT"),
    # Plex collection mode (hide members in library view, etc.); NULL = leave alone
    ("collection_definitions", "collection_mode", "TEXT"),
    # Watch state from the server (drives 'watched' smart-collection rules)
    ("movies", "play_count", "INTEGER"),
    ("movies", "last_viewed_at", "TEXT"),
    ("shows", "watched_episodes", "INTEGER"),
    # imdb_tmdb_map poster art — the table briefly shipped without these, and
    # CREATE TABLE IF NOT EXISTS never upgrades an existing table's shape.
    ("imdb_tmdb_map", "movie_poster", "TEXT"),
    ("imdb_tmdb_map", "show_poster", "TEXT"),
    # Filtered overlay targeting: apply a scope's template only to items
    # matching a smart-rule definition (JSON, same language as collections)
    ("overlay_assignment", "filter", "TEXT"),
    ("youtube_video_stats", "dearrow_attempted", "TEXT"),
    # TMDB details backfill: the server pre-matches shows/movies (so the matcher
    # skips them) but never supplies details-only fields like `status` (airing vs
    # ended) — which the watchlist's airing-default depends on. This marker drives a
    # one-time per-item detail re-fetch that fills those gaps. Starts 0 = needs it.
    ("shows", "details_synced", "INTEGER NOT NULL DEFAULT 0"),
    ("movies", "details_synced", "INTEGER NOT NULL DEFAULT 0"),
    # User-edited metadata locks (JSON list of field names, e.g. '["title","genres"]').
    # A locked field is owned by the user: scan upserts and enrichment skip it.
    ("movies", "locked_fields", "TEXT"),
    ("shows", "locked_fields", "TEXT"),
    # 'Cleared' download-history rows: hidden from the History modal but KEPT —
    # YouTube completed rows are the ownership ledger (scan dedup, retention,
    # Channels tab); a user clear must never delete the facts.
    ("video_download_history", "cleared_at", "TEXT"),
    # Multi-library (P.4): which configured library (root_folders row) a scanned
    # item belongs to — powers per-library Library-page tabs + grab destination.
    # Defensively re-declared here even though schema.sql's CREATE TABLE already
    # has them: root_folder_id predates this migrations list, so an upgraded DB
    # whose movies/shows tables were created before that column existed would
    # otherwise never gain it (CREATE TABLE IF NOT EXISTS doesn't retrofit columns).
    # NOTE: no REFERENCES clause here — SQLite's ALTER TABLE ADD COLUMN rejects a
    # REFERENCES clause while foreign_keys=ON (every connection here has it on);
    # the FK/cascade only applies via schema.sql's CREATE TABLE for fresh installs.
    ("movies", "root_folder_id", "INTEGER"),
    ("shows", "root_folder_id", "INTEGER"),
    # root_folders doubling as the "Libraries" registry (see schema.sql comment).
    ("root_folders", "server", "TEXT"),
    ("root_folders", "server_title", "TEXT"),
    ("root_folders", "label", "TEXT"),
    ("root_folders", "sort_order", "INTEGER NOT NULL DEFAULT 0"),
    # Per-library torrent-client category/label (e.g. Movies -> "movies", Anime ->
    # "anime") — NULL inherits the global torrent_client.category setting.
    ("root_folders", "category", "TEXT"),
    # Per-library preferred tracker(s) — comma-separated Prowlarr indexer ids
    # (same format as the global prowlarr.indexer_ids allowlist). A hit from
    # one of these indexers ranks higher for titles in this Library; NULL/blank
    # means no preference (every allowed indexer ranks equally, as today).
    ("root_folders", "preferred_indexer_ids", "TEXT"),
    # Which provider owns this show's EPISODE NUMBERING: NULL/'auto' detects it
    # from the season structure, 'tmdb'/'tvdb' pins it. Exists because episodes
    # are keyed by the SERVER's season numbers, and a provider that splits the
    # show differently (TMDB has Bleach in 3 seasons where Plex and TVDB have 17)
    # can only write rows into seasons they don't belong to.
    ("shows", "episode_source", "TEXT"),
    # The Library a wishlist item is destined for, chosen when it was added.
    # NULL falls back to the owned movies/shows row's Library (via library_id),
    # then to the primary — so existing rows keep their current behavior.
    ("video_wishlist", "root_folder_id", "INTEGER"),
    # Watchlist approval gate (see schema.sql). approved defaults to 1 so every
    # follow that predates the column — and every admin follow — stays live; a
    # follow from a profile without download rights is written as 0 and every
    # acquisition path skips it until approved.
    # User-supplied "also known as" titles, newline-separated. A matching aid only:
    # the release-title gate accepts a release named by any of these. Exists because
    # TMDB's alias coverage is patchy for anime — a show whose fansub releases use a
    # translation of the original title has no automatic bridge to its TMDB name.
    # Never pushed to Plex/Jellyfin; this is SoulSync-local.
    ("shows", "aka_titles", "TEXT"),
    ("movies", "aka_titles", "TEXT"),
    ("video_watchlist", "approved", "INTEGER NOT NULL DEFAULT 1"),
    ("video_watchlist", "requested_by", "INTEGER"),
    ("video_watchlist", "requested_by_name", "TEXT"),
    ("video_watchlist", "monitor", "TEXT"),
    # The Library a FOLLOWED show is destined for, chosen when you followed it.
    # The wishlist gained this column already, but a wishlist row is created by
    # the airing automation moments before the grab — far too late to ask the
    # user where it goes. The watchlist is where the decision can actually be
    # made in advance, and it was the only link in the chain with nowhere to
    # record it: a show you do not own yet has no movies/shows row to inherit
    # from, so every first grab of a new show landed in the PRIMARY Library,
    # was scanned back in from there, and had that mistake stamped onto its
    # shows row permanently. NULL keeps today's behaviour exactly.
    ("video_watchlist", "root_folder_id", "INTEGER"),
    # Default series type for shows filed into this Library ('standard' |
    # 'daily' | 'anime'; NULL = no default). series_type drives how a release is
    # SEARCHED for — anime by absolute number, dailies by air date — and it had
    # to be set per show, by hand, on a field most users never find. A Library
    # that exists specifically to hold anime already knows the answer.
    ("root_folders", "default_series_type", "TEXT"),
]


def _norm_indexer_ids(raw) -> str | None:
    """A comma-separated Prowlarr indexer id list → a clean digits-only comma
    string, or None when empty/all-garbage. Same tolerant parsing as the
    global prowlarr.indexer_ids allowlist (core/video/prowlarr_search.py's
    _indexer_ids) — non-digit tokens are just dropped, never an error."""
    ids = [p.strip() for p in str(raw or "").split(",") if p.strip().isdigit()]
    return ",".join(ids) if ids else None


def _proxied_art(url) -> str | None:
    """Route external TMDB art through the app's own image proxy.

    Watchlist rows store whatever art the surface that added them happened to
    have. The detail page proxies (``/api/video/img?u=``); the search/discover
    cards pass TMDB's URL through raw, so those posters are fetched by the
    BROWSER straight from image.tmdb.org — outside the disk cache, and dead for
    any client whose DNS/blocklist eats that host or that has no route to it.
    Same follow, different poster behaviour depending on where you clicked it.

    Applied on write AND on read, so rows stored raw before this heal without
    needing a migration. Anything not TMDB (a local proxy path, a Jellyfin URL)
    is returned untouched."""
    u = str(url or "").strip()
    if u.startswith("https://image.tmdb.org/"):
        from urllib.parse import quote
        return "/api/video/img?u=" + quote(u, safe="")
    return u or None


def _history_library_clause(root) -> tuple:
    """(sql, args) scoping video_download_history to one configured Library.

    History rows predate root_folder_id and were never tied to a Library the
    way movies/shows rows are, so the Library's ``dest_path`` prefix is the
    only join key available. Shared by the paged query and the tab-badge
    counts so a badge can never disagree with the list it labels.
    """
    root_path = str((root or {}).get("path") or "").strip()
    if not root_path:
        return ("0", [])   # unknown/unset Library path → match nothing, not everything
    norm = root_path.replace("\\", "/").rstrip("/") + "/"
    return ("REPLACE(COALESCE(dest_path, ''), '\\', '/') LIKE ? ESCAPE '\\' COLLATE NOCASE",
            [norm.replace("%", "\\%").replace("_", "\\_") + "%"])


def _subtitle_langs_list(raw) -> list:
    """Parse the stored OpenSubtitles ``subtitle_langs`` JSON array into a list of
    language codes for the detail payload. Returns [] for null/garbage so the UI
    can simply hide the row when empty."""
    if not raw:
        return []
    try:
        v = json.loads(raw)
        return [str(x) for x in v if x] if isinstance(v, list) else []
    except (ValueError, TypeError):
        return []


def youtube_surrogate_id(source_id: str) -> int:
    """A stable positive 60-bit int derived from a YouTube id, used as the
    NOT NULL ``tmdb_id`` surrogate for non-tmdb rows so the existing
    UNIQUE(kind, tmdb_id) dedup + group-by machinery keeps working unchanged.
    Collision probability across realistic channel counts is negligible."""
    h = hashlib.sha1((source_id or "").encode("utf-8")).hexdigest()
    return int(h[:15], 16)  # 60 bits — comfortably inside SQLite's signed 64-bit INTEGER


class VideoDatabase:
    """Connection + schema manager for the isolated video library DB."""

    def __init__(self, database_path: str | None = None):
        # Honour the env override (Docker mounts) the same way music does, but
        # under a DISTINCT variable so the two databases never collide.
        if database_path is None or database_path == _DEFAULT_DB_PATH:
            database_path = os.environ.get("VIDEO_DATABASE_PATH", _DEFAULT_DB_PATH)
        self.database_path = Path(database_path)
        self.database_path.parent.mkdir(parents=True, exist_ok=True)
        self._initialize_once()

    # ── connection ──────────────────────────────────────────────────────────
    def _get_connection(self) -> sqlite3.Connection:
        """A fresh connection with the standard pragmas applied."""
        conn = sqlite3.connect(str(self.database_path), timeout=30.0)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        conn.execute("PRAGMA journal_mode = WAL")
        conn.execute("PRAGMA busy_timeout = 30000")  # 30s
        return conn

    def connect(self) -> sqlite3.Connection:
        """Public connection factory — caller owns closing it.

        Prefer using ``with db.connect() as conn:`` so commits/rollbacks and
        close happen automatically.
        """
        return self._get_connection()

    # ── init ────────────────────────────────────────────────────────────────
    def _initialize_once(self) -> None:
        key = str(self.database_path.resolve())
        with _init_lock:
            if key in _initialized_paths:
                return
            self._initialize_database()
            _initialized_paths.add(key)

    def _initialize_database(self) -> None:
        # Staged restore (P10): if the admin picked a backup, swap it in NOW —
        # before any connection opens. The current DB is set aside (kept), so
        # this is reversible; a no-pending call is a no-op.
        try:
            from core.video.backup_restore import apply_pending_restore
            apply_pending_restore(str(self.database_path))
        except Exception:   # noqa: BLE001 - a restore hiccup must never block startup
            logger.exception("staged video restore failed; continuing with the current DB")
        schema = _SCHEMA_FILE.read_text(encoding="utf-8")
        conn = self._get_connection()
        try:
            prev_version = int(conn.execute("PRAGMA user_version").fetchone()[0])
            conn.executescript(schema)
            self._ensure_columns(conn)
            self._ensure_indexes(conn)
            self._seed_named_links_from_scalar(conn)
            self._rescue_orphan_youtube_library_path(conn)
            self._run_data_migrations(conn, prev_version)
            conn.execute(f"PRAGMA user_version = {int(SCHEMA_VERSION)}")
            conn.commit()
            logger.info(
                "Video database ready at %s (schema v%d)",
                self.database_path, SCHEMA_VERSION,
            )
        except Exception:
            conn.rollback()
            logger.exception("Failed to initialize video database at %s", self.database_path)
            raise
        finally:
            conn.close()

    # Partial indexes that reference migration-added columns. They MUST run after
    # _ensure_columns (the schema executescript runs first, before the ALTERs, so
    # these would fail with "no such column" on an upgraded DB if placed there).
    _POST_INDEXES = (
        "CREATE UNIQUE INDEX IF NOT EXISTS idx_video_wishlist_video "
        "ON video_wishlist(source_id) WHERE kind = 'video'",
        "CREATE INDEX IF NOT EXISTS idx_video_wishlist_channel "
        "ON video_wishlist(parent_source_id) WHERE kind = 'video'",
        # Index on movies.tmdb_collection_id — a migration-added column, so it must be
        # created AFTER _ensure_columns (the schema executescript runs before the ALTERs).
        "CREATE INDEX IF NOT EXISTS idx_movies_collection ON movies(tmdb_collection_id)",
        # video_watchlist.approved is migration-added too — the pending-approval
        # queue reads it on every watchlist render and nav-badge poll.
        "CREATE INDEX IF NOT EXISTS idx_video_watchlist_approved "
        "ON video_watchlist(approved) WHERE approved = 0",
        # Same for the wishlist's gate: every acquisition feed filters on
        # approved, and the pending queue + nav badge poll for the 0s.
        "CREATE INDEX IF NOT EXISTS idx_video_wishlist_approved "
        "ON video_wishlist(approved) WHERE approved = 0",
        # Recently-Added ranks shows by their newest episode's add-date — MAX(added_at)
        # per show over a 200k-episode table, so index it.
        "CREATE INDEX IF NOT EXISTS idx_episodes_show_added ON episodes(show_id, added_at)",
        # ── adapted from upstream 3.2.0's perf sweep (21276350) ──────────────
        # movies had a tmdb_id index from day one; shows never did. Every
        # library-id lookup, watchlist-state check and calendar hit was a full
        # shows scan — upstream measured 224ms EACH on a 3.4k-show library, and
        # a discover rail hits it once per row.
        "CREATE INDEX IF NOT EXISTS idx_shows_tmdb ON shows(tmdb_id)",
        # The watchlist/episode roll-ups count owned episodes per show. With
        # only idx_episodes_show, every row of the show is fetched just to test
        # has_file.
        "CREATE INDEX IF NOT EXISTS idx_episodes_show_file ON episodes(show_id, has_file)",
        # Upstream's sweep added a third — episodes(show_id, season_number,
        # episode_number) — for wishlist_owned_media_resolutions. Deliberately
        # NOT taken: our episodes table declares UNIQUE (show_id, season_number,
        # episode_number), and SQLite's implicit index for that constraint
        # already covers exactly those columns in that order. Measured on 120k
        # episodes with the real query, the planner picks sqlite_autoindex_
        # episodes_1 either way and the timing is identical (1.0 ms). Adding it
        # would cost an extra index write per episode row for no read.
    )

    @classmethod
    def _ensure_indexes(cls, conn) -> None:
        """Create indexes that depend on migration-added columns (after columns exist)."""
        for stmt in cls._POST_INDEXES:
            conn.execute(stmt)

    @staticmethod
    def _ensure_columns(conn) -> None:
        """Add any new columns to an existing DB (idempotent ALTER TABLE)."""
        for table, col, coltype in _COLUMN_MIGRATIONS:
            cols = {r[1] for r in conn.execute(f"PRAGMA table_info({table})").fetchall()}
            if col not in cols:
                conn.execute(f"ALTER TABLE {table} ADD COLUMN {col} {coltype}")

    @staticmethod
    def _run_data_migrations(conn, prev_version: int) -> None:
        """One-time data fixes, gated on the PRAGMA user_version the DB carried
        BEFORE this boot (fresh DBs start at 0 with empty tables, so every step
        is a no-op there)."""
        if prev_version < 47:
            # v47: AKAs moved from shows/movies.aka_titles to video_title_overrides,
            # keyed by tmdb_id. The columns could only hold an alias for a title
            # already IN the library — which is precisely not the case when you
            # need one. Carry anything already typed across; the now-unused
            # columns are left in place rather than dropped (SQLite makes that
            # expensive, and a stale column is harmless).
            try:
                for kind, table in (("movie", "movies"), ("show", "shows")):
                    cols = {r[1] for r in conn.execute("PRAGMA table_info(%s)" % table)}
                    if "aka_titles" not in cols:
                        continue
                    for row in conn.execute(
                            "SELECT tmdb_id, aka_titles FROM %s "
                            "WHERE aka_titles IS NOT NULL AND TRIM(aka_titles) <> '' "
                            "AND tmdb_id IS NOT NULL" % table).fetchall():
                        conn.execute(
                            "INSERT INTO video_title_overrides (kind, tmdb_id, aka_titles) VALUES (?,?,?) "
                            "ON CONFLICT(kind, tmdb_id) DO NOTHING",
                            (kind, row["tmdb_id"], row["aka_titles"]))
            except sqlite3.Error:
                logger.exception("v47 AKA migration failed; aliases may need re-entering")

        if prev_version < 41:
            # v41 heal: the details backfill used to swallow failed TMDB calls
            # (429/5xx/timeout) into empty metadata and still mark
            # details_synced=1 — permanently, since the queue only picks
            # details_synced=0. Those burn victims sit matched-but-status-less
            # forever: no watchlist button (unknown status reads as ended), no
            # airing refresh, no calendar. The call-failure propagation is fixed
            # (clients.py raises now); this re-queues the already-burned rows for
            # one fresh attempt. Genuine TMDB 404s just settle back to synced.
            for tbl in ("shows", "movies"):
                conn.execute(
                    f"UPDATE {tbl} SET details_synced=0 "
                    f"WHERE tmdb_id IS NOT NULL AND details_synced=1 "
                    f"AND (status IS NULL OR TRIM(status) = '')")

    # ── enrichment plumbing (per-source match status, like music) ─────────────
    def enrichment_next(self, service: str, retry_days: int = 30, priority=None) -> dict | None:
        """Next item that needs enrichment for a service: pending (never tried)
        first, then a not_found item older than retry_days. Returns
        {kind, id, title, year, known_id, external_ids} or None. ``known_id`` is
        the provider id the media server already supplied (e.g. tmdb_id/tvdb_id)
        so the worker can enrich BY ID instead of re-searching by title.

        ``external_ids`` are the OTHER providers' ids already on the row. They
        matter when ``known_id`` is None: a show the server identified by TVDB
        guid but not by TMDB can still be resolved EXACTLY (TMDB's /find), which
        is the difference between knowing what a title is and guessing from its
        name. See _ENRICH_XREF_COLS.

        ``priority`` ('movie'/'show') pins a kind to be processed first across the
        queue; a compound 'movie:<root_folder_id>'/'show:<root_folder_id>' also
        prefers one configured Library WITHIN that kind before the rest of it (e.g.
        an Anime library's shows ahead of the rest of the show queue) — drives the
        modal's 'Process first everywhere' control."""
        kinds = _ENRICH.get(service)
        if not kinds:
            return None
        items = list(kinds.items())
        priority_kind, _, priority_root = str(priority or "").partition(":")
        priority_root = int(priority_root) if priority_root.isdigit() else None
        if priority_kind in kinds:
            items.sort(key=lambda kv: 0 if kv[0] == priority_kind else 1)
        cutoff = (datetime.now(timezone.utc) - timedelta(days=retry_days)).strftime("%Y-%m-%d %H:%M:%S")
        conn = self._get_connection()

        def _xrefs(tbl, idc):
            """The row's OTHER provider id columns — never the service's own."""
            return [c for c in _ENRICH_XREF_COLS.get(tbl, ()) if c != idc]

        def _cols(tbl, idc):
            return ", ".join(["id", "title", "year", idc] + _xrefs(tbl, idc))

        def _row(row, kind, tbl, idc):
            return {"kind": kind, "id": row["id"], "title": row["title"],
                    "year": row["year"], "known_id": row[idc],
                    "external_ids": {c: row[c] for c in _xrefs(tbl, idc) if row[c]}}

        try:
            for kind, (tbl, idc, sc, _ac) in items:
                if priority_root and kind == priority_kind:
                    row = conn.execute(
                        f"SELECT {_cols(tbl, idc)} FROM {tbl} "
                        f"WHERE {sc} IS NULL AND root_folder_id=? ORDER BY id LIMIT 1",
                        (priority_root,)).fetchone()
                    if row:
                        return _row(row, kind, tbl, idc)
                row = conn.execute(
                    f"SELECT {_cols(tbl, idc)} FROM {tbl} "
                    f"WHERE {sc} IS NULL ORDER BY id LIMIT 1").fetchone()
                if row:
                    return _row(row, kind, tbl, idc)
            for kind, (tbl, idc, sc, ac) in items:
                if priority_root and kind == priority_kind:
                    row = conn.execute(
                        f"SELECT {_cols(tbl, idc)} FROM {tbl} "
                        f"WHERE {sc} IN ('not_found','error') AND root_folder_id=? "
                        f"AND ({ac} IS NULL OR {ac} < ?) ORDER BY {ac} LIMIT 1",
                        (priority_root, cutoff)).fetchone()
                    if row:
                        return _row(row, kind, tbl, idc)
                row = conn.execute(
                    f"SELECT {_cols(tbl, idc)} FROM {tbl} "
                    f"WHERE {sc} IN ('not_found','error') "
                    f"AND ({ac} IS NULL OR {ac} < ?) ORDER BY {ac} LIMIT 1", (cutoff,)).fetchone()
                if row:
                    return _row(row, kind, tbl, idc)
            return None
        finally:
            conn.close()

    def enrichment_apply(self, service: str, kind: str, item_id: int, matched: bool,
                         external_id=None, metadata: dict | None = None,
                         error: bool = False) -> None:
        """Record a match result: set match_status + last_attempted, the external
        id (when matched), and any whitelisted metadata columns.

        Status is one of 'matched' / 'not_found' / 'error'. 'error' means the
        lookup CALL failed (network/rate-limit/timeout) — distinct from a genuine
        'not_found' so a transient blip isn't permanently recorded as "no match"
        (mirrors the music workers). Both 'not_found' and 'error' are retried by
        enrichment_next after retry_days."""
        spec = _ENRICH.get(service, {}).get(kind)
        if not spec:
            return
        tbl, idc, sc, ac = spec
        allowed = _ENRICH_META_COLS.get(tbl, set())
        status = "matched" if matched else "error" if error else "not_found"
        # On legacy DBs tmdb_id/tvdb_id may still carry a UNIQUE index; if a match
        # would collide with another row's id we drop the id columns and keep the
        # existing (authoritative) id, still recording status + metadata.
        id_cols = {"tmdb_id", "tvdb_id", "imdb_id"}

        def build(include_ids, locked):
            sets = [f"{sc}=?", f"{ac}=CURRENT_TIMESTAMP"]
            params = [status]
            if matched and external_id is not None and include_ids:
                sets.append(f"{idc}=?")
                params.append(external_id)
            for col, val in (metadata or {}).items():
                if val is None or col not in allowed or col in locked:
                    continue
                if not include_ids and col in id_cols:
                    continue
                # BACKFILL: only fill a column the server left empty — enrichment
                # fills gaps, it never clobbers data the media server provided.
                sets.append(f"{col}=COALESCE(NULLIF({col}, ''), ?)")
                params.append(val)
            params.append(item_id)
            return f"UPDATE {tbl} SET {', '.join(sets)} WHERE id=?", params

        conn = self._get_connection()
        try:
            # User-locked fields belong to the user — enrichment never touches
            # them, not even to fill a deliberately-blanked value.
            locked = self._locked_fields_set(conn, tbl, item_id)
            sql, params = build(True, locked)
            try:
                conn.execute(sql, params)
            except sqlite3.IntegrityError:
                conn.rollback()
                sql, params = build(False, locked)   # keep existing id, just record status/metadata
                conn.execute(sql, params)
            # Genres backfill — only when the item has none yet (enrichment fills
            # the gap the server didn't). Written to the normalised link tables.
            genres = (metadata or {}).get("genres")
            link = {"movies": ("movie_genres", "movie_id"),
                    "shows": ("show_genres", "show_id")}.get(tbl)
            if matched and genres and link and "genres" not in locked:
                lt, oc = link
                has = conn.execute(f"SELECT 1 FROM {lt} WHERE {oc}=? LIMIT 1", (item_id,)).fetchone()
                if not has:
                    self._set_genres(conn, lt, oc, item_id, genres)
            # Studios / networks — TMDB is the authority for the FULL company list (the media
            # server only exposes one), so REPLACE the link table on every match rather than
            # gap-fill. This is what makes studio/network collections complete (a title made by
            # several companies lands in every one). The scalar studio/network column is left as
            # the server/first value for display.
            if matched:
                studios = (metadata or {}).get("studios")
                if tbl == "movies" and studios and "studio" not in locked:
                    self._set_named_links(conn, "movie_studios", "movie_id", "studios", "studio_id",
                                          item_id, studios)
                networks = (metadata or {}).get("networks")
                if tbl == "shows" and networks and "network" not in locked:
                    self._set_named_links(conn, "show_networks", "show_id", "networks", "network_id",
                                          item_id, networks)
            # Cast/crew backfill — only when the item has none yet (gap-fill).
            cast = (metadata or {}).get("cast")
            crew = (metadata or {}).get("crew")
            if matched and (cast or crew) and tbl in ("movies", "shows"):
                oc = "movie_id" if tbl == "movies" else "show_id"
                has = conn.execute(f"SELECT 1 FROM credits WHERE {oc}=? LIMIT 1", (item_id,)).fetchone()
                if not has:
                    self._set_credits(conn, oc, item_id, cast or [], crew or [])
            # Per-season poster backfill (TMDB) — fills only seasons the server
            # left without art.
            seasons_meta = (metadata or {}).get("seasons")
            if matched and seasons_meta and tbl == "shows":
                for s in seasons_meta:
                    sn, purl = s.get("season_number"), s.get("poster_url")
                    if sn is None or not purl:
                        continue
                    conn.execute(
                        "UPDATE seasons SET poster_url=COALESCE(NULLIF(poster_url, ''), ?) "
                        "WHERE show_id=? AND season_number=?", (purl, item_id, sn))
            conn.commit()
        finally:
            conn.close()

    def enrichment_breakdown(self, service: str) -> dict:
        if service == "omdb":
            return self._ratings_breakdown()
        if service == "ryd":
            return self.youtube_enrich_breakdown("ryd_status")
        if service == "sponsorblock":
            return self.youtube_enrich_breakdown("sb_status")
        if service in _BACKFILL:
            return self.backfill_breakdown(service)
        kinds = _ENRICH.get(service, {})
        out = {}
        conn = self._get_connection()
        try:
            for kind, (tbl, _idc, sc, _ac) in kinds.items():
                out[kind] = {
                    "matched": conn.execute(f"SELECT COUNT(*) FROM {tbl} WHERE {sc}='matched'").fetchone()[0],
                    "not_found": conn.execute(f"SELECT COUNT(*) FROM {tbl} WHERE {sc}='not_found'").fetchone()[0],
                    "errors": conn.execute(f"SELECT COUNT(*) FROM {tbl} WHERE {sc}='error'").fetchone()[0],
                    "pending": conn.execute(f"SELECT COUNT(*) FROM {tbl} WHERE {sc} IS NULL").fetchone()[0],
                }
            # TMDB also cascades episode art (still) backfill from the show worker,
            # so the manager sees episode coverage. Not a queue (matched = has a
            # still; the rest are "pending" art) — kept out of the idle/pending
            # calc by the worker so it never blocks "Complete".
            if service == "tmdb":
                total = conn.execute("SELECT COUNT(*) FROM episodes").fetchone()[0]
                with_still = conn.execute(
                    "SELECT COUNT(*) FROM episodes WHERE still_url IS NOT NULL AND still_url<>''").fetchone()[0]
                out["episode"] = {"matched": with_still, "not_found": 0, "errors": 0,
                                  "pending": total - with_still, "coverage_only": True}
            return out
        finally:
            conn.close()

    # ── backfill-worker plumbing (artwork / subtitles, by id) ─────────────────
    def backfill_next(self, service: str) -> dict | None:
        """Next library item needing a backfill service: a row that already has the
        id the service needs and no status yet. Returns
        {kind, id, title, tmdb_id, imdb_id[, tvdb_id]} or None."""
        kinds = _BACKFILL.get(service)
        if not kinds:
            return None
        conn = self._get_connection()
        try:
            for kind, (tbl, sc, _ac, has_id) in kinds.items():
                cols = "id, title, tmdb_id, imdb_id" + (", tvdb_id" if tbl == "shows" else "")
                row = conn.execute(
                    f"SELECT {cols} FROM {tbl} WHERE {sc} IS NULL AND {has_id} "
                    f"ORDER BY id LIMIT 1").fetchone()
                if row:
                    d = dict(row)
                    d["kind"] = kind
                    return d
            return None
        finally:
            conn.close()

    def backfill_mark(self, service: str, kind: str, item_id: int, status: str,
                      columns: dict | None = None) -> None:
        """Record a backfill result (status + attempted) and gap-fill whitelisted
        columns (COALESCE — never clobbers). status: 'ok'|'not_found'|'error'."""
        spec = _BACKFILL.get(service, {}).get(kind)
        if not spec:
            return
        tbl, sc, ac, _has = spec
        allowed = _BACKFILL_COLS.get(service, set())
        sets = [f"{sc}=?", f"{ac}=CURRENT_TIMESTAMP"]
        params: list = [status]
        for col, val in (columns or {}).items():
            if val is None or col not in allowed:
                continue
            sets.append(f"{col}=COALESCE(NULLIF({col}, ''), ?)")
            params.append(val)
        params.append(item_id)
        conn = self._get_connection()
        try:
            conn.execute(f"UPDATE {tbl} SET {', '.join(sets)} WHERE id=?", params)
            conn.commit()
        finally:
            conn.close()

    def backfill_breakdown(self, service: str) -> dict:
        kinds = _BACKFILL.get(service, {})
        out = {}
        conn = self._get_connection()
        try:
            for kind, (tbl, sc, _ac, has_id) in kinds.items():
                base = f"FROM {tbl} WHERE {has_id}"
                out[kind] = {
                    "matched": conn.execute(f"SELECT COUNT(*) {base} AND {sc}='ok'").fetchone()[0],
                    "not_found": conn.execute(f"SELECT COUNT(*) {base} AND {sc}='not_found'").fetchone()[0],
                    "errors": conn.execute(f"SELECT COUNT(*) {base} AND {sc}='error'").fetchone()[0],
                    "pending": conn.execute(f"SELECT COUNT(*) {base} AND {sc} IS NULL").fetchone()[0],
                }
            return out
        finally:
            conn.close()

    # ── per-video YouTube enrichment (no-key: RYD votes + SponsorBlock) ────────
    def youtube_enrich_next(self, status_col: str) -> dict | None:
        """Next cached YouTube video missing a per-video enrichment. status_col is
        'ryd_status' or 'sb_status'. Distinct by youtube_id (a video shared across
        playlists is enriched once). Returns {kind:'video', id, name, youtube_id}."""
        if status_col not in ("ryd_status", "sb_status", "dearrow_status"):
            return None
        conn = self._get_connection()
        try:
            row = conn.execute(
                "SELECT cv.youtube_id AS youtube_id, MIN(cv.title) AS title "
                "FROM youtube_channel_videos cv "
                "LEFT JOIN youtube_video_stats s ON s.youtube_id = cv.youtube_id "
                f"WHERE s.youtube_id IS NULL OR s.{status_col} IS NULL "
                "GROUP BY cv.youtube_id LIMIT 1").fetchone()
            if not row:
                return None
            # "title" is what the worker base class logs/displays (movie/show
            # feeds use it) — the old name-only shape made every YouTube
            # enrichment log as "Enriched video 'None'".
            return {"kind": "video", "id": row["youtube_id"], "title": row["title"],
                    "name": row["title"], "youtube_id": row["youtube_id"]}
        finally:
            conn.close()

    def apply_youtube_votes(self, youtube_id, like_count, dislike_count, status: str) -> None:
        yid = str(youtube_id or "").strip()
        if not yid:
            return
        conn = self._get_connection()
        try:
            conn.execute(
                "INSERT INTO youtube_video_stats "
                "(youtube_id, like_count, dislike_count, ryd_status, ryd_attempted) "
                "VALUES (?,?,?,?,CURRENT_TIMESTAMP) ON CONFLICT(youtube_id) DO UPDATE SET "
                "like_count=COALESCE(excluded.like_count, like_count), "
                "dislike_count=COALESCE(excluded.dislike_count, dislike_count), "
                "ryd_status=excluded.ryd_status, ryd_attempted=CURRENT_TIMESTAMP",
                (yid, like_count, dislike_count, status))
            conn.commit()
        finally:
            conn.close()

    def apply_youtube_segments(self, youtube_id, segments, status: str) -> None:
        yid = str(youtube_id or "").strip()
        if not yid:
            return
        conn = self._get_connection()
        try:
            conn.execute(
                "INSERT INTO youtube_video_stats (youtube_id, sb_status, sb_attempted) "
                "VALUES (?,?,CURRENT_TIMESTAMP) ON CONFLICT(youtube_id) DO UPDATE SET "
                "sb_status=excluded.sb_status, sb_attempted=CURRENT_TIMESTAMP", (yid, status))
            if segments:
                conn.execute("DELETE FROM youtube_video_segments WHERE youtube_id=?", (yid,))
                rows = [(yid, s.get("category"), s.get("start_sec"), s.get("end_sec"),
                         s.get("votes"), s.get("uuid"))
                        for s in segments if s.get("uuid") and s.get("category")]
                if rows:
                    conn.executemany(
                        "INSERT OR IGNORE INTO youtube_video_segments "
                        "(youtube_id, category, start_sec, end_sec, votes, uuid) "
                        "VALUES (?,?,?,?,?,?)", rows)
            conn.commit()
        finally:
            conn.close()

    def apply_youtube_dearrow(self, youtube_id, title, status: str) -> None:
        """Record DeArrow's crowd-sourced better title (+ status) for a video."""
        yid = str(youtube_id or "").strip()
        if not yid:
            return
        conn = self._get_connection()
        try:
            conn.execute(
                "INSERT INTO youtube_video_stats (youtube_id, dearrow_title, dearrow_status, dearrow_attempted) "
                "VALUES (?,?,?,CURRENT_TIMESTAMP) ON CONFLICT(youtube_id) DO UPDATE SET "
                "dearrow_title=COALESCE(excluded.dearrow_title, dearrow_title), "
                "dearrow_status=excluded.dearrow_status, dearrow_attempted=CURRENT_TIMESTAMP",
                (yid, title, status))
            conn.commit()
        finally:
            conn.close()

    def youtube_video_dearrow_title(self, youtube_id) -> str | None:
        """The DeArrow crowd title for a video, if one was recorded (detail UI)."""
        yid = str(youtube_id or "").strip()
        if not yid:
            return None
        conn = self._get_connection()
        try:
            row = conn.execute(
                "SELECT dearrow_title FROM youtube_video_stats WHERE youtube_id=?", (yid,)).fetchone()
            return row["dearrow_title"] if row and row["dearrow_title"] else None
        finally:
            conn.close()

    def youtube_enrich_breakdown(self, status_col: str) -> dict:
        if status_col not in ("ryd_status", "sb_status", "dearrow_status"):
            return {}
        conn = self._get_connection()
        try:
            total = conn.execute(
                "SELECT COUNT(DISTINCT youtube_id) FROM youtube_channel_videos").fetchone()[0]

            def c(st):
                return conn.execute(
                    f"SELECT COUNT(*) FROM youtube_video_stats WHERE {status_col}=?", (st,)).fetchone()[0]

            matched, nf, err = c("ok"), c("not_found"), c("error")
            return {"video": {"matched": matched, "not_found": nf, "errors": err,
                              "pending": max(0, total - matched - nf - err)}}
        finally:
            conn.close()

    def youtube_video_segments(self, youtube_id) -> list:
        """SponsorBlock segments for a video (detail UI / skip logic)."""
        yid = str(youtube_id or "").strip()
        if not yid:
            return []
        conn = self._get_connection()
        try:
            rows = conn.execute(
                "SELECT category, start_sec, end_sec, votes FROM youtube_video_segments "
                "WHERE youtube_id=? ORDER BY start_sec", (yid,)).fetchall()
            return [dict(r) for r in rows]
        finally:
            conn.close()

    def show_match_info(self, show_id: int) -> dict | None:
        """Title/year/tmdb_id/tvdb_id for one show — for on-demand (lazy) art refresh
        (tvdb_id lets the refresh gap-fill episode metadata TMDB is missing).
        ``episode_source`` is the per-show numbering override (see
        core/video/episode_numbering); NULL means detect it."""
        conn = self._get_connection()
        try:
            row = conn.execute("SELECT title, year, tmdb_id, tvdb_id, episode_source "
                               "FROM shows WHERE id=?", (show_id,)).fetchone()
            return dict(row) if row else None
        finally:
            conn.close()

    def server_season_numbers(self, show_id: int) -> list:
        """Season numbers the MEDIA SERVER actually reports for this show — the
        structure a provider has to match to be usable for episode numbering.

        Deliberately not ``show_season_numbers``: that returns every season row,
        including ones a mis-numbered backfill invented, so comparing a provider
        against it would score the provider against its own mistakes."""
        conn = self._get_connection()
        try:
            return [r["season_number"] for r in conn.execute(
                "SELECT DISTINCT season_number FROM episodes "
                "WHERE show_id=? AND server_id IS NOT NULL ORDER BY season_number",
                (show_id,)).fetchall()]
        except sqlite3.Error:
            logger.exception("server_season_numbers failed for show %s", show_id)
            return []
        finally:
            conn.close()

    def set_show_episode_source(self, show_id: int, source) -> bool:
        """Pin which provider supplies this show's episode list, or None/'auto'
        to go back to detecting it."""
        from core.video.episode_numbering import SOURCES
        val = str(source or "auto").strip().lower()
        if val not in SOURCES:
            return False
        conn = self._get_connection()
        try:
            cur = conn.execute("UPDATE shows SET episode_source=? WHERE id=?",
                               (None if val == "auto" else val, show_id))
            conn.commit()
            return cur.rowcount > 0
        except sqlite3.Error:
            logger.exception("set_show_episode_source failed for show %s", show_id)
            return False
        finally:
            conn.close()

    def movie_match_info(self, movie_id: int) -> dict | None:
        """Title/year/tmdb_id/imdb_id for one movie — for on-demand (lazy) refresh.
        ``imdb_id`` lets an unmatched movie be resolved EXACTLY (TMDB's /find)
        instead of by a title search that can only guess."""
        conn = self._get_connection()
        try:
            row = conn.execute("SELECT title, year, tmdb_id, imdb_id FROM movies WHERE id=?",
                               (movie_id,)).fetchone()
            return dict(row) if row else None
        finally:
            conn.close()

    def library_id_for_tmdb(self, kind: str, tmdb_id, server_source=None) -> int | None:
        """The library row id for a TMDB id if it's owned on the active video
        server (``server_source``), else None. Lets the search → detail flow link
        owned titles to their real library detail — scoped per server so an item
        owned only on the inactive server doesn't read as owned here."""
        table = {"movie": "movies", "show": "shows"}.get(kind)
        if not table or tmdb_id is None:
            return None
        try:
            tmdb_id = int(tmdb_id)
        except (TypeError, ValueError):
            return None
        conn = self._get_connection()
        try:
            if server_source:
                row = conn.execute(
                    f"SELECT id FROM {table} WHERE tmdb_id=? AND server_source=? LIMIT 1",
                    (tmdb_id, server_source)).fetchone()
            else:
                row = conn.execute(
                    f"SELECT id FROM {table} WHERE tmdb_id=? LIMIT 1", (tmdb_id,)).fetchone()
            return row["id"] if row else None
        except sqlite3.Error:
            return None
        finally:
            conn.close()

    def root_folder_id_for_tmdb(self, kind: str, tmdb_id, server_source=None) -> int | None:
        """The Library (root_folders.id) a TMDB title is already filed under,
        if it's in the library at all — for callers that only have a bare
        tmdb_id (no wishlist row to read library_id from), e.g. a repair-job
        upgrade grab for an already-owned movie. None when the title isn't
        owned, or is owned but not yet assigned to a Library."""
        table = {"movie": "movies", "show": "shows"}.get(kind)
        if not table or tmdb_id is None:
            return None
        try:
            tmdb_id = int(tmdb_id)
        except (TypeError, ValueError):
            return None
        conn = self._get_connection()
        try:
            if server_source:
                row = conn.execute(
                    f"SELECT root_folder_id FROM {table} WHERE tmdb_id=? AND server_source=? LIMIT 1",
                    (tmdb_id, server_source)).fetchone()
            else:
                row = conn.execute(
                    f"SELECT root_folder_id FROM {table} WHERE tmdb_id=? LIMIT 1", (tmdb_id,)).fetchone()
            return row["root_folder_id"] if row else None
        except sqlite3.Error:
            return None
        finally:
            conn.close()

    def root_folder_id_for_library_row(self, kind: str, row_id) -> int | None:
        """The Library (root_folders.id) a movies/shows ROW is filed under.

        The sibling of ``root_folder_id_for_tmdb`` for callers holding a library
        row id rather than a tmdb_id — the two ids a grab payload can carry
        (``media_source`` says which). None when the row is unknown or not yet
        assigned to a Library."""
        table = {"movie": "movies", "show": "shows"}.get(kind)
        if not table or row_id is None:
            return None
        try:
            row_id = int(row_id)
        except (TypeError, ValueError):
            return None
        conn = self._get_connection()
        try:
            row = conn.execute(
                f"SELECT root_folder_id FROM {table} WHERE id=? LIMIT 1", (row_id,)).fetchone()
            return row["root_folder_id"] if row else None
        except sqlite3.Error:
            return None
        finally:
            conn.close()

    @staticmethod
    def _split_akas(raw) -> list:
        """Stored newline-separated AKAs → a clean deduped list (case-insensitive,
        order preserved). Also tolerates commas, since that's what people type."""
        out, seen = [], set()
        for part in str(raw or "").replace(",", "\n").split("\n"):
            t = part.strip()
            if t and t.lower() not in seen:
                seen.add(t.lower())
                out.append(t)
        return out

    def set_aka_titles(self, kind: str, tmdb_id, titles) -> list | None:
        """Replace a title's user AKA list, keyed by TMDB id. ``titles`` may be a
        list or a raw newline/comma string. Returns the stored list, or None when
        the kind/tmdb id is unusable.

        Keyed on the tmdb id rather than a library row deliberately: the releases
        these fix are for titles you do NOT own yet — you're searching to grab the
        first episode — and no row exists then. It also means an alias survives
        the row being deleted and rescanned, and is shared by every copy of a
        title mirrored across servers."""
        if kind not in ("movie", "show") or tmdb_id is None:
            return None
        if isinstance(titles, (list, tuple)):
            titles = "\n".join(str(t or "") for t in titles)
        clean = self._split_akas(titles)
        try:
            tid = int(tmdb_id)
        except (TypeError, ValueError):
            return None
        conn = self._get_connection()
        try:
            if clean:
                conn.execute(
                    "INSERT INTO video_title_overrides (kind, tmdb_id, aka_titles, updated_at) "
                    "VALUES (?,?,?,CURRENT_TIMESTAMP) "
                    "ON CONFLICT(kind, tmdb_id) DO UPDATE SET "
                    "  aka_titles=excluded.aka_titles, updated_at=CURRENT_TIMESTAMP",
                    (kind, tid, "\n".join(clean)))
            else:
                # Clearing deletes the row rather than storing an empty string, so
                # "no aliases" is one state instead of two.
                conn.execute("UPDATE video_title_overrides SET aka_titles=NULL, updated_at=CURRENT_TIMESTAMP "
                             "WHERE kind=? AND tmdb_id=?",
                             (kind, tid))
            conn.commit()
            return clean
        except sqlite3.Error:
            logger.exception("set_aka_titles failed (%s %s)", kind, tmdb_id)
            return None
        finally:
            conn.close()

    def aka_titles_for_tmdb(self, kind: str, tmdb_id) -> list:
        """User AKAs for a TMDB title — what the alias resolvers hold."""
        if kind not in ("movie", "show") or tmdb_id is None:
            return []
        conn = self._get_connection()
        try:
            row = conn.execute(
                "SELECT aka_titles FROM video_title_overrides WHERE kind=? AND tmdb_id=?",
                (kind, int(tmdb_id))).fetchone()
            return self._split_akas(row["aka_titles"]) if row else []
        except (sqlite3.Error, TypeError, ValueError):
            return []
        finally:
            conn.close()

    def set_series_type_override(self, tmdb_id, series_type) -> str | None:
        """Set a show's series type BEFORE it's in the library, keyed by tmdb id.

        Series type decides how episode releases are hunted — SxxExx (standard),
        air date (daily), or absolute number (anime) — which matters most while
        you're still trying to acquire the show. ``set_show_series_type`` writes
        the shows row and so only works once you own it. Passing a blank/unknown
        value clears the override and hands the decision back to the library row
        (or the 'standard' default)."""
        st = str(series_type or "").strip().lower()
        if st not in ("standard", "daily", "anime", ""):
            return None
        try:
            tid = int(tmdb_id)
        except (TypeError, ValueError):
            return None
        conn = self._get_connection()
        try:
            conn.execute(
                "INSERT INTO video_title_overrides (kind, tmdb_id, series_type, updated_at) "
                "VALUES ('show',?,?,CURRENT_TIMESTAMP) "
                "ON CONFLICT(kind, tmdb_id) DO UPDATE SET "
                "  series_type=excluded.series_type, updated_at=CURRENT_TIMESTAMP",
                (tid, st or None))
            conn.commit()
            return st
        except sqlite3.Error:
            logger.exception("set_series_type_override failed (%s)", tmdb_id)
            return None
        finally:
            conn.close()

    def series_type_for_tmdb(self, tmdb_id) -> str | None:
        """A show's series-type override, or None when none is set."""
        if tmdb_id is None:
            return None
        conn = self._get_connection()
        try:
            row = conn.execute(
                "SELECT series_type FROM video_title_overrides "
                "WHERE kind='show' AND tmdb_id=?", (int(tmdb_id),)).fetchone()
            return (row["series_type"] or None) if row else None
        except (sqlite3.Error, TypeError, ValueError):
            return None
        finally:
            conn.close()

    def aka_titles(self, kind: str, item_id) -> list:
        """User AKAs for a LIBRARY ROW — resolves the row to its tmdb id first.
        Kept so callers holding a row id (the detail payload) don't each repeat
        that hop. A row with no tmdb id simply has no aliases."""
        return self.aka_titles_for_tmdb(kind, self.tmdb_id_for_library_row(kind, item_id))

    def tmdb_id_for_library_row(self, kind: str, row_id):
        """The tmdb_id of a movies/shows ROW — the inverse of
        ``root_folder_id_for_library_row``'s lookup, for callers holding a library
        row id that need the TMDB identity (e.g. to fetch a title's alias set)."""
        table = {"movie": "movies", "show": "shows"}.get(kind)
        if not table or row_id is None:
            return None
        try:
            row_id = int(row_id)
        except (TypeError, ValueError):
            return None
        conn = self._get_connection()
        try:
            row = conn.execute(
                f"SELECT tmdb_id FROM {table} WHERE id=? LIMIT 1", (row_id,)).fetchone()
            return row["tmdb_id"] if row else None
        except sqlite3.Error:
            return None
        finally:
            conn.close()

    def library_ids_for_tmdb(self, kind: str, tmdb_ids, server_source=None) -> dict:
        """{tmdb_id: library_row_id} for the owned subset of ``tmdb_ids`` on the
        active server. Batched (chunked IN) so a whole Discover rail costs one
        query per kind instead of one connection+query per item."""
        table = {"movie": "movies", "show": "shows"}.get(kind)
        out: dict = {}
        if not table:
            return out
        ids = []
        for x in (tmdb_ids or []):
            try:
                ids.append(int(x))
            except (TypeError, ValueError):
                pass
        if not ids:
            return out
        conn = self._get_connection()
        try:
            for i in range(0, len(ids), 400):   # stay under SQLite's variable cap
                chunk = ids[i:i + 400]
                ph = ",".join("?" * len(chunk))
                sql = f"SELECT id, tmdb_id FROM {table} WHERE tmdb_id IN ({ph})"
                args = list(chunk)
                if server_source:
                    sql += " AND server_source=?"
                    args.append(server_source)
                for row in conn.execute(sql, args):
                    out.setdefault(row["tmdb_id"], row["id"])   # first match wins
            return out
        except sqlite3.Error:
            return out
        finally:
            conn.close()

    def top_owned_genres(self, kind: str, server_source=None, limit: int = 6) -> list:
        """The user's most-owned genre names for movies/shows, busiest first —
        drives Discover's personalized 'Because you like …' rails."""
        if kind == "movie":
            link, owner, tbl, owned = "movie_genres", "movie_id", "movies", "t.has_file=1"
        elif kind == "show":
            link, owner, tbl, owned = "show_genres", "show_id", "shows", "1=1"   # any library show counts
        else:
            return []
        sql = (f"SELECT g.name AS name, COUNT(*) AS c FROM {link} lt "
               f"JOIN genres g ON g.id = lt.genre_id "
               f"JOIN {tbl} t ON t.id = lt.{owner} WHERE {owned}")
        args: list = []
        if server_source:
            sql += " AND t.server_source=?"
            args.append(server_source)
        sql += " GROUP BY g.name ORDER BY c DESC, g.name LIMIT ?"
        args.append(int(limit))
        conn = self._get_connection()
        try:
            return [r["name"] for r in conn.execute(sql, args)]
        except sqlite3.Error:
            return []
        finally:
            conn.close()

    def random_owned_titles(self, limit: int = 2, server_source=None) -> list:
        """A few random owned titles (with a tmdb_id) to seed 'More like …' rails —
        up to ``limit`` movies and ``limit`` shows."""
        out = []
        conn = self._get_connection()
        try:
            for kind, tbl, alias, owned in (("movie", "movies", "m", "m.has_file=1"),
                                            ("show", "shows", "s", "1=1")):
                sql = (f"SELECT {alias}.id AS id, {alias}.tmdb_id AS tmdb_id, {alias}.title AS title "
                       f"FROM {tbl} {alias} WHERE {alias}.tmdb_id IS NOT NULL AND {owned}")
                args: list = []
                if server_source:
                    sql += f" AND {alias}.server_source=?"
                    args.append(server_source)
                sql += " ORDER BY RANDOM() LIMIT ?"
                args.append(int(limit))
                for r in conn.execute(sql, args):
                    out.append({"kind": kind, "tmdb_id": r["tmdb_id"],
                                "title": r["title"], "library_id": r["id"]})
            return out
        except sqlite3.Error:
            return out
        finally:
            conn.close()

    def owned_movie_tmdb_ids(self, server_source=None) -> set:
        """Set of TMDB ids the user OWNS (movies with a file) — for diffing against
        franchise/filmography lists in the gap engine."""
        sql = "SELECT DISTINCT tmdb_id FROM movies WHERE has_file=1 AND tmdb_id IS NOT NULL"
        args: list = []
        if server_source:
            sql += " AND server_source=?"
            args.append(server_source)
        conn = self._get_connection()
        try:
            return {r["tmdb_id"] for r in conn.execute(sql, args)}
        except sqlite3.Error:
            return set()
        finally:
            conn.close()

    def show_tmdb_id(self, show_id):
        """The TMDB id for a library show row (so a failed grab can be re-wishlisted)."""
        conn = self._get_connection()
        try:
            r = conn.execute("SELECT tmdb_id FROM shows WHERE id=?", (int(show_id),)).fetchone()
            return r["tmdb_id"] if r and r["tmdb_id"] else None
        except (sqlite3.Error, ValueError, TypeError):
            return None
        finally:
            conn.close()

    def movie_tmdb_id(self, movie_id):
        """The TMDB id for a library movie row (so a failed grab can be re-wishlisted)."""
        conn = self._get_connection()
        try:
            r = conn.execute("SELECT tmdb_id FROM movies WHERE id=?", (int(movie_id),)).fetchone()
            return r["tmdb_id"] if r and r["tmdb_id"] else None
        except (sqlite3.Error, ValueError, TypeError):
            return None
        finally:
            conn.close()

    def owned_episode_keys(self, show_id) -> set:
        """(season_number, episode_number) pairs already in the library for a show —
        so a season-pack grab can skip episodes you already own."""
        conn = self._get_connection()
        try:
            return {(r["season_number"], r["episode_number"]) for r in conn.execute(
                "SELECT season_number, episode_number FROM episodes WHERE show_id=? AND has_file=1",
                (int(show_id),))}
        except (sqlite3.Error, ValueError, TypeError):
            return set()
        finally:
            conn.close()

    # ── Discover ignore list ("Not interested") ──────────────────────────────
    def add_ignored(self, kind: str, tmdb_id, title=None, year=None, poster_url=None) -> bool:
        """Hide a title from Discover (movie/show level). Idempotent."""
        if kind not in ("movie", "show") or tmdb_id is None:
            return False
        try:
            with self._get_connection() as conn:
                conn.execute(
                    "INSERT OR REPLACE INTO video_ignored (kind, tmdb_id, title, year, poster_url) "
                    "VALUES (?, ?, ?, ?, ?)",
                    (kind, int(tmdb_id), title, int(year) if year else None, poster_url),
                )
                conn.commit()
                return True
        except (sqlite3.Error, TypeError, ValueError) as e:
            logger.debug(f"add_ignored failed: {e}")
            return False

    def remove_ignored(self, kind: str, tmdb_id) -> bool:
        """Un-hide a title."""
        try:
            with self._get_connection() as conn:
                conn.execute("DELETE FROM video_ignored WHERE kind=? AND tmdb_id=?",
                             (kind, int(tmdb_id)))
                conn.commit()
                return True
        except (sqlite3.Error, TypeError, ValueError) as e:
            logger.debug(f"remove_ignored failed: {e}")
            return False

    def list_ignored(self) -> list:
        """All ignored titles, most-recently-added first — drives the manage modal."""
        try:
            with self._get_connection() as conn:
                rows = conn.execute(
                    "SELECT kind, tmdb_id, title, year, poster_url FROM video_ignored "
                    "ORDER BY added_at DESC"
                ).fetchall()
                return [{"kind": r["kind"], "tmdb_id": r["tmdb_id"], "title": r["title"],
                         "year": r["year"], "poster": r["poster_url"]} for r in rows]
        except sqlite3.Error:
            return []

    def ignored_keys(self) -> set:
        """Set of 'kind:tmdb_id' strings for fast Discover filtering."""
        try:
            with self._get_connection() as conn:
                return {f"{r['kind']}:{r['tmdb_id']}"
                        for r in conn.execute("SELECT kind, tmdb_id FROM video_ignored")}
        except sqlite3.Error:
            return set()

    def movies_missing_collection(self, server_source=None, limit: int = 20) -> list:
        """Owned, TMDB-matched movies whose franchise (tmdb_collection_id) hasn't been
        backfilled yet — drives the lazy collection-id backfill. Returns [{id, tmdb_id}].

        Also re-checks rows the OLD buggy backfill zeroed: id=0 WITH a franchise name is a
        real franchise (e.g. Jurassic Park) that got mis-written as 0 — genuinely-no-franchise
        rows are id=0 with a NULL name, so they stay excluded and aren't re-fetched forever."""
        sql = ("SELECT id, tmdb_id FROM movies WHERE has_file=1 AND tmdb_id IS NOT NULL "
               "AND (tmdb_collection_id IS NULL "
               "     OR (tmdb_collection_id = 0 AND tmdb_collection_name IS NOT NULL))")
        args: list = []
        if server_source:
            sql += " AND server_source=?"
            args.append(server_source)
        sql += " LIMIT ?"
        args.append(int(limit))
        conn = self._get_connection()
        try:
            return [{"id": r["id"], "tmdb_id": r["tmdb_id"]} for r in conn.execute(sql, args)]
        except sqlite3.Error:
            return []
        finally:
            conn.close()

    def set_movie_collection(self, movie_id: int, collection_id, name) -> None:
        """Persist a movie's TMDB collection id/name (backfill). collection_id may be
        None — recorded as 0 so the row isn't re-checked forever (a movie with no
        franchise). 0 is excluded from the gap rails."""
        conn = self._get_connection()
        try:
            # A 0 (no-franchise) row must not carry a name — otherwise it looks like a
            # mis-zeroed franchise and gets re-fetched forever.
            conn.execute("UPDATE movies SET tmdb_collection_id=?, tmdb_collection_name=? WHERE id=?",
                         (int(collection_id) if collection_id else 0,
                          name if collection_id else None, int(movie_id)))
            conn.commit()
        except sqlite3.Error:
            pass
        finally:
            conn.close()

    def owned_movie_collections(self, server_source=None, limit: int = 12) -> list:
        """Franchises the user has STARTED (owns >=1 movie in), most-invested first —
        drives the 'Complete your collections' gap rails. Returns
        [{collection_id, name, owned_count}]."""
        sql = ("SELECT tmdb_collection_id AS cid, "
               "MAX(tmdb_collection_name) AS name, COUNT(*) AS c "
               "FROM movies WHERE has_file=1 AND tmdb_collection_id IS NOT NULL "
               "AND tmdb_collection_id != 0")
        args: list = []
        if server_source:
            sql += " AND server_source=?"
            args.append(server_source)
        sql += " GROUP BY tmdb_collection_id ORDER BY c DESC, name LIMIT ?"
        args.append(int(limit))
        conn = self._get_connection()
        try:
            return [{"collection_id": r["cid"], "name": r["name"], "owned_count": r["c"]}
                    for r in conn.execute(sql, args)]
        except sqlite3.Error:
            return []
        finally:
            conn.close()

    def top_owned_people(self, jobs=("Director", "Creator"), min_titles: int = 2,
                         limit: int = 8, server_source=None) -> list:
        """People the user owns the most titles from (e.g. directors), busiest first —
        drives the 'More from <person>' gap rails. Returns
        [{person_id, tmdb_id, name, owned_count}] for people with a TMDB id and at
        least ``min_titles`` owned movies in the given crew ``jobs``."""
        job_list = [j for j in (jobs or []) if j]
        if not job_list:
            return []
        placeholders = ",".join("?" for _ in job_list)
        sql = (f"SELECT p.id AS pid, p.tmdb_id AS tmdb_id, p.name AS name, "
               f"COUNT(DISTINCT c.movie_id) AS c "
               f"FROM credits c JOIN people p ON p.id = c.person_id "
               f"JOIN movies m ON m.id = c.movie_id "
               f"WHERE m.has_file=1 AND c.department='crew' AND c.job IN ({placeholders}) "
               f"AND p.tmdb_id IS NOT NULL")
        args: list = list(job_list)
        if server_source:
            sql += " AND m.server_source=?"
            args.append(server_source)
        sql += " GROUP BY p.id HAVING c >= ? ORDER BY c DESC, p.name LIMIT ?"
        args.append(int(min_titles))
        args.append(int(limit))
        conn = self._get_connection()
        try:
            return [{"person_id": r["pid"], "tmdb_id": r["tmdb_id"],
                     "name": r["name"], "owned_count": r["c"]}
                    for r in conn.execute(sql, args)]
        except sqlite3.Error:
            return []
        finally:
            conn.close()

    def apply_ratings(self, kind: str, item_id: int, ratings: dict) -> None:
        """Store IMDb / RT / Metacritic scores (from OMDb) + mark ratings_synced.
        Ratings are dynamic, so these overwrite (unlike gap-only metadata)."""
        table = {"movie": "movies", "show": "shows"}.get(kind)
        cols = {"imdb_rating", "rt_rating", "metacritic"}
        sets, params = ["ratings_synced=1"], []
        for c, v in (ratings or {}).items():
            if c in cols and v is not None:
                sets.append(f"{c}=?")
                params.append(v)
        if not table:
            return
        params.append(item_id)
        conn = self._get_connection()
        try:
            conn.execute(f"UPDATE {table} SET {', '.join(sets)} WHERE id=?", params)
            conn.commit()
        finally:
            conn.close()

    def ratings_next(self) -> dict | None:
        """Next library item that needs OMDb ratings (has an imdb_id, not synced).
        Drives the OMDb worker's background pass. Returns {kind, id, title, imdb_id}."""
        conn = self._get_connection()
        try:
            for kind, tbl in (("movie", "movies"), ("show", "shows")):
                row = conn.execute(
                    f"SELECT id, title, imdb_id FROM {tbl} "
                    "WHERE imdb_id IS NOT NULL AND imdb_id<>'' AND ratings_synced=0 "
                    "ORDER BY id LIMIT 1").fetchone()
                if row:
                    return {"kind": kind, "id": row["id"], "title": row["title"], "imdb_id": row["imdb_id"]}
            return None
        finally:
            conn.close()

    def mark_ratings_synced(self, kind: str, item_id: int) -> None:
        table = {"movie": "movies", "show": "shows"}.get(kind)
        if not table:
            return
        conn = self._get_connection()
        try:
            conn.execute(f"UPDATE {table} SET ratings_synced=1 WHERE id=?", (item_id,))
            conn.commit()
        finally:
            conn.close()

    def mark_episodes_synced(self, show_id: int) -> None:
        """Flag that the show's FULL episode list has been pulled from metadata
        (so the lazy on-view refresh doesn't re-cascade every visit)."""
        conn = self._get_connection()
        try:
            conn.execute("UPDATE shows SET episodes_synced=1 WHERE id=?", (show_id,))
            conn.commit()
        finally:
            conn.close()

    def episode_sync_next(self) -> dict | None:
        """A matched show (has tmdb_id) whose FULL episode list hasn't been pulled
        yet — for the TMDB worker's background episode-sync pass, so library cards
        show real owned/total without the user opening each one."""
        conn = self._get_connection()
        try:
            row = conn.execute(
                "SELECT id, title, year, tmdb_id FROM shows "
                "WHERE tmdb_id IS NOT NULL AND episodes_synced=0 ORDER BY id LIMIT 1").fetchone()
            return dict(row) if row else None
        finally:
            conn.close()

    def stale_enriched_items(self, limit: int = 500, movie_days: int = 30,
                             show_days: int = 30) -> list[dict]:
        """The N matched library items whose metadata is stalest — for the periodic
        re-enrichment automation. Oldest-refreshed first, and ONLY items last touched
        beyond their per-kind staleness floor (so a run never re-pulls something already
        fresh). ``tmdb_last_attempted`` is stamped by every enrichment_apply — initial
        match, lazy on-view refresh, or a prior re-enrich pass — so it doubles as the
        'last refreshed' cursor without a new column.

        Movies and shows each carry their own floor (both default 30 — roughly monthly;
        metadata drifts slowly, so once a month is plenty). An episode rides along when
        its show is refreshed (refresh_show_art cascades the episode list), so there's no
        separate episode queue — episodes stay as fresh as their show's monthly pass.

        Returns ``[{kind, id, title, last_attempted}]`` (kind ∈ movie/show). A NULL
        last_attempted (matched but never stamped — legacy rows) sorts first."""
        try:
            limit = max(1, int(limit))
        except (TypeError, ValueError):
            limit = 500

        def _cutoff(days, default):
            try:
                d = max(0, int(days))
            except (TypeError, ValueError):
                d = default
            return (datetime.now(timezone.utc) - timedelta(days=d)).strftime("%Y-%m-%d %H:%M:%S")

        m_cut = _cutoff(movie_days, 30)
        s_cut = _cutoff(show_days, 30)
        conn = self._get_connection()
        try:
            rows = conn.execute(
                "SELECT kind, id, title, last_attempted FROM ("
                "  SELECT 'movie' AS kind, id, title, tmdb_last_attempted AS last_attempted"
                "    FROM movies"
                "   WHERE tmdb_match_status='matched'"
                "     AND (tmdb_last_attempted IS NULL OR tmdb_last_attempted < ?)"
                "  UNION ALL"
                "  SELECT 'show' AS kind, id, title, tmdb_last_attempted AS last_attempted"
                "    FROM shows"
                "   WHERE tmdb_match_status='matched'"
                "     AND (tmdb_last_attempted IS NULL OR tmdb_last_attempted < ?)"
                ") ORDER BY last_attempted ASC LIMIT ?",
                (m_cut, s_cut, limit)).fetchall()
            return [dict(r) for r in rows]
        finally:
            conn.close()

    def episode_sync_pending_count(self) -> int:
        conn = self._get_connection()
        try:
            return conn.execute(
                "SELECT COUNT(*) FROM shows WHERE tmdb_id IS NOT NULL AND episodes_synced=0").fetchone()[0]
        finally:
            conn.close()

    # ── TMDB details backfill (status / network / tagline / rating …) ──────────
    # The media server pre-matches items (tmdb_id set), so the matcher skips them and
    # never fetches details-only fields. This one-time pass re-fetches details for a
    # matched item and gap-fills, then marks it done so it isn't re-picked.
    _DETAIL_TBL = {"show": "shows", "movie": "movies"}

    def detail_backfill_next(self, kind: str) -> dict | None:
        """Next matched show/movie (has tmdb_id) whose details haven't been
        backfilled yet. Returns {kind, id, title, year, tmdb_id} or None."""
        tbl = self._DETAIL_TBL.get(kind)
        if not tbl:
            return None
        conn = self._get_connection()
        try:
            row = conn.execute(
                f"SELECT id, title, year, tmdb_id FROM {tbl} "
                f"WHERE tmdb_id IS NOT NULL AND details_synced=0 ORDER BY id LIMIT 1").fetchone()
            if not row:
                return None
            d = dict(row)
            d["kind"] = kind
            return d
        finally:
            conn.close()

    def mark_details_synced(self, kind: str, item_id: int) -> None:
        """Flag that an item's TMDB details were backfilled (attempted once), so the
        background pass doesn't re-pick it even if a field stayed empty."""
        tbl = self._DETAIL_TBL.get(kind)
        if not tbl:
            return
        conn = self._get_connection()
        try:
            conn.execute(f"UPDATE {tbl} SET details_synced=1 WHERE id=?", (item_id,))
            conn.commit()
        finally:
            conn.close()

    def identity_unverified_show(self) -> dict | None:
        """Next show whose TMDB id has never been CONFIRMED against a second
        provider. Returns {id, title, tmdb_id, tvdb_id} or None.

        Only shows carrying BOTH ids qualify, because the tvdb_id is what makes
        confirmation possible — it resolves to exactly one TMDB id, so a
        disagreement is proof rather than an opinion. Shows without one are left
        alone: there is nothing to check them against, and re-running the same
        title search that produced the id would only agree with itself."""
        conn = self._get_connection()
        try:
            row = conn.execute(
                "SELECT id, title, tmdb_id, tvdb_id FROM shows "
                "WHERE tmdb_id IS NOT NULL AND tvdb_id IS NOT NULL "
                "AND COALESCE(tmdb_identity_verified, 0) = 0 ORDER BY id LIMIT 1").fetchone()
            return dict(row) if row else None
        except sqlite3.Error:
            logger.exception("identity_unverified_show failed")
            return None
        finally:
            conn.close()

    def mark_identity_verified(self, show_id: int) -> None:
        """Record that a show's TMDB id was checked against its TVDB id — whether
        it agreed or was corrected. One call per show, once, ever."""
        conn = self._get_connection()
        try:
            conn.execute("UPDATE shows SET tmdb_identity_verified=1 WHERE id=?", (show_id,))
            conn.commit()
        except sqlite3.Error:
            logger.exception("mark_identity_verified failed for show %s", show_id)
        finally:
            conn.close()

    def identity_unverified_count(self) -> int:
        conn = self._get_connection()
        try:
            return conn.execute(
                "SELECT COUNT(*) FROM shows WHERE tmdb_id IS NOT NULL AND tvdb_id IS NOT NULL "
                "AND COALESCE(tmdb_identity_verified, 0) = 0").fetchone()[0]
        except sqlite3.Error:
            return 0
        finally:
            conn.close()

    def detail_backfill_pending_count(self) -> int:
        conn = self._get_connection()
        try:
            n = 0
            for tbl in ("shows", "movies"):
                n += conn.execute(
                    f"SELECT COUNT(*) FROM {tbl} WHERE tmdb_id IS NOT NULL AND details_synced=0").fetchone()[0]
            return n
        finally:
            conn.close()

    def queue_detail_resync(self, kind: str = "all") -> int:
        """Reset ``details_synced=0`` for on-server, TMDB-matched titles so the detail-sync
        worker re-pulls their FULL TMDB metadata (studios/networks/overview/…) and re-applies
        it through enrichment_apply. This is how an existing library gets the complete
        multi-company studio/network data (the server scan only knows one). Returns the count
        queued; the already-running worker drains it in the background."""
        tables = []
        if kind in ("all", "movie"):
            tables.append("movies")
        if kind in ("all", "show"):
            tables.append("shows")
        total = 0
        conn = self._get_connection()
        try:
            for tbl in tables:
                cur = conn.execute(
                    f"UPDATE {tbl} SET details_synced=0 "
                    f"WHERE tmdb_id IS NOT NULL AND server_id IS NOT NULL AND TRIM(server_id) != ''")
                total += cur.rowcount
            conn.commit()
            return total
        finally:
            conn.close()

    # ── manual match editor (Manage panel "Matches" section) ──────────────────
    # Services a user can re-point per kind. tmdb/tvdb have real matchers (the
    # enrichment workers re-enrich BY the new id); imdb is a plain id the ratings
    # + several backfills key off.
    _MATCH_SERVICES = {"movie": ("tmdb", "imdb"), "show": ("tmdb", "tvdb", "imdb")}

    # Textual metadata the old (wrong) match may have gap-filled — cleared on a
    # re-match (unlocked fields only) so the fresh enrichment can land; gap-fill
    # never overwrites, so leaving them would keep the wrong show's data. Art
    # columns (poster/backdrop/logo/…) are deliberately NOT cleared: they may be
    # server-provided or user-picked (Poster Manager), and wrong art is fixable
    # there without data loss.
    _REMATCH_CLEAR_COLS = {
        "overview", "tagline", "status", "rating", "rating_critic", "content_rating",
        "network", "studio", "first_air_date", "last_air_date", "airs_time",
        "release_date", "runtime_minutes", "tmdb_collection_id", "tmdb_collection_name",
        "trakt_rating", "trakt_votes", "tvmaze_rating", "anilist_score",
        "wikidata_url", "streaming", "mediastinger", "awards", "subtitle_langs",
    }

    def item_matches(self, kind: str, item_id: int) -> list | None:
        """Per-service match state for one movie/show — feeds the Manage panel's
        Matches section. None for an unknown kind, [] for a missing row."""
        tbl = self._DETAIL_TBL.get(kind)
        services = self._MATCH_SERVICES.get(kind)
        if not tbl or not services:
            return None
        conn = self._get_connection()
        try:
            row = conn.execute(f"SELECT * FROM {tbl} WHERE id=?", (item_id,)).fetchone()
            if not row:
                return []
            keys = row.keys()
            out = []
            for svc in services:
                if svc == "imdb":
                    imdb = row["imdb_id"] if "imdb_id" in keys else None
                    out.append({"service": "imdb", "id": imdb,
                                "status": "matched" if imdb else None, "attempted": None})
                    continue
                idc, sc, ac = f"{svc}_id", f"{svc}_match_status", f"{svc}_last_attempted"
                out.append({"service": svc,
                            "id": row[idc] if idc in keys else None,
                            "status": row[sc] if sc in keys else None,
                            "attempted": row[ac] if ac in keys else None})
            return out
        finally:
            conn.close()

    def rematch_item(self, kind: str, item_id: int, service: str, external_id) -> bool:
        """Re-point one service's match to ``external_id`` (or clear it with None)
        and reset everything DERIVED from the old match so the existing workers
        re-enrich fresh, by the new id:

        - the service's match_status goes back to NULL (pending) so
          enrichment_next re-picks the item and enriches BY the new known id —
          or to 'not_found' on a clear;
        - textual metadata the old match gap-filled is cleared (unlocked fields
          only — user edits stay theirs), because gap-fill never overwrites;
        - details/episodes/ratings sync flags and every backfill service's
          status reset, so the whole derived pipeline re-runs;
        - enrichment-sourced credits are dropped (they were the wrong title's).

        Mirrors the music side's "re-match clears the stored id + re-enriches"
        contract (#868)."""
        tbl = self._DETAIL_TBL.get(kind)
        if not tbl or service not in self._MATCH_SERVICES.get(kind, ()):
            return False
        conn = self._get_connection()
        try:
            if not conn.execute(f"SELECT 1 FROM {tbl} WHERE id=?", (item_id,)).fetchone():
                return False
            cols = {r[1] for r in conn.execute(f"PRAGMA table_info({tbl})").fetchall()}
            locked = self._locked_fields_set(conn, tbl, item_id)
            sets, params = [], []

            if service == "imdb":
                # An imdb re-point only feeds the id-keyed pipeline (ratings +
                # imdb-based backfills) — TMDB-sourced text stays untouched.
                sets.append("imdb_id=?")
                params.append(external_id)
                if "ratings_synced" in cols:
                    sets.append("ratings_synced=0")
                for svc, svc_map in _BACKFILL.items():
                    spec = svc_map.get(kind)
                    if not spec or spec[0] != tbl or "imdb_id" not in spec[3]:
                        continue
                    for c in (spec[1], spec[2]):
                        if c in cols:
                            sets.append(f"{c}=NULL")
                    for c in sorted(_BACKFILL_COLS.get(svc, set()) & cols - locked):
                        sets.append(f"{c}=NULL")
            elif service == "tvdb":
                # TVDB owns the air time + the tvdb-keyed backfills (fanart,
                # TVmaze); TMDB-sourced text is not its blast radius.
                sets.append("tvdb_id=?")
                params.append(external_id)
                sets.append("tvdb_match_status=" +
                            ("NULL" if external_id is not None else "'not_found'"))
                sets.append("tvdb_last_attempted=NULL")
                if "airs_time" in cols and "airs_time" not in locked:
                    sets.append("airs_time=NULL")
                for svc_map in _BACKFILL.values():
                    spec = svc_map.get(kind)
                    if not spec or spec[0] != tbl or "tvdb_id" not in spec[3]:
                        continue
                    for c in (spec[1], spec[2]):
                        if c in cols:
                            sets.append(f"{c}=NULL")
            else:   # tmdb — the identity match; the whole derived pipeline re-runs
                sets.append("tmdb_id=?")
                params.append(external_id)
                sets.append("tmdb_match_status=" +
                            ("NULL" if external_id is not None else "'not_found'"))
                sets.append("tmdb_last_attempted=NULL")
                # Clear what the old match derived (never a locked field, never art).
                for col in sorted(self._REMATCH_CLEAR_COLS & cols - locked):
                    sets.append(f"{col}=NULL")
                # Re-run everything: detail backfill, episode cascade, OMDb
                # ratings, and every id-keyed backfill service.
                for flag in ("details_synced", "episodes_synced", "ratings_synced"):
                    if flag in cols:
                        sets.append(f"{flag}=0")
                for svc_map in _BACKFILL.values():
                    spec = svc_map.get(kind)
                    if spec and spec[0] == tbl:
                        if spec[1] in cols:
                            sets.append(f"{spec[1]}=NULL")
                        if spec[2] in cols:
                            sets.append(f"{spec[2]}=NULL")

            params.append(item_id)
            conn.execute(f"UPDATE {tbl} SET {', '.join(sets)} WHERE id=?", params)
            # Credits came from the old TMDB match (gap-filled only-when-empty, so
            # they'd never self-correct). Dropping them lets the fresh enrichment
            # refill from the right title. TVDB/imdb don't source credits.
            if service == "tmdb":
                oc = "movie_id" if tbl == "movies" else "show_id"
                conn.execute(f"DELETE FROM credits WHERE {oc}=?", (item_id,))
            conn.commit()
            return True
        finally:
            conn.close()

    def _ratings_breakdown(self) -> dict:
        """OMDb 'coverage' breakdown: matched = ratings present, pending = has an
        imdb_id but not fetched, not_found = fetched but OMDb had no rating."""
        conn = self._get_connection()
        out = {}
        try:
            for kind, tbl in (("movie", "movies"), ("show", "shows")):
                def c(where, _tbl=tbl):   # bind tbl per-iteration (ruff B023)
                    return conn.execute(f"SELECT COUNT(*) FROM {_tbl} WHERE {where}").fetchone()[0]
                out[kind] = {
                    "matched": c("imdb_rating IS NOT NULL"),
                    "not_found": c("ratings_synced=1 AND imdb_rating IS NULL AND imdb_id IS NOT NULL"),
                    "errors": 0,
                    "pending": c("imdb_id IS NOT NULL AND imdb_id<>'' AND ratings_synced=0"),
                }
            return out
        finally:
            conn.close()

    def show_season_numbers(self, show_id: int) -> list:
        conn = self._get_connection()
        try:
            return [r["season_number"] for r in conn.execute(
                "SELECT season_number FROM seasons WHERE show_id=? ORDER BY season_number",
                (show_id,)).fetchall()]
        finally:
            conn.close()

    # A backfilled row is a PHANTOM when the media server already holds that
    # episode under a different season number. Same identity rule as
    # _already_owned_elsewhere, run in reverse over rows that already exist —
    # and with the same refusal to guess: exactly one owned row and exactly one
    # server-less row may share the date, or the pairing is not provable.
    _DUPE_EPISODE_SQL = """
        SELECT e.id, e.show_id, e.season_number, e.episode_number, e.title,
               substr(COALESCE(e.air_date,''),1,10) AS air_date,
               s.title AS show_title,
               (SELECT o.season_number FROM episodes o
                 WHERE o.show_id = e.show_id
                   AND substr(COALESCE(o.air_date,''),1,10) = substr(e.air_date,1,10)
                   AND o.server_id IS NOT NULL) AS owned_season,
               (SELECT o.episode_number FROM episodes o
                 WHERE o.show_id = e.show_id
                   AND substr(COALESCE(o.air_date,''),1,10) = substr(e.air_date,1,10)
                   AND o.server_id IS NOT NULL) AS owned_episode
          FROM episodes e
          JOIN shows s ON s.id = e.show_id
         WHERE e.server_id IS NULL            -- never a row the server put there
           AND e.has_file = 0                 -- never something you actually hold
           AND COALESCE(e.air_date,'') <> ''
           AND NOT EXISTS (SELECT 1 FROM media_files mf WHERE mf.episode_id = e.id)
           AND (SELECT COUNT(*) FROM episodes o
                 WHERE o.show_id = e.show_id
                   AND substr(COALESCE(o.air_date,''),1,10) = substr(e.air_date,1,10)
                   AND o.server_id IS NOT NULL) = 1
           AND (SELECT COUNT(*) FROM episodes o
                 WHERE o.show_id = e.show_id
                   AND substr(COALESCE(o.air_date,''),1,10) = substr(e.air_date,1,10)
                   AND o.server_id IS NULL) = 1
           AND EXISTS (SELECT 1 FROM episodes o
                 WHERE o.show_id = e.show_id
                   AND substr(COALESCE(o.air_date,''),1,10) = substr(e.air_date,1,10)
                   AND o.server_id IS NOT NULL
                   AND o.season_number <> e.season_number)
    """

    def duplicate_episode_rows(self, show_id=None, limit: int = 2000) -> list:
        """Phantom episode rows — the same episode listed a second time under the
        OTHER source's season numbering (see _already_owned_elsewhere).

        Read-only. This is the preview a destructive clean-up is chosen from, so
        it returns enough to judge each row: which season/episode the phantom
        claims to be, and which owned one it duplicates.
        """
        sql = self._DUPE_EPISODE_SQL
        params: list = []
        if show_id is not None:
            sql += " AND e.show_id = ?"
            params.append(int(show_id))
        sql += " ORDER BY s.title COLLATE NOCASE, e.season_number, e.episode_number LIMIT ?"
        params.append(max(1, int(limit)))
        conn = self._get_connection()
        try:
            return [dict(r) for r in conn.execute(sql, params).fetchall()]
        except (sqlite3.Error, ValueError, TypeError):
            logger.exception("duplicate_episode_rows failed")
            return []
        finally:
            conn.close()

    def delete_episode_rows(self, ids) -> int:
        """Delete specific episode rows by id — but only ones that still qualify
        as phantoms at delete time.

        The caller passes ids it got from a PREVIEW, and the library may have
        changed since (a scan landed, a file arrived). Re-checking against the
        same rule means a row that has since become real is skipped rather than
        deleted, so a stale preview can never destroy something you own.
        """
        wanted = {int(i) for i in (ids or []) if str(i).strip().lstrip("-").isdigit()}
        if not wanted:
            return 0
        conn = self._get_connection()
        try:
            still = {int(r["id"]) for r in conn.execute(self._DUPE_EPISODE_SQL).fetchall()}
            doomed = sorted(wanted & still)
            if not doomed:
                return 0
            ph = ",".join("?" * len(doomed))
            conn.execute(f"DELETE FROM episodes WHERE id IN ({ph})", doomed)
            conn.commit()
            return len(doomed)
        except (sqlite3.Error, ValueError, TypeError):
            logger.exception("delete_episode_rows failed")
            return 0
        finally:
            conn.close()

    @staticmethod
    def _ambiguous_air_dates(episodes) -> set:
        """Air dates shared by more than one episode in THIS batch.

        The other half of the streaming-drop problem. A whole season released in
        one go carries a single date across every episode, so if you happen to
        own just one of them, every other episode would match that one owned row
        and be suppressed — hiding the very episodes the backfill exists to
        surface. A date that cannot identify a single episode on the incoming
        side is no more usable than one that cannot on the existing side.
        """
        seen, dupes = set(), set()
        for e in (episodes or []):
            d = str((e or {}).get("air_date") or "").strip()[:10]
            if not d:
                continue
            if d in seen:
                dupes.add(d)
            seen.add(d)
        return dupes

    @staticmethod
    def _already_owned_elsewhere(conn, show_id: int, season_number: int, air_date) -> bool:
        """Whether the media server already holds this episode under a DIFFERENT
        season number, so backfilling it again would duplicate it.

        Media servers and TMDB do not always agree on how a long-running show is
        split into seasons — Plex files Bleach's newer run as S2 where TMDB calls
        it S17. Both numbers are 'right', but they are different rows under
        UNIQUE(show_id, season_number, episode_number), so the episode appears
        twice: once owned, once missing. The scan's prune only ever inspects rows
        with a server_id, so the backfilled phantom survives every rescan.

        The air date is the only identity both sources reliably share (TMDB's
        season endpoint carries no tvdb id). It is used under three guards,
        because a careless match here would HIDE episodes:

          • the existing row must be SERVER-BACKED (server_id NOT NULL). A row
            put there by an earlier backfill proves nothing about ownership.
          • it must be under a different season — same-season is the normal
            update path handled above.
          • the date must identify exactly ONE episode in the show. A streaming
            season that drops in one go shares a single air date across every
            episode; matching on that would suppress all but one of them.

        No air date → no judgement, and the episode is created as before.
        """
        d = str(air_date or "").strip()[:10]
        if not d:
            return False
        try:
            rows = conn.execute(
                "SELECT season_number, server_id FROM episodes "
                "WHERE show_id=? AND substr(COALESCE(air_date,''),1,10)=?",
                (int(show_id), d)).fetchall()
        except (sqlite3.Error, ValueError, TypeError):
            return False
        if len(rows) != 1:      # ambiguous (or nothing) — never guess
            return False
        row = rows[0]
        return (row["server_id"] is not None
                and int(row["season_number"] or -1) != int(season_number))

    def backfill_episodes(self, show_id: int, season_number: int, episodes: list,
                          season_overview: str | None = None, season_poster: str | None = None,
                          update_only: bool = False) -> int:
        """UPSERT a season's episodes from the metadata provider so the show's
        FULL episode list is represented — owned episodes (from the server) keep
        has_file=1, and episodes the server doesn't have are inserted as MISSING
        (has_file=0). Existing rows get gap-only metadata fills (never clobbered);
        the season row is created if it didn't exist (a fully-missing season).
        Returns the number of episode rows touched.

        ``update_only=True`` fills metadata on episodes that already exist and
        NEVER creates one. Required for any SECONDARY provider, because a season
        NUMBER does not mean the same thing in two providers. Bleach is the case
        that proved it: TMDB season 2 is the 2005 Soul Society arc, while TVDB
        files the 2022+ Thousand-Year Blood War run as its season 2. Feeding
        TMDB's season numbers to TVDB (which is what the caller has) and letting
        it insert put seventeen TYBW episodes — numbered 25, 26, 39-53 — inside
        the 21-episode 2005 season, where no release will ever match them."""
        conn = self._get_connection()
        touched = 0
        try:
            if not update_only:
                conn.execute("INSERT OR IGNORE INTO seasons (show_id, season_number) VALUES (?, ?)",
                             (show_id, season_number))
            srow = conn.execute("SELECT id FROM seasons WHERE show_id=? AND season_number=?",
                                (show_id, season_number)).fetchone()
            if srow is None:
                return 0          # update_only, and this season isn't ours to create
            season_id = srow["id"]
            if season_overview or season_poster:
                conn.execute("UPDATE seasons SET overview=COALESCE(NULLIF(overview, ''), ?), "
                             "poster_url=COALESCE(NULLIF(poster_url, ''), ?) WHERE id=?",
                             (season_overview, season_poster, season_id))
            _ambiguous = self._ambiguous_air_dates(episodes)
            for e in (episodes or []):
                en = e.get("episode_number")
                if en is None:
                    continue
                row = conn.execute(
                    "SELECT id FROM episodes WHERE show_id=? AND season_number=? AND episode_number=?",
                    (show_id, season_number, en)).fetchone()
                if row:
                    # Gap-fill only, always. An earlier attempt let the primary
                    # provider OVERWRITE rows that looked provider-owned (no
                    # server_id, no file) to repair mis-filed metadata — but a
                    # server CAN report an episode without a per-episode id, and
                    # that heuristic then clobbered real server data. Mis-filed
                    # rows are handled by choosing the right provider in the first
                    # place (core/video/episode_numbering) and removing what does
                    # not belong, not by weakening this guarantee.
                    sets, params = [], []
                    for col in ("title", "still_url", "overview", "air_date", "rating", "runtime_minutes"):
                        if e.get(col) is None:
                            continue
                        sets.append(f"{col}=COALESCE(NULLIF({col}, ''), ?)")
                        params.append(e[col])
                    if sets:
                        params += [row["id"]]
                        conn.execute(f"UPDATE episodes SET {', '.join(sets)} WHERE id=?", params)
                        touched += 1
                elif update_only:
                    continue          # a secondary provider may enrich, never invent
                elif (str(e.get("air_date") or "").strip()[:10] not in _ambiguous
                      and self._already_owned_elsewhere(conn, show_id, season_number,
                                                        e.get("air_date"))):
                    # The media server already has this episode under ITS OWN season
                    # numbering. Bleach: Plex files the newer run as S2, TMDB calls it
                    # S17 — different season numbers are different rows under
                    # UNIQUE(show_id, season_number, episode_number), so inserting
                    # would list the same episode twice, once owned and once missing.
                    # Worse, the scan's prune only ever looks at rows with a
                    # server_id, so the phantom copy survives every rescan forever.
                    continue
                else:
                    conn.execute(
                        "INSERT INTO episodes (show_id, season_id, season_number, episode_number, title, "
                        "overview, air_date, runtime_minutes, still_url, rating, has_file) "
                        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 0)",
                        (show_id, season_id, season_number, en, e.get("title"), e.get("overview"),
                         e.get("air_date"), e.get("runtime_minutes"), e.get("still_url"), e.get("rating")))
                    touched += 1
            conn.commit()
            return touched
        finally:
            conn.close()

    def enrichment_unmatched(self, service: str, kind: str, status: str = "not_found",
                             search=None, limit: int = 50, offset: int = 0) -> dict:
        if kind == "episode" and service == "tmdb":
            return self._episodes_missing_art(search, limit, offset)
        if service == "omdb" and kind in ("movie", "show"):
            return self._ratings_unmatched(kind, search, limit, offset)
        spec = _ENRICH.get(service, {}).get(kind)
        if not spec:
            return {"items": [], "total": 0}
        tbl, _idc, sc, ac = spec
        where, params = [], []
        if status == "pending":
            where.append(f"{sc} IS NULL")
        elif status == "unmatched":
            where.append(f"({sc} IS NULL OR {sc} IN ('not_found','error'))")
        else:
            where.append(f"{sc}='not_found'")
        if search:
            where.append("title LIKE ? COLLATE NOCASE")
            params.append("%" + search + "%")
        where_sql = " WHERE " + " AND ".join(where)
        conn = self._get_connection()
        try:
            total = conn.execute(f"SELECT COUNT(*) FROM {tbl}{where_sql}", params).fetchone()[0]
            rows = conn.execute(
                f"SELECT id, title, year, {ac} AS last_attempted, "
                f"(poster_url IS NOT NULL AND poster_url<>'') AS has_poster "
                f"FROM {tbl}{where_sql} ORDER BY COALESCE(sort_title, title) COLLATE NOCASE "
                f"LIMIT ? OFFSET ?", params + [limit, offset]).fetchall()
            items = []
            for r in rows:
                d = dict(r)
                d["has_poster"] = bool(d.get("has_poster"))
                items.append(d)
            return {"items": items, "total": total}
        finally:
            conn.close()

    def _episodes_missing_art(self, search, limit, offset) -> dict:
        """Episodes still lacking a still image (for the manager's Episodes view).
        Read-only: episode art is backfilled as a cascade, not a retry queue."""
        where = ["(e.still_url IS NULL OR e.still_url='')"]
        params: list = []
        if search:
            where.append("(e.title LIKE ? COLLATE NOCASE OR sh.title LIKE ? COLLATE NOCASE)")
            params += ["%" + search + "%", "%" + search + "%"]
        where_sql = " WHERE " + " AND ".join(where)
        conn = self._get_connection()
        try:
            total = conn.execute(
                f"SELECT COUNT(*) FROM episodes e JOIN shows sh ON sh.id=e.show_id{where_sql}",
                params).fetchone()[0]
            rows = conn.execute(
                "SELECT e.id, sh.title || ' · S' || e.season_number || 'E' || e.episode_number "
                "|| COALESCE(' · ' || e.title, '') AS title, e.air_date AS year, "
                "0 AS has_poster, NULL AS last_attempted "
                f"FROM episodes e JOIN shows sh ON sh.id=e.show_id{where_sql} "
                "ORDER BY sh.sort_title, e.season_number, e.episode_number LIMIT ? OFFSET ?",
                params + [limit, offset]).fetchall()
            return {"items": [dict(r) | {"has_poster": False} for r in rows], "total": total}
        finally:
            conn.close()

    def _ratings_unmatched(self, kind: str, search, limit: int, offset: int) -> dict:
        tbl = {"movie": "movies", "show": "shows"}[kind]
        where = ["imdb_rating IS NULL", "imdb_id IS NOT NULL", "imdb_id<>''"]
        params: list = []
        if search:
            where.append("title LIKE ? COLLATE NOCASE")
            params.append("%" + search + "%")
        where_sql = " WHERE " + " AND ".join(where)
        conn = self._get_connection()
        try:
            total = conn.execute(f"SELECT COUNT(*) FROM {tbl}{where_sql}", params).fetchone()[0]
            rows = conn.execute(
                f"SELECT id, title, year, (poster_url IS NOT NULL AND poster_url<>'') AS has_poster "
                f"FROM {tbl}{where_sql} ORDER BY COALESCE(sort_title, title) COLLATE NOCASE "
                "LIMIT ? OFFSET ?", params + [limit, offset]).fetchall()
            return {"items": [dict(r) | {"has_poster": bool(r["has_poster"])} for r in rows], "total": total}
        finally:
            conn.close()

    def enrichment_retry(self, service: str, kind: str, scope: str = "failed", item_id=None) -> int:
        """Re-queue items by resetting status/last_attempted to NULL."""
        if service == "omdb":
            tbl = {"movie": "movies", "show": "shows"}.get(kind)
            if not tbl:
                return 0
            conn = self._get_connection()
            try:
                if scope == "item" and item_id is not None:
                    cur = conn.execute(f"UPDATE {tbl} SET ratings_synced=0 WHERE id=?", (item_id,))
                else:
                    cur = conn.execute(f"UPDATE {tbl} SET ratings_synced=0 WHERE imdb_rating IS NULL")
                conn.commit()
                return cur.rowcount
            finally:
                conn.close()
        if service in ("ryd", "sponsorblock", "dearrow"):
            col = {"ryd": "ryd_status", "sponsorblock": "sb_status", "dearrow": "dearrow_status"}[service]
            att = {"ryd": "ryd_attempted", "sponsorblock": "sb_attempted", "dearrow": "dearrow_attempted"}[service]
            conn = self._get_connection()
            try:
                if scope == "item" and item_id is not None:
                    cur = conn.execute(
                        f"UPDATE youtube_video_stats SET {col}=NULL, {att}=NULL WHERE youtube_id=?",
                        (str(item_id),))
                else:
                    cur = conn.execute(
                        f"UPDATE youtube_video_stats SET {col}=NULL, {att}=NULL "
                        f"WHERE {col} IN ('not_found','error')")
                conn.commit()
                return cur.rowcount
            finally:
                conn.close()
        if service in _BACKFILL:
            spec = _BACKFILL[service].get(kind)
            if not spec:
                return 0
            tbl, sc, ac, _has = spec
            conn = self._get_connection()
            try:
                if scope == "item" and item_id is not None:
                    cur = conn.execute(f"UPDATE {tbl} SET {sc}=NULL, {ac}=NULL WHERE id=?", (item_id,))
                else:
                    cur = conn.execute(
                        f"UPDATE {tbl} SET {sc}=NULL, {ac}=NULL WHERE {sc} IN ('not_found','error')")
                conn.commit()
                return cur.rowcount
            finally:
                conn.close()
        spec = _ENRICH.get(service, {}).get(kind)
        if not spec:
            return 0
        tbl, _idc, sc, ac = spec
        conn = self._get_connection()
        try:
            if scope == "item" and item_id is not None:
                cur = conn.execute(f"UPDATE {tbl} SET {sc}=NULL, {ac}=NULL WHERE id=?", (item_id,))
            else:
                cur = conn.execute(
                    f"UPDATE {tbl} SET {sc}=NULL, {ac}=NULL WHERE {sc} IN ('not_found','error')")
            conn.commit()
            return cur.rowcount
        finally:
            conn.close()

    def retry_all_failed(self) -> int:
        """Re-queue every failed/not_found item across ALL enrichment services and
        their kinds (the modal's GLOBAL 'Retry all failed'). Derives the service+kind
        set from the same maps the workers use, so it stays in sync. Returns the
        total number of items re-queued."""
        pairs = [(svc, k) for svc, kinds in _ENRICH.items() for k in kinds]   # tmdb, tvdb
        pairs += [("omdb", "movie"), ("omdb", "show")]                        # ratings (special-cased)
        pairs += [(svc, k) for svc, kinds in _BACKFILL.items() for k in kinds]  # fanart/trakt/…
        pairs += [("ryd", "video"), ("sponsorblock", "video"), ("dearrow", "video")]  # YouTube video stats
        total = 0
        for svc, kind in pairs:
            try:
                total += self.enrichment_retry(svc, kind, scope="failed")
            except Exception:
                logger.exception("retry_all_failed: %s/%s failed", svc, kind)
        return total

    def requeue_shows_for_airtime(self) -> int:
        """One-time backfill: re-queue TVDB enrichment for shows that have a
        tvdb_id but no air time yet, so the worker re-fetches `airsTime`. Only
        touches shows missing the time — idempotent, fills in the background."""
        conn = self._get_connection()
        try:
            cur = conn.execute(
                "UPDATE shows SET tvdb_match_status=NULL, tvdb_last_attempted=NULL "
                "WHERE tvdb_id IS NOT NULL AND (airs_time IS NULL OR airs_time='')")
            conn.commit()
            return cur.rowcount
        finally:
            conn.close()

    @property
    def schema_version(self) -> int:
        conn = self._get_connection()
        try:
            return int(conn.execute("PRAGMA user_version").fetchone()[0])
        finally:
            conn.close()

    # ── video_settings KV (temporary home until the settings.db move) ────────
    def get_setting(self, key: str, default=None):
        conn = self._get_connection()
        try:
            row = conn.execute(
                "SELECT value FROM video_settings WHERE key = ?", (key,)
            ).fetchone()
            return row["value"] if row is not None else default
        finally:
            conn.close()

    def set_setting(self, key: str, value: str) -> None:
        conn = self._get_connection()
        try:
            conn.execute(
                "INSERT INTO video_settings(key, value, updated_at) "
                "VALUES (?, ?, CURRENT_TIMESTAMP) "
                "ON CONFLICT(key) DO UPDATE SET value = excluded.value, "
                "updated_at = CURRENT_TIMESTAMP",
                (key, value),
            )
            conn.commit()
        finally:
            conn.close()

    def all_video_settings(self, exclude=frozenset()) -> dict:
        """Every video_settings key/value (for the config export). Values are
        stored as strings/JSON-strings; parsed back to objects where possible so
        the export is real JSON, not strings-of-JSON. ``exclude`` drops
        one-time/internal keys that shouldn't migrate."""
        import json as _json
        conn = self._get_connection()
        try:
            rows = conn.execute("SELECT key, value FROM video_settings").fetchall()
        finally:
            conn.close()
        out = {}
        for r in rows:
            k = r["key"]
            if k in exclude:
                continue
            v = r["value"]
            try:
                out[k] = _json.loads(v) if isinstance(v, str) else v
            except (ValueError, TypeError):
                out[k] = v
        return out

    def replace_video_settings(self, settings: dict) -> int:
        """Import: upsert a dict of video settings (config migration). Objects
        are re-serialized to their stored JSON-string form. Returns the count
        written."""
        import json as _json
        if not isinstance(settings, dict):
            return 0
        n = 0
        for k, v in settings.items():
            stored = v if isinstance(v, str) else _json.dumps(v)
            self.set_setting(str(k), stored)
            n += 1
        return n

    # ── video downloads (the grab → transfer pipeline) ────────────────────────
    _DL_FIELDS = ("kind", "title", "release_title", "source", "username", "filename",
                  "size_bytes", "quality_label", "target_dir", "status",
                  "media_id", "media_source", "year", "poster_url",
                  "candidates", "search_ctx", "tried_queries", "tried_files", "attempts",
                  "client_ref",   # torrent/usenet client tracking id
                  "quality_profile_id")   # the profile the grab was judged under (P2)

    def add_video_download(self, rec: dict) -> int:
        """Insert a download row (status defaults to 'downloading'); returns its id."""
        rec = rec or {}
        cols = [f for f in self._DL_FIELDS if f in rec]
        conn = self._get_connection()
        try:
            cur = conn.execute(
                "INSERT INTO video_downloads (" + ", ".join(cols) + ") VALUES (" +
                ", ".join("?" for _ in cols) + ")",
                tuple(rec[c] for c in cols),
            )
            conn.commit()
            return int(cur.lastrowid)
        finally:
            conn.close()

    def list_video_downloads(self, limit: int = 100) -> list:
        conn = self._get_connection()
        try:
            rows = conn.execute(
                "SELECT * FROM video_downloads ORDER BY "
                "CASE status WHEN 'downloading' THEN 0 WHEN 'queued' THEN 1 ELSE 2 END, "
                "id DESC LIMIT ?", (int(limit),)
            ).fetchall()
            return [dict(r) for r in rows]
        finally:
            conn.close()

    def get_video_download(self, dl_id: int) -> dict | None:
        conn = self._get_connection()
        try:
            row = conn.execute("SELECT * FROM video_downloads WHERE id = ?", (int(dl_id),)).fetchone()
            return dict(row) if row else None
        finally:
            conn.close()

    def get_active_video_downloads(self) -> list:
        conn = self._get_connection()
        try:
            rows = conn.execute(
                "SELECT * FROM video_downloads WHERE status IN "
                "('queued', 'downloading', 'importing', 'searching') ORDER BY id"
            ).fetchall()
            return [dict(r) for r in rows]
        finally:
            conn.close()

    def count_active_youtube_downloads(self) -> int:
        """How many YouTube downloads are ACTIVELY in flight right now (fetching OR importing,
        not queued) — the concurrency gauge the wishlist pump checks before starting more.
        Counting 'importing' too keeps the worker's slot held while it moves into the library."""
        conn = self._get_connection()
        try:
            r = conn.execute("SELECT COUNT(*) AS n FROM video_downloads "
                             "WHERE source='youtube' AND status IN ('downloading', 'importing')").fetchone()
            return int(r["n"]) if r else 0
        finally:
            conn.close()

    def claim_next_youtube_queued(self) -> dict | None:
        """Atomically take the oldest QUEUED YouTube download and flip it to
        'downloading', returning the row (or None if none are queued). The
        ``WHERE status='queued'`` guard makes the claim race-safe — two workers finishing
        at once can't grab the same row."""
        conn = self._get_connection()
        try:
            row = conn.execute(
                "SELECT * FROM video_downloads WHERE source='youtube' AND status='queued' "
                "ORDER BY id LIMIT 1").fetchone()
            if not row:
                return None
            cur = conn.execute(
                "UPDATE video_downloads SET status='downloading', updated_at=datetime('now') "
                "WHERE id=? AND status='queued'", (row["id"],))
            conn.commit()
            return dict(row) if cur.rowcount else None   # rowcount 0 = lost the race
        finally:
            conn.close()

    def downloaded_youtube_video_ids(self) -> list:
        """YouTube video ids already successfully downloaded (from the permanent history).
        The watchlist scans exclude these — a completed video is removed from the wishlist
        on download, so without this it would be re-added (it's still in the channel's recent
        uploads / last-N net) and re-downloaded on every scan."""
        conn = self._get_connection()
        try:
            return [r["media_id"] for r in conn.execute(
                "SELECT DISTINCT media_id FROM video_download_history "
                "WHERE source='youtube' AND outcome='completed' AND media_id IS NOT NULL")]
        finally:
            conn.close()

    def owned_youtube_video_ids(self) -> list:
        """YouTube video ids whose file is (believed) still ON DISK — pruned rows excluded.
        The UI-annotation variant of the above: a 'Downloaded' badge means *you have this
        now*, while the scan dedup deliberately keeps pruned rows (had-it-once must never
        re-download)."""
        conn = self._get_connection()
        try:
            return [r["media_id"] for r in conn.execute(
                "SELECT DISTINCT media_id FROM video_download_history "
                "WHERE source='youtube' AND outcome='completed' AND media_id IS NOT NULL "
                "AND pruned_at IS NULL")]
        finally:
            conn.close()

    def youtube_ledger_rows(self) -> list:
        """Every still-on-disk (not pruned) completed YouTube grab with a recorded file
        path — the set the ghost-cleanup repair job path-checks. Rows without a
        dest_path can't be verified and are left out."""
        conn = self._get_connection()
        try:
            return [dict(r) for r in conn.execute(
                "SELECT id, media_id, title, channel_id, dest_path, published_at, "
                "completed_at FROM video_download_history "
                "WHERE source='youtube' AND outcome='completed' AND pruned_at IS NULL "
                "AND media_id IS NOT NULL "
                "AND dest_path IS NOT NULL AND dest_path != '' "
                "ORDER BY channel_id, published_at DESC")]
        finally:
            conn.close()

    def youtube_failed_counts(self) -> dict:
        """{video_id: failed-attempt count} from the permanent history — so the processor
        can stop re-grabbing a video that keeps failing (deleted / private / geo- or
        age-gated) instead of retrying it every run forever."""
        conn = self._get_connection()
        try:
            return {r["media_id"]: r["n"] for r in conn.execute(
                "SELECT media_id, COUNT(*) AS n FROM video_download_history "
                "WHERE source='youtube' AND outcome='failed' AND media_id IS NOT NULL "
                "GROUP BY media_id")}
        finally:
            conn.close()

    def youtube_video_detail(self, youtube_id) -> dict | None:
        """Cached metadata for one YouTube video (title / thumbnail / duration / views) — the
        extra detail the download drawer shows. None if it was never cached by a channel scan."""
        conn = self._get_connection()
        try:
            r = conn.execute(
                "SELECT channel_id, title, thumbnail_url, duration, view_count "
                "FROM youtube_channel_videos WHERE youtube_id=? LIMIT 1", (youtube_id,)).fetchone()
            return dict(r) if r else None
        finally:
            conn.close()

    # ── YouTube retention (auto-clean old channel episodes) ──────────────────────
    def youtube_channels_with_downloads(self) -> list:
        """Channel ids that have at least one still-on-disk (not pruned) downloaded episode —
        the set the retention automation iterates."""
        conn = self._get_connection()
        try:
            return [r["channel_id"] for r in conn.execute(
                "SELECT DISTINCT channel_id FROM video_download_history "
                "WHERE source='youtube' AND outcome='completed' AND channel_id IS NOT NULL "
                "AND pruned_at IS NULL")]
        finally:
            conn.close()

    def youtube_channel_episodes(self, channel_id) -> list:
        """A channel's downloaded, still-on-disk episodes (newest upload first) for retention —
        each carries the history id, file path, upload date + filename (date fallback)."""
        if not channel_id:
            return []
        conn = self._get_connection()
        try:
            return [dict(r) for r in conn.execute(
                "SELECT id, media_id, title, dest_path, filename, published_at, completed_at, size_bytes "
                "FROM video_download_history WHERE source='youtube' AND outcome='completed' "
                "AND channel_id=? AND pruned_at IS NULL ORDER BY published_at DESC, completed_at DESC",
                (str(channel_id),))]
        finally:
            conn.close()

    def mark_download_pruned(self, history_id, when) -> bool:
        """Flag a history row as retention-pruned (file deleted) — kept so the scan's dedup
        still excludes it (no re-download). Returns True if a row was updated."""
        conn = self._get_connection()
        try:
            cur = conn.execute("UPDATE video_download_history SET pruned_at=? "
                               "WHERE id=? AND pruned_at IS NULL", (when, int(history_id)))
            conn.commit()
            return cur.rowcount > 0
        except (sqlite3.Error, TypeError, ValueError):
            return False
        finally:
            conn.close()

    def media_tmdb_id(self, kind: str, media_id) -> tuple:
        """(tmdb_id, imdb_id) for a library movie/show row — used to resolve sidecar /
        subtitle metadata for an owned re-grab (whose media_id is the library id, not a
        TMDB id). (None, None) if the row is gone."""
        table = "movies" if str(kind or "").lower() == "movie" else "shows"
        conn = self._get_connection()
        try:
            row = conn.execute(
                "SELECT tmdb_id, imdb_id FROM %s WHERE id = ?" % table, (media_id,)
            ).fetchone()
            return (row["tmdb_id"], row["imdb_id"]) if row else (None, None)
        finally:
            conn.close()

    def get_import_failed_video_downloads(self) -> list:
        """Downloads that finished but couldn't be auto-placed (sample / wrong episode /
        not-an-upgrade / corrupt / pack / parse fail). Their file is still on disk at
        ``dest_path`` — the Import page surfaces these for manual placement."""
        conn = self._get_connection()
        try:
            rows = conn.execute(
                "SELECT * FROM video_downloads WHERE status = 'import_failed' ORDER BY id DESC"
            ).fetchall()
            return [dict(r) for r in rows]
        finally:
            conn.close()

    def update_video_download(self, dl_id: int, **fields) -> None:
        """Patch a download row; ``updated_at`` is always bumped. Underscore-
        prefixed keys are transient patch metadata (e.g. ``_upgraded`` for the
        event bus), not columns — stripped here so producers can ride the patch."""
        fields = {k: v for k, v in fields.items() if not k.startswith("_")}
        if not fields:
            return
        keys = list(fields.keys())
        sets = ", ".join(k + " = ?" for k in keys) + ", updated_at = datetime('now')"
        conn = self._get_connection()
        try:
            conn.execute("UPDATE video_downloads SET " + sets + " WHERE id = ?",
                         tuple(fields[k] for k in keys) + (int(dl_id),))
            conn.commit()
        finally:
            conn.close()

    def torrents_awaiting_seed_release(self, limit=100) -> list:
        """Completed TORRENT grabs the seeding sweep still manages: imported,
        client ref known, not yet released."""
        conn = self._get_connection()
        try:
            return [dict(r) for r in conn.execute(
                "SELECT * FROM video_downloads WHERE status='completed' AND source='torrent' "
                "AND client_ref IS NOT NULL AND client_ref <> '' "
                "AND COALESCE(seed_released, 0) = 0 ORDER BY id LIMIT ?",
                (max(1, int(limit)),))]
        except sqlite3.Error:
            logger.exception("torrents_awaiting_seed_release failed")
            return []
        finally:
            conn.close()

    def title_download_history(self, kind: str, *, library_id=None, tmdb_id=None,
                               limit=50) -> list:
        """This TITLE's permanent acquisition history (arr-parity P9): every
        grab/import/upgrade/failure archived for it, newest first. A title can
        appear under BOTH identities (grabbed from a TMDB preview page before
        it was owned, re-grabbed from the library page after), so both are
        matched."""
        idents = []
        if library_id:
            idents.append(("library", str(library_id)))
        if tmdb_id:
            idents.append(("tmdb", str(tmdb_id)))
        if not idents:
            return []
        where = " OR ".join("(media_source=? AND media_id=?)" for _ in idents)
        args = [v for pair in idents for v in pair]
        conn = self._get_connection()
        try:
            return [dict(r) for r in conn.execute(
                "SELECT * FROM video_download_history WHERE kind IN (?, 'episode') AND (" + where + ") "
                "ORDER BY id DESC LIMIT ?",
                [kind] + args + [max(1, int(limit))])]
        except sqlite3.Error:
            logger.exception("title_download_history failed")
            return []
        finally:
            conn.close()

    def clear_finished_video_downloads(self) -> int:
        conn = self._get_connection()
        try:
            cur = conn.execute("DELETE FROM video_downloads WHERE status IN ('completed', 'failed', 'cancelled', 'import_failed')")
            conn.commit()
            return cur.rowcount
        finally:
            conn.close()

    # ── download history (permanent archive; survives the queue cleanup) ───────
    @staticmethod
    def _parse_resolution(*texts) -> str | None:
        """Best-effort resolution/codec sniff from a release/file name."""
        import re
        blob = " ".join(t for t in texts if t)
        for pat, label in ((r"\b2160p\b|\b4k\b|\buhd\b", "2160p"), (r"\b1080p\b", "1080p"),
                           (r"\b720p\b", "720p"), (r"\b480p\b", "480p")):
            if re.search(pat, blob, re.I):
                return label
        return None

    @staticmethod
    def _codec(*texts) -> str | None:
        import re
        blob = " ".join(t for t in texts if t)
        for pat, label in ((r"\b[xh]?265\b|\bhevc\b", "x265"), (r"\b[xh]?264\b|\bavc\b", "x264"),
                           (r"\bav1\b", "AV1"), (r"\bxvid\b", "XviD")):
            if re.search(pat, blob, re.I):
                return label
        return None

    def record_download_history(self, row: dict) -> int:
        """Snapshot a finished download (the merged final ``video_downloads`` record)
        into the permanent history. ``outcome`` is derived from its status. Idempotent
        per (download_id, outcome, dest_path) so a re-persist / restart never dupes.
        Returns the new history id, or 0 if it was a duplicate / not worth recording."""
        if not row:
            return 0
        status = row.get("status") or "completed"
        outcome = {"completed": "completed", "import_failed": "import_failed",
                   "failed": "failed", "cancelled": "cancelled"}.get(status, status)
        kind = row.get("kind") or "movie"
        media_type = "show" if kind == "show" else ("movie" if kind == "movie" else kind)
        # season/episode + (youtube) channel/upload-date live in the search_ctx JSON.
        sn = en = channel_id = published_at = None
        ctx = row.get("search_ctx")
        if ctx:
            try:
                ctx = json.loads(ctx) if isinstance(ctx, str) else (ctx or {})
                sn, en = ctx.get("season"), ctx.get("episode")
                channel_id, published_at = ctx.get("channel_id"), ctx.get("published_at")
            except (ValueError, TypeError):
                pass
        rel, fn = row.get("release_title"), row.get("filename")
        conn = self._get_connection()
        try:
            cur = conn.execute(
                """INSERT OR IGNORE INTO video_download_history
                       (download_id, kind, media_type, title, year, season_number, episode_number,
                        release_title, source, username, filename, dest_path, size_bytes,
                        quality_label, resolution, video_codec, media_id, media_source, poster_url,
                        outcome, error, grabbed_at, completed_at, channel_id, published_at)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (row.get("id"), kind, media_type, row.get("title"), row.get("year"), sn, en,
                 rel, row.get("source"), row.get("username"), fn, row.get("dest_path"),
                 int(row.get("size_bytes") or 0), row.get("quality_label"),
                 self._parse_resolution(rel, fn, row.get("quality_label")), self._codec(rel, fn),
                 row.get("media_id"), row.get("media_source"), row.get("poster_url"),
                 outcome, row.get("error"), row.get("created_at"),
                 row.get("completed_at"), channel_id, published_at))
            # Opportunistic LOG trim (the user-visible diary keeps a rolling year;
            # the YouTube ownership ledger is exempt and lives forever).
            conn.execute(
                f"DELETE FROM video_download_history WHERE NOT {self._YT_LEDGER} "
                "AND COALESCE(completed_at, grabbed_at) < datetime('now', '-365 days')")
            conn.commit()
            return cur.lastrowid or 0
        except Exception:
            logger.exception("record_download_history failed for download %s", row.get("id"))
            conn.rollback()
            return 0
        finally:
            conn.close()

    def query_download_history(self, *, kind=None, search=None, outcome=None,
                               root_folder_id=None, page=1, limit=40) -> dict:
        """Paged history slice for the modal. ``kind`` ∈ movie|show (None=all);
        ``outcome`` filters by result. ``root_folder_id`` scopes to one configured
        Library (its ``dest_path`` prefix — the only place a placed download
        remembers where it landed; history rows predate root_folder_id and were
        never tied to a specific Library the way movies/shows rows are). Newest
        first. {items, pagination}."""
        try:
            page = max(1, int(page or 1)); limit = max(1, min(200, int(limit or 40)))
        except (TypeError, ValueError):
            page, limit = 1, 40
        # Cleared ledger rows are hidden from the modal (the facts live on).
        where, args = ["cleared_at IS NULL"], []
        # Tabs classify by kind+source, not raw kind: episodes store kind='episode'
        # (not 'show'), and YouTube grabs carry source='youtube'. So 'TV' = anything
        # that isn't a movie or a YouTube grab; 'youtube' = source/kind youtube.
        _yt = "(COALESCE(source,'') = 'youtube' OR kind = 'youtube')"
        if kind == "movie":
            where.append("kind = 'movie' AND NOT " + _yt)
        elif kind in ("show", "tv"):
            where.append("kind <> 'movie' AND NOT " + _yt)
        elif kind == "youtube":
            where.append(_yt)
        if outcome:
            where.append("outcome = ?"); args.append(outcome)
        if root_folder_id:
            clause = _history_library_clause(self.get_root_folder(root_folder_id))
            where.append(clause[0])
            args += clause[1]
        s = (search or "").strip()
        if s:
            where.append("(title LIKE ? OR release_title LIKE ?) COLLATE NOCASE")
            args += ["%" + s + "%", "%" + s + "%"]
        wsql = " WHERE " + " AND ".join(where)
        conn = self._get_connection()
        try:
            total = conn.execute("SELECT COUNT(*) c FROM video_download_history" + wsql, args).fetchone()["c"]
            rows = conn.execute(
                "SELECT * FROM video_download_history" + wsql +
                " ORDER BY COALESCE(completed_at, created_at) DESC, id DESC LIMIT ? OFFSET ?",
                args + [limit, (page - 1) * limit]).fetchall()
            items = [dict(r) for r in rows]
        finally:
            conn.close()
        total_pages = max(1, (total + limit - 1) // limit)
        return {"items": items, "pagination": {
            "page": min(page, total_pages), "total_pages": total_pages, "total_count": total,
            "has_prev": page > 1, "has_next": page < total_pages}}

    def download_history_detail(self, history_id: int) -> dict | None:
        conn = self._get_connection()
        try:
            row = conn.execute("SELECT * FROM video_download_history WHERE id=?", (int(history_id),)).fetchone()
            return dict(row) if row else None
        finally:
            conn.close()

    def download_history_counts(self) -> dict:
        """{movie, show(=TV: episodes + show/season packs), youtube, total,
        by_library: {<root_folder_id>: n}} of completed grabs (for the modal
        tabs/badges). Classifies by kind+source so episodes (kind='episode')
        land under TV and YouTube (source='youtube') gets its own bucket —
        the old version counted only kind='movie'/'show', so TV + YouTube
        vanished. ``by_library`` uses the same dest_path prefix rule the
        per-Library filter does (see ``_history_library_clause``)."""
        conn = self._get_connection()
        try:
            _yt = "(COALESCE(source,'') = 'youtube' OR kind = 'youtube')"
            _base = "WHERE outcome='completed' AND cleared_at IS NULL"
            row = conn.execute(
                "SELECT "
                "SUM(CASE WHEN " + _yt + " THEN 1 ELSE 0 END) AS yt, "
                "SUM(CASE WHEN kind = 'movie' AND NOT " + _yt + " THEN 1 ELSE 0 END) AS mv, "
                "SUM(CASE WHEN kind <> 'movie' AND NOT " + _yt + " THEN 1 ELSE 0 END) AS tv, "
                "COUNT(*) AS total FROM video_download_history " + _base).fetchone()
            by_library = {}
            for r in self.all_library_rows():
                sql, args = _history_library_clause(r)
                by_library[r["id"]] = conn.execute(
                    "SELECT COUNT(*) c FROM video_download_history " + _base + " AND " + sql,
                    args).fetchone()["c"]
            return {"movie": row["mv"] or 0, "show": row["tv"] or 0,
                    "youtube": row["yt"] or 0, "total": row["total"] or 0,
                    "by_library": by_library}
        finally:
            conn.close()

    # ── release blocklist (never re-grab a proven-bad release) ───────────────
    def add_video_blocklist(self, row: dict) -> int:
        """Block one exact release file. Idempotent on (username, filename).
        Returns the row id (existing or new), 0 on bad input."""
        username, filename = (row or {}).get("username"), (row or {}).get("filename")
        if not username or not filename:
            return 0
        conn = self._get_connection()
        try:
            conn.execute(
                """INSERT INTO video_blocklist
                       (kind, title, media_id, media_source, season_number, episode_number,
                        username, filename, release_title, reason)
                   VALUES (?,?,?,?,?,?,?,?,?,?)
                   ON CONFLICT(username, filename) DO UPDATE SET
                       reason=COALESCE(excluded.reason, video_blocklist.reason)""",
                (row.get("kind"), row.get("title"), row.get("media_id"),
                 row.get("media_source"), row.get("season_number"), row.get("episode_number"),
                 username, filename, row.get("release_title"), row.get("reason")))
            conn.commit()
            r = conn.execute("SELECT id FROM video_blocklist WHERE username=? AND filename=?",
                             (username, filename)).fetchone()
            return r["id"] if r else 0
        except sqlite3.Error:
            logger.exception("add_video_blocklist failed")
            return 0
        finally:
            conn.close()

    def video_blocklist_pairs(self) -> set:
        """{(username, filename)} for per-release candidate filtering — cheap enough to
        read per search/retry pass (the table stays small). Excludes the source-wide
        ('' filename) sentinels — those are matched by ``blocked_usernames`` instead."""
        conn = self._get_connection()
        try:
            return {(r["username"], r["filename"]) for r in conn.execute(
                "SELECT username, filename FROM video_blocklist WHERE COALESCE(filename,'') <> ''")}
        finally:
            conn.close()

    def block_video_source(self, username, reason=None) -> int:
        """Block a whole uploader/peer: every release from this username is skipped by
        future searches (a SOURCE-wide block, stored with the '' filename sentinel so it
        dedupes on (username,'')). Returns the row id, 0 on an empty username."""
        username = (username or "").strip()
        if not username:
            return 0
        conn = self._get_connection()
        try:
            conn.execute(
                "INSERT INTO video_blocklist (username, filename, reason) VALUES (?, '', ?) "
                "ON CONFLICT(username, filename) DO UPDATE SET "
                "reason=COALESCE(excluded.reason, video_blocklist.reason)",
                (username, reason or "Uploader blocked"))
            conn.commit()
            r = conn.execute("SELECT id FROM video_blocklist WHERE username=? AND filename=''",
                             (username,)).fetchone()
            return r["id"] if r else 0
        except sqlite3.Error:
            logger.exception("block_video_source failed")
            return 0
        finally:
            conn.close()

    def blocked_usernames(self) -> set:
        """Uploaders blocked source-wide (the '' filename sentinel) — every release from
        them is filtered out of search, on top of the per-release (username,filename)
        blocks. Read per search pass alongside ``video_blocklist_pairs``."""
        conn = self._get_connection()
        try:
            return {r["username"] for r in conn.execute(
                "SELECT username FROM video_blocklist WHERE COALESCE(filename,'') = '' AND username IS NOT NULL")}
        finally:
            conn.close()

    def list_video_blocklist(self) -> list:
        conn = self._get_connection()
        try:
            return [dict(r) for r in conn.execute(
                "SELECT * FROM video_blocklist ORDER BY created_at DESC, id DESC")]
        finally:
            conn.close()

    def remove_video_blocklist(self, row_id) -> bool:
        conn = self._get_connection()
        try:
            cur = conn.execute("DELETE FROM video_blocklist WHERE id=?", (int(row_id),))
            conn.commit()
            return cur.rowcount > 0
        except (sqlite3.Error, TypeError, ValueError):
            return False
        finally:
            conn.close()

    def clear_video_blocklist(self) -> int:
        conn = self._get_connection()
        try:
            cur = conn.execute("DELETE FROM video_blocklist")
            conn.commit()
            return cur.rowcount
        finally:
            conn.close()

    def delete_download_history(self, history_id) -> bool:
        """Forget one history grab — so the scans stop deduping against it and re-grab it
        (the 'Re-download' action after you've deleted the file). Returns True if removed."""
        conn = self._get_connection()
        try:
            cur = conn.execute("DELETE FROM video_download_history WHERE id=?", (int(history_id),))
            conn.commit()
            return cur.rowcount > 0
        except (sqlite3.Error, TypeError, ValueError):
            return False
        finally:
            conn.close()

    # The YouTube OWNERSHIP LEDGER: completed grabs (incl. retention-pruned ones).
    # Scan dedup, retention, the Channels tab, the downloaded badges and the
    # ghost-cleanup repair job all depend on these rows never disappearing.
    # Dedup reads the whole ledger; UI truth (badges/counts) excludes pruned rows.
    _YT_LEDGER = "(source='youtube' AND outcome='completed')"

    def clear_download_history(self, kind=None) -> int:
        """The user-facing 'clear': the LOG deletes, the LEDGER only hides.
        YouTube completed rows are stamped ``cleared_at`` — they leave the
        History modal but keep every ownership fact (the user can clear all
        they want; the real history survives). Everything else — failures,
        cancellations, movie/TV rows (re-derivable from the server scan) —
        truly deletes. The per-row delete_download_history stays a REAL forget:
        that's the deliberate 'Re-download' action. Returns rows affected."""
        conn = self._get_connection()
        try:
            kf, args = ("", []) if kind not in ("movie", "show", "youtube") else \
                (" AND kind=?", [kind])
            hid = conn.execute(
                f"UPDATE video_download_history SET cleared_at=datetime('now') "
                f"WHERE {self._YT_LEDGER} AND cleared_at IS NULL{kf}", args).rowcount
            gone = conn.execute(
                f"DELETE FROM video_download_history WHERE NOT {self._YT_LEDGER}{kf}",
                args).rowcount
            conn.commit()
            return hid + gone
        finally:
            conn.close()

    def latest_completed_download(self, media_type: str = "all") -> dict | None:
        """The most recently completed grab of a type — the probe target for the smart
        post-download scan. ``media_type`` ∈ movie|show|all."""
        where, args = ["outcome = 'completed'"], []
        if media_type in ("movie", "show"):
            where.append("kind = ?"); args.append(media_type)
        conn = self._get_connection()
        try:
            row = conn.execute(
                "SELECT * FROM video_download_history WHERE " + " AND ".join(where) +
                " ORDER BY COALESCE(completed_at, created_at) DESC, id DESC LIMIT 1", args).fetchone()
            return dict(row) if row else None
        finally:
            conn.close()

    # ── library mapping (which server libraries are Movies / TV) ───────────────
    @staticmethod
    def _parse_library_names(raw) -> list:
        """A stored library-selection value -> list[str]. Tolerates the legacy
        format (a bare library name, pre-multi-library) alongside the current
        JSON-array format, so existing installs upgrade in place."""
        if not raw:
            return []
        import json
        try:
            v = json.loads(raw)
        except (ValueError, TypeError):
            return [raw]   # legacy plain string, e.g. "Movies"
        if isinstance(v, list):
            return [str(x) for x in v if x]
        if isinstance(v, str):
            return [v] if v else []
        return [raw]

    @staticmethod
    def _normalize_library_names(names) -> list:
        if names is None:
            return []
        if isinstance(names, str):
            names = [names] if names else []
        return [str(n).strip() for n in names if str(n).strip()]

    # root_folders doubles as the "Libraries" registry: each row is one server
    # library — its section title, an optional display label/rename, and the
    # download destination path new grabs for it land in (see schema.sql).
    _LIB_KIND = {"movies": "movie", "tv": "show", "youtube": "youtube"}
    _LEGACY_PATH_KEY = {"movies": "movies_path", "tv": "tv_path", "youtube": "youtube_path"}

    def _migrate_legacy_libraries(self, conn, server: str) -> None:
        """One-time backfill: if ``root_folders`` has no rows yet for this
        server, create them from the legacy KV selection (Part-1's
        JSON-array-of-titles, or the even older pre-multi-library plain
        string) + the legacy flat movies_path/tv_path, so existing installs
        upgrade without reconfiguring. The first title of each kind keeps the
        exact legacy path (so scanning/downloads behave identically
        post-upgrade); any additional titles (a Part-1 user who had already
        picked >1 library) get a distinct best-guess subfolder the user can
        edit in the new Libraries editor."""
        existing = conn.execute(
            "SELECT COUNT(*) FROM root_folders WHERE server=?", (server,)).fetchone()[0]
        if existing:
            return
        for ui_kind, content_kind in self._LIB_KIND.items():
            titles = self._parse_library_names(self.get_setting(server + "." + ui_kind + "_library"))
            if not titles:
                continue
            legacy_path = (self.get_setting(self._LEGACY_PATH_KEY[ui_kind]) or "").strip()
            for i, title in enumerate(titles):
                if i == 0:
                    path = legacy_path
                else:
                    path = (legacy_path.rstrip("/\\") + "/" + title) if legacy_path else ""
                conn.execute(
                    "INSERT INTO root_folders (path, content_kind, server, server_title, label, sort_order) "
                    "VALUES (?, ?, ?, ?, NULL, ?)",
                    (path, content_kind, server, title, i))
        conn.commit()

    def list_libraries(self, server: str) -> dict:
        """Every configured library for this server, full detail (for the
        Settings editor + the Library page's tab bar).
        {"movies": [...], "tv": [...]} — each entry:
        {id, server_title, label, path, sort_order, category, preferred_indexer_ids,
        default_series_type}."""
        conn = self._get_connection()
        try:
            self._migrate_legacy_libraries(conn, server)
            out = {}
            for ui_kind, content_kind in self._LIB_KIND.items():
                rows = conn.execute(
                    "SELECT id, server_title, label, path, sort_order, category, preferred_indexer_ids, default_series_type "
                    "FROM root_folders WHERE server=? AND content_kind=? ORDER BY sort_order, id",
                    (server, content_kind)).fetchall()
                out[ui_kind] = [dict(r) for r in rows]
            return out
        finally:
            conn.close()

    def get_library_selection(self, server: str) -> dict:
        """Just the section titles to scan, {"movies": [...], "tv": [...]} —
        the contract core/video/sources.py's scan-scope filtering relies on."""
        libs = self.list_libraries(server)
        return {k: [r["server_title"] for r in v if r.get("server_title")] for k, v in libs.items()}

    def save_libraries(self, server: str, movies, tv, youtube=None) -> dict:
        """Replace this server's Libraries with the given lists (each entry:
        {id?, server_title, label, path, category, preferred_indexer_ids}).
        Missing ids are deleted (their movies/shows rows fall back to
        unassigned); others are upserted in list order, which becomes
        sort_order (index 0 = primary). Passing None for a kind leaves that
        kind's rows untouched — only an explicitly supplied list prunes, so a
        caller that manages movies+tv can't delete the YouTube roots it never
        knew about. Returns the new list_libraries(server)
        shape. ``youtube`` entries are manually named (no server-side
        discovery — YouTube isn't scanned from a Plex/Jellyfin section), but
        stored the same way as movies/tv. ``category`` is the torrent-client
        category/label new grabs into this library carry; blank/omitted
        inherits the global torrent_client.category. ``preferred_indexer_ids``
        is a comma-separated list of Prowlarr indexer ids (same format as the
        global prowlarr.indexer_ids allowlist) that rank higher for titles in
        this library; blank/omitted means no preference."""
        entries_by_kind = {"movies": movies, "tv": tv, "youtube": youtube}
        conn = self._get_connection()
        try:
            for ui_kind, content_kind in self._LIB_KIND.items():
                entries = entries_by_kind.get(ui_kind)
                # None means "this caller didn't manage this kind" — leave its rows
                # alone. Only an explicitly supplied (possibly empty) list may prune.
                # The Libraries settings page posts movies+tv and omits youtube, so
                # folding None into [] here deleted every YouTube root on every save,
                # silently dropping it from health checks, recycle and path re-rooting
                # (all_library_rows is kind-agnostic and does read those rows).
                if entries is None:
                    continue
                keep_ids = set()
                for i, e in enumerate(entries):
                    e = e or {}
                    title = str(e.get("server_title") or "").strip()
                    if not title:
                        continue
                    label = str(e.get("label") or "").strip() or None
                    path = str(e.get("path") or "").strip()
                    category = str(e.get("category") or "").strip() or None
                    preferred_indexer_ids = _norm_indexer_ids(e.get("preferred_indexer_ids"))
                    # Only meaningful for show Libraries; a movie has no series type.
                    dst = str(e.get("default_series_type") or "").strip().lower()
                    if content_kind != "show" or dst not in ("standard", "daily", "anime"):
                        dst = None
                    eid = e.get("id")
                    if eid:
                        conn.execute(
                            "UPDATE root_folders SET server_title=?, label=?, path=?, sort_order=?, category=?, "
                            "preferred_indexer_ids=?, default_series_type=? "
                            "WHERE id=? AND server=? AND content_kind=?",
                            (title, label, path, i, category, preferred_indexer_ids, dst,
                             int(eid), server, content_kind))
                        keep_ids.add(int(eid))
                    else:
                        cur = conn.execute(
                            "INSERT INTO root_folders (path, content_kind, server, server_title, label, "
                            "sort_order, category, preferred_indexer_ids, default_series_type) "
                            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                            (path, content_kind, server, title, label, i, category,
                             preferred_indexer_ids, dst))
                        keep_ids.add(cur.lastrowid)
                existing_ids = {r[0] for r in conn.execute(
                    "SELECT id FROM root_folders WHERE server=? AND content_kind=?",
                    (server, content_kind)).fetchall()}
                for gone in existing_ids - keep_ids:
                    conn.execute("UPDATE movies SET root_folder_id=NULL WHERE root_folder_id=?", (gone,))
                    conn.execute("UPDATE shows SET root_folder_id=NULL WHERE root_folder_id=?", (gone,))
                    conn.execute("DELETE FROM root_folders WHERE id=?", (gone,))
            conn.commit()
        finally:
            conn.close()
        # Apply the (possibly just-changed) Library defaults to shows that have
        # no series type of their own. Doing it on SAVE rather than only on the
        # next scan means setting "Anime" on a Library fixes the shows already
        # in it immediately, which is the whole point of the setting.
        self.apply_library_default_series_type()
        return self.list_libraries(server)

    def root_folder_id_for(self, server: str, kind: str, server_title) -> int | None:
        """The configured library (root_folders row) matching this scanned
        item's server section — for stamping movies/shows.root_folder_id."""
        if not server_title:
            return None
        content_kind = "movie" if kind == "movie" else "show"
        conn = self._get_connection()
        try:
            row = conn.execute(
                "SELECT id FROM root_folders WHERE server=? AND content_kind=? AND server_title=?",
                (server, content_kind, server_title)).fetchone()
            return row["id"] if row else None
        finally:
            conn.close()

    def root_folder_ids_for(self, server: str, kind: str) -> dict:
        """{server_title: root_folder_id} for a whole server+kind, in ONE query —
        the scanner's per-item lookup table (avoids a query per movie/show)."""
        content_kind = "movie" if kind == "movie" else "show"
        conn = self._get_connection()
        try:
            rows = conn.execute(
                "SELECT server_title, id FROM root_folders WHERE server=? AND content_kind=?",
                (server, content_kind)).fetchall()
            return {r["server_title"]: r["id"] for r in rows if r["server_title"]}
        finally:
            conn.close()

    def primary_root_folder(self, server: str, kind: str) -> dict | None:
        """The 'primary' library for a kind (lowest sort_order) — the default
        destination for unattended grabs and the fallback Library-page tab."""
        content_kind = "movie" if kind == "movie" else "show"
        conn = self._get_connection()
        try:
            row = conn.execute(
                "SELECT id, server_title, label, path, category FROM root_folders "
                "WHERE server=? AND content_kind=? ORDER BY sort_order, id LIMIT 1",
                (server, content_kind)).fetchone()
            return dict(row) if row else None
        finally:
            conn.close()

    # Content-kind ordering used by all_library_rows: movies, then shows, then
    # YouTube — matches the legacy movies_path/tv_path/youtube_path order the
    # flat settings used, so callers that union the two keep a stable list.
    _KIND_ORDER = "CASE content_kind WHEN 'movie' THEN 0 WHEN 'show' THEN 1 ELSE 2 END"

    def all_library_rows(self, kind: str | None = None) -> list:
        """Every configured library row, ordered kind then sort_order.

        Server-AGNOSTIC on purpose, unlike list_libraries/primary_root_folder.
        Callers here (health checks, the recycle bin, path re-resolution, the
        naming-conformance job) care about folders on disk, not about which
        media server indexes them — and resolve_video_server() returns None on
        an install with no server configured, which would make a server-scoped
        query silently yield nothing and reintroduce the very bug this exists
        to fix. root_folders.path is globally UNIQUE, so an unscoped query
        cannot double-count.

        Deliberately does NOT run _migrate_legacy_libraries (that needs a
        server): an install that never opened the Libraries editor simply has
        no rows, and its callers fall through to the legacy scalars.
        """
        sql = ("SELECT id, path, content_kind, server, server_title, label, sort_order, category, "
               "preferred_indexer_ids FROM root_folders")
        params = ()
        if kind:
            content_kind = {"movie": "movie", "movies": "movie", "show": "show",
                            "shows": "show", "tv": "show"}.get(str(kind).lower(), str(kind).lower())
            sql += " WHERE content_kind=?"
            params = (content_kind,)
        sql += f" ORDER BY {self._KIND_ORDER}, sort_order, id"

        conn = self._get_connection()
        try:
            return [dict(r) for r in conn.execute(sql, params).fetchall()]
        finally:
            conn.close()

    def all_library_paths(self, kind: str | None = None) -> list:
        """Just the destination paths from all_library_rows, blanks dropped."""
        return [p for p in (str(r.get("path") or "").strip()
                            for r in self.all_library_rows(kind)) if p]

    def get_root_folder(self, root_folder_id) -> dict | None:
        """A single library row by id (for resolving a grab's chosen
        destination). None if unknown."""
        conn = self._get_connection()
        try:
            row = conn.execute(
                "SELECT id, path, content_kind, server, server_title, label, sort_order, category, "
                "preferred_indexer_ids FROM root_folders WHERE id=?", (int(root_folder_id),)).fetchone()
            return dict(row) if row else None
        except (TypeError, ValueError):
            return None
        finally:
            conn.close()

    # ── dashboard ─────────────────────────────────────────────────────────────
    def enrichment_coverage(self) -> dict:
        """How much of the library is matched + detail-enriched against TMDB / TVDB — powers
        the dashboard Studio cards' coverage bars (Overlay + Collection Studio need this
        metadata: posters, ratings, logos, studios/networks). ``tmdb_enriched`` = full detail
        pulled (``details_synced``); ``*_matched`` = an external id resolved. Purely read-only."""
        conn = self._get_connection()
        try:
            def n(sql):
                r = conn.execute(sql).fetchone()
                return int(r[0]) if r and r[0] is not None else 0
            return {
                "movies": {
                    "total": n("SELECT COUNT(*) FROM movies"),
                    "tmdb_matched": n("SELECT COUNT(*) FROM movies WHERE tmdb_id IS NOT NULL"),
                    "tmdb_enriched": n("SELECT COUNT(*) FROM movies WHERE details_synced=1"),
                },
                "shows": {
                    "total": n("SELECT COUNT(*) FROM shows"),
                    "tmdb_matched": n("SELECT COUNT(*) FROM shows WHERE tmdb_id IS NOT NULL"),
                    "tmdb_enriched": n("SELECT COUNT(*) FROM shows WHERE details_synced=1"),
                    "tvdb_matched": n("SELECT COUNT(*) FROM shows WHERE tvdb_id IS NOT NULL"),
                },
            }
        except sqlite3.Error:
            logger.exception("enrichment_coverage failed")
            return {"movies": {}, "shows": {}}
        finally:
            conn.close()

    def recently_added(self, server_source=None, limit=12) -> list:
        """Newest movies + shows for the dashboard's Recently Added row. A SHOW ranks by its
        NEWEST EPISODE's add-date (so a show with a freshly-added episode surfaces — matching
        Plex's own Recently Added), falling back to the show's own add-date. Movies rank by
        their add-date. [{kind, id, title, year, added_at}], newest first."""
        limit = max(1, min(50, int(limit)))
        m_where = "server_id IS NOT NULL AND server_id <> '' AND added_at IS NOT NULL"
        s_where = "s.server_id IS NOT NULL AND s.server_id <> ''"
        if server_source:
            m_where += " AND server_source = ?"
            s_where += " AND s.server_source = ?"
        p = ([server_source] if server_source else [])
        conn = self._get_connection()
        try:
            rows = conn.execute(
                "SELECT kind, id, title, year, added_at FROM ("
                f"  SELECT 'movie' AS kind, id, title, year, added_at FROM movies WHERE {m_where}"
                "   UNION ALL "
                "  SELECT 'show' AS kind, s.id AS id, s.title AS title, s.year AS year, "
                "         COALESCE(MAX(e.added_at), s.added_at) AS added_at "
                "  FROM shows s LEFT JOIN episodes e ON e.show_id = s.id "
                f"  WHERE {s_where} GROUP BY s.id "
                "  HAVING COALESCE(MAX(e.added_at), s.added_at) IS NOT NULL"
                ") ORDER BY added_at DESC, id DESC LIMIT ?",
                p + p + [limit]).fetchall()
            return [dict(r) for r in rows]
        except sqlite3.Error:
            logger.exception("recently_added query failed")
            return []
        finally:
            conn.close()

    def dashboard_stats(self, server_source=None) -> dict:
        """Live counts for the video dashboard, straight from video.db. Library
        counts are scoped to the active video server (``server_source``) so Plex
        and Jellyfin never commingle.

        Shape is stable so the frontend can map it directly; with an empty
        database every number is a real 0 (not a stub).
        """
        # server_source given → scope to that server; None → all servers.
        mw = " WHERE server_source=?" if server_source else ""
        sw = " WHERE s.server_source=?" if server_source else ""
        sv = (server_source,) if server_source else ()
        size_sql = ("SELECT COALESCE(SUM(size_bytes), 0) FROM media_files mf "
                    "WHERE mf.movie_id IN (SELECT id FROM movies WHERE server_source=?) "
                    "OR mf.episode_id IN (SELECT e.id FROM episodes e JOIN shows s ON s.id=e.show_id "
                    "WHERE s.server_source=?)") if server_source else \
                   "SELECT COALESCE(SUM(size_bytes), 0) FROM media_files"
        conn = self._get_connection()
        try:
            def scalar(sql: str, params=()):
                return conn.execute(sql, params).fetchone()[0]

            # Per-Library breakdown — the fixed movies/shows/size totals above sum
            # EVERY configured Library of that kind into one number, so a second
            # Library (an Anime library alongside the standard one, say) had no way
            # to show its own count or its own share of the disk. Every configured
            # movie/show Library gets an entry; the dashboard renders these INSTEAD
            # of the aggregate tiles and only falls back to them when this is empty
            # (no Libraries configured yet).
            lib_rows = [r for r in self.all_library_rows() if r.get("content_kind") in ("movie", "show")]
            movie_libs = [r for r in lib_rows if r["content_kind"] == "movie"]
            show_libs = [r for r in lib_rows if r["content_kind"] == "show"]
            by_library = []
            for r in movie_libs + show_libs:
                is_movie = r["content_kind"] == "movie"
                tbl = "movies" if is_movie else "shows"
                where = "root_folder_id=?" + (" AND server_source=?" if server_source else "")
                params = (r["id"],) + (sv if server_source else ())
                # Movie files hang off the movie row; episode files hang off the
                # show's episodes — one hop further for a show Library.
                if is_movie:
                    size_sub = f"SELECT id FROM movies WHERE {where}"
                    size_q = ("SELECT COALESCE(SUM(size_bytes), 0) FROM media_files "
                              f"WHERE movie_id IN ({size_sub})")
                else:
                    size_sub = (f"SELECT e.id FROM episodes e JOIN shows s ON s.id=e.show_id "
                                f"WHERE s.root_folder_id=?"
                                + (" AND s.server_source=?" if server_source else ""))
                    size_q = ("SELECT COALESCE(SUM(size_bytes), 0) FROM media_files "
                              f"WHERE episode_id IN ({size_sub})")
                by_library.append({
                    "id": r["id"], "kind": r["content_kind"],
                    "label": r.get("label") or r.get("server_title") or "Library",
                    "count": scalar(f"SELECT COUNT(*) FROM {tbl} WHERE {where}", params),
                    "size_bytes": scalar(size_q, params),
                })

            return {
                "library": {
                    "movies": scalar("SELECT COUNT(*) FROM movies" + mw, sv),
                    "shows": scalar("SELECT COUNT(*) FROM shows" + mw, sv),
                    "episodes": scalar(
                        "SELECT COUNT(*) FROM episodes e JOIN shows s ON s.id=e.show_id" + sw, sv),
                    "size_bytes": scalar(size_sql, (sv + sv) if server_source else ()),
                    "by_library": by_library,
                },
                "downloads": {
                    "active": scalar(
                        "SELECT COUNT(*) FROM downloads "
                        "WHERE status IN ('queued','downloading','importing')"),
                    "finished": scalar("SELECT COUNT(*) FROM downloads WHERE status = 'completed'"),
                    "speed_bps": scalar(
                        "SELECT COALESCE(SUM(download_speed_bps), 0) FROM downloads "
                        "WHERE status = 'downloading'"),
                },
                # Curated watchlist (explicit follows + actively-airing library
                # shows), NOT the old monitored-based v_watchlist view.
                "watchlist": self.watchlist_counts(server_source=server_source)["total"],
                # Curated wishlist (movies + wanted episodes), NOT the old
                # v_wishlist view that auto-listed every missing item.
                "wishlist": self.wishlist_counts()["total"],
            }
        finally:
            conn.close()

    # ── scan upserts (server is the source of truth) ──────────────────────────
    # The scanner passes normalized, server-agnostic dicts (a Plex/Jellyfin
    # adapter produces them) so this layer never touches a media-server SDK.
    @staticmethod
    def _set_media_file(conn, owner_col: str, owner_id: int, files) -> None:
        """Replace the media_files row(s) for one owner — EVERY version the
        server reported (multiple copies/editions each get a row). Accepts a
        single file dict (legacy callers) or a list. owner_col is internal
        ('movie_id'|'episode_id'|'video_id'), never user input."""
        conn.execute(f"DELETE FROM media_files WHERE {owner_col} = ?", (owner_id,))
        if not files:
            return
        if isinstance(files, dict):
            files = [files]
        from core.video.mediainfo import canonical_aspect
        for file in files:
            if not file:
                continue
            conn.execute(
                f"INSERT INTO media_files ({owner_col}, relative_path, size_bytes, resolution, "
                "video_codec, audio_codec, release_source, quality, runtime_seconds, aspect, "
                "audio_channels, dynamic_range, atmos) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (owner_id,
                 file.get("relative_path") or file.get("path") or "",
                 file.get("size_bytes"), file.get("resolution"), file.get("video_codec"),
                 file.get("audio_codec"), file.get("release_source"), file.get("quality"),
                 file.get("runtime_seconds"), canonical_aspect(file.get("aspect")),
                 file.get("audio_channels"), file.get("dynamic_range"),
                 1 if file.get("atmos") else 0),
            )

    @staticmethod
    def _parse_locked(raw) -> set:
        """Parse a stored ``locked_fields`` JSON list (None/garbage → empty)."""
        if not raw:
            return set()
        try:
            v = json.loads(raw)
            return {str(f) for f in v} if isinstance(v, list) else set()
        except (ValueError, TypeError):
            return set()

    @classmethod
    def _locked_fields_set(cls, conn, table: str, item_id: int) -> set:
        """The user-locked field names for one movies/shows row (empty when none)."""
        row = conn.execute(f"SELECT locked_fields FROM {table} WHERE id=?", (item_id,)).fetchone()
        return cls._parse_locked(row["locked_fields"]) if row else set()

    @staticmethod
    def _set_genres(conn, link_table: str, owner_col: str, owner_id: int, names) -> None:
        """Replace the genre links for one owner (normalised; dedup names in the
        shared genres table). owner_col/link_table are internal, never user input."""
        conn.execute(f"DELETE FROM {link_table} WHERE {owner_col}=?", (owner_id,))
        for raw in (names or []):
            name = (raw or "").strip()
            if not name:
                continue
            conn.execute("INSERT OR IGNORE INTO genres (name) VALUES (?)", (name,))
            gid = conn.execute("SELECT id FROM genres WHERE name=? COLLATE NOCASE", (name,)).fetchone()["id"]
            conn.execute(f"INSERT OR IGNORE INTO {link_table} ({owner_col}, genre_id) VALUES (?, ?)",
                         (owner_id, gid))

    @staticmethod
    def _set_named_links(conn, link_table: str, owner_col: str, ref_table: str, ref_fk: str,
                         owner_id: int, names) -> None:
        """Replace the studios/networks links for one owner (normalised; dedup names in
        the shared ref table). Same shape as _set_genres, generalised for the multi-valued
        studio/network facets. Table/column names are internal, never user input."""
        conn.execute(f"DELETE FROM {link_table} WHERE {owner_col}=?", (owner_id,))
        seen = set()
        for raw in (names or []):
            name = (raw or "").strip()
            if not name or name.casefold() in seen:
                continue
            seen.add(name.casefold())
            conn.execute(f"INSERT OR IGNORE INTO {ref_table} (name) VALUES (?)", (name,))
            rid = conn.execute(f"SELECT id FROM {ref_table} WHERE name=? COLLATE NOCASE", (name,)).fetchone()["id"]
            conn.execute(f"INSERT OR IGNORE INTO {link_table} ({owner_col}, {ref_fk}) VALUES (?, ?)",
                         (owner_id, rid))

    @staticmethod
    def _rescue_orphan_youtube_library_path(conn) -> None:
        """One-time: the removed "YouTube Libraries" editor wrote root_folders rows
        with content_kind='youtube' that NOTHING ever read back — primary_root_folder
        maps only movie/show, and every YouTube destination reads the youtube_path
        scalar. Anyone who configured a folder there had it silently ignored; if
        youtube_path is still blank, adopt the first such row so their setting
        finally takes effect instead of vanishing with the editor. Marker-guarded."""
        try:
            row = conn.execute(
                "SELECT value FROM video_settings WHERE key='youtube_lib_path_rescued'").fetchone()
            if row and str(row[0]) == '1':
                return
            current = conn.execute(
                "SELECT value FROM video_settings WHERE key='youtube_path'").fetchone()
            if not (current and str(current[0] or "").strip()):
                orphan = conn.execute(
                    "SELECT path FROM root_folders WHERE content_kind='youtube' "
                    "AND path IS NOT NULL AND TRIM(path) != '' ORDER BY sort_order, id "
                    "LIMIT 1").fetchone()
                if orphan:
                    conn.execute("INSERT INTO video_settings(key, value, updated_at) "
                                 "VALUES('youtube_path', ?, CURRENT_TIMESTAMP) "
                                 "ON CONFLICT(key) DO UPDATE SET value=excluded.value, "
                                 "updated_at=CURRENT_TIMESTAMP", (str(orphan[0]),))
                    logger.info("Adopted an orphaned YouTube library folder as youtube_path: %s",
                                orphan[0])
            conn.execute("INSERT INTO video_settings(key, value, updated_at) "
                         "VALUES('youtube_lib_path_rescued', '1', CURRENT_TIMESTAMP) "
                         "ON CONFLICT(key) DO UPDATE SET value='1', updated_at=CURRENT_TIMESTAMP")
        except sqlite3.Error:
            logger.exception("YouTube library path rescue failed (non-fatal)")

    @staticmethod
    def _seed_named_links_from_scalar(conn) -> None:
        """One-time backfill: seed the studios/networks link tables from the LEGACY single
        studio/network columns, so existing collections match IDENTICALLY the moment the
        smart filters switch to the link tables — no regression. Full multi-company data
        then fills in as titles re-enrich (the setter replaces the seeded single value with
        the whole list). Guarded by a marker so it runs exactly once."""
        try:
            row = conn.execute(
                "SELECT value FROM video_settings WHERE key='studio_network_links_seeded'").fetchone()
            if row and str(row[0]) == '1':
                return
            conn.execute("INSERT OR IGNORE INTO studios(name) SELECT DISTINCT studio FROM movies "
                         "WHERE studio IS NOT NULL AND TRIM(studio) != ''")
            conn.execute("INSERT OR IGNORE INTO movie_studios(movie_id, studio_id) "
                         "SELECT m.id, s.id FROM movies m JOIN studios s ON s.name = m.studio COLLATE NOCASE "
                         "WHERE m.studio IS NOT NULL AND TRIM(m.studio) != ''")
            conn.execute("INSERT OR IGNORE INTO networks(name) SELECT DISTINCT network FROM shows "
                         "WHERE network IS NOT NULL AND TRIM(network) != ''")
            conn.execute("INSERT OR IGNORE INTO show_networks(show_id, network_id) "
                         "SELECT sh.id, n.id FROM shows sh JOIN networks n ON n.name = sh.network COLLATE NOCASE "
                         "WHERE sh.network IS NOT NULL AND TRIM(sh.network) != ''")
            conn.execute("INSERT INTO video_settings(key, value, updated_at) "
                         "VALUES('studio_network_links_seeded', '1', CURRENT_TIMESTAMP) "
                         "ON CONFLICT(key) DO UPDATE SET value='1', updated_at=CURRENT_TIMESTAMP")
        except sqlite3.Error:
            logger.exception("studio/network link seed failed (non-fatal)")

    @staticmethod
    def _resilient_upsert(conn, table: str, base: dict, id_cols: dict,
                          preserve_enrichment: bool = True) -> None:
        """INSERT…ON CONFLICT(server_source, server_id) for a movie/show row.

        Resilient to a LEGACY UNIQUE on tmdb_id/tvdb_id/imdb_id (old DBs created
        before those were made non-unique — SQLite can't drop an inline UNIQUE):
        on IntegrityError we retry WITHOUT the id columns, so the row is still
        stored (same film in >1 library) instead of being dropped by the scan.
        ``base`` holds the always-written cols; ``id_cols`` the droppable ids.

        ``preserve_enrichment`` (default) keeps enrichment-owned fields (``status``,
        network, ratings, air dates…) that the SERVER left blank — so an incremental
        or deep re-scan never wipes the TMDB-backfilled ``status`` (which the airing
        watchlist depends on). A FULL scan passes False = a clean overwrite / reset.

        User-locked fields (``locked_fields`` JSON on the row) are kept in EVERY
        mode — a full scan resets enrichment, never a user edit."""
        protect = (_ENRICH_META_COLS.get(table, set()) if preserve_enrichment else set())

        def _set(c):
            # On a conflict UPDATE, an enrichment-owned column only takes the server
            # value when it's non-blank; otherwise it keeps what's already stored.
            take = f"excluded.{c}"
            if c in protect:
                take = f"COALESCE(NULLIF(excluded.{c}, ''), {table}.{c})"
            # instr on the quoted name — '"title"' never matches '"sort_title"'.
            return (f"{c}=CASE WHEN instr(COALESCE({table}.locked_fields, ''), '\"{c}\"') > 0 "
                    f"THEN {table}.{c} ELSE {take} END")

        def run(include_ids):
            cols = list(base.keys()) + (list(id_cols.keys()) if include_ids else [])
            vals = list(base.values()) + (list(id_cols.values()) if include_ids else [])
            updates = [c for c in cols if c not in ("server_source", "server_id")]
            set_clause = ", ".join(_set(c) for c in updates) + ", updated_at=CURRENT_TIMESTAMP"
            sql = (f"INSERT INTO {table} ({', '.join(cols)}, updated_at) "
                   f"VALUES ({', '.join(['?'] * len(cols))}, CURRENT_TIMESTAMP) "
                   f"ON CONFLICT(server_source, server_id) DO UPDATE SET {set_clause}")
            conn.execute(sql, vals)
        try:
            run(True)
        except sqlite3.IntegrityError:
            conn.rollback()                 # legacy UNIQUE on an id — keep the row, drop the id
            run(False)

    @staticmethod
    def _set_credits(conn, owner_col: str, owner_id: int, cast, crew) -> None:
        """Replace the cast+crew for one owner (deduped people in the shared
        people table). owner_col is internal ('movie_id'|'show_id')."""
        conn.execute(f"DELETE FROM credits WHERE {owner_col}=?", (owner_id,))

        def person_id(p):
            tid = p.get("tmdb_id")
            if tid is not None:
                conn.execute("INSERT OR IGNORE INTO people (name, tmdb_id, photo_url) VALUES (?, ?, ?)",
                             (p["name"], tid, p.get("photo_url")))
                row = conn.execute("SELECT id FROM people WHERE tmdb_id=?", (tid,)).fetchone()
            else:
                conn.execute("INSERT INTO people (name, photo_url) VALUES (?, ?)",
                             (p["name"], p.get("photo_url")))
                row = conn.execute("SELECT last_insert_rowid() AS id").fetchone()
            pid = row["id"] if row else None
            if pid and p.get("photo_url"):
                conn.execute("UPDATE people SET photo_url=COALESCE(NULLIF(photo_url, ''), ?) WHERE id=?",
                             (p["photo_url"], pid))
            return pid

        def add(group, department, job_default):
            for i, c in enumerate(group or []):
                if not c.get("name"):
                    continue
                pid = person_id(c)
                if not pid:
                    continue
                conn.execute(
                    f"INSERT INTO credits (person_id, {owner_col}, department, job, character, sort_order) "
                    "VALUES (?, ?, ?, ?, ?, ?)",
                    (pid, owner_id, department, c.get("job") or job_default, c.get("character"), i))
        add(cast, "cast", "Actor")
        add(crew, "crew", None)

    @staticmethod
    def _credits_for(conn, owner_col: str, owner_id: int, cast_limit: int = 18) -> dict:
        rows = conn.execute(
            "SELECT p.name, p.photo_url, p.tmdb_id, c.department, c.job, c.character "
            f"FROM credits c JOIN people p ON p.id = c.person_id WHERE c.{owner_col}=? "
            "ORDER BY c.department, c.sort_order", (owner_id,)).fetchall()
        cast = [{"name": r["name"], "character": r["character"], "photo": r["photo_url"],
                 "tmdb_id": r["tmdb_id"]}
                for r in rows if r["department"] == "cast"][:cast_limit]
        crew = [{"name": r["name"], "job": r["job"], "tmdb_id": r["tmdb_id"]}
                for r in rows if r["department"] == "crew"]
        return {"cast": cast, "crew": crew}

    def upsert_movie(self, server_source: str, item: dict, preserve_enrichment: bool = True) -> int:
        """Insert/update one movie (keyed on server id) and its file. Returns row id.
        ``preserve_enrichment`` keeps enrichment-owned fields the server left blank
        (default); a FULL scan passes False for a clean reset."""
        conn = self._get_connection()
        try:
            self._resilient_upsert(conn, "movies", {
                "server_source": server_source, "server_id": item["server_id"],
                "title": item.get("title"), "sort_title": _sort_title(item.get("title")),
                "year": item.get("year"), "overview": item.get("overview"),
                "runtime_minutes": item.get("runtime_minutes"), "content_rating": item.get("content_rating"),
                "studio": item.get("studio"), "tagline": item.get("tagline"),
                "rating": item.get("rating"), "rating_critic": item.get("rating_critic"),
                "play_count": item.get("play_count"),
                "last_viewed_at": item.get("last_viewed_at"),
                "view_offset_ms": item.get("view_offset_ms"),
                "poster_url": item.get("poster_url"),
                "has_file": 1 if (item.get("files") or item.get("file")) else 0,
                "root_folder_id": item.get("root_folder_id"),
            }, {"tmdb_id": item.get("tmdb_id"), "imdb_id": item.get("imdb_id")},
                preserve_enrichment=preserve_enrichment)
            movie_id = conn.execute(
                "SELECT id FROM movies WHERE server_source=? AND server_id=?",
                (server_source, item["server_id"]),
            ).fetchone()["id"]
            self._set_media_file(conn, "movie_id", movie_id,
                                 item.get("files") or item.get("file"))
            if "genres" not in self._locked_fields_set(conn, "movies", movie_id):
                self._set_genres(conn, "movie_genres", "movie_id", movie_id, item.get("genres"))
            if "studio" not in self._locked_fields_set(conn, "movies", movie_id):
                # ALL production companies (falls back to the single scalar so a scan/enrichment
                # that only has one still links it — keeps parity with the old behaviour).
                studios = item.get("studios") or ([item["studio"]] if item.get("studio") else [])
                self._set_named_links(conn, "movie_studios", "movie_id", "studios", "studio_id",
                                      movie_id, studios)
            conn.commit()
            return movie_id
        finally:
            conn.close()

    def upsert_show_tree(self, server_source: str, item: dict, preserve_enrichment: bool = True) -> int:
        """Insert/update a show with its seasons + episodes (and files) in one
        transaction. Episodes/seasons no longer present on the server for this
        show are pruned. Returns the show row id. ``preserve_enrichment`` keeps
        enrichment-owned fields (status etc.) the server left blank (default); a FULL
        scan passes False for a clean reset."""
        conn = self._get_connection()
        try:
            # musicagine's rename report: some servers (Jellyfin/Emby show
            # metadata especially) report a PremiereDate but no ProductionYear,
            # leaving shows.year NULL — movies carry year fine, so $year worked
            # for films and vanished for series. The premiere year IS the show
            # year, so derive it when the server omits it.
            _year = item.get("year")
            if _year is None:
                _fad = str(item.get("first_air_date") or "")[:4]
                _year = int(_fad) if _fad.isdigit() else None
            self._resilient_upsert(conn, "shows", {
                "server_source": server_source, "server_id": item["server_id"],
                "title": item.get("title"), "sort_title": _sort_title(item.get("title")),
                "year": _year, "overview": item.get("overview"),
                "status": item.get("status"), "network": item.get("network"),
                "runtime_minutes": item.get("runtime_minutes"), "content_rating": item.get("content_rating"),
                "tagline": item.get("tagline"), "rating": item.get("rating"),
                "first_air_date": item.get("first_air_date"), "last_air_date": item.get("last_air_date"),
                "watched_episodes": item.get("watched_episodes"),
                "poster_url": item.get("poster_url"),
                "root_folder_id": item.get("root_folder_id"),
            }, {"tvdb_id": item.get("tvdb_id"), "tmdb_id": item.get("tmdb_id"), "imdb_id": item.get("imdb_id")},
                preserve_enrichment=preserve_enrichment)
            show_id = conn.execute(
                "SELECT id FROM shows WHERE server_source=? AND server_id=?",
                (server_source, item["server_id"]),
            ).fetchone()["id"]
            if "genres" not in self._locked_fields_set(conn, "shows", show_id):
                self._set_genres(conn, "show_genres", "show_id", show_id, item.get("genres"))
            if "network" not in self._locked_fields_set(conn, "shows", show_id):
                networks = item.get("networks") or ([item["network"]] if item.get("network") else [])
                self._set_named_links(conn, "show_networks", "show_id", "networks", "network_id",
                                      show_id, networks)

            seen_seasons: set[int] = set()
            seen_eps: set[tuple[int, int]] = set()
            for season in item.get("seasons", []):
                snum = season["season_number"]
                seen_seasons.add(snum)
                conn.execute(
                    "INSERT INTO seasons (show_id, server_id, season_number, title, overview, poster_url) "
                    "VALUES (?, ?, ?, ?, ?, ?) "
                    "ON CONFLICT(show_id, season_number) DO UPDATE SET "
                    "server_id=excluded.server_id, title=excluded.title, "
                    "overview=excluded.overview, poster_url=excluded.poster_url",
                    (show_id, season.get("server_id"), snum, season.get("title"),
                     season.get("overview"), season.get("poster_url")),
                )
                season_id = conn.execute(
                    "SELECT id FROM seasons WHERE show_id=? AND season_number=?", (show_id, snum)
                ).fetchone()["id"]

                for ep in season.get("episodes", []):
                    enum = ep.get("episode_number")
                    if enum is None or snum is None:
                        continue  # can't key an episode without season+episode numbers
                    seen_eps.add((snum, enum))
                    conn.execute(
                        "INSERT INTO episodes (show_id, season_id, server_source, server_id, "
                        "season_number, episode_number, title, overview, air_date, "
                        "runtime_minutes, still_url, rating, tvdb_id, has_file, added_at, "
                        "play_count, last_viewed_at, view_offset_ms) "
                        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?) "
                        "ON CONFLICT(show_id, season_number, episode_number) DO UPDATE SET "
                        "season_id=excluded.season_id, server_source=excluded.server_source, "
                        "server_id=excluded.server_id, title=excluded.title, "
                        "overview=excluded.overview, air_date=excluded.air_date, "
                        "runtime_minutes=excluded.runtime_minutes, still_url=excluded.still_url, "
                        "rating=excluded.rating, tvdb_id=excluded.tvdb_id, has_file=excluded.has_file, "
                        # keep the earliest known add-date: don't clobber it with a NULL from a
                        # source that didn't report one.
                        "added_at=COALESCE(episodes.added_at, excluded.added_at), "
                        # watch state is server truth — always take the fresh values
                        "play_count=excluded.play_count, last_viewed_at=excluded.last_viewed_at, "
                        "view_offset_ms=excluded.view_offset_ms",
                        (show_id, season_id, server_source, ep.get("server_id"), snum, enum,
                         ep.get("title"), ep.get("overview"), ep.get("air_date"),
                         ep.get("runtime_minutes"), ep.get("still_url"), ep.get("rating"),
                         ep.get("tvdb_id"), 1 if (ep.get("files") or ep.get("file")) else 0,
                         ep.get("added_at"),
                         ep.get("play_count"), ep.get("last_viewed_at"), ep.get("view_offset_ms")),
                    )
                    ep_id = conn.execute(
                        "SELECT id FROM episodes WHERE show_id=? AND season_number=? AND episode_number=?",
                        (show_id, snum, enum),
                    ).fetchone()["id"]
                    self._set_media_file(conn, "episode_id", ep_id,
                                         ep.get("files") or ep.get("file"))

            # Prune only SERVER-originated rows that vanished (server_id set) — the
            # full episode/season list now includes enrichment-added MISSING items
            # (server_id NULL), which the scan must never remove.
            #
            # Episodes are FACTS, only their files are server-owned. A missing
            # (enrichment) row that later got downloaded acquires a server_id via
            # the upsert above — if its file then disappears from the server, a
            # hard DELETE erases the episode's very existence from the page (the
            # Silo-E03 hole: aired episodes vanish, and nothing re-creates them
            # because the full episode sync is one-time). So a vanished episode
            # that carries enrichment identity (an air date or a tvdb id) is
            # DEMOTED back to a missing row — files cleared, server_id NULL —
            # and only identity-less server junk is actually deleted.
            for row in conn.execute(
                "SELECT id, season_number, episode_number, air_date, tvdb_id FROM episodes "
                "WHERE show_id=? AND server_id IS NOT NULL", (show_id,)
            ).fetchall():
                if (row["season_number"], row["episode_number"]) not in seen_eps:
                    if row["air_date"] or row["tvdb_id"]:
                        conn.execute("DELETE FROM media_files WHERE episode_id=?", (row["id"],))
                        conn.execute(
                            "UPDATE episodes SET server_id=NULL, has_file=0 WHERE id=?",
                            (row["id"],),
                        )
                    else:
                        conn.execute(
                            "DELETE FROM episodes WHERE show_id=? AND season_number=? AND episode_number=?",
                            (show_id, row["season_number"], row["episode_number"]),
                        )
            for row in conn.execute(
                "SELECT season_number FROM seasons WHERE show_id=? AND server_id IS NOT NULL", (show_id,)
            ).fetchall():
                if row["season_number"] not in seen_seasons:
                    conn.execute("DELETE FROM seasons WHERE show_id=? AND season_number=?",
                                 (show_id, row["season_number"]))
            conn.commit()
            return show_id
        finally:
            conn.close()

    def server_ids(self, table: str, server_source: str) -> set:
        """All server_ids already stored for a server (for incremental early-stop).
        'episodes' lets an incremental scan tell a fully-present show from one that
        gained episodes (a show's add-date doesn't move when episodes arrive)."""
        if table not in ("movies", "shows", "episodes"):
            return set()
        conn = self._get_connection()
        try:
            return {str(r[0]) for r in conn.execute(
                f"SELECT server_id FROM {table} WHERE server_source=? AND server_id IS NOT NULL",
                (server_source,)).fetchall()}
        finally:
            conn.close()

    def table_count(self, table: str) -> int:
        if table not in ("movies", "shows", "episodes"):
            return 0
        conn = self._get_connection()
        try:
            return conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
        finally:
            conn.close()

    def prune_missing(self, table: str, server_source: str, seen_ids) -> int:
        """Delete top-level rows for a server that the scan no longer saw.
        ``table`` is internal ('movies'|'shows'); cascades clean children.

        Safety (mirrors music's deep scan): if removal would wipe >50% of a
        >100-row library, assume a partial server failure and skip it."""
        if table not in ("movies", "shows"):
            raise ValueError(f"prune_missing: unexpected table {table!r}")
        seen = {str(s) for s in seen_ids}
        conn = self._get_connection()
        try:
            existing = [r["server_id"] for r in conn.execute(
                f"SELECT server_id FROM {table} WHERE server_source=?", (server_source,)
            ).fetchall()]
            stale = [sid for sid in existing if str(sid) not in seen]
            if len(stale) > len(existing) * 0.5 and len(existing) > 100:
                logger.warning(
                    "Video deep scan: %d/%d %s stale (>50%%) — skipping removal (likely a "
                    "partial server response)", len(stale), len(existing), table)
                return 0
            for sid in stale:
                conn.execute(f"DELETE FROM {table} WHERE server_source=? AND server_id=?",
                             (server_source, sid))
            conn.commit()
            return len(stale)
        finally:
            conn.close()

    # ── library listing ───────────────────────────────────────────────────────
    @staticmethod
    def _with_poster_flag(row: dict) -> dict:
        # Don't leak the raw server thumb path; just say whether a poster exists
        # (the frontend hits /api/video/poster/<kind>/<id> when true).
        d = dict(row)
        d["has_poster"] = bool(d.pop("poster_url", None))
        return d

    def list_movies(self) -> list[dict]:
        conn = self._get_connection()
        try:
            rows = conn.execute(
                "SELECT id, title, year, poster_url, has_file, monitored "
                "FROM movies ORDER BY COALESCE(sort_title, title) COLLATE NOCASE, title"
            ).fetchall()
            return [self._with_poster_flag(r) for r in rows]
        finally:
            conn.close()

    def list_shows(self) -> list[dict]:
        conn = self._get_connection()
        try:
            rows = conn.execute(
                "SELECT s.id, s.title, s.year, s.poster_url, s.monitored, "
                "(SELECT COUNT(*) FROM episodes e WHERE e.show_id = s.id) AS episode_count, "
                "(SELECT COUNT(*) FROM episodes e WHERE e.show_id = s.id AND e.has_file = 1) AS owned_count "
                "FROM shows s ORDER BY COALESCE(s.sort_title, s.title) COLLATE NOCASE, s.title"
            ).fetchall()
            return [self._with_poster_flag(r) for r in rows]
        finally:
            conn.close()

    def calendar_upcoming(self, start_date: str, end_date: str, server_source=None,
                          watchlist_only: bool = False) -> list[dict]:
        """Episodes airing in [start_date, end_date] (ISO) for shows on the active
        video server (``server_source``) — the Calendar feed. Scoped to one server
        so Plex and Jellyfin never commingle. Each row carries owned/missing
        (has_file), a still flag, and show network/airs_time/year for the card.

        ``watchlist_only`` restricts to the EFFECTIVE watchlist — explicit show
        follows ∪ airing library shows (not muted), mirroring _effective_shows /
        the Shows watchlist tab — so the calendar tracks what you follow."""
        # server_source given → that server only; None → all owned shows.
        if server_source:
            srv_where, pre = "s.server_source = ?", [server_source]
        else:
            srv_where, pre = "s.server_source IS NOT NULL", []
        wl_where = ""
        if watchlist_only:
            # A show with an episode in the calendar window is airing BY DEFINITION,
            # so for the calendar we only exclude shows we KNOW are terminal — a
            # NULL/blank status (enrichment gap) must NOT hide an airing show. (The
            # global _ACTIVE_SHOW_SQL requires a non-null status, which drops ~29%
            # of shows whose status backfill never landed — Cops, Dutton Ranch, …)
            active = ("(s.status IS NULL OR TRIM(s.status)='' OR LOWER(s.status) "
                      "NOT IN ('ended','canceled','cancelled','completed'))")
            # approved=1 on the explicit-follow arm: a follow still awaiting admin
            # approval must not feed the auto-wishlist airing job. The default arm
            # (airing library shows) is unaffected — those are already-owned shows,
            # not something a member just asked for.
            wl_where = (
                " AND (s.tmdb_id IN (SELECT tmdb_id FROM video_watchlist "
                "                    WHERE kind='show' AND state='follow' AND approved=1)"
                " OR (" + active + " AND s.tmdb_id NOT IN "
                "(SELECT tmdb_id FROM video_watchlist WHERE kind='show' AND state='mute')))")
        conn = self._get_connection()
        try:
            rows = conn.execute(
                "SELECT e.id, e.show_id, e.season_number, e.episode_number, e.title, "
                "e.overview, e.air_date, e.runtime_minutes, e.rating, e.has_file, e.monitored, "
                "e.still_url, (e.still_url IS NOT NULL AND e.still_url<>'') AS has_still, "
                "s.tmdb_id AS show_tmdb_id, "
                "s.title AS show_title, s.network, s.airs_time, s.year AS show_year, s.status AS show_status, "
                "(s.poster_url IS NOT NULL AND s.poster_url<>'') AS show_has_poster, "
                "(s.backdrop_url IS NOT NULL AND s.backdrop_url<>'') AS show_has_backdrop "
                "FROM episodes e JOIN shows s ON s.id = e.show_id "
                "WHERE " + srv_where + " "
                "AND e.air_date IS NOT NULL AND e.air_date >= ? AND e.air_date <= ?" + wl_where + " "
                "ORDER BY e.air_date, COALESCE(s.sort_title, s.title) COLLATE NOCASE, "
                "e.season_number, e.episode_number",
                pre + [start_date, end_date]).fetchall()
            return [dict(r) for r in rows]
        finally:
            conn.close()

    def calendar_movie_releases(self, start_date: str, end_date: str) -> list[dict]:
        """Movie release events in [start_date, end_date] (ISO) for WISHLISTED
        movies — the calendar's movie lane. Up to two event types per movie:

          'cinema'    — theatrical premiere (detail_json's TMDB release_date,
                        captured at add time)
          'available' — home availability (the release_date column, owned by the
                        wishlist drain's backfill: digital/physical, or
                        wide-theatrical + window)

        Same-day collision (streaming films: theatrical == digital) emits one
        event, labeled 'available' — that's the truthful label for a film that
        never sees a cinema. The 1970 backfill sentinel means "no date known"
        and is never a calendar event."""
        import json as _json
        conn = self._get_connection()
        try:
            rows = conn.execute(
                "SELECT tmdb_id, title, year, poster_url, library_id, status, "
                "release_date, detail_json FROM video_wishlist WHERE kind='movie' "
                "AND tmdb_id IS NOT NULL").fetchall()
        finally:
            conn.close()
        out: list[dict] = []
        for r in rows:
            r = dict(r)
            theatrical = None
            if r.get("detail_json"):
                try:
                    theatrical = (_json.loads(r["detail_json"]) or {}).get("release_date")
                except Exception:
                    theatrical = None
            avail = r.get("release_date")
            if avail and str(avail).startswith("1970"):
                avail = None
            seen: set[str] = set()
            # 'available' first so a same-day theatrical collapses into it.
            for ev_type, ev_date in (("available", avail), ("cinema", theatrical)):
                d = str(ev_date or "")[:10]
                if len(d) != 10 or d < start_date or d > end_date or d in seen:
                    continue
                seen.add(d)
                out.append({"date": d, "type": ev_type, "tmdb_id": r["tmdb_id"],
                            "title": r["title"], "year": r["year"],
                            "poster_url": r["poster_url"],
                            "owned": bool(r["library_id"]),
                            "library_id": r["library_id"],
                            "status": r["status"]})
        out.sort(key=lambda e: (e["date"], (e["title"] or "").lower()))
        return out

    def get_poster_ref(self, kind: str, item_id: int) -> dict | None:
        """Server source/id/poster path for one movie or show, for the poster proxy."""
        return self.get_art_ref(kind, item_id, "poster")

    def get_art_ref(self, kind: str, item_id: int, art: str = "poster") -> dict | None:
        """Server source/id + artwork path for one movie/show/season, for the image
        proxy. ``art`` is 'poster' or 'backdrop'. Returns the path under
        'poster_url' so the proxy is artwork-agnostic."""
        conn = self._get_connection()
        try:
            if kind == "season":
                if art != "poster":
                    return None
                # Seasons don't carry server_source — inherit the parent show's.
                row = conn.execute(
                    "SELECT sh.server_source, se.server_id, se.poster_url "
                    "FROM seasons se JOIN shows sh ON sh.id = se.show_id WHERE se.id=?",
                    (item_id,)).fetchone()
                return dict(row) if row else None
            if kind == "episode":
                if art != "poster":
                    return None
                # Episode still; episodes carry their own server_source + the still path.
                row = conn.execute(
                    "SELECT server_source, server_id, still_url AS poster_url "
                    "FROM episodes WHERE id=?", (item_id,)).fetchone()
                return dict(row) if row else None
            table = {"movie": "movies", "show": "shows"}.get(kind)
            col = {"poster": "poster_url", "backdrop": "backdrop_url"}.get(art)
            if not table or not col:
                return None
            row = conn.execute(
                f"SELECT server_source, server_id, {col} AS poster_url FROM {table} WHERE id=?",
                (item_id,)).fetchone()
            return dict(row) if row else None
        finally:
            conn.close()

    def random_overlay_preview_item(self, kind="poster", server_source=None) -> dict | None:
        """A random owned item with art, to preview a template on real data. kind
        'poster' = movie/show (2:3), 'season' = a season poster, 'episode' = an
        episode still (16:9) — matching the template's type."""
        kind = str(kind).lower()
        if kind in ("season", "episode"):
            conn = self._get_connection()
            try:
                if kind == "season":
                    r = conn.execute(
                        "SELECT 'season' AS kind, se.id AS id, NULL AS tmdb_id, "
                        "COALESCE(sh.title,'') || ' — ' || COALESCE(NULLIF(se.title,''), 'Season ' || se.season_number) AS title "
                        "FROM seasons se JOIN shows sh ON sh.id = se.show_id "
                        "WHERE se.poster_url IS NOT NULL AND se.poster_url <> '' ORDER BY RANDOM() LIMIT 1").fetchone()
                else:
                    r = conn.execute(
                        "SELECT 'episode' AS kind, e.id AS id, NULL AS tmdb_id, "
                        "COALESCE(sh.title,'') || ' — S' || e.season_number || 'E' || e.episode_number AS title "
                        "FROM episodes e JOIN shows sh ON sh.id = e.show_id "
                        "WHERE e.still_url IS NOT NULL AND e.still_url <> '' ORDER BY RANDOM() LIMIT 1").fetchone()
                return dict(r) if r else None
            except sqlite3.Error:
                return None
            finally:
                conn.close()
        srcm = " AND server_source = ?" if server_source else ""
        srcs = " AND server_source = ?" if server_source else ""
        params = ([server_source] if server_source else []) + ([server_source] if server_source else [])
        conn = self._get_connection()
        try:
            r = conn.execute(
                "SELECT kind, id, tmdb_id, title FROM ("
                f"  SELECT 'movie' AS kind, id, tmdb_id, title FROM movies "
                f"   WHERE tmdb_id IS NOT NULL AND poster_url IS NOT NULL AND poster_url <> ''{srcm}"
                "   UNION ALL "
                f"  SELECT 'show' AS kind, id, tmdb_id, title FROM shows "
                f"   WHERE tmdb_id IS NOT NULL AND poster_url IS NOT NULL AND poster_url <> ''{srcs}"
                ") ORDER BY RANDOM() LIMIT 1", params).fetchone()
            return dict(r) if r else None
        except sqlite3.Error:
            return None
        finally:
            conn.close()

    def search_overlay_preview(self, kind, q, limit=8) -> list:
        """[{id, title, has_poster}] seasons/episodes matching a query, to pick a
        specific one to preview a Season/Episode template on. (Poster templates use
        the general /library search.)"""
        kind = str(kind).lower()
        like = "%" + str(q or "").strip() + "%"
        limit = max(1, min(20, int(limit)))
        conn = self._get_connection()
        try:
            if kind == "season":
                rows = conn.execute(
                    "SELECT se.id AS id, "
                    "COALESCE(sh.title,'') || ' — ' || COALESCE(NULLIF(se.title,''), 'Season ' || se.season_number) AS title, "
                    "(se.poster_url IS NOT NULL AND se.poster_url <> '') AS has_poster "
                    "FROM seasons se JOIN shows sh ON sh.id = se.show_id "
                    "WHERE sh.title LIKE ? AND se.poster_url IS NOT NULL AND se.poster_url <> '' "
                    "ORDER BY sh.title, se.season_number LIMIT ?", (like, limit)).fetchall()
                return [dict(r) for r in rows]
            if kind == "episode":
                rows = conn.execute(
                    "SELECT e.id AS id, "
                    "COALESCE(sh.title,'') || ' — S' || e.season_number || 'E' || e.episode_number || "
                    "CASE WHEN e.title IS NOT NULL AND e.title <> '' THEN ' ' || e.title ELSE '' END AS title, "
                    "(e.still_url IS NOT NULL AND e.still_url <> '') AS has_poster "
                    "FROM episodes e JOIN shows sh ON sh.id = e.show_id "
                    "WHERE (sh.title LIKE ? OR e.title LIKE ?) AND e.still_url IS NOT NULL AND e.still_url <> '' "
                    "ORDER BY sh.title, e.season_number, e.episode_number LIMIT ?", (like, like, limit)).fetchall()
                return [dict(r) for r in rows]
            return []
        except sqlite3.Error:
            return []
        finally:
            conn.close()

    def random_overlay_preview_items(self, n: int = 4, server_source=None) -> list:
        """Up to N distinct random owned titles (tmdb_id + poster) — powers the
        multi-poster filmstrip that checks a template across varying real data."""
        n = max(1, min(12, int(n)))
        srcm = " AND server_source = ?" if server_source else ""
        srcs = " AND server_source = ?" if server_source else ""
        params = ([server_source] if server_source else []) + ([server_source] if server_source else [])
        conn = self._get_connection()
        try:
            rows = conn.execute(
                "SELECT kind, id, tmdb_id, title FROM ("
                f"  SELECT 'movie' AS kind, id, tmdb_id, title FROM movies "
                f"   WHERE tmdb_id IS NOT NULL AND poster_url IS NOT NULL AND poster_url <> ''{srcm}"
                "   UNION ALL "
                f"  SELECT 'show' AS kind, id, tmdb_id, title FROM shows "
                f"   WHERE tmdb_id IS NOT NULL AND poster_url IS NOT NULL AND poster_url <> ''{srcs}"
                ") ORDER BY RANDOM() LIMIT ?", params + [n]).fetchall()
            return [dict(r) for r in rows]
        except sqlite3.Error:
            return []
        finally:
            conn.close()

    def item_tmdb_id(self, kind: str, item_id: int):
        """TMDB id for a movie/show — the key to re-fetch its clean original poster."""
        table = {"movie": "movies", "show": "shows"}.get(str(kind).lower())
        if not table:
            return None
        conn = self._get_connection()
        try:
            r = conn.execute(f"SELECT tmdb_id FROM {table} WHERE id=?", (int(item_id),)).fetchone()
            return r["tmdb_id"] if r else None
        except (sqlite3.Error, ValueError, TypeError):
            return None
        finally:
            conn.close()

    def poster_set_target(self, kind: str, item_id: int) -> dict | None:
        """Server id + on-disk folder for a movie/show, so a new poster can be pushed
        to the media server and (best-effort) written into the item's folder."""
        kind = str(kind).lower()
        conn = self._get_connection()
        try:
            if kind == "season":
                row = conn.execute(
                    "SELECT sh.server_source, se.server_id, NULL AS path "
                    "FROM seasons se JOIN shows sh ON sh.id = se.show_id WHERE se.id=?",
                    (int(item_id),)).fetchone()
                return dict(row) if row else None
            if kind == "episode":
                row = conn.execute(
                    "SELECT server_source, server_id, NULL AS path FROM episodes WHERE id=?",
                    (int(item_id),)).fetchone()
                return dict(row) if row else None
            table = {"movie": "movies", "show": "shows"}.get(kind)
            if not table:
                return None
            row = conn.execute(
                f"SELECT server_source, server_id, path FROM {table} WHERE id=?",
                (int(item_id),)).fetchone()
            return dict(row) if row else None
        except (sqlite3.Error, ValueError, TypeError):
            return None
        finally:
            conn.close()

    def show_episode_count(self, show_id: int) -> int:
        """How many episode rows a show has (owned + known-missing). Used to
        report what a TMDB episode re-scan actually added, so the action can say
        'added 12' instead of claiming success and leaving you to count."""
        conn = self._get_connection()
        try:
            row = conn.execute("SELECT COUNT(*) c FROM episodes WHERE show_id=?",
                               (int(show_id),)).fetchone()
            return int(row["c"] if row else 0)
        except (sqlite3.Error, ValueError, TypeError):
            return 0
        finally:
            conn.close()

    def set_item_root_folder(self, kind: str, item_id: int, root_folder_id) -> bool:
        """File a movie/show under a configured Library (root_folders row).

        Metadata only — nothing on disk moves. It decides where FUTURE work for
        this title goes: the wishlist drain, RSS instant-grab and upgrades all
        resolve their destination from this column, so correcting it is how a
        title that landed in the wrong Library starts behaving.

        ``root_folder_id`` None clears the assignment, which is a real state, not
        an error: an unassigned title falls back to the primary Library for its
        kind (resolve_download_root), exactly as it did before Libraries existed.
        A row is only accepted when it exists AND matches the item's kind — a
        movie filed under a TV Library would send every future grab to the wrong
        tree, and the failure would be silent."""
        table = {"movie": "movies", "show": "shows"}.get(str(kind).lower())
        if not table:
            return False
        rid = None
        if root_folder_id not in (None, "", "null"):
            try:
                rid = int(root_folder_id)
            except (TypeError, ValueError):
                return False
            row = self.get_root_folder(rid)
            want = "movie" if table == "movies" else "show"
            if not row or str(row.get("content_kind") or "") != want:
                return False
        conn = self._get_connection()
        try:
            cur = conn.execute(f"UPDATE {table} SET root_folder_id=? WHERE id=?",
                               (rid, int(item_id)))
            conn.commit()
            return cur.rowcount > 0
        except (sqlite3.Error, ValueError, TypeError):
            logger.exception("set_item_root_folder failed (%s %s)", kind, item_id)
            return False
        finally:
            conn.close()

    def set_item_poster_url(self, kind: str, item_id: int, poster_url: str) -> bool:
        """Best-effort: point a movie/show at a new poster path/URL so SoulSync shows it
        immediately (the next scan reconciles it with the server's own copy)."""
        table = {"movie": "movies", "show": "shows"}.get(str(kind).lower())
        if not table:
            return False
        conn = self._get_connection()
        try:
            conn.execute(f"UPDATE {table} SET poster_url=? WHERE id=?",
                         (poster_url, int(item_id)))
            conn.commit()
            return True
        except (sqlite3.Error, ValueError, TypeError):
            return False
        finally:
            conn.close()

    # ── overlay templates (Artwork Studio) ────────────────────────────────────
    # CRUD for saved overlay designs. `definition` is a JSON scene (canvas meta +
    # ordered layer list); we store it as text and parse on read so callers get a
    # dict. The gallery list omits the (potentially large) definition + thumbnail.
    @staticmethod
    def _parse_definition(raw) -> dict:
        try:
            d = json.loads(raw) if raw else {}
            return d if isinstance(d, dict) else {}
        except (ValueError, TypeError):
            return {}

    def list_overlay_templates(self) -> list:
        """Every saved template, newest-edited first — light rows for the gallery
        (id, name, timestamps, layer count, thumbnail). No full definition."""
        conn = self._get_connection()
        try:
            rows = conn.execute(
                "SELECT id, name, thumbnail, definition, created_at, updated_at "
                "FROM overlay_templates ORDER BY updated_at DESC, id DESC").fetchall()
            out = []
            for r in rows:
                d = dict(r)
                defn = self._parse_definition(d.pop("definition", None))
                d["layer_count"] = len(defn.get("layers") or [])
                d["kind"] = defn.get("kind") or "poster"   # Poster / Season / Episode
                out.append(d)
            return out
        finally:
            conn.close()

    def get_overlay_template(self, template_id: int) -> dict | None:
        """One template with its parsed `definition` dict, or None."""
        conn = self._get_connection()
        try:
            row = conn.execute(
                "SELECT id, name, thumbnail, definition, created_at, updated_at "
                "FROM overlay_templates WHERE id=?", (int(template_id),)).fetchone()
            if not row:
                return None
            d = dict(row)
            d["definition"] = self._parse_definition(d.get("definition"))
            return d
        except (ValueError, TypeError):
            return None
        finally:
            conn.close()

    def create_overlay_template(self, name: str, definition=None, thumbnail=None) -> int | None:
        """Insert a new template; returns its id. `definition` may be a dict or a
        JSON string (dicts are serialized)."""
        name = (name or "").strip() or "Untitled template"
        raw = json.dumps(definition) if isinstance(definition, (dict, list)) else (definition or "{}")
        conn = self._get_connection()
        try:
            cur = conn.execute(
                "INSERT INTO overlay_templates (name, definition, thumbnail) VALUES (?,?,?)",
                (name, raw, thumbnail))
            conn.commit()
            return cur.lastrowid
        except sqlite3.Error:
            logger.exception("create_overlay_template failed")
            return None
        finally:
            conn.close()

    def update_overlay_template(self, template_id: int, *, name=None,
                                definition=None, thumbnail=None) -> bool:
        """Patch a template's name / definition / thumbnail (only the provided
        fields) and bump updated_at. Returns True if a row was changed."""
        sets, params = [], []
        if name is not None:
            sets.append("name=?"); params.append((name or "").strip() or "Untitled template")
        if definition is not None:
            raw = json.dumps(definition) if isinstance(definition, (dict, list)) else definition
            sets.append("definition=?"); params.append(raw)
        if thumbnail is not None:
            sets.append("thumbnail=?"); params.append(thumbnail)
        if not sets:
            return False
        sets.append("updated_at=datetime('now')")
        params.append(int(template_id))
        conn = self._get_connection()
        try:
            cur = conn.execute(
                f"UPDATE overlay_templates SET {', '.join(sets)} WHERE id=?", params)
            conn.commit()
            return cur.rowcount > 0
        except sqlite3.Error:
            logger.exception("update_overlay_template failed for %s", template_id)
            return False
        finally:
            conn.close()

    def delete_overlay_template(self, template_id: int) -> bool:
        conn = self._get_connection()
        try:
            cur = conn.execute("DELETE FROM overlay_templates WHERE id=?", (int(template_id),))
            conn.commit()
            return cur.rowcount > 0
        except (sqlite3.Error, ValueError, TypeError):
            return False
        finally:
            conn.close()

    def duplicate_overlay_template(self, template_id: int) -> int | None:
        """Copy a template into a new "… (copy)" row; returns the new id or None."""
        src = self.get_overlay_template(template_id)
        if not src:
            return None
        return self.create_overlay_template(
            (src.get("name") or "Untitled") + " (copy)", src.get("definition") or {})

    # ── Collections (Kometa parity) ───────────────────────────────────────────
    # SoulSync-managed movie/show collections. A definition (`definition` JSON =
    # smart rules OR a list source) resolves to a set of owned items and is synced
    # to the active video server. Same CRUD shape as overlay templates.
    _COLLECTION_BOOL_COLS = ("pinned", "wishlist_missing", "enabled")

    def list_collection_definitions(self) -> list:
        """Every collection definition, newest-edited first (light rows for the
        gallery) with the last-synced member count/time joined in."""
        conn = self._get_connection()
        try:
            rows = conn.execute(
                "SELECT id, name, kind, media_type, poster_url, summary, sort_order, "
                "sync_mode, pinned, wishlist_missing, enabled, window_start, window_end, "
                "collection_mode, created_at, updated_at "
                "FROM collection_definitions ORDER BY updated_at DESC, id DESC").fetchall()
            out = []
            for r in rows:
                d = dict(r)
                s = conn.execute(
                    "SELECT member_count, synced_at FROM collection_sync WHERE definition_id=?",
                    (d["id"],)).fetchone()
                d["member_count"] = s["member_count"] if s else None
                d["synced_at"] = s["synced_at"] if s else None
                out.append(d)
            return out
        finally:
            conn.close()

    def get_collection_definition(self, definition_id: int) -> dict | None:
        """One definition with its parsed `definition` dict, or None."""
        conn = self._get_connection()
        try:
            row = conn.execute(
                "SELECT * FROM collection_definitions WHERE id=?",
                (int(definition_id),)).fetchone()
            if not row:
                return None
            d = dict(row)
            d["definition"] = self._parse_definition(d.get("definition"))
            return d
        except (ValueError, TypeError):
            return None
        finally:
            conn.close()

    def create_collection_definition(self, name: str, *, kind="smart", media_type="movie",
                                     definition=None, poster_url=None, summary=None,
                                     sort_order="release", sync_mode="sync", pinned=False,
                                     wishlist_missing=False, enabled=True,
                                     window_start=None, window_end=None,
                                     collection_mode=None) -> int | None:
        """Insert a collection definition; returns its id. `definition` may be a
        dict or a JSON string (dicts are serialized)."""
        name = (name or "").strip() or "Untitled collection"
        raw = json.dumps(definition) if isinstance(definition, (dict, list)) else (definition or "{}")
        conn = self._get_connection()
        try:
            cur = conn.execute(
                "INSERT INTO collection_definitions (name, kind, media_type, definition, "
                "poster_url, summary, sort_order, sync_mode, pinned, wishlist_missing, enabled, "
                "window_start, window_end, collection_mode) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (name, kind, media_type, raw, poster_url, summary, sort_order, sync_mode,
                 1 if pinned else 0, 1 if wishlist_missing else 0, 1 if enabled else 0,
                 (window_start or "").strip() or None, (window_end or "").strip() or None,
                 (collection_mode or "").strip() or None))
            conn.commit()
            return cur.lastrowid
        except sqlite3.Error:
            logger.exception("create_collection_definition failed")
            return None
        finally:
            conn.close()

    def update_collection_definition(self, definition_id: int, **fields) -> bool:
        """Patch only the provided fields (None values ignored) and bump
        updated_at. Booleans coerced to 0/1; `definition` dicts serialized."""
        allowed = {"name", "kind", "media_type", "definition", "poster_url", "summary",
                   "sort_order", "sync_mode", "pinned", "wishlist_missing", "enabled",
                   "window_start", "window_end", "collection_mode"}
        sets, params = [], []
        for k, v in fields.items():
            if k not in allowed or v is None:
                continue
            if k == "definition":
                v = json.dumps(v) if isinstance(v, (dict, list)) else v
            elif k in self._COLLECTION_BOOL_COLS:
                v = 1 if v else 0
            elif k == "name":
                v = (v or "").strip() or "Untitled collection"
            elif k in ("window_start", "window_end", "collection_mode"):
                v = (v or "").strip() or None   # "" clears (mode: back to leave-alone)
            sets.append(f"{k}=?")
            params.append(v)
        if not sets:
            return False
        sets.append("updated_at=datetime('now')")
        params.append(int(definition_id))
        conn = self._get_connection()
        try:
            cur = conn.execute(
                f"UPDATE collection_definitions SET {', '.join(sets)} WHERE id=?", params)
            conn.commit()
            return cur.rowcount > 0
        except sqlite3.Error:
            logger.exception("update_collection_definition failed for %s", definition_id)
            return False
        finally:
            conn.close()

    def delete_collection_definition(self, definition_id: int) -> bool:
        conn = self._get_connection()
        try:
            cur = conn.execute(
                "DELETE FROM collection_definitions WHERE id=?", (int(definition_id),))
            conn.commit()
            return cur.rowcount > 0
        except (sqlite3.Error, ValueError, TypeError):
            return False
        finally:
            conn.close()

    def duplicate_collection_definition(self, definition_id: int) -> int | None:
        src = self.get_collection_definition(definition_id)
        if not src:
            return None
        return self.create_collection_definition(
            (src.get("name") or "Untitled") + " (copy)",
            kind=src.get("kind", "smart"), media_type=src.get("media_type", "movie"),
            definition=src.get("definition") or {}, poster_url=src.get("poster_url"),
            summary=src.get("summary"), sort_order=src.get("sort_order", "release"),
            sync_mode=src.get("sync_mode", "sync"), pinned=bool(src.get("pinned")),
            wishlist_missing=bool(src.get("wishlist_missing")), enabled=bool(src.get("enabled")))

    def resolve_smart_members(self, media_type: str, definition: dict) -> list:
        """Owned library items matching a smart definition's rules. Raises
        SmartFilterError (from compile_rules) on an empty/invalid rule set — the
        caller decides how to surface it. Only items that are actually on a
        server (have a server_id) are returned, since a collection is a server
        grouping."""
        from core.video.collections.smart_filter import compile_rules
        where, params = compile_rules(definition, media_type)
        table = "movies" if media_type == "movie" else "shows"
        date_col = "release_date" if media_type == "movie" else "first_air_date"
        sql = (
            f"SELECT id, server_source, server_id, tmdb_id, title, year, poster_url, "
            f"rating, added_at, {date_col} AS release_date "
            f"FROM {table} "
            f"WHERE server_id IS NOT NULL AND TRIM(server_id) != '' AND {where}"
        )
        conn = self._get_connection()
        try:
            return [dict(r) for r in conn.execute(sql, params).fetchall()]
        finally:
            conn.close()

    def owned_by_tmdb_ids(self, media_type: str, tmdb_ids) -> list:
        """Owned (on-server) items whose tmdb_id is in the given set — the
        intersection step for list/franchise collections."""
        ids = [int(t) for t in (tmdb_ids or []) if t is not None]
        if not ids:
            return []
        table = "movies" if media_type == "movie" else "shows"
        date_col = "release_date" if media_type == "movie" else "first_air_date"
        placeholders = ", ".join("?" for _ in ids)
        sql = (
            f"SELECT id, server_source, server_id, tmdb_id, title, year, poster_url, "
            f"rating, added_at, {date_col} AS release_date "
            f"FROM {table} "
            f"WHERE server_id IS NOT NULL AND TRIM(server_id) != '' "
            f"AND tmdb_id IN ({placeholders})"
        )
        conn = self._get_connection()
        try:
            return [dict(r) for r in conn.execute(sql, ids).fetchall()]
        finally:
            conn.close()

    def franchise_owned_members(self, tmdb_collection_id: int) -> list:
        """Owned movies belonging to a TMDB franchise (movies only)."""
        conn = self._get_connection()
        try:
            rows = conn.execute(
                "SELECT id, server_source, server_id, tmdb_id, title, year, poster_url, "
                "rating, added_at, release_date FROM movies "
                "WHERE server_id IS NOT NULL AND TRIM(server_id) != '' "
                "AND tmdb_collection_id = ?", (int(tmdb_collection_id),)).fetchall()
            return [dict(r) for r in rows]
        finally:
            conn.close()

    # ── Preset-pack aggregates (Collection Studio "easy setup") ──────────────
    # Facet counts over the OWNED, ON-SERVER library — the same base condition
    # resolve_smart_members uses, so a preset entry's count equals what the
    # collection will actually contain after apply + sync.
    _ON_SERVER = "server_id IS NOT NULL AND TRIM(server_id) != ''"

    def owned_genre_counts(self, media_type: str, limit: int = 60) -> list:
        """Genres across owned items with counts, busiest first —
        [{value, count}] for the Genres preset pack."""
        if media_type == "movie":
            link, owner, tbl = "movie_genres", "movie_id", "movies"
        elif media_type == "show":
            link, owner, tbl = "show_genres", "show_id", "shows"
        else:
            return []
        conn = self._get_connection()
        try:
            rows = conn.execute(
                f"SELECT g.name AS v, COUNT(*) AS c FROM {link} lt "
                f"JOIN genres g ON g.id = lt.genre_id "
                f"JOIN {tbl} t ON t.id = lt.{owner} "
                f"WHERE t.server_id IS NOT NULL AND TRIM(t.server_id) != '' "
                f"GROUP BY g.name ORDER BY c DESC, g.name LIMIT ?", (int(limit),)).fetchall()
            return [{"value": r["v"], "count": r["c"]} for r in rows]
        except sqlite3.Error:
            return []
        finally:
            conn.close()

    def owned_decade_counts(self, media_type: str) -> list:
        """Owned items bucketed by decade — [{value: 1980, count}] newest first."""
        if media_type not in ("movie", "show"):
            return []
        tbl = "movies" if media_type == "movie" else "shows"
        conn = self._get_connection()
        try:
            rows = conn.execute(
                f"SELECT (year / 10) * 10 AS d, COUNT(*) AS c FROM {tbl} "
                f"WHERE {self._ON_SERVER} AND year IS NOT NULL AND year >= 1900 "
                f"GROUP BY d ORDER BY d DESC").fetchall()
            return [{"value": r["d"], "count": r["c"]} for r in rows]
        except sqlite3.Error:
            return []
        finally:
            conn.close()

    def owned_studio_counts(self, limit: int = 40) -> list:
        """Movie studios with owned counts, busiest first — [{value, count}]. Counts over
        the studios link table (ALL production companies per movie), so a movie counts for
        every company it was made by, not just its primary one."""
        conn = self._get_connection()
        try:
            rows = conn.execute(
                "SELECT s.name AS v, COUNT(*) AS c FROM movie_studios ms "
                "JOIN studios s ON s.id = ms.studio_id "
                "JOIN movies m ON m.id = ms.movie_id "
                "WHERE m.server_id IS NOT NULL AND TRIM(m.server_id) != '' "
                "GROUP BY s.name ORDER BY c DESC, s.name LIMIT ?", (int(limit),)).fetchall()
            return [{"value": r["v"], "count": r["c"]} for r in rows]
        except sqlite3.Error:
            return []
        finally:
            conn.close()

    def owned_network_counts(self, limit: int = 40) -> list:
        """Show networks with owned counts, busiest first — [{value, count}]. Counts over the
        networks link table (ALL networks per show)."""
        conn = self._get_connection()
        try:
            rows = conn.execute(
                "SELECT n.name AS v, COUNT(*) AS c FROM show_networks sn "
                "JOIN networks n ON n.id = sn.network_id "
                "JOIN shows sh ON sh.id = sn.show_id "
                "WHERE sh.server_id IS NOT NULL AND TRIM(sh.server_id) != '' "
                "GROUP BY n.name ORDER BY c DESC, n.name LIMIT ?", (int(limit),)).fetchall()
            return [{"value": r["v"], "count": r["c"]} for r in rows]
        except sqlite3.Error:
            return []
        finally:
            conn.close()

    def count_smart_members(self, media_type: str, definition: dict) -> int:
        """COUNT of resolve_smart_members without materializing the rows — sizes
        the fixed preset entries (4K, Recently Added, …). Raises SmartFilterError
        for a bad definition, same as resolve."""
        from core.video.collections.smart_filter import compile_rules
        where, params = compile_rules(definition, media_type)
        table = "movies" if media_type == "movie" else "shows"
        conn = self._get_connection()
        try:
            row = conn.execute(
                f"SELECT COUNT(*) AS c FROM {table} "
                f"WHERE {self._ON_SERVER} AND {where}", params).fetchone()
            return int(row["c"] if row else 0)
        finally:
            conn.close()

    def search_owned_titles(self, media_type: str, query: str, limit: int = 10) -> list:
        """Owned, TMDB-matched items whose title matches — feeds the editor's
        include-override picker. Returns [{tmdb_id, title, year}]."""
        if media_type not in ("movie", "show") or not (query or "").strip():
            return []
        table = "movies" if media_type == "movie" else "shows"
        like = "%" + query.strip().replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"
        conn = self._get_connection()
        try:
            rows = conn.execute(
                f"SELECT tmdb_id, title, year FROM {table} "
                f"WHERE {self._ON_SERVER} AND tmdb_id IS NOT NULL "
                f"AND title LIKE ? ESCAPE '\\' ORDER BY title LIMIT ?",
                (like, int(limit))).fetchall()
            return [dict(r) for r in rows]
        except sqlite3.Error:
            return []
        finally:
            conn.close()

    def owned_titles_by_tmdb_ids(self, media_type: str, tmdb_ids) -> list:
        """Titles for override chips — [{tmdb_id, title, year}] for owned items."""
        rows = self.owned_by_tmdb_ids(media_type, tmdb_ids) if tmdb_ids else []
        return [{"tmdb_id": r.get("tmdb_id"), "title": r.get("title"), "year": r.get("year")}
                for r in rows]

    def find_library_ref_by_title(self, kind, title, year=None):
        """A library movie/show id matched by title (+ year for movies) — the
        fallback the live-activity click-through uses when the native server id
        doesn't line up. None when not owned."""
        if not title:
            return None
        table = "movies" if str(kind) == "movie" else "shows"
        conn = self._get_connection()
        try:
            if year:
                r = conn.execute(f"SELECT id FROM {table} WHERE title=? COLLATE NOCASE "
                                 "AND year=? LIMIT 1", (str(title), year)).fetchone()
                if r:
                    return r["id"]
            r = conn.execute(f"SELECT id FROM {table} WHERE title=? COLLATE NOCASE LIMIT 1",
                             (str(title),)).fetchone()
            return r["id"] if r else None
        except sqlite3.Error:
            return None
        finally:
            conn.close()

    def items_by_server_ids(self, server_ids, server_source=None) -> list:
        """Library items (movies + shows) matching the given native server ids —
        maps a server collection's membership back to our rows for adoption.
        Returns [{kind, id, tmdb_id, server_id, title}]."""
        ids = [str(s) for s in (server_ids or []) if str(s).strip()]
        if not ids:
            return []
        out = []
        conn = self._get_connection()
        try:
            for kind, table in (("movie", "movies"), ("show", "shows")):
                for i in range(0, len(ids), 400):        # chunk under SQLite's var cap
                    chunk = ids[i:i + 400]
                    ph = ", ".join("?" for _ in chunk)
                    sql = (f"SELECT id, tmdb_id, server_id, title FROM {table} "
                           f"WHERE server_id IN ({ph})")
                    args: list = list(chunk)
                    if server_source:
                        sql += " AND server_source=?"
                        args.append(server_source)
                    for r in conn.execute(sql, args):
                        out.append({"kind": kind, "id": r["id"], "tmdb_id": r["tmdb_id"],
                                    "server_id": str(r["server_id"]), "title": r["title"]})
            return out
        except sqlite3.Error:
            logger.exception("items_by_server_ids failed")
            return []
        finally:
            conn.close()

    def get_imdb_tmdb(self, imdb_id) -> dict | None:
        """Persisted tt→TMDB mapping (+ poster art), or None when never resolved."""
        conn = self._get_connection()
        try:
            r = conn.execute("SELECT movie_tmdb, show_tmdb, movie_poster, show_poster "
                             "FROM imdb_tmdb_map WHERE imdb_id=?", (imdb_id,)).fetchone()
            return ({"movie": r["movie_tmdb"], "show": r["show_tmdb"],
                     "movie_poster": r["movie_poster"], "show_poster": r["show_poster"]}
                    if r else None)
        except sqlite3.Error:
            return None
        finally:
            conn.close()

    def put_imdb_tmdb(self, imdb_id, movie_tmdb, show_tmdb,
                      movie_poster=None, show_poster=None) -> None:
        conn = self._get_connection()
        try:
            conn.execute(
                "INSERT INTO imdb_tmdb_map (imdb_id, movie_tmdb, show_tmdb, movie_poster, show_poster) "
                "VALUES (?,?,?,?,?) "
                "ON CONFLICT(imdb_id) DO UPDATE SET movie_tmdb=excluded.movie_tmdb, "
                "show_tmdb=excluded.show_tmdb, "
                "movie_poster=COALESCE(excluded.movie_poster, imdb_tmdb_map.movie_poster), "
                "show_poster=COALESCE(excluded.show_poster, imdb_tmdb_map.show_poster), "
                "updated_at=datetime('now')",
                (imdb_id, movie_tmdb, show_tmdb, movie_poster, show_poster))
            conn.commit()
        except sqlite3.Error:
            logger.exception("put_imdb_tmdb failed")
        finally:
            conn.close()

    def tmdb_by_library_imdb(self, imdb_id) -> dict:
        """{'movie': tmdb|None, 'show': tmdb|None} from the LIBRARY's own rows —
        a chart title you already own needs no network to map at all."""
        out = {"movie": None, "show": None}
        conn = self._get_connection()
        try:
            for kind, table in (("movie", "movies"), ("show", "shows")):
                r = conn.execute(f"SELECT tmdb_id FROM {table} "
                                 f"WHERE imdb_id=? AND tmdb_id IS NOT NULL LIMIT 1",
                                 (imdb_id,)).fetchone()
                if r:
                    out[kind] = r["tmdb_id"]
            return out
        except sqlite3.Error:
            return out
        finally:
            conn.close()

    # ── Collection sync ledger (managed-collection map + skip signature) ──────
    def get_collection_sync(self, definition_id: int) -> dict | None:
        conn = self._get_connection()
        try:
            row = conn.execute(
                "SELECT * FROM collection_sync WHERE definition_id=?",
                (int(definition_id),)).fetchone()
            return dict(row) if row else None
        finally:
            conn.close()

    def record_collection_sync(self, definition_id: int, *, server_source, server_id,
                               members_sig, member_count) -> bool:
        conn = self._get_connection()
        try:
            conn.execute(
                "INSERT INTO collection_sync (definition_id, server_source, server_id, "
                "members_sig, member_count, synced_at) VALUES (?,?,?,?,?, datetime('now')) "
                "ON CONFLICT(definition_id) DO UPDATE SET server_source=excluded.server_source, "
                "server_id=excluded.server_id, members_sig=excluded.members_sig, "
                "member_count=excluded.member_count, synced_at=excluded.synced_at",
                (int(definition_id), server_source, server_id, members_sig, int(member_count)))
            conn.commit()
            return True
        except sqlite3.Error:
            logger.exception("record_collection_sync failed for %s", definition_id)
            return False
        finally:
            conn.close()

    def delete_collection_sync(self, definition_id: int) -> None:
        conn = self._get_connection()
        try:
            conn.execute("DELETE FROM collection_sync WHERE definition_id=?", (int(definition_id),))
            conn.commit()
        except sqlite3.Error:
            logger.exception("delete_collection_sync failed for %s", definition_id)
        finally:
            conn.close()

    def list_collection_syncs(self) -> list:
        """Every ledger row (+ its definition's name) — maps server collections
        back to the SoulSync definition that manages them, so the server-cleanup
        view can tell ours from foreign (e.g. old Kometa) collections."""
        conn = self._get_connection()
        try:
            rows = conn.execute(
                "SELECT s.definition_id, s.server_source, s.server_id, s.member_count, "
                "s.synced_at, d.name AS definition_name "
                "FROM collection_sync s "
                "LEFT JOIN collection_definitions d ON d.id = s.definition_id").fetchall()
            return [dict(r) for r in rows]
        except sqlite3.Error:
            return []
        finally:
            conn.close()

    def overlay_sample_data(self, kind: str, item_id: int) -> dict | None:
        """Real badge values for one library item — the "load from a real title"
        source for the overlay editor's sample data. Raw values (e.g. resolution
        '2160p', ratings as numbers); the editor formats them for display."""
        kind = str(kind).lower()
        if kind in ("season", "episode"):
            return self._overlay_sample_sub(kind, item_id)
        table = {"movie": "movies", "show": "shows"}.get(kind)
        if not table:
            return None
        conn = self._get_connection()
        try:
            _show_ratings = ", tvmaze_rating, anilist_score" if kind == "show" else ""
            _movie_only = ", tmdb_collection_name, tagline" if kind == "movie" else ""
            row = conn.execute(
                f"SELECT title, year, runtime_minutes, status, content_rating, "
                f"rating, imdb_rating, rt_rating, metacritic, trakt_rating{_show_ratings}{_movie_only}, subtitle_langs, streaming, mediastinger, awards, logo_url, "
                f"{'studio' if kind == 'movie' else 'network'} AS org, "
                f"(poster_url IS NOT NULL AND poster_url <> '') AS has_poster, "
                f"(backdrop_url IS NOT NULL AND backdrop_url <> '') AS has_backdrop "
                f"FROM {table} WHERE id=?", (int(item_id),)).fetchone()
            if not row:
                return None
            d = dict(row)
            out = {
                "title": d.get("title"), "year": d.get("year"),
                "runtime": d.get("runtime_minutes"), "status": d.get("status"),
                "content_rating": d.get("content_rating"),
                "tmdb": d.get("rating"), "imdb": d.get("imdb_rating"),
                "rt": d.get("rt_rating"), "metacritic": d.get("metacritic"),
                "trakt": d.get("trakt_rating"),
                "tvmaze": d.get("tvmaze_rating"), "anilist": d.get("anilist_score"),
                "streaming": d.get("streaming"),
                "collection": d.get("tmdb_collection_name"),
                "tagline": d.get("tagline"),
                "mediastinger": d.get("mediastinger"), "awards": d.get("awards"),
                "studio": d.get("org") if kind == "movie" else None,
                "network": d.get("org") if kind == "show" else None,
                "logo_url": d.get("logo_url"),
                "subtitles": len(_subtitle_langs_list(d.get("subtitle_langs"))) or None,
                "resolution": None, "video_codec": None, "audio_codec": None, "source": None,
                "aspect": None,
                "season_count": None, "episode_count": None, "versions": None,
                "season_number": None, "episode_number": None, "episode_code": None,
            }
            # ALL of the item's genres, comma-joined — a Genre badge shows the
            # primary (first) one, while a "genre includes X" condition can match
            # any of them. Isolated so a genre hiccup can't null the whole payload.
            out["genre"] = None
            try:
                g_link, g_owner = ("movie_genres", "movie_id") if kind == "movie" else ("show_genres", "show_id")
                genres = self._genres_for(conn, g_link, g_owner, int(item_id))
                out["genre"] = ", ".join(genres) if genres else None
            except sqlite3.Error:
                logger.debug("overlay genre lookup failed for %s %s", kind, item_id, exc_info=True)
            # Best owned file drives the quality badges.
            _res_rank = ("CASE mf.resolution WHEN '2160p' THEN 4 WHEN '1080p' THEN 3 "
                         "WHEN '720p' THEN 2 ELSE 1 END DESC")
            if kind == "movie":
                mf = conn.execute(
                    "SELECT resolution, video_codec, audio_codec, release_source, aspect FROM media_files mf "
                    f"WHERE mf.movie_id=? ORDER BY {_res_rank}, mf.size_bytes DESC LIMIT 1",
                    (int(item_id),)).fetchone()
                vc = conn.execute("SELECT COUNT(*) AS n FROM media_files WHERE movie_id=?",
                                  (int(item_id),)).fetchone()
                out["versions"] = (vc["n"] if vc else 0) or None
            else:
                mf = conn.execute(
                    "SELECT resolution, video_codec, audio_codec, release_source, aspect FROM media_files mf "
                    "JOIN episodes e ON e.id=mf.episode_id WHERE e.show_id=? "
                    f"ORDER BY {_res_rank} LIMIT 1", (int(item_id),)).fetchone()
                counts = conn.execute(
                    "SELECT COUNT(DISTINCT season_number) AS seasons, COUNT(*) AS eps "
                    "FROM episodes WHERE show_id=?", (int(item_id),)).fetchone()
                if counts:
                    out["season_count"] = counts["seasons"]
                    out["episode_count"] = counts["eps"]
            if mf:
                m = dict(mf)
                out["resolution"] = m.get("resolution")
                out["video_codec"] = m.get("video_codec")
                out["audio_codec"] = m.get("audio_codec")
                out["source"] = m.get("release_source")
                out["aspect"] = m.get("aspect")
            out["has_poster"] = bool(d.get("has_poster"))
            out["has_backdrop"] = bool(d.get("has_backdrop"))
            return out
        except (sqlite3.Error, ValueError, TypeError):
            logger.exception("overlay_sample_data failed for %s %s", kind, item_id)
            return None
        finally:
            conn.close()

    @staticmethod
    def _empty_overlay_values() -> dict:
        """Full badge-value key set (all None) so a season/episode payload has the
        same shape as movie/show — the editor + compositor use .get(), but keeping
        parity avoids surprises."""
        return {k: None for k in (
            "title", "year", "runtime", "status", "content_rating",
            "tmdb", "imdb", "rt", "metacritic", "trakt", "tvmaze", "anilist",
            "streaming", "collection", "tagline", "mediastinger", "awards",
            "studio", "network", "logo_url", "subtitles",
            "resolution", "video_codec", "audio_codec", "source", "aspect",
            "season_count", "episode_count", "versions", "genre",
            "season_number", "episode_number", "episode_code",
            "has_poster", "has_backdrop")}

    def _overlay_sample_sub(self, kind: str, item_id: int) -> dict | None:
        """Badge values for a season or episode (the sub-item overlay scopes). A
        season inherits the show's content_rating/network; an episode adds its own
        number/code/air-year/rating and quality badges from its owned file."""
        conn = self._get_connection()
        try:
            if kind == "season":
                row = conn.execute(
                    "SELECT se.title, se.season_number, sh.title AS show_title, "
                    "sh.content_rating, sh.network, sh.year, "
                    "(SELECT COUNT(*) FROM episodes e WHERE e.season_id = se.id) AS episode_count, "
                    "(se.poster_url IS NOT NULL AND se.poster_url <> '') AS has_poster "
                    "FROM seasons se JOIN shows sh ON sh.id = se.show_id WHERE se.id=?",
                    (int(item_id),)).fetchone()
                if not row:
                    return None
                d = dict(row)
                out = self._empty_overlay_values()
                out.update({
                    "title": d.get("show_title"),
                    "season_number": d.get("season_number"),
                    "episode_count": d.get("episode_count") or None,
                    "content_rating": d.get("content_rating"),
                    "network": d.get("network"), "year": d.get("year"),
                    "has_poster": bool(d.get("has_poster")),
                })
                return out
            # episode
            row = conn.execute(
                "SELECT e.title, e.season_number, e.episode_number, e.air_date, "
                "e.runtime_minutes, e.rating, sh.title AS show_title, sh.content_rating, sh.network, "
                "(e.still_url IS NOT NULL AND e.still_url <> '') AS has_poster "
                "FROM episodes e JOIN shows sh ON sh.id = e.show_id WHERE e.id=?",
                (int(item_id),)).fetchone()
            if not row:
                return None
            d = dict(row)
            sn, en = d.get("season_number"), d.get("episode_number")
            code = ("S%dE%d" % (int(sn), int(en))) if sn is not None and en is not None else None
            ad = str(d.get("air_date") or "")
            year = int(ad[:4]) if len(ad) >= 4 and ad[:4].isdigit() else None
            mf = conn.execute(
                "SELECT resolution, video_codec, audio_codec, release_source, aspect "
                "FROM media_files WHERE episode_id=? ORDER BY size_bytes DESC LIMIT 1",
                (int(item_id),)).fetchone()
            out = self._empty_overlay_values()
            out.update({
                "title": d.get("title"),
                "season_number": sn, "episode_number": en, "episode_code": code,
                "runtime": d.get("runtime_minutes"), "tmdb": d.get("rating"),
                "content_rating": d.get("content_rating"), "network": d.get("network"),
                "year": year, "has_poster": bool(d.get("has_poster")),
            })
            if mf:
                m = dict(mf)
                out["resolution"] = m.get("resolution")
                out["video_codec"] = m.get("video_codec")
                out["audio_codec"] = m.get("audio_codec")
                out["source"] = m.get("release_source")
                out["aspect"] = m.get("aspect")
            return out
        except (sqlite3.Error, ValueError, TypeError):
            logger.exception("overlay_sample_data (sub) failed for %s %s", kind, item_id)
            return None
        finally:
            conn.close()

    # ── overlay apply: assignment (template → scope) + ledger ─────────────────
    def get_overlay_assignments(self) -> dict:
        """{'movie': {template_id, enabled, template_name}, 'show': {...}} — which
        template the apply pipeline burns onto each library scope."""
        conn = self._get_connection()
        try:
            rows = conn.execute(
                "SELECT a.scope, a.template_id, a.enabled, a.filter, t.name AS template_name "
                "FROM overlay_assignment a LEFT JOIN overlay_templates t ON t.id = a.template_id").fetchall()
            out = {}
            for r in rows:
                d = dict(r)
                out[d["scope"]] = {"template_id": d["template_id"], "enabled": bool(d["enabled"]),
                                   "template_name": d["template_name"],
                                   "filter": self._parse_definition(d.get("filter")) or None}
            return out
        finally:
            conn.close()

    def set_overlay_assignment(self, scope: str, template_id, enabled: bool,
                               filter_definition=None) -> bool:
        if scope not in ("movie", "show", "season", "episode"):
            return False
        raw = (json.dumps(filter_definition)
               if isinstance(filter_definition, dict) and filter_definition.get("rules")
               else None)
        conn = self._get_connection()
        try:
            conn.execute(
                "INSERT INTO overlay_assignment (scope, template_id, enabled, filter, updated_at) "
                "VALUES (?,?,?,?,datetime('now')) ON CONFLICT(scope) DO UPDATE SET "
                "template_id=excluded.template_id, enabled=excluded.enabled, "
                "filter=excluded.filter, updated_at=datetime('now')",
                (scope, template_id, 1 if enabled else 0, raw))
            conn.commit()
            return True
        except sqlite3.Error:
            logger.exception("set_overlay_assignment failed for %s", scope)
            return False
        finally:
            conn.close()

    def record_overlay_apply(self, kind: str, item_id: int, template_id,
                             base_sha=None, values_sig=None, plex_poster_key=None) -> None:
        conn = self._get_connection()
        try:
            conn.execute(
                "INSERT INTO overlay_apply (kind, item_id, template_id, base_sha, values_sig, "
                "plex_poster_key, applied_at) "
                "VALUES (?,?,?,?,?,?,datetime('now')) ON CONFLICT(kind, item_id) DO UPDATE SET "
                "template_id=excluded.template_id, base_sha=excluded.base_sha, "
                "values_sig=excluded.values_sig, plex_poster_key=excluded.plex_poster_key, "
                "applied_at=datetime('now')",
                (kind, int(item_id), template_id, base_sha, values_sig, plex_poster_key))
            conn.commit()
        except sqlite3.Error:
            logger.exception("record_overlay_apply failed for %s %s", kind, item_id)
        finally:
            conn.close()

    def get_overlay_apply(self, kind: str, item_id: int) -> dict | None:
        conn = self._get_connection()
        try:
            r = conn.execute(
                "SELECT kind, item_id, template_id, base_sha, values_sig, plex_poster_key, applied_at "
                "FROM overlay_apply WHERE kind=? AND item_id=?", (kind, int(item_id))).fetchone()
            return dict(r) if r else None
        finally:
            conn.close()

    def delete_overlay_apply(self, kind: str, item_id: int) -> bool:
        conn = self._get_connection()
        try:
            cur = conn.execute("DELETE FROM overlay_apply WHERE kind=? AND item_id=?", (kind, int(item_id)))
            conn.commit()
            return cur.rowcount > 0
        except (sqlite3.Error, ValueError, TypeError):
            return False
        finally:
            conn.close()

    def overlay_scope_items(self, scope: str, server_source=None,
                            filter_definition=None) -> list:
        """[{id, title}] for every on-server item in a scope — the apply targets
        (only items with a server_id can receive a pushed poster). Seasons/episodes
        inherit their show's server_source (seasons don't store one).

        ``filter_definition`` (smart-rule JSON, same language as collections)
        narrows the targets; for seasons/episodes it filters the PARENT SHOW
        ("only anime shows get episode overlays"). Raises SmartFilterError on a
        bad filter — the caller skips the scope rather than mis-applying."""
        scope = str(scope).lower()
        fwhere, fparams = "", []
        if filter_definition and (filter_definition.get("rules") or []):
            from core.video.collections.smart_filter import compile_rules
            kind = "movie" if scope == "movie" else "show"
            fwhere, fparams = compile_rules(filter_definition, kind)
        conn = self._get_connection()
        try:
            if scope == "season":
                where = ["seasons.server_id IS NOT NULL", "seasons.server_id <> ''"]
                params = []
                if server_source:
                    where.append("shows.server_source = ?")
                    params.append(server_source)
                if fwhere:
                    where.append(fwhere)
                    params.extend(fparams)
                rows = conn.execute(
                    "SELECT seasons.id AS id, "
                    "COALESCE(shows.title,'') || ' — ' "
                    "|| COALESCE(NULLIF(seasons.title,''), 'Season ' || seasons.season_number) AS title "
                    "FROM seasons JOIN shows ON shows.id = seasons.show_id "
                    f"WHERE {' AND '.join(where)} "
                    "ORDER BY COALESCE(shows.sort_title, shows.title) COLLATE NOCASE, seasons.season_number",
                    params).fetchall()
                return [dict(r) for r in rows]
            if scope == "episode":
                where = ["episodes.server_id IS NOT NULL", "episodes.server_id <> ''"]
                params = []
                if server_source:
                    where.append("episodes.server_source = ?")
                    params.append(server_source)
                if fwhere:
                    where.append(fwhere)
                    params.extend(fparams)
                rows = conn.execute(
                    "SELECT episodes.id AS id, "
                    "COALESCE(shows.title,'') || ' — S' || episodes.season_number || 'E' || episodes.episode_number "
                    "|| CASE WHEN episodes.title IS NOT NULL AND episodes.title <> '' THEN ' ' || episodes.title ELSE '' END AS title "
                    "FROM episodes JOIN shows ON shows.id = episodes.show_id "
                    f"WHERE {' AND '.join(where)} "
                    "ORDER BY COALESCE(shows.sort_title, shows.title) COLLATE NOCASE, episodes.season_number, episodes.episode_number",
                    params).fetchall()
                return [dict(r) for r in rows]
            table = {"movie": "movies", "show": "shows"}.get(scope)
            if not table:
                return []
            where = [f"{table}.server_id IS NOT NULL", f"{table}.server_id <> ''"]
            params = []
            if server_source:
                where.append(f"{table}.server_source = ?")
                params.append(server_source)
            if fwhere:
                where.append(fwhere)
                params.extend(fparams)
            rows = conn.execute(
                f"SELECT id, title FROM {table} WHERE {' AND '.join(where)} "
                f"ORDER BY COALESCE(sort_title, title) COLLATE NOCASE", params).fetchall()
            return [dict(r) for r in rows]
        finally:
            conn.close()

    def overlay_applied_ids(self, kind: str) -> set:
        """Item ids with a live overlay ledger row for a kind — the restore pass
        diffs these against the current (filtered) targets."""
        conn = self._get_connection()
        try:
            return {r["item_id"] for r in conn.execute(
                "SELECT item_id FROM overlay_apply WHERE kind=?", (kind,))}
        except sqlite3.Error:
            return set()
        finally:
            conn.close()

    def overlay_applied_count(self, template_id=None) -> int:
        conn = self._get_connection()
        try:
            if template_id is None:
                r = conn.execute("SELECT COUNT(*) FROM overlay_apply").fetchone()
            else:
                r = conn.execute("SELECT COUNT(*) FROM overlay_apply WHERE template_id=?", (template_id,)).fetchone()
            return r[0] if r else 0
        finally:
            conn.close()

    # ── detail payloads (drill-in pages) ──────────────────────────────────────
    @staticmethod
    def _genres_for(conn, link_table: str, owner_col: str, owner_id: int) -> list:
        rows = conn.execute(
            f"SELECT g.name FROM {link_table} lt JOIN genres g ON g.id = lt.genre_id "
            f"WHERE lt.{owner_col}=? ORDER BY g.name", (owner_id,)).fetchall()
        return [r["name"] for r in rows]

    def show_detail(self, show_id: int) -> dict | None:
        """Full TV-show detail: the show + its seasons → episodes tree, with
        owned/total roll-ups. Drives the (isolated) video show-detail page."""
        conn = self._get_connection()
        try:
            show = conn.execute("SELECT * FROM shows WHERE id=?", (show_id,)).fetchone()
            if not show:
                return None
            genres = self._genres_for(conn, "show_genres", "show_id", show_id)
            credits = self._credits_for(conn, "show_id", show_id)
            seasons = conn.execute(
                "SELECT id, season_number, title, overview, "
                "(poster_url IS NOT NULL AND poster_url<>'') AS has_poster "
                "FROM seasons WHERE show_id=? ORDER BY season_number", (show_id,)).fetchall()
            eps = conn.execute(
                "SELECT id, season_number, episode_number, title, overview, air_date, "
                "runtime_minutes, rating, monitored, has_file, added_at, "
                "play_count, last_viewed_at, view_offset_ms, "
                "(still_url IS NOT NULL AND still_url<>'') AS has_still, "
                # A server may hold several COPIES of one episode — surface them.
                "(SELECT COUNT(*) FROM media_files f WHERE f.episode_id=episodes.id) AS versions, "
                "(SELECT f.resolution FROM media_files f WHERE f.episode_id=episodes.id "
                "  ORDER BY f.size_bytes DESC LIMIT 1) AS resolution "
                "FROM episodes WHERE show_id=? "
                "ORDER BY season_number, episode_number", (show_id,)).fetchall()
        finally:
            conn.close()

        by_season: dict = {}
        for e in eps:
            by_season.setdefault(e["season_number"], []).append({
                "id": e["id"], "episode_number": e["episode_number"],
                "title": e["title"], "overview": e["overview"], "air_date": e["air_date"],
                "runtime_minutes": e["runtime_minutes"], "rating": e["rating"],
                "has_still": bool(e["has_still"]),
                "monitored": bool(e["monitored"]), "owned": bool(e["has_file"]),
                "versions": e["versions"], "resolution": e["resolution"],
                # Continue Watching: server watch state (scanned per episode)
                "watched": (e["play_count"] or 0) > 0,
                "last_viewed_at": e["last_viewed_at"],
                "view_offset_ms": e["view_offset_ms"] or 0,
                "added_at": e["added_at"],   # NEW badge (freshly landed on the server)
            })

        # Seasons declared in the seasons table, plus any season numbers that only
        # exist via episodes (defensive — a show with episodes but no season row).
        season_nums = [s["season_number"] for s in seasons]
        season_meta = {s["season_number"]: s for s in seasons}
        for num in by_season:
            if num not in season_meta:
                season_nums.append(num)
        out_seasons = []
        for num in sorted(set(season_nums)):
            ep_list = by_season.get(num, [])
            owned = sum(1 for e in ep_list if e["owned"])
            meta = season_meta.get(num)
            out_seasons.append({
                "id": meta["id"] if meta else None,    # needed for the season poster proxy
                "season_number": num,
                "title": (meta["title"] if meta else None) or (
                    "Specials" if num == 0 else "Season %d" % num),
                "overview": meta["overview"] if meta else None,
                "has_poster": bool(meta["has_poster"]) if meta else False,
                "episode_total": len(ep_list),
                "episode_owned": owned,
                "episodes": ep_list,
            })

        total = len(eps)
        owned_total = sum(1 for e in eps if e["has_file"])
        return {
            "kind": "show", "id": show["id"], "title": show["title"], "year": show["year"],
            "sort_title": show["sort_title"],
            "quality_profile_id": show["quality_profile_id"] or 0,
            "series_type": show["series_type"] or "standard",
            # NULL = 'auto' (detect from the season structure). See
            # core/video/episode_numbering.
            "episode_source": show["episode_source"] or "auto",
            "locked_fields": sorted(self._parse_locked(show["locked_fields"])),
            "watched": (show["watched_episodes"] or 0) >= total > 0,
            "watched_episodes": show["watched_episodes"] or 0,   # raw count for "N of M watched"
            # Enriched-but-never-surfaced facts (detail BIC P3)
            "awards": show["awards"],
            "mediastinger": bool(show["mediastinger"] or 0),
            "overview": show["overview"], "status": show["status"], "network": show["network"],
            "content_rating": show["content_rating"], "runtime_minutes": show["runtime_minutes"],
            "tagline": show["tagline"], "rating": show["rating"],
            "first_air_date": show["first_air_date"], "last_air_date": show["last_air_date"],
            "imdb_rating": show["imdb_rating"], "rt_rating": show["rt_rating"],
            "metacritic": show["metacritic"],
            "trakt_rating": show["trakt_rating"], "trakt_votes": show["trakt_votes"],
            "tvmaze_rating": show["tvmaze_rating"],
            "anilist_score": show["anilist_score"],
            "wikidata_url": show["wikidata_url"],
            "genres": genres, "cast": credits["cast"], "crew": credits["crew"],
            "tmdb_id": show["tmdb_id"], "tvdb_id": show["tvdb_id"], "imdb_id": show["imdb_id"],
            "aka_titles": self.aka_titles_for_tmdb("show", show["tmdb_id"]),
            # The Library this show is filed under, so a grab/import started from the
            # detail page routes to ITS folder and category instead of defaulting to
            # the primary Library for the kind (an Anime show landing under TV Shows).
            "root_folder_id": show["root_folder_id"],
            "has_poster": bool(show["poster_url"]), "has_backdrop": bool(show["backdrop_url"]),
            "logo": show["logo_url"],
            "subtitle_langs": _subtitle_langs_list(show["subtitle_langs"]),
            "episodes_synced": bool(show["episodes_synced"]),
            "monitored": bool(show["monitored"]),
            "season_count": len(out_seasons),
            "episode_total": total, "episode_owned": owned_total,
            "seasons": out_seasons,
            # Continue Watching: the episode to play next (None until something
            # has actually been watched — a fresh show has no 'continue' story)
            "next_up": self.show_next_up(show_id),
        }

    def show_next_up(self, show_id: int) -> dict | None:
        """The episode to watch NEXT (the Netflix 'Next Up' slot): the most
        recently started-but-unfinished OWNED episode wins (resume), else the
        first unwatched owned episode in airing order (specials sort last).
        Returns None when nothing is owned, nothing has been watched yet, or
        everything owned is already watched — the hero CTA then stays a plain
        'Play on <server>'."""
        conn = self._get_connection()
        try:
            rows = conn.execute(
                "SELECT season_number, episode_number, title, server_id, "
                "view_offset_ms, runtime_minutes, play_count, last_viewed_at "
                "FROM episodes WHERE show_id=? AND has_file=1 "
                "ORDER BY (season_number=0), season_number, episode_number",
                (show_id,)).fetchall()
        finally:
            conn.close()
        if not rows or not any((r["play_count"] or 0) or (r["view_offset_ms"] or 0) for r in rows):
            return None    # nothing watched yet — no continue story
        started = [r for r in rows
                   if (r["view_offset_ms"] or 0) > 0 and not (r["play_count"] or 0)]
        if started:
            r = max(started, key=lambda x: x["last_viewed_at"] or "")
            resume = True
        else:
            unwatched = [r for r in rows if not (r["play_count"] or 0)]
            if not unwatched:
                return None    # all owned episodes watched
            r = unwatched[0]
            resume = False
        return {"season_number": r["season_number"], "episode_number": r["episode_number"],
                "title": r["title"], "server_id": r["server_id"],
                "view_offset_ms": r["view_offset_ms"] or 0,
                "runtime_minutes": r["runtime_minutes"], "resume": resume}

    def set_monitored(self, kind: str, item_id: int, monitored: bool) -> bool:
        """Toggle the 'follow/watchlist' flag on a movie or show. Returns True if a
        row was updated."""
        table = {"movie": "movies", "show": "shows"}.get(kind)
        if not table:
            return False
        conn = self._get_connection()
        try:
            cur = conn.execute(f"UPDATE {table} SET monitored=? WHERE id=?",
                               (1 if monitored else 0, item_id))
            conn.commit()
            return cur.rowcount > 0
        finally:
            conn.close()

    def set_watch_state(self, kind: str, item_id: int, watched: bool) -> bool:
        """Reflect a played/unplayed toggle locally (the server push happens in
        core.video.metadata) so 'watched' smart rules react without waiting for
        the next scan. Movies flip play_count; shows set watched_episodes to the
        full episode count (matching what the server does on markPlayed)."""
        conn = self._get_connection()
        try:
            if kind == "movie":
                cur = conn.execute(
                    "UPDATE movies SET play_count=CASE WHEN ? THEN MAX(COALESCE(play_count,0),1) "
                    "ELSE 0 END, view_offset_ms=0 WHERE id=?", (1 if watched else 0, item_id))
            elif kind == "show":
                cur = conn.execute(
                    "UPDATE shows SET watched_episodes=CASE WHEN ? THEN "
                    "(SELECT COUNT(*) FROM episodes WHERE show_id=shows.id) ELSE 0 END "
                    "WHERE id=?", (1 if watched else 0, item_id))
                # markPlayed/markUnplayed on a SHOW hits every episode on the
                # server — mirror that here so the per-episode watch state
                # (checkmarks, next-up) agrees without waiting for the next scan.
                conn.execute(
                    "UPDATE episodes SET play_count=CASE WHEN ? THEN "
                    "MAX(COALESCE(play_count,0),1) ELSE 0 END, view_offset_ms=0 "
                    "WHERE show_id=?", (1 if watched else 0, item_id))
            else:
                return False
            conn.commit()
            return cur.rowcount > 0
        finally:
            conn.close()

    # ── User metadata edits + field locks (Manage sidebar) ────────────────────
    # An edit writes the row AND records the field in ``locked_fields`` — from
    # then on scans (every mode) and enrichment leave it alone. Releasing the
    # lock hands the field back: the next scan re-adopts the server's value.

    def get_locked_fields(self, kind: str, item_id: int) -> list:
        table = {"movie": "movies", "show": "shows"}.get(kind)
        if not table:
            return []
        conn = self._get_connection()
        try:
            return sorted(self._locked_fields_set(conn, table, item_id))
        finally:
            conn.close()

    def set_field_lock(self, kind: str, item_id: int, field: str, locked: bool):
        """Lock/release one field. Returns the new lock list, or None for an
        unknown kind/field/item."""
        table = {"movie": "movies", "show": "shows"}.get(kind)
        if not table or field not in _USER_EDITABLE[table]:
            return None
        conn = self._get_connection()
        try:
            if not conn.execute(f"SELECT 1 FROM {table} WHERE id=?", (item_id,)).fetchone():
                return None
            locks = self._locked_fields_set(conn, table, item_id)
            locks = (locks | {field}) if locked else (locks - {field})
            conn.execute(f"UPDATE {table} SET locked_fields=? WHERE id=?",
                         (json.dumps(sorted(locks)) if locks else None, item_id))
            conn.commit()
            return sorted(locks)
        finally:
            conn.close()

    def update_item_fields(self, kind: str, item_id: int, changes: dict):
        """Apply user edits to a movie/show and auto-lock the edited fields.

        ``changes`` maps field name -> new value; fields outside _USER_EDITABLE
        raise ValueError (nothing partially applied). Editing ``title`` also
        derives ``sort_title`` unless it was provided or is already locked.
        Returns {"applied": [...], "locked": [...]} or None when the row is gone."""
        table = {"movie": "movies", "show": "shows"}.get(kind)
        if not table:
            return None
        editable = _USER_EDITABLE[table]
        bad = [f for f in changes if f not in editable]
        if bad:
            raise ValueError(f"not editable: {', '.join(sorted(bad))}")
        clean: dict = {}
        for field, value in changes.items():
            if field == "genres":
                if not isinstance(value, (list, tuple)):
                    raise ValueError("genres must be a list")
                clean[field] = [str(g).strip() for g in value if str(g or "").strip()]
            elif field == "year":
                try:
                    clean[field] = int(value) if value not in (None, "") else None
                except (TypeError, ValueError):
                    raise ValueError("year must be a number") from None
            else:
                v = "" if value is None else str(value).strip()
                if field == "title" and not v:
                    raise ValueError("title cannot be empty")
                clean[field] = v
        conn = self._get_connection()
        try:
            row = conn.execute(f"SELECT 1 FROM {table} WHERE id=?", (item_id,)).fetchone()
            if not row:
                return None
            locks = self._locked_fields_set(conn, table, item_id)
            if "title" in clean and "sort_title" not in clean and "sort_title" not in locks:
                clean["sort_title"] = _sort_title(clean["title"])
            genres = clean.pop("genres", None)
            if genres is not None:
                lt, oc = (("movie_genres", "movie_id") if table == "movies"
                          else ("show_genres", "show_id"))
                self._set_genres(conn, lt, oc, item_id, genres)
                locks.add("genres")
            if clean:
                sets = ", ".join(f"{c}=?" for c in clean)
                conn.execute(f"UPDATE {table} SET {sets}, updated_at=CURRENT_TIMESTAMP WHERE id=?",
                             (*clean.values(), item_id))
                locks.update(clean)
            conn.execute(f"UPDATE {table} SET locked_fields=? WHERE id=?",
                         (json.dumps(sorted(locks)) if locks else None, item_id))
            conn.commit()
            applied = sorted(set(clean) | ({"genres"} if genres is not None else set()))
            return {"applied": applied, "locked": sorted(locks)}
        finally:
            conn.close()

    def item_genres(self, kind: str, item_id: int) -> list:
        """Current genre names for one movie/show (bulk genre add/remove reads
        these to build the new list per item)."""
        spec = {"movie": ("movie_genres", "movie_id"), "show": ("show_genres", "show_id")}.get(kind)
        if not spec:
            return []
        conn = self._get_connection()
        try:
            return self._genres_for(conn, spec[0], spec[1], item_id)
        finally:
            conn.close()

    def item_tmdb_ids(self, kind: str, ids) -> list:
        """tmdb ids for a set of library rows (order-free, Nones dropped) —
        bulk add-to-collection maps its selection through this."""
        table = {"movie": "movies", "show": "shows"}.get(kind)
        clean = [int(i) for i in (ids or []) if str(i).lstrip("-").isdigit()]
        if not table or not clean:
            return []
        conn = self._get_connection()
        try:
            marks = ",".join("?" * len(clean))
            rows = conn.execute(
                f"SELECT DISTINCT tmdb_id FROM {table} WHERE id IN ({marks}) "
                "AND tmdb_id IS NOT NULL", clean).fetchall()
            return [r["tmdb_id"] for r in rows]
        finally:
            conn.close()

    # ── Library Maintenance: jobs & findings (mirrors music's repair standard) ─
    # Findings: pending → resolved (approve == fix) | dismissed. Dedup treats
    # EVERY status as already-seen, so a re-scan never resurrects a dismissed or
    # fixed finding. Runs record per-job tallies for the History tab.

    def repair_create_finding(self, job_id: str, finding_type: str, *, title: str,
                              severity: str = "info", entity_type=None, entity_id=None,
                              file_path=None, description=None, details=None,
                              ignore_resolved: bool = False) -> bool:
        """Insert a finding unless an equivalent one exists in ANY status —
        same job+type and (same entity OR same non-null file_path). Returns
        True only when a genuinely new row landed (music dedup semantics).

        ``ignore_resolved`` drops already-FIXED findings from that dedup basis,
        for entities that legitimately come back. A wishlist row is the case:
        once its finding is approved the row is deleted, so an entity showing up
        again is a genuinely NEW row (re-wished, re-added by a monitored show, a
        failed grab put back) — not the same situation reported twice. Dismissed
        findings still suppress: "no, leave this one alone" has to stick."""
        conn = self._get_connection()
        try:
            # Dedup basis: entity match (when the finding names one) OR same
            # non-null file_path. NULL entities never match each other — two
            # entity-less findings are only dupes via an identical path.
            conds, dparams = [], [job_id, finding_type]
            if entity_id is not None:
                conds.append("(entity_type IS ? AND entity_id=?)")
                dparams += [entity_type, str(entity_id)]
            if file_path:
                conds.append("(file_path IS NOT NULL AND file_path=?)")
                dparams.append(file_path)
            if conds:
                dup_sql = ("SELECT 1 FROM video_repair_findings WHERE job_id=? AND finding_type=? "
                           + ("AND status <> 'resolved' " if ignore_resolved else "")
                           + "AND (" + " OR ".join(conds) + ") LIMIT 1")
                if conn.execute(dup_sql, dparams).fetchone():
                    return False
            conn.execute(
                "INSERT INTO video_repair_findings (job_id, finding_type, severity, entity_type, "
                "entity_id, file_path, title, description, details_json) VALUES (?,?,?,?,?,?,?,?,?)",
                (job_id, finding_type, severity, entity_type,
                 None if entity_id is None else str(entity_id), file_path, title, description,
                 json.dumps(details or {})))
            conn.commit()
            return True
        finally:
            conn.close()

    @staticmethod
    def _finding_row(r) -> dict:
        d = dict(r)
        try:
            d["details"] = json.loads(d.pop("details_json") or "{}")
        except (ValueError, TypeError):
            d["details"] = {}
        return d

    def repair_get_findings(self, job_id=None, status=None, severity=None,
                            page: int = 1, limit: int = 50) -> dict:
        where, params = [], []
        for col, val in (("job_id", job_id), ("status", status), ("severity", severity)):
            if val:
                where.append(f"{col}=?")
                params.append(val)
        w = (" WHERE " + " AND ".join(where)) if where else ""
        page, limit = max(1, int(page or 1)), max(1, min(200, int(limit or 50)))
        conn = self._get_connection()
        try:
            total = conn.execute(f"SELECT COUNT(*) FROM video_repair_findings{w}", params).fetchone()[0]
            rows = conn.execute(
                f"SELECT * FROM video_repair_findings{w} ORDER BY created_at DESC, id DESC "
                "LIMIT ? OFFSET ?", (*params, limit, (page - 1) * limit)).fetchall()
            return {"items": [self._finding_row(r) for r in rows],
                    "total": total, "page": page, "limit": limit}
        finally:
            conn.close()

    def repair_get_finding(self, finding_id: int):
        conn = self._get_connection()
        try:
            r = conn.execute("SELECT * FROM video_repair_findings WHERE id=?",
                             (int(finding_id),)).fetchone()
            return self._finding_row(r) if r else None
        finally:
            conn.close()

    def repair_set_finding_details(self, finding_id: int, details: dict) -> bool:
        conn = self._get_connection()
        try:
            cur = conn.execute(
                "UPDATE video_repair_findings SET details_json=?, updated_at=CURRENT_TIMESTAMP "
                "WHERE id=?", (json.dumps(details or {}), int(finding_id)))
            conn.commit()
            return cur.rowcount > 0
        finally:
            conn.close()

    def repair_set_finding_status(self, finding_id: int, status: str, action=None) -> bool:
        """resolved/dismissed stamp resolved_at (+user_action on resolve)."""
        if status not in ("pending", "resolved", "dismissed"):
            return False
        conn = self._get_connection()
        try:
            stamp = ", resolved_at=CURRENT_TIMESTAMP" if status != "pending" else ", resolved_at=NULL"
            cur = conn.execute(
                f"UPDATE video_repair_findings SET status=?, user_action=?{stamp}, "
                "updated_at=CURRENT_TIMESTAMP WHERE id=?",
                (status, action, int(finding_id)))
            conn.commit()
            return cur.rowcount > 0
        finally:
            conn.close()

    def repair_bulk_update_findings(self, ids, status: str) -> int:
        if status not in ("resolved", "dismissed") or not ids:
            return 0
        clean = [int(i) for i in ids]
        conn = self._get_connection()
        try:
            marks = ",".join("?" * len(clean))
            cur = conn.execute(
                f"UPDATE video_repair_findings SET status=?, resolved_at=CURRENT_TIMESTAMP, "
                f"updated_at=CURRENT_TIMESTAMP WHERE id IN ({marks}) AND status='pending'",
                (status, *clean))
            conn.commit()
            return cur.rowcount
        finally:
            conn.close()

    def repair_clear_findings(self, job_id=None, status=None) -> int:
        where, params = [], []
        if job_id:
            where.append("job_id=?")
            params.append(job_id)
        if status:
            where.append("status=?")
            params.append(status)
        w = (" WHERE " + " AND ".join(where)) if where else ""
        conn = self._get_connection()
        try:
            cur = conn.execute(f"DELETE FROM video_repair_findings{w}", params)
            conn.commit()
            return cur.rowcount
        finally:
            conn.close()

    def repair_counts(self) -> dict:
        conn = self._get_connection()
        try:
            out = {"pending": 0, "resolved": 0, "dismissed": 0, "total": 0, "by_job": {}}
            for r in conn.execute("SELECT status, COUNT(*) n FROM video_repair_findings "
                                  "GROUP BY status").fetchall():
                out[r["status"]] = r["n"]
                out["total"] += r["n"]
            for r in conn.execute("SELECT job_id, COUNT(*) n FROM video_repair_findings "
                                  "WHERE status='pending' GROUP BY job_id").fetchall():
                out["by_job"][r["job_id"]] = r["n"]
            return out
        finally:
            conn.close()

    def repair_dismiss_absent(self, job_id: str, finding_type: str, keep_entity_ids) -> int:
        """After a COMPLETE scan: dismiss PENDING findings whose entity the scan
        no longer produced — the situation changed (set shrank, file replaced,
        row removed by hand) or resolved itself entirely. Handled findings are
        never touched, and callers must skip this on a cancelled/errored scan
        (a partial enumeration would wrongly retire live findings)."""
        keep = {str(e) for e in (keep_entity_ids or [])}
        conn = self._get_connection()
        try:
            rows = conn.execute(
                "SELECT id, entity_id FROM video_repair_findings "
                "WHERE job_id=? AND finding_type=? AND status='pending'",
                (job_id, finding_type)).fetchall()
            stale = [r["id"] for r in rows if (r["entity_id"] or "") not in keep]
            n = 0
            for i in range(0, len(stale), 500):
                chunk = stale[i:i + 500]
                marks = ",".join("?" * len(chunk))
                cur = conn.execute(
                    "UPDATE video_repair_findings SET status='dismissed', "
                    "user_action='superseded by a newer scan', resolved_at=CURRENT_TIMESTAMP, "
                    f"updated_at=CURRENT_TIMESTAMP WHERE id IN ({marks})", chunk)
                n += cur.rowcount
            conn.commit()
            return n
        finally:
            conn.close()

    def repair_sweep_stale_runs(self) -> int:
        """Close 'running' run rows left behind by a process death so the
        scheduler doesn't treat the job as forever-running."""
        conn = self._get_connection()
        try:
            cur = conn.execute(
                "UPDATE video_repair_job_runs SET status='completed', "
                "finished_at=COALESCE(finished_at, datetime('now')) WHERE status='running'")
            conn.commit()
            return cur.rowcount
        finally:
            conn.close()

    def missing_episode_rows(self, include_specials: bool = False) -> list:
        """Every aired, monitored, un-owned episode of a LIBRARY show (the
        missing-episodes job's source). Terminal-status shows are included on
        purpose — 'ended' matters for watching-for-new, not for filling gaps."""
        w = "" if include_specials else "AND e.season_number > 0 "
        conn = self._get_connection()
        try:
            rows = conn.execute(
                "SELECT e.show_id, e.season_number, e.episode_number, e.title, e.air_date, "
                "e.still_url, e.overview, s.title AS show_title, s.tmdb_id AS show_tmdb_id, "
                "s.server_source FROM episodes e JOIN shows s ON s.id = e.show_id "
                "WHERE e.has_file=0 AND e.monitored=1 AND s.server_source IS NOT NULL "
                "AND e.air_date IS NOT NULL AND e.air_date <= date('now') "
                + w +
                "ORDER BY s.title COLLATE NOCASE, e.season_number, e.episode_number").fetchall()
            return [dict(r) for r in rows]
        finally:
            conn.close()

    def video_stored_file_path(self, kind: str, *, tmdb_id=None,
                               season=None, episode=None):
        """The server-reported path (+ size) of the library's existing file for
        this item (largest first): {"path", "size_bytes"} or None when nothing
        is owned. The path is the SERVER's filesystem view — feed both through
        core.video.path_resolver (the size is the identity proof; the resolver's
        callers REPLACE what they resolve to)."""
        if not tmdb_id:
            return None
        conn = self._get_connection()
        try:
            if kind == "movie":
                r = conn.execute(
                    "SELECT f.relative_path, f.size_bytes FROM media_files f "
                    "JOIN movies m ON m.id = f.movie_id "
                    "WHERE m.tmdb_id=? AND m.has_file=1 AND f.relative_path<>'' "
                    "ORDER BY f.size_bytes DESC LIMIT 1", (int(tmdb_id),)).fetchone()
            elif season is not None and episode is not None:
                r = conn.execute(
                    "SELECT f.relative_path, f.size_bytes FROM media_files f "
                    "JOIN episodes e ON e.id = f.episode_id "
                    "JOIN shows s ON s.id = e.show_id "
                    "WHERE s.tmdb_id=? AND e.season_number=? AND e.episode_number=? "
                    "AND e.has_file=1 AND f.relative_path<>'' "
                    "ORDER BY f.size_bytes DESC LIMIT 1",
                    (int(tmdb_id), int(season), int(episode))).fetchone()
            else:
                return None
            return ({"path": r["relative_path"], "size_bytes": r["size_bytes"]}
                    if r else None)
        except (sqlite3.Error, ValueError, TypeError):
            return None
        finally:
            conn.close()

    # ── repair-job source queries (movie-side jobs) ───────────────────────────
    def repair_movie_franchises(self) -> dict:
        """Owned movies grouped by TMDB collection:
        {collection_id: {"name", "movies": [{library_id, tmdb_id, title, year}]}}."""
        conn = self._get_connection()
        try:
            rows = conn.execute(
                "SELECT id, tmdb_id, title, year, tmdb_collection_id, tmdb_collection_name "
                "FROM movies WHERE has_file=1 AND tmdb_collection_id IS NOT NULL "
                "AND tmdb_id IS NOT NULL ORDER BY year, title").fetchall()
        finally:
            conn.close()
        out: dict = {}
        for r in rows:
            g = out.setdefault(r["tmdb_collection_id"],
                               {"name": r["tmdb_collection_name"], "movies": []})
            if not g["name"] and r["tmdb_collection_name"]:
                g["name"] = r["tmdb_collection_name"]
            g["movies"].append({"library_id": r["id"], "tmdb_id": r["tmdb_id"],
                                "title": r["title"], "year": r["year"]})
        return out

    def repair_owned_movie_files(self, movie_id=None) -> list:
        """Every owned movie with each of its files (quality/runtime checks).

        ``movie_id`` narrows it to one title — the per-title rename preview on
        the movie page. None (the default) is every owned movie, exactly as
        every existing caller expects."""
        where, args = "m.has_file=1", []
        if movie_id is not None:
            where += " AND m.id=?"
            args.append(int(movie_id))
        conn = self._get_connection()
        try:
            rows = conn.execute(
                "SELECT m.id AS movie_id, m.tmdb_id, m.imdb_id, m.title, m.year, "
                "m.runtime_minutes, "
                "f.id AS file_id, f.relative_path, f.size_bytes, f.resolution, f.quality, "
                "f.video_codec, f.audio_codec, f.release_source, f.runtime_seconds, "
                # See rename_owned_episode_files — the MediaInfo a {Token}
                # rename needs so it doesn't propose stripping the filename.
                "f.audio_channels, f.dynamic_range "
                "FROM movies m JOIN media_files f ON f.movie_id = m.id "
                f"WHERE {where} ORDER BY m.title COLLATE NOCASE, f.size_bytes DESC",
                args).fetchall()
            return [dict(r) for r in rows]
        finally:
            conn.close()

    def rename_owned_episode_files(self, show_id=None) -> list:
        """Every owned episode file with the show/episode context the naming
        templates need (mass rename, P7).

        ``show_id`` narrows it to one series — the per-show rename preview on
        the show page. None (the default) is the whole library, unchanged."""
        where, args = "e.has_file=1", []
        if show_id is not None:
            where += " AND s.id=?"
            args.append(int(show_id))
        conn = self._get_connection()
        try:
            rows = conn.execute(
                "SELECT e.id AS episode_id, e.season_number, e.episode_number, "
                "e.title AS episode_title, s.title AS show_title, "
                # $year for episode templates: stored year, else the premiere
                # year from first_air_date (enrichment fills that even when the
                # server omits ProductionYear) — heals existing NULL-year rows
                # without waiting for a deep scan (musicagine).
                "COALESCE(s.year, CAST(substr(NULLIF(s.first_air_date, ''), 1, 4) AS INTEGER)) AS show_year, "
                "f.id AS file_id, f.relative_path, f.size_bytes, f.resolution, f.quality, "
                "f.video_codec, f.release_source, "
                # MediaInfo + ids for the Sonarr/Radarr {Token} names. Without
                # these a rename recomputes the file's name WITHOUT its audio,
                # dynamic range or release group and proposes deleting them.
                "f.audio_codec, f.audio_channels, f.dynamic_range, "
                "s.tvdb_id, s.tmdb_id, s.imdb_id, e.air_date "
                "FROM episodes e JOIN shows s ON s.id = e.show_id "
                "JOIN media_files f ON f.episode_id = e.id "
                f"WHERE {where} "
                "ORDER BY s.title COLLATE NOCASE, e.season_number, e.episode_number",
                args).fetchall()
            return [dict(r) for r in rows]
        finally:
            conn.close()

    def media_file_stored_path(self, file_id) -> str | None:
        conn = self._get_connection()
        try:
            row = conn.execute("SELECT relative_path FROM media_files WHERE id=?",
                               (int(file_id),)).fetchone()
            return row["relative_path"] if row else None
        except (sqlite3.Error, TypeError, ValueError):
            return None
        finally:
            conn.close()

    def set_media_file_stored_path(self, file_id, new_path) -> bool:
        conn = self._get_connection()
        try:
            cur = conn.execute("UPDATE media_files SET relative_path=? WHERE id=?",
                               (str(new_path), int(file_id)))
            conn.commit()
            return cur.rowcount > 0
        except (sqlite3.Error, TypeError, ValueError):
            logger.exception("set_media_file_stored_path failed")
            return False
        finally:
            conn.close()

    def repair_movie_metadata_gaps(self) -> list:
        """Owned movies with enrichment gaps: unmatched, or missing overview /
        genres / poster / backdrop. Returns the raw signals; the job decides
        (user-locked blank fields are deliberate and get filtered there)."""
        conn = self._get_connection()
        try:
            rows = conn.execute(
                "SELECT m.id AS movie_id, m.tmdb_id, m.title, m.year, m.tmdb_match_status, "
                "m.locked_fields, "
                "(m.overview IS NULL OR m.overview='') AS no_overview, "
                "(m.poster_url IS NULL OR m.poster_url='') AS no_poster, "
                "(m.backdrop_url IS NULL OR m.backdrop_url='') AS no_backdrop, "
                "NOT EXISTS (SELECT 1 FROM movie_genres mg WHERE mg.movie_id=m.id) AS no_genres "
                "FROM movies m WHERE m.has_file=1 "
                "AND (m.tmdb_match_status IS NULL OR m.tmdb_match_status<>'matched' "
                "     OR m.overview IS NULL OR m.overview='' "
                "     OR m.poster_url IS NULL OR m.poster_url='' "
                "     OR m.backdrop_url IS NULL OR m.backdrop_url='' "
                "     OR NOT EXISTS (SELECT 1 FROM movie_genres mg WHERE mg.movie_id=m.id)) "
                "ORDER BY m.title COLLATE NOCASE").fetchall()
            return [dict(r) for r in rows]
        finally:
            conn.close()

    def repair_duplicate_movies(self) -> dict:
        """Duplicate signals: {"rows": [[movie,…] same tmdb_id twice],
        "files": [{movie + its 2+ files}]} — both owned-only."""
        conn = self._get_connection()
        try:
            dup_rows = conn.execute(
                "SELECT m.id, m.tmdb_id, m.title, m.year, m.server_source, m.path "
                "FROM movies m WHERE m.has_file=1 AND m.tmdb_id IN ("
                "  SELECT tmdb_id FROM movies WHERE has_file=1 AND tmdb_id IS NOT NULL "
                "  GROUP BY tmdb_id HAVING COUNT(*)>1) ORDER BY m.tmdb_id, m.id").fetchall()
            multi = conn.execute(
                "SELECT m.id AS movie_id, m.tmdb_id, m.title, m.year, "
                "f.id AS file_id, f.relative_path, f.size_bytes, f.resolution, f.quality, "
                "f.video_codec FROM movies m JOIN media_files f ON f.movie_id=m.id "
                "WHERE m.has_file=1 AND m.id IN ("
                "  SELECT movie_id FROM media_files WHERE movie_id IS NOT NULL "
                "  GROUP BY movie_id HAVING COUNT(*)>1) "
                "ORDER BY m.id, f.size_bytes DESC").fetchall()
        finally:
            conn.close()
        by_tmdb: dict = {}
        for r in dup_rows:
            by_tmdb.setdefault(r["tmdb_id"], []).append(dict(r))
        by_movie: dict = {}
        for r in multi:
            by_movie.setdefault(r["movie_id"], []).append(dict(r))
        return {"rows": list(by_tmdb.values()), "files": list(by_movie.values())}

    def repair_watched_movies(self) -> list:
        """Every OWNED movie with its watch state + largest file — the
        watched-cleanup job's source (age filtering happens in the job)."""
        conn = self._get_connection()
        try:
            return [dict(r) for r in conn.execute(
                "SELECT m.id AS movie_id, m.tmdb_id, m.title, m.year, "
                "m.play_count, m.last_viewed_at, m.added_at, "
                "f.relative_path, f.size_bytes "
                "FROM movies m JOIN media_files f ON f.movie_id = m.id "
                "WHERE m.has_file = 1 "
                "AND f.id = (SELECT f2.id FROM media_files f2 WHERE f2.movie_id = m.id "
                "            ORDER BY f2.size_bytes DESC LIMIT 1)")]
        finally:
            conn.close()

    def movie_file_paths(self, movie_id) -> list:
        """Every stored file for a movie ({relative_path, size_bytes}), largest
        first — so a cleanup recycles ALL versions of a multi-part movie, not
        just the biggest one (which would orphan the rest)."""
        conn = self._get_connection()
        try:
            return [dict(r) for r in conn.execute(
                "SELECT relative_path, size_bytes FROM media_files "
                "WHERE movie_id=? ORDER BY size_bytes DESC", (int(movie_id),))]
        finally:
            conn.close()

    def repair_mark_movie_fileless(self, movie_id) -> None:
        """After a cleanup fix recycled the movie's file: reflect reality now
        (has_file=0, file rows gone) instead of waiting for the weekly deep
        scan — exactly the state that scan would find."""
        conn = self._get_connection()
        try:
            conn.execute("DELETE FROM media_files WHERE movie_id=?", (int(movie_id),))
            conn.execute("UPDATE movies SET has_file=0, updated_at=datetime('now') WHERE id=?",
                         (int(movie_id),))
            conn.commit()
        finally:
            conn.close()

    def repair_library_files(self) -> list:
        """Every owned movie/episode file with the fields the naming templates
        need — the naming-conformance job's source.

        Must select the SAME columns as repair_owned_movie_files /
        rename_owned_episode_files. It did not, and the gap was not cosmetic:
        episodes carried a hardcoded ``NULL AS year`` and neither scope carried
        the MediaInfo columns, so this job rendered a strictly poorer name than
        the Rename Files preview did for the very same file — no series year, no
        audio codec or channels, no dynamic range, no release-group ids. Against
        a template that asks for them, a correctly-named file looked wrong and
        approving the "fix" would have stripped that detail off disk.
        """
        conn = self._get_connection()
        try:
            movies = [dict(r) for r in conn.execute(
                "SELECT 'movie' AS scope, m.id AS item_id, f.id AS file_id, "
                "m.title, m.year, m.tmdb_id, m.imdb_id, "
                "NULL AS season, NULL AS episode, NULL AS air_date, "
                "NULL AS episode_title, NULL AS series, NULL AS tvdb_id, "
                "f.relative_path, f.size_bytes, f.quality, f.resolution, f.video_codec, "
                "f.audio_codec, f.audio_channels, f.dynamic_range, f.release_source "
                "FROM movies m JOIN media_files f ON f.movie_id = m.id "
                "WHERE m.has_file = 1")]
            eps = [dict(r) for r in conn.execute(
                "SELECT 'episode' AS scope, e.id AS item_id, f.id AS file_id, "
                "s.title AS title, s.tmdb_id, s.tvdb_id, s.imdb_id, "
                # Same COALESCE as rename_owned_episode_files: the premiere year
                # stands in when the server never sent ProductionYear, so
                # {(Series Year)} resolves instead of silently vanishing.
                "COALESCE(s.year, CAST(substr(NULLIF(s.first_air_date, ''), 1, 4) AS INTEGER)) AS year, "
                "e.season_number AS season, e.episode_number AS episode, e.air_date, "
                "e.title AS episode_title, s.title AS series, "
                "f.relative_path, f.size_bytes, f.quality, f.resolution, f.video_codec, "
                "f.audio_codec, f.audio_channels, f.dynamic_range, f.release_source "
                "FROM episodes e JOIN shows s ON e.show_id = s.id "
                "JOIN media_files f ON f.episode_id = e.id "
                "WHERE e.has_file = 1")]
            return movies + eps
        finally:
            conn.close()

    def repair_stale_wishlist(self) -> list:
        """Wishlist rows whose target is ALREADY OWNED, annotated with the owned
        files' resolutions — the audit job judges them against the cutoff:
        owned-below-cutoff rows are LEGITIMATE (upgrade-until keeps them for a
        better copy); owned-at-cutoff and quality-unreadable rows are clutter.

        "Owned" matches on tmdb_id OR the row's own ``library_id`` (movies.id /
        shows.id — the same meaning the to_download queries give it). tmdb_id
        alone missed a copy sitting in a library row that never got TMDB-matched,
        which is exactly the row a user has downloaded, imported, and can see on
        their server while the audit reported nothing to clean."""
        conn = self._get_connection()
        try:
            movies = conn.execute(
                "SELECT w.id AS wishlist_id, 'movie' AS kind, w.tmdb_id, w.title, "
                "w.poster_url, NULL AS season_number, NULL AS episode_number, "
                "(SELECT m.id FROM movies m WHERE (m.tmdb_id=w.tmdb_id OR m.id=w.library_id) "
                "  AND m.has_file=1 LIMIT 1) AS library_id, "
                "(SELECT GROUP_CONCAT(f.resolution) FROM movies m "
                "  JOIN media_files f ON f.movie_id=m.id "
                "  WHERE (m.tmdb_id=w.tmdb_id OR m.id=w.library_id) "
                "  AND m.has_file=1) AS owned_resolutions "
                "FROM video_wishlist w WHERE w.kind='movie' "
                "AND EXISTS (SELECT 1 FROM movies m WHERE (m.tmdb_id=w.tmdb_id OR m.id=w.library_id) "
                "  AND m.has_file=1)").fetchall()
            eps = conn.execute(
                "SELECT w.id AS wishlist_id, 'episode' AS kind, w.tmdb_id, w.title, "
                "w.poster_url, w.season_number, w.episode_number, "
                # the show row to link the finding at — the wishlist row's own
                # when it has one, else whichever owns the episode.
                "COALESCE(w.library_id, (SELECT s.id FROM shows s "
                "  JOIN episodes e ON e.show_id=s.id "
                "  WHERE s.tmdb_id=w.tmdb_id AND e.season_number=w.season_number "
                "  AND e.episode_number=w.episode_number AND e.has_file=1 LIMIT 1)) "
                "AS library_id, "
                "(SELECT GROUP_CONCAT(f.resolution) FROM episodes e "
                "  JOIN shows s ON e.show_id=s.id "
                "  JOIN media_files f ON f.episode_id=e.id "
                "  WHERE (s.tmdb_id=w.tmdb_id OR s.id=w.library_id) "
                "  AND e.season_number=w.season_number "
                "  AND e.episode_number=w.episode_number AND e.has_file=1) "
                "  AS owned_resolutions "
                "FROM video_wishlist w WHERE w.kind='episode' "
                "AND EXISTS (SELECT 1 FROM episodes e JOIN shows s ON e.show_id=s.id "
                "  WHERE (s.tmdb_id=w.tmdb_id OR s.id=w.library_id) "
                "  AND e.season_number=w.season_number "
                "  AND e.episode_number=w.episode_number AND e.has_file=1)").fetchall()
            return [dict(r) for r in movies] + [dict(r) for r in eps]
        finally:
            conn.close()

    def repair_record_job_start(self, job_id: str) -> int:
        conn = self._get_connection()
        try:
            cur = conn.execute(
                "INSERT INTO video_repair_job_runs (job_id, started_at) "
                "VALUES (?, datetime('now'))", (job_id,))
            conn.commit()
            return cur.lastrowid
        finally:
            conn.close()

    def repair_record_job_finish(self, run_id: int, *, items_scanned=0, findings_created=0,
                                 auto_fixed=0, errors=0) -> None:
        conn = self._get_connection()
        try:
            conn.execute(
                "UPDATE video_repair_job_runs SET finished_at=datetime('now'), "
                "duration_seconds=(julianday(datetime('now'))-julianday(started_at))*86400, "
                "items_scanned=?, findings_created=?, auto_fixed=?, errors=?, "
                "status='completed' WHERE id=?",
                (int(items_scanned), int(findings_created), int(auto_fixed),
                 int(errors), int(run_id)))
            conn.commit()
        finally:
            conn.close()

    def repair_last_run(self, job_id: str):
        conn = self._get_connection()
        try:
            r = conn.execute(
                "SELECT * FROM video_repair_job_runs WHERE job_id=? "
                "ORDER BY started_at DESC, id DESC LIMIT 1", (job_id,)).fetchone()
            return dict(r) if r else None
        finally:
            conn.close()

    def repair_history(self, job_id=None, limit: int = 50) -> list:
        conn = self._get_connection()
        try:
            if job_id:
                rows = conn.execute(
                    "SELECT * FROM video_repair_job_runs WHERE job_id=? "
                    "ORDER BY started_at DESC, id DESC LIMIT ?", (job_id, int(limit))).fetchall()
            else:
                rows = conn.execute(
                    "SELECT * FROM video_repair_job_runs ORDER BY started_at DESC, id DESC "
                    "LIMIT ?", (int(limit),)).fetchall()
            return [dict(r) for r in rows]
        finally:
            conn.close()

    # ── Issues: user-reported problems on library items (music standard) ──────
    # Lifecycle open → in_progress → resolved | dismissed (reopenable). Resolved
    # issues are RETAINED. Non-admin reads are force-scoped to the caller.

    _ISSUE_UPDATABLE = {"status", "priority", "admin_response", "resolved_by",
                        "resolved_at", "title", "description", "category"}

    def create_issue(self, profile_id: int, entity_type: str, entity_id, category: str,
                     title: str, description: str = "", snapshot_data=None,
                     priority: str = "normal", reporter_name=None) -> int:
        conn = self._get_connection()
        try:
            cur = conn.execute(
                "INSERT INTO video_issues (profile_id, reporter_name, entity_type, entity_id, "
                "category, title, description, snapshot_data, priority) "
                "VALUES (?,?,?,?,?,?,?,?,?)",
                (int(profile_id), reporter_name, entity_type, str(entity_id), category,
                 title, description or "", json.dumps(snapshot_data or {}),
                 priority if priority in ("low", "normal", "high") else "normal"))
            conn.commit()
            return cur.lastrowid
        finally:
            conn.close()

    @staticmethod
    def _issue_row(r) -> dict:
        d = dict(r)
        try:
            d["snapshot_data"] = json.loads(d.get("snapshot_data") or "{}")
        except (ValueError, TypeError):
            d["snapshot_data"] = {}
        return d

    def get_issues(self, profile_id: int, status=None, category=None, entity_type=None,
                   limit: int = 100, offset: int = 0, is_admin: bool = False) -> list:
        """Open/urgent first (music ordering): open, in_progress, then the rest;
        high priority first inside each; newest first. Non-admin sees own only."""
        where, params = ["1=1"], []
        if not is_admin:
            where.append("profile_id=?")
            params.append(int(profile_id))
        for col, val in (("status", status), ("category", category),
                         ("entity_type", entity_type)):
            if val and val != "all":
                where.append(f"{col}=?")
                params.append(val)
        conn = self._get_connection()
        try:
            rows = conn.execute(
                "SELECT * FROM video_issues WHERE " + " AND ".join(where) +
                " ORDER BY CASE status WHEN 'open' THEN 0 WHEN 'in_progress' THEN 1 ELSE 2 END, "
                "CASE priority WHEN 'high' THEN 0 WHEN 'normal' THEN 1 ELSE 2 END, "
                "created_at DESC LIMIT ? OFFSET ?",
                (*params, max(1, min(200, int(limit or 100))), max(0, int(offset or 0)))
            ).fetchall()
            return [self._issue_row(r) for r in rows]
        finally:
            conn.close()

    def get_issue(self, issue_id: int):
        conn = self._get_connection()
        try:
            r = conn.execute("SELECT * FROM video_issues WHERE id=?",
                             (int(issue_id),)).fetchone()
            return self._issue_row(r) if r else None
        finally:
            conn.close()

    def update_issue(self, issue_id: int, updates: dict) -> bool:
        fields = {k: v for k, v in (updates or {}).items() if k in self._ISSUE_UPDATABLE}
        if not fields:
            return False
        sets = ", ".join(f"{k}=?" for k in fields)
        conn = self._get_connection()
        try:
            cur = conn.execute(
                f"UPDATE video_issues SET {sets}, updated_at=CURRENT_TIMESTAMP WHERE id=?",
                (*fields.values(), int(issue_id)))
            conn.commit()
            return cur.rowcount > 0
        finally:
            conn.close()

    def delete_issue(self, issue_id: int) -> bool:
        conn = self._get_connection()
        try:
            cur = conn.execute("DELETE FROM video_issues WHERE id=?", (int(issue_id),))
            conn.commit()
            return cur.rowcount > 0
        finally:
            conn.close()

    def get_issue_counts(self, is_admin: bool = False, profile_id: int = 1) -> dict:
        w, params = ("", []) if is_admin else (" WHERE profile_id=?", [int(profile_id)])
        conn = self._get_connection()
        try:
            out = {"open": 0, "in_progress": 0, "resolved": 0, "dismissed": 0, "total": 0}
            for r in conn.execute(
                    f"SELECT status, COUNT(*) n FROM video_issues{w} GROUP BY status", params):
                if r["status"] in out:
                    out[r["status"]] = r["n"]
                out["total"] += r["n"]
            return out
        finally:
            conn.close()

    # ── User watchlist (curated follow-list: shows + people) ──────────────────
    # Mirrors the music watchlist_artists model: an explicit follow-list that may
    # include shows/people not in the library yet. Keyed on (kind, tmdb_id). The
    # monitoring/discovery engine is a later phase — these just manage membership.
    # An actively-airing library show (status present and not finished) is on the
    # watchlist BY DEFAULT — owning a still-running show means you want its new
    # episodes. The `state` rows store only explicit user decisions; this default
    # is computed at read time so it always tracks the library + a show's status.
    _ACTIVE_SHOW_SQL = ("status IS NOT NULL AND TRIM(status) <> '' "
                        "AND LOWER(status) NOT IN ('ended', 'canceled', 'cancelled', 'completed')")

    def add_to_watchlist(self, kind: str, tmdb_id: int, title: str,
                         poster_url: str | None = None, library_id: int | None = None,
                         approved: bool = True, requested_by=None,
                         requested_by_name=None, monitor=None) -> bool:
        """Explicitly follow a show/person/studio (state='follow'). Idempotent upsert on
        (kind, tmdb_id) — re-adding refreshes title/poster/library_id and clears
        any 'mute' tombstone. Returns True on success.

        ``approved=False`` files the follow as awaiting admin approval: it appears
        on the watchlist right away but no acquisition path acts on it. The upsert
        deliberately uses MAX() on approved, so a later pending re-add can never
        DOWNGRADE a follow an admin already approved (nor can a member revoke one
        by re-adding it). requested_by/name and monitor are recorded only when the
        row is created pending, and are cleared on approval."""
        if kind not in ("show", "person", "studio") or not tmdb_id or not title:
            return False
        conn = self._get_connection()
        try:
            was = conn.execute("SELECT state FROM video_watchlist WHERE kind=? AND tmdb_id=?",
                               (kind, int(tmdb_id))).fetchone()
            conn.execute(
                """INSERT INTO video_watchlist (kind, tmdb_id, title, poster_url, library_id, state,
                                                approved, requested_by, requested_by_name, monitor)
                   VALUES (?, ?, ?, ?, ?, 'follow', ?, ?, ?, ?)
                   ON CONFLICT(kind, tmdb_id) DO UPDATE SET
                       state='follow', title=excluded.title,
                       poster_url=COALESCE(excluded.poster_url, video_watchlist.poster_url),
                       library_id=COALESCE(excluded.library_id, video_watchlist.library_id),
                       approved=MAX(video_watchlist.approved, excluded.approved),
                       requested_by=COALESCE(video_watchlist.requested_by, excluded.requested_by),
                       requested_by_name=COALESCE(video_watchlist.requested_by_name,
                                                  excluded.requested_by_name),
                       monitor=COALESCE(video_watchlist.monitor, excluded.monitor)""",
                (kind, int(tmdb_id), title, _proxied_art(poster_url), library_id,
                 1 if approved else 0, requested_by, requested_by_name, monitor))
            conn.commit()
            # A refresh-upsert of an existing follow is not a new follow.
            if not (was and was["state"] == "follow"):
                _publish_video_event("video_watchlist_added", {"kind": kind, "title": title})
                # The watchlist has had an approval queue since 1.6.7 and has
                # never been able to tell anyone about it.
                if not approved:
                    _publish_video_event("video_request_pending", {
                        "kind": kind, "title": title, "count": 1,
                        "queue": "watchlist", "tmdb_id": int(tmdb_id),
                        "requested_by": requested_by_name})
            return True
        except Exception:
            logger.exception("add_to_watchlist failed (%s %s)", kind, tmdb_id)
            return False
        finally:
            conn.close()

    def approve_watchlist_entry(self, kind: str, tmdb_id: int) -> dict | None:
        """Approve a pending follow so acquisition can act on it. Returns the row
        as it was BEFORE the flip (carrying the requester's ``monitor`` choice, so
        the caller can expand the back catalog they asked for), or None when the
        entry is unknown or already approved — which makes double-approval a
        no-op rather than a second round of wishlist writes."""
        if kind not in ("show", "person", "studio") or not tmdb_id:
            return None
        conn = self._get_connection()
        try:
            row = conn.execute(
                "SELECT * FROM video_watchlist WHERE kind=? AND tmdb_id=? AND approved=0",
                (kind, int(tmdb_id))).fetchone()
            if not row:
                return None
            conn.execute("UPDATE video_watchlist SET approved=1 WHERE id=?", (row["id"],))
            conn.commit()
            return dict(row)
        except Exception:
            logger.exception("approve_watchlist_entry failed (%s %s)", kind, tmdb_id)
            return None
        finally:
            conn.close()

    def pending_watchlist_entries(self, requested_by=None) -> list[dict]:
        """Follows awaiting approval, newest first. ``requested_by`` scopes to one
        profile's own asks (what a member is allowed to see)."""
        sql = ("SELECT id, kind, tmdb_id, title, poster_url, library_id, monitor, "
               "requested_by, requested_by_name, date_added "
               "FROM video_watchlist WHERE approved=0 AND state='follow'")
        args: list = []
        if requested_by is not None:
            sql += " AND requested_by=?"
            args.append(int(requested_by))
        sql += " ORDER BY date_added DESC, id DESC"
        conn = self._get_connection()
        try:
            return [dict(r) for r in conn.execute(sql, args)]
        finally:
            conn.close()

    def pending_watchlist_count(self, requested_by=None) -> int:
        """How many follows are awaiting approval — the nav badge."""
        sql = "SELECT COUNT(*) c FROM video_watchlist WHERE approved=0 AND state='follow'"
        args: list = []
        if requested_by is not None:
            sql += " AND requested_by=?"
            args.append(int(requested_by))
        conn = self._get_connection()
        try:
            return conn.execute(sql, args).fetchone()["c"]
        except Exception:
            return 0
        finally:
            conn.close()

    def watchlist_entry_requester(self, kind: str, tmdb_id: int):
        """(approved, requested_by) for one follow, or None when there's no row —
        so an endpoint can tell an admin's live follow from a member's own pending
        ask before allowing a removal."""
        if kind not in ("show", "person", "studio") or not tmdb_id:
            return None
        conn = self._get_connection()
        try:
            row = conn.execute(
                "SELECT approved, requested_by FROM video_watchlist WHERE kind=? AND tmdb_id=?",
                (kind, int(tmdb_id))).fetchone()
            return (row["approved"], row["requested_by"]) if row else None
        finally:
            conn.close()

    def followed_shows(self) -> list[dict]:
        """Explicitly-followed shows (state='follow') with their library status when
        owned (NULL for tmdb-only follows). Used by the watchlist-prune pass to drop
        shows that have since ended/been canceled."""
        conn = self._get_connection()
        try:
            return [dict(r) for r in conn.execute(
                "SELECT w.tmdb_id, w.title, w.library_id, s.status "
                "FROM video_watchlist w LEFT JOIN shows s ON s.id = w.library_id "
                "WHERE w.kind='show' AND w.state='follow'")]
        finally:
            conn.close()

    def watchlist_continuing_shows(self, server_source=None) -> list[dict]:
        """Effective-watchlist shows that are IN the library (so they have an episodes table
        to refresh) and still airing — the set whose TMDB episode schedules the 'Refresh
        Airing TV Schedules' automation re-pulls so the calendar stays current. Skips
        tmdb-only follows (no episodes to refresh) and ended/canceled shows (no new episodes
        coming); unknown status is kept (never skip on uncertainty)."""
        terminal = ("ended", "canceled", "cancelled", "completed")
        conn = self._get_connection()
        try:
            rows = self._effective_shows(conn, server_source)
        finally:
            conn.close()
        out, seen = [], set()
        for r in rows:
            lib = r.get("library_id")
            if lib is None or lib in seen:
                continue
            if str(r.get("status") or "").strip().lower() in terminal:
                continue
            seen.add(lib)
            out.append({"library_id": lib, "tmdb_id": r.get("tmdb_id"),
                        "title": r.get("title"), "status": r.get("status")})
        return out

    def remove_from_watchlist(self, kind: str, tmdb_id: int) -> bool:
        """Un-follow. Stored as a 'mute' tombstone (not a delete) so an
        actively-airing library show — watched by default — is not silently
        re-added. Returns True."""
        if kind not in ("show", "person", "studio") or not tmdb_id:
            return False
        conn = self._get_connection()
        try:
            was = conn.execute("SELECT state, title FROM video_watchlist WHERE kind=? AND tmdb_id=?",
                               (kind, int(tmdb_id))).fetchone()
            conn.execute(
                """INSERT INTO video_watchlist (kind, tmdb_id, title, state)
                   VALUES (?, ?, '', 'mute')
                   ON CONFLICT(kind, tmdb_id) DO UPDATE SET state='mute'""",
                (kind, int(tmdb_id)))
            conn.commit()
            # Only an actual follow → mute transition is an unfollow event
            # (muting something never followed is just a tombstone write).
            if was and was["state"] == "follow":
                _publish_video_event("video_watchlist_removed",
                                     {"kind": kind, "title": was["title"] or ""})
            return True
        finally:
            conn.close()

    # owned/total episode counts — joined off s.id in both queries below.
    _EPS_COLS = ("(SELECT COUNT(*) FROM episodes e WHERE e.show_id=s.id) AS episode_count, "
                 "(SELECT COUNT(*) FROM episodes e WHERE e.show_id=s.id AND e.has_file=1) AS owned_count")

    def _effective_shows(self, conn, server_source, approved_only: bool = False) -> list[dict]:
        """Explicit show follows ∪ actively-airing library shows (not muted),
        each carrying status + owned/total episode counts for the card chrome.

        Pending follows are INCLUDED by default and carry ``approved``/requester
        fields — the watchlist page shows them so the requester can see their own
        ask. ``approved_only`` is for acquisition callers, which must not act on
        an entry an admin hasn't cleared. The default arm (airing library shows)
        is always approved: those are already-owned shows, not requests."""
        out, seen = [], set()
        wl_where = " AND w.approved=1" if approved_only else ""
        # Resolve the LIVE library row instead of trusting w.library_id. Show rows
        # are deleted and re-inserted when Plex re-keys an item (show_sync.py), and
        # a follow added before the show was scanned never had an id at all — both
        # strand the stored id, and with it the status, the episode counts and the
        # '/api/video/poster/show/<id>' the follow was saved with.
        heal, args = "", []
        if server_source:
            heal = " AND s2.server_source = ?"
            args.append(server_source)
        for r in conn.execute(
                "SELECT w.tmdb_id, w.title, w.poster_url AS saved_poster, w.date_added, "
                "s.id AS library_id, s.status, "
                "(s.poster_url IS NOT NULL AND s.poster_url <> '') AS lib_has_poster, "
                "w.approved, w.requested_by, w.requested_by_name, w.monitor, "
                # The Library this follow is aimed at — the card's picker renders
                # its CURRENT state from this, and a control that cannot show what
                # it is set to reads as if it were never set.
                "w.root_folder_id, "
                + self._EPS_COLS +
                " FROM video_watchlist w LEFT JOIN shows s ON s.id = COALESCE("
                "(SELECT s1.id FROM shows s1 WHERE s1.id = w.library_id), "
                "(SELECT s2.id FROM shows s2 WHERE s2.tmdb_id = w.tmdb_id" + heal +
                " ORDER BY s2.id LIMIT 1)) "
                "WHERE w.kind='show' AND w.state='follow'" + wl_where +
                " ORDER BY w.date_added DESC, w.id DESC", args):
            d = dict(r); d["kind"] = "show"
            d["approved"] = bool(d.get("approved", 1))
            # An owned show's art comes from its library row: that's live, and it's
            # what the poster manager edits. The follow's own poster_url is a
            # snapshot taken when it was added, so it is only ever the fallback.
            lib_art, saved = d.pop("lib_has_poster", 0), d.pop("saved_poster", None)
            if d["library_id"] and lib_art:
                d["poster_url"] = "/api/video/poster/show/%d" % d["library_id"]
            elif str(saved or "").startswith("/api/video/poster/"):
                # The follow saved a library-art path but no live row backs it, so
                # that URL 404s. Report no art rather than making the card fire a
                # request it is guaranteed to lose before it paints its placeholder.
                d["poster_url"] = None
            else:
                d["poster_url"] = _proxied_art(saved)
            out.append(d); seen.add(r["tmdb_id"])
        muted = {r["tmdb_id"] for r in conn.execute(
            "SELECT tmdb_id FROM video_watchlist WHERE kind='show' AND state='mute'")}
        sql = ("SELECT s.tmdb_id, s.title, s.id AS library_id, s.status, " + self._EPS_COLS +
               " FROM shows s WHERE s.tmdb_id IS NOT NULL AND " + self._ACTIVE_SHOW_SQL)
        args: list = []
        if server_source:
            sql += " AND s.server_source = ?"; args.append(server_source)
        sql += " ORDER BY COALESCE(s.sort_title, s.title) COLLATE NOCASE"
        for r in conn.execute(sql, args):
            tid = r["tmdb_id"]
            if tid in seen or tid in muted:
                continue
            seen.add(tid)
            out.append({"kind": "show", "tmdb_id": tid, "title": r["title"],
                        "poster_url": "/api/video/poster/show/%d" % r["library_id"],
                        "library_id": r["library_id"], "status": r["status"],
                        "episode_count": r["episode_count"], "owned_count": r["owned_count"],
                        # No watchlist row exists for this arm, so there is no
                        # recorded intent — carried as None rather than omitted so
                        # every show row has the same shape. These all have a
                        # library_id anyway: the show is already filed, and its
                        # detail page reassigns it for real.
                        "root_folder_id": None,
                        "date_added": None, "auto": True})
        return out

    def list_watchlist(self, kind: str | None = None, server_source=None,
                       approved_only: bool = False) -> list[dict]:
        """Effective watchlist. Shows include the airing-library default; people and
        studios are explicit follows only.

        ``approved_only=True`` drops follows still awaiting admin approval — what
        the people/studio scan handlers pass, since each row they see becomes
        wishlist writes. The watchlist PAGE leaves it False so a requester can see
        their own pending ask, flagged by the ``approved`` field on each row."""
        gate = " AND approved=1" if approved_only else ""
        cols = ("SELECT tmdb_id, title, poster_url, library_id, date_added, lookback_years, "
                "approved, requested_by, requested_by_name, monitor ")
        conn = self._get_connection()
        try:
            people = []
            if kind in (None, "person"):
                for r in conn.execute(
                        cols + "FROM video_watchlist WHERE kind='person' AND state='follow'"
                        + gate + " ORDER BY date_added DESC, id DESC"):
                    d = dict(r); d["kind"] = "person"
                    d["poster_url"] = _proxied_art(d.get("poster_url"))
                    d["approved"] = bool(d.get("approved", 1)); people.append(d)
            studios = []
            if kind in (None, "studio"):
                for r in conn.execute(
                        cols + "FROM video_watchlist WHERE kind='studio' AND state='follow'"
                        + gate + " ORDER BY date_added DESC, id DESC"):
                    d = dict(r); d["kind"] = "studio"
                    d["poster_url"] = _proxied_art(d.get("poster_url"))
                    d["approved"] = bool(d.get("approved", 1)); studios.append(d)
            shows = (self._effective_shows(conn, server_source, approved_only=approved_only)
                     if kind in (None, "show") else [])
            if kind == "person":
                return people
            if kind == "studio":
                return studios
            if kind == "show":
                return shows
            return shows + people + studios
        finally:
            conn.close()

    def watchlist_state(self, kind: str, tmdb_ids, server_source=None) -> dict:
        """{tmdb_id: True} for ids that are watched — explicit follow OR (for
        shows) an actively-airing library show that isn't muted. Hydrates buttons."""
        out: dict = {}
        ids = [int(x) for x in (tmdb_ids or []) if x]
        if kind not in ("show", "person", "studio") or not ids:
            return out
        conn = self._get_connection()
        try:
            for i in range(0, len(ids), 400):  # stay under SQLite's variable cap
                chunk = ids[i:i + 400]
                ph = ",".join("?" * len(chunk))
                for r in conn.execute(
                        f"SELECT tmdb_id FROM video_watchlist WHERE kind=? AND state='follow' "
                        f"AND tmdb_id IN ({ph})", [kind] + chunk):
                    out[r["tmdb_id"]] = True
                if kind == "show":
                    muted = {r["tmdb_id"] for r in conn.execute(
                        f"SELECT tmdb_id FROM video_watchlist WHERE kind='show' AND state='mute' "
                        f"AND tmdb_id IN ({ph})", chunk)}
                    ssql = f"SELECT tmdb_id FROM shows WHERE tmdb_id IN ({ph}) AND " + self._ACTIVE_SHOW_SQL
                    sargs = list(chunk)
                    if server_source:
                        ssql += " AND server_source = ?"; sargs.append(server_source)
                    for r in conn.execute(ssql, sargs):
                        if r["tmdb_id"] not in muted:
                            out[r["tmdb_id"]] = True
            return out
        finally:
            conn.close()

    def watchlist_counts(self, server_source=None) -> dict:
        """{'show': n, 'person': n, 'studio': n, 'total': n} over the EFFECTIVE watchlist."""
        shows = self.list_watchlist("show", server_source=server_source)
        people = self.list_watchlist("person")
        studios = self.list_watchlist("studio")
        return {"show": len(shows), "person": len(people), "studio": len(studios),
                "total": len(shows) + len(people) + len(studios)}

    def query_watchlist(self, kind: str, *, search=None, sort="default", page=1, limit=60,
                        server_source=None) -> dict:
        """One searched/sorted/paged slice of the effective watchlist for a kind —
        mirrors query_library's {items, pagination} shape so the page can paginate
        like the library. The effective list is bounded (follows + airing library
        shows), so it's computed then filtered/sorted/sliced rather than via SQL."""
        try:
            page = max(1, int(page or 1))
            limit = max(1, min(200, int(limit or 60)))
        except (TypeError, ValueError):
            page, limit = 1, 60
        items = self.list_watchlist(kind, server_source=server_source) if kind in ("show", "person", "studio") else []
        s = (search or "").strip().lower()
        if s:
            items = [it for it in items if s in (it.get("title") or "").lower()]
        if sort == "added":   # opt-in: explicit follows (have a date) newest-first
            items.sort(key=lambda it: (it.get("date_added") or ""), reverse=True)
        else:   # "default" / "title": alphabetical by name — a manual follow is no more
                # special than an auto-added airing show, so they sort together A–Z.
                # .strip() guards against dirty titles (e.g. a leading space from the
                # scan, which would otherwise sort before 'a').
            items.sort(key=lambda it: (it.get("sort_title") or it.get("title") or "").strip().lower())
        total = len(items)
        total_pages = max(1, (total + limit - 1) // limit)
        page = min(page, total_pages)
        start = (page - 1) * limit
        return {"items": items[start:start + limit], "pagination": {
            "page": page, "total_pages": total_pages, "total_count": total,
            "has_prev": page > 1, "has_next": page < total_pages}}

    # ── Wishlist (curated 'get this': movies + episodes) ──────────────────────
    # Atomic units are movies and episodes. Adding a whole show/season expands
    # into episode rows (the caller supplies the explicit episodes); show/season
    # are just bulk add/remove operations over those rows.
    def add_movie_to_wishlist(self, tmdb_id, title, *, year=None, poster_url=None,
                              library_id=None, server_source=None, status='wanted',
                              detail_json=None, root_folder_id=None,
                              added_by_profile_id=None, approved: bool = True,
                              requested_by=None, requested_by_name=None) -> bool:
        """Wish for a movie. Idempotent upsert on its tmdb id.

        ``status`` lets the watchlist-people scan add an UPCOMING (unreleased) movie as
        'monitored' so the wishlist engine skips it until it's out; a later scan re-adds it
        with 'wanted' once released, which PROMOTES it. We never downgrade a status the engine
        has already advanced (searching/downloading/downloaded/failed) and never knock a
        'wanted' back to 'monitored'.

        ``detail_json`` is a rich TMDB-detail blob (backdrop/overview/genres/cast/etc.,
        captured at add time) so the wishlist renders a full card without re-fetching; it's
        only filled in, never wiped, on re-add.
        """
        if not tmdb_id or not title:
            return False
        # Resolve the destination Library HERE rather than trusting the caller.
        # Only ONE of the nine paths that create wishlist rows ever passed this
        # (the manual add); the airing automation, watchlist scans, import
        # lists, member requests, the re-wishlist-on-failure hook and two repair
        # jobs all passed nothing — and nothing meant "primary Library". Doing it
        # in the adder fixes every caller at once and cannot be forgotten by the
        # next one. An explicit id still wins.
        if root_folder_id is None:
            root_folder_id = self.resolve_destination_library("movie", tmdb_id)
        # NOTE: release_date (the downloadable/home-availability date) is NOT set here — TMDB's
        # primary detail date is theatrical/premiere, which mis-gates cinema-only films. The
        # wishlist drain's availability backfill owns it (digital/physical, or wide-theatrical +
        # window), leaving it NULL here so the backfill picks the movie up.
        if isinstance(detail_json, (dict, list)):
            import json as _json
            detail_json = _json.dumps(detail_json)
        conn = self._get_connection()
        try:
            existed = conn.execute(
                "SELECT 1 FROM video_wishlist WHERE kind='movie' AND tmdb_id=?",
                (int(tmdb_id),)).fetchone()
            conn.execute(
                # added_by_profile_id is deliberately absent from the DO UPDATE:
                # ownership belongs to whoever asked FIRST. Re-adding a title
                # someone else wished must not hand their row to the re-adder,
                # which would be a way to delete another member's wish.
                """INSERT INTO video_wishlist (kind, tmdb_id, title, poster_url, year, library_id, server_source, status, detail_json, root_folder_id, added_by_profile_id, approved, requested_by, requested_by_name)
                   VALUES ('movie', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                   ON CONFLICT(tmdb_id) WHERE kind='movie' DO UPDATE SET
                       title=excluded.title,
                       poster_url=COALESCE(excluded.poster_url, video_wishlist.poster_url),
                       year=COALESCE(excluded.year, video_wishlist.year),
                       library_id=COALESCE(excluded.library_id, video_wishlist.library_id),
                       root_folder_id=COALESCE(excluded.root_folder_id, video_wishlist.root_folder_id),
                       detail_json=COALESCE(excluded.detail_json, video_wishlist.detail_json),
                       status=CASE WHEN video_wishlist.status='monitored' AND excluded.status='wanted'
                                   THEN 'wanted' ELSE video_wishlist.status END,
                       -- MAX, so a pending re-add can never DOWNGRADE a wish an
                       -- admin already approved (and a member re-wishing something
                       -- live cannot quietly park it back in the queue).
                       approved=MAX(video_wishlist.approved, excluded.approved),
                       requested_by=COALESCE(video_wishlist.requested_by, excluded.requested_by),
                       requested_by_name=COALESCE(video_wishlist.requested_by_name, excluded.requested_by_name)""",
                (int(tmdb_id), title, poster_url, year, library_id, server_source, status,
                 detail_json, root_folder_id, added_by_profile_id,
                 1 if approved else 0, requested_by, requested_by_name))
            conn.commit()
            if not existed:   # a refresh-upsert is not a new wish
                _publish_video_event("video_wishlist_item_added",
                                     {"kind": "movie", "title": title, "count": 1})
                # ...and, separately, that somebody is waiting on an admin. Both
                # fire: the item WAS added, so an existing subscription to the
                # 'added' event keeps behaving as it did. This one is the signal
                # you can actually act on.
                if not approved:
                    _publish_video_event("video_request_pending", {
                        "kind": "movie", "title": title, "count": 1,
                        "queue": "wishlist", "tmdb_id": int(tmdb_id),
                        "requested_by": requested_by_name})
            return True
        except Exception:
            logger.exception("add_movie_to_wishlist failed (%s)", tmdb_id)
            return False
        finally:
            conn.close()

    def watchlist_states(self, kind: str) -> dict:
        """{tmdb_id: state} for every watchlist row of a kind — lets the
        collections tie-in skip shows already followed AND respect 'mute'
        tombstones (a muted show must never be re-followed by automation)."""
        conn = self._get_connection()
        try:
            return {int(r["tmdb_id"]): r["state"] for r in conn.execute(
                "SELECT tmdb_id, state FROM video_watchlist "
                "WHERE kind=? AND tmdb_id IS NOT NULL", (kind,))}
        except Exception:
            logger.exception("watchlist_states failed")
            return {}
        finally:
            conn.close()

    def wishlisted_movie_status(self) -> dict:
        """{tmdb_id: status} for every movie on the wishlist. Lets the watchlist-people
        scan skip movies it's already handled (fast re-runs) and spot 'monitored' rows
        that are now releasable so it can promote them."""
        conn = self._get_connection()
        try:
            return {int(r["tmdb_id"]): r["status"] for r in conn.execute(
                "SELECT tmdb_id, status FROM video_wishlist WHERE kind='movie' AND tmdb_id IS NOT NULL")}
        except Exception:
            logger.exception("wishlisted_movie_status failed")
            return {}
        finally:
            conn.close()

    def movie_wishlist_to_download(self) -> list:
        """Wished movies ready to grab: only ``status='wanted'`` (released) — skips
        'monitored' (unreleased). OWNED titles are included, annotated with
        ``owned`` + ``owned_resolutions`` (the library files' resolutions,
        comma-joined) — the 'upgrade until cutoff' semantics: the drain skips
        owned items that already meet the cutoff and only accepts strictly
        better releases for the rest, which is what keeps the old
        owned-re-download loop broken. Newest year first.

        ``root_folder_id`` is the Library the movie is ALREADY filed under
        (via ``library_id``, when set) — so an unattended grab lands in the
        SAME Library instead of always the primary one for the kind. NULL
        when the movie isn't linked to a library row yet; the caller falls
        back to the primary in that case."""
        conn = self._get_connection()
        try:
            return [dict(r) for r in conn.execute(
                "SELECT w.tmdb_id, w.title, w.year, w.poster_url, w.library_id, "
                "COALESCE(w.root_folder_id, (SELECT root_folder_id FROM movies WHERE id = w.library_id)) "
                "AS root_folder_id, "
                "EXISTS (SELECT 1 FROM movies m WHERE m.tmdb_id=w.tmdb_id AND m.has_file=1) "
                "  AS owned, "
                "(SELECT GROUP_CONCAT(f.resolution) FROM movies m "
                "  JOIN media_files f ON f.movie_id=m.id "
                "  WHERE m.tmdb_id=w.tmdb_id AND m.has_file=1) AS owned_resolutions, "
                "COALESCE(w.quality_profile_id, (SELECT m.quality_profile_id FROM movies m "
                "  WHERE m.tmdb_id=w.tmdb_id)) AS quality_profile_id "
                "FROM video_wishlist w "
                # APPROVAL GATE: a wish from a profile without download rights is
                # invisible to every acquisition path until an admin approves it.
                # It still shows on the wishlist — only the fetching waits.
                "WHERE w.approved=1 AND w.kind='movie' AND w.status='wanted' AND w.tmdb_id IS NOT NULL "
                # release-window gate: only search once within a week of release (early scene
                # releases appear a few days out). A further-off movie stays wished but isn't
                # hunted — no risk of grabbing a wrong-titled or fake 'release' before it exists.
                # Unknown release date → allow (the year check on the release still guards).
                "AND (w.release_date IS NULL OR w.release_date <= date('now', '+7 days')) "
                "ORDER BY w.year DESC, w.id DESC")]
        finally:
            conn.close()

    def wishlist_movies_missing_release_date(self, limit=25) -> list:
        """Wished (wanted) movies whose downloadable date hasn't been resolved yet — the drain's
        availability backfill fills these (via TMDB) before it gates. Returns [tmdb_id, ...]."""
        conn = self._get_connection()
        try:
            return [int(r["tmdb_id"]) for r in conn.execute(
                "SELECT tmdb_id FROM video_wishlist WHERE kind='movie' AND status='wanted' "
                "AND tmdb_id IS NOT NULL AND release_date IS NULL ORDER BY id DESC LIMIT ?",
                (max(1, int(limit)),))]
        except sqlite3.Error:
            logger.exception("wishlist_movies_missing_release_date failed")
            return []
        finally:
            conn.close()

    def set_wishlist_release_date(self, tmdb_id, release_date) -> None:
        """Record a wished movie's downloadable ('available') date — drives the release-window
        gate. Pass the sentinel '1970-01-01' for 'checked, but TMDB has no date' so it isn't
        re-queried forever yet still searches (the year check guards)."""
        if not tmdb_id or not release_date:
            return
        conn = self._get_connection()
        try:
            conn.execute("UPDATE video_wishlist SET release_date=? WHERE kind='movie' AND tmdb_id=?",
                         (str(release_date), int(tmdb_id)))
            conn.commit()
        except sqlite3.Error:
            logger.exception("set_wishlist_release_date failed for %s", tmdb_id)
        finally:
            conn.close()

    def clear_wishlist_movie_release_dates(self) -> int:
        """Wipe every wished movie's derived availability date so the backfill re-derives them
        with the current logic (used once when that logic changes). Returns rows cleared."""
        conn = self._get_connection()
        try:
            cur = conn.execute("UPDATE video_wishlist SET release_date=NULL "
                               "WHERE kind='movie' AND release_date IS NOT NULL")
            conn.commit()
            return cur.rowcount
        except sqlite3.Error:
            logger.exception("clear_wishlist_movie_release_dates failed")
            return 0
        finally:
            conn.close()

    def episode_wishlist_to_download(self) -> list:
        """Wished episodes READY to grab: aired (or air-date-unknown), never episodes still in
        the future. Upcoming episodes CAN sit on the wishlist now (e.g. pre-ordered from the
        calendar), but the drain must not hunt for a release that can't exist yet — so this
        skips ``air_date`` in the future. OWNED episodes are included with the same
        ``owned``/``owned_resolutions`` annotation as movies (upgrade-until semantics; the
        drain does the cutoff/strictly-better judging). Newest air date first.

        ``root_folder_id`` is the show's OWN Library (via ``library_id``), so
        a grab lands where the show already lives (e.g. Anime vs TV-Shows)
        instead of always the primary TV Library. NULL when the show isn't
        linked to a library row yet."""
        conn = self._get_connection()
        try:
            return [dict(r) for r in conn.execute(
                "SELECT w.tmdb_id AS show_tmdb_id, w.title AS show_title, w.season_number, "
                "w.episode_number, w.episode_title, w.air_date, w.poster_url, w.library_id, "
                "COALESCE(w.root_folder_id, (SELECT root_folder_id FROM shows WHERE id = w.library_id)) "
                "AS root_folder_id, "
                "EXISTS (SELECT 1 FROM episodes e JOIN shows s ON e.show_id = s.id "
                "  WHERE s.tmdb_id = w.tmdb_id AND e.season_number = w.season_number "
                "  AND e.episode_number = w.episode_number AND e.has_file = 1) AS owned, "
                "(SELECT GROUP_CONCAT(f.resolution) FROM episodes e "
                "  JOIN shows s ON e.show_id = s.id "
                "  JOIN media_files f ON f.episode_id = e.id "
                "  WHERE s.tmdb_id = w.tmdb_id AND e.season_number = w.season_number "
                "  AND e.episode_number = w.episode_number AND e.has_file = 1) "
                "  AS owned_resolutions, "
                "COALESCE(w.quality_profile_id, (SELECT s.quality_profile_id FROM shows s "
                "  WHERE s.tmdb_id = w.tmdb_id)) AS quality_profile_id, "
                # series type (P8): daily/anime shows QUERY differently (air date /
                # absolute number). NULL for shows not in the library = standard.
                # MAX = a type set on ANY server's row wins over a NULL sibling.
                # The library row WINS over the override. The override is a stand-in for
                # a show that has no row yet; once one exists the full Manage panel
                # edits the row, and leaving a stale override ahead of it would make
                # that edit silently do nothing. An unset row is NULL, so the
                # override keeps applying right up until someone sets it for real.
                "COALESCE((SELECT MAX(s.series_type) FROM shows s WHERE s.tmdb_id = w.tmdb_id), "
                "         (SELECT o.series_type FROM video_title_overrides o "
                "          WHERE o.kind='show' AND o.tmdb_id = w.tmdb_id)) "
                "AS series_type "
                # APPROVAL GATE — see movie_wishlist_to_download.
                "FROM video_wishlist w WHERE w.approved=1 AND w.kind='episode' AND w.tmdb_id IS NOT NULL "
                # release-window gate: search an episode only once it's within a week of air
                # (early scene releases show up a few days out) — a further-off episode stays on
                # the wishlist but isn't searched yet, so the drain never hunts a release that
                # can't exist. Unknown air date → allow (can't prove it's future).
                "AND (w.air_date IS NULL OR w.air_date <= date('now', '+7 days')) "
                "ORDER BY w.air_date DESC, w.id DESC")]
        finally:
            conn.close()

    def wishlist_manual_search_items(self, scope, tmdb_id, season_number=None,
                                     episode_number=None) -> list:
        """Wishlist rows shaped EXACTLY like the to_download queries, for a
        user-initiated 'Search now' — but WITHOUT the release-window gate and
        (movies) without the status gate: the click is the override, same as
        Sonarr's manual search. scope: 'movie' → that movie; 'show' → every
        wished episode of the show; 'season' → that season; 'episode' → one row."""
        if not tmdb_id:
            return []
        conn = self._get_connection()
        try:
            if scope == "movie":
                return [dict(r) for r in conn.execute(
                    "SELECT w.tmdb_id, w.title, w.year, w.poster_url, w.library_id, "
                    "COALESCE(w.root_folder_id, (SELECT root_folder_id FROM movies WHERE id = w.library_id)) "
                "AS root_folder_id, "
                    "EXISTS (SELECT 1 FROM movies m WHERE m.tmdb_id=w.tmdb_id AND m.has_file=1) "
                    "  AS owned, "
                    "(SELECT GROUP_CONCAT(f.resolution) FROM movies m "
                    "  JOIN media_files f ON f.movie_id=m.id "
                    "  WHERE m.tmdb_id=w.tmdb_id AND m.has_file=1) AS owned_resolutions, "
                    "COALESCE(w.quality_profile_id, (SELECT m.quality_profile_id FROM movies m "
                    "  WHERE m.tmdb_id=w.tmdb_id)) AS quality_profile_id "
                    "FROM video_wishlist w "
                    # APPROVAL GATE: 'Search now' is a manual acquisition path, so
                    # it must respect the queue too — otherwise a pending wish
                    # could be fetched by pressing a different button.
                    "WHERE w.approved=1 AND w.kind='movie' AND w.tmdb_id=?", (int(tmdb_id),))]
            where, args = "", [int(tmdb_id)]
            if scope == "season" and season_number is not None:
                where, args = " AND w.season_number=?", args + [int(season_number)]
            elif scope == "episode":
                where = " AND w.season_number=? AND w.episode_number=?"
                args += [int(season_number or 0), int(episode_number or 0)]
            return [dict(r) for r in conn.execute(
                "SELECT w.tmdb_id AS show_tmdb_id, w.title AS show_title, w.season_number, "
                "w.episode_number, w.episode_title, w.air_date, w.poster_url, w.library_id, "
                "COALESCE(w.root_folder_id, (SELECT root_folder_id FROM shows WHERE id = w.library_id)) "
                "AS root_folder_id, "
                "EXISTS (SELECT 1 FROM episodes e JOIN shows s ON e.show_id = s.id "
                "  WHERE s.tmdb_id = w.tmdb_id AND e.season_number = w.season_number "
                "  AND e.episode_number = w.episode_number AND e.has_file = 1) AS owned, "
                "(SELECT GROUP_CONCAT(f.resolution) FROM episodes e "
                "  JOIN shows s ON e.show_id = s.id "
                "  JOIN media_files f ON f.episode_id = e.id "
                "  WHERE s.tmdb_id = w.tmdb_id AND e.season_number = w.season_number "
                "  AND e.episode_number = w.episode_number AND e.has_file = 1) "
                "  AS owned_resolutions, "
                "COALESCE(w.quality_profile_id, (SELECT s.quality_profile_id FROM shows s "
                "  WHERE s.tmdb_id = w.tmdb_id)) AS quality_profile_id, "
                # The library row WINS over the override. The override is a stand-in for
                # a show that has no row yet; once one exists the full Manage panel
                # edits the row, and leaving a stale override ahead of it would make
                # that edit silently do nothing. An unset row is NULL, so the
                # override keeps applying right up until someone sets it for real.
                "COALESCE((SELECT MAX(s.series_type) FROM shows s WHERE s.tmdb_id = w.tmdb_id), "
                "         (SELECT o.series_type FROM video_title_overrides o "
                "          WHERE o.kind='show' AND o.tmdb_id = w.tmdb_id)) "
                "AS series_type "
                # APPROVAL GATE — see the movie arm above.
                "FROM video_wishlist w WHERE w.approved=1 AND w.kind='episode' AND w.tmdb_id=?" + where +
                " ORDER BY w.season_number, w.episode_number", args)]
        finally:
            conn.close()

    def add_episodes_to_wishlist(self, show_tmdb_id, show_title, episodes, *,
                                 poster_url=None, library_id=None, server_source=None,
                                 root_folder_id=None, added_by_profile_id=None,
                                 approved: bool = True, requested_by=None,
                                 requested_by_name=None) -> int:
        """Wish for one or more episodes of a show (the show's tmdb id keys them).
        ``episodes`` = [{season_number, episode_number, title?, air_date?}, …].
        Idempotent per (show, season, episode). Returns the count written."""
        if not show_tmdb_id or not show_title or not episodes:
            return 0
        # See add_movie_to_wishlist — resolved here so every caller benefits.
        # This is the one that matters most: the daily airing automation is how
        # episodes of a followed show reach the wishlist, and it passed nothing,
        # so a new anime show's first episode always went to the primary TV
        # Library and was cemented there by the next scan.
        if root_folder_id is None:
            root_folder_id = self.resolve_destination_library("show", show_tmdb_id)
        conn = self._get_connection()
        n = 0
        try:
            before_count = conn.execute(
                "SELECT COUNT(*) FROM video_wishlist WHERE kind='episode' AND tmdb_id=?",
                (int(show_tmdb_id),)).fetchone()[0]
            for e in episodes:
                sn, en = e.get("season_number"), e.get("episode_number")
                if sn is None or en is None:
                    continue
                conn.execute(
                    # added_by_profile_id set on INSERT only — see add_movie_to_wishlist.
                    """INSERT INTO video_wishlist
                           (kind, tmdb_id, title, poster_url, season_number, episode_number,
                            episode_title, still_url, episode_overview, season_poster_url,
                            air_date, library_id, server_source, root_folder_id, added_by_profile_id,
                            approved, requested_by, requested_by_name)
                       VALUES ('episode', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                       ON CONFLICT(tmdb_id, season_number, episode_number) WHERE kind='episode' DO UPDATE SET
                           title=excluded.title,
                           poster_url=COALESCE(excluded.poster_url, video_wishlist.poster_url),
                           episode_title=COALESCE(excluded.episode_title, video_wishlist.episode_title),
                           still_url=COALESCE(excluded.still_url, video_wishlist.still_url),
                           episode_overview=COALESCE(excluded.episode_overview, video_wishlist.episode_overview),
                           season_poster_url=COALESCE(excluded.season_poster_url, video_wishlist.season_poster_url),
                           air_date=COALESCE(excluded.air_date, video_wishlist.air_date),
                           library_id=COALESCE(excluded.library_id, video_wishlist.library_id),
                           root_folder_id=COALESCE(excluded.root_folder_id, video_wishlist.root_folder_id),
                           -- MAX — see add_movie_to_wishlist.
                           approved=MAX(video_wishlist.approved, excluded.approved),
                           requested_by=COALESCE(video_wishlist.requested_by, excluded.requested_by),
                           requested_by_name=COALESCE(video_wishlist.requested_by_name, excluded.requested_by_name)""",
                    (int(show_tmdb_id), show_title, poster_url, int(sn), int(en),
                     e.get("title"), e.get("still_url"), e.get("overview"), e.get("season_poster_url"),
                     e.get("air_date"), library_id, server_source, root_folder_id,
                     added_by_profile_id, 1 if approved else 0, requested_by, requested_by_name))
                n += 1
            new_rows = conn.execute(
                "SELECT COUNT(*) FROM video_wishlist WHERE kind='episode' AND tmdb_id=?",
                (int(show_tmdb_id),)).fetchone()[0] - before_count
            conn.commit()
            if new_rows > 0:   # refresh-upserts of already-wished episodes don't fire
                _publish_video_event("video_wishlist_item_added",
                                     {"kind": "episode", "title": show_title, "count": new_rows})
                # ONE event for the whole ask, carrying the count — a 24-episode
                # season request must not become 24 Discord messages.
                if not approved:
                    _publish_video_event("video_request_pending", {
                        "kind": "episode", "title": show_title, "count": new_rows,
                        "queue": "wishlist", "tmdb_id": int(show_tmdb_id),
                        "requested_by": requested_by_name})
            return n
        except Exception:
            logger.exception("add_episodes_to_wishlist failed (%s)", show_tmdb_id)
            conn.rollback()
            return 0
        finally:
            conn.close()

    @staticmethod
    def _wishlist_scope(scope, tmdb_id, season_number, episode_number):
        """(where, args) selecting the rows a remove/count at this granularity
        covers, or None when the scope+arguments don't name anything."""
        if not tmdb_id:
            return None
        if scope == "movie":
            return "kind='movie' AND tmdb_id=?", [int(tmdb_id)]
        if scope == "show":
            return "kind='episode' AND tmdb_id=?", [int(tmdb_id)]
        if scope == "season":
            if season_number is None:
                return None
            return "kind='episode' AND tmdb_id=? AND season_number=?", [int(tmdb_id), int(season_number)]
        if scope == "episode":
            if season_number is None or episode_number is None:
                return None
            return ("kind='episode' AND tmdb_id=? AND season_number=? AND episode_number=?",
                    [int(tmdb_id), int(season_number), int(episode_number)])
        return None

    def remove_from_wishlist(self, scope, *, tmdb_id, season_number=None, episode_number=None,
                             only_profile_id=None) -> int:
        """Remove at any granularity: 'movie' | 'show' (all its episodes) |
        'season' | 'episode'. Returns the number of rows removed.

        ``only_profile_id`` narrows the delete to rows THAT profile added — how a
        member without download rights removes their own wishes without being
        able to touch anyone else's (or automation's, whose rows have no adder).
        None = no ownership restriction, for admins and can_download profiles."""
        sel = self._wishlist_scope(scope, tmdb_id, season_number, episode_number)
        if sel is None:
            return 0
        where, args = sel
        if only_profile_id is not None:
            where += " AND added_by_profile_id=?"
            args = args + [int(only_profile_id)]
        conn = self._get_connection()
        try:
            cur = conn.execute("DELETE FROM video_wishlist WHERE " + where, args)
            conn.commit()
            return cur.rowcount
        finally:
            conn.close()

    # ── approval queue ───────────────────────────────────────────────────────
    def approve_wishlist(self, scope, *, tmdb_id, season_number=None,
                         episode_number=None) -> int:
        """Release a pending wish to the acquisition paths. Returns rows approved.

        Only ever touches approved=0 rows, so approving twice is a no-op rather
        than something that could reset state on a live wish."""
        sel = self._wishlist_scope(scope, tmdb_id, season_number, episode_number)
        if sel is None:
            return 0
        where, args = sel
        conn = self._get_connection()
        try:
            cur = conn.execute(
                "UPDATE video_wishlist SET approved=1 WHERE approved=0 AND " + where, args)
            conn.commit()
            return cur.rowcount
        finally:
            conn.close()

    def approve_youtube_wishlist(self, scope: str, source_id: str) -> int:
        """Same, for wished YouTube videos ('video') or a whole channel's ('channel')."""
        sel = self._youtube_wishlist_scope(scope, source_id)
        if sel is None:
            return 0
        where, args = sel
        conn = self._get_connection()
        try:
            cur = conn.execute(
                "UPDATE video_wishlist SET approved=1 WHERE approved=0 AND " + where, args)
            conn.commit()
            return cur.rowcount
        finally:
            conn.close()

    def count_pending_wishlist_rows(self, scope, *, tmdb_id, season_number=None,
                                    episode_number=None) -> int:
        """How many rows in this scope are still awaiting approval — lets deny
        refuse cleanly rather than silently removing nothing."""
        sel = self._wishlist_scope(scope, tmdb_id, season_number, episode_number)
        if sel is None:
            return 0
        where, args = sel
        conn = self._get_connection()
        try:
            return conn.execute(
                "SELECT COUNT(*) c FROM video_wishlist WHERE approved=0 AND " + where,
                args).fetchone()["c"]
        finally:
            conn.close()

    def deny_wishlist(self, scope, *, tmdb_id, season_number=None, episode_number=None) -> int:
        """Remove pending wishes in this scope. Never touches an approved row, so
        a deny aimed at the wrong thing cannot delete a live wish."""
        sel = self._wishlist_scope(scope, tmdb_id, season_number, episode_number)
        if sel is None:
            return 0
        where, args = sel
        conn = self._get_connection()
        try:
            cur = conn.execute(
                "DELETE FROM video_wishlist WHERE approved=0 AND " + where, args)
            conn.commit()
            return cur.rowcount
        finally:
            conn.close()

    def pending_wishlist_entries(self, requested_by=None) -> list:
        """Wishes awaiting approval, newest first. ``requested_by`` scopes it to one
        profile's own asks — what a member sees of the queue."""
        sql = ("SELECT id, kind, tmdb_id, title, poster_url, year, season_number, "
               "episode_number, episode_title, source, source_id, parent_source_id, "
               "requested_by, requested_by_name, date_added "
               "FROM video_wishlist WHERE approved=0")
        args: list = []
        if requested_by is not None:
            sql += " AND requested_by=?"
            args.append(int(requested_by))
        sql += " ORDER BY date_added DESC, id DESC"
        conn = self._get_connection()
        try:
            return [dict(r) for r in conn.execute(sql, args)]
        finally:
            conn.close()

    def pending_wishlist_count(self, requested_by=None) -> int:
        """How many wishes are awaiting approval — the badge."""
        sql = "SELECT COUNT(*) c FROM video_wishlist WHERE approved=0"
        args: list = []
        if requested_by is not None:
            sql += " AND requested_by=?"
            args.append(int(requested_by))
        conn = self._get_connection()
        try:
            return conn.execute(sql, args).fetchone()["c"]
        except Exception:
            return 0
        finally:
            conn.close()

    def count_wishlist_rows(self, scope, *, tmdb_id, season_number=None, episode_number=None) -> int:
        """How many rows a remove at this granularity WOULD cover, ignoring who
        added them. Lets the API tell 'those aren't yours' apart from 'there was
        nothing there' — an ownership-scoped delete reports 0 for both."""
        sel = self._wishlist_scope(scope, tmdb_id, season_number, episode_number)
        if sel is None:
            return 0
        where, args = sel
        conn = self._get_connection()
        try:
            return conn.execute(
                "SELECT COUNT(*) c FROM video_wishlist WHERE " + where, args).fetchone()["c"]
        finally:
            conn.close()

    def clear_wishlist(self, kind: str, *, only_profile_id=None) -> int:
        """Empty a wishlist tab in one go. ``kind`` is the user-facing tab:
        'movie' | 'show' (TV) | 'youtube'. Returns the number of rows removed.

        ``only_profile_id`` limits it to the rows THAT profile added, so 'Clear
        all' means "clear all of mine" for a member and "clear the tab" for a
        profile that manages the shared wishlist. Same rule as
        remove_from_wishlist — one bulk button must not be a way around the
        per-item ownership check."""
        dbkind = {"movie": "movie", "show": "episode", "youtube": "video"}.get(kind)
        if not dbkind:
            return 0
        sql, args = "DELETE FROM video_wishlist WHERE kind=?", [dbkind]
        if only_profile_id is not None:
            sql += " AND added_by_profile_id=?"
            args.append(int(only_profile_id))
        conn = self._get_connection()
        try:
            cur = conn.execute(sql, args)
            conn.commit()
            return cur.rowcount
        finally:
            conn.close()

    def wishlist_counts(self) -> dict:
        """{'movie': n, 'show': n, 'episode': n, 'total': movies+episodes,
        'by_library': {<root_folder_id>: n}}.

        ``by_library`` feeds the badge on each per-Library tab, counting the
        SAME unit its kind tab does — movies for a movie Library, distinct
        shows (not episodes) for a show Library — so a Library's badge is
        directly comparable to the 'All' badge above it. Scoped through
        ``library_id`` exactly like ``query_wishlist``'s root_folder_id
        filter, so the badge can never disagree with the list it labels.
        """
        conn = self._get_connection()
        try:
            movie = conn.execute("SELECT COUNT(*) c FROM video_wishlist WHERE kind='movie'").fetchone()["c"]
            episode = conn.execute("SELECT COUNT(*) c FROM video_wishlist WHERE kind='episode'").fetchone()["c"]
            shows = conn.execute("SELECT COUNT(DISTINCT tmdb_id) c FROM video_wishlist WHERE kind='episode'").fetchone()["c"]
            by_library = {}
            for r in self.all_library_rows():
                if r.get("content_kind") == "movie":
                    sql = ("SELECT COUNT(*) c FROM video_wishlist WHERE kind='movie' AND "
                           "COALESCE(root_folder_id, (SELECT root_folder_id FROM movies WHERE id = library_id)) = ?")
                elif r.get("content_kind") == "show":
                    sql = ("SELECT COUNT(DISTINCT tmdb_id) c FROM video_wishlist WHERE kind='episode' AND "
                           "COALESCE(root_folder_id, (SELECT root_folder_id FROM shows WHERE id = library_id)) = ?")
                else:
                    continue
                by_library[r["id"]] = conn.execute(sql, (r["id"],)).fetchone()["c"]
            return {"movie": movie, "show": shows, "episode": episode,
                    "total": movie + episode, "by_library": by_library}
        finally:
            conn.close()

    def record_wishlist_search_outcome(self, kind: str, tmdb_id, grabbed: bool,
                                       season_number=None, episode_number=None) -> None:
        """Track consecutive fruitless drain searches per wishlist row
        (#liveleak-failing-hub): a grab resets the counter, a genuinely
        fruitless search (no results / all rejected) increments it. Searches
        that never RAN (slskd down, rate-limited) must not be recorded — the
        caller owns that distinction. Best-effort; never raises."""
        conn = self._get_connection()
        try:
            where = "kind=? AND tmdb_id=?"
            args = [str(kind), tmdb_id]
            if season_number is not None and episode_number is not None:
                where += " AND season_number=? AND episode_number=?"
                args += [int(season_number), int(episode_number)]
            if grabbed:
                conn.execute(
                    "UPDATE video_wishlist SET search_attempts=0, "
                    "last_search_at=datetime('now') WHERE " + where, args)
            else:
                conn.execute(
                    "UPDATE video_wishlist SET "
                    "search_attempts=COALESCE(search_attempts,0)+1, "
                    "last_search_at=datetime('now') WHERE " + where, args)
            conn.commit()
        except sqlite3.Error:
            logger.exception("record_wishlist_search_outcome failed")
        finally:
            conn.close()

    def query_wishlist(self, kind: str, *, search=None, sort="added", page=1, limit=60,
                       root_folder_id=None) -> dict:
        """One paged slice of the wishlist. kind='movie' → movie cards; kind='show'
        → shows grouped show→season→episode with wanted/done roll-ups. ``sort`` ∈
        added | title | wanted (wanted = most episodes, shows only). ``root_folder_id``
        scopes to one configured Library (e.g. Anime, not just the movie/show kind
        split) — matched via ``library_id``, the FK to the movie's/show's own row,
        so a title wished before it's linked to any Library (``library_id`` unset)
        won't appear under a specific Library filter, same as the Library page's
        per-Library tabs. {items, pagination} like the other paged queries."""
        try:
            page = max(1, int(page or 1))
            limit = max(1, min(200, int(limit or 60)))
        except (TypeError, ValueError):
            page, limit = 1, 60
        s = (search or "").strip()
        conn = self._get_connection()
        try:
            if kind == "movie":
                where, args = ["kind='movie'"], []
                if s:
                    where.append("title LIKE ? COLLATE NOCASE"); args.append("%" + s + "%")
                if root_folder_id:
                    where.append("COALESCE(root_folder_id, (SELECT root_folder_id FROM movies WHERE id = library_id)) = ?")
                    args.append(root_folder_id)
                wsql = " WHERE " + " AND ".join(where)
                order = {"title": "title COLLATE NOCASE", "oldest": "date_added ASC, id ASC",
                         "added": "date_added DESC, id DESC"}.get(sort, "date_added DESC, id DESC")
                total = conn.execute("SELECT COUNT(*) c FROM video_wishlist" + wsql, args).fetchone()["c"]
                rows = conn.execute(
                    "SELECT tmdb_id, title, poster_url, year, status, library_id, date_added, "
                    "search_attempts, last_search_at, added_by_profile_id, "
                    "approved, requested_by_name, "
                    # The EFFECTIVE destination, resolved the same way the drain
                    # resolves it (_root_folder_for_item) — so the Library picker
                    # on the card shows where this actually lands, not just
                    # whether someone has set an override.
                    "COALESCE(root_folder_id, (SELECT root_folder_id FROM movies WHERE id = library_id)) "
                    "AS root_folder_id "
                    "FROM video_wishlist" + wsql + " ORDER BY " + order + " LIMIT ? OFFSET ?",
                    args + [limit, (page - 1) * limit]).fetchall()
                items = [{"kind": "movie", "tmdb_id": r["tmdb_id"], "title": r["title"],
                          "poster_url": r["poster_url"], "year": r["year"], "status": r["status"],
                          "library_id": r["library_id"],
                          "root_folder_id": r["root_folder_id"],
                          "search_attempts": r["search_attempts"] or 0,
                          "last_search_at": r["last_search_at"],
                          # who asked for it — the page shows a remove control
                          # only on rows the viewer added (or to a downloader)
                          "added_by_profile_id": r["added_by_profile_id"],
                          "approved": r["approved"],
                          "requested_by_name": r["requested_by_name"]} for r in rows]
            else:   # shows (grouped from episode rows)
                where, args = ["kind='episode'"], []
                if s:
                    where.append("title LIKE ? COLLATE NOCASE"); args.append("%" + s + "%")
                if root_folder_id:
                    where.append("COALESCE(root_folder_id, (SELECT root_folder_id FROM shows WHERE id = library_id)) = ?")
                    args.append(root_folder_id)
                wsql = " WHERE " + " AND ".join(where)
                total = conn.execute(
                    "SELECT COUNT(DISTINCT tmdb_id) c FROM video_wishlist" + wsql, args).fetchone()["c"]
                order = {"title": "title COLLATE NOCASE", "wanted": "wanted DESC, last_added DESC",
                         "oldest": "last_added ASC", "added": "last_added DESC"}.get(sort, "last_added DESC")
                show_rows = conn.execute(
                    "SELECT tmdb_id, MAX(title) AS title, MAX(poster_url) AS poster_url, "
                    "MAX(library_id) AS library_id, COUNT(*) AS wanted, "
                    "SUM(CASE WHEN status='downloaded' THEN 1 ELSE 0 END) AS done, "
                    "MAX(date_added) AS last_added "
                    "FROM video_wishlist" + wsql +
                    " GROUP BY tmdb_id ORDER BY " + order + " LIMIT ? OFFSET ?",
                    args + [limit, (page - 1) * limit]).fetchall()
                items = []
                for sr in show_rows:
                    eps = conn.execute(
                        "SELECT season_number, episode_number, episode_title, still_url, "
                        "episode_overview, season_poster_url, air_date, status, "
                        "search_attempts, last_search_at, added_by_profile_id, "
                        "approved, requested_by_name, "
                        "COALESCE(root_folder_id, (SELECT root_folder_id FROM shows WHERE id = library_id)) "
                        "AS root_folder_id "
                        "FROM video_wishlist WHERE kind='episode' AND tmdb_id=? "
                        "ORDER BY season_number, episode_number", (sr["tmdb_id"],)).fetchall()
                    by_season: dict = {}
                    season_poster: dict = {}
                    # A show's Library is only reportable when EVERY wished episode
                    # agrees. Rows can disagree (some predate the assignment), and
                    # showing one of them as "the" Library would let a picker that
                    # looks unchanged silently move the rest.
                    ep_roots = {e["root_folder_id"] for e in eps}
                    for e in eps:
                        by_season.setdefault(e["season_number"], []).append({
                            "episode_number": e["episode_number"], "title": e["episode_title"],
                            "still_url": e["still_url"], "overview": e["episode_overview"],
                            "air_date": e["air_date"], "status": e["status"],
                            "search_attempts": e["search_attempts"] or 0,
                            "last_search_at": e["last_search_at"],
                            "added_by_profile_id": e["added_by_profile_id"],
                            "approved": e["approved"],
                            "requested_by_name": e["requested_by_name"]})
                        if e["season_poster_url"] and e["season_number"] not in season_poster:
                            season_poster[e["season_number"]] = e["season_poster_url"]
                    seasons = [{"season_number": sn, "poster_url": season_poster.get(sn),
                                "episodes": by_season[sn]} for sn in sorted(by_season)]
                    items.append({"kind": "show", "tmdb_id": sr["tmdb_id"], "title": sr["title"],
                                  "poster_url": sr["poster_url"], "library_id": sr["library_id"],
                                  "root_folder_id": (list(ep_roots)[0] if len(ep_roots) == 1 else None),
                                  "wanted": sr["wanted"], "done": sr["done"] or 0, "seasons": seasons})
        finally:
            conn.close()
        total_pages = max(1, (total + limit - 1) // limit)
        page = min(page, total_pages)
        return {"items": items, "pagination": {
            "page": page, "total_pages": total_pages, "total_count": total,
            "has_prev": page > 1, "has_next": page < total_pages}}

    # Rows a metadata provider invented: no server_id (your server never reported
    # it), has_file=0 and no media_files (nothing on disk). Only such a row is
    # ever a deletion candidate — everything owned is untouchable by definition.
    _UNLISTED_SQL = (
        "SELECT e.id, e.season_number, e.episode_number, e.title, e.air_date "
        "FROM episodes e WHERE e.show_id=? AND e.season_number=? "
        "AND e.server_id IS NULL AND COALESCE(e.has_file, 0)=0 "
        "AND NOT EXISTS (SELECT 1 FROM media_files f WHERE f.episode_id = e.id) ")

    def unlisted_episode_rows(self, show_id: int, season_number: int, keep_numbers) -> list:
        """Server-less, file-less rows in this season that the AUTHORITATIVE
        provider doesn't list — i.e. episodes nothing asked for and nothing can
        match.

        ``keep_numbers`` is TMDB's own episode-number set for the season. The
        caller must have actually fetched it: an empty set would make every
        missing episode in the season a candidate, so this refuses outright
        rather than trusting a failed lookup."""
        keep = {int(n) for n in (keep_numbers or []) if str(n).lstrip("-").isdigit()}
        if not keep:
            return []
        conn = self._get_connection()
        try:
            return [dict(r) for r in conn.execute(
                self._UNLISTED_SQL + "ORDER BY e.episode_number",
                (int(show_id), int(season_number))).fetchall()
                if r["episode_number"] not in keep]
        except (sqlite3.Error, TypeError, ValueError):
            logger.exception("unlisted_episode_rows failed (show %s S%s)", show_id, season_number)
            return []
        finally:
            conn.close()

    def delete_unlisted_episode_rows(self, show_id: int, season_number: int, keep_numbers) -> int:
        """Delete exactly what ``unlisted_episode_rows`` reports, RE-DERIVED here
        rather than trusting ids from a preview — a scan landing in between must
        not let a stale list remove an episode that has since become real."""
        rows = self.unlisted_episode_rows(show_id, season_number, keep_numbers)
        if not rows:
            return 0
        conn = self._get_connection()
        try:
            conn.execute("PRAGMA foreign_keys=ON")   # raw connections default OFF
            cur = conn.execute(
                "DELETE FROM episodes WHERE id IN (%s)" % ",".join("?" * len(rows)),
                [r["id"] for r in rows])
            conn.commit()
            return cur.rowcount
        except sqlite3.Error:
            logger.exception("delete_unlisted_episode_rows failed (show %s)", show_id)
            return 0
        finally:
            conn.close()

    def apply_library_default_series_type(self, root_folder_id=None) -> int:
        """Give shows the series type their Library says they are.

        ``series_type`` decides how a release is SEARCHED for — anime by
        absolute number ('Show 1071'), dailies by air date, everything else by
        SxxExx. It is per-show, and in practice almost nobody sets it: on the
        library this was written for, 565 of 571 shows in a Library literally
        named "Anime" had it unset, so their releases were being hunted as
        standard SxxExx.

        A Library that exists to hold anime already knows the answer, so this
        applies its ``default_series_type`` to every show filed there whose own
        type is UNSET. Only unset ones — a type set by hand, or detected per
        show, is a deliberate statement and outranks a Library-wide default.

        Idempotent, and safe to run after every scan. ``root_folder_id`` limits
        it to one Library (used when that Library's default has just changed);
        None does all of them. Returns rows changed."""
        conn = self._get_connection()
        try:
            sql = ("UPDATE shows SET series_type = ("
                   "  SELECT rf.default_series_type FROM root_folders rf"
                   "  WHERE rf.id = shows.root_folder_id) "
                   "WHERE COALESCE(shows.series_type,'') = '' "
                   "  AND shows.root_folder_id IS NOT NULL "
                   "  AND EXISTS (SELECT 1 FROM root_folders rf WHERE rf.id = shows.root_folder_id "
                   "              AND COALESCE(rf.default_series_type,'') <> '')")
            params = ()
            if root_folder_id is not None:
                sql += " AND shows.root_folder_id = ?"
                params = (int(root_folder_id),)
            cur = conn.execute(sql, params)
            conn.commit()
            if cur.rowcount:
                logger.info("[Library] applied the Library default series type to %s show(s)",
                            cur.rowcount)
            return cur.rowcount
        except (sqlite3.Error, ValueError, TypeError):
            logger.exception("apply_library_default_series_type failed (%s)", root_folder_id)
            return 0
        finally:
            conn.close()

    def set_watchlist_root_folder(self, tmdb_id, root_folder_id) -> int:
        """Direct a FOLLOWED show at a specific Library, before it exists at all.

        ``set_wishlist_root_folder`` writes the decision onto wishlist rows — but
        those are created by the airing automation moments before the grab, so
        there is no point at which a human could realistically set one. The
        watchlist is where you actually make the choice: at follow time, weeks
        before the first episode airs.

        Shows only (the watchlist also holds people and channels, which have no
        destination of their own). Returns rows changed; 0 for an unknown
        Library or one that isn't a show Library."""
        if not tmdb_id:
            return 0
        rid = None
        if root_folder_id not in (None, "", "null"):
            try:
                rid = int(root_folder_id)
            except (TypeError, ValueError):
                return 0
            row = self.get_root_folder(rid)
            if not row or str(row.get("content_kind") or "") != "show":
                return 0
        conn = self._get_connection()
        try:
            cur = conn.execute(
                "UPDATE video_watchlist SET root_folder_id=? WHERE kind='show' AND tmdb_id=?",
                (rid, int(tmdb_id)))
            # Keep the wishlist and the library row in step, exactly as
            # set_wishlist_root_folder does — otherwise choosing a Library on the
            # watchlist would leave episodes already queued heading elsewhere.
            # Clearing does NOT cascade: 'Default' means 'inherit', and wiping
            # the others would unassign a title nobody asked to touch.
            if rid is not None:
                conn.execute(
                    "UPDATE video_wishlist SET root_folder_id=? WHERE kind='episode' AND tmdb_id=?",
                    (rid, int(tmdb_id)))
                conn.execute("UPDATE shows SET root_folder_id=? WHERE tmdb_id=?",
                             (rid, int(tmdb_id)))
            conn.commit()
            return cur.rowcount
        except (sqlite3.Error, ValueError, TypeError):
            logger.exception("set_watchlist_root_folder failed (%s)", tmdb_id)
            return 0
        finally:
            conn.close()

    def resolve_destination_library(self, kind: str, tmdb_id) -> int | None:
        """The Library a title should be filed into, for a caller that has only a
        tmdb id — the single answer every wishlist-creating path needs.

        Order: the title's own library row (it already lives somewhere, so keep
        it there), else — for shows — the Library chosen when you FOLLOWED it.
        None when nothing has an opinion, which callers treat as 'use primary'.

        The library row wins over the watchlist choice deliberately: once a show
        is on disk, moving new episodes somewhere else would split it in two,
        and the fix for a genuinely wrong Library is to reassign the show (which
        ``set_watchlist_root_folder`` does to all three tables at once)."""
        owned = self.root_folder_id_for_tmdb(kind, tmdb_id)
        if owned:
            return owned
        if str(kind or "").lower() != "show" or tmdb_id is None:
            return None
        try:
            tmdb_id = int(tmdb_id)
        except (TypeError, ValueError):
            return None
        conn = self._get_connection()
        try:
            row = conn.execute(
                "SELECT root_folder_id FROM video_watchlist "
                "WHERE kind='show' AND tmdb_id=? AND root_folder_id IS NOT NULL LIMIT 1",
                (tmdb_id,)).fetchone()
            return row["root_folder_id"] if row else None
        except sqlite3.Error:
            logger.exception("resolve_destination_library failed (%s %s)", kind, tmdb_id)
            return None
        finally:
            conn.close()

    def set_wishlist_root_folder(self, kind: str, tmdb_id, root_folder_id) -> int:
        """Direct a WISHED title at a specific Library, before it exists on disk.

        ``set_item_root_folder`` can only reassign a title that already has a
        movies/shows row — which a wished title usually doesn't, and that gap is
        why everything unattended landed in the primary ('All Movies'/'All TV')
        Library no matter what it was. This writes the same decision one step
        earlier, onto the wishlist rows the drain reads.

        Metadata only — nothing on disk moves. It decides where the next grab
        for this title goes: every acquisition path resolves its destination
        through ``_root_folder_for_item``, which reads
        ``COALESCE(video_wishlist.root_folder_id, <the library row's>)``.

        Setting a Library also stamps the linked library row (if any), so a
        title that IS already in the library doesn't end up split — new episodes
        in one Library, the existing ones in another. CLEARING deliberately does
        not: 'Default' means 'inherit from the library row', so wiping that row
        too would make choosing Default unassign a title it was never asked to
        touch.

        Returns the number of wishlist rows changed. 0 for an unknown Library, or
        one whose ``content_kind`` doesn't match — a movie filed under a TV
        Library would send every future grab to the wrong tree, silently."""
        want = str(kind or "").lower()
        row_kind = {"movie": "movie", "show": "episode"}.get(want)
        if not row_kind or not tmdb_id:
            return 0
        rid = None
        if root_folder_id not in (None, "", "null"):
            try:
                rid = int(root_folder_id)
            except (TypeError, ValueError):
                return 0
            row = self.get_root_folder(rid)
            if not row or str(row.get("content_kind") or "") != want:
                return 0
        conn = self._get_connection()
        try:
            cur = conn.execute(
                "UPDATE video_wishlist SET root_folder_id=? WHERE kind=? AND tmdb_id=?",
                (rid, row_kind, int(tmdb_id)))
            if rid is not None:
                conn.execute(
                    "UPDATE %s SET root_folder_id=? WHERE tmdb_id=?"
                    % ("movies" if want == "movie" else "shows"), (rid, int(tmdb_id)))
            conn.commit()
            return cur.rowcount
        except (sqlite3.Error, ValueError, TypeError):
            logger.exception("set_wishlist_root_folder failed (%s %s)", kind, tmdb_id)
            return 0
        finally:
            conn.close()

    # ── named quality profiles (the schema's day-one quality_profiles table,
    #    finally in service; arr-parity P2). ``items`` holds the FULL normalized
    #    profile blob as JSON (the original 'list of quality names' idea grew
    #    into the rich tiers/rejects/preferences profile); ``cutoff`` mirrors
    #    cutoff_resolution for at-a-glance SQL. ─────────────────────────────────
    def named_quality_profiles(self) -> list:
        conn = self._get_connection()
        try:
            return [dict(r) for r in conn.execute(
                "SELECT id, name, cutoff, items FROM quality_profiles ORDER BY name COLLATE NOCASE")]
        except sqlite3.Error:
            logger.exception("named_quality_profiles failed")
            return []
        finally:
            conn.close()

    def get_named_quality_profile(self, profile_id) -> dict | None:
        conn = self._get_connection()
        try:
            row = conn.execute("SELECT id, name, cutoff, items FROM quality_profiles WHERE id=?",
                               (int(profile_id),)).fetchone()
            return dict(row) if row else None
        except (sqlite3.Error, TypeError, ValueError):
            return None
        finally:
            conn.close()

    def upsert_named_quality_profile(self, profile_id, name, items_json, cutoff):
        """Create (profile_id None) or update a named profile row; returns the id.
        The name is UNIQUE — collisions get a numeric suffix rather than failing."""
        conn = self._get_connection()
        try:
            base = str(name or "").strip() or "Unnamed profile"
            for attempt in range(20):
                candidate = base if attempt == 0 else "%s (%d)" % (base, attempt + 1)
                try:
                    if profile_id is None:
                        cur = conn.execute(
                            "INSERT INTO quality_profiles (name, cutoff, items) VALUES (?, ?, ?)",
                            (candidate, cutoff, items_json))
                        conn.commit()
                        return cur.lastrowid
                    conn.execute(
                        "UPDATE quality_profiles SET name=?, cutoff=?, items=? WHERE id=?",
                        (candidate, cutoff, items_json, int(profile_id)))
                    conn.commit()
                    return int(profile_id)
                except sqlite3.IntegrityError:
                    conn.rollback()   # duplicate name → next suffix
            return None
        except sqlite3.Error:
            logger.exception("upsert_named_quality_profile failed")
            return None
        finally:
            conn.close()

    def delete_named_quality_profile(self, profile_id) -> bool:
        """Delete a named profile. The schema's ON DELETE SET NULL clears
        movies/shows assignments; migrated columns (wishlist/downloads) degrade
        via profile_by_id's dangling-id rule."""
        conn = self._get_connection()
        try:
            cur = conn.execute("DELETE FROM quality_profiles WHERE id=?", (int(profile_id),))
            conn.commit()
            return cur.rowcount > 0
        except (sqlite3.Error, TypeError, ValueError):
            logger.exception("delete_named_quality_profile failed")
            return False
        finally:
            conn.close()

    def quality_profile_id_for(self, kind: str, *, tmdb_id=None, library_id=None):
        """The per-title quality-profile id for a movie/show (P2), or None for
        the Default. Library row wins; a wishlist row's assignment covers
        titles not in the library yet."""
        tbl = "movies" if kind == "movie" else "shows"
        conn = self._get_connection()
        try:
            if library_id:
                row = conn.execute(f"SELECT quality_profile_id FROM {tbl} WHERE id=?",
                                   (int(library_id),)).fetchone()
                if row and row[0]:
                    return row[0]
            if tmdb_id:
                row = conn.execute(f"SELECT quality_profile_id FROM {tbl} WHERE tmdb_id=?",
                                   (int(tmdb_id),)).fetchone()
                if row and row[0]:
                    return row[0]
                row = conn.execute(
                    "SELECT quality_profile_id FROM video_wishlist "
                    "WHERE tmdb_id=? AND quality_profile_id IS NOT NULL LIMIT 1",
                    (int(tmdb_id),)).fetchone()
                if row and row[0]:
                    return row[0]
            return None
        except sqlite3.Error:
            logger.exception("quality_profile_id_for failed")
            return None
        finally:
            conn.close()

    def set_title_quality_profile(self, kind: str, library_id, profile_id) -> bool:
        """Assign a quality profile to an owned movie/show (NULL/0 = Default).
        Also stamps the title's wishlist rows so in-flight wishes follow."""
        tbl = "movies" if kind == "movie" else "shows"
        pid = int(profile_id) if profile_id else None
        conn = self._get_connection()
        try:
            cur = conn.execute(f"UPDATE {tbl} SET quality_profile_id=? WHERE id=?",
                               (pid, int(library_id)))
            row = conn.execute(f"SELECT tmdb_id FROM {tbl} WHERE id=?",
                               (int(library_id),)).fetchone()
            if row and row[0]:
                conn.execute("UPDATE video_wishlist SET quality_profile_id=? WHERE tmdb_id=?",
                             (pid, int(row[0])))
            conn.commit()
            return cur.rowcount > 0
        except sqlite3.Error:
            logger.exception("set_title_quality_profile failed")
            return False
        finally:
            conn.close()

    def set_show_series_type(self, library_id, series_type) -> bool:
        """Set a show's series type (P8): 'standard' | 'daily' | 'anime'. NULL/
        'standard' means standard. Drives episode query building in the drain."""
        st = str(series_type or "").strip().lower()
        if st not in ("standard", "daily", "anime"):
            return False
        conn = self._get_connection()
        try:
            cur = conn.execute("UPDATE shows SET series_type=? WHERE id=?",
                               (None if st == "standard" else st, int(library_id)))
            conn.commit()
            return cur.rowcount > 0
        except sqlite3.Error:
            logger.exception("set_show_series_type failed")
            return False
        finally:
            conn.close()

    def episode_air_date(self, show_tmdb_id, season_number, episode_number):
        """One episode's air date (ISO), or None when it isn't in the library.

        Daily series are released by DATE rather than SxxExx ('The.Daily.Show.
        2026.07.08'), so this is the only thing that identifies such a release —
        the sibling of ``episode_absolute_number`` for the other naming
        convention the scope check understands."""
        try:
            s, e = int(season_number), int(episode_number)
        except (TypeError, ValueError):
            return None
        conn = self._get_connection()
        try:
            row = conn.execute(
                "SELECT air_date FROM episodes "
                "WHERE show_id = (SELECT id FROM shows WHERE tmdb_id=? ORDER BY id LIMIT 1) "
                "AND season_number=? AND episode_number=? LIMIT 1",
                (int(show_tmdb_id), s, e)).fetchone()
            return (str(row["air_date"])[:10] or None) if row and row["air_date"] else None
        except (sqlite3.Error, TypeError, ValueError):
            return None
        finally:
            conn.close()

    def episode_absolute_number(self, show_tmdb_id, season_number, episode_number):
        """The episode's ABSOLUTE number (anime numbering, P8): its 1-based position
        in the show's aired order, specials (season 0) excluded. None when the show
        or its episode list isn't in the library (can't be derived)."""
        try:
            s, e = int(season_number), int(episode_number)
        except (TypeError, ValueError):
            return None
        if s < 1 or e < 1:
            return None
        conn = self._get_connection()
        try:
            row = conn.execute(
                "SELECT COUNT(*) AS n, "
                "SUM(CASE WHEN e.season_number=? AND e.episode_number=? THEN 1 ELSE 0 END) AS hit "
                "FROM episodes e "
                # ONE show row only — a show mirrored on two servers would
                # otherwise count every episode twice
                "WHERE e.show_id = (SELECT id FROM shows WHERE tmdb_id=? ORDER BY id LIMIT 1) "
                "AND e.season_number >= 1 "
                "AND (e.season_number < ? OR (e.season_number = ? AND e.episode_number <= ?))",
                (s, e, int(show_tmdb_id), s, s, e)).fetchone()
            # the episode itself must be known — otherwise the count is a guess
            return int(row["n"]) if row and row["hit"] else None
        except (sqlite3.Error, TypeError, ValueError):
            return None
        finally:
            conn.close()

    # ── requests (in-app Overseerr; arr-parity P4) ────────────────────────────
    def add_video_request(self, *, profile_id, requester_name, kind, tmdb_id, title,
                          year=None, poster_url=None, note=None, monitor="future"):
        """File a request. One PENDING request per (profile, kind, tmdb) —
        re-asking returns the existing id ('already'). Returns (id, created)."""
        conn = self._get_connection()
        try:
            row = conn.execute(
                "SELECT id FROM video_requests WHERE profile_id=? AND kind=? AND tmdb_id=? "
                "AND status='pending'", (int(profile_id), kind, int(tmdb_id))).fetchone()
            if row:
                return row["id"], False
            cur = conn.execute(
                "INSERT INTO video_requests (profile_id, requester_name, kind, tmdb_id, title, "
                "year, poster_url, note, monitor) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (int(profile_id), requester_name, kind, int(tmdb_id), title, year,
                 poster_url, note, monitor or "future"))
            conn.commit()
            return cur.lastrowid, True
        except sqlite3.Error:
            logger.exception("add_video_request failed")
            return None, False
        finally:
            conn.close()

    def list_video_requests(self, *, profile_id=None, status=None, limit=200) -> list:
        """Requests, newest first. profile_id None = all (admin); else own only."""
        where, args = [], []
        if profile_id is not None:
            where.append("profile_id=?")
            args.append(int(profile_id))
        if status:
            where.append("status=?")
            args.append(status)
        sql = ("SELECT * FROM video_requests" +
               ((" WHERE " + " AND ".join(where)) if where else "") +
               " ORDER BY (status='pending') DESC, created_at DESC, id DESC LIMIT ?")
        conn = self._get_connection()
        try:
            return [dict(r) for r in conn.execute(sql, args + [max(1, int(limit))])]
        except sqlite3.Error:
            logger.exception("list_video_requests failed")
            return []
        finally:
            conn.close()

    def get_video_request(self, request_id) -> dict | None:
        conn = self._get_connection()
        try:
            row = conn.execute("SELECT * FROM video_requests WHERE id=?",
                               (int(request_id),)).fetchone()
            return dict(row) if row else None
        except (sqlite3.Error, TypeError, ValueError):
            return None
        finally:
            conn.close()

    def resolve_video_request(self, request_id, *, status, resolved_by, admin_response=None) -> bool:
        """Approve/deny a PENDING request (terminal states never flip back)."""
        if status not in ("approved", "denied"):
            return False
        conn = self._get_connection()
        try:
            cur = conn.execute(
                "UPDATE video_requests SET status=?, admin_response=?, resolved_by=?, "
                "resolved_at=datetime('now') WHERE id=? AND status='pending'",
                (status, admin_response, int(resolved_by), int(request_id)))
            conn.commit()
            return cur.rowcount > 0
        except sqlite3.Error:
            logger.exception("resolve_video_request failed")
            return False
        finally:
            conn.close()

    def delete_video_request(self, request_id, profile_id=None, include_resolved=False) -> bool:
        """Delete a request row.

        ``profile_id`` scopes to that profile's own rows (member); None = any
        row (admin). ``include_resolved`` False keeps the original withdraw
        semantics (pending only); True also removes approved/denied rows —
        the "remove from history" action. Approval side effects (wishlist /
        watchlist entries) are never touched; this only clears the record."""
        where, args = ["id=?"], [int(request_id)]
        if profile_id is not None:
            where.append("profile_id=?")
            args.append(int(profile_id))
        if not include_resolved:
            where.append("status='pending'")
        conn = self._get_connection()
        try:
            cur = conn.execute(
                "DELETE FROM video_requests WHERE " + " AND ".join(where), args)
            conn.commit()
            return cur.rowcount > 0
        except sqlite3.Error:
            logger.exception("delete_video_request failed")
            return False
        finally:
            conn.close()

    def delete_profile_data(self, profile_id: int) -> int:
        """Remove every video-side row belonging to a (music-side) profile —
        called when a profile is deleted so requests/issues don't orphan.
        Schema-derived like the music sweep: any table with an exact
        ``profile_id`` column is cleaned (``quality_profile_id`` etc. never
        match). Returns rows removed."""
        if int(profile_id) == 1:
            return 0
        removed = 0
        conn = self._get_connection()
        try:
            tables = [r[0] for r in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' "
                "AND name NOT LIKE 'sqlite_%'").fetchall()]
            for table in tables:
                try:
                    cols = {c[1] for c in conn.execute(
                        f"PRAGMA table_info({table})").fetchall()}   # noqa: S608 - name from sqlite_master
                    if 'profile_id' in cols:
                        cur = conn.execute(
                            f"DELETE FROM {table} WHERE profile_id = ?",   # noqa: S608
                            (int(profile_id),))
                        removed += cur.rowcount
                except sqlite3.Error:
                    logger.debug("video profile sweep: %s failed", table, exc_info=True)
            conn.commit()
            if removed:
                logger.info("Removed %d video-side row(s) for deleted profile %s",
                            removed, profile_id)
            return removed
        except sqlite3.Error:
            logger.exception("delete_profile_data failed")
            return removed
        finally:
            conn.close()

    def clear_resolved_video_requests(self, profile_id=None) -> int:
        """Remove all approved/denied request rows (admin housekeeping;
        profile_id scopes to one member's history). Pending rows are never
        touched. Returns the number of rows removed."""
        where = "status IN ('approved','denied')"
        args = []
        if profile_id is not None:
            where += " AND profile_id=?"
            args.append(int(profile_id))
        conn = self._get_connection()
        try:
            cur = conn.execute("DELETE FROM video_requests WHERE " + where, args)
            conn.commit()
            return cur.rowcount
        except sqlite3.Error:
            logger.exception("clear_resolved_video_requests failed")
            return 0
        finally:
            conn.close()

    def video_requests_status_counts(self, profile_id=None) -> dict:
        """{pending, approved, denied} counts for the page tabs (all rows for
        admins, own rows for members)."""
        counts = {"pending": 0, "approved": 0, "denied": 0}
        sql = "SELECT status, COUNT(*) FROM video_requests"
        args = []
        if profile_id is not None:
            sql += " WHERE profile_id=?"
            args.append(int(profile_id))
        sql += " GROUP BY status"
        conn = self._get_connection()
        try:
            for status, n in conn.execute(sql, args).fetchall():
                if status in counts:
                    counts[status] = n
            return counts
        except sqlite3.Error:
            return counts
        finally:
            conn.close()

    def annotate_requests_in_library(self, rows) -> None:
        """Stamp each request dict with ``in_library`` — whether a library row
        (movies/shows) exists for its tmdb id. This is what lets an approved
        request show 'In library' instead of sitting ambiguously forever."""
        wanted = {"movie": set(), "show": set()}
        for r in rows:
            kind, tid = r.get("kind"), r.get("tmdb_id")
            if kind in wanted and tid:
                wanted[kind].add(int(tid))
        owned = {"movie": set(), "show": set()}
        conn = self._get_connection()
        try:
            for kind, table in (("movie", "movies"), ("show", "shows")):
                ids = list(wanted[kind])
                if not ids:
                    continue
                ph = ",".join("?" * len(ids))
                owned[kind] = {row[0] for row in conn.execute(
                    f"SELECT DISTINCT tmdb_id FROM {table} WHERE tmdb_id IN ({ph})", ids)}
        except sqlite3.Error:
            logger.exception("annotate_requests_in_library failed")
        finally:
            conn.close()
        for r in rows:
            r["in_library"] = bool(r.get("tmdb_id")) and \
                int(r["tmdb_id"]) in owned.get(r.get("kind"), set())

    def video_requests_pending_count(self, profile_id=None) -> int:
        conn = self._get_connection()
        try:
            if profile_id is None:
                return conn.execute(
                    "SELECT COUNT(*) FROM video_requests WHERE status='pending'").fetchone()[0]
            return conn.execute(
                "SELECT COUNT(*) FROM video_requests WHERE status='pending' AND profile_id=?",
                (int(profile_id),)).fetchone()[0]
        except sqlite3.Error:
            return 0
        finally:
            conn.close()

    def wishlist_owned_media_resolutions(self) -> dict:
        """Owned-file resolutions for wishlist rows whose media is in the library —
        {'movie:<tmdb>': 'res,res', 'ep:<tmdb>:<s>:<e>': 'res'}. Feeds the wishlist
        page's upgrade-watch badges (owned + still wishlisted = chasing better)."""
        conn = self._get_connection()
        out: dict = {}
        try:
            for r in conn.execute(
                    "SELECT w.tmdb_id, GROUP_CONCAT(f.resolution) AS res FROM video_wishlist w "
                    "JOIN movies m ON m.tmdb_id=w.tmdb_id AND m.has_file=1 "
                    "JOIN media_files f ON f.movie_id=m.id "
                    "WHERE w.kind='movie' GROUP BY w.tmdb_id"):
                out["movie:%s" % r["tmdb_id"]] = r["res"]
            for r in conn.execute(
                    "SELECT w.tmdb_id AS t, w.season_number AS sn, w.episode_number AS en, "
                    "GROUP_CONCAT(f.resolution) AS res FROM video_wishlist w "
                    "JOIN shows s ON s.tmdb_id=w.tmdb_id "
                    "JOIN episodes e ON e.show_id=s.id AND e.season_number=w.season_number "
                    "  AND e.episode_number=w.episode_number AND e.has_file=1 "
                    "JOIN media_files f ON f.episode_id=e.id "
                    "WHERE w.kind='episode' GROUP BY w.tmdb_id, w.season_number, w.episode_number"):
                out["ep:%s:%s:%s" % (r["t"], r["sn"], r["en"])] = r["res"]
            return out
        except sqlite3.Error:
            logger.exception("wishlist_owned_media_resolutions failed")
            return out
        finally:
            conn.close()

    def wishlist_keys_for_shows(self, show_tmdb_ids) -> dict:
        """{show_tmdb_id: set('S_E')} of episodes already wishlisted — lets the
        calendar's 'add missing' button skip what's already queued."""
        out: dict = {}
        ids = [int(x) for x in (show_tmdb_ids or []) if x]
        if not ids:
            return out
        conn = self._get_connection()
        try:
            for i in range(0, len(ids), 400):
                chunk = ids[i:i + 400]
                ph = ",".join("?" * len(chunk))
                for r in conn.execute(
                        f"SELECT tmdb_id, season_number, episode_number FROM video_wishlist "
                        f"WHERE kind='episode' AND tmdb_id IN ({ph})", chunk):
                    out.setdefault(r["tmdb_id"], set()).add("%s_%s" % (r["season_number"], r["episode_number"]))
            return out
        finally:
            conn.close()

    def wishlist_art_backfill_targets(self) -> list:
        """Distinct (show_tmdb_id, season) with episode rows missing a still OR a
        season poster — one tmdb_season fetch per group fills both (cheap backfill
        for rows added before art-capture existed)."""
        conn = self._get_connection()
        try:
            rows = conn.execute(
                "SELECT DISTINCT tmdb_id, season_number FROM video_wishlist "
                "WHERE kind='episode' AND tmdb_id IS NOT NULL AND season_number IS NOT NULL "
                "AND ((still_url IS NULL OR still_url='') OR "
                "     (season_poster_url IS NULL OR season_poster_url='') OR "
                "     (episode_overview IS NULL OR episode_overview=''))").fetchall()
            return [{"tmdb_id": r["tmdb_id"], "season_number": r["season_number"]} for r in rows]
        finally:
            conn.close()

    def wishlist_movies_missing_art(self, limit: int = 40) -> list:
        """Movie wishlist rows with no poster yet — added while upcoming, before TMDB had art.
        Bounded; the movie art-backfill re-fetches these so they stop rendering art-less."""
        conn = self._get_connection()
        try:
            return [{"tmdb_id": r["tmdb_id"], "title": r["title"]} for r in conn.execute(
                "SELECT tmdb_id, title FROM video_wishlist "
                "WHERE kind='movie' AND tmdb_id IS NOT NULL "
                "AND (poster_url IS NULL OR TRIM(poster_url)='') "
                "ORDER BY id DESC LIMIT ?", (int(limit),))]
        except sqlite3.Error:
            logger.exception("wishlist_movies_missing_art failed")
            return []
        finally:
            conn.close()

    def set_wishlist_movie_art(self, tmdb_id, poster_url=None, year=None) -> bool:
        """Fill a wishlist movie's poster / year once TMDB has them. Only fills blanks
        (COALESCE keeps any art already there), so it never clobbers. Returns True if updated."""
        if not poster_url and not year:
            return False
        conn = self._get_connection()
        try:
            cur = conn.execute(
                "UPDATE video_wishlist SET poster_url=COALESCE(poster_url, ?), year=COALESCE(year, ?) "
                "WHERE kind='movie' AND tmdb_id=?",
                (poster_url or None, year or None, int(tmdb_id)))
            conn.commit()
            return cur.rowcount > 0
        except sqlite3.Error:
            logger.exception("set_wishlist_movie_art failed (%s)", tmdb_id)
            return False
        finally:
            conn.close()

    def set_wishlist_still(self, show_tmdb_id, season_number, episode_number, still_url) -> bool:
        """Fill a single episode's still (only if it doesn't already have one)."""
        if not still_url:
            return False
        conn = self._get_connection()
        try:
            cur = conn.execute(
                "UPDATE video_wishlist SET still_url=? WHERE kind='episode' AND tmdb_id=? "
                "AND season_number=? AND episode_number=? AND (still_url IS NULL OR still_url='')",
                (still_url, int(show_tmdb_id), int(season_number), int(episode_number)))
            conn.commit()
            return cur.rowcount > 0
        finally:
            conn.close()

    def set_wishlist_episode_overview(self, show_tmdb_id, season_number, episode_number, overview) -> bool:
        """Fill a single episode's synopsis (only if it doesn't already have one)."""
        if not overview:
            return False
        conn = self._get_connection()
        try:
            cur = conn.execute(
                "UPDATE video_wishlist SET episode_overview=? WHERE kind='episode' AND tmdb_id=? "
                "AND season_number=? AND episode_number=? AND (episode_overview IS NULL OR episode_overview='')",
                (overview, int(show_tmdb_id), int(season_number), int(episode_number)))
            conn.commit()
            return cur.rowcount > 0
        finally:
            conn.close()

    def set_wishlist_season_poster(self, show_tmdb_id, season_number, poster_url) -> int:
        """Fill the season poster on every episode row of a season that lacks one."""
        if not poster_url:
            return 0
        conn = self._get_connection()
        try:
            cur = conn.execute(
                "UPDATE video_wishlist SET season_poster_url=? WHERE kind='episode' AND tmdb_id=? "
                "AND season_number=? AND (season_poster_url IS NULL OR season_poster_url='')",
                (poster_url, int(show_tmdb_id), int(season_number)))
            conn.commit()
            return cur.rowcount
        finally:
            conn.close()

    def wishlist_state(self, *, movie_ids=None, show_tmdb_id=None) -> dict:
        """Hydration: which of ``movie_ids`` are wishlisted, and which episode
        keys ('S_E') of ``show_tmdb_id`` are. Returns {movies:set, episodes:set}."""
        out = {"movies": set(), "episodes": set()}
        conn = self._get_connection()
        try:
            ids = [int(x) for x in (movie_ids or []) if x]
            for i in range(0, len(ids), 400):
                chunk = ids[i:i + 400]
                ph = ",".join("?" * len(chunk))
                for r in conn.execute(
                        f"SELECT tmdb_id FROM video_wishlist WHERE kind='movie' AND tmdb_id IN ({ph})", chunk):
                    out["movies"].add(r["tmdb_id"])
            if show_tmdb_id:
                for r in conn.execute(
                        "SELECT season_number, episode_number FROM video_wishlist "
                        "WHERE kind='episode' AND tmdb_id=?", (int(show_tmdb_id),)):
                    out["episodes"].add("%s_%s" % (r["season_number"], r["episode_number"]))
            return out
        finally:
            conn.close()

    # ── YouTube channels (bridged onto the watchlist/wishlist tables) ─────────
    # A followed CHANNEL is a video_watchlist row (kind='channel', source='youtube',
    # source_id=channel id). Its wished VIDEOS are video_wishlist rows
    # (kind='video', source_id=video id, parent_source_id=channel id). tmdb_id on
    # both carries the channel's surrogate so existing dedup/grouping just works.
    def add_channel_to_watchlist(self, channel: dict) -> bool:
        """Follow a YouTube channel. ``channel`` = {youtube_id, title, avatar_url?}.
        Idempotent upsert on the channel surrogate. Returns True on success."""
        cid = (channel or {}).get("youtube_id")
        title = (channel or {}).get("title")
        if not cid or not title:
            return False
        conn = self._get_connection()
        try:
            was = conn.execute(
                "SELECT state FROM video_watchlist WHERE kind='channel' AND source_id=?",
                (cid,)).fetchone()
            conn.execute(
                """INSERT INTO video_watchlist (kind, tmdb_id, title, poster_url, source, source_id, state)
                   VALUES ('channel', ?, ?, ?, 'youtube', ?, 'follow')
                   ON CONFLICT(kind, tmdb_id) DO UPDATE SET
                       state='follow', title=excluded.title,
                       poster_url=COALESCE(excluded.poster_url, video_watchlist.poster_url),
                       source='youtube', source_id=excluded.source_id""",
                (youtube_surrogate_id(cid), title, channel.get("avatar_url"), cid))
            conn.commit()
            if not (was and was["state"] == "follow"):
                _publish_video_event("video_watchlist_added", {"kind": "channel", "title": title})
            return True
        except Exception:
            logger.exception("add_channel_to_watchlist failed (%s)", cid)
            return False
        finally:
            conn.close()

    def remove_channel_from_watchlist(self, youtube_id: str) -> bool:
        """Un-follow a channel (hard delete — channels have no airing-default to
        guard against, so no tombstone). Its already-wished videos are left alone."""
        if not youtube_id:
            return False
        conn = self._get_connection()
        try:
            was = conn.execute(
                "SELECT title FROM video_watchlist WHERE kind='channel' AND source_id=? AND state='follow'",
                (youtube_id,)).fetchone()
            cur = conn.execute("DELETE FROM video_watchlist WHERE kind='channel' AND source_id=?", (youtube_id,))
            conn.commit()
            if was and cur.rowcount:
                _publish_video_event("video_watchlist_removed",
                                     {"kind": "channel", "title": was["title"] or ""})
            return True
        finally:
            conn.close()

    def get_watchlist_lookback(self, kind: str, tmdb_id) -> dict | None:
        """A followed person/studio's back-catalog window: {tmdb_id, title, date_added,
        lookback_years} (0/NULL = forward-only, N = years, -1 = everything). None if not
        followed. Shared by the person + studio settings modals."""
        if kind not in ("person", "studio"):
            return None
        conn = self._get_connection()
        try:
            r = conn.execute("SELECT tmdb_id, title, date_added, lookback_years FROM video_watchlist "
                             "WHERE kind=? AND tmdb_id=? AND state='follow'",
                             (kind, int(tmdb_id))).fetchone()
            if not r:
                return None
            d = dict(r)
            d["lookback_years"] = int(d["lookback_years"]) if d["lookback_years"] is not None else 0
            return d
        except (sqlite3.Error, TypeError, ValueError):
            logger.exception("get_watchlist_lookback failed (%s %s)", kind, tmdb_id)
            return None
        finally:
            conn.close()

    def set_watchlist_lookback(self, kind: str, tmdb_id, lookback_years) -> bool:
        """Set a followed person/studio's back-catalog window (0=forward-only, N=years,
        -1=everything). Returns True if a followed row was updated."""
        if kind not in ("person", "studio"):
            return False
        try:
            lb = int(lookback_years)
        except (TypeError, ValueError):
            return False
        lb = max(-1, min(100, lb))
        conn = self._get_connection()
        try:
            cur = conn.execute("UPDATE video_watchlist SET lookback_years=? "
                               "WHERE kind=? AND tmdb_id=? AND state='follow'",
                               (lb, kind, int(tmdb_id)))
            conn.commit()
            return cur.rowcount > 0
        except sqlite3.Error:
            logger.exception("set_watchlist_lookback failed (%s %s)", kind, tmdb_id)
            return False
        finally:
            conn.close()

    # kind-specific delegates (keep the existing person call sites + a studio pair).
    def get_person_lookback(self, tmdb_id) -> dict | None:
        return self.get_watchlist_lookback("person", tmdb_id)

    def set_person_lookback(self, tmdb_id, lookback_years) -> bool:
        return self.set_watchlist_lookback("person", tmdb_id, lookback_years)

    def get_studio_lookback(self, tmdb_id) -> dict | None:
        return self.get_watchlist_lookback("studio", tmdb_id)

    def set_studio_lookback(self, tmdb_id, lookback_years) -> bool:
        return self.set_watchlist_lookback("studio", tmdb_id, lookback_years)

    def list_watchlist_channels(self) -> list[dict]:
        """Followed channels (newest first): ``video_count`` is the REMEMBERED catalog
        size (from the cache, fills in as the channel is enriched/opened), plus how
        many of its videos are wished."""
        conn = self._get_connection()
        try:
            rows = conn.execute(
                "SELECT w.title, w.poster_url, w.source_id, w.date_added, "
                "(SELECT COUNT(*) FROM youtube_channel_videos cv WHERE cv.channel_id = w.source_id) AS video_count, "
                "(SELECT COUNT(*) FROM video_wishlist v WHERE v.kind='video' "
                " AND v.parent_source_id = w.source_id) AS wished_count "
                "FROM video_watchlist w WHERE w.kind='channel' AND w.state='follow' "
                "ORDER BY w.date_added DESC, w.id DESC").fetchall()
            return [{"kind": "channel", "youtube_id": r["source_id"], "title": r["title"],
                     "poster_url": r["poster_url"], "video_count": r["video_count"],
                     "wished_count": r["wished_count"], "date_added": r["date_added"]} for r in rows]
        finally:
            conn.close()

    def query_channel_library(self, *, search=None, letter=None, sort="title",
                              page=1, limit=75) -> dict:
        """One page of the Library's Channels tab: FOLLOWED channels ∪ channels
        you HAVE DOWNLOADS FROM (a library shows what you own — a one-off grab
        from an unfollowed channel still belongs here). Same paged shape as
        query_library. ``owned_count`` = completed downloads in the permanent
        history; ``video_count`` = remembered catalog size; unfollowed channels
        fill title/avatar from the channel-meta cache."""
        try:
            page = max(1, int(page or 1))
            limit = max(1, min(500, int(limit or 75)))
        except (TypeError, ValueError):
            page, limit = 1, 75
        base = (
            "WITH chans AS ("
            "  SELECT w.source_id AS cid, w.title AS wl_title, w.poster_url AS wl_poster, "
            "         1 AS followed, w.date_added AS added "
            "  FROM video_watchlist w WHERE w.kind='channel' AND w.state='follow' "
            "  UNION ALL "
            # ownership = ON DISK (pruned excluded): an unfollowed channel whose
            # every download was deleted/ghost-cleaned leaves the library tab.
            "  SELECT h.channel_id, NULL, NULL, 0, MAX(h.completed_at) "
            "  FROM video_download_history h "
            "  WHERE h.source='youtube' AND h.outcome='completed' AND h.channel_id IS NOT NULL "
            "    AND h.pruned_at IS NULL "
            "    AND h.channel_id NOT IN (SELECT source_id FROM video_watchlist "
            "                             WHERE kind='channel' AND state='follow') "
            "  GROUP BY h.channel_id"
            "), named AS ("
            "  SELECT c.cid, COALESCE(c.wl_title, m.title, c.cid) AS title, "
            "         COALESCE(c.wl_poster, m.avatar_url) AS poster_url, "
            "         c.followed, c.added "
            "  FROM chans c LEFT JOIN youtube_channel_meta m ON m.channel_id = c.cid"
            ") ")
        where, params = [], []
        if search:
            where.append("title LIKE ? COLLATE NOCASE")
            params.append("%" + str(search) + "%")
        if letter and letter != "all":
            if letter == "#":
                where.append("substr(UPPER(title), 1, 1) NOT BETWEEN 'A' AND 'Z'")
            else:
                where.append("title LIKE ? COLLATE NOCASE")
                params.append(str(letter) + "%")
        w = (" WHERE " + " AND ".join(where)) if where else ""
        order = "added DESC" if sort == "added" else "title COLLATE NOCASE"
        conn = self._get_connection()
        try:
            total = conn.execute(base + f"SELECT COUNT(*) FROM named{w}", params).fetchone()[0]
            rows = conn.execute(
                base + "SELECT cid, title, poster_url, followed, "
                "(SELECT COUNT(*) FROM youtube_channel_videos cv "
                "  WHERE cv.channel_id = named.cid) AS video_count, "
                "(SELECT COUNT(DISTINCT h.media_id) FROM video_download_history h "
                "  WHERE h.source='youtube' AND h.outcome='completed' "
                "  AND h.pruned_at IS NULL "
                "  AND h.channel_id = named.cid) AS owned_count "
                f"FROM named{w} ORDER BY {order} LIMIT ? OFFSET ?",
                (*params, limit, (page - 1) * limit)).fetchall()
            pages = max(1, (total + limit - 1) // limit)
            return {"items": [{"kind": "channel", "id": r["cid"],
                               "title": r["title"], "poster_url": r["poster_url"],
                               "followed": bool(r["followed"]),
                               "video_count": r["video_count"],
                               "owned_count": r["owned_count"]} for r in rows],
                    "pagination": {"page": page, "total_pages": pages, "total_count": total,
                                   "has_prev": page > 1, "has_next": page < pages}}
        finally:
            conn.close()

    def channel_watch_state(self, youtube_ids) -> dict:
        """{youtube_id: True} for followed channels — hydrates the Follow button."""
        out: dict = {}
        ids = [str(x) for x in (youtube_ids or []) if x]
        if not ids:
            return out
        conn = self._get_connection()
        try:
            for i in range(0, len(ids), 400):
                chunk = ids[i:i + 400]
                ph = ",".join("?" * len(chunk))
                for r in conn.execute(
                        f"SELECT source_id FROM video_watchlist WHERE kind='channel' "
                        f"AND state='follow' AND source_id IN ({ph})", chunk):
                    out[r["source_id"]] = True
            return out
        finally:
            conn.close()

    # A followed PLAYLIST mirrors a channel: a video_watchlist row (kind='playlist',
    # source='youtube', source_id=PL id). Same surrogate scheme so dedup just works.
    def add_playlist_to_watchlist(self, playlist: dict) -> bool:
        """Follow a YouTube playlist. ``playlist`` = {playlist_id, title, thumbnail_url?}."""
        pid = (playlist or {}).get("playlist_id")
        title = (playlist or {}).get("title")
        if not pid or not title:
            return False
        conn = self._get_connection()
        try:
            was = conn.execute(
                "SELECT state FROM video_watchlist WHERE kind='playlist' AND source_id=?",
                (pid,)).fetchone()
            conn.execute(
                """INSERT INTO video_watchlist (kind, tmdb_id, title, poster_url, source, source_id, state)
                   VALUES ('playlist', ?, ?, ?, 'youtube', ?, 'follow')
                   ON CONFLICT(kind, tmdb_id) DO UPDATE SET
                       state='follow', title=excluded.title,
                       poster_url=COALESCE(excluded.poster_url, video_watchlist.poster_url),
                       source='youtube', source_id=excluded.source_id""",
                (youtube_surrogate_id(pid), title, (playlist or {}).get("thumbnail_url"), pid))
            conn.commit()
            if not (was and was["state"] == "follow"):
                _publish_video_event("video_watchlist_added", {"kind": "playlist", "title": title})
            return True
        except Exception:
            logger.exception("add_playlist_to_watchlist failed (%s)", pid)
            return False
        finally:
            conn.close()

    def remove_playlist_from_watchlist(self, playlist_id: str) -> bool:
        if not playlist_id:
            return False
        conn = self._get_connection()
        try:
            was = conn.execute(
                "SELECT title FROM video_watchlist WHERE kind='playlist' AND source_id=? AND state='follow'",
                (playlist_id,)).fetchone()
            cur = conn.execute("DELETE FROM video_watchlist WHERE kind='playlist' AND source_id=?", (playlist_id,))
            conn.commit()
            if was and cur.rowcount:
                _publish_video_event("video_watchlist_removed",
                                     {"kind": "playlist", "title": was["title"] or ""})
            return True
        finally:
            conn.close()

    def list_watchlist_playlists(self) -> list[dict]:
        """Followed playlists (newest first), each with its remembered video count
        (cached when the playlist is followed/opened)."""
        conn = self._get_connection()
        try:
            rows = conn.execute(
                "SELECT w.title, w.poster_url, w.source_id, w.date_added, "
                "(SELECT COUNT(*) FROM youtube_channel_videos cv WHERE cv.channel_id = w.source_id) AS video_count "
                "FROM video_watchlist w WHERE w.kind='playlist' AND w.state='follow' "
                "ORDER BY w.date_added DESC, w.id DESC").fetchall()
            return [{"kind": "playlist", "playlist_id": r["source_id"], "title": r["title"],
                     "poster_url": r["poster_url"], "video_count": r["video_count"],
                     "date_added": r["date_added"]} for r in rows]
        finally:
            conn.close()

    def playlist_watch_state(self, playlist_ids) -> dict:
        """{playlist_id: True} for followed playlists — hydrates the Follow button."""
        out: dict = {}
        ids = [str(x) for x in (playlist_ids or []) if x]
        if not ids:
            return out
        conn = self._get_connection()
        try:
            for i in range(0, len(ids), 400):
                chunk = ids[i:i + 400]
                ph = ",".join("?" * len(chunk))
                for r in conn.execute(
                        f"SELECT source_id FROM video_watchlist WHERE kind='playlist' "
                        f"AND state='follow' AND source_id IN ({ph})", chunk):
                    out[r["source_id"]] = True
            return out
        finally:
            conn.close()

    def add_videos_to_wishlist(self, channel: dict, videos: list, *, server_source=None,
                               allow_downloaded: bool = False,
                               added_by_profile_id=None, approved: bool = True,
                               requested_by=None, requested_by_name=None) -> int:
        """Wish for a channel's videos. ``channel`` = {youtube_id, title, avatar_url?};
        ``videos`` = [{youtube_id, title, published_at?, thumbnail_url?, description?}, …].
        Idempotent per video id. Returns the count written."""
        cid = (channel or {}).get("youtube_id")
        ctitle = (channel or {}).get("title")
        if not cid or not ctitle or not videos:
            return 0
        avatar = (channel or {}).get("avatar_url")
        surrogate = youtube_surrogate_id(cid)
        conn = self._get_connection()
        n = 0
        try:
            # Don't re-wish videos already downloaded — re-following a channel must
            # never re-queue everything you already have. ``allow_downloaded`` is
            # the DELIBERATE-single-click exception: with the ✓ downloaded marker
            # visible, a manual wish on an owned video means "get it again"
            # (silently no-oping that click was a dead button).
            downloaded = set() if allow_downloaded else {r["media_id"] for r in conn.execute(
                "SELECT DISTINCT media_id FROM video_download_history "
                "WHERE source='youtube' AND outcome='completed' AND media_id IS NOT NULL")}
            before_count = conn.execute(
                "SELECT COUNT(*) FROM video_wishlist WHERE kind='video' AND parent_source_id=?",
                (cid,)).fetchone()[0]
            for v in videos:
                vid = v.get("youtube_id")
                if not vid or vid in downloaded:
                    continue
                conn.execute(
                    # added_by_profile_id set on INSERT only — see add_movie_to_wishlist.
                    """INSERT INTO video_wishlist
                           (kind, tmdb_id, title, poster_url, episode_title, still_url,
                            episode_overview, air_date, source, source_id, parent_source_id,
                            server_source, added_by_profile_id, approved, requested_by,
                            requested_by_name)
                       VALUES ('video', ?, ?, ?, ?, ?, ?, ?, 'youtube', ?, ?, ?, ?, ?, ?, ?)
                       ON CONFLICT(source_id) WHERE kind='video' DO UPDATE SET
                           title=excluded.title,
                           poster_url=COALESCE(excluded.poster_url, video_wishlist.poster_url),
                           episode_title=COALESCE(excluded.episode_title, video_wishlist.episode_title),
                           still_url=COALESCE(excluded.still_url, video_wishlist.still_url),
                           episode_overview=COALESCE(excluded.episode_overview, video_wishlist.episode_overview),
                           air_date=COALESCE(excluded.air_date, video_wishlist.air_date),
                           -- MAX — see add_movie_to_wishlist.
                           approved=MAX(video_wishlist.approved, excluded.approved),
                           requested_by=COALESCE(video_wishlist.requested_by, excluded.requested_by),
                           requested_by_name=COALESCE(video_wishlist.requested_by_name, excluded.requested_by_name)""",
                    (surrogate, ctitle, avatar, v.get("title"), v.get("thumbnail_url"),
                     v.get("description"), v.get("published_at"), vid, cid, server_source,
                     added_by_profile_id, 1 if approved else 0, requested_by, requested_by_name))
                n += 1
            new_rows = conn.execute(
                "SELECT COUNT(*) FROM video_wishlist WHERE kind='video' AND parent_source_id=?",
                (cid,)).fetchone()[0] - before_count
            conn.commit()
            if new_rows > 0:   # refresh-upserts of already-wished videos don't fire
                _publish_video_event("video_wishlist_item_added",
                                     {"kind": "youtube", "title": ctitle, "count": new_rows})
                if not approved:
                    _publish_video_event("video_request_pending", {
                        "kind": "youtube", "title": ctitle, "count": new_rows,
                        "queue": "wishlist", "tmdb_id": None,
                        "requested_by": requested_by_name})
            return n
        except Exception:
            logger.exception("add_videos_to_wishlist failed (%s)", cid)
            conn.rollback()
            return 0
        finally:
            conn.close()

    @staticmethod
    def _youtube_wishlist_scope(scope: str, source_id: str):
        if not source_id:
            return None
        if scope == "channel":
            return "kind='video' AND parent_source_id=?", [source_id]
        if scope == "video":
            return "kind='video' AND source_id=?", [source_id]
        return None

    def remove_youtube_from_wishlist(self, scope: str, source_id: str,
                                     only_profile_id=None) -> int:
        """Remove wished videos: scope 'channel' (all of a channel, source_id=channel
        id) or 'video' (one, source_id=video id). Returns rows removed.

        ``only_profile_id`` scopes the delete to that profile's own wishes — same
        rule as remove_from_wishlist."""
        sel = self._youtube_wishlist_scope(scope, source_id)
        if sel is None:
            return 0
        where, args = sel
        if only_profile_id is not None:
            where += " AND added_by_profile_id=?"
            args = args + [int(only_profile_id)]
        conn = self._get_connection()
        try:
            cur = conn.execute("DELETE FROM video_wishlist WHERE " + where, args)
            conn.commit()
            return cur.rowcount
        finally:
            conn.close()

    def count_youtube_wishlist_rows(self, scope: str, source_id: str) -> int:
        """How many rows that removal WOULD cover regardless of who added them."""
        sel = self._youtube_wishlist_scope(scope, source_id)
        if sel is None:
            return 0
        where, args = sel
        conn = self._get_connection()
        try:
            return conn.execute(
                "SELECT COUNT(*) c FROM video_wishlist WHERE " + where, args).fetchone()["c"]
        finally:
            conn.close()

    def youtube_video_wish_state(self, video_ids) -> set:
        """Which of ``video_ids`` (youtube ids) are already wished — hydrates the
        per-video buttons on the channel detail page."""
        out: set = set()
        ids = [str(x) for x in (video_ids or []) if x]
        if not ids:
            return out
        conn = self._get_connection()
        try:
            for i in range(0, len(ids), 400):
                chunk = ids[i:i + 400]
                ph = ",".join("?" * len(chunk))
                for r in conn.execute(
                        f"SELECT source_id FROM video_wishlist WHERE kind='video' "
                        f"AND source_id IN ({ph})", chunk):
                    out.add(r["source_id"])
            return out
        finally:
            conn.close()

    def remove_one_video_from_wishlist(self, video_id) -> int:
        """Remove a single wished video by its youtube id. (Thin alias kept explicit
        for the detail page's per-video toggle.)"""
        return self.remove_youtube_from_wishlist("video", video_id)

    def set_wishlist_video_overview(self, video_id, overview) -> bool:
        """Persist a video's lazily-fetched description onto its wishlist row (only
        if it doesn't already have one) so re-opening is instant — mirrors the
        episode-overview backfill. Returns True if a row was updated."""
        if not overview or not video_id:
            return False
        conn = self._get_connection()
        try:
            cur = conn.execute(
                "UPDATE video_wishlist SET episode_overview=? WHERE kind='video' AND source_id=? "
                "AND (episode_overview IS NULL OR episode_overview='')", (overview, str(video_id)))
            conn.commit()
            return cur.rowcount > 0
        finally:
            conn.close()

    def set_wishlist_channel_poster(self, channel_id, poster_url) -> int:
        """Refresh the channel avatar on all of a channel's wished video rows — used
        to backfill avatars that flat listing didn't surface (channel page resolves
        the real avatar). Returns rows updated."""
        if not poster_url or not channel_id:
            return 0
        conn = self._get_connection()
        try:
            cur = conn.execute(
                "UPDATE video_wishlist SET poster_url=? WHERE kind='video' AND parent_source_id=?",
                (poster_url, str(channel_id)))
            conn.commit()
            return cur.rowcount
        finally:
            conn.close()

    def cache_video_dates(self, pairs) -> int:
        """Persist learned YouTube upload dates ([{youtube_id, published_at}, …]).
        Idempotent; only stores non-empty dates. Returns rows written."""
        rows = [(p.get("youtube_id"), p.get("published_at")) for p in (pairs or [])
                if p.get("youtube_id") and p.get("published_at")]
        if not rows:
            return 0
        conn = self._get_connection()
        try:
            conn.executemany(
                "INSERT INTO youtube_video_dates (youtube_id, published_at) VALUES (?, ?) "
                "ON CONFLICT(youtube_id) DO UPDATE SET published_at=excluded.published_at", rows)
            conn.commit()
            return len(rows)
        finally:
            conn.close()

    def get_video_dates(self, video_ids) -> dict:
        """{youtube_id: published_at} for cached ids — hydrates channel year-seasons."""
        out: dict = {}
        ids = [str(x) for x in (video_ids or []) if x]
        if not ids:
            return out
        conn = self._get_connection()
        try:
            for i in range(0, len(ids), 400):
                chunk = ids[i:i + 400]
                ph = ",".join("?" * len(chunk))
                for r in conn.execute(
                        f"SELECT youtube_id, published_at FROM youtube_video_dates WHERE youtube_id IN ({ph})", chunk):
                    if r["published_at"]:
                        out[r["youtube_id"]] = r["published_at"]
            return out
        finally:
            conn.close()

    def get_channel_settings(self, channel_id) -> dict:
        """Per-channel YouTube overrides — ``{custom_name?, quality?}`` — or {} if none.
        ``custom_name`` overrides the show-name (the ``$channel`` folder token); ``quality``
        is a youtube_quality profile that forces a different quality than the global default
        for this channel. Kept in the settings KV store, so no schema change."""
        if not channel_id:
            return {}
        raw = self.get_setting("youtube_channel_settings:" + str(channel_id))
        if not raw:
            return {}
        try:
            import json
            d = json.loads(raw)
            return d if isinstance(d, dict) else {}
        except (ValueError, TypeError):
            return {}

    def set_channel_settings(self, channel_id, settings: dict) -> bool:
        """Persist per-channel overrides (or clear them with an empty/blank dict)."""
        if not channel_id:
            return False
        import json
        clean = settings if isinstance(settings, dict) else {}
        # Drop empty values so a blank form clears the override rather than storing noise.
        clean = {k: v for k, v in clean.items() if v not in (None, "", {})}
        self.set_setting("youtube_channel_settings:" + str(channel_id), json.dumps(clean))
        return True

    def get_playlist_seen(self, playlist_id) -> list:
        """The video ids already accounted for in a followed playlist — the membership
        baseline captured on the first scan (+ additions since). A current member NOT in
        this set is a genuine new addition. Empty until the first scan baselines it."""
        raw = self.get_setting("youtube_playlist_seen:" + str(playlist_id or ""))
        if not raw:
            return []
        try:
            import json
            d = json.loads(raw)
            return [str(x) for x in d] if isinstance(d, list) else []
        except (ValueError, TypeError):
            return []

    def add_playlist_seen(self, playlist_id, video_ids) -> None:
        """Fold ids into a playlist's seen-baseline (union; never shrinks) so later scans
        only surface members added after this point."""
        pid = str(playlist_id or "")
        add = [str(v) for v in (video_ids or []) if v]
        if not pid or not add:
            return
        import json
        merged = set(self.get_playlist_seen(pid))
        merged.update(add)
        self.set_setting("youtube_playlist_seen:" + pid, json.dumps(sorted(merged)))

    def wishlisted_video_ids_for_channel(self, channel_id) -> list:
        """The youtube video ids wished under a channel (the per-video date fallback set)."""
        if not channel_id:
            return []
        conn = self._get_connection()
        try:
            return [r["source_id"] for r in conn.execute(
                "SELECT source_id FROM video_wishlist WHERE kind='video' AND parent_source_id=?",
                (str(channel_id),))]
        finally:
            conn.close()

    def youtube_wishlist_to_download(self, limit: int = 0) -> list:
        """Flat list of wished YouTube videos for the fulfillment worker to grab, newest
        upload first. Each carries what organising + the download row need: the video id,
        its channel, the video title, thumbnail, and upload date. (A completed download
        removes its wishlist row, so this list naturally shrinks as the queue drains.)"""
        conn = self._get_connection()
        try:
            sql = ("SELECT source_id AS video_id, parent_source_id AS channel_id, "
                   "title AS channel_title, episode_title AS video_title, "
                   "still_url AS thumbnail_url, air_date AS published_at "
                   # APPROVAL GATE — see movie_wishlist_to_download.
                   "FROM video_wishlist WHERE approved=1 AND kind='video' AND source='youtube' "
                   "AND source_id IS NOT NULL ORDER BY air_date DESC, id DESC")
            if limit and int(limit) > 0:
                sql += " LIMIT %d" % int(limit)
            return [dict(r) for r in conn.execute(sql)]
        finally:
            conn.close()

    def mark_channel_dates_enriched(self, channel_id, date_count=0, method="innertube") -> None:
        """Record that the background enricher swept this channel (skip re-sweeps).
        ``method`` tags which source produced the dates; legacy rows have NULL and
        get re-enriched once so they upgrade to the InnerTube catalog."""
        if not channel_id:
            return
        conn = self._get_connection()
        try:
            conn.execute(
                "INSERT INTO youtube_channel_enrichment (channel_id, enriched_at, date_count, method) "
                "VALUES (?, CURRENT_TIMESTAMP, ?, ?) ON CONFLICT(channel_id) DO UPDATE SET "
                "enriched_at=CURRENT_TIMESTAMP, date_count=excluded.date_count, method=excluded.method",
                (str(channel_id), int(date_count or 0), method or None))
            conn.commit()
        finally:
            conn.close()

    def channel_dates_enriched_recently(self, channel_id, within_hours=24) -> bool:
        """True if the channel was date-enriched within the window (don't re-sweep).
        Coverage-aware: a run that produced FEW dates (proxies were down) retries
        soon instead of being locked out for the full window — so the catalog
        actually fills in once a source works."""
        if not channel_id:
            return False
        conn = self._get_connection()
        try:
            row = conn.execute(
                "SELECT enriched_at, date_count, method FROM youtube_channel_enrichment WHERE channel_id=?",
                (str(channel_id),)).fetchone()
            if not row or not row["enriched_at"]:
                return False
            # Legacy rows (enriched before InnerTube, method NULL) → always re-enrich
            # once so they upgrade to the full catalog, then settle under the window.
            if not row["method"]:
                return False
            try:
                when = datetime.strptime(row["enriched_at"], "%Y-%m-%d %H:%M:%S")
            except (ValueError, TypeError):
                return False
            now = datetime.now(timezone.utc).replace(tzinfo=None)   # naive UTC, matches CURRENT_TIMESTAMP
            # Good coverage → skip for the full window; thin result → retry in 15 min.
            window = within_hours if (row["date_count"] or 0) >= 15 else 0.25
            return (now - when) < timedelta(hours=window)
        finally:
            conn.close()

    # ── Remembered channel catalog + metadata (cache-first channel pages) ──────
    def cache_channel_videos(self, channel_id, videos) -> int:
        """Remember a channel's videos (id/title/thumbnail). Upsert — refreshes
        title/thumbnail, never deletes (older pages stay remembered)."""
        cid = str(channel_id or "").strip()
        rows = [(cid, v.get("youtube_id"), v.get("title"), v.get("thumbnail_url"),
                 v.get("duration"), v.get("view_count"))
                for v in (videos or []) if isinstance(v, dict) and v.get("youtube_id")]
        if not cid or not rows:
            return 0
        conn = self._get_connection()
        try:
            conn.executemany(
                "INSERT INTO youtube_channel_videos (channel_id, youtube_id, title, thumbnail_url, "
                "duration, view_count) VALUES (?,?,?,?,?,?) ON CONFLICT(channel_id, youtube_id) DO UPDATE SET "
                "title=COALESCE(excluded.title, title), "
                "thumbnail_url=COALESCE(excluded.thumbnail_url, thumbnail_url), "
                "duration=COALESCE(excluded.duration, duration), "
                "view_count=COALESCE(excluded.view_count, view_count), "
                "cached_at=CURRENT_TIMESTAMP", rows)
            conn.commit()
            return len(rows)
        finally:
            conn.close()

    def get_channel_videos(self, channel_id) -> list:
        """The remembered video list with dates merged from youtube_video_dates,
        newest first (undated last). [] if nothing is cached for the channel."""
        cid = str(channel_id or "").strip()
        if not cid:
            return []
        conn = self._get_connection()
        try:
            rows = conn.execute(
                "SELECT v.youtube_id, v.title, v.thumbnail_url, v.duration, v.view_count, "
                "d.published_at, s.like_count, s.dislike_count "
                "FROM youtube_channel_videos v "
                "LEFT JOIN youtube_video_dates d ON d.youtube_id = v.youtube_id "
                "LEFT JOIN youtube_video_stats s ON s.youtube_id = v.youtube_id "
                "WHERE v.channel_id=? "
                "ORDER BY (d.published_at IS NULL), d.published_at DESC, v.rowid",
                (cid,)).fetchall()
            return [{"youtube_id": r["youtube_id"], "title": r["title"], "thumbnail_url": r["thumbnail_url"],
                     "duration": r["duration"], "view_count": r["view_count"], "published_at": r["published_at"],
                     "like_count": r["like_count"], "dislike_count": r["dislike_count"]}
                    for r in rows]
        finally:
            conn.close()

    def cache_channel_meta(self, channel_id, meta) -> None:
        """Remember a channel's header metadata (avatar/subs/tags/…) for instant re-open."""
        cid = str(channel_id or "").strip()
        if not cid or not isinstance(meta, dict):
            return
        import json
        tags = meta.get("tags")
        conn = self._get_connection()
        try:
            conn.execute(
                "INSERT INTO youtube_channel_meta (channel_id, title, handle, description, "
                "avatar_url, banner_url, subscriber_count, view_count, tags, cached_at) "
                "VALUES (?,?,?,?,?,?,?,?,?,CURRENT_TIMESTAMP) ON CONFLICT(channel_id) DO UPDATE SET "
                "title=COALESCE(excluded.title, title), handle=COALESCE(excluded.handle, handle), "
                "description=COALESCE(excluded.description, description), "
                "avatar_url=COALESCE(excluded.avatar_url, avatar_url), "
                "banner_url=COALESCE(excluded.banner_url, banner_url), "
                "subscriber_count=COALESCE(excluded.subscriber_count, subscriber_count), "
                "view_count=COALESCE(excluded.view_count, view_count), "
                "tags=COALESCE(excluded.tags, tags), cached_at=CURRENT_TIMESTAMP",
                (cid, meta.get("title"), meta.get("handle"), meta.get("description"),
                 meta.get("avatar_url"), meta.get("banner_url"),
                 meta.get("subscriber_count"), meta.get("view_count"),
                 json.dumps(tags) if tags else None))
            conn.commit()
        finally:
            conn.close()

    def get_channel_meta(self, channel_id):
        """The remembered channel metadata dict (tags decoded), or None."""
        cid = str(channel_id or "").strip()
        if not cid:
            return None
        import json
        conn = self._get_connection()
        try:
            r = conn.execute("SELECT * FROM youtube_channel_meta WHERE channel_id=?", (cid,)).fetchone()
            if not r:
                return None
            d = dict(r)
            try:
                d["tags"] = json.loads(d["tags"]) if d.get("tags") else []
            except (ValueError, TypeError):
                d["tags"] = []
            return d
        finally:
            conn.close()

    def youtube_wishlist_counts(self) -> dict:
        """{'channel': n distinct channels, 'video': n videos} in the wishlist."""
        conn = self._get_connection()
        try:
            video = conn.execute("SELECT COUNT(*) c FROM video_wishlist WHERE kind='video'").fetchone()["c"]
            channel = conn.execute(
                "SELECT COUNT(DISTINCT parent_source_id) c FROM video_wishlist WHERE kind='video'").fetchone()["c"]
            return {"channel": channel, "video": video}
        finally:
            conn.close()

    def query_youtube_wishlist(self, *, search=None, sort="added", page=1, limit=60) -> dict:
        """Wished YouTube videos shaped exactly like the TV nebula: channel = show,
        YEAR = season, video = episode. Each channel returns ``seasons`` grouped by
        upload year (newest first), videos as episodes (newest first within a year).
        ``sort`` ∈ added | oldest | title | wanted. {items, pagination} like query_wishlist."""
        try:
            page = max(1, int(page or 1))
            limit = max(1, min(200, int(limit or 60)))
        except (TypeError, ValueError):
            page, limit = 1, 60
        s = (search or "").strip()
        conn = self._get_connection()
        try:
            where, args = ["kind='video'"], []
            if s:
                where.append("(title LIKE ? COLLATE NOCASE OR episode_title LIKE ? COLLATE NOCASE)")
                args += ["%" + s + "%", "%" + s + "%"]
            wsql = " WHERE " + " AND ".join(where)
            total = conn.execute(
                "SELECT COUNT(DISTINCT parent_source_id) c FROM video_wishlist" + wsql, args).fetchone()["c"]
            order = {"title": "title COLLATE NOCASE", "wanted": "video_count DESC, last_added DESC",
                     "oldest": "last_added ASC", "added": "last_added DESC"}.get(sort, "last_added DESC")
            chan_rows = conn.execute(
                "SELECT parent_source_id, MAX(tmdb_id) AS surrogate, MAX(title) AS title, "
                "MAX(poster_url) AS poster_url, COUNT(*) AS video_count, "
                "SUM(CASE WHEN status='downloaded' THEN 1 ELSE 0 END) AS done, "
                "MAX(date_added) AS last_added "
                "FROM video_wishlist" + wsql +
                " GROUP BY parent_source_id ORDER BY " + order + " LIMIT ? OFFSET ?",
                args + [limit, (page - 1) * limit]).fetchall()
            items = []
            for cr in chan_rows:
                vids = conn.execute(
                    "SELECT source_id, episode_title, still_url, episode_overview, air_date, status, "
                    "added_by_profile_id, approved, requested_by_name "
                    "FROM video_wishlist WHERE kind='video' AND parent_source_id=? "
                    "ORDER BY (air_date IS NULL), air_date DESC, id DESC", (cr["parent_source_id"],)).fetchall()
                # group by upload year → "seasons"; newest video in a year = episode 1
                by_year: dict = {}
                for v in vids:
                    ad = v["air_date"]
                    yr = int(ad[:4]) if ad and len(ad) >= 4 and ad[:4].isdigit() else 0
                    by_year.setdefault(yr, []).append(v)
                seasons = []
                for yr in sorted(by_year, reverse=True):   # newest year first
                    eps = []
                    for i, v in enumerate(by_year[yr]):
                        eps.append({"episode_number": i + 1, "title": v["episode_title"],
                                    "still_url": v["still_url"], "overview": v["episode_overview"],
                                    "air_date": v["air_date"], "status": v["status"],
                                    "source_id": v["source_id"],
                                    "added_by_profile_id": v["added_by_profile_id"],
                                    "approved": v["approved"],
                                    "requested_by_name": v["requested_by_name"]})
                    poster = next((e["still_url"] for e in eps if e["still_url"]), cr["poster_url"])
                    seasons.append({"season_number": yr, "year": yr, "poster_url": poster, "episodes": eps})
                items.append({
                    "kind": "channel", "source": "youtube",
                    "tmdb_id": cr["surrogate"], "youtube_id": cr["parent_source_id"],
                    "title": cr["title"], "poster_url": cr["poster_url"],
                    "wanted": cr["video_count"], "done": cr["done"] or 0, "seasons": seasons})
        finally:
            conn.close()
        total_pages = max(1, (total + limit - 1) // limit)
        page = min(page, total_pages)
        return {"items": items, "pagination": {
            "page": page, "total_pages": total_pages, "total_count": total,
            "has_prev": page > 1, "has_next": page < total_pages}}

    def movie_detail(self, movie_id: int) -> dict | None:
        """Full movie detail: the movie + owned/file info. Drives the (isolated)
        video movie-detail page."""
        conn = self._get_connection()
        try:
            m = conn.execute("SELECT * FROM movies WHERE id=?", (movie_id,)).fetchone()
            if not m:
                return None
            genres = self._genres_for(conn, "movie_genres", "movie_id", movie_id)
            credits = self._credits_for(conn, "movie_id", movie_id)
            files = conn.execute(
                "SELECT resolution, quality, video_codec, audio_codec, release_source, size_bytes, "
                "audio_channels, dynamic_range, atmos "
                "FROM media_files WHERE movie_id=? ORDER BY size_bytes DESC",
                (movie_id,)).fetchall()
        finally:
            conn.close()
        return {
            "kind": "movie", "id": m["id"], "title": m["title"], "year": m["year"],
            "sort_title": m["sort_title"],
            "quality_profile_id": m["quality_profile_id"] or 0,
            "locked_fields": sorted(self._parse_locked(m["locked_fields"])),
            "watched": (m["play_count"] or 0) > 0,
            # Continue Watching raw state (v45): last watched + resume position
            "play_count": m["play_count"] or 0,
            "last_viewed_at": m["last_viewed_at"],
            "view_offset_ms": m["view_offset_ms"] or 0,
            # Enriched-but-never-surfaced facts (detail BIC P3)
            "awards": m["awards"],
            "mediastinger": bool(m["mediastinger"] or 0),
            "digital_release_date": m["digital_release_date"],
            "overview": m["overview"], "status": m["status"], "studio": m["studio"],
            "release_date": m["release_date"], "runtime_minutes": m["runtime_minutes"],
            "content_rating": m["content_rating"], "tagline": m["tagline"],
            "rating": m["rating"], "rating_critic": m["rating_critic"], "genres": genres,
            "imdb_rating": m["imdb_rating"], "rt_rating": m["rt_rating"], "metacritic": m["metacritic"],
            "trakt_rating": m["trakt_rating"], "trakt_votes": m["trakt_votes"],
            "wikidata_url": m["wikidata_url"],
            "cast": credits["cast"], "crew": credits["crew"],
            "tmdb_id": m["tmdb_id"], "imdb_id": m["imdb_id"],
            "aka_titles": self.aka_titles_for_tmdb("movie", m["tmdb_id"]),
            # See show_detail — the Library this movie is filed under, so a grab
            # started from the detail page routes to ITS folder and category.
            "root_folder_id": m["root_folder_id"],
            "has_poster": bool(m["poster_url"]), "has_backdrop": bool(m["backdrop_url"]),
            "logo": m["logo_url"],
            "subtitle_langs": _subtitle_langs_list(m["subtitle_langs"]),
            "owned": bool(m["has_file"]), "monitored": bool(m["monitored"]),
            "file": (dict(files[0]) if files else None),       # best version (compat)
            "files": [dict(x) for x in files],                 # all versions/editions
        }

    # ── paged/filtered/sorted library query (server-side, like music) ─────────
    def query_library(self, kind: str, *, search=None, letter=None, sort="title",
                      status="all", genre=None, resolution=None, page=1, limit=75,
                      server_source=None, root_folder_id=None, include_unassigned=False,
                      air_status=None) -> dict:
        """One page of movies/shows with search + A–Z + sort + owned/wanted/
        watched + genre filtering done in SQL. Scoped to ``server_source`` (the
        active video server) so Plex and Jellyfin libraries never commingle —
        mirrors how the music side keeps servers separate.

        Every row carries ``size_bytes`` (summed over its media files) and the
        result carries ``total_size_bytes`` for the whole filtered set — the
        size-on-disk surfaces Radarr/Sonarr users expect. Extra filters:
        ``status='missing'`` (shows: partially owned — the classic 'wanted'
        view) and ``resolution`` (movies: any file at that resolution).
        ``root_folder_id`` scopes to one configured Library (its tab on the
        Library page); ``include_unassigned`` additionally pulls in items
        scanned before that column was populated (used for the PRIMARY
        library's tab only, so nothing vanishes post-upgrade until a rescan).
        ``air_status`` (shows only) buckets TMDB's free-text ``status``
        ('Returning Series', 'Ended', 'Canceled', 'In Production', ...) into
        airing | ended | upcoming — the same buckets the poster badge already
        shows, so the filter never disagrees with what's on screen.
        Returns {items, total_size_bytes, pagination:{...}}."""
        try:
            page = max(1, int(page or 1))
            limit = max(1, min(500, int(limit or 75)))
        except (TypeError, ValueError):
            page, limit = 1, 75
        is_shows = kind == "shows"
        alias = "s" if is_shows else "m"
        tbl = "shows" if is_shows else "movies"

        where, params = [], []
        if server_source:
            where.append(f"{alias}.server_source = ?")
            params.append(server_source)
        if search:
            where.append(f"{alias}.title LIKE ? COLLATE NOCASE")
            params.append("%" + search + "%")
        if letter and letter != "all":
            col = f"COALESCE({alias}.sort_title, {alias}.title)"
            if letter == "#":
                where.append(f"substr(UPPER({col}), 1, 1) NOT BETWEEN 'A' AND 'Z'")
            else:
                where.append(f"{col} LIKE ? COLLATE NOCASE")
                params.append(letter + "%")
        if genre:
            jt, fk = ("show_genres", "show_id") if is_shows else ("movie_genres", "movie_id")
            where.append(f"EXISTS (SELECT 1 FROM {jt} xg JOIN genres g ON g.id=xg.genre_id "
                         f"WHERE xg.{fk}={alias}.id AND g.name = ? COLLATE NOCASE)")
            params.append(genre)
        if root_folder_id:
            if include_unassigned:
                where.append(f"({alias}.root_folder_id = ? OR {alias}.root_folder_id IS NULL)")
            else:
                where.append(f"{alias}.root_folder_id = ?")
            params.append(root_folder_id)
        if not is_shows:
            if status == "owned":
                where.append("m.has_file = 1")
            elif status in ("wanted", "missing"):
                where.append("m.has_file = 0")
            elif status == "watched":
                where.append("COALESCE(m.play_count, 0) > 0")
            elif status == "unwatched":
                # owned-but-untouched — the "what should I put on tonight" filter
                where.append("m.has_file = 1 AND COALESCE(m.play_count, 0) = 0")
            if resolution:
                where.append("EXISTS (SELECT 1 FROM media_files mf WHERE mf.movie_id=m.id "
                             "AND mf.resolution = ? COLLATE NOCASE)")
                params.append(resolution)
        else:
            if status == "owned":
                where.append("EXISTS (SELECT 1 FROM episodes e WHERE e.show_id=s.id AND e.has_file=1)")
            elif status == "wanted":
                where.append("NOT EXISTS (SELECT 1 FROM episodes e WHERE e.show_id=s.id AND e.has_file=1)")
            elif status == "missing":
                # partially owned — you have SOME episodes but gaps remain (Sonarr's
                # bread-and-butter 'which of my shows are incomplete' view)
                where.append("EXISTS (SELECT 1 FROM episodes e WHERE e.show_id=s.id AND e.has_file=1) "
                             "AND EXISTS (SELECT 1 FROM episodes e WHERE e.show_id=s.id AND e.has_file=0)")
            elif status == "watched":
                where.append("COALESCE(s.watched_episodes, 0) > 0")
            elif status == "unwatched":
                where.append("COALESCE(s.watched_episodes, 0) = 0 AND "
                             "EXISTS (SELECT 1 FROM episodes e WHERE e.show_id=s.id AND e.has_file=1)")
            if air_status in ("airing", "ended", "upcoming"):
                buckets = {
                    "airing": ("continu", "return", "air"),
                    "ended": ("end", "cancel"),
                    "upcoming": ("upcoming", "production", "planned"),
                }[air_status]
                where.append("(" + " OR ".join(
                    "LOWER(COALESCE(s.status, '')) LIKE ?" for _ in buckets) + ")")
                params += ["%" + b + "%" for b in buckets]
        where_sql = (" WHERE " + " AND ".join(where)) if where else ""

        title_key = f"COALESCE({alias}.sort_title, {alias}.title) COLLATE NOCASE ASC"
        # display rating: IMDb once OMDb has synced it, TMDB audience score before (both 0-10)
        rating_key = f"COALESCE({alias}.imdb_rating, {alias}.rating)"
        if is_shows:
            size_sub = ("(SELECT COALESCE(SUM(mf.size_bytes), 0) FROM media_files mf "
                        "JOIN episodes e ON mf.episode_id=e.id WHERE e.show_id=s.id)")
        else:
            size_sub = ("(SELECT COALESCE(SUM(mf.size_bytes), 0) FROM media_files mf "
                        "WHERE mf.movie_id=m.id)")
        order_sql = {
            "title": title_key,
            "year": f"{alias}.year DESC, " + title_key,
            "added": f"{alias}.added_at DESC",
            "rating": f"{rating_key} IS NULL, {rating_key} DESC, " + title_key,
            "size": "size_bytes DESC, " + title_key,
        }.get(sort, title_key)

        if is_shows:
            select = ("SELECT s.id, s.title, s.year, s.tmdb_id, s.status, "
                      f"{rating_key} AS rating, s.watched_episodes, "
                      "(s.poster_url IS NOT NULL AND s.poster_url <> '') AS has_poster, "
                      "(SELECT COUNT(*) FROM episodes e WHERE e.show_id=s.id) AS episode_count, "
                      "(SELECT COUNT(*) FROM episodes e WHERE e.show_id=s.id AND e.has_file=1) AS owned_count, "
                      f"{size_sub} AS size_bytes "
                      "FROM shows s")
        else:
            select = ("SELECT m.id, m.title, m.year, m.has_file, m.tmdb_id, "
                      f"{rating_key} AS rating, m.play_count, "
                      "(m.poster_url IS NOT NULL AND m.poster_url <> '') AS has_poster, "
                      "(SELECT mf.resolution FROM media_files mf WHERE mf.movie_id=m.id LIMIT 1) AS resolution, "
                      f"{size_sub} AS size_bytes "
                      "FROM movies m")

        conn = self._get_connection()
        try:
            total = conn.execute(f"SELECT COUNT(*) FROM {tbl} {alias}{where_sql}", params).fetchone()[0]
            total_size = conn.execute(
                f"SELECT COALESCE(SUM({size_sub}), 0) FROM {tbl} {alias}{where_sql}",
                params).fetchone()[0]
            rows = conn.execute(
                f"{select}{where_sql} ORDER BY {order_sql} LIMIT ? OFFSET ?",
                params + [limit, (page - 1) * limit]).fetchall()
            items = []
            for r in rows:
                d = dict(r)
                d["has_poster"] = bool(d.get("has_poster"))
                if not is_shows:
                    d["has_file"] = bool(d.get("has_file"))
                items.append(d)
            total_pages = max(1, (total + limit - 1) // limit)
            return {"items": items, "total_size_bytes": total_size or 0, "pagination": {
                "page": page, "total_pages": total_pages, "total_count": total,
                "has_prev": page > 1, "has_next": page < total_pages}}
        finally:
            conn.close()

    def library_genres(self, kind: str, server_source=None, root_folder_id=None) -> list:
        """Distinct genre names in use for movies/shows — feeds the library page's
        genre filter dropdown (only genres that would actually match something).
        ``root_folder_id`` scopes it to one Library, matching ``query_library``'s
        own filter — without it the Anime tab offers every genre in the whole TV
        collection and picking one can return nothing."""
        is_shows = kind == "shows"
        jt, fk = ("show_genres", "show_id") if is_shows else ("movie_genres", "movie_id")
        tbl, alias = ("shows", "s") if is_shows else ("movies", "m")
        sql = (f"SELECT DISTINCT g.name FROM genres g "
               f"JOIN {jt} xg ON xg.genre_id=g.id "
               f"JOIN {tbl} {alias} ON {alias}.id=xg.{fk}")
        where, params = [], []
        if server_source:
            where.append(f"{alias}.server_source = ?")
            params.append(server_source)
        if root_folder_id:
            where.append(f"{alias}.root_folder_id = ?")
            params.append(root_folder_id)
        if where:
            sql += " WHERE " + " AND ".join(where)
        sql += " ORDER BY g.name COLLATE NOCASE"
        conn = self._get_connection()
        try:
            return [r[0] for r in conn.execute(sql, params)]
        finally:
            conn.close()

    def library_resolutions(self, server_source=None, root_folder_id=None) -> list:
        """Distinct file resolutions in the movie library (best first) — feeds
        the library page's resolution filter dropdown. ``root_folder_id`` scopes
        it to one Library (see ``library_genres``)."""
        from core.video.quality_eval import resolution_rank
        sql = ("SELECT DISTINCT mf.resolution FROM media_files mf "
               "JOIN movies m ON m.id=mf.movie_id "
               "WHERE mf.resolution IS NOT NULL AND mf.resolution <> ''")
        params = []
        if server_source:
            sql += " AND m.server_source = ?"
            params.append(server_source)
        if root_folder_id:
            sql += " AND m.root_folder_id = ?"
            params.append(root_folder_id)
        conn = self._get_connection()
        try:
            vals = [r[0] for r in conn.execute(sql, params)]
            return sorted(vals, key=resolution_rank, reverse=True)
        finally:
            conn.close()

    # ── health ───────────────────────────────────────────────────────────────
    def health_check(self) -> bool:
        """True when the DB opens and passes a quick integrity check."""
        conn = self._get_connection()
        try:
            row = conn.execute("PRAGMA quick_check").fetchone()
            return bool(row) and row[0] == "ok"
        except Exception:
            logger.exception("Video database health check failed")
            return False
        finally:
            conn.close()
