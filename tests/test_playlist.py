from m3u_player.playlist import Channel, parse_m3u

SAMPLE = """#EXTM3U
#EXTINF:-1 tvg-id="" tvg-logo="http://x/logo.png" group-title="UCL",Ziggo Sport 1 HD [NL]
http://example.com:80/USER/PASS/894968.ts
#EXTINF:-1 group-title="UCL",DAZN 1 HD [DE]
http://example.com:80/USER/PASS/894974.ts
#EXTINF:-1,No Group Channel
http://host/1000.ts
"""


def test_parses_all_channels():
    chans = parse_m3u(SAMPLE)
    assert len(chans) == 3


def test_extracts_name_group_url():
    c = parse_m3u(SAMPLE)[0]
    assert c.name == "Ziggo Sport 1 HD [NL]"
    assert c.group == "UCL"
    assert c.url == "http://example.com:80/USER/PASS/894968.ts"


def test_extracts_stream_id_from_url():
    assert parse_m3u(SAMPLE)[0].stream_id == "894968"


def test_missing_group_becomes_ungrouped():
    assert parse_m3u(SAMPLE)[2].group == "Ungrouped"


def test_skips_extinf_with_no_following_url():
    text = '#EXTM3U\n#EXTINF:-1 group-title="A",Orphan\n#EXTINF:-1 group-title="A",Real\nhttp://h/5.ts\n'
    chans = parse_m3u(text)
    assert len(chans) == 1
    assert chans[0].name == "Real"


def test_handles_crlf_and_blank_lines():
    text = '#EXTM3U\r\n\r\n#EXTINF:-1 group-title="A",Chan\r\nhttp://h/9.ts\r\n'
    chans = parse_m3u(text)
    assert len(chans) == 1 and chans[0].name == "Chan"
