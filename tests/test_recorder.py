from m3u_player.recorder import RecordedSeg, parse_hls_index

INDEX = """#EXTM3U
#EXT-X-VERSION:3
#EXT-X-TARGETDURATION:5
#EXT-X-MEDIA-SEQUENCE:12
#EXTINF:5.000000,
seg_000012.ts
#EXTINF:4.800000,
seg_000013.ts
#EXTINF:5.000000,
seg_000014.ts
"""


def test_parses_segments_with_paths_and_durations():
    segs = parse_hls_index(INDEX, "/dvr")
    assert segs == [
        RecordedSeg(12, "/dvr/seg_000012.ts", 5.0),
        RecordedSeg(13, "/dvr/seg_000013.ts", 4.8),
        RecordedSeg(14, "/dvr/seg_000014.ts", 5.0),
    ]


def test_media_seq_comes_from_filename():
    segs = parse_hls_index(INDEX, "/dvr")
    assert [s.media_seq for s in segs] == [12, 13, 14]


def test_total_duration_is_buffer_window():
    segs = parse_hls_index(INDEX, "/dvr")
    assert round(sum(s.duration for s in segs), 1) == 14.8


def test_ignores_comments_and_unknown_lines():
    text = "#EXTM3U\n#EXT-X-ENDLIST\n#EXTINF:5,\nnot_a_segment.foo\n#EXTINF:5,\nseg_000001.ts\n"
    segs = parse_hls_index(text, "/dvr")
    assert [s.media_seq for s in segs] == [1]


def test_empty_index_yields_nothing():
    assert parse_hls_index("#EXTM3U\n", "/dvr") == []
