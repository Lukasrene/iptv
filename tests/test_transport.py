from m3u_player.transport import clamp_seek, fmt_hms, hover_label


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
