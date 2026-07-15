# M3U Player

A basic macOS desktop player for M3U / IPTV playlists. Load a playlist from a
provider URL or a local file, browse channels by group, search across the whole
list, favorite channels, and watch the selected stream in an embedded video pane.

Built for large IPTV exports (tested against a ~26,000-channel, 700+ group
playlist of MPEG-TS live streams).

## Requirements

- macOS
- **[VLC](https://www.videolan.org/vlc/) installed** in `/Applications` — the app
  uses VLC's engine to decode the streams. (These `.ts` MPEG-TS streams can't be
  played by a browser or QuickTime.)
- Python 3.9+

## Setup

```bash
cd m3u-player
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
```

## Run

```bash
./run.sh
```

(or `.venv/bin/python -m m3u_player.main`)

## Using it

- On first launch you'll be asked for a playlist. You can either:
  - **Paste your provider's m3u URL** (e.g. `http://provider/get.php?username=…&type=m3u_plus`), or
  - **Choose a local `.m3u` file**.
  The choice is remembered and re-loaded next time.
- **Left pane** — groups (with **★ Favorites** pinned on top). Click one to see
  its channels.
- **Middle pane** — the search box filters across *all* channels; the list shows
  the selected group otherwise. Double-click a channel to play it.
- **Right pane** — the video, with Play/Pause, Stop, a volume slider, and a
  fullscreen button (or double-click the video).
- **Favorites** — right-click a channel to add/remove it.
- **Refresh** (toolbar) re-fetches a URL playlist to pick up new channels and
  refreshed access tokens.

### Why URL support matters

Your stream URLs carry your access token in the path
(`…/USER/PASS/894968.ts`). Providers rotate these tokens periodically, so a
saved file eventually goes stale. Loading from the provider's m3u **URL**
re-fetches a fresh list with current tokens. If a fetch fails (offline, server
down), the app falls back to the last cached copy so you can still browse.

Favorites are stored by **group + channel name** (not the URL), so your starred
channels survive token rotation.

## Where settings live

Favorites, last-watched channel, the chosen source, and the playlist cache are
stored in:

```
~/Library/Application Support/M3UPlayer/
```

## Tests

```bash
.venv/bin/pytest
```

## Not in this version (possible later)

- Channel logos / thumbnails
- EPG / TV guide
- Recording / time-shift
- Multiple saved playlists
- Packaging into a double-clickable `.app`
