from __future__ import annotations

import math
import re
import socket
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse

from m3u_player.recorder import RecordedSeg

_SEG_RE = re.compile(r"^/seg/(\d+)\.ts$")


def lan_ip() -> str:
    """Best-effort primary LAN IP (so a Chromecast on the network can reach us)."""
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("8.8.8.8", 80))
        return s.getsockname()[0]
    except Exception:
        return "127.0.0.1"
    finally:
        s.close()


def _manifest_lines(segments: list[RecordedSeg], local_base: str) -> list[str]:
    """Header + segment list shared by both manifest flavours."""
    first_seq = segments[0].media_seq if segments else 0
    target = math.ceil(max((s.duration for s in segments), default=10.0))
    lines = [
        "#EXTM3U",
        "#EXT-X-VERSION:3",
        f"#EXT-X-TARGETDURATION:{target}",
        f"#EXT-X-MEDIA-SEQUENCE:{first_seq}",
    ]
    for s in segments:
        lines.append(f"#EXTINF:{s.duration:.3f},")
        lines.append(f"{local_base}/seg/{s.media_seq}.ts")
    return lines


def build_dvr_manifest(segments: list[RecordedSeg], local_base: str) -> str:
    """Sliding-window *live* manifest — served at ``/live.m3u8`` for the Chromecast.

    No ``#EXT-X-ENDLIST``, so the receiver keeps reloading it and follows live.
    Do not add a playlist type or an endlist here: the cast path depends on this
    staying an open-ended live playlist.
    """
    return "\n".join(_manifest_lines(segments, local_base)) + "\n"


def build_vod_manifest(segments: list[RecordedSeg], local_base: str) -> str:
    """Closed *VOD* snapshot — served at ``/dvr.m3u8`` for local playback.

    Qt's FFmpeg demuxer reports ``duration=0`` and refuses to seek an open-ended
    live playlist (an ``EVENT`` type claims to be seekable but still reports no
    duration). Only a closed VOD playlist gives us a real duration and working
    seeks — measured at 0.23s to first frame after a seek.

    The trade-off is that this is a *snapshot*: it stops at the newest segment
    known when it was built, so a client watching behind live must periodically
    re-arm against a fresh copy. See ``MainWindow._rearm_dvr``.
    """
    lines = _manifest_lines(segments, local_base)
    # after the header, before the segments
    lines.insert(4, "#EXT-X-PLAYLIST-TYPE:VOD")
    lines.append("#EXT-X-ENDLIST")
    return "\n".join(lines) + "\n"


class HlsProxy:
    """Local HTTP server that serves the DVR buffer to VLC and the Chromecast.

    ``/live.m3u8`` returns a seekable manifest built from the attached Recorder's
    on-disk buffer; ``/seg/<n>.ts`` serves a recorded segment file. Because the
    Mac records and serves everything, both local playback and the TV can seek
    anywhere in the buffered window, and the provider's redirect/session-hash
    scheme never reaches the Chromecast.
    """

    def __init__(self):
        self._server: ThreadingHTTPServer | None = None
        self._thread: threading.Thread | None = None
        self._base: str | None = None        # LAN address, for the Chromecast
        self._local_base: str | None = None  # loopback, for local playback
        self._recorder = None

    def attach_recorder(self, recorder) -> None:
        self._recorder = recorder

    @property
    def base_url(self) -> str | None:
        return self._base

    def manifest_url(self) -> str:
        """Open-ended live manifest on the LAN address — for the Chromecast,
        which has to reach this machine over the network."""
        return f"{self._base}/live.m3u8"

    def dvr_url(self) -> str:
        """Closed VOD snapshot on the loopback address — for the local player.

        Deliberately *not* the LAN address: local playback never needs to leave
        the machine, and routing it over the LAN address makes playback hostage
        to whatever the network is doing. A VPN coming up mid-session moves
        ``lan_ip()`` to an address that is not locally reachable, which stalls
        playback until it times out. Loopback is immune to that, and faster.

        Carries a cache-busting query so re-arming re-fetches rather than
        replaying the stale snapshot.
        """
        return f"{self._local_base}/dvr.m3u8?t={time.time():.3f}"

    def start(self) -> None:
        if self._server is not None:
            return
        proxy = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def do_GET(self):
                path = urlparse(self.path).path
                if path == "/live.m3u8":
                    # cast: segments must be reachable over the LAN
                    proxy._serve_manifest(self, build_dvr_manifest, proxy._base)
                    return
                if path == "/dvr.m3u8":
                    # local: keep the whole chain on loopback
                    proxy._serve_manifest(self, build_vod_manifest, proxy._local_base)
                    return
                m = _SEG_RE.match(path)
                if m:
                    proxy._serve_segment(self, int(m.group(1)))
                    return
                self.send_error(404)

        self._server = ThreadingHTTPServer(("", 0), Handler)
        port = self._server.server_address[1]
        self._base = f"http://{lan_ip()}:{port}"
        self._local_base = f"http://127.0.0.1:{port}"
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)
        self._thread.start()

    def _serve_manifest(self, h: BaseHTTPRequestHandler, builder, base: str) -> None:
        if self._recorder is None:
            h.send_error(503)
            return
        body = builder(self._recorder.snapshot(), base).encode("utf-8")
        h.send_response(200)
        h.send_header("Content-Type", "application/vnd.apple.mpegurl")
        h.send_header("Content-Length", str(len(body)))
        h.send_header("Cache-Control", "no-store")
        h.send_header("Access-Control-Allow-Origin", "*")
        h.end_headers()
        try:
            h.wfile.write(body)
        except Exception:
            pass

    def _serve_segment(self, h: BaseHTTPRequestHandler, media_seq: int) -> None:
        path = self._recorder.path_for(media_seq) if self._recorder else None
        if not path:
            h.send_error(404)
            return
        try:
            with open(path, "rb") as f:
                data = f.read()
        except OSError:
            h.send_error(404)
            return
        h.send_response(200)
        h.send_header("Content-Type", "video/mp2t")
        h.send_header("Content-Length", str(len(data)))
        h.send_header("Access-Control-Allow-Origin", "*")
        h.end_headers()
        try:
            h.wfile.write(data)
        except Exception:
            pass

    def stop(self) -> None:
        if self._server is not None:
            try:
                self._server.shutdown()
            except Exception:
                pass
            self._server = None
