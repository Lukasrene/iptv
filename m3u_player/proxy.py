from __future__ import annotations

import re
import socket
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, quote, urljoin, urlparse

import requests

_UA = "VLC/3.0.20 LibVLC/3.0.20"
_URI_ATTR = re.compile(r'URI="([^"]+)"')


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


def rewrite_manifest(text: str, effective_url: str, local_base: str) -> str:
    """Rewrite an HLS playlist so every segment/variant/key URL points back at
    this local proxy as an absolute URL.

    ``effective_url`` is the URL the manifest was actually loaded from (after any
    redirects) — relative URIs resolve against it. ``local_base`` is this proxy's
    ``http://<lan-ip>:<port>`` base.
    """

    def proxied_seg(abs_url: str) -> str:
        return f"{local_base}/seg?u={quote(abs_url, safe='')}"

    out = []
    for raw in text.splitlines():
        line = raw.rstrip("\r")
        if not line:
            out.append(line)
            continue
        if line.startswith("#"):
            # Rewrite any URI="..." attribute (e.g. #EXT-X-KEY) to a proxied URL.
            if 'URI="' in line:
                line = _URI_ATTR.sub(
                    lambda m: f'URI="{proxied_seg(urljoin(effective_url, m.group(1)))}"',
                    line,
                )
            out.append(line)
            continue
        abs_url = urljoin(effective_url, line)
        if abs_url.split("?", 1)[0].endswith(".m3u8"):
            out.append(f"{local_base}/live.m3u8?src={quote(abs_url, safe='')}")
        else:
            out.append(proxied_seg(abs_url))
    return "\n".join(out) + "\n"


class HlsProxy:
    """Local HTTP server that relays a provider's HLS stream to a Chromecast.

    The Chromecast is pointed at :meth:`manifest_url`; this proxy fetches the real
    manifest (following redirects, with a VLC user-agent), rewrites its URLs to
    point back here, and streams the segment bytes through. That hides the
    provider's redirect + session-hashed relative segment paths, which the
    Chromecast's own player cannot follow.
    """

    def __init__(self):
        self._server: ThreadingHTTPServer | None = None
        self._thread: threading.Thread | None = None
        self._source: str | None = None
        self._base: str | None = None
        self._session = requests.Session()
        self._session.headers["User-Agent"] = _UA

    def set_source(self, url: str) -> None:
        self._source = url

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
            def log_message(self, *args):  # silence access logging
                pass

            def do_GET(self):
                parsed = urlparse(self.path)
                qs = parse_qs(parsed.query)
                if parsed.path == "/live.m3u8":
                    proxy._serve_manifest(self, qs)
                elif parsed.path == "/seg":
                    proxy._serve_segment(self, qs)
                else:
                    self.send_error(404)

        self._server = ThreadingHTTPServer(("", 0), Handler)
        port = self._server.server_address[1]
        self._base = f"http://{lan_ip()}:{port}"
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)
        self._thread.start()

    def _serve_manifest(self, h: BaseHTTPRequestHandler, qs: dict) -> None:
        src = (qs.get("src") or [self._source])[0]
        if not src:
            h.send_error(503)
            return
        try:
            r = self._session.get(src, allow_redirects=True, timeout=15)
            r.raise_for_status()
            body = rewrite_manifest(r.text, r.url, self._base).encode("utf-8")
        except Exception:
            h.send_error(502)
            return
        h.send_response(200)
        h.send_header("Content-Type", "application/vnd.apple.mpegurl")
        h.send_header("Content-Length", str(len(body)))
        h.send_header("Access-Control-Allow-Origin", "*")
        h.end_headers()
        h.wfile.write(body)

    def _serve_segment(self, h: BaseHTTPRequestHandler, qs: dict) -> None:
        u = (qs.get("u") or [None])[0]
        if not u:
            h.send_error(400)
            return
        try:
            r = self._session.get(u, stream=True, allow_redirects=True, timeout=20)
            r.raise_for_status()
        except Exception:
            h.send_error(502)
            return
        h.send_response(200)
        h.send_header("Content-Type", r.headers.get("Content-Type", "video/mp2t"))
        if "Content-Length" in r.headers:
            h.send_header("Content-Length", r.headers["Content-Length"])
        h.send_header("Access-Control-Allow-Origin", "*")
        h.end_headers()
        try:
            for chunk in r.iter_content(65536):
                if chunk:
                    h.wfile.write(chunk)
        except Exception:
            pass  # client (Chromecast) went away between segments

    def stop(self) -> None:
        if self._server is not None:
            try:
                self._server.shutdown()
            except Exception:
                pass
            self._server = None
