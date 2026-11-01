import pytest

from allin.core.errors import InvalidSetting, MalformedAssignment, UnknownSetting
from allin.core.settings import (
    MainSettings,
    Sampling,
    SessionSettings,
    SettingKey,
    SettingName,
    apply_settings,
    parse_assignments,
    setting_keys,
    shown_settings,
)

MAIN = MainSettings(session=SessionSettings(username="ana"))


def test_every_setting_declares_its_key_scope_default_and_what_it_is():
    keys: dict[str, SettingKey] = {key.name: key for key in setting_keys()}
    assert set(keys) == {
        "base_url",
        "parallel",
        "session.assistant_name",
        "session.username",
        "session.sysprompt",
        "session.model",
        "session.sampling.temperature",
        "session.sampling.top_p",
        "session.sampling.max_output_tokens",
    }
    assert keys["parallel"].scope == "main" and keys["parallel"].default is None
    assert keys["session.model"].scope == "session" and keys["session.model"].default == "allin"
    assert all(key.description for key in keys.values())


def test_a_value_is_parsed_into_the_keys_type():
    changed = apply_settings(MAIN, parse_assignments("session.sampling.temperature=0.3 parallel=2"))
    assert changed.session.sampling.temperature == 0.3
    assert changed.parallel == 2


def test_applying_returns_a_copy_and_leaves_the_original_as_it_was():
    apply_settings(MAIN, parse_assignments("session.username=bea"))
    assert MAIN.session.username == "ana"


def test_an_empty_value_unsets_a_key_that_may_be_left_out():
    sampled = MAIN.model_copy(update={"parallel": 2})
    assert apply_settings(sampled, parse_assignments("parallel=")).parallel is None


def test_session_settings_take_their_keys_without_the_session_prefix():
    session = SessionSettings(sampling=Sampling(top_p=0.9))
    changed = apply_settings(session, parse_assignments("sampling.temperature=0.5"))
    assert changed.sampling == Sampling(temperature=0.5, top_p=0.9)


@pytest.mark.parametrize(
    "key", ["nope", "session", "session.sampling", "session.nope", "session.model.x", ""]
)
def test_an_unknown_key_is_an_error(key: str):
    with pytest.raises(UnknownSetting) as raised:
        apply_settings(MAIN, {SettingName(key): "1"})
    assert raised.value.fields == {"key": key}


@pytest.mark.parametrize(
    ("key", "value"),
    [
        ("parallel", "0"),
        ("parallel", "two"),
        ("session.sampling.temperature", "-1"),
        ("session.sampling.top_p", "1.5"),
        ("session.sampling.max_output_tokens", "0"),
        ("session.username", ""),
        ("base_url", "localhost:9931"),
    ],
)
def test_an_invalid_value_is_an_error_naming_its_key(key: str, value: str):
    with pytest.raises(InvalidSetting) as raised:
        apply_settings(MAIN, {SettingName(key): value})
    assert raised.value.fields["key"] == key


def test_assignments_are_key_value_words_and_quotes_keep_spaces():
    assert parse_assignments('session.username="Ana María" parallel=2') == {
        "session.username": "Ana María",
        "parallel": "2",
    }


@pytest.mark.parametrize("text", ["parallel", "=2", 'session.username="open'])
def test_an_assignment_without_a_key_and_a_value_is_an_error(text: str):
    with pytest.raises(MalformedAssignment):
        parse_assignments(text)


def test_settings_are_shown_one_key_per_line_with_unset_keys_empty():
    shown = shown_settings(MAIN)
    assert "parallel=" in shown
    assert "session.username=ana" in shown
    assert "session.sampling.temperature=" in shown
    assert [line.split("=")[0] for line in shown] == [key.name for key in setting_keys()]


def test_a_sessions_settings_are_shown_with_their_own_keys():
    assert "sampling.top_p=0.9" in shown_settings(SessionSettings(sampling=Sampling(top_p=0.9)))
