# CLAUDE.md — working notes for this repo

macOS desktop IPTV/M3U player (Python + PySide6 + VLC) with a DVR buffer,
Chromecast support, and keyboard control. Read this before changing anything —
several decisions here are counter-intuitive and were arrived at by measurement.

## Run & test

The virtualenv deliberately lives **outside the repo**:

```bash
./run.sh                                                   # sets up + launches
~/"Library/Application Support/M3UPlayer/venv/bin/pytest"  # tests
```

Do **not** put a `.venv` inside a folder that syncs to iCloud Drive — iCloud
evicts binary files to the cloud and Qt/VLC then fail to load with misleading
errors ("Could not find the Qt platform plugin"). Keep this project on local
disk. `run.sh` bootstraps the venv on first run.

Requires **VLC.app** installed (python-vlc uses its engine). `imageio-ffmpeg`
supplies a bundled ffmpeg — no system install needed.

## Architecture

| Module | Responsibility |
| --- | --- |
| `playlist.py` | Parse M3U → `Channel(name, group, url, stream_id)`. Pure. |
| `store.py` | Config/favorites JSON. Favorites keyed by **group+name**, never URL. |
| `source.py` | Load playlist from file or URL, with cache fallback. |
| `recorder.py` | Runs **ffmpeg** to remux the raw stream into short local HLS segments (the DVR buffer). |
| `proxy.py` | Local HTTP server: serves a seekable DVR manifest + segments from disk. |
| `thumbnails.py` | Extracts one preview frame per segment for timeline hover. |
| `caster.py` | Google Cast (pychromecast) discovery + playback. No Qt. |
| `transport.py` | Pure helpers: `SmoothedClock`, `SeekAccumulator`, labels, clamping. |
| `player.py` | Thin python-vlc wrapper. |
| `main.py` | Qt UI wiring everything together. |

Pure logic (`playlist`, `store`, `source`, `transport`, `recorder.parse_hls_index`,
`proxy.build_dvr_manifest`) is unit-tested. UI/network layers are verified by
driving them against the real stream — see "Verification" below.

## Non-obvious decisions (don't undo these without measuring)

**Provider quirks**
- Requests need a **VLC `User-Agent`**, or the provider resets the connection
  (`error 54`).
- The provider's HLS manifest **302-redirects to an edge host**, and its segment
  URLs are relative with a **session hash** valid only against that host. A
  Chromecast can't follow this, which is why casting goes through our local relay.

**Why we re-segment locally.** The provider ships 10s HLS segments. A player
needs ~2–3 segments of lookahead before playback resumes, so seeking near the
live edge of *their* segments stalls >25s. `recorder.py` therefore runs ffmpeg
(`-c copy`, no transcode) over the **raw** `.ts` stream to produce short
segments, which cuts the live margin to a few seconds. Measured: seek near live
went from >25s to ~3s.

**Why playback starts on the raw stream.** Waiting for the DVR buffer's first
segments cost ~11.5s before any picture. `_play()` now plays `channel.url`
directly (`_play_mode == "direct"`) and starts the recorder in the background;
playback only switches to the relay (`"dvr"`) when the user rewinds/pauses.
`Live ⏭` switches back to direct. Measured: ~11.5s → ~3.4s to first video.
Note this opens **two** provider connections per channel (playback + recorder).

**Why the live edge is smoothed.** The buffer grows in segment-sized jumps while
playback advances continuously, so a naive `buffer_end - position` counts down
then snaps back. `SmoothedClock` projects it with wall-clock time.
**It is display-only — never seek to it**, or you seek past what's downloaded and
stall. Seeks clamp to `_safe_live()` (the real extent).

**Keyboard focus.** Transport controls are `NoFocus` so Space doesn't press a
button and arrows don't drag sliders. Arrows are context-sensitive: panel
navigation while a list has focus (see `eventFilter`), seek/volume while
watching. The fullscreen overlay is a separate top-level window and must stay
`NoFocus` or it swallows shortcuts.

## Verification

Unit tests don't cover playback. For anything touching streaming, verify against
the real playlist (`~/Desktop/tv-scratchpad-files/playlist.m3u`) by driving
`Recorder`/`HlsProxy`/VLC headlessly and measuring — that's how every perf claim
above was established. For Qt, run offscreen:

```python
os.environ["QT_QPA_PLATFORM"] = "offscreen"
QApplication.addLibraryPath(os.path.join(os.path.dirname(PySide6.__file__), "Qt", "plugins"))
```

Casting needs the real device on the LAN; it can't be verified when the box is
off.

## Repo

`https://github.com/Lukasrene/iptv` (public). Keep provider credentials
(user/pass path segments, host) out of code, tests and docs — use
`example.com/USER/PASS` style placeholders.

Design specs live in `docs/superpowers/specs/`.
