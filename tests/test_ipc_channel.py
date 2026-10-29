import queue
from pathlib import Path

from allin.core.errors import AllInError, MalformedMessage
from allin.ipc.channel import Channel, create_fifo_pair
from allin.ipc.messages import (
    ChatAppend,
    UserInput,
    decode_main_message,
    decode_window_message,
)

TIMEOUT = 2.0


class Recorder:
    """Listener that records every callback in arrival order."""

    def __init__(self) -> None:
        self.events: queue.Queue[object] = queue.Queue()

    def on_message(self, message: object) -> None:
        self.events.put(message)

    def on_error(self, error: AllInError) -> None:
        self.events.put(error)

    def on_eof(self) -> None:
        self.events.put("eof")

    def next(self) -> object:
        return self.events.get(timeout=TIMEOUT)


def test_main_and_window_exchange_messages(tmp_path: Path):
    pair = create_fifo_pair(tmp_path, "abc123")
    main_side, window_side = Recorder(), Recorder()
    main = Channel(pair.from_window, pair.to_window, decode_window_message, main_side)
    window = Channel(pair.to_window, pair.from_window, decode_main_message, window_side)
    main.start()
    window.start()

    window.send(UserInput(text="hola"))
    assert main_side.next() == UserInput(text="hola")

    main.send(ChatAppend(role="echo", text="hola"))
    assert window_side.next() == ChatAppend(role="echo", text="hola")

    window.close()
    assert main_side.next() == "eof"
    main.close()


def test_malformed_line_is_reported_and_reader_keeps_going(tmp_path: Path):
    pair = create_fifo_pair(tmp_path, "abc123")
    listener = Recorder()
    main = Channel(pair.from_window, pair.to_window, decode_window_message, listener)
    main.start()

    with open(pair.from_window, "w", encoding="utf-8") as peer:
        peer.write("garbage\n")
        peer.write('{"type":"user_input","text":"after"}\n')

    error = listener.next()
    assert isinstance(error, MalformedMessage)
    assert error.fields["line"] == "garbage"
    assert listener.next() == UserInput(text="after")
    assert listener.next() == "eof"
    main.close()


def test_invalid_utf8_is_reported_as_malformed_and_reader_keeps_going(tmp_path: Path):
    pair = create_fifo_pair(tmp_path, "abc123")
    listener = Recorder()
    main = Channel(pair.from_window, pair.to_window, decode_window_message, listener)
    main.start()

    with open(pair.from_window, "wb") as peer:
        peer.write(b'{"type":"user_input","text":"\xff\xfe"}\n')
        peer.write(b'{"type":"user_input","text":"after"}\n')

    assert isinstance(listener.next(), MalformedMessage)
    assert listener.next() == UserInput(text="after")
    assert listener.next() == "eof"
    main.close()


def test_fifo_pair_is_named_after_the_session(tmp_path: Path):
    pair = create_fifo_pair(tmp_path, "abc123")
    assert pair.to_window == tmp_path / "abc123.in"
    assert pair.from_window == tmp_path / "abc123.out"
    assert pair.to_window.is_fifo() and pair.from_window.is_fifo()
    pair.remove()
    assert not pair.to_window.exists() and not pair.from_window.exists()
