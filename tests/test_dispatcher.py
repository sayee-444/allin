import queue
from pathlib import Path

import pytest
from pydantic import BaseModel

from allin.core.errors import MalformedMessage
from allin.core.ids import MainId
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
from allin.main.sessions import SessionStore

ME = MainId("root")


class FakeWindowLink:
    def __init__(self) -> None:
        self.sent: list[BaseModel] = []
        self.closed = False

    def send(self, message: BaseModel) -> None:
        self.sent.append(message)

    def close(self) -> None:
        self.closed = True


class MainHarness:
    """A Dispatcher over a real SessionStore whose Windows are fakes."""

    def __init__(self, tmp_path: Path) -> None:
        self.root = tmp_path / "sessions"
        self.sessions = SessionStore(self.root)
        self.opened: list[tuple[str, FakeWindowLink]] = []

    def windows(self, session_id: str) -> list[FakeWindowLink]:
        return [link for sid, link in self.opened if sid == session_id]

    def run(self, *events: MainEvent) -> list[str]:
        """Feed events to a dispatcher, run it to completion and return Main's Chat."""
        main_chat: list[str] = []
        inbox: queue.Queue[MainEvent] = queue.Queue()
        dispatcher = Dispatcher(inbox, main_chat.append, self.sessions, self._open_window, ME)
        for event in events:
            inbox.put(event)
        inbox.put(Shutdown())
        dispatcher.run()
        return main_chat

    def _open_window(self, session_id: str) -> FakeWindowLink:
        link = FakeWindowLink()
        self.opened.append((session_id, link))
        return link


@pytest.fixture
def main(tmp_path: Path) -> MainHarness:
    return MainHarness(tmp_path)


def test_new_creates_a_session_owned_by_this_main_and_opens_its_window(main: MainHarness):
    main.run(MainCommandEntered("/new"))
    [(session_id, _)] = main.opened
    session = main.sessions.load(session_id)
    assert session.owner == ME and session.created_by == ME


def test_open_opens_a_window_for_a_session_with_none(main: MainHarness):
    session = main.sessions.create(owner=ME)
    main.run(MainCommandEntered(f"/open {session.id}"))
    assert len(main.windows(session.id)) == 1


def test_closing_a_window_keeps_its_session_so_it_can_be_opened_again(main: MainHarness):
    session = main.sessions.create(owner=ME)
    main.run(
        MainCommandEntered(f"/open {session.id}"),
        WindowClosed(session.id, returncode=0),
        MainCommandEntered(f"/open {session.id}"),
    )
    first, second = main.windows(session.id)
    assert first.closed and Close() not in first.sent
    assert main.sessions.load(session.id) == session
    assert second.sent == [Close()] and second.closed


def test_open_on_a_session_whose_window_is_open_is_an_error_and_does_nothing(main: MainHarness):
    session = main.sessions.create(owner=ME)
    chat = main.run(
        MainCommandEntered(f"/open {session.id}"),
        MainCommandEntered(f"/open {session.id}"),
    )
    assert len(main.windows(session.id)) == 1
    assert any("window_already_open" in line and session.id in line for line in chat)


def test_open_on_an_unknown_session_is_an_error(main: MainHarness):
    chat = main.run(MainCommandEntered("/open zzzzzz"))
    assert main.opened == []
    assert any("session_not_found" in line and "zzzzzz" in line for line in chat)


def test_open_on_a_session_owned_by_another_main_is_an_error(main: MainHarness):
    session = main.sessions.create(owner=MainId("other"))
    chat = main.run(MainCommandEntered(f"/open {session.id}"))
    assert main.opened == []
    assert any("session_not_owned" in line and session.id in line for line in chat)


@pytest.mark.parametrize("command", ["/open", "/open a b", "/new extra"])
def test_a_command_with_the_wrong_arguments_shows_its_usage(main: MainHarness, command: str):
    chat = main.run(MainCommandEntered(command))
    assert main.opened == []
    assert any("invalid_arguments" in line and "usage" in line for line in chat)


def test_input_from_a_window_is_echoed_back_to_that_window_only(main: MainHarness):
    a, b = main.sessions.create(owner=ME), main.sessions.create(owner=ME)
    main.run(
        MainCommandEntered(f"/open {a.id}"),
        MainCommandEntered(f"/open {b.id}"),
        InputReceived(a.id, "hola"),
    )
    [window_a], [window_b] = main.windows(a.id), main.windows(b.id)
    assert ChatAppend(role="echo", text="hola") in window_a.sent
    assert not any(isinstance(m, ChatAppend) for m in window_b.sent)


def test_closed_window_is_reported_and_no_longer_receives_messages(main: MainHarness):
    session = main.sessions.create(owner=ME)
    chat = main.run(
        MainCommandEntered(f"/open {session.id}"),
        WindowClosed(session.id, returncode=0),
        InputReceived(session.id, "late"),
    )
    assert any(session.id in line and "closed" in line for line in chat)
    [window] = main.windows(session.id)
    assert window.sent == []


def test_errors_are_shown_in_main_chat_with_their_session(main: MainHarness):
    chat = main.run(ErrorOccurred(MalformedMessage(line="garbage"), session_id="aaaaaa"))
    assert any("aaaaaa" in line and "malformed_message" in line for line in chat)


def test_exit_closes_every_window_and_stops(main: MainHarness):
    main.run(
        MainCommandEntered("/new"),
        MainCommandEntered("/new"),
        MainCommandEntered("/exit"),
        MainCommandEntered("/new"),
    )
    assert len(main.opened) == 2
    assert all(link.sent == [Close()] and link.closed for _, link in main.opened)


def test_unknown_main_command_is_reported(main: MainHarness):
    chat = main.run(MainCommandEntered("/nope"))
    assert any("/nope" in line for line in chat)


def test_a_session_that_cannot_be_stored_is_reported_and_main_keeps_running(main: MainHarness):
    main.root.write_text("not a folder")
    chat = main.run(MainCommandEntered("/new"), MainCommandEntered("/nope"))
    assert main.opened == []
    assert any("session_storage_failed" in line for line in chat)
    assert any("/nope" in line for line in chat)
