from __future__ import annotations

import re

# http(s)://host[:port]/USER/PASS/<id>.ts[?query]
_XTREAM_TS = re.compile(r"^(https?://[^/]+)/([^/]+)/([^/]+)/(\d+)\.ts(\?.*)?$")


def to_hls_url(url: str) -> str:
    """Return an HLS (.m3u8) URL for a stream.

    Xtream-codes providers expose the same channel as HLS under a ``/live/``
    path: ``http://host/USER/PASS/<id>.ts`` -> ``http://host/live/USER/PASS/<id>.m3u8``.
    For URLs that don't match that shape, fall back to a plain ``.ts`` -> ``.m3u8``
    extension swap; anything that isn't a ``.ts`` URL is returned unchanged.
    """
    m = _XTREAM_TS.match(url)
    if m:
        base, user, pw, sid, query = m.groups()
        return f"{base}/live/{user}/{pw}/{sid}.m3u8{query or ''}"
    if url.endswith(".ts"):
        return url[:-3] + ".m3u8"
    return url
