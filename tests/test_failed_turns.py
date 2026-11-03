"""What goes back to the input when a Turn fails: with its Window closed (#15), and with the
Prompts its Session had queued behind it (#16)."""

from collections.abc import Sequence
from pathlib import Path

import pytest

from allin.core.errors import TurnCancelled, TurnCrashed
from allin.core.origin import HUMAN, from_session
from allin.ipc.messages import InputRestore
from allin.main.chat_rebuild import FAILED_BEFORE, rebuild_chat, returned_prompts
from allin.main.events import (
    InputReceived,
    MainCommandEntered,
    ToolsEnded,
    ToolsStarted,
    TurnEnded,
    WindowClosed,
)
from allin.main.log import LogEntry
from tests.llm_fakes import DroppedStream, FakeLlm, answer, full_reply
from tests.test_dispatcher import SETTINGS, MainHarness, restored, server_ready
from tests.test_write_during_turn import CALLED, RETURNED


@pytest.fixture
def main(tmp_path: Path) -> MainHarness:
    return MainHarness(tmp_path)


def failing_twice() -> FakeLlm:
    """A Server that falls twice in the same Step: its Turn fails (ADR-0023)."""
    return FakeLlm(DroppedStream([answer("half")]), DroppedStream([answer("half")]))


def entry(event: str, **fields: object) -> LogEntry:
    return LogEntry.model_validate({"ts": "2026-11-01T00:00:00Z", "event": event, **fields})


SENT = ["human", "session:ab12"]  # what another Session sent with ``@send``


def failed(prompt: str, **fields: object) -> LogEntry:
    return entry(
        "turn_failed", prompt=prompt, error="llm_unavailable", reasoning="", answer="", **fields
    )


def too_big(typed: object, origin: Sequence[str] = ("human",)) -> LogEntry:
    """A Prompt that did not fit even in the empty slot (#174)."""
    return entry("prompt_too_big", typed=typed, origin=list(origin))


def turn_finished() -> LogEntry:
    return entry("turn_finished", turn=0, cut=None)


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
        (  # as typed, with its Command prompts (#166)
            [
                entry(
                    "turn_failed",
                    error="llm_unavailable",
                    reasoning="",
                    answer="",
                    prompt="a1",
                    typed="a1 @top_k=5",
                ),
                entry(
                    "prompts_dropped",
                    reason=FAILED_BEFORE,
                    texts=["keep names", "a2", "a3"],
                    kinds=["compact", "prompt", "prompt"],
                    typed=["keep names", "a2 @top_k=3", "a3"],
                ),
            ],
            ["a1 @top_k=5", "a2 @top_k=3", "a3"],
        ),
        # Only a human's (#171); a Log from before it names no Origin, and was a human's.
        ([failed("a1", origin=SENT)], []),
        ([failed("a1", origin=["human"])], ["a1"]),
        ([failed("a1", origin=["not a link"])], []),  # unreadable: never taken for a human's
        ([failed("a1"), failed("a2", origin=SENT)], []),
        (
            [
                failed("a1", origin=SENT),
                entry(
                    "prompts_dropped",
                    reason=FAILED_BEFORE,
                    texts=["a2", "a3", "a4"],
                    kinds=["prompt", "prompt", "prompt"],
                    origins=[SENT, ["human"], SENT],
                ),
            ],
            ["a3"],
        ),
        (
            [
                failed("a1", origin=SENT),
                entry("prompts_dropped", reason=FAILED_BEFORE, texts=["a2"]),
                entry("prompt_returned", texts=["a2"]),
            ],
            [],
        ),
        # Its Textos intercalados, after its Prompt and ahead of the queue's (#174); a human's
        # whatever the Turn's Origin.
        ([failed("a1", joined=["j1", "j2"])], ["a1", "j1", "j2"]),
        (
            [
                failed("a1", joined=["j1"]),
                entry("prompts_dropped", reason=FAILED_BEFORE, texts=["a2"]),
            ],
            ["a1", "j1", "a2"],
        ),
        ([failed("a1", origin=SENT, joined=["j1"])], ["j1"]),
        ([failed("a1", joined=["j1"]), entry("prompt_returned", texts=["a1", "j1"])], []),
        ([failed("a1", joined=["j1"]), entry("prompt", text="a2")], []),
        ([failed("a1", joined=["j1", 7])], ["a1", "j1"]),  # never what is not a text
        # A human's Prompt that did not fit, as it was typed (#174); another Origin's never.
        ([entry("prompt", text="b1"), too_big("b1 @top_k=5")], ["b1 @top_k=5"]),
        ([too_big("b1", origin=SENT)], []),
        ([too_big("b1", origin=["not a link"])], []),
        ([too_big("b1"), entry("prompt_returned", texts=["b1"])], []),
        ([too_big("b1"), entry("prompt", text="b2")], []),
        ([too_big("b1"), entry("prompt", text="b2", origin=["human"])], []),
        # Sent by another Session, it says nothing of what the human did with theirs.
        ([too_big("b1"), entry("prompt", text="b2", origin=SENT)], ["b1"]),
        ([too_big(7)], []),
        # One queued behind it may still run and end: what did not fit stays (#174).
        ([too_big("b1"), entry("prompt_started", text="b2"), turn_finished()], ["b1"]),
        ([too_big("b1"), entry("prompt_started", text="b2"), failed("b2")], ["b2", "b1"]),
        ([too_big("b1"), entry("rewound", turn=0)], ["b1"]),  # it was in no Conversation
        (  # each one given back with the Window open goes ahead of those before it
            [too_big("b1"), entry("prompt_started", text="b2"), too_big("b2")],
            ["b2", "b1"],
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


def test_a_cancelled_turn_gives_its_prompt_back_on_reopen_once(main: MainHarness):
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
    # From its Draft (#156), and not a second time as a failed Turn's (#15).
    assert [m for m in again.sent if isinstance(m, InputRestore)] == [InputRestore(text="hola")]


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
    assert dropped.fields == {
        "reason": FAILED_BEFORE,
        "texts": ["a2"],
        "kinds": ["prompt"],
        "origins": [["human"]],
    }


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


def test_a_failed_turn_gives_back_what_was_typed_with_its_window_open(main: MainHarness):
    a = main.create_session()
    main.server_up = False  # both wait, so a2 is queued behind a1
    main.llm = failing_twice()
    main.run(
        MainCommandEntered(f"/open {a.id}"),
        InputReceived(a.id, "a1 @top_k=5"),
        InputReceived(a.id, "a2 @top_k=3"),
        server_ready(main.server),
        server_ready(main.server),
        settle=True,
    )
    # As typed (#166), while the Log keeps what the LLM read and the typed text apart.
    assert restored(main.windows(a.id)[0]) == ["a1 @top_k=5", "a2 @top_k=3"]
    log = main.session_log(a)
    [failed_turn] = [e for e in log if e.event == "turn_failed"]
    assert (failed_turn.fields["prompt"], failed_turn.fields["typed"]) == ("a1", "a1 @top_k=5")
    [dropped] = [e for e in log if e.event == "prompts_dropped"]
    assert (dropped.fields["texts"], dropped.fields["typed"]) == (["a2"], ["a2 @top_k=3"])


def test_a_failed_turn_gives_back_what_was_typed_as_its_window_opens(main: MainHarness):
    a = main.create_session()
    main.server_up = False
    main.llm = failing_twice()
    main.run(
        MainCommandEntered(f"/send {a.id} a1 @top_k=5"),
        MainCommandEntered(f"/send {a.id} a2 @top_k=3"),
        MainCommandEntered(f"/send {a.id} a3"),
        server_ready(main.server),
        server_ready(main.server),
        settle=True,
    )
    main.run(MainCommandEntered(f"/open {a.id}"), settle=True)
    assert restored(main.windows(a.id)[0]) == ["a1 @top_k=5", "a2 @top_k=3", "a3"]


def test_a_failed_turn_gives_back_only_the_human_prompts_with_its_window_open(main: MainHarness):
    a, sender = main.create_session(), main.create_session()
    sent = from_session(HUMAN, sender.id)
    main.server_up = False  # all wait, so a2 and a3 are queued behind a1
    main.llm = failing_twice()
    main.run(
        MainCommandEntered(f"/open {a.id}"),
        MainCommandEntered(f"/send {a.id} a1", origin=sent),
        MainCommandEntered(f"/send {a.id} a2 @top_k=3"),
        MainCommandEntered(f"/send {a.id} a3", origin=sent),
        server_ready(main.server),
        server_ready(main.server),
        settle=True,
    )
    # The others failed towards whoever sent them (#171).
    assert restored(main.windows(a.id)[0]) == ["a2 @top_k=3"]
    log = main.session_log(a)
    [failed_turn] = [e for e in log if e.event == "turn_failed"]
    assert failed_turn.fields["origin"] == sent.labels
    [dropped] = [e for e in log if e.event == "prompts_dropped"]
    assert dropped.fields["origins"] == [["human"], sent.labels]
    main.run(WindowClosed(a.id, returncode=0), MainCommandEntered(f"/open {a.id}"), settle=True)
    assert restored(main.windows(a.id)[1]) == [], "its Draft keeps it, not the Log again"


def test_a_failed_turn_gives_back_only_the_human_prompts_as_its_window_opens(main: MainHarness):
    a, sender = main.create_session(), main.create_session()
    sent = from_session(HUMAN, sender.id)
    main.server_up = False
    main.llm = failing_twice()
    main.run(
        MainCommandEntered(f"/send {a.id} a1", origin=sent),
        MainCommandEntered(f"/send {a.id} a2 @top_k=3"),
        MainCommandEntered(f"/send {a.id} a3", origin=sent),
        server_ready(main.server),
        server_ready(main.server),
        settle=True,
    )
    main.run(MainCommandEntered(f"/open {a.id}"), settle=True)
    assert restored(main.windows(a.id)[0]) == ["a2 @top_k=3"]


def test_a_failed_turn_of_another_origin_alone_gives_nothing_back(main: MainHarness):
    a, sender = main.create_session(), main.create_session()
    main.llm = failing_twice()
    main.run(
        MainCommandEntered(f"/open {a.id}"),
        MainCommandEntered(f"/send {a.id} trabajo", origin=from_session(HUMAN, sender.id)),
        server_ready(main.server),
        settle=True,
    )
    assert restored(main.windows(a.id)[0]) == []
    assert all(e.event != "prompt_returned" for e in main.session_log(a))
    main.run(WindowClosed(a.id, returncode=0), MainCommandEntered(f"/open {a.id}"), settle=True)
    assert restored(main.windows(a.id)[1]) == []


def test_with_its_window_closed_a_failed_turns_joined_texts_go_back_on_open_once(
    main: MainHarness,
):
    a, sender = main.create_session(), main.create_session()
    main.hold_turns = True
    main.run(
        MainCommandEntered(f"/open {a.id}"),
        MainCommandEntered(f"/send {a.id} trabajo", origin=from_session(HUMAN, sender.id)),
        ToolsStarted(a.id, ("nap",), CALLED),
        InputReceived(a.id, "y luego esto"),
        ToolsEnded(a.id, RETURNED, 10),
        WindowClosed(a.id, returncode=0),  # a Turn of another Origin goes on (ADR-0013)
        MainCommandEntered(f"/send {a.id} otra @top_k=3"),  # queued behind it
        TurnEnded(a.id, error=TurnCrashed(reason="boom")),
        settle=True,
    )
    main.run(MainCommandEntered(f"/open {a.id}"), settle=True)
    # After its Prompt, which is not a human's, and ahead of the queue's (#174).
    assert restored(main.windows(a.id)[1]) == ["y luego esto", "otra @top_k=3"]
    main.run(WindowClosed(a.id, returncode=0), MainCommandEntered(f"/open {a.id}"), settle=True)
    again = ["y luego esto", "otra @top_k=3"]
    assert restored(main.windows(a.id)[2]) == again, "as the Draft kept them, once"
    assert sum(e.event == "prompt_returned" for e in main.session_log(a)) == 1


def test_with_its_window_open_a_failed_turns_joined_texts_go_back_once(main: MainHarness):
    a = main.create_session()
    main.hold_turns = True
    main.run(
        MainCommandEntered(f"/open {a.id}"),
        InputReceived(a.id, "duerme"),
        ToolsStarted(a.id, ("nap",), CALLED),
        InputReceived(a.id, "y luego esto"),
        ToolsEnded(a.id, RETURNED, 10),
        InputReceived(a.id, "y aquello"),  # queued
        TurnEnded(a.id, error=TurnCrashed(reason="boom")),
        settle=True,
    )
    assert restored(main.windows(a.id)[0]) == ["duerme", "y luego esto", "y aquello"]
    main.run(WindowClosed(a.id, returncode=0), MainCommandEntered(f"/open {a.id}"), settle=True)
    assert restored(main.windows(a.id)[1]) == [], "its Draft keeps them, not the Log again"
