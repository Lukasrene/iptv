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
