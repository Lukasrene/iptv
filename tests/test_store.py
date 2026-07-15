from m3u_player.playlist import Channel
from m3u_player.store import Config


def chan(name, group, sid, prefix="AAA/BBB"):
    return Channel(name, group, f"http://h/{prefix}/{sid}.ts", sid)


def test_toggle_and_check_favorite():
    cfg = Config()
    c = chan("Ziggo Sport 1 HD", "UCL", "894968")
    assert not cfg.is_favorite(c)
    cfg.toggle_favorite(c)
    assert cfg.is_favorite(c)
    cfg.toggle_favorite(c)
    assert not cfg.is_favorite(c)


def test_favorite_survives_url_token_rotation():
    cfg = Config()
    cfg.toggle_favorite(chan("Ziggo Sport 1 HD", "UCL", "894968", prefix="OLD/TOKEN"))
    # same channel, provider rotated the credential prefix
    rotated = chan("Ziggo Sport 1 HD", "UCL", "894968", prefix="NEW/TOKEN")
    assert cfg.is_favorite(rotated)


def test_save_and_load_roundtrip(tmp_path):
    path = tmp_path / "config.json"
    cfg = Config(path=path)
    cfg.source = {"type": "url", "value": "http://p/get.php"}
    cfg.toggle_favorite(chan("A", "G", "1"))
    cfg.last_group = "G"
    cfg.last_watched = {"group": "G", "name": "A", "stream_id": "1"}
    cfg.save()

    loaded = Config.load(path)
    assert loaded.source["value"] == "http://p/get.php"
    assert loaded.is_favorite(chan("A", "G", "1", prefix="OTHER"))
    assert loaded.last_group == "G"
    assert loaded.last_watched["name"] == "A"
