"""Server Settings: the key registry, ``clave=valor`` and reading a ``llama serve`` line (#26)."""

import shlex
from collections import Counter
from pathlib import Path

import pytest

from allin.core.errors import (
    HumanOnly,
    InvalidSetting,
    ServerNotAdoptable,
    ServerSettingsInvalid,
    UnknownSetting,
)
from allin.core.origin import HUMAN
from allin.core.settings import parse_assignments
from allin.supervisor.settings import (
    REACHING_KEYS,
    ServerSettings,
    apply_server_assignments,
    check_server_authority,
    load_server_settings,
    parse_cmdline,
    save_server_settings,
    shares_pool,
)
from tests.origins import A_SESSION

# The user's line for one Server (models/como llamar llama serve…), as /proc/<pid>/cmdline has it.
USER_LINE = shlex.split(
    'llama serve -m "Qwen3.8-27B-Uncensored-Q6_K.gguf" --mmproj'
    ' "mmproj-Qwen3.8-27B-Uncensored-f16.gguf" -ngl all -fa on -ctk q8_0 -ctv q8_0 -c 128000'
    " --jinja --temp 0.6 --top-p 0.95 --top-k 20 --min-p 0.05 --presence-penalty 0"
    " --repeat-penalty 1.0 --kv-unified -np 2 --spec-type draft-mtp --port 9931"
)


def pairs(argv: list[str]) -> Counter[str]:
    """A line's arguments as a multiset, each flag with the value that follows it."""
    return Counter(argv)


def test_the_users_line_is_read_into_settings_that_give_back_exactly_its_arguments():
    settings = parse_cmdline(USER_LINE)
    assert settings.model == "Qwen3.8-27B-Uncensored-Q6_K.gguf"
    assert settings.context == 128000 and settings.parallel == 2 and settings.port == 9931
    assert settings.kv_unified is True
    assert settings.extra[:2] == ["--jinja", "--temp"]
    assert settings.arguments()[:2] == ["llama", "serve"]
    assert pairs(settings.arguments()) == pairs(USER_LINE)


def test_a_line_that_sets_little_leaves_the_rest_to_llamas_own_defaults():
    settings = parse_cmdline(["llama-server", "--model=m.gguf", "--port", "8081"])
    assert settings.command == ["llama-server"]
    assert settings.arguments() == ["llama-server", "-m", "m.gguf", "--port", "8081"]


def test_flags_with_long_names_and_a_bare_flash_attn_are_understood():
    line = ["llama", "serve", "--model", "m.gguf", "--ctx-size", "4096", "--parallel", "3"]
    line += ["--n-gpu-layers", "40", "-fa", "-kvu", "--port", "9"]
    settings = parse_cmdline(line)
    assert (settings.context, settings.parallel, settings.gpu_layers) == (4096, 3, "40")
    assert settings.flash_attn == "on" and settings.kv_unified is True


@pytest.mark.parametrize("flag", ["--context-shift", "--context-shift=true"])
def test_a_line_with_context_shift_is_not_adopted_and_says_why(flag: str):
    with pytest.raises(ServerNotAdoptable, match="context-shift"):
        parse_cmdline(["llama", "serve", "-m", "m.gguf", "--port", "9", flag])


def test_a_line_without_a_model_or_that_is_not_llama_is_not_adopted():
    with pytest.raises(ServerNotAdoptable):
        parse_cmdline(["llama", "serve", "--port", "9"])
    with pytest.raises(ServerNotAdoptable):
        parse_cmdline([])


def assign(settings: ServerSettings | None, text: str) -> ServerSettings:
    return apply_server_assignments(settings, parse_assignments(text))


def test_new_settings_need_a_model_and_a_port_and_take_values_as_typed():
    settings = assign(None, 'model=m.gguf port=9940 context=64000 extra="--jinja --temp 0.6"')
    assert (settings.model, settings.port, settings.context) == ("m.gguf", 9940, 64000)
    assert settings.extra == ["--jinja", "--temp", "0.6"]
    with pytest.raises(InvalidSetting, match="port"):
        assign(None, "model=m.gguf")


def test_an_empty_value_unsets_a_key_so_llamas_default_applies():
    settings = assign(None, "model=m.gguf port=9940 parallel=2")
    assert assign(settings, "parallel=").parallel is None


@pytest.mark.parametrize(
    ("text", "error"),
    [
        ("temperature=0.6", UnknownSetting),
        ("context=0", InvalidSetting),
        ("context=lots", InvalidSetting),
        ("flash_attn=maybe", InvalidSetting),
        ("port=70000", InvalidSetting),
        ("cwd=relative/folder", InvalidSetting),
        ("extra=--context-shift", InvalidSetting),
        ('extra="--jinja --context-shift=1"', InvalidSetting),
        ("extra=-kvu", InvalidSetting),
    ],
)
def test_an_unknown_key_or_an_invalid_value_is_rejected(text: str, error: type[Exception]):
    settings = assign(None, "model=m.gguf port=9940")
    with pytest.raises(error):
        assign(settings, text)


def test_the_registry_marks_the_keys_that_name_a_file_or_open_the_network():
    assert set(REACHING_KEYS) <= set(ServerSettings.model_fields)
    assert {"model", "mmproj", "cwd", "command"} <= {
        k for k, v in REACHING_KEYS.items() if "file" in v
    }
    assert {"port", "extra"} <= {k for k, v in REACHING_KEYS.items() if "network" in v}
    assert "context" not in REACHING_KEYS and "parallel" not in REACHING_KEYS


@pytest.mark.parametrize("key", ["model", "port", "extra", "command", "cwd", "mmproj"])
def test_only_a_human_sets_a_key_that_names_a_file_or_opens_the_network(key: str):
    with pytest.raises(HumanOnly):
        check_server_authority(parse_assignments(f"{key}=x"), A_SESSION)
    check_server_authority(parse_assignments(f"{key}=x"), HUMAN)
    check_server_authority(parse_assignments("context=1 parallel=1 gpu_layers=1"), A_SESSION)


def test_saved_settings_load_back_the_same(tmp_path: Path):
    settings = parse_cmdline(USER_LINE, cwd="/srv/models")
    path = tmp_path / "gpu.toml"
    save_server_settings(path, settings)
    assert load_server_settings(path) == settings
    assert "mmproj = " in path.read_text()


def test_a_unified_cache_follows_the_slots_unless_the_settings_say_so():
    one = ServerSettings(model="m.gguf", port=9931)
    two = one.model_copy(update={"parallel": 2})
    assert "--kv-unified" not in one.arguments()
    assert "--kv-unified" in two.arguments()
    assert "--no-kv-unified" in two.model_copy(update={"kv_unified": False}).arguments()


def test_an_invalid_value_names_its_key():
    with pytest.raises(InvalidSetting) as raised:
        assign(assign(None, "model=m.gguf port=9940"), "context=0")
    assert raised.value.fields["key"] == "context"


def test_only_a_human_names_the_key_it_was_refused():
    with pytest.raises(HumanOnly) as raised:
        check_server_authority(parse_assignments("context=1 port=2"), A_SESSION)
    assert raised.value.fields["action"] == "port"


def test_a_broken_toml_names_its_file_and_the_failing_key(tmp_path: Path):
    path = tmp_path / "gpu.toml"
    with pytest.raises(ServerSettingsInvalid) as missing:
        load_server_settings(path)
    assert missing.value.fields == {"file": "gpu.toml", "key": None, "reason": "missing"}
    path.write_text('model = "m.gguf"\nport = 9940\ncontext = -1\n', encoding="utf-8")
    with pytest.raises(ServerSettingsInvalid) as invalid:
        load_server_settings(path)
    assert invalid.value.fields["key"] == "context"
    path.write_text("model = ", encoding="utf-8")
    with pytest.raises(ServerSettingsInvalid):
        load_server_settings(path)


def test_settings_saved_in_a_new_folder_with_a_cache_turned_off_load_back_the_same(
    tmp_path: Path,
):
    settings = assign(None, "model=modèle.gguf port=9940 parallel=2 kv_unified=false")
    path = tmp_path / "servers" / "gpu.toml"
    save_server_settings(path, settings)
    assert load_server_settings(path) == settings
    assert "kv_unified = false" in path.read_text(encoding="utf-8")


def test_a_line_that_gives_no_port_runs_on_llamas_own():
    settings = parse_cmdline(["llama", "serve", "-m", "m.gguf"])
    assert settings.port == 8080


def test_flash_attn_with_its_value_after_an_equals_sign():
    line = ["llama", "serve", "-m", "m.gguf", "--flash-attn=off", "--port", "9"]
    assert parse_cmdline(line).flash_attn == "off"


def test_given_slots_without_a_unified_cache_flag_keep_it_off_as_llama_does():
    settings = parse_cmdline(["llama", "serve", "-m", "m.gguf", "--parallel=2", "--port", "9"])
    assert settings.parallel == 2 and settings.kv_unified is False
    assert "--no-kv-unified" in settings.arguments()
    one = parse_cmdline(["llama", "serve", "-m", "m.gguf", "-np", "1", "--port", "9"])
    assert one.kv_unified is None


def test_a_flag_left_without_its_value_says_which_key_is_wrong():
    with pytest.raises(ServerNotAdoptable) as raised:
        parse_cmdline(["llama", "serve", "-m", "m.gguf", "--port", "9", "-c"])
    assert raised.value.fields["key"] == "context"
    with pytest.raises(ServerNotAdoptable) as empty:
        parse_cmdline(["-m", "m.gguf"])
    assert empty.value.fields["reason"] == "not a llama serve line"


def test_flash_attn_takes_the_value_that_follows_it():
    settings = parse_cmdline(["llama", "serve", "-m", "m.gguf", "-fa", "off", "--port", "9"])
    assert settings.flash_attn == "off" and settings.extra == []


@pytest.mark.parametrize("slots", ["many", "-1"])
def test_slots_that_are_not_a_count_are_not_adopted(slots: str):
    with pytest.raises(ServerNotAdoptable):
        parse_cmdline(["llama", "serve", "-m", "m.gguf", "-np", slots, "--port", "9"])


@pytest.mark.parametrize(
    ("line", "shared"),
    [
        ("llama serve -m m.gguf -c 8192 -np 2 --kv-unified --port 9931", True),
        ("llama serve -m m.gguf -c 8192 -np 2 --no-kv-unified --port 9931", False),
        ("llama serve -m m.gguf -c 8192 -np 2 --port 9931", False),  # llama: off with -np
        ("llama serve -m m.gguf -c 8192 --port 9931", True),  # -np auto turns it on
        ("llama serve -m m.gguf --context-shift", True),  # unreadable: the careful guess
    ],
)
def test_the_running_arguments_say_whether_the_slots_share_a_pool(line: str, shared: bool) -> None:
    assert shares_pool(line.split()) is shared
