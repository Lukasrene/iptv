from m3u_player.proxy import build_dvr_manifest
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
