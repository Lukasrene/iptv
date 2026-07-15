from __future__ import annotations

import sys

import vlc


class Player:
    """Thin wrapper over python-vlc bound to a Qt widget's native window.

    Isolates every VLC-specific call so the rest of the app stays free of the
    media engine. The video renders into whatever widget is passed to
    :meth:`bind_window`.
    """

    def __init__(self):
        self._instance = vlc.Instance("--no-xlib", "--quiet")
        self._mp = self._instance.media_player_new()

    def bind_window(self, widget) -> None:
        wid = int(widget.winId())
        if sys.platform == "darwin":
            self._mp.set_nsobject(wid)
        elif sys.platform.startswith("win"):
            self._mp.set_hwnd(wid)
        else:
            self._mp.set_xwindow(wid)

    def play(self, url: str) -> None:
        media = self._instance.media_new(url)
        self._mp.set_media(media)
        self._mp.play()

    def stop(self) -> None:
        self._mp.stop()

    def pause(self) -> None:
        """Toggle pause/resume."""
        self._mp.pause()

    def is_playing(self) -> bool:
        return bool(self._mp.is_playing())

    def set_volume(self, value: int) -> None:
        self._mp.audio_set_volume(int(value))

    def toggle_fullscreen(self) -> None:
        self._mp.toggle_fullscreen()
