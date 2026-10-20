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
- **Never mass-edit "SoulSync"** again. Branding goes through a display-name
  constant.

## Phase 0 — foundation (done)

Fork code lives in `core/commissary/` and `tests/commissary/`; upstream files
only carry small hooks marked `Commissary:`.

| Item | Where |
|---|---|
| Publish workflow | `.github/workflows/commissary-publish.yml`: manual, guarded to `ThyMrMan/commissary`, ghcr.io/thymrman/commissary, tag read from `_SOULSYNC_BASE_VERSION` when left blank. Upstream workflows untouched (guarded to Nezreka, they skip). |
| Version + update check | `_SOULSYNC_BASE_VERSION = "3.5.7-c.1"`; `core/commissary/update_check.py` compares the `-c.N` counter; `_GITHUB_REPO` from `core/commissary/brand.py`. |
| Branding | `core/commissary/brand.py` + `web.py` (Jinja `app_name`, `commissary_head`, `window.COMMISSARY`): title, sidebar, sign-in/lock screens, manifest, compose image, `templates/commissary.*`, README notice, MusicBrainz/Discogs contact, bug-report link. Prose stays "SoulSync". Never-rename identifiers pinned in `tests/commissary/test_branding.py`. |
| Chat + donation | `commissary.chat_enabled` / `commissary.support_button` (default off): chat blueprint and its two slskd loops not started; nav/page/support hidden by CSS. |
| Download paths | `core/commissary/paths.split_paths`: torrent/usenet completed-download settings accept a list (legacy) or `;`-separated string; every folder is a resolver root. |
| DB compatibility | Verified on copies of the live DBs (see Dev data). |

## Phase 1 — small, high-value backend fixes (several fix live upstream bugs)

Security / Plex:
- CSRF Origin/Referer check (`core/security/csrf.py`) — fit to upstream CORS, `/api/v1` keys, URL base path, Plex sign-in callback.
- Plex device identity (`core/plex_identity.py`), reusing `plex_signin.client_id`.
- Persistent sessions (`security.session_days` sliding window only).
- Video Plex/Jellyfin credentials out of video.db plaintext into encrypted config.
- `auth-hold.js` boot fetch hold, allowlist rebuilt from 3.5.7 `login_gate`.

Imports (each S):
- Re-identify hint copies grouped by album tag → hints ignored (breaks upstream's own album reassign for 2+ tracks).
- Re-identify retry no-op.
- `_track_duration_ms` ×1000 for Discogs/MusicBrainz + ISRC/MBID/duration in staging tags.
- Failed manual import reported as success.
- "Album 01 Title" filename prefix stripping.
- Auto-import threshold (re-check upstream score first).

Downloads (each S–M):
- Usenet `add_nzb()` category defaults to literal `"soulsync"`.
- Deluge/aria2 adopt an already-held torrent (use upstream hash helper).
- Batch worker clamp / `<= 0` gate / retry-slot release (1.9.16). Do NOT port 2.1.1 `task_is_active` alone.
- Video torrent/usenet retries calling slskd.
- Tracker name/link in candidate picker.

Video (each S):
- Torrent "100% but still moving" stability gate.
- Wishlist audit fixes.
- Importer naming identity (premiere year, real tmdb/tvdb ids, empty `(tmdb-)`).
- Configurable manual-search caps.

## Phase 2 — video features (dependency order)

1. Multi-library registry (L) — extend upstream `library_roots.py`/`libraries.py`; foundation for everything "per-Library".
2. Fansub/anime/arc release parsing (M) — merge into upstream `release_parse`.
3. Manual search knows the title: aliases, AKAs, episode hints, absolute numbers (M).
4. tmdb-keyed title overrides before owning the show (S–M), mapped onto upstream `manual_aliases`/`pack_preference`/`series_type`.
5. Season-pack "only" mode + RSS packs (M).
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
