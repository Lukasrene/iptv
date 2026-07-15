from urllib.parse import quote

from m3u_player.proxy import rewrite_manifest

EFF = "http://45.67.98.15:37481/live/USER/PASS/894968.m3u8"
LOCAL = "http://192.168.1.50:55555"

MEDIA_PLAYLIST = """#EXTM3U
#EXT-X-VERSION:3
#EXT-X-MEDIA-SEQUENCE:710
#EXT-X-TARGETDURATION:10
#EXTINF:10.000000,
/hls/abc123/894968_3263.ts
#EXTINF:10.000000,
/hls/abc123/894968_3264.ts
"""


def test_comment_and_tag_lines_preserved():
    out = rewrite_manifest(MEDIA_PLAYLIST, EFF, LOCAL)
    assert "#EXTM3U" in out
    assert "#EXT-X-MEDIA-SEQUENCE:710" in out
    assert "#EXTINF:10.000000," in out


def test_relative_segment_rewritten_to_proxied_absolute():
    out = rewrite_manifest(MEDIA_PLAYLIST, EFF, LOCAL)
    abs_seg = "http://45.67.98.15:37481/hls/abc123/894968_3263.ts"
    expected = f"{LOCAL}/seg?u={quote(abs_seg, safe='')}"
    assert expected in out


def test_no_original_relative_paths_leak_through():
    out = rewrite_manifest(MEDIA_PLAYLIST, EFF, LOCAL)
    # every non-comment, non-empty line must be an absolute local proxy URL
    for line in out.splitlines():
        if line and not line.startswith("#"):
            assert line.startswith(LOCAL + "/"), line


def test_variant_playlist_routed_back_through_proxy():
    master = "#EXTM3U\n#EXT-X-STREAM-INF:BANDWIDTH=1\nchunks.m3u8\n"
    out = rewrite_manifest(master, EFF, LOCAL)
    abs_variant = "http://45.67.98.15:37481/live/USER/PASS/chunks.m3u8"
    expected = f"{LOCAL}/live.m3u8?src={quote(abs_variant, safe='')}"
    assert expected in out


def test_absolute_segment_url_also_proxied():
    pl = "#EXTM3U\nhttp://other.host/x/9.ts\n"
    out = rewrite_manifest(pl, EFF, LOCAL)
    expected = f"{LOCAL}/seg?u={quote('http://other.host/x/9.ts', safe='')}"
    assert expected in out
