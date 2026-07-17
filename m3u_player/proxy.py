from __future__ import annotations

import math
import re
import socket
import threading
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


def build_dvr_manifest(segments: list[RecordedSeg], local_base: str) -> str:
    """Build a seekable live HLS manifest from the recorded DVR buffer.

    Lists every buffered segment (oldest→newest) as a local proxy URL, with the
    correct ``#EXT-X-MEDIA-SEQUENCE`` and no ``#EXT-X-ENDLIST`` — a sliding-window
    live playlist that both VLC and the Chromecast can seek within.
    """
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
        self._base: str | None = None
        self._recorder = None

    def attach_recorder(self, recorder) -> None:
        self._recorder = recorder

    @property
    def base_url(self) -> str | None:
        return self._base

    def manifest_url(self) -> str:
        return f"{self._base}/live.m3u8"

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
                    proxy._serve_manifest(self)
                    return
                m = _SEG_RE.match(path)
                if m:
                    proxy._serve_segment(self, int(m.group(1)))
                    return
                self.send_error(404)

        self._server = ThreadingHTTPServer(("", 0), Handler)
        port = self._server.server_address[1]
        self._base = f"http://{lan_ip()}:{port}"
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)
        self._thread.start()

    def _serve_manifest(self, h: BaseHTTPRequestHandler) -> None:
        if self._recorder is None:
            h.send_error(503)
            return
        body = build_dvr_manifest(self._recorder.snapshot(), self._base).encode("utf-8")
        h.send_response(200)
        h.send_header("Content-Type", "application/vnd.apple.mpegurl")
        h.send_header("Content-Length", str(len(body)))
        h.send_header("Access-Control-Allow-Origin", "*")
        h.end_headers()
        h.wfile.write(body)

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
