# DVR / Timeshift Design

**Date:** 2026-07-17
**Status:** Approved (goal-directed) — building

## Goal

Pause / rewind live TV. From the moment a channel starts, record a rolling
**1-hour** buffer. The user can rewind anywhere back to the start of viewing (up
to 1h), jump back to live, and step ±10s. Nothing already recorded is lost when
seeking around (eviction is purely age-based, never position-based). A timeline
shows, on hover, how far back a click would land. **All of this must work while
casting to the TV**, not just locally.

## Key idea: one DVR buffer feeds both local and cast

The casting relay already interposes the Mac between the provider and the TV.
We upgrade it: instead of proxying live segments on demand, a **Recorder**
continuously downloads every segment into a local rolling buffer, and the relay
serves a **seekable DVR HLS manifest** built from that buffer. Both the local
VLC player and the Chromecast consume this same relay URL, so seeking works
identically whether watching on the Mac or the TV.

Consequence: local playback now also goes through the relay (previously it played
the raw provider URL). Live is therefore ~1–3 segments (~10–30s) behind
real-time; "jump to live" seeks to the newest buffered segment.

## Modules

- **`recorder.py`**
  - `parse_media_segments(manifest_text, effective_url) -> list[Seg]` — pure;
    parse a provider media playlist into `(media_seq, abs_uri, duration)`.
  - `DvrBuffer` — ordered recorded segments on disk; `add`, `total_duration`,
    `evict(max_seconds)` (age/duration-based only), `snapshot`. Pure logic over a
    temp dir.
  - `Recorder(source_url, out_dir, fetcher)` — background thread: fetch the live
    manifest (follow redirect, VLC UA), download new segments (by seq) to
    `out_dir`, append to `DvrBuffer`, evict beyond 3600s. Thread-safe snapshot.
- **`proxy.py`** (upgrade)
  - Holds a `Recorder`. `/live.m3u8` → `build_dvr_manifest(buffer.snapshot(),
    base)` — a live, seekable playlist (correct `#EXT-X-MEDIA-SEQUENCE`, all
    buffered segments, **no** `#EXT-X-ENDLIST`). `/seg/<n>.ts` → the recorded file
    from disk. `build_dvr_manifest` is pure and tested.
- **`transport.py`** (pure helpers)
  - `hover_label(hover_pos_s, live_edge_s) -> str` → e.g. `"–2:35 behind live"`.
  - `clamp_seek(target_s, buffer_start_s, live_edge_s) -> float`.
- **`main.py`**
  - Start the `Recorder` + relay when a channel is played or cast; both local VLC
    and the cast play `proxy.manifest_url()`.
  - Transport bar: **⏮ live**, **−10s**, **⏯ pause**, **+10s**, and a **timeline**
    (`QSlider`-based) spanning buffer-start→live with a live marker. Hovering shows
    `hover_label`. Controls act on the **active target**: the local VLC player, or
    the Chromecast media controller when casting.
  - Position polling (timer) updates the timeline from VLC (`get_time/length`) or
    the cast (`status.current_time` / seekable range).

## Seek semantics (works local and cast)

| Control | Local (VLC) | Cast (Chromecast) |
|---|---|---|
| −10s / +10s | `set_time(get_time ± 10000)` | `mc.seek(current_time ± 10)` |
| Jump to live | seek to newest buffered second | `mc.seek(live_edge)` |
| Pause / resume | `player.pause()` | `mc.pause()` / `mc.play()` |
| Timeline click | seek to mapped time | `mc.seek(mapped)` |

## Buffer / disk

- Rolling window capped at 3600s; old segments (files) deleted on eviction only.
- Buffer lives in a temp dir under Application Support; cleared on channel change
  and app close. ~2–3 GB (HD) to ~10 GB (UHD) for a full hour.

## Testing

- TDD pure logic: `parse_media_segments`, `build_dvr_manifest`, `DvrBuffer`
  eviction, `hover_label`/`clamp_seek`.
- Integration (real stream, no TV): run the recorder ~30s, confirm the DVR
  manifest grows and lists on-disk segments; fetch a buffered segment via the
  relay; seek the local VLC within the buffer and confirm position jumps.
- Cast seek: verify on the box via telemetry that `mc.seek()` moves
  `current_time` within the DVR window.

## Out of scope

- Persisting the buffer across app restarts.
- Buffer longer than 1 hour.
