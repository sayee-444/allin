import queue
from pathlib import Path

import pytest
from pydantic import BaseModel

from allin.core.errors import MalformedMessage
from allin.core.ids import MainId
from allin.core.settings import MainSettings, Sampling, SessionSettings
from allin.ipc.messages import ChatAppend, ChatDelta, Close, TurnState
from allin.main.conversation import Conversation
from allin.main.dispatcher import Dispatcher
from allin.main.events import (
    ErrorOccurred,
    InputReceived,
    MainCommandEntered,
    MainEvent,
    Shutdown,
    WindowClosed,
)
from allin.main.executors import TurnJob, execute
from allin.main.sessions import Session, SessionStore
from tests.llm_fakes import FakeLlm, answer, full_reply

ME = MainId("root")
SETTINGS = MainSettings(session=SessionSettings(username="ana", assistant_name="AllIn"))


class FakeWindowLink:
    def __init__(self) -> None:
        self.sent: list[BaseModel] = []
        self.closed = False

    def send(self, message: BaseModel) -> None:
        self.sent.append(message)

    def close(self) -> None:
        self.closed = True


class DrainingInbox(queue.Queue[MainEvent]):
    """An inbox that says Shutdown once it is empty, so a test's dispatcher always stops."""

    def get(self, block: bool = True, timeout: float | None = None) -> MainEvent:
        try:
            return super().get(block=False)
        except queue.Empty:
            return Shutdown()


class MainHarness:
    """A Dispatcher over a real SessionStore whose Windows are fakes and whose LLM is fake.

    Turns run synchronously on submit; their events reach the inbox after the ones already in
    it, as they would from an executor thread.
    """

    def __init__(self, tmp_path: Path) -> None:
        self.root = tmp_path / "sessions"
        self.sessions = SessionStore(self.root)
        self.sysprompts = tmp_path / "sysprompts"
        self.sysprompts.mkdir()
        (self.sysprompts / "default.txt").write_text("I am $assistant_name; you are $username.")
        self.llm = FakeLlm()
        self.settings = SETTINGS
        self.opened: list[tuple[str, FakeWindowLink]] = []

    def create_session(self, owner: MainId = ME, **settings: object) -> Session:
        session_settings = SETTINGS.session.model_copy(update=settings)
        return self.sessions.create(owner, session_settings, "I am AllIn; you are ana.")

    def windows(self, session_id: str) -> list[FakeWindowLink]:
        return [link for sid, link in self.opened if sid == session_id]

    def run(self, *events: MainEvent) -> list[str]:
        """Feed events to a dispatcher, run it until nothing is left and return Main's Chat."""
        main_chat: list[str] = []
        inbox = DrainingInbox()

        def submit_turn(job: TurnJob) -> None:
            execute(self.llm, job, inbox.put, lambda: False)

        dispatcher = Dispatcher(
            inbox,
            main_chat.append,
            self.sessions,
            self._open_window,
            submit_turn,
            ME,
            self.settings,
            self.sysprompts,
        )
        for event in events:
            inbox.put(event)
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
    session = main.create_session()
    main.run(MainCommandEntered(f"/open {session.id}"))
    assert len(main.windows(session.id)) == 1


def test_closing_a_window_keeps_its_session_so_it_can_be_opened_again(main: MainHarness):
    session = main.create_session()
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
    session = main.create_session()
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
    session = main.create_session(owner=MainId("other"))
    chat = main.run(MainCommandEntered(f"/open {session.id}"))
    assert main.opened == []
    assert any("session_not_owned" in line and session.id in line for line in chat)


@pytest.mark.parametrize(
    "command", ["/open", "/open a b", "/new extra", "/send aaaaaa", "/send aaaaaa   "]
)
def test_a_command_with_the_wrong_arguments_shows_its_usage(main: MainHarness, command: str):
    chat = main.run(MainCommandEntered(command))
    assert main.opened == []
    assert any("invalid_arguments" in line and "usage" in line for line in chat)


def chat_messages(window: FakeWindowLink) -> list[BaseModel]:
    return [m for m in window.sent if isinstance(m, ChatAppend | ChatDelta | TurnState)]


def test_enter_in_a_window_streams_the_turn_into_that_window_only(main: MainHarness):
    a, b = main.create_session(), main.create_session()
    main.llm = FakeLlm(full_reply("thinking", "Hello"))
    main.run(
        MainCommandEntered(f"/open {a.id}"),
        MainCommandEntered(f"/open {b.id}"),
        InputReceived(a.id, "hola"),
    )
    [window_a], [window_b] = main.windows(a.id), main.windows(b.id)
    assert chat_messages(window_a) == [
        ChatAppend(role="user", label="ana", text="hola"),
        TurnState(active=True),
        TurnState(active=True),
        ChatDelta(role="reasoning", label="reasoning", text="thinking"),
        ChatDelta(role="answer", label="AllIn", text="Hello"),
        TurnState(active=False),
    ]
    assert not any(isinstance(m, ChatAppend | ChatDelta) for m in window_b.sent)


def test_a_finished_turn_is_saved_whole_and_the_next_turn_sees_it(main: MainHarness):
    session = main.create_session()
    main.llm = FakeLlm(full_reply("hm", "Mango"), full_reply("hm", "I said Mango"))
    main.run(InputReceived(session.id, "pick a fruit"), InputReceived(session.id, "which?"))

    conversation = main.sessions.load_conversation(session)
    assert conversation is not None
    assert [turn.prompt for turn in conversation.turns] == ["pick a fruit", "which?"]
    second_input = main.llm.requests[1]["input"]
    assert second_input[0] == {"role": "developer", "content": "I am AllIn; you are ana."}
    assert second_input[1] == {"role": "user", "content": "pick a fruit"}
    assert second_input[3]["content"][0]["text"] == "Mango"
    assert second_input[-1] == {"role": "user", "content": "which?"}


def test_a_turn_that_fails_half_way_leaves_the_conversation_as_it_was(main: MainHarness):
    session = main.create_session()
    main.llm = FakeLlm([answer("half an ans")])  # the stream ends without response.completed
    chat = main.run(MainCommandEntered(f"/open {session.id}"), InputReceived(session.id, "hi"))
    assert main.sessions.load_conversation(session) == Conversation(
        sysprompt="I am AllIn; you are ana."
    )
    [window] = main.windows(session.id)
    assert any(
        isinstance(m, ChatAppend) and m.role == "error" and "llm_request_failed" in m.text
        for m in window.sent
    )
    assert window.sent[-2] == TurnState(active=False)  # Enter is unblocked again
    assert any("llm_request_failed" in line for line in chat)


def test_send_from_main_keeps_the_whole_text_and_sends_only_the_sampling_params_set(
    main: MainHarness,
):
    session = main.create_session(sampling=Sampling(temperature=0.3))
    main.llm = FakeLlm(full_reply("", "ok"))
    main.run(MainCommandEntered(f"/send {session.id} two words\nand a line"))
    [request] = main.llm.requests
    assert request["input"][-1] == {"role": "user", "content": "two words\nand a line"}
    assert request["model"] == "allin" and request["temperature"] == 0.3
    assert "top_p" not in request and "max_output_tokens" not in request


def test_text_from_a_window_reaches_the_llm_exactly_as_typed(main: MainHarness):
    session = main.create_session()
    main.llm = FakeLlm(full_reply("", "ok"))
    main.run(InputReceived(session.id, "\n    indented()\n\tdone"))
    [request] = main.llm.requests
    assert request["input"][-1] == {"role": "user", "content": "\n    indented()\n\tdone"}


def test_a_session_waits_for_its_own_turn_but_not_for_the_others(main: MainHarness):
    main.settings = SETTINGS.model_copy(update={"parallel": 2})
    a, b = main.create_session(), main.create_session()
    main.llm = FakeLlm(*(full_reply("", str(n)) for n in range(3)))
    main.run(
        InputReceived(a.id, "a1"),
        InputReceived(a.id, "a2"),
        InputReceived(b.id, "b1"),
    )
    prompts = [request["input"][-1]["content"] for request in main.llm.requests]
    assert prompts == ["a1", "b1", "a2"]


def test_send_to_an_unknown_session_is_an_error(main: MainHarness):
    chat = main.run(MainCommandEntered("/send zzzzzz hola"))
    assert main.llm.requests == []
    assert any("session_not_found" in line for line in chat)


def test_new_renders_the_sysprompt_as_the_start_of_the_conversation(main: MainHarness):
    main.run(MainCommandEntered("/new"))
    [(session_id, _)] = main.opened
    session = main.sessions.load(session_id)
    assert main.sessions.load_conversation(session) == Conversation(
        sysprompt="I am AllIn; you are ana."
    )


def test_closed_window_is_reported_and_no_longer_receives_messages(main: MainHarness):
    session = main.create_session()
    chat = main.run(
        MainCommandEntered(f"/open {session.id}"),
        WindowClosed(session.id, returncode=0),
        MainCommandEntered(f"/send {session.id} late"),
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
