# Documentation Screenshots

The in-app **Help** page (`webui/static/docs.js`) shows a screenshot wherever it
calls `docsImg('<file>', '<alt text>')`. Every image lives in
**`webui/static/docs/`** and is referenced by bare filename.

Missing images are not an error — `docsImg` renders an `onerror` handler that
hides the element — so the docs page works with none of them. They just make it
much easier to follow.

## Conventions

- **Location:** `webui/static/docs/` (flat, no subfolders)
- **Format:** `.jpg` for screenshots, `.gif` for the short workflow recordings
- **Width:** roughly 1200px; the page scales them down
- **Naming:** the file must match the name in the `docsImg(...)` call exactly

## Still missing

22 of the 74 images the docs reference are not present yet. All of them are on
the video side or are workflow recordings; the music side is fully illustrated.

| File | Docs section | What to capture |
|------|--------------|-----------------|
| `video-automations.jpg` | Video: Automations | Video automations |
| `video-calendar.jpg` | Video: Calendar | Video calendar week grid |
| `video-collections.jpg` | Video: Tools | Collection Manager |
| `video-dashboard.jpg` | Video: Dashboard | Video dashboard overview |
| `video-detail.jpg` | Video: Detail Pages | Video show detail page |
| `video-discover.jpg` | Video: Discover | Video discover hero billboard |
| `video-downloads.jpg` | Video: Downloads | Video downloads queue |
| `video-library.jpg` | Video: Library | Video library poster grid |
| `video-overlay-studio.jpg` | Video: Tools | Overlay Studio editor |
| `video-repair.jpg` | Video: Tools | Library Maintenance jobs and findings |
| `video-requests.jpg` | Video: Requests | Video requests queue |
| `video-search.jpg` | Video: Search & Studios | Video search results |
| `video-settings.jpg` | Video: Settings & Side Access | Video settings |
| `video-side-switch.jpg` | Video: Overview | Switching between the Music and Video sides |
| `video-watchlist.jpg` | Video: Watchlist | Video watchlist tabs |
| `video-wishlist.jpg` | Video: Wishlist | Video wishlist |
| `video-youtube.jpg` | Video: YouTube Channels | YouTube channels tab |
| `wf-auto-downloads.gif` | Quick Start Workflows | Setting up auto-downloads |
| `wf-download-album.gif` | Quick Start Workflows | Downloading an album |
| `wf-import-music.gif` | Quick Start Workflows | Importing music |
| `wf-media-server.gif` | Quick Start Workflows | Connecting media server |
| `wf-sync-playlist.gif` | Quick Start Workflows | Syncing a Spotify playlist |

## Adding one

Drop the file into `webui/static/docs/` under the exact name above. Nothing else
to change — the call site already exists. **Restart the server afterwards:** the
static cache-buster is generated once per run, so a browser reload alone will not
pick up a newly added asset.

## Regenerating this list

This section is derived from `docs.js`, so it goes stale as screenshots land.
To rebuild it, list every `docsImg(...)` filename that is not in
`webui/static/docs/`.
