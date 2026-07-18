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


# --------------------------------------------------------------------------- #
# Absolute timeline.
#
# ffmpeg's rolling window deletes segments off the front, which used to shift
# every UI position underneath the viewer. media_seq increments monotonically
# for the life of a recording, so it anchors an absolute timeline; the recorder
# accumulates the duration of everything that has aged out.
# --------------------------------------------------------------------------- #
from m3u_player.recorder import EvictionTracker


def test_eviction_tracker_starts_at_zero():
    assert EvictionTracker().evicted_seconds == 0.0


def test_eviction_tracker_ignores_a_stable_window():
    t = EvictionTracker()
    segs = [RecordedSeg(1, "a", 2.0), RecordedSeg(2, "b", 2.0)]
    t.observe(segs)
    t.observe(segs)
    assert t.evicted_seconds == 0.0


def test_eviction_tracker_accumulates_dropped_segments():
    t = EvictionTracker()
    t.observe([RecordedSeg(1, "a", 2.0), RecordedSeg(2, "b", 3.0)])
    # seg 1 ages out, seg 3 arrives
    t.observe([RecordedSeg(2, "b", 3.0), RecordedSeg(3, "c", 2.0)])
    assert t.evicted_seconds == 2.0
    # seg 2 ages out too
    t.observe([RecordedSeg(3, "c", 2.0)])
    assert t.evicted_seconds == 5.0


def test_eviction_tracker_survives_a_gap_between_snapshots():
    """Several segments can age out between two polls."""
    t = EvictionTracker()
    t.observe([RecordedSeg(n, str(n), 2.0) for n in (1, 2, 3, 4)])
    t.observe([RecordedSeg(4, "4", 2.0)])
    assert t.evicted_seconds == 6.0


def test_eviction_tracker_ignores_empty_snapshot():
    """An unreadable index must not be mistaken for total eviction."""
    t = EvictionTracker()
    t.observe([RecordedSeg(1, "a", 2.0), RecordedSeg(2, "b", 2.0)])
    t.observe([])
    assert t.evicted_seconds == 0.0
