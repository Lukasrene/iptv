# CLAUDE.md — working notes for this repo

macOS desktop IPTV/M3U player (Python + PySide6) with a DVR buffer,
Chromecast support, and keyboard control. Read this before changing anything —
several decisions here are counter-intuitive and were arrived at by measurement.

## Run & test

The virtualenv deliberately lives **outside the repo**:

```bash
./run.sh                                                   # sets up + launches
~/"Library/Application Support/M3UPlayer/venv/bin/pytest"  # tests
```

Do **not** put a `.venv` inside a folder that syncs to iCloud Drive — iCloud
evicts binary files to the cloud and Qt then fails to load with misleading
errors ("Could not find the Qt platform plugin"). Keep this project on local
disk. `run.sh` bootstraps the venv on first run.

**No external installs.** The media engine is Qt Multimedia's FFmpeg backend,
which ships inside the PySide6 wheel; `imageio-ffmpeg` supplies the ffmpeg binary
the recorder shells out to. `./build_app.sh` packages the lot into a standalone
`M3U Player.app` (see `packaging/M3UPlayer.spec`).

VLC was removed — do not reintroduce `python-vlc`. Qt's backend measured faster
on both numbers that matter (first frame 3.4s → ~2s, DVR seek ~3s → 0.23s).

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
| `player.py` | Thin `QMediaPlayer` wrapper. Forces the FFmpeg backend. |
| `main.py` | Qt UI wiring everything together. |

Pure logic (`playlist`, `store`, `source`, `transport`, `recorder.parse_hls_index`,
`proxy.build_dvr_manifest`) is unit-tested. UI/network layers are verified by
driving them against the real stream — see "Verification" below.

## Non-obvious decisions (don't undo these without measuring)

**Provider quirks**
- Requests need a **VLC `User-Agent`**, or the provider resets the connection
  (`error 54`). `recorder._UA` is therefore *not* a leftover VLC dependency —
  it is a string the provider demands. Do not "clean it up".
- The account allows **`max_connections: 1`** (see the panel API). Two streams
  on the account fight each other: the provider kills one every ~30s ("Stream
  ends prematurely" in ffmpeg's stderr). This has two consequences. (1) Any
  headless test that opens the provider **while the app is playing steals the
  user's stream** — check `pgrep -fl "M3U Player"` before running one. (2) The
  app's own raw-bootstrap + recorder pair briefly opens two connections per
  channel start; enforcement racing the handoff is a suspected cause of early
  recorder deaths.
- The provider's HLS manifest **302-redirects to an edge host**, and its segment
  URLs are relative with a **session hash** valid only against that host. A
  Chromecast can't follow this, which is why casting goes through our local relay.

**Why we re-segment locally.** The provider ships 10s HLS segments. A player
needs ~2–3 segments of lookahead before playback resumes, so seeking near the
live edge of *their* segments stalls >25s. `recorder.py` therefore runs ffmpeg
(`-c copy`, no transcode) over the **raw** `.ts` stream to produce short
segments, which cuts the live margin to a few seconds. Measured: seek near live
went from >25s to ~3s.

**Why local playback is loopback-only.** `proxy.manifest_url()` (LAN address)
is for the **Chromecast**, which has to reach this machine over the network.
`proxy.dvr_url()` is loopback, for local playback. Do not merge them: routing
local playback over the LAN address makes it hostage to the network, and a VPN
coming up moves `lan_ip()` to an address that is not locally reachable, which
stalls playback. Provider traffic still follows the OS routing table, so it goes
through a VPN as normal.

**Why there are two manifests.** Qt's FFmpeg demuxer reports `duration=0` and
refuses to seek an open-ended live playlist (an `EVENT` type claims to be
seekable but still reports no duration). Only a closed VOD playlist gives a real
duration and working seeks. So `/dvr.m3u8` is a closed VOD **snapshot** for local
seeking, while `/live.m3u8` stays open-ended for the Chromecast. A snapshot is
frozen when built, so playback behind live re-arms against a fresher one as it
nears the end (`_rearm_dvr`).

**Why DVR positions are absolute.** ffmpeg's rolling window deletes segments off
the front. Measuring position as an offset into the *current* buffer slid the
whole timeline under the viewer whenever that happened, so positions are anchored
on `media_seq` via `Recorder.evicted_seconds()`.

**Why the raw stream is only a bootstrap.** `_play()` starts on `channel.url` for
a fast first frame (~2s vs several seconds waiting for segments), but hands off
to the local relay once the buffer can carry it (`_maybe_handoff_to_dvr`). The
raw connection was measured dying after ~30s while the recorder read the same
source indefinitely — so it is not a good bet for sustained playback, and the
relay makes rewinding instant anyway. `Live ⏭` seeks to the buffer's live edge
rather than reopening the raw stream.

**Why the recorder self-heals.** The upstream connection drops for reasons
outside the app (VPN reconnect, provider reset — measured 19 drops in one
68-minute evening session). `restart_if_dead()` relaunches ffmpeg;
`append_list` means the buffer and its media sequence survive. ffmpeg's
stderr is captured (`last_error()`) — it used to go to `/dev/null`, which made a
dead recorder indistinguishable from a slow one.

**Why a relaunch passes `-output_ts_offset`.** A fresh ffmpeg rebases its
output PTS to ~0, and appending that to the existing playlist puts a backward
DTS jump mid-stream. Qt's demuxer does not ride that out: it abandons the rest
of the buffer and fires EndOfMedia — playback leaps to the live edge, starves,
and visibly breaks up/reloads. An `#EXT-X-DISCONTINUITY` tag does *not* help
(measured: same abort with the tag present). Relaunching with
`-output_ts_offset <buffer end>` keeps the timeline monotonic, and playback
rides straight through a restart. Qt also survives a 30s upstream stall on the
open live playlist unaided (position holds, then resumes), so no extra
recovery logic is needed on the player side.

**Why live playback snaps forward after a reconnect.** On reconnect the
provider replays the last ~10-20s it already sent. The relaunch necessarily
appends that with continuous timestamps (see above), so a viewer following
live seamlessly *re-watches* it — and drifts that much further behind true
live on every drop. `ReplaySkip` (transport.py) notes the buffer end at the
restart; once the buffer has regrown `LIVE_START_OFFSET` past it, `main`
re-arms `/live.m3u8`, whose start offset then lands beyond the replayed
stretch — one small forward skip instead of a 20s rerun, and no latency creep.

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
`Recorder`/`HlsProxy`/`Player` headlessly and measuring — that's how every perf claim
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
