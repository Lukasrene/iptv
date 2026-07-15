import pytest

from m3u_player.source import load_playlist_text


def test_loads_from_file(tmp_path):
    f = tmp_path / "pl.m3u"
    f.write_text("#EXTM3U\n#EXTINF:-1,A\nhttp://h/1.ts\n")
    cache = tmp_path / "cache.m3u"
    text = load_playlist_text({"type": "file", "value": str(f)}, cache)
    assert "A" in text


def test_url_fetch_writes_cache(tmp_path):
    cache = tmp_path / "cache.m3u"
    text = load_playlist_text(
        {"type": "url", "value": "http://p/get.php"},
        cache,
        fetcher=lambda url: "#EXTM3U\nFRESH",
    )
    assert text == "#EXTM3U\nFRESH"
    assert cache.read_text() == "#EXTM3U\nFRESH"


def test_url_failure_falls_back_to_cache(tmp_path):
    cache = tmp_path / "cache.m3u"
    cache.write_text("#EXTM3U\nCACHED")

    def boom(url):
        raise OSError("network down")

    text = load_playlist_text({"type": "url", "value": "http://p"}, cache, fetcher=boom)
    assert text == "#EXTM3U\nCACHED"


def test_url_failure_no_cache_raises(tmp_path):
    cache = tmp_path / "missing.m3u"

    def boom(url):
        raise OSError("network down")

    with pytest.raises(OSError):
        load_playlist_text({"type": "url", "value": "http://p"}, cache, fetcher=boom)
