from __future__ import annotations

# Within this many seconds of the live edge counts as "live".
LIVE_EPSILON = 2.0


def fmt_hms(seconds: float) -> str:
    """Format seconds as M:SS, or H:MM:SS past an hour."""
    s = int(round(seconds))
    h, rem = divmod(s, 3600)
    m, sec = divmod(rem, 60)
    if h:
        return f"{h}:{m:02d}:{sec:02d}"
    return f"{m}:{sec:02d}"


def hover_label(pos_s: float, live_edge_s: float) -> str:
    """Label for a hovered/target timeline position: how far behind live it is."""
    behind = live_edge_s - pos_s
    if behind <= LIVE_EPSILON:
        return "LIVE"
    return f"−{fmt_hms(behind)} behind live"


def clamp_seek(target_s: float, lo_s: float, hi_s: float) -> float:
    """Clamp a seek target into the seekable window [lo, hi]."""
    return max(lo_s, min(target_s, hi_s))


def signed_delta_label(hovered_s: float, current_s: float) -> str:
    """Signed jump from the current position to a target, e.g. ``−0:30`` / ``+0:30``."""
    d = hovered_s - current_s
    if abs(d) < 0.5:
        return "±0:00"
    return f"{'+' if d > 0 else '−'}{fmt_hms(abs(d))}"


class SeekAccumulator:
    """Coalesces rapid ±step presses so they stack instead of each resolving from
    the (lagging) observed position. ``add`` returns the running target; a debounce
    timer eventually calls ``take`` to fire one seek."""

    def __init__(self):
        self._pending: float | None = None

    def add(self, delta: float, current: float, lo: float, hi: float) -> float:
        base = self._pending if self._pending is not None else current
        self._pending = clamp_seek(base + delta, lo, hi)
        return self._pending

    @property
    def pending(self) -> bool:
        return self._pending is not None

    def take(self) -> float | None:
        p = self._pending
        self._pending = None
        return p
