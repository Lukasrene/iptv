from __future__ import annotations

from uuid import UUID

import pychromecast
import zeroconf
from pychromecast.discovery import CastBrowser, SimpleCastListener

# Chromecast's receiver plays HLS with this content type; live streams use the
# LIVE stream type so the receiver doesn't try to show a seek bar.
_HLS_CONTENT_TYPE = "application/x-mpegurl"


class CastManager:
    """Isolates all Google Cast (pychromecast) interaction. No Qt here.

    Discovery runs in the background and keeps a live device list. Connecting and
    casting block on the network, so callers must run :meth:`cast` and
    :meth:`stop` off the UI thread.
    """

    def __init__(self):
        self._zconf: zeroconf.Zeroconf | None = None
        self._browser: CastBrowser | None = None
        self._active = None  # active pychromecast.Chromecast

    # ---- discovery ---------------------------------------------------- #
    def start_discovery(self) -> None:
        if self._browser is not None:
            return
        self._zconf = zeroconf.Zeroconf()
        self._browser = CastBrowser(SimpleCastListener(), self._zconf)
        self._browser.start_discovery()

    def rescan(self) -> None:
        """Restart discovery to force a fresh scan."""
        self.stop_discovery()
        self.start_discovery()

    def stop_discovery(self) -> None:
        if self._browser is not None:
            try:
                self._browser.stop_discovery()
            except Exception:
                pass
            self._browser = None
        if self._zconf is not None:
            try:
                self._zconf.close()
            except Exception:
                pass
            self._zconf = None

    def list_devices(self) -> list[tuple[str, str]]:
        """Return [(uuid_str, friendly_name)] for currently discovered devices."""
        if self._browser is None:
            return []
        return [
            (str(uuid), info.friendly_name or "Unknown device")
            for uuid, info in self._browser.devices.items()
        ]

    def device_host(self, uuid_str: str) -> str | None:
        """The device's own IP, so we can work out which of our addresses it can
        reach us on. With a VPN up, our default-route address is the tunnel's and
        is unreachable from the LAN — see ``proxy.lan_ip_for``."""
        if self._browser is None:
            return None
        info = self._browser.devices.get(UUID(uuid_str))
        if info is None:
            return None
        host = getattr(info, "host", None)
        if host:
            return host
        # older pychromecast exposes the address via the mDNS service entry
        services = getattr(info, "services", None) or []
        for svc in services:
            addr = getattr(svc, "address", None)
            if addr:
                return addr
        return None

    # ---- casting ------------------------------------------------------ #
    def cast(self, uuid_str: str, url: str, title: str) -> str:
        """Connect to the device and play the URL. Returns the device name.

        Blocking — run from a worker thread. Raises on failure.
        """
        if self._browser is None:
            raise RuntimeError("Discovery not started")
        info = self._browser.devices[UUID(uuid_str)]
        cast = pychromecast.get_chromecast_from_cast_info(info, self._zconf)
        cast.wait(timeout=10)
        mc = cast.media_controller
        # Tell the receiver the HLS segments are MPEG-2 TS so its player demuxes
        # them correctly (the default receiver otherwise mishandles TS HLS).
        mc.play_media(
            url,
            content_type=_HLS_CONTENT_TYPE,
            title=title,
            stream_type="LIVE",
            media_info={
                "hlsVideoSegmentFormat": "mpeg2_ts",
                "hlsSegmentFormat": "mpeg2_ts",
            },
        )
        mc.block_until_active(timeout=10)
        self._active = cast
        return info.friendly_name or "TV"

    def is_active(self) -> bool:
        return self._active is not None

    # ---- DVR transport on the casting device (times in seconds) ---- #
    def current_time(self) -> float | None:
        if self._active is None:
            return None
        try:
            return self._active.media_controller.status.current_time
        except Exception:
            return None

    def seek(self, t: float) -> None:
        if self._active is not None:
            try:
                self._active.media_controller.seek(max(0.0, t))
            except Exception:
                pass

    def pause(self) -> None:
        if self._active is not None:
            try:
                self._active.media_controller.pause()
            except Exception:
                pass

    def resume(self) -> None:
        if self._active is not None:
            try:
                self._active.media_controller.play()
            except Exception:
                pass

    def is_paused(self) -> bool:
        if self._active is None:
            return False
        try:
            return str(self._active.media_controller.status.player_state) == "PAUSED"
        except Exception:
            return False

    def stop(self) -> None:
        """Stop playback on the TV and disconnect. Blocking — run off UI thread."""
        cast = self._active
        self._active = None
        if cast is None:
            return
        try:
            cast.media_controller.stop()
        except Exception:
            pass
        try:
            cast.quit_app()
        except Exception:
            pass
        try:
            cast.disconnect(timeout=5)
        except Exception:
            pass

    def shutdown(self) -> None:
        self.stop()
        self.stop_discovery()
