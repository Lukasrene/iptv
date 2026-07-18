from m3u_player.proxy import build_dvr_manifest, build_vod_manifest
from m3u_player.recorder import RecordedSeg

BASE = "http://192.168.1.50:55555"
SEGS = [
    RecordedSeg(710, "/tmp/710.ts", 10.0),
    RecordedSeg(711, "/tmp/711.ts", 9.5),
    RecordedSeg(712, "/tmp/712.ts", 10.0),
]


def test_manifest_media_sequence_is_first_segment():
    out = build_dvr_manifest(SEGS, BASE)
    assert "#EXT-X-MEDIA-SEQUENCE:710" in out


def test_manifest_lists_all_segments_as_local_urls():
    out = build_dvr_manifest(SEGS, BASE)
    for seq in (710, 711, 712):
        assert f"{BASE}/seg/{seq}.ts" in out


def test_manifest_is_seekable_live_no_endlist():
    out = build_dvr_manifest(SEGS, BASE)
    assert "#EXT-X-ENDLIST" not in out
    assert out.startswith("#EXTM3U")


def test_manifest_target_duration_rounds_up_max():
    out = build_dvr_manifest(SEGS, BASE)
    assert "#EXT-X-TARGETDURATION:10" in out


def test_manifest_segment_durations_present():
    out = build_dvr_manifest(SEGS, BASE)
    assert "#EXTINF:10.000," in out
    assert "#EXTINF:9.500," in out


def test_empty_buffer_still_valid_header():
    out = build_dvr_manifest([], BASE)
    assert out.startswith("#EXTM3U")
    assert "#EXT-X-MEDIA-SEQUENCE:0" in out


# --------------------------------------------------------------------------- #
# VOD manifest — what the *local* player seeks in.
#
# Qt's FFmpeg demuxer reports duration=0 and refuses to seek a sliding-window
# live playlist; it only seeks a closed VOD one. So local playback gets this
# variant while the Chromecast keeps consuming build_dvr_manifest() above.
# --------------------------------------------------------------------------- #
def test_vod_manifest_is_closed_and_typed():
    out = build_vod_manifest(SEGS, BASE)
    assert "#EXT-X-PLAYLIST-TYPE:VOD" in out
    assert out.rstrip().endswith("#EXT-X-ENDLIST")


def test_vod_manifest_lists_same_segments_as_live():
    out = build_vod_manifest(SEGS, BASE)
    for seq in (710, 711, 712):
        assert f"{BASE}/seg/{seq}.ts" in out


def test_vod_manifest_media_sequence_matches_first_segment():
    out = build_vod_manifest(SEGS, BASE)
    assert "#EXT-X-MEDIA-SEQUENCE:710" in out


def test_vod_manifest_empty_buffer_still_valid():
    out = build_vod_manifest([], BASE)
    assert out.startswith("#EXTM3U")
    assert "#EXT-X-ENDLIST" in out


def test_live_manifest_stays_open_ended():
    """Regression guard: the cast path must not acquire an ENDLIST."""
    assert "#EXT-X-ENDLIST" not in build_dvr_manifest(SEGS, BASE)
    assert "#EXT-X-PLAYLIST-TYPE" not in build_dvr_manifest(SEGS, BASE)


# --------------------------------------------------------------------------- #
# The live manifest is a sliding window, not the whole retained buffer.
# --------------------------------------------------------------------------- #
MANY = [RecordedSeg(700 + n, f"/tmp/{n}.ts", 2.0) for n in range(50)]


def test_live_manifest_lists_only_the_newest_segments():
    out = build_dvr_manifest(MANY, BASE, window=20, hold_back=0)
    assert out.count("#EXTINF:") == 20
    assert f"{BASE}/seg/749.ts" in out       # newest present
    assert f"{BASE}/seg/700.ts" not in out   # oldest dropped


def test_live_manifest_media_sequence_follows_the_window():
    out = build_dvr_manifest(MANY, BASE, window=20, hold_back=0)
    assert "#EXT-X-MEDIA-SEQUENCE:730" in out


def test_live_manifest_shorter_than_window_lists_everything():
    out = build_dvr_manifest(SEGS, BASE, window=20)
    assert out.count("#EXTINF:") == 3


def test_vod_manifest_still_lists_the_whole_buffer():
    """The seekable snapshot must not be windowed — that is the DVR depth."""
    out = build_vod_manifest(MANY, BASE)
    assert out.count("#EXTINF:") == 50
    assert f"{BASE}/seg/700.ts" in out


# --------------------------------------------------------------------------- #
# Buffering cushion: the live manifest lags what we have actually downloaded,
# so an upstream stall is absorbed instead of starving the player.
# --------------------------------------------------------------------------- #
def test_live_manifest_holds_newest_segments_in_reserve():
    out = build_dvr_manifest(MANY, BASE, window=90, hold_back=6)
    assert f"{BASE}/seg/749.ts" not in out   # newest 6 withheld
    assert f"{BASE}/seg/743.ts" in out       # newest offered


def test_hold_back_does_not_empty_a_small_buffer():
    """While the buffer is still filling there must still be something to play."""
    out = build_dvr_manifest(SEGS, BASE, hold_back=6)
    assert out.count("#EXTINF:") == 3


def test_vod_snapshot_is_not_held_back():
    """Rewinding should reach everything recorded, cushion or not."""
    out = build_vod_manifest(MANY, BASE)
    assert f"{BASE}/seg/749.ts" in out
