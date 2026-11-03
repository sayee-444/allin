"""A Continuation whose estimate passes its Server's pool (#188, ADR-0004): Main counts what
its tools gave back a token per character, so its next Step is measured on the Server first;
one that fits starts, and one that does not even fit in the empty pool fails instead of waiting
for room for ever."""

from pathlib import Path

import pytest
from openai.types.responses import (
    ResponseCompletedEvent,
    ResponseStreamEvent,
    ResponseUsage,
)

from allin.core.errors import TurnCancelled
from allin.core.ids import ServerKey, SessionId
from allin.core.settings import Sampling
from allin.core.wait_list import WaitList
from allin.ipc.messages import ChatAppend, InputRestore, TurnState
from allin.main.conversation import Item, Step
from allin.main.events import (
    InputMeasured,
    InputReceived,
    MainCommandEntered,
    MainEvent,
    ServerChanged,
    ServerDown,
    ServerReady,
    ServerRestarting,
    ServerState,
    ToolsEnded,
    ToolsStarted,
    TurnEnded,
    WindowClosed,
)
from allin.main.sessions import Session
from tests.llm_fakes import FakeLlm, tool_call
from tests.test_compaction_turns import conversation, events, reply, words
from tests.test_dispatcher import KEY, MainHarness, server
from tests.test_pool_admission import other_main_releases, other_main_reserves, waiting

CAP = Sampling(max_output_tokens=1_000)
POOL = 10_000  # two slots sharing it
CALL: Item = {"type": "function_call", "call_id": "c1", "name": "read", "arguments": "{}"}


@pytest.fixture
def main(tmp_path: Path) -> MainHarness:
    harness = MainHarness(tmp_path)
    harness.servers.tokens_of = words
    harness.server = server(slots=2, context=POOL)
    harness.hold_turns = True
    return harness


def tool_gave(session: Session, count: int) -> list[MainEvent]:
    """Its first Step called a tool that gave back ``count`` words of eight characters: Main
    estimates them at eight times what the Server counts."""
    output = " ".join(["palabra"] * count)
    step = Step(output=[CALL])
    result = {"type": "function_call_output", "call_id": "c1", "output": output}
    done = step.model_copy(update={"results": [result]})
    return [ToolsStarted(session.id, ("read",), step), ToolsEnded(session.id, done, len(output))]


def reserved(main: MainHarness) -> list[int]:
    return [r.tokens for r in WaitList(main.slot_locks_dir, KEY).look().reserved]


def next_input(main: MainHarness, count: int) -> list[Item]:
    """What the Turn's next Step reads: its first Step's input, then its call and the output."""
    output = " ".join(["palabra"] * count)
    result = {"type": "function_call_output", "call_id": "c1", "output": output}
    return [*main.held[0].request.input, CALL, result]


def errors(main: MainHarness, session: Session) -> list[str]:
    [window] = main.windows(session.id)
    return [m.text for m in window.sent if isinstance(m, ChatAppend) and m.role == "error"]


def test_a_continuation_estimated_past_the_pool_that_measures_less_starts_its_step(
    main: MainHarness,
):
    session = main.create_session(sampling=CAP)
    main.run(
        MainCommandEntered(f"/open {session.id}"),
        InputReceived(session.id, "lee"),
        *tool_gave(session, 2_000),  # 16 000 characters: estimated past the 10 000 of the pool
        lambda: _started_measured(main, 2_000),
        settle=True,
    )
    assert main.servers.measured == [session.id, session.id]
    assert len(main.servers.taken) == 2
    assert "waiting for room in the pool" not in waiting(main, session)
    assert "continuation_measured" in events(main, session)


def _started_measured(main: MainHarness, count: int) -> None:
    """Its next Step started, reserving what the Server measured of its input, plus its tools'
    schemas (none) and its Output cap."""
    measured = words(next_input(main, count)) + 1_000
    assert measured < POOL
    assert reserved(main) == [measured]


def test_a_continuation_that_does_not_fit_in_the_empty_pool_fails_with_a_clear_error(
    main: MainHarness,
):
    session = main.create_session(sampling=CAP)
    main.run(
        MainCommandEntered(f"/open {session.id}"),
        InputReceived(session.id, "lee"),
        *tool_gave(session, 12_000),
        lambda: _executor_stops(main, session),
        settle=True,
    )
    need = words(next_input(main, 12_000)) + 1_000
    assert errors(main, session) == [f"prompt_too_big: need={need}, limit={POOL}"]
    [window] = main.windows(session.id)
    assert InputRestore(text="lee") in window.sent  # it is in no Conversation
    assert [m for m in window.sent if isinstance(m, TurnState)][-1] == TurnState(active=False)
    assert "turn_failed" in events(main, session)
    assert "waiting for room in the pool" not in waiting(main, session)
    assert reserved(main) == []
    assert len(main.servers.taken) == 1, "its next Step never took a slot"


def _executor_stops(main: MainHarness, session: Session) -> None:
    """The held Turn's executor sees it cancelled, as its tools' wait does."""
    reason = main.held[0].cancellation.reason
    assert reason is not None, "Main stopped it"
    assert main.servers.inbox is not None
    main.servers.inbox.put(TurnEnded(session.id, error=TurnCancelled(reason=reason)))


def test_a_continuation_the_server_cannot_measure_reserves_the_whole_pool(main: MainHarness):
    session = main.create_session(sampling=CAP)
    first = True

    def unmeasurable(items: list[Item]) -> int | None:
        nonlocal first
        if first:
            first = False
            return words(items)
        return None  # e.g. its Server fell as it measured

    main.servers.tokens_of = unmeasurable
    main.run(
        MainCommandEntered(f"/open {session.id}"),
        InputReceived(session.id, "lee"),
        lambda: other_main_reserves(main, "x", 500),
        *tool_gave(session, 2_000),
        lambda: _reserved_now(main, [500]),  # it waits for the pool to be empty
        lambda: other_main_releases(main, "x"),
        ServerChanged(KEY, ServerReady(main.server)),  # Main looks again
        lambda: _reserved_now(main, [POOL]),
        settle=True,
    )
    assert "waiting for room in the pool" in waiting(main, session)
    assert "input_unmeasured" in events(main, session)


def _reserved_now(main: MainHarness, tokens: list[int]) -> None:
    assert reserved(main) == tokens


def test_a_continuation_measured_to_fit_fails_once_its_pool_shrinks_below_it(
    main: MainHarness,
):
    session = main.create_session(sampling=CAP)
    smaller = server(slots=2, context=2_000)
    main.run(
        MainCommandEntered(f"/open {session.id}"),
        InputReceived(session.id, "lee"),
        lambda: other_main_reserves(main, "x", 9_000),  # it waits for room meanwhile
        *tool_gave(session, 2_000),
        ServerChanged(KEY, ServerReady(smaller)),
        lambda: _executor_stops(main, session),
        settle=True,
    )
    [error] = errors(main, session)
    assert error.startswith("prompt_too_big:") and error.endswith("limit=2000")
    assert main.servers.measured == [session.id, session.id], "measured once, not again"


def test_shift_enter_while_its_next_step_is_measured_cuts_the_turn_as_ever(
    main: MainHarness,
):
    session = main.create_session(sampling=CAP)
    asked = _continuations_measured_later(main)
    main.run(
        MainCommandEntered(f"/open {session.id}"),
        InputReceived(session.id, "lee"),
        *tool_gave(session, 2_000),
        InputReceived(session.id, "", interrupt=True),
        lambda: _executor_stops(main, session),
        lambda: _measure_arrives(main, session, asked, 2_000),
        settle=True,
    )
    assert [turn.prompt for turn in conversation(main, session).turns] == ["lee"]
    assert conversation(main, session).turns[-1].cut == "interrupted"
    assert errors(main, session) == []
    assert len(main.servers.taken) == 1


def _continuations_measured_later(main: MainHarness) -> list[int]:
    """The measures a Continuation asks answer only once the test says so: their tickets."""
    asked: list[int] = []
    measure = main.servers.measure

    def later(server: ServerKey, session_id: SessionId, ticket: int, items: list[Item]) -> None:
        if main.held:  # a Turn runs: what it asks is its Continuation's
            asked.append(ticket)
            return
        measure(server, session_id, ticket, items)

    main.servers.measure = later
    return asked


def _measure_arrives(
    main: MainHarness, session: Session, asked: list[int], count: int | None
) -> None:
    """The last measure asked answers: its input as ``count`` words, or unmeasured."""
    assert main.servers.inbox is not None
    tokens = None if count is None else words(next_input(main, count))
    main.servers.inbox.put(InputMeasured(KEY, session.id, asked[-1], tokens))


@pytest.mark.parametrize("gone", [ServerDown(), ServerRestarting()], ids=["falls", "restarts"])
def test_a_continuation_whose_server_is_gone_as_it_is_measured_is_measured_again_once_back(
    main: MainHarness, gone: ServerState
):
    session = main.create_session(sampling=CAP)
    asked = _continuations_measured_later(main)
    main.run(
        MainCommandEntered(f"/open {session.id}"),
        InputReceived(session.id, "lee"),
        *tool_gave(session, 2_000),  # estimated past the pool, truly ~ 4 000
        ServerChanged(KEY, gone),
        lambda: _measure_arrives(main, session, asked, None),  # it could not reach the Server
        lambda: _asked_count(asked, 1),  # nothing to measure on until it is back
        ServerChanged(KEY, ServerReady(main.server)),
        lambda: _asked_count(asked, 2),
        lambda: _measure_arrives(main, session, asked, 2_000),
        lambda: _started_measured(main, 2_000),
        settle=True,
    )
    assert len(main.servers.taken) == 2, "its next Step started"
    assert "prompt_too_big" not in events(main, session), "it was never measured too big"
    assert "continuation_measured" in events(main, session)


def _asked_count(asked: list[int], count: int) -> None:
    assert len(asked) == count


def test_closing_the_window_while_its_next_step_is_measured_cancels_the_turn_as_ever(
    main: MainHarness,
):
    session = main.create_session(sampling=CAP)
    asked = _continuations_measured_later(main)
    main.run(
        MainCommandEntered(f"/open {session.id}"),
        InputReceived(session.id, "lee"),
        *tool_gave(session, 12_000),
        WindowClosed(session.id, returncode=0),
        lambda: _executor_stops(main, session),
        lambda: _measure_arrives(main, session, asked, 12_000),
        settle=True,
    )
    assert main.held[0].cancellation.reason == "window closed"
    assert "turn_cancelled" in events(main, session)
    assert "turn_failed" not in events(main, session)
    assert len(main.servers.taken) == 1


def test_a_continuation_that_does_not_fit_ends_its_executor_and_fails(main: MainHarness):
    """The same, with its executor running: its tools' wait sees Main stop the Turn."""
    main.hold_turns, main.threaded_turns = False, True
    session = main.create_session(sampling=CAP)
    main.llm = FakeLlm(_long_call(), reply("never"))
    # Its next Step reads its long call: what the fake Server counts of it.
    main.servers.tokens_of = lambda items: words(items) + (12_000 if len(items) > 2 else 0)
    main.run(
        MainCommandEntered(f"/open {session.id}"), InputReceived(session.id, "lee"), settle=True
    )
    [error] = errors(main, session)
    assert error.startswith("prompt_too_big:") and error.endswith(f"limit={POOL}")
    assert len(main.llm.requests) == 1, "its next Step never reached the Server"
    assert "turn_failed" in events(main, session)


def _long_call() -> list[ResponseStreamEvent]:
    """A Step that generated 12 000 tokens and ends calling a tool, which this Session lacks."""
    [event] = tool_call("read", "{}")
    assert isinstance(event, ResponseCompletedEvent)
    usage = ResponseUsage.model_construct(input_tokens=5, output_tokens=12_000)
    response = event.response.model_copy(update={"usage": usage})
    return [event.model_copy(update={"response": response})]


def test_a_continuation_estimated_within_the_pool_is_not_measured(main: MainHarness):
    session = main.create_session(sampling=CAP)
    main.run(
        InputReceived(session.id, "lee"),
        *tool_gave(session, 100),
        lambda: _reserved_now(main, [_estimated(main, 100)]),
        settle=True,
    )
    assert main.servers.measured == [session.id]


def _estimated(main: MainHarness, count: int) -> int:
    """Its first Step's need, plus what its tool gave back a token per character."""
    first = words(main.held[0].request.input) + 1_000
    return first + len(" ".join(["palabra"] * count))


def test_a_continuation_estimated_past_the_pool_is_measured_while_a_profile_switch_waits(
    main: MainHarness,
):
    session = main.create_session(sampling=CAP)
    main.profiles.create("tesis", main.profile)
    tools_started, tools_ended = tool_gave(session, 2_000)
    main.run(
        MainCommandEntered(f"/open {session.id}"),
        InputReceived(session.id, "lee"),
        tools_started,
        MainCommandEntered("/profile switch tesis"),  # it waits for the Turn to end (#197)
        tools_ended,
        lambda: _started_measured(main, 2_000),
        TurnEnded(session.id, steps=[Step(output=[])]),
        settle=True,
    )
    assert main.servers.measured == [session.id, session.id]
    assert main.profiles.open(None).name == "tesis"
