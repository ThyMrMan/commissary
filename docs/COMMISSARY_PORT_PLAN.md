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

Deferred to Phase 3: rest of 1.9.8 (picker open by default, Sources button on
failed rows, album release picker).

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
  - React note: `webui/static/dist` is NOT committed - the Dockerfile builds it from `webui/src`, so a fork React
    change is an ordinary source edit (fork components in `webui/src/commissary/`).
- Prefer explicit for structured sources (M) — **done** (core/commissary/explicit_preference.py). Deezer, HiFi,
  Qobuz and Tidal candidates carry their explicit flag in `_source_metadata`; validation's sort puts the explicit
  cut first when the wanted track is explicit (ranking only, never the 0.60 gate; torrent/usenet/YouTube/Amazon
  fall back to "(Clean)"/"(Explicit)" in the name). Also fixed what left the legacy version inert: the worker
  rebuilt the wanted track without its flag and the wishlist saved tracks without it (rows saved before this
  have none). Upstream's own Soulseek filename preference (#923) is unchanged.
- Multiple completed-download paths + depth search (M).
- Qobuz ISRC into upstream `isrc_match` (M).
- Import from any download folder — new route + React import inbox UI (M).
- Purchased tracking — `api/purchased.py` + React `routes/purchased` + artist-detail hooks (L).
- Discovery: Fix dialog searching every source, suggestions/unavailable, match-quality chips (React, M–L).

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
