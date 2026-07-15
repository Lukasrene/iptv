from __future__ import annotations

from pathlib import Path
from urllib.request import Request, urlopen

# Many IPTV panels reset connections from clients that don't look like a media
# player, so identify as VLC (what these streams are meant to be played with).
_USER_AGENT = "VLC/3.0.20 LibVLC/3.0.20"


def _default_fetcher(url: str) -> str:
    req = Request(url, headers={"User-Agent": _USER_AGENT})  # noqa: S310 (user URL)
    with urlopen(req, timeout=60) as resp:
        return resp.read().decode("utf-8", errors="replace")


def load_playlist_text(source: dict, cache_path, fetcher=_default_fetcher) -> str:
    """Return playlist text for a source.

    ``source`` is ``{"type": "file"|"url", "value": str}``. For a file, read it
    directly. For a URL, fetch fresh (so credential tokens stay current) and
    write the result to ``cache_path``; if the fetch fails, fall back to the
    cached copy so the user can still browse the last-known list.
    """
    cache_path = Path(cache_path)
    if source["type"] == "file":
        return Path(source["value"]).read_text(encoding="utf-8", errors="replace")

    # URL source: try fresh fetch, cache it; on failure fall back to cache.
    try:
        text = fetcher(source["value"])
    except Exception:
        if cache_path.exists():
            return cache_path.read_text(encoding="utf-8", errors="replace")
        raise
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    cache_path.write_text(text, encoding="utf-8")
    return text
