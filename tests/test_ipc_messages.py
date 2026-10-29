import pytest

from allin.core.errors import AllInError, MalformedMessage
from allin.ipc.messages import (
    ChatAppend,
    Close,
    UserInput,
    WindowReady,
    decode_main_message,
    decode_window_message,
    encode,
)


def test_window_messages_round_trip():
    for message in (UserInput(text="hola\nmundo"), WindowReady()):
        assert decode_window_message(encode(message)) == message


def test_main_messages_round_trip():
    for message in (ChatAppend(role="user", label="ana", text="hola"), Close()):
        assert decode_main_message(encode(message)) == message


def test_encoded_message_is_a_single_json_line():
    line = encode(UserInput(text="a\nb"))
    assert line.endswith("\n")
    assert line.count("\n") == 1
    assert line == '{"type":"user_input","text":"a\\nb"}\n'


@pytest.mark.parametrize(
    "line",
    [
        "not json\n",
        '{"type":"unknown"}\n',
        '{"type":"user_input"}\n',
        '{"type":"close"}\n',  # a Main message is not valid in the Window -> Main direction
    ],
)
def test_invalid_window_message_raises_malformed_message(line: str):
    with pytest.raises(MalformedMessage) as info:
        decode_window_message(line)
    assert isinstance(info.value, AllInError)
    assert info.value.code == "malformed_message"
    assert info.value.fields["line"] == line.rstrip("\n")
