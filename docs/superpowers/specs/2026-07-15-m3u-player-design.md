# M3U / IPTV Player — Design (v1)

**Date:** 2026-07-15
**Status:** Approved for planning

## Purpose

A basic desktop player for macOS that loads an M3U/IPTV playlist (from a URL or
local file), lets the user browse channels by group, search across all channels,
star favorites, and watch a selected channel in an embedded video pane. The
target playlist is a large IPTV export (~26,000 channels, hundreds of groups,
MPEG-TS live streams over HTTP).

## Key technical facts

- Streams are **MPEG-TS over HTTP** (`.ts` URLs). Web browsers and QuickTime
  cannot reliably play these; a real media engine is required. The app uses the
  user's **installed VLC** engine via `python-vlc` (libVLC).
- **Authentication is embedded in the stream URLs.** Example:
  `http://example.com:80/USER/PASS/894968.ts` — the `USER/PASS`
  path segment is the credential. There is no separate per-stream login.
- Providers **rotate** these credential tokens and change the channel lineup
  over time, so a saved file goes stale. Loading from the provider's **m3u URL**
  (typically a `get.php?username=…&password=…&type=m3u_plus` link) re-fetches a
  fresh playlist with current tokens. This is why URL support is required, not
  just file support.

## Stack

- Python 3
- **PySide6** (Qt) — window and UI
- **python-vlc** — embeds the installed VLC engine for playback
- Standard library `urllib`/`requests` for fetching a playlist URL

## Window layout — three-pane

```
┌──────────────────────────────────────────────────────────┐
│ ● ● ●                                        [Refresh][⚙] │
├────────────┬──────────────────┬──────────────────────────┤
│ GROUPS     │ 🔍 Search…       │                          │
│ ★ Favorites│ Ziggo Sport 1 HD │        ▶ VIDEO           │
│ UCL …      │ DAZN 1 HD        │                          │
│ AFR·Kenya  │ TNT Sports 1     │   Ziggo Sport 1 HD [NL]  │
│ Hindi·India│ Canal+ Foot      │  ⏯  ⏹  🔊────  ⛶        │
└────────────┴──────────────────┴──────────────────────────┘
```

- **Left — Groups:** scrollable list of every `group-title` in the playlist,
  with a pinned **★ Favorites** entry at the top. Clicking a group filters the
  middle column.
- **Middle — Channels:** channels in the selected group, with a **search box**
  on top. Typing searches across *all* channels (ignoring the group selection)
  and shows a flat result list. Must stay responsive at ~26k entries (Qt model +
  filter proxy; no eager rendering of the whole list).
- **Right — Video:** VLC renders the stream into this pane. A "now playing"
  label shows the channel name. Controls: play/pause, stop, volume slider,
  fullscreen toggle (double-click video toggles fullscreen).

## Playlist source: URL or file

- **Settings** allow either pasting an m3u **URL** or picking a local **file**.
  The chosen source is remembered and re-loaded on next launch.
- **Fetch-on-launch + cache (URL sources):** on startup the app fetches the URL
  with a loading indicator, and writes the fetched playlist to a local **cache**.
  If a fetch fails (offline, server error), the app falls back to the cached copy
  so the user can still browse the last-known list. A manual **Refresh** button
  re-fetches on demand.
- **Parser:** stream-parses the file line by line. Reads each `#EXTINF` line
  (channel name after the comma, `group-title="…"` attribute) followed by its URL
  line. Malformed or incomplete entries are skipped without crashing. Must handle
  a ~52k-line / ~26k-channel file quickly.
- **Logos (`tvg-logo`) are out of scope for v1** — fetching thousands of remote
  images would be slow, and the user did not flag them as essential.

## Favorites, groups, and last-watched — persistence

- A small JSON config lives in `~/Library/Application Support/M3UPlayer/`.
  It stores: the playlist source (URL or file path), the playlist cache location,
  the set of favorites, the last-selected group, and the last-watched channel.
- **Stable favorite identity:** because the credential prefix in stream URLs can
  change between fetches, favorites are keyed by **`group-title` + channel name**,
  with the **trailing stream id** (e.g. `894968` parsed from the URL) as a
  tiebreak — **never** by the full URL. After a refresh with rotated tokens, a
  favorite re-binds to the freshly-fetched entry by this stable key.
- On launch the app restores the last group selection and highlights the
  last-watched channel. It does **not** auto-play; the user presses play.

## Playback behavior

- Selecting a channel and pressing play (or double-clicking it) starts the stream
  in the embedded video pane. Switching channels stops the current stream and
  starts the new one in the **same** pane — smooth channel-surfing, no extra
  windows.
- If a stream fails or stalls, a non-blocking message appears
  ("Couldn't play this channel") and the app stays responsive.

## Module structure (isolated, testable units)

- **`playlist.py`** — parse m3u text → `list[Channel(name, group, url, stream_id)]`.
  Pure function, no UI, no network. Owns the parsing quirks.
- **`store.py`** — load/save the JSON config (source, favorites, last group,
  last-watched); resolve a favorite key from a `Channel`. Pure, no UI.
- **`source.py`** — resolve a source to playlist text: read a local file, or
  fetch a URL and update the cache; fall back to cache on failure.
- **`player.py`** — thin wrapper over python-vlc: `play(url)`, `stop`, `pause`,
  `set_volume`, `attach_to_widget`, fullscreen. Isolates all VLC specifics.
- **`main.py`** — the Qt window wiring the three panes to the modules above.
- **`tests/`** — unit tests for `playlist.py` (real-world m3u quirks: missing
  `group-title`, blank names, `#EXTINF:-1`, CRLF, entries with no following URL)
  and `store.py` (favorite key stability across changed URL prefixes).

Each unit has one clear job, a small interface, and can be understood and tested
on its own. `player.py` and `source.py` isolate the two external dependencies
(VLC, network) so the rest of the app stays pure and testable.

## Running it

- `pip install -r requirements.txt` (PySide6, python-vlc), then `python main.py`.
- A short **README** documents setup and the one macOS quirk of pointing
  python-vlc at `VLC.app`'s `libvlc` library.
- Packaging into a double-clickable `.app` (e.g. via py2app/briefcase) is a
  documented **later** step, not part of v1.

## Out of scope for v1 (future candidates)

- Channel logos / thumbnails
- EPG / TV guide
- Recording / time-shift
- Multiple saved playlists / profiles
- Categories beyond the m3u's own `group-title`
- Packaged `.app` distribution
