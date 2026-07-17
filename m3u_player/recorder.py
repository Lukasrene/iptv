from __future__ import annotations

import os
import re
import threading
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urljoin

import requests

_UA = "VLC/3.0.20 LibVLC/3.0.20"
_INF_RE = re.compile(r"#EXTINF:([\d.]+)")


@dataclass(frozen=True)
class Seg:
    """A segment as advertised by the provider's media playlist."""

    media_seq: int
    uri: str
    duration: float


@dataclass(frozen=True)
class RecordedSeg:
    """A segment downloaded to local disk."""

    media_seq: int
    path: str
    duration: float


def parse_media_segments(text: str, effective_url: str) -> list[Seg]:
    """Parse an HLS media playlist into ordered segments with absolute URIs.

    Sequence numbers start from ``#EXT-X-MEDIA-SEQUENCE`` (default 0) and count
    up per segment. Relative URIs resolve against ``effective_url`` (the URL the
    playlist was actually fetched from, after redirects).
    """
    base_seq = 0
    index = 0
    pending_dur: float | None = None
    segs: list[Seg] = []
    for raw in text.splitlines():
        line = raw.strip()
        if not line:
            continue
        if line.startswith("#EXT-X-MEDIA-SEQUENCE:"):
            base_seq = int(line.split(":", 1)[1])
            continue
        if line.startswith("#EXTINF:"):
            m = _INF_RE.match(line)
            pending_dur = float(m.group(1)) if m else 0.0
            continue
        if line.startswith("#"):
            continue
        segs.append(Seg(base_seq + index, urljoin(effective_url, line), pending_dur or 0.0))
        index += 1
        pending_dur = None
    return segs


class DvrBuffer:
    """Ordered on-disk segment buffer with age-based (never position-based)
    eviction. Not thread-safe on its own; the Recorder guards it with a lock."""

    def __init__(self, max_seconds: float = 3600.0):
        self.max_seconds = max_seconds
        self._segments: list[RecordedSeg] = []
        self._high_water: int | None = None

    def has(self, media_seq: int) -> bool:
        return self._high_water is not None and media_seq <= self._high_water

    def add(self, media_seq: int, path: str, duration: float) -> None:
        if self.has(media_seq):
            return  # duplicate or older than something already seen
        self._segments.append(RecordedSeg(media_seq, path, duration))
        self._high_water = media_seq

    def total_duration(self) -> float:
        return sum(s.duration for s in self._segments)

    def evict(self) -> list[RecordedSeg]:
        """Drop oldest segments (deleting their files) until the buffer is within
        ``max_seconds``. Returns the removed segments."""
        removed: list[RecordedSeg] = []
        while len(self._segments) > 1 and self.total_duration() > self.max_seconds:
            seg = self._segments.pop(0)
            removed.append(seg)
            try:
                os.remove(seg.path)
            except OSError:
                pass
        return removed

    def snapshot(self) -> list[RecordedSeg]:
        return list(self._segments)


class Recorder:
    """Continuously downloads a live HLS stream into a rolling DVR buffer on disk.

    Runs a background thread that polls the provider manifest, downloads any new
    segments (following the redirect + session-hashed paths with a VLC
    user-agent), appends them to the buffer, and evicts anything older than the
    buffer window. Recording is independent of playback position, so seeking
    around never drops buffered content.
    """

    def __init__(self, source_url, out_dir, fetcher=None, max_seconds=3600.0, poll_interval=4.0):
        self._source = source_url
        self._out = Path(out_dir)
        self._out.mkdir(parents=True, exist_ok=True)
        self._buffer = DvrBuffer(max_seconds)
        self._session = requests.Session()
        self._session.headers["User-Agent"] = _UA
        self._fetch = fetcher or self._default_fetch
        self._poll = poll_interval
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._lock = threading.Lock()

    def _default_fetch(self):
        r = self._session.get(self._source, allow_redirects=True, timeout=15)
        r.raise_for_status()
        return r.text, r.url

    def start(self) -> None:
        if self._thread is not None:
            return
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def _run(self) -> None:
        while not self._stop.is_set():
            try:
                text, eff = self._fetch()
                for seg in parse_media_segments(text, eff):
                    if self._stop.is_set():
                        break
                    with self._lock:
                        if self._buffer.has(seg.media_seq):
                            continue
                    data = self._session.get(seg.uri, timeout=20).content
                    path = self._out / f"{seg.media_seq}.ts"
                    path.write_bytes(data)
                    with self._lock:
                        self._buffer.add(seg.media_seq, str(path), seg.duration)
                        self._buffer.evict()
            except Exception:
                pass  # transient network/provider hiccups; retry next poll
            self._stop.wait(self._poll)

    def snapshot(self) -> list[RecordedSeg]:
        with self._lock:
            return self._buffer.snapshot()

    def path_for(self, media_seq: int) -> str | None:
        with self._lock:
            for s in self._buffer.snapshot():
                if s.media_seq == media_seq:
                    return s.path
        return None

    def stop(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=5)
            self._thread = None
        for s in self._buffer.snapshot():
            try:
                os.remove(s.path)
            except OSError:
                pass
