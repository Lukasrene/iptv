# Cast to TV — Design

**Date:** 2026-07-15
**Status:** Approved for implementation

## Purpose

Let the user cast a channel from the Mac app to a Google Cast device (their
Xiaomi Mi Box 4K). Playback **moves** to the TV: the Mac stops local playback and
the TV pulls the stream itself. The cast target is chosen by right-clicking a
channel → **Cast** → selecting a discovered device.

## Verified facts (from testing)

- The provider exposes HLS at `http://host:port/live/USER/PASS/<id>.m3u8`
  (a plain `.ts`→`.m3u8` swap 404s; the `/live/` prefix is required).
- HD channels are H.264/AAC; UHD channels are HEVC/E-AC3. The Mi Box 4K decodes
  HEVC, so **all** channels cast for this user.
- Chromecast's receiver plays HLS natively with content-type
  `application/x-mpegurl`, stream type LIVE.

## Modules

- **`hls.py`** (pure, tested) — `to_hls_url(ts_url) -> str`. Transforms an Xtream
  `.ts` URL into the `/live/…​.m3u8` HLS form. Falls back to a plain extension
  swap for non-matching URLs, and returns the input unchanged if it isn't `.ts`.
- **`caster.py`** — wraps `pychromecast`. No Qt. Responsibilities:
  - `start_discovery()` — background Cast browser keeping a live device list.
  - `list_devices() -> list[(uuid_str, friendly_name)]`.
  - `cast(uuid_str, url, title)` — connect to the device and play the HLS URL
    (blocking; callers run it off the UI thread).
  - `stop()` — stop TV playback / quit the receiver app.
  - `is_active() -> bool`, `shutdown()` — clean teardown of discovery/zeroconf.
- **`main.py`** — UI wiring (below). Runs `cast()`/`stop()` in a `QThread` so the
  UI never blocks.

## UI flow

- Channel right-click menu gains a **Cast ▸** submenu:
  - one action per discovered device,
  - a disabled "Searching…" item when none found yet,
  - **Rescan**,
  - **Stop casting** (only shown while a cast is active).
- Selecting a device: stop local VLC, show **"📺 Casting '<channel>' to
  <device>"** in the now-playing label, start the cast in a worker thread. On
  success, persist `last_watched`. On failure, a non-blocking status message and
  restore normal state.
- **Stop casting**: stop the TV, reset the now-playing label to "Nothing
  playing".

## Discovery

Background Cast browser starts at app launch and maintains a live device dict
read on demand when the submenu opens. **Rescan** restarts discovery.

## Errors

Device unreachable / connect timeout / cast failure → status-bar message
("Couldn't cast to <device>"), local state restored, no crash. All network work
is off the UI thread.

## Out of scope (v1)

- TV volume / pause control from the Mac (the Mi Box remote handles it; live pause
  has the known live-stream caveat).
- Re-casting a different channel is just right-click → Cast again.

## Testing

- TDD `hls.py` (Xtream match, query string, already-m3u8, non-`.ts`, extra path
  segments → fallback).
- `caster.py` and the menu are device/network dependent: verify the app builds,
  `CastManager` instantiates, and `list_devices()` returns `[]` with no device.
  The real cast to the Mi Box is the user's manual verification (my environment
  is not on their LAN).

## Dependency

`pychromecast` (adds `zeroconf`, `casttube`).
