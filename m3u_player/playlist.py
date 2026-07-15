from __future__ import annotations

import re
from dataclasses import dataclass

_GROUP_RE = re.compile(r'group-title="([^"]*)"')
_STREAMID_RE = re.compile(r"/(\d+)\.[A-Za-z0-9]+(?:\?.*)?$")


@dataclass(frozen=True)
class Channel:
    name: str
    group: str
    url: str
    stream_id: str


def _stream_id(url: str) -> str:
    m = _STREAMID_RE.search(url)
    return m.group(1) if m else ""


def parse_m3u(text: str) -> list[Channel]:
    """Parse M3U/M3U8 playlist text into a list of Channels.

    Handles the common IPTV export format: an ``#EXTINF`` directive line
    carrying ``group-title="..."`` and a display name after the last comma,
    followed by the stream URL on the next non-comment line. Malformed or
    incomplete entries (an #EXTINF with no following URL) are skipped.
    """
    channels: list[Channel] = []
    pending = None  # (name, group) awaiting a URL line
    for raw in text.splitlines():
        line = raw.strip()
        if not line:
            continue
        if line.startswith("#EXTINF"):
            group_match = _GROUP_RE.search(line)
            group = group_match.group(1).strip() if group_match else ""
            name = line.split(",", 1)[1].strip() if "," in line else ""
            pending = (name or "Unnamed", group or "Ungrouped")
        elif line.startswith("#"):
            continue  # other directives (#EXTM3U, #EXTGRP, ...)
        else:
            if pending is not None:
                name, group = pending
                channels.append(Channel(name, group, line, _stream_id(line)))
                pending = None
    return channels
