from m3u_player.hls import to_hls_url


def test_xtream_ts_becomes_live_m3u8():
    ts = "http://example.com:80/USER/PASS/894968.ts"
    assert to_hls_url(ts) == "http://example.com:80/live/USER/PASS/894968.m3u8"


def test_https_and_no_port():
    ts = "https://host.example/USER/PASS/123.ts"
    assert to_hls_url(ts) == "https://host.example/live/USER/PASS/123.m3u8"


def test_preserves_query_string():
    ts = "http://h:80/U/P/55.ts?token=abc"
    assert to_hls_url(ts) == "http://h:80/live/U/P/55.m3u8?token=abc"


def test_already_m3u8_unchanged():
    url = "http://h:80/live/U/P/55.m3u8"
    assert to_hls_url(url) == url


def test_non_ts_url_unchanged():
    url = "http://h:80/stream/index"
    assert to_hls_url(url) == url


def test_non_xtream_ts_falls_back_to_extension_swap():
    # doesn't match host/USER/PASS/ID.ts shape -> plain .ts -> .m3u8 swap
    url = "http://h:80/a/b/c/d/movie.ts"
    assert to_hls_url(url) == "http://h:80/a/b/c/d/movie.m3u8"
