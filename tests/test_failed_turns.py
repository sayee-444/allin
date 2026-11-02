"""What goes back to the input when a Turn fails: with its Window closed (#15), and with the
Prompts its Session had queued behind it (#16)."""

from pathlib import Path

import pytest

from allin.core.errors import TurnCancelled
from allin.ipc.messages import InputRestore
from allin.main.chat_rebuild import FAILED_BEFORE, rebuild_chat, returned_prompts
from allin.main.events import InputReceived, MainCommandEntered, TurnEnded, WindowClosed
from allin.main.log import LogEntry
from tests.llm_fakes import DroppedStream, FakeLlm, answer, full_reply
from tests.test_dispatcher import SETTINGS, MainHarness, restored, server_ready


@pytest.fixture
def main(tmp_path: Path) -> MainHarness:
    return MainHarness(tmp_path)


def failing_twice() -> FakeLlm:
    """A Server that falls twice in the same Step: its Turn fails (ADR-0023)."""
    return FakeLlm(DroppedStream([answer("half")]), DroppedStream([answer("half")]))


def entry(event: str, **fields: object) -> LogEntry:
    return LogEntry.model_validate({"ts": "2026-11-01T00:00:00Z", "event": event, **fields})


def failed(prompt: str) -> LogEntry:
    return entry("turn_failed", prompt=prompt, error="llm_unavailable", reasoning="", answer="")


@pytest.mark.parametrize(
    ("log", "texts"),
    [
        ([entry("prompt", text="a1"), failed("a1")], ["a1"]),
        (
            [
                entry("prompt", text="a1"),
                entry("prompt", text="a2"),
                failed("a1"),
                entry("prompts_dropped", reason=FAILED_BEFORE, texts=["a2"]),
            ],
            ["a1", "a2"],
        ),
        ([entry("prompt", text="a1"), failed("a1"), entry("prompt", text="a1")], []),
        ([failed("a1"), entry("prompt_returned")], []),
        (
            [
                entry("prompt", text="a1"),
                entry(
                    "turn_cancelled", error="turn_cancelled", reasoning="", answer="", prompt="a1"
                ),
            ],
            [],
        ),
        ([failed("a1"), entry("prompts_dropped", reason="window closed", texts=["a2"])], ["a1"]),
        ([entry("turn_failed", error="main stopped", reasoning="", answer="")], []),
        (
            [
                failed("a1"),
                entry(
                    "prompts_dropped",
                    reason=FAILED_BEFORE,
                    texts=["keep names", "a2"],
                    kinds=["compact", "prompt"],
                ),
            ],
            ["a1", "a2"],
        ),
    ],
)
def test_what_goes_back_to_the_input_is_read_from_the_log(log: list[LogEntry], texts: list[str]):
    assert returned_prompts(log) == texts


def test_with_its_window_closed_a_failed_turns_text_goes_back_to_the_input_on_open(
    main: MainHarness,
):
    session = main.create_session()
    main.llm = failing_twice()
    main.run(MainCommandEntered(f"/send {session.id} hola"), server_ready(main.server), settle=True)
    assert main.opened == []
    main.run(MainCommandEntered(f"/open {session.id}"), settle=True)
    [window] = main.windows(session.id)
    assert restored(window) == ["hola"]
    assert any(e.event == "prompt_returned" for e in main.session_log(session))


def test_given_back_once_it_does_not_come_back_on_the_next_open(main: MainHarness):
    session = main.create_session()
    main.llm = failing_twice()
    main.run(
        MainCommandEntered(f"/send {session.id} hola"),
        server_ready(main.server),
        MainCommandEntered(f"/open {session.id}"),
        WindowClosed(session.id, returncode=0),
        settle=True,
    )
    main.run(MainCommandEntered(f"/open {session.id}"), settle=True)
    [_, again] = main.windows(session.id)
    assert restored(again) == ["hola"], "as the Draft kept it, once"
    assert sum(e.event == "prompt_returned" for e in main.session_log(session)) == 1


def test_sent_again_it_does_not_come_back_on_the_next_open(main: MainHarness):
    session = main.create_session()
    main.llm = FakeLlm(
        DroppedStream([answer("half")]), DroppedStream([answer("half")]), full_reply("", "ok")
    )
    main.run(
        MainCommandEntered(f"/send {session.id} hola"),
        server_ready(main.server),
        MainCommandEntered(f"/send {session.id} hola"),
        settle=True,
    )
    main.run(MainCommandEntered(f"/open {session.id}"), settle=True)
    [window] = main.windows(session.id)
    assert InputRestore(text="hola") not in window.sent


def test_a_cancelled_turn_gives_nothing_back_on_reopen(main: MainHarness):
    session = main.create_session()
    main.hold_turns = True
    main.run(
        MainCommandEntered(f"/open {session.id}"),
        InputReceived(session.id, "hola"),
        WindowClosed(session.id, returncode=0),
        TurnEnded(session.id, error=TurnCancelled(reason="window closed")),
        settle=True,
    )
    main.hold_turns = False
    main.run(MainCommandEntered(f"/open {session.id}"), settle=True)
    [_, again] = main.windows(session.id)
    assert InputRestore(text="hola") not in again.sent


def test_a_failed_turn_takes_its_sessions_queued_prompts_back_to_the_input_in_order(
    main: MainHarness,
):
    a, b = main.create_session(), main.create_session()
    main.server_up = False  # all three wait, so a2 is queued behind a1
    main.llm = FakeLlm(
        DroppedStream([answer("half")]),
        DroppedStream([answer("half")]),
        full_reply("", "b answered"),
    )
    main.run(
        MainCommandEntered(f"/open {a.id}"),
        MainCommandEntered(f"/send {a.id} a1"),
        MainCommandEntered(f"/send {a.id} a2"),
        server_ready(main.server),
        server_ready(main.server),  # back after the first fall
        MainCommandEntered(f"/send {b.id} b1"),  # once a's Turn failed
        server_ready(main.server),
        settle=True,
    )
    [window] = main.windows(a.id)
    assert restored(window) == ["a1", "a2"]
    conversation_a = main.sessions.load_conversation(a)
    assert conversation_a is not None and conversation_a.turns == []
    conversation_b = main.sessions.load_conversation(b)
    assert conversation_b is not None and [t.prompt for t in conversation_b.turns] == ["b1"]
    [dropped] = [e for e in main.session_log(a) if e.event == "prompts_dropped"]
    assert dropped.fields == {"reason": FAILED_BEFORE, "texts": ["a2"], "kinds": ["prompt"]}


def test_the_dropped_prompts_show_in_the_rebuilt_chat(main: MainHarness):
    a = main.create_session()
    main.server_up = False
    main.llm = failing_twice()
    main.run(
        MainCommandEntered(f"/send {a.id} a1"),
        MainCommandEntered(f"/send {a.id} a2"),
        server_ready(main.server),
        server_ready(main.server),
        settle=True,
    )
    log = main.session_log(a)
    conversation = main.sessions.load_conversation(a)
    assert conversation is not None
    shown = [message.text for message in rebuild_chat(conversation, log, SETTINGS.session)]
    assert any("1 queued prompt" in text and FAILED_BEFORE in text for text in shown), shown
    main.run(MainCommandEntered(f"/open {a.id}"), settle=True)
    assert restored(main.windows(a.id)[0]) == ["a1", "a2"]


def test_a_compaction_queued_behind_a_failed_turn_leaves_but_never_goes_to_the_input(
    main: MainHarness,
):
    a = main.create_session()
    main.server_up = False
    main.llm = failing_twice()
    main.run(
        MainCommandEntered(f"/open {a.id}"),
        MainCommandEntered(f"/send {a.id} a1"),
        MainCommandEntered(f"/session compact {a.id} keep names"),
        server_ready(main.server),
        server_ready(main.server),
        settle=True,
    )
    assert restored(main.windows(a.id)[0]) == ["a1"]
    [dropped] = [e for e in main.session_log(a) if e.event == "prompts_dropped"]
    assert dropped.fields["kinds"] == ["compact"]
    assert returned_prompts(main.session_log(a)) == []
