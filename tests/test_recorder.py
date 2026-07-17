from m3u_player.recorder import DvrBuffer, Seg, parse_media_segments

EFF = "http://45.67.98.15:37481/live/U/P/894968.m3u8"

PLAYLIST = """#EXTM3U
#EXT-X-VERSION:3
#EXT-X-MEDIA-SEQUENCE:710
#EXT-X-TARGETDURATION:10
#EXTINF:10.000000,
/hls/abc/894968_710.ts
#EXTINF:9.500000,
/hls/abc/894968_711.ts
"""


def test_parse_segments_seq_uri_duration():
    segs = parse_media_segments(PLAYLIST, EFF)
    assert segs == [
        Seg(710, "http://45.67.98.15:37481/hls/abc/894968_710.ts", 10.0),
        Seg(711, "http://45.67.98.15:37481/hls/abc/894968_711.ts", 9.5),
    ]


def test_parse_defaults_media_sequence_zero():
    text = "#EXTM3U\n#EXTINF:6,\nseg0.ts\n"
    segs = parse_media_segments(text, EFF)
    assert segs[0].media_seq == 0
    assert segs[0].uri.endswith("/live/U/P/seg0.ts")


def test_buffer_add_and_total_duration(tmp_path):
    buf = DvrBuffer(max_seconds=3600)
    buf.add(1, str(tmp_path / "a.ts"), 10.0)
    buf.add(2, str(tmp_path / "b.ts"), 8.0)
    assert buf.total_duration() == 18.0
    assert [s.media_seq for s in buf.snapshot()] == [1, 2]


def test_buffer_ignores_duplicate_or_older_seq(tmp_path):
    buf = DvrBuffer()
    buf.add(5, str(tmp_path / "a.ts"), 10.0)
    buf.add(5, str(tmp_path / "a.ts"), 10.0)  # duplicate
    buf.add(3, str(tmp_path / "old.ts"), 10.0)  # older
    assert [s.media_seq for s in buf.snapshot()] == [5]


def test_eviction_is_age_based_and_deletes_files(tmp_path):
    buf = DvrBuffer(max_seconds=20)
    paths = []
    for seq in range(1, 5):  # 4 segments x 10s = 40s, cap 20s
        p = tmp_path / f"{seq}.ts"
        p.write_bytes(b"x")
        paths.append(p)
        buf.add(seq, str(p), 10.0)
    removed = buf.evict()
    # keeps only the newest 20s (seq 3 and 4); older files deleted from disk
    assert [s.media_seq for s in buf.snapshot()] == [3, 4]
    assert not paths[0].exists() and not paths[1].exists()
    assert paths[2].exists() and paths[3].exists()
    assert [r.media_seq for r in removed] == [1, 2]
