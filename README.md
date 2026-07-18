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

## Setup & Run

Just run it — the script sets up its environment automatically on first launch:

```bash
./run.sh
```

The first run creates a Python virtualenv and installs the dependencies; later
runs skip straight to launching.

> **Where the virtualenv lives:** this repo may sit in an iCloud-synced folder,
> and iCloud evicts binary files (like the Qt and VLC libraries) to the cloud,
> which breaks the app with cryptic library-loading errors. So the environment is
> created on **local disk** at `~/Library/Application Support/M3UPlayer/venv`,
> never inside the synced repo. `run.sh` handles this automatically.

To set it up manually instead:

```bash
python3 -m venv ~/"Library/Application Support/M3UPlayer/venv"
~/"Library/Application Support/M3UPlayer/venv/bin/pip" install -r requirements.txt
```

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
- **Cast to TV** — right-click a channel → **Cast** → pick a device. Playback
  **moves to the TV** (the Mac stops playing and the TV streams it directly). Use
  **Cast → Stop casting** to stop, or **Rescan** if your device isn't listed yet.
- **Pause / rewind (DVR)** — from the moment you start a channel, the app records
  a rolling **1-hour** buffer. Use the timeline to scrub back, **−5s / +5s** to
  step (rapid presses stack: −5, −10, −15…), **⏸** to pause, and **Live ⏭** to
  jump forward to the live edge. Hovering the timeline shows a **scene thumbnail**
  plus how far you'd jump (and how far behind live it is). Nothing recorded is
  lost while you jump around. **Works while casting too** — the same controls
  drive the TV, and the previews come from the buffer so they work there as well.
- **In fullscreen** the transport controls stay on screen as a floating overlay
  you can **drag anywhere**; it remembers where you put it.
- **Refresh** (toolbar) re-fetches a URL playlist to pick up new channels and
  refreshed access tokens.

### Casting notes

- Works with Google Cast devices (Chromecast, Google TV, Android TV boxes with
  Chromecast built-in) on the same network as your Mac.
- The app casts the channel's **HLS** stream (`…/live/…/<id>.m3u8`). HD channels
  (H.264) cast to any device; UHD channels (HEVC) need an HEVC-capable device
  such as a 4K Chromecast with Google TV.

### DVR / disk usage

The rolling 1-hour buffer is written to
`~/Library/Application Support/M3UPlayer/dvr/` while a channel is active and
cleared when you switch channels, stop, or quit. A full hour is roughly
2–3 GB for HD and up to ~10 GB for UHD. Because both local playback and casting
run through this buffer, live is ~15–30s behind real-time; **⏮ Live** jumps to
the newest buffered moment.

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
~/"Library/Application Support/M3UPlayer/venv/bin/pytest"
```

## Not in this version (possible later)

- Channel logos / thumbnails
- EPG / TV guide
- Recording / time-shift
- Multiple saved playlists
- Packaging into a double-clickable `.app`
