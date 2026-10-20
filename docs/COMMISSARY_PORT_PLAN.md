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

Still open (security): legacy 2.4.2 redacted API keys/tokens from log lines in the
log formatter (a keyed URL in a traceback leaked the key into app.log).

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
6. Per-show TMDB/TVDB numbering provider + episode repair (M–L); skip 3.5.7 scene mapping when `episode_source='tvdb'`.
7. Import lock (M).
8. Sonarr/Radarr `{Token}` naming + rename panel (L).
9. Manual import any file/folder on upstream `season_pack.py` claim model (L).
10. RAR-only refusal + `list_files` on torrent adapters (M).
11. Retries on the row's own client (M); TMDB search paging/certainty ladder (M).
12. Per-Library UI surfaces (L, rebuild; history is React now).

## Phase 3 — music features

- Language versions kept apart — extend upstream `recording_version_markers` (M).
- Foreign-language position+duration pairing (M).
- Edition preference — fold download/consistency/bundle gates onto upstream `edition_grouping` (M).
- Every source searchable + "search all sources" + album release picker (M–L; backend + vanilla downloads.js, React search chips).
- Prefer explicit for structured sources (M).
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
