from __future__ import annotations

import os
import subprocess
import threading
from pathlib import Path

from m3u_player.recorder import ffmpeg_exe


class Thumbnailer:
    """Extracts one small preview frame per buffered DVR segment (using the
    bundled ffmpeg) so the timeline can show a scene thumbnail on hover.

    Runs in the background off the recorder's on-disk segments, so previews are
    available whether watching locally or casting. Thumbnails for evicted
    segments are pruned to bound disk use.
    """

    def __init__(self, out_dir, recorder, width: int = 160):
        self._out = Path(out_dir)
        self._out.mkdir(parents=True, exist_ok=True)
        self._recorder = recorder
        self._width = width
        self._ffmpeg = ffmpeg_exe()
        self._done: dict[int, str] = {}  # media_seq -> jpg path
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        if self._thread is not None:
            return
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def _run(self) -> None:
        while not self._stop.is_set():
            snap = self._recorder.snapshot()
            for seg in snap:
                if self._stop.is_set():
                    break
                with self._lock:
                    if seg.media_seq in self._done:
                        continue
                out = self._out / f"{seg.media_seq}.jpg"
                # Grab the first frame. Don't use an input -ss seek: remuxed
                # segments keep the source's original timestamps (they don't
                # start at 0), so seeking lands outside the segment and yields
                # no packets. Each segment starts on a keyframe anyway.
                try:
                    subprocess.run(
                        [self._ffmpeg, "-y", "-i", seg.path, "-an",
                         "-frames:v", "1", "-vf", f"scale={self._width}:-1",
                         "-q:v", "6", str(out)],
                        capture_output=True, timeout=15,
                    )
                except Exception:
                    continue
                if out.exists() and out.stat().st_size > 0:
                    with self._lock:
                        self._done[seg.media_seq] = str(out)
            self._prune(snap)
            self._stop.wait(1.0)

    def _prune(self, snap) -> None:
        live = {s.media_seq for s in snap}
        with self._lock:
            stale = [seq for seq in self._done if seq not in live]
            for seq in stale:
                try:
                    os.remove(self._done[seq])
                except OSError:
                    pass
                del self._done[seq]

    def thumbnail_for_offset(self, offset_s: float) -> str | None:
        """Path of the thumbnail nearest a time offset from the buffer start, or
        None if not generated yet."""
        snap = self._recorder.snapshot()
        if not snap:
            return None
        acc = 0.0
        for seg in snap:
            acc += seg.duration
            if offset_s <= acc:
                with self._lock:
                    return self._done.get(seg.media_seq)
        with self._lock:
            return self._done.get(snap[-1].media_seq)

    def stop(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=5)
            self._thread = None
        with self._lock:
            for path in self._done.values():
                try:
                    os.remove(path)
                except OSError:
                    pass
            self._done.clear()
