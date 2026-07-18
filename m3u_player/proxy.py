from __future__ import annotations

import math
import os
import re
import socket
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse

from m3u_player.recorder import RecordedSeg

_SEG_RE = re.compile(r"^/seg/(\d+)\.ts$")


def lan_ip_for(host: str) -> str:
    """Our source address for reaching ``host``.

    Asking the routing table which local address it would use to talk to a
    specific peer is the only reliable way to get this. Probing a public address
    instead (the old behaviour) returns whatever the *default* route uses, and
    with a VPN up that is the tunnel address — which no device on the LAN can
    reach, so a Chromecast handed that URL silently fails to load anything.
    LAN peers stay on the physical interface even when a VPN owns the default
    route, so asking about the device itself gives the right answer.
    """
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect((host, 80))
        return s.getsockname()[0]
    except Exception:
        return "127.0.0.1"
    finally:
        s.close()


def lan_ip() -> str:
    """Best-effort primary LAN IP, when no specific peer is known."""
    return lan_ip_for("8.8.8.8")


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


# ~3 minutes of lookahead at 2s segments. This is a starvation guard, not a
# style choice: measured over 4 minutes, a 20-segment window stalled three times
# for 100s in total, while 90 segments ran clean. A live playlist still has to
# be bounded — handing a client the whole retained hour makes it buffer the lot
# before showing anything.
LIVE_WINDOW_SEGMENTS = 90


# Segments withheld from the end of the live manifest, so the player's idea of
# "live" sits behind what we have actually downloaded.
#
# This is the buffering cushion. The upstream feed is not smooth — segment
# arrival was measured gapping by 5s, and the recorder running at ~0.5x realtime
# for stretches. A player sitting at the true live edge starves the moment that
# happens, which is what breaks picture and sound up. Holding segments back
# means a stall upstream is invisible until the reserve is used up.
#
# Measured at 0: holding segments back made things *worse* — 4 dark periods
# totalling 104s over 4 minutes, against none with no hold-back. A cushion only
# absorbs jitter if the buffer fills at least as fast as it drains, and here the
# recorder was measured capturing ~0.5x realtime. Withholding content from a
# player that is already starving just starves it sooner.
#
# Kept as a knob because "add more buffering" is the obvious response to
# break-up, and on this setup it is the wrong one. Fix the fill rate instead.
LIVE_HOLD_BACK_SEGMENTS = 0


def build_dvr_manifest(
    segments: list[RecordedSeg],
    local_base: str,
    window: int = LIVE_WINDOW_SEGMENTS,
    hold_back: int = LIVE_HOLD_BACK_SEGMENTS,
) -> str:
    """Sliding-window *live* manifest — ``/live.m3u8``, for the Chromecast and
    for local playback while following live.

    No ``#EXT-X-ENDLIST``, so the client keeps reloading it and follows live. Do
    not add a playlist type or an endlist: both consumers depend on this staying
    an open-ended live playlist.

    Only the newest ``window`` segments are listed, with ``#EXT-X-MEDIA-SEQUENCE``
    moved up to match. Listing the whole retained hour instead makes this an
    ever-growing playlist, which is not what a live playlist looks like — Qt's
    demuxer was measured stalling for >20s on one, and a Chromecast would buffer
    the entire history before showing anything. Seeking further back is what
    ``/dvr.m3u8`` is for.
    """
    # Hold back the newest segments as reserve — but never starve the client of
    # a playlist entirely while the buffer is still filling.
    if hold_back > 0 and len(segments) > hold_back + 1:
        segments = segments[:-hold_back]
    if window > 0:
        segments = segments[-window:]
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
        self._port = 0
        self._recorder = None

    def attach_recorder(self, recorder) -> None:
        self._recorder = recorder

    @property
    def base_url(self) -> str | None:
        return self._base

    def manifest_url(self, client_host: str | None = None) -> str:
        """Open-ended live manifest for the Chromecast, on an address the device
        can actually reach.

        ``client_host`` is the device's own IP. The source address for reaching
        it is what the device must call back on — which is *not* the default
        route when a VPN is up: the tunnel's address is unreachable from the LAN.
        """
        host = lan_ip_for(client_host) if client_host else lan_ip()
        port = self._server.server_address[1] if self._server else 0
        return f"http://{host}:{port}/live.m3u8"

    def live_url(self) -> str:
        """Open-ended live manifest on loopback — for local playback at the live
        edge.

        Playing live must not use the VOD snapshot: a snapshot ends a few seconds
        past the live edge, so playback would reach the end and need re-arming
        almost immediately, and every re-arm is a visible reload. An open-ended
        playlist is one Qt keeps reloading by itself, so live playback runs
        uninterrupted. It just cannot be seeked — which is what ``/dvr.m3u8`` is
        for, once the viewer rewinds.
        """
        return f"{self._local_base}/live.m3u8"

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
                    # Segment URLs must be reachable *from whoever asked*. A
                    # Chromecast needs our LAN address; local playback asked
                    # over loopback and should stay there.
                    client = self.client_address[0]
                    if client.startswith("127."):
                        base = proxy._local_base
                    else:
                        base = f"http://{lan_ip_for(client)}:{proxy._port}"
                    proxy._serve_manifest(self, build_dvr_manifest, base)
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
        port = self._port = self._server.server_address[1]
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
        """Serve one recorded segment, honouring ``Range``.

        Range support is not optional here: ffmpeg's HTTP client issues range
        requests when seeking inside a segment, and answering every one with a
        full ``200`` leaves the demuxer's byte offsets disagreeing with what it
        actually received — playback then stalls after a seek while still
        reporting itself as playing. Segments are several MB, so serving the
        whole thing repeatedly is wasteful besides.
        """
        path = self._recorder.path_for(media_seq) if self._recorder else None
        if not path:
            h.send_error(404)
            return
        try:
            size = os.path.getsize(path)
        except OSError:
            h.send_error(404)
            return

        start, end = 0, size - 1
        rng = h.headers.get("Range")
        partial = False
        if rng:
            m = re.match(r"bytes=(\d*)-(\d*)", rng.strip())
            if m:
                lo, hi = m.group(1), m.group(2)
                if lo:
                    start = min(int(lo), max(0, size - 1))
                    end = int(hi) if hi else size - 1
                elif hi:  # suffix range: last N bytes
                    start = max(0, size - int(hi))
                end = min(end, size - 1)
                partial = True

        length = max(0, end - start + 1)
        try:
            with open(path, "rb") as f:
                f.seek(start)
                data = f.read(length)
        except OSError:
            h.send_error(404)
            return

        h.send_response(206 if partial else 200)
        h.send_header("Content-Type", "video/mp2t")
        h.send_header("Content-Length", str(len(data)))
        h.send_header("Accept-Ranges", "bytes")
        if partial:
            h.send_header("Content-Range", f"bytes {start}-{start + len(data) - 1}/{size}")
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
