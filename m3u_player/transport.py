from __future__ import annotations

# Within this many seconds of the live edge counts as "live".
LIVE_EPSILON = 2.0

# How far back from the newest buffered content the "live" position sits. Landing
# exactly on the newest segment starves the player (nothing buffered ahead), which
# is what makes "jump to live" stall — keeping ~1 segment of runway makes it
# feel instant.
LIVE_MARGIN = 12.0


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


class SmoothedClock:
    """Turns a step-wise measurement into a smoothly advancing value.

    The DVR buffer only grows in ~10s jumps as segments land, while playback
    position advances continuously — so a naive ``buffer_length - position``
    counts down for 10s then snaps back. This projects the measured value forward
    with wall-clock time so the difference stays steady, catching up instantly if
    reality moves ahead and resyncing down only after a genuine stall.
    """

    def __init__(self, resync_threshold: float = 20.0):
        self._resync = resync_threshold
        self._anchor: tuple[float, float] | None = None  # (value, timestamp)

    def reset(self) -> None:
        self._anchor = None

    def update(self, measured, now: float, advancing: bool = True):
        if measured is None:
            return None
        measured = float(measured)
        if self._anchor is None:
            self._anchor = (measured, now)
            return measured
        base, ts = self._anchor
        projected = base + (now - ts) if advancing else base
        if measured > projected or (projected - measured) > self._resync:
            value = measured  # reality moved ahead, or we stalled — snap to truth
        else:
            value = projected  # keep advancing smoothly
        self._anchor = (value, now)
        return value


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
