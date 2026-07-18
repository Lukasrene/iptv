from __future__ import annotations

import os

# Qt on macOS defaults to the AVFoundation ("darwin") backend, which will not
# demux the provider's raw MPEG-TS stream. The FFmpeg backend ships inside the
# PySide6 wheel (libffmpegmediaplugin) and handles both that and HLS. This must
# be set before any Qt multimedia object is constructed, hence module import
# time rather than main().
os.environ.setdefault("QT_MEDIA_BACKEND", "ffmpeg")

from PySide6.QtCore import QUrl  # noqa: E402
from PySide6.QtMultimedia import QAudioOutput, QMediaPlayer  # noqa: E402


class Player:
    """Thin wrapper over Qt Multimedia's FFmpeg backend.

    Isolates every media-engine call so the rest of the app stays free of it —
    this used to wrap python-vlc and presents the same surface, so `main.py`
    talks to a stable interface either way.

    Video renders into the ``QVideoWidget`` handed to :meth:`bind_surface`.
    Unlike the VLC binding, there is no native window handle involved: Qt owns
    the surface, so it composites correctly with the rest of the UI.
    """

    def __init__(self):
        self._mp = QMediaPlayer()
        self._audio = QAudioOutput()
        self._mp.setAudioOutput(self._audio)
        self._volume = 100

    def bind_surface(self, widget) -> None:
        self._mp.setVideoOutput(widget)

    def play(self, url: str) -> None:
        self._mp.setSource(QUrl(url))
        self._mp.play()

    def stop(self) -> None:
        self._mp.stop()
        self._mp.setSource(QUrl())

    def pause(self) -> None:
        """Toggle pause/resume."""
        if self._mp.playbackState() == QMediaPlayer.PlayingState:
            self._mp.pause()
        else:
            self._mp.play()

    def is_playing(self) -> bool:
        return self._mp.playbackState() == QMediaPlayer.PlayingState

    def is_paused(self) -> bool:
        return self._mp.playbackState() == QMediaPlayer.PausedState

    def set_volume(self, value: int) -> None:
        # QAudioOutput takes 0.0-1.0; the UI slider is 0-100.
        self._volume = int(value)
        self._audio.setVolume(max(0, min(100, int(value))) / 100.0)

    # ---- DVR position / seek (times in seconds) ---- #
    def time_s(self) -> float:
        return max(0.0, self._mp.position() / 1000.0)

    def length_s(self) -> float:
        return max(0.0, self._mp.duration() / 1000.0)

    def seek_s(self, t: float) -> None:
        self._mp.setPosition(int(max(0.0, t) * 1000))

    def is_seekable(self) -> bool:
        """True only once a closed VOD manifest is loaded — the raw live stream
        reports a zero duration and cannot be seeked."""
        return self._mp.isSeekable() and self._mp.duration() > 0

    def at_end_of_buffer(self, margin_s: float) -> bool:
        """Whether playback is within ``margin_s`` of the end of the armed
        manifest. A VOD snapshot stops at the newest segment it listed, so this
        is the cue to re-arm against a fresher one."""
        duration = self._mp.duration()
        if duration <= 0:
            return False
        return (duration - self._mp.position()) / 1000.0 <= margin_s
