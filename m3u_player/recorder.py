from __future__ import annotations

import re
import shutil
import subprocess
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
        self._proc = subprocess.Popen(
            cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
        )

    def is_alive(self) -> bool:
        return self._proc is not None and self._proc.poll() is None

    def snapshot(self) -> list[RecordedSeg]:
        try:
            return parse_hls_index(self._index.read_text(), self._dir)
        except OSError:
            return []

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
