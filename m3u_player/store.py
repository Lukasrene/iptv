from __future__ import annotations

import json
from pathlib import Path

from m3u_player.playlist import Channel


def default_config_path() -> Path:
    base = Path.home() / "Library" / "Application Support" / "M3UPlayer"
    return base / "config.json"


def _fav_key(group: str, name: str) -> str:
    # Stable across credential-token rotation: group + name only (not the URL,
    # whose credential prefix the provider periodically rotates).
    return f"{group}␟{name}"


class Config:
    """App configuration: playlist source, favorites, last group/channel.

    Persisted as JSON. Favorites are matched by ``group + name`` so a starred
    channel re-binds to a freshly fetched entry even after the provider rotates
    the credential token embedded in stream URLs.
    """

    def __init__(self, path: Path | None = None):
        self.path = path or default_config_path()
        self.source: dict | None = None       # {"type": "file"|"url", "value": str}
        self.favorites: list[dict] = []        # [{group, name, stream_id}]
        self.last_group: str | None = None
        self.last_watched: dict | None = None  # {group, name, stream_id}

    # --- favorites ---
    def is_favorite(self, c: Channel) -> bool:
        key = _fav_key(c.group, c.name)
        return any(_fav_key(f["group"], f["name"]) == key for f in self.favorites)

    def toggle_favorite(self, c: Channel) -> None:
        key = _fav_key(c.group, c.name)
        for i, f in enumerate(self.favorites):
            if _fav_key(f["group"], f["name"]) == key:
                del self.favorites[i]
                return
        self.favorites.append(
            {"group": c.group, "name": c.name, "stream_id": c.stream_id}
        )

    # --- persistence ---
    def to_dict(self) -> dict:
        return {
            "source": self.source,
            "favorites": self.favorites,
            "last_group": self.last_group,
            "last_watched": self.last_watched,
        }

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps(self.to_dict(), indent=2))

    @classmethod
    def load(cls, path: Path | None = None) -> "Config":
        cfg = cls(path=path)
        if cfg.path.exists():
            data = json.loads(cfg.path.read_text())
            cfg.source = data.get("source")
            cfg.favorites = data.get("favorites", [])
            cfg.last_group = data.get("last_group")
            cfg.last_watched = data.get("last_watched")
        return cfg
