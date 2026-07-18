# Standalone app: replacing VLC with Qt Multimedia

**Status:** approved, not yet implemented
**Date:** 2026-07-18

## Goal

Ship a double-clickable `M3U Player.app` that needs nothing preinstalled — no
VLC, no Python, no virtualenv. Feature parity with the current build, verified by
measurement against the real provider stream.

## Why this is tractable

VLC is already confined to `player.py` (66 lines, 12 methods). Just as important,
the DVR timeline runs off `_safe_live()` — derived from the recorder's own
on-disk segment list — not off the player's clock. The player's `length_s()` is
consumed in exactly one place, as a readiness gate in `_update_timeline`. The
media engine is therefore a genuinely swappable part.

## Engine choice

PySide6 6.10.3 ships `libffmpegmediaplugin.dylib` inside the wheel: a full FFmpeg
7.1.3 backend, already a dependency, no external install. Measured against the
real stream, it beats VLC on both numbers that matter:

| | VLC (current) | Qt + FFmpeg |
| --- | --- | --- |
| First frame, raw `.ts` | ~3.4s | **1.41s** |
| Seek within DVR buffer | ~3s | **0.23s** |

macOS Qt defaults to the AVFoundation ("darwin") backend, which will not demux
the provider's raw MPEG-TS. Set `QT_MEDIA_BACKEND=ffmpeg` in `main.py` **before**
`QApplication` is constructed. Do this in code, not in `run.sh` — the `.app`
bundle has no shell wrapper to set it.

## The seekability problem

Qt's FFmpeg demuxer will not seek a sliding-window live playlist. Measured, with
a 12-segment buffer:

| Manifest form | `duration` | `isSeekable` | seek works |
| --- | --- | --- | --- |
| Live sliding window (current) | 0 | false | no |
| `#EXT-X-PLAYLIST-TYPE:EVENT` | 0 | true (lies) | no |
| `VOD` + `#EXT-X-ENDLIST` | 60000ms | true | yes, 0.23s |

Only the VOD form works. But a VOD manifest is a frozen snapshot, and the
Chromecast still needs the live form it consumes today.

**Resolution — the proxy serves two manifests:**

- `/live.m3u8` — the existing sliding window, byte-for-byte unchanged.
  Chromecast keeps using it. **The cast path is not touched by this work.**
- `/dvr.m3u8` — a VOD snapshot with `#EXT-X-ENDLIST`, for local seeking.

Both are built by pure functions in `proxy.py` and unit-tested.

### Seamless re-arm

A VOD snapshot is frozen at build time, so while watching behind live the buffer
outgrows it. In `dvr` mode, when `duration - position < 12s`:

- if still behind live → re-issue `setSource()` with a fresh `/dvr.m3u8` and
  restore the absolute position;
- if caught up to the live edge → fall through to the existing "switch to
  direct" path.

At 0.23s per seek this should be imperceptible. If it turns out not to be,
lengthening the trigger window is the tuning knob.

## Absolute DVR timeline

Today, positions are offsets from the start of the buffer. The recorder keeps a
rolling 1-hour window (`delete_segments`), so when segments drop off the front
the entire timeline shifts underneath the user. This is pre-existing, but the
seek path is being rewritten anyway, so it gets fixed here.

`media_seq` (from `seg_%06d.ts`) increments monotonically for the life of a
recording and never repeats — it is a stable anchor.

- `Recorder` tracks `evicted_seconds`: the summed duration of segments it has
  seen and that have since left the index. It accumulates across snapshots
  rather than being derived from the current index alone.
- Absolute buffer extent becomes
  `[evicted_seconds, evicted_seconds + sum(current durations)]`.
- The UI works in absolute seconds throughout.
- When arming a `/dvr.m3u8`, record its `origin` (the absolute start of its first
  segment). Player position converts as `absolute = origin + player.time_s()`,
  and seeking converts back.

`_safe_live()` keeps its current meaning — start of the newest complete segment —
but returns an absolute value. The `SmoothedClock` display projection is
unchanged and remains **display-only; never a seek target.**

## Module changes

| Module | Change |
| --- | --- |
| `player.py` | Rewritten on `QMediaPlayer` + `QAudioOutput`. `bind_window()` → `bind_surface(QVideoWidget)`. Same method surface otherwise. `toggle_fullscreen()` dropped — it was already a no-op for an embedded surface. |
| `main.py` | `video_frame` becomes a `QVideoWidget`; force the ffmpeg backend; re-arm logic; absolute-position conversions. |
| `proxy.py` | Add `build_vod_manifest` + `/dvr.m3u8`. `build_dvr_manifest` and `/live.m3u8` unchanged. |
| `recorder.py` | Track `evicted_seconds` for the absolute timeline. |
| `requirements.txt` | Drop `python-vlc`. |
| `run.sh` | Drop the VLC env-var comments. Keep it working for development. |

**Not changed:** `recorder.py`'s `_UA = "VLC/3.0.20 LibVLC/3.0.20"` stays. That is
a User-Agent string the provider demands — removing it gets the connection reset
(`error 54`). It is not a VLC dependency.

Also unchanged: `caster.py`, `thumbnails.py`, `playlist.py`, `store.py`,
`source.py`, `transport.py`.

## Packaging

A PyInstaller spec producing `M3U Player.app`, bundling the Python runtime,
PySide6 with the Qt multimedia plugins (the ffmpeg plugin must be explicitly
collected — PyInstaller's hooks do not reliably pick it up), `imageio-ffmpeg`'s
ffmpeg binary, pychromecast and requests.

Config, favorites and the DVR scratch directory continue to live in
`~/Library/Application Support/M3UPlayer/`, so an existing setup carries over.

The bundle is unsigned: it runs without complaint on this machine, but on
another Mac Gatekeeper requires a one-time right-click → Open. Signing and
notarization are explicitly out of scope and would need an Apple Developer
account.

## Testing

Unit tests (pure functions): both manifest builders, `evicted_seconds`
accumulation across snapshots including eviction, and absolute↔relative position
conversion.

Playback verification, per CLAUDE.md, against the real playlist at
`~/Desktop/tv-scratchpad-files/playlist.m3u` — driving the components headlessly
with `QT_QPA_PLATFORM=offscreen` and re-measuring first-frame and seek latency to
confirm no regression against the VLC baselines above.

Then a human pass on the built `.app`: channel switching, rewind, pause, jump to
live, fullscreen, keyboard control.

Casting is verified only if the device is on the LAN. Since `/live.m3u8` is
unchanged, the risk there is low.

## Out of scope

Codesigning and notarization. Windows and Linux bundles. Any UI redesign.
