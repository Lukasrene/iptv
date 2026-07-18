from __future__ import annotations

import re
import shutil
import subprocess
import threading
from collections import deque
from dataclasses import dataclass
from pathlib import Path

import imageio_ffmpeg

_UA = "VLC/3.0.20 LibVLC/3.0.20"
_INF_RE = re.compile(r"#EXTINF:([\d.]+)")
_SEQ_RE = re.compile(r"seg_(\d+)\.ts")


@dataclass(frozen=True)
class RecordedSeg:
    """A segment written to local disk."""

    media_seq: int
    path: str
    duration: float


def parse_hls_index(text: str, directory) -> list[RecordedSeg]:
    """Parse the HLS index ffmpeg writes into ordered on-disk segments."""
    directory = Path(directory)
    out: list[RecordedSeg] = []
    pending: float | None = None
    for raw in text.splitlines():
        line = raw.strip()
        if not line:
            continue
        if line.startswith("#EXTINF:"):
            m = _INF_RE.match(line)
            pending = float(m.group(1)) if m else 0.0
            continue
        if line.startswith("#"):
            continue
        m = _SEQ_RE.search(line)
        if m is None:
            pending = None
            continue
        out.append(RecordedSeg(int(m.group(1)), str(directory / line), pending or 0.0))
        pending = None
    return out


class EvictionTracker:
    """Tracks how much recorded material has aged out of the rolling window.

    ffmpeg's ``delete_segments`` drops segments off the front of the buffer. If
    the UI measured positions as offsets into the *current* buffer, the whole
    timeline would slide underneath the viewer every time that happened. Adding
    the evicted duration back gives a position that stays put.

    ``media_seq`` increments monotonically for the life of a recording, so it is
    a stable anchor even across a poll that misses several segments.
    """

    def __init__(self):
        self._known: dict[int, float] = {}
        self._evicted = 0.0

    @property
    def evicted_seconds(self) -> float:
        return self._evicted

    def observe(self, segments: list[RecordedSeg]) -> None:
        if not segments:
            # An unreadable index reads as an empty snapshot. Treating that as
            # "everything was evicted" would jump the timeline; ignore it.
            return
        oldest = segments[0].media_seq
        for seq, duration in list(self._known.items()):
            if seq < oldest:
                self._evicted += duration
                del self._known[seq]
        for s in segments:
            self._known[s.media_seq] = s.duration


class Recorder:
    """Remuxes the provider's continuous MPEG-TS stream into small local HLS
    segments using ffmpeg (``-c copy`` — no transcoding, so it's cheap).

    Segment size is what governs how close to live you can sit: a player needs a
    couple of segments of lookahead before playback resumes, so the provider's
    10s segments force ~30s of delay, while our short segments allow a few
    seconds. ffmpeg also handles the rolling window, deleting segments that fall
    out of the retention list.
    """

    def __init__(self, source_url, out_dir, segment_seconds=2.0, max_seconds=3600.0):
        self._src = source_url
        self._dir = Path(out_dir)
        self._dir.mkdir(parents=True, exist_ok=True)
        self._index = self._dir / "index.m3u8"
        self._segment_seconds = segment_seconds
        self._list_size = max(10, int(max_seconds / max(1.0, segment_seconds)))
        self._proc: subprocess.Popen | None = None
        self._stderr_tail: deque[str] = deque(maxlen=20)
        # snapshot() is polled from the UI thread and from proxy request threads
        self._lock = threading.Lock()
        self._evictions = EvictionTracker()

    def start(self) -> None:
        if self._proc is not None:
            return
        ffmpeg = imageio_ffmpeg.get_ffmpeg_exe()
        cmd = [
            ffmpeg, "-y", "-hide_banner", "-loglevel", "error",
            "-user_agent", _UA,
            "-fflags", "+genpts",
            "-i", str(self._src),
            "-c", "copy",
            "-f", "hls",
            "-hls_time", str(self._segment_seconds),
            "-hls_list_size", str(self._list_size),
            # temp_file: write each segment atomically so we never serve a partial
            "-hls_flags", "delete_segments+append_list+temp_file",
            "-hls_segment_filename", str(self._dir / "seg_%06d.ts"),
            str(self._index),
        ]
        # Keep the tail of stderr. ffmpeg dying used to be silent, which made a
        # dead DVR buffer indistinguishable from a slow one — the UI would just
        # sit at "buffering…" forever with no way to find out why.
        self._proc = subprocess.Popen(
            cmd, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE
        )
        threading.Thread(target=self._drain_stderr, daemon=True).start()

    def _drain_stderr(self) -> None:
        proc = self._proc
        if proc is None or proc.stderr is None:
            return
        for raw in proc.stderr:
            line = raw.decode("utf-8", "replace").strip()
            if line:
                self._stderr_tail.append(line)

    def last_error(self) -> str:
        """Most recent ffmpeg stderr lines — why the buffer stopped filling."""
        return "\n".join(self._stderr_tail)

    def is_alive(self) -> bool:
        return self._proc is not None and self._proc.poll() is None

    def restart_if_dead(self) -> bool:
        """Relaunch ffmpeg if it exited. Returns True if a restart happened.

        The upstream connection is long-lived and can drop for reasons outside
        the app — a VPN reconnecting, the provider resetting, the network
        changing. Left alone the buffer simply stops growing and the UI sits at
        a frozen live edge, so recover instead.

        ``append_list`` means the relaunched ffmpeg continues the existing index
        rather than truncating it, so the buffer and its media sequence survive.
        """
        if self._proc is None or self.is_alive():
            return False
        self._proc = None
        self.start()
        return True

    def snapshot(self) -> list[RecordedSeg]:
        try:
            segs = parse_hls_index(self._index.read_text(), self._dir)
        except OSError:
            return []
        with self._lock:
            self._evictions.observe(segs)
        return segs

    def evicted_seconds(self) -> float:
        """Duration that has aged out of the rolling window — the origin of the
        absolute timeline. Only meaningful after ``snapshot()`` has been polled."""
        with self._lock:
            return self._evictions.evicted_seconds

    def extent(self) -> tuple[float, float]:
        """Absolute ``(start, end)`` of the buffer, in seconds since recording began."""
        segs = self.snapshot()
        start = self.evicted_seconds()
        return start, start + sum(s.duration for s in segs)

    def origin_for(self, segments: list[RecordedSeg]) -> float:
        """Absolute start time of a manifest built from ``segments``.

        Player positions are relative to the first segment in whatever snapshot
        was armed, so converting to the absolute timeline needs this offset.
        """
        if not segments:
            return self.evicted_seconds()
        start = self.evicted_seconds()
        for s in self.snapshot():
            if s.media_seq == segments[0].media_seq:
                return start
            start += s.duration
        return start

    def segment_seconds(self) -> float:
        """Actual segment length being produced (ffmpeg cuts on keyframes, so it
        can exceed the requested value)."""
        snap = self.snapshot()
        return snap[-1].duration if snap else self._segment_seconds

    def path_for(self, media_seq: int) -> str | None:
        for s in self.snapshot():
            if s.media_seq == media_seq:
                return s.path
        return None

    def stop(self) -> None:
        if self._proc is not None:
            try:
                self._proc.terminate()
                self._proc.wait(timeout=5)
            except Exception:
                try:
                    self._proc.kill()
                except Exception:
                    pass
            self._proc = None
        shutil.rmtree(self._dir, ignore_errors=True)
