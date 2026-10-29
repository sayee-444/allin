import queue

from pydantic import BaseModel

from allin.core.errors import MalformedMessage
from allin.ipc.messages import ChatAppend, Close
from allin.main.dispatcher import Dispatcher
from allin.main.events import (
    ErrorOccurred,
    InputReceived,
    MainCommandEntered,
    MainEvent,
    Shutdown,
    WindowClosed,
)


class FakeWindowLink:
    def __init__(self) -> None:
        self.sent: list[BaseModel] = []

    def send(self, message: BaseModel) -> None:
        self.sent.append(message)


def run_dispatcher(*events: MainEvent, links: dict[str, FakeWindowLink]) -> list[str]:
    """Feed events to a dispatcher, run it to completion and return the lines of Main's Chat."""
    main_chat: list[str] = []
    inbox: queue.Queue[MainEvent] = queue.Queue()
    dispatcher = Dispatcher(inbox, main_chat.append)
    for session_id, link in links.items():
        dispatcher.register(session_id, link)
    for event in events:
        inbox.put(event)
    inbox.put(Shutdown())
    dispatcher.run()
    return main_chat


def test_input_from_a_window_is_echoed_back_to_that_window_only():
    a, b = FakeWindowLink(), FakeWindowLink()
    run_dispatcher(InputReceived("aaaaaa", "hola"), links={"aaaaaa": a, "bbbbbb": b})
    assert ChatAppend(role="echo", text="hola") in a.sent
    assert not any(isinstance(m, ChatAppend) for m in b.sent)


def test_closed_window_is_reported_and_no_longer_receives_messages():
    a = FakeWindowLink()
    chat = run_dispatcher(
        WindowClosed("aaaaaa", returncode=0),
        InputReceived("aaaaaa", "late"),
        links={"aaaaaa": a},
    )
    assert any("aaaaaa" in line and "closed" in line for line in chat)
    assert a.sent == []


def test_errors_are_shown_in_main_chat_with_their_session():
    chat = run_dispatcher(
        ErrorOccurred(MalformedMessage(line="garbage"), session_id="aaaaaa"), links={}
    )
    assert any("aaaaaa" in line and "malformed_message" in line for line in chat)


def test_exit_closes_every_window_and_stops():
    a, b = FakeWindowLink(), FakeWindowLink()
    main_chat: list[str] = []
    inbox: queue.Queue[MainEvent] = queue.Queue()
    dispatcher = Dispatcher(inbox, main_chat.append)
    dispatcher.register("aaaaaa", a)
    dispatcher.register("bbbbbb", b)
    inbox.put(MainCommandEntered("/exit"))
    inbox.put(InputReceived("aaaaaa", "never handled"))
    dispatcher.run()
    assert a.sent == [Close()] and b.sent == [Close()]


def test_unknown_main_command_is_reported():
    chat = run_dispatcher(MainCommandEntered("/nope"), links={})
    assert any("/nope" in line for line in chat)
