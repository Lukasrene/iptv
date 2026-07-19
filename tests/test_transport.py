from m3u_player.transport import (
    SeekAccumulator,
    SmoothedClock,
    clamp_seek,
    fmt_hms,
    hover_label,
    signed_delta_label,
)


def test_fmt_hms_minutes_seconds():
    assert fmt_hms(0) == "0:00"
    assert fmt_hms(5) == "0:05"
    assert fmt_hms(65) == "1:05"
    assert fmt_hms(600) == "10:00"


def test_fmt_hms_hours():
    assert fmt_hms(3600) == "1:00:00"
    assert fmt_hms(3661) == "1:01:01"


def test_hover_label_behind_live():
    # hovering 155s before the live edge
    assert hover_label(pos_s=445, live_edge_s=600) == "−2:35 behind live"


def test_hover_label_at_live_shows_live():
    assert hover_label(pos_s=599, live_edge_s=600) == "LIVE"
    assert hover_label(pos_s=600, live_edge_s=600) == "LIVE"


def test_clamp_seek_within_bounds():
    assert clamp_seek(50, 0, 120) == 50
    assert clamp_seek(-10, 0, 120) == 0
    assert clamp_seek(999, 0, 120) == 120


def test_signed_delta_label():
    assert signed_delta_label(90, 90) == "±0:00"
    assert signed_delta_label(60, 90) == "−0:30"
    assert signed_delta_label(120, 90) == "+0:30"


def test_seek_accumulator_stacks_rapid_presses():
    acc = SeekAccumulator()
    assert acc.add(-5, current=100, lo=0, hi=3600) == 95
    # subsequent presses stack on the pending target, ignoring the (lagging) current
    assert acc.add(-5, current=100, lo=0, hi=3600) == 90
    assert acc.add(-5, current=100, lo=0, hi=3600) == 85
    assert acc.pending is True
    assert acc.take() == 85
    assert acc.pending is False


def test_seek_accumulator_clamps_to_window():
    acc = SeekAccumulator()
    assert acc.add(-5, current=3, lo=0, hi=3600) == 0
    acc.take()
    assert acc.add(10, current=3595, lo=0, hi=3600) == 3600


def test_seek_accumulator_resumes_from_current_after_commit():
    acc = SeekAccumulator()
    acc.add(-5, current=100, lo=0, hi=3600)
    acc.take()
    assert acc.add(-5, current=80, lo=0, hi=3600) == 75


# --- SmoothedClock: keeps "behind live" steady between 10s segment arrivals --- #


def test_clock_advances_smoothly_between_segment_arrivals():
    c = SmoothedClock()
    assert c.update(100, now=0) == 100
    # buffer length hasn't changed yet, but wall time moved: edge advances anyway
    assert c.update(100, now=1) == 101
    assert c.update(100, now=5) == 105


def test_clock_catches_up_when_measurement_jumps_ahead():
    c = SmoothedClock()
    c.update(100, now=0)
    c.update(100, now=9)  # projected 109
    assert c.update(112, now=9.5) == 112


def test_clock_resyncs_down_after_long_stall():
    c = SmoothedClock(resync_threshold=20)
    c.update(100, now=0)
    # nothing recorded for 30s -> projection is way ahead, snap back to reality
    assert c.update(100, now=30) == 100


def test_clock_holds_while_paused():
    c = SmoothedClock()
    c.update(50, now=0)
    assert c.update(50, now=3, advancing=False) == 50


def test_clock_reset():
    c = SmoothedClock()
    c.update(100, now=0)
    c.reset()
    assert c.update(5, now=99) == 5


# --------------------------------------------------------------------------- #
# Stall detection.
#
# From the viewer's POV a freeze frame and buffering look identical. The player
# reports a position 4x/second; if it stops advancing while playback is
# supposed to be running, that IS buffering/starvation — surface it, after a
# grace period so ordinary decode jitter doesn't flicker the label.
# --------------------------------------------------------------------------- #
from m3u_player.transport import StallDetector


def test_advancing_position_is_not_stalled():
    d = StallDetector(grace=1.5)
    assert d.update(1.0, playing=True, now=100.0) is False
    assert d.update(1.25, playing=True, now=100.25) is False


def test_frozen_position_stalls_after_grace():
    d = StallDetector(grace=1.5)
    d.update(1.0, playing=True, now=100.0)
    assert d.update(1.0, playing=True, now=101.0) is False
    assert d.update(1.0, playing=True, now=101.6) is True


def test_recovery_clears_the_stall():
    d = StallDetector(grace=1.5)
    d.update(1.0, playing=True, now=100.0)
    d.update(1.0, playing=True, now=102.0)
    assert d.update(1.5, playing=True, now=102.25) is False


def test_paused_playback_never_stalls():
    d = StallDetector(grace=1.5)
    d.update(1.0, playing=True, now=100.0)
    assert d.update(1.0, playing=False, now=105.0) is False
    # resuming starts the grace period fresh
    assert d.update(1.0, playing=True, now=105.25) is False
    assert d.update(1.0, playing=True, now=107.0) is True


# --------------------------------------------------------------------------- #
# Replay skip after a recorder restart.
#
# On reconnect the provider replays the last ~10-20s it already sent. The
# relaunched ffmpeg appends that with continuous timestamps (deliberately — see
# Recorder.start), so a viewer following live seamlessly re-watches it and
# drifts further behind true live on every drop. The cure is to note where the
# buffer ended at the restart and, once it has regrown a full cushion past that
# point, snap live playback forward over the replayed stretch.
# --------------------------------------------------------------------------- #
from m3u_player.transport import ReplaySkip


def test_no_skip_without_a_restart():
    r = ReplaySkip()
    assert r.should_skip(buffer_end=100.0, cushion=18.0) is False


def test_skip_waits_until_the_buffer_regrows_a_cushion():
    r = ReplaySkip()
    r.note_restart(buffer_end=100.0)
    assert r.should_skip(buffer_end=110.0, cushion=18.0) is False
    assert r.should_skip(buffer_end=118.0, cushion=18.0) is True


def test_skip_fires_once():
    r = ReplaySkip()
    r.note_restart(buffer_end=100.0)
    assert r.should_skip(buffer_end=120.0, cushion=18.0) is True
    assert r.should_skip(buffer_end=125.0, cushion=18.0) is False


def test_cancel_forgets_the_pending_skip():
    r = ReplaySkip()
    r.note_restart(buffer_end=100.0)
    r.cancel()
    assert r.should_skip(buffer_end=200.0, cushion=18.0) is False


def test_back_to_back_restarts_anchor_at_the_newest():
    r = ReplaySkip()
    r.note_restart(buffer_end=100.0)
    r.note_restart(buffer_end=120.0)
    assert r.should_skip(buffer_end=130.0, cushion=18.0) is False
    assert r.should_skip(buffer_end=138.0, cushion=18.0) is True
