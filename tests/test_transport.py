from m3u_player.transport import (
    SeekAccumulator,
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
