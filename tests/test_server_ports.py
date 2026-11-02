"""The ports no Alcance reaches: every Server of AllIn's, the observed one too (ADR-0016)."""

from pathlib import Path

from allin.main.links import LinkResolver
from allin.supervisor.settings import ServerSettings, save_server_settings


def test_they_are_each_supervisors_port_and_the_observed_servers(tmp_path: Path):
    servers = tmp_path / "servers"
    servers.mkdir()
    save_server_settings(servers / "fast.toml", ServerSettings(model="a.gguf", port=9932))
    save_server_settings(servers / "big.toml", ServerSettings(model="b.gguf", port=9940))
    (servers / "broken.toml").write_text("port = 'not a number'\n")
    resolver = LinkResolver(servers, "http://127.0.0.1:9931")
    assert resolver.server_ports() == frozenset({9931, 9932, 9940})


def test_an_observed_server_without_a_port_in_its_url_is_on_its_schemes(tmp_path: Path):
    assert LinkResolver(tmp_path, "http://localhost").server_ports() == frozenset({80})
    assert LinkResolver(tmp_path, "https://localhost").server_ports() == frozenset({443})


def test_a_supervisor_whose_settings_broke_still_keeps_its_port_if_it_names_one(tmp_path: Path):
    # Its Server may still be running there: a broken file must not open the port to tools.
    (tmp_path / "half.toml").write_text("port = 9950\nparallel = 'many'\n")
    (tmp_path / "garbled.toml").write_text("port = = 9951\n")
    resolver = LinkResolver(tmp_path, "http://127.0.0.1:9931")
    assert resolver.server_ports() == frozenset({9931, 9950})
