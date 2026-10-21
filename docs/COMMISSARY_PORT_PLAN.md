# Commissary rebuild on upstream 3.5.7 — porting plan

The old fork (`legacy-2.4.3` tag / `legacy-main` branch) was a snapshot of
upstream 3.1.5 with no shared git history. This branch (`upstream-rebuild`)
starts from upstream tag 3.5.7 and re-applies fork work feature by feature,
so `git merge upstream/main` works from here on.

Reference the old code with `git show legacy-2.4.3:<path>`.

## Ground rules

- **Keep fork changes small and separate.** Prefer new files, flags and hooks
  over editing upstream hunks. Every edited upstream line is a future merge
  conflict.
- **Don't re-port what upstream already does.** Adopt upstream's version and
  drop ours (list below).
- **Each port brings its tests along.** Static-JS harnesses need the CRLF
  normalisation (see memory notes).
- **No rebranding.** The app keeps upstream's SoulSync name; only the repo,
  image and update check point at the fork.

## Phase 0 — foundation (done)

Fork code lives in `core/commissary/` and `tests/commissary/`; upstream files
only carry small hooks marked `Commissary:`.

| Item | Where |
|---|---|
| Publish workflow | `.github/workflows/commissary-publish.yml`: manual, guarded to `ThyMrMan/commissary`, ghcr.io/thymrman/commissary, tag read from `_SOULSYNC_BASE_VERSION` when left blank. Upstream workflows untouched (guarded to Nezreka, they skip). |
| Version + update check | `_SOULSYNC_BASE_VERSION = "3.5.7-c.1"`; `core/commissary/update_check.py` compares the `-c.N` counter; `_GITHUB_REPO` from `core/commissary/brand.py`. |
| Chat + donation | `commissary.chat_enabled` / `commissary.support_button` (default off): chat blueprint and its two slskd loops not started; nav/page/support hidden by CSS. |
| Download paths | `core/commissary/paths.split_paths`: torrent/usenet completed-download settings accept a list (legacy) or `;`-separated string; every folder is a resolver root. |
| DB compatibility | Verified on copies of the live DBs (see Dev data). |

## Phase 1 — small, high-value backend fixes (done)

Security (core/commissary/): Plex device identity (`plex_identity.py`), CSRF
Origin/Referer guard (`csrf.py`, API-key callers exempt), 30-day sliding
sessions (`sessions.py`), video Plex/Jellyfin credentials encrypted in video.db
(`video_secrets.py`; also never exported), boot-time request hold while
signed out (`webui/static/commissary-auth-hold.js`, open endpoints injected from
upstream's gate lists).

Imports: re-identify hint copies get their own candidates (fixes upstream's
album reassign of 2+ tracks), re-identify retry, Discogs/MusicBrainz duration
x1000 + ISRC/MBID in Import-page tags, failed manual import no longer reported
as success, "Album NN Title" filenames. Auto-import threshold kept at 0.9
(one shared constant): upstream's settings UI has a 70% review band floor.

Downloads: usenet category honoured, Deluge/aria2 adopt held torrents, batch
worker counter clamp + retry/slot release, tracker name/link in the picker
(`safe_info_url` keeps indexer download URLs out of the browser). Follow-up:
"dispatched but still pending" counts as a busy slot (2.3.7 rule). Not done
on purpose: torrent adapters' "soulsync" default category (nothing calls them
without one).

Video: 100%-but-still-moving stability gate, Wishlist Audit fixes, importer
naming from the library's identity, configurable manual-search caps, import
failure logging. Retries already stay on their own transport upstream.

Security: legacy 2.4.2's log API-key redaction — now done, see "Security" under Phase 2.

Rest of 1.9.8, done in Phase 3: album release picker (item 5c); the picker open by
default - a track clicked on the Search page opens the every-source Interactive Search
on top of upstream's one-track download window, closing it leaves Begin Analysis as
the automatic path, a profile that requests keeps upstream's flow
(webui/src/commissary/search-picker.ts, one hook in -search.actions.ts openSearchTrack);
a Sources button in a failed / not found / cancelled row's Actions cell opens upstream's
own search modal for that task, the one its status cell already opened unannounced
(commissary-track-inspector.js, one hook in downloads.js; quarantined rows keep Approve).

## Phase 2 — video features (dependency order)

1. Multi-library registry (L) — **done** (core/commissary/video_libraries.py): scan every library's
   section and stamp titles; library folders as roots (health, recycle, resolver, temp sweep);
   grabs filed into the title's library with its client category; library default quality
   profile + series type; Prowlarr searches limited to the library's trackers (unassigned titles
   unrestricted); Settings editor + assign endpoints. Still to do: wishlist/watchlist library
   pickers in the UI (endpoints exist), Library page tabs (item 12).
2. Fansub/anime/arc release parsing (M) — **done** (core/commissary/release_titles.py).
3. Manual search knows the title: aliases, AKAs, episode hints, absolute numbers (M) — **done**
   (core/commissary/search_identity.py + commissary-search-identity.js).
4. tmdb-keyed title overrides before owning the show (S–M), mapped onto upstream `manual_aliases`/`pack_preference`/`series_type` — **done**
   (core/commissary/title_overrides.py, the legacy `video_title_overrides` table kept as-is; Manage on an
   unowned TMDB page via commissary-title-setup.js).
5. Season-pack "only" mode + RSS packs (M) — **done** (core/commissary/season_packs.py). Per-show only; upstream's
   global switch is the automation's `season_pack_first` (no UI), which RSS doesn't read (packs on, as its default).
   Also: an episode whose season pack is in flight is no longer grabbed alone (drain and RSS).
6. Per-show TMDB/TVDB numbering provider + episode repair (M–L); skip 3.5.7 scene mapping when `episode_source='tvdb'` — **done**
   (core/commissary/episode_numbering.py, api/video/commissary_episodes.py, commissary-episode-numbering.js in Manage).
   Only the owning provider creates episodes (refresh_show_art, the worker's match cascade and episode-sync queue);
   TVDB pages fully. Not ported: the legacy duplicate-episode tool (1.8.2/2.0.3) and tools/diagnose_show.py.
7. Import lock (M) — **done** (core/commissary/import_lock.py, commissary-import-lock.js: Manage toggle + season-bar
   button). Gate in the monitor's organize() after planning; the drain's default fetch and RSS skip locked targets.
   Legacy 2.0.2 part B (rename existing files before an import) belongs with item 8.
   Found on the way: upstream fetch-dedupe.js reuses an identical /api GET for 2.5s, so every fork re-read after a
   write now uses a unique `?t=` URL.
8. Sonarr/Radarr `{Token}` naming + rename panel (L) — **done**. Engine core/commissary/naming_tokens.py, vocabulary
   + import/rename/conformance suppliers core/commissary/naming.py; settings chips/TRaSH preset/server preview
   (commissary-naming-settings.js); per-title Rename Files (commissary-rename-panel.js, mass_rename scope +
   one-off template); rename-before-import (fork key `commissary_rename_before_import`, since upstream's
   organization.normalize drops unknown keys) - skipped for a locked title, once per pack.
9. Manual import any file/folder on upstream `season_pack.py` claim model (L) — **done**
   (core/commissary/manual_import.py, api/video/commissary_import.py, commissary-import.js). Browse + filter,
   add file/folder, folder Place with "Import as season" renumber (span kept via importer ctx
   `renumbered_from`), fansub absolute folders, background placement + status. Also fixed upstream's
   unsized Import search icon (fork CSS).
10. RAR-only refusal + `list_files` on torrent adapters (M) — **done** (core/commissary/packed_release.py; the
    adapters are asked through their own RPC helpers, no adapter file touched). Upstream already names the
    client for a vanished torrent, and duplicate-add adoption was ported in Phase 1.
11. Retries on the row's own client (M); TMDB search paging/certainty ladder (M). — **done** (core/commissary/client_retry.py, tmdb_search.py). Differs from
    the old fork: upstream already refuses to retry a client download through Soulseek, so instead a failed
    torrent/usenet release is remembered for a week (every search marks it rejected) and the title is
    re-searched at once as the background drain would, at most 3 times a day. TMDB: 3 pages + trailing-year
    fallback (tmdb_search.py); match certainty ladder - /find by the row's TVDB/IMDb id, then a title search
    that must carry the title, never results[0] on trust (tmdb_match.py).
12. Per-Library UI surfaces (L, rebuild; history is React now) — **Library page + Wishlist done**
    (commissary-library-tabs.js; `?library=` on /api/video/library and /api/video/wishlist; member-readable
    /api/video/commissary/library-tabs with counts, no paths). Not done: Download History (a compiled React
    bundle - a fork change means rebuilding/committing static/dist, which conflicts on every upstream UI
    update), Library dashboard tiles, per-Library enrichment priority.

Security: log API-key redaction (legacy 2.4.2) — **done** (core/commissary/log_redaction.py, installed when
utils/logging_config is imported; wraps logging.Formatter.format so every formatter redacts, side logs and
AcoustID's `client=` included).

## Phase 3 — music features

- Language versions kept apart — extend upstream `recording_version_markers` (M) — **done**
  (core/commissary/language_versions.py). Upstream's markers gain the spellings its tokens miss
  ("-Japanese ver.-", "(JP ver.)", "英語版"); the engine's similarity is capped, its version detector
  labels a language version (staging and AcoustID verification compare those labels), the Soulseek
  scorer rejects another language (whole path + requested album), and album import's title scoring
  and staging skip a file in another language (title, album tag or filename).
- Foreign-language position+duration pairing (M) — **done** (core/commissary/position_pairing.py). Album
  import pairs what titles left over by place in the album (per disc, straight through, or a "CD2" folder)
  where lengths agree within 3 s, once two places confirm the numbering (confidence 0.7, below the 0.8 that
  carries an album alone). Staging claims a batch track's file the same way, reading a straight-through
  offset off the staged files, and keeps the task's own disc/track number. The staging scan now carries
  each file's length (upstream's reader had it, the list dropped it).
- Edition preference — **dropped** (2026-10-20): the legacy `wishlist.prefer_deluxe_editions` was off on the
  live instance, and upstream's `watchlist.edition_preference` ("one_complete") covers watchlist + discography.
- Every source searchable + "search all sources" + album release picker (M–L; backend + vanilla downloads.js, React search chips).
  - 5a **done** (core/commissary/search_sources.py): the candidates window's manual search and the Search page's
    Files picker offer every configured source, the download order first ("Your download order" / "Other
    sources"), in any mode. Upstream's Candidate Inspector already searched every source. Note: upstream's Files
    tab still opens Settings while Soulseek itself is unconfigured.
  - 5b **done** (core/commissary/track_inspector.py, commissary-track-inspector.js, webui/src/commissary/
    track-inspector.ts): every row of the Download Missing Tracks window (every album, every Search page track)
    gets a compass button beside upstream's play / add-to-playlist ones. It opens upstream's Candidate Inspector
    for that track (`POST /api/commissary/inspect`); a pick starts that exact file as a pinned one-task batch
    (`/api/commissary/inspect/download`), like upstream's wishlist pick. Hooks: one line in downloads.js
    `renderModalTrackPlaylistButton`, the inspector host's mount, a route registration in web_server.py.
    At ~1024px wide the three row icons leave little room for the track name.
  - 5c **done** (core/commissary/release_picker.py, commissary-release-picker.js): a "Choose Release" button in
    the album Download Missing Tracks window lists whole-album releases from torrent, usenet and Soulseek
    (`POST /api/commissary/album-releases`, NDJSON per source; torrent/usenet run the album download's own
    "artist album" search and mark upstream's auto-pick). A pick starts the album with
    `commissary_pinned_release` on the batch: the master tries only that source, the torrent/usenet plugins take
    that candidate (by candidate-store token, never a URL) and fail visibly if it's gone, Soulseek gets the
    folder and skips its pre-flight. "Let SoulSync choose" is Begin Analysis.
  - React note: `webui/static/dist` is NOT committed - the Dockerfile builds it from `webui/src`, so a fork React
    change is an ordinary source edit (fork components in `webui/src/commissary/`).
- Prefer explicit for structured sources (M) — **done** (core/commissary/explicit_preference.py). Deezer, HiFi,
  Qobuz and Tidal candidates carry their explicit flag in `_source_metadata`; validation's sort puts the explicit
  cut first when the wanted track is explicit (ranking only, never the 0.60 gate; torrent/usenet/YouTube/Amazon
  fall back to "(Clean)"/"(Explicit)" in the name). Also fixed what left the legacy version inert: the worker
  rebuilt the wanted track without its flag and the wishlist saved tracks without it (rows saved before this
  have none). Upstream's own Soulseek filename preference (#923) is unchanged.
- Multiple completed-download paths + depth search (M) — **done**. Several folders per setting was Phase 0
  (core/commissary/paths.py). The depth search is core/commissary/download_depth.py, hooked into upstream's
  `resolve_reported_save_path` after its own `<root>/<release>` and `<root>/<parent>/<release>` checks: a
  release under category folders (`<root>/complete/Music/<release>`) resolves, breadth-first across every root
  (shallowest wins, newest within a level), `download_source.import_search_depth` levels (default 3, 1..6, no
  UI - as in the old fork), 400 folders per root at most; and a client reporting only its save folder finds
  the category folder holding the release. Shared by music, video and audiobooks.
- Qobuz ISRC into upstream `isrc_match` (M) — **done** (core/commissary/qobuz_isrc.py, one hook in
  `core/discovery/direct_match.direct_source_match`): a mirrored Qobuz track's ISRC comes from Qobuz's own
  `track/get` (cached per track, the running client only) and goes to upstream's `isrc_match` - Spotify or
  Deezer, artist-checked - before the cache or any title search. Not ported: the old fork's "Deezer by ISRC
  whatever the discovery source" for iTunes users (it stored a Deezer match under another provider and needed
  its own album completion and drift exemptions); with iTunes as the source the title search runs as before.
- Import from any download folder — new route + React import inbox UI (M) — **done**
  (core/commissary/import_folders.py, webui/src/commissary/import-folder*.ts(x)). "Scan another folder" in the
  Import page's status strip browses the Import folder and every download folder (Soulseek download/transfer,
  torrent/usenet completed; not the music library) and scans the chosen one: a ky `beforeRequest` hook on the
  shared api client adds `scan_path` to every /api/import/ request except uploads, and the Import cache is
  reset on a change. Server: one hook in api/import_routes `_build_import_route_runtime` (admin-only, the folder
  must resolve inside a root - realpath + commonpath, symlinks and lookalike siblings refused) and one in
  core/imports/routes `_get_allowed_import_roots` (the download folders accepted by upstream's move check).
  The choice lasts until a page reload.
- Purchased tracking — **dropped** (2026-10-20, to keep the fork simpler to maintain). The legacy
  `tracks.purchased_at` / `to_be_purchased` columns stay in the live DB (640 tracks marked, July 2026),
  unused and harmless; nothing reads or writes them.
- Discovery: Fix dialog searching every source, suggestions/unavailable, match-quality chips (React, M–L).
  The Sync page and its discovery/Fix modals are React now (webui/src/routes/sync); the Discovery / Wing It
  Pools are still vanilla (stats-automations.js). Done in batches:
  - 10a **done** (core/commissary/discovery_fixes.py, commissary-pool-fix.js): iTunes searches wait once
    (search_tracks/search_albums lost their outer @rate_limited); a YouTube / YouTube Music / mirrored Fix is
    cached under the playlist track's own name + artist (was a `youtube_track` no result has); the mirrored
    write-back reads deezer_track/qobuz_track/beatport_track ids and maps index 0.. onto position 1..; a
    Wing It guess / unmatched / "Not available" track never reads as "Provider changed", and a mirrored guess
    reopens as a Wing It row; ListenBrainz unmatch finds its '<profile>:<mbid>' state; a Pool Rematch replaces
    the entry under its own key and provider; Pool Fix Match searches every source (active first).
    Deferred to 10c: completing a pick from its own source (fix_pick) - it matters once picks come from
    any source.
  - 10b **done**. Counts: a Wing It guess is a match on the Tidal/Deezer/Qobuz/Spotify-link/iTunes-link/Beatport
    tabs (their workers count it) and not on YouTube/YouTube Music/mirrored/ListenBrainz - Fix no longer counts a
    counted guess twice, unmatch no longer lowers the count for an uncounted one. "Not available"
    (core/commissary/discovery_unavailable.py, POST /api/commissary/discovery/unavailable,
    webui/src/commissary/discovery-unavailable.tsx): a 🚫 beside Fix in the discovery modal, "↺ Available
    again" on a marked row; clears the match and guess, saved in a mirrored track's extra_data; the YouTube
    worker (YouTube, YouTube Music, mirrored), the Pipeline's should_rediscover and reopening a mirrored
    playlist respect it; a Fix clears it. On the other tabs the mark lasts until that playlist is discovered
    again (their workers are separate copies upstream - not hooked).
  - 10c **done** (webui/src/commissary/fix-search.tsx, core/commissary/fix_pick.py): the Sync page's Fix dialog
    asks Spotify, Deezer, iTunes and MusicBrainz in parallel through upstream's own search_tracks endpoints
    (no new search backend), merges as each answers, ranks by title closeness (live/remix/cover/karaoke last,
    then the active source), source chips with counts, artwork + source on each card. A Deezer or iTunes pick
    is completed from its own source (POST /api/commissary/fix-pick/complete: track/disc number, album id,
    release date, track count - the id only when the album is complete) before upstream's update_match saves
    it, so none of the eight Fix routes changed. Not ported: the length delta (a row carries no source
    duration except ListenBrainz).
  - 10d suggestions and 10e Review mode / match-quality chips - **dropped** (2026-10-21, item 10 stops at 10c
    to keep the fork simpler to maintain).

## Decisions (2026-10-20)

- Versioning follows upstream: `<upstream version>-c.<n>`, e.g. `3.5.7-c.1`.
- Per-user dashboard layout + per-widget policy: deferred, revisit later.
- Shared seeding goals / free-disk floor: dropped (upstream per-indexer seed rules).
- Multiple music libraries: dropped (upstream per-profile own libraries).

## Dev data

`data/` (git-ignored) holds a copy of the live `music_library.db`,
`video_library.db`, `config.json` and `.encryption_key`. Upstream 3.5.7
migrations were run against copies of these DBs: both boot, no table or row
loss, integrity ok. Fork-only tables (`music_root_folders`,
`torrent_seed_grabs`, extra video columns) are left behind unused.

## Dropped — upstream already covers it

multiple Music Libraries (`music_root_folders`, `core/imports/destinations.py`, `destination_probe` for music) — use upstream per-profile own libraries; legacy DB keeps the unused table/`wishlist_tracks.root_folder_id`, and the fork always mirrored the default library onto `soulseek.transfer_path`, so files keep landing in the same place · source_chain (use upstream hybrid order; legacy configs already carry valid `mode`/`hybrid_order`) · Last.fm import · daily mixes/stations/playable · fork Plex account login (upstream Sign in with Plex) · member-approval columns (upstream Requests) · account switcher fix · sidebar policy (upstream `allowed_pages`) · album Re-identify (upstream reassign, once the hint fix lands) · infohash.py · timestamps.py · case folding · atomic landing, season-pack import, stall clock, per-title profile · the hand-ported upstream fixes (verified present) · 2.1.0 visual unification.
