"""Admission to the pool and turns between Mains (#29, ADR-0004, ADR-0022), through Main's
dispatcher with a fake Server. The other Main is the test itself, writing the Server's Wait
list and holding its slot locks as a real one would."""

import os
from pathlib import Path

import pytest

from allin.core.ids import MainId, SessionId
from allin.core.settings import Sampling
from allin.core.slot_locks import SlotLock, SlotLocks
from allin.core.wait_list import Reservation, Waiter, WaitList
from allin.ipc.messages import BoxShow, ChatAppend, InputRestore, PoolShared, TurnState
from allin.main.events import (
    InputReceived,
    MainCommandEntered,
    ServerChanged,
    ServerLoading,
    ServerReady,
    SlotFreed,
    TurnEnded,
    WindowConnected,
)
from allin.main.sessions import Session
from allin.main.slots import ServerProps
from tests.llm_fakes import FakeLlm, answer, completed
from tests.test_dispatcher import KEY, MainHarness, ended, server, turn_lines

OTHER = MainId("other")


@pytest.fixture
def main(tmp_path: Path) -> MainHarness:
    return MainHarness(tmp_path)


CAP = Sampling(max_output_tokens=1_000)  # so each Step needs its input plus 1 000


def session_needing(main: MainHarness, tokens: int) -> Session:
    """A Session whose next Step needs ``tokens``: its measured input plus its Output cap."""
    session = main.create_session(sampling=CAP)
    main.servers.input_tokens[session.id] = tokens - 1_000
    return session


def started(chat: list[str]) -> list[str]:
    return [line.split("]")[0][1:] for line in turn_lines(chat) if line.endswith("started")]


def waiting(main: MainHarness, session: Session) -> list[str | None]:
    """What the Session's border said, each time it changed, of why it waits."""
    [window] = main.windows(session.id)
    said: list[str | None] = []
    for message in window.sent:
        if isinstance(message, TurnState) and (not said or said[-1] != message.waiting):
            said.append(message.waiting)
    return said


def other_main_waits(main: MainHarness, session: str, since: float, need: int = 100) -> None:
    WaitList(main.slot_locks_dir, KEY).publish(
        OTHER,
        os.getpid(),
        [
            Waiter(
                main=OTHER,
                pid=os.getpid(),
                session=SessionId(session),
                kind="prompt",
                since=since,
                need=need,
                firm_at=since + 120,
            )
        ],
    )


def other_main_reserves(main: MainHarness, session: str, tokens: int) -> None:
    reservation = Reservation(
        main=OTHER, pid=os.getpid(), session=SessionId(session), tokens=tokens
    )
    WaitList(main.slot_locks_dir, KEY).reserve(reservation)


def other_main_releases(main: MainHarness, session: str) -> None:
    WaitList(main.slot_locks_dir, KEY).release(OTHER, os.getpid(), SessionId(session))


def open_all(*sessions: Session) -> list[MainCommandEntered]:
    return [MainCommandEntered(f"/open {session.id}") for session in sessions]


def test_two_steps_that_do_not_fit_together_in_the_pool_never_run_at_once(main: MainHarness):
    main.server = server(slots=2, context=128_000)
    big, mid, small = (session_needing(main, n) for n in (80_000, 50_000, 10_000))
    main.hold_turns = True
    chat = main.run(
        *open_all(big, mid, small),
        InputReceived(big.id, "b"),
        InputReceived(mid.id, "m"),
        InputReceived(small.id, "s"),
        settle=True,
    )
    assert started(chat) == [big.id, small.id]  # 80k + 10k fit in 128k; 80k + 50k do not
    assert waiting(main, mid)[-1] == "waiting for room in the pool"
    windows = {s.id: main.windows(s.id)[0] for s in (big, small)}
    assert PoolShared(shared=True) in windows[big.id].sent  # the border says "128k shared"


def test_once_the_big_step_ends_the_one_that_waited_for_room_starts(main: MainHarness):
    main.server = server(slots=2, context=128_000)
    big, mid = session_needing(main, 80_000), session_needing(main, 50_000)
    main.hold_turns = True
    chat = main.run(InputReceived(big.id, "b"), InputReceived(mid.id, "m"), ended(big), settle=True)
    assert started(chat) == [big.id, mid.id]
    assert WaitList(main.slot_locks_dir, KEY).look().reserved == ()  # all given back


def test_without_a_shared_pool_only_the_slots_count(main: MainHarness):
    main.server_up = False
    props = ServerProps(total_slots=2, slot_context=64_000, model_path="m.gguf")
    big, mid = session_needing(main, 60_000), session_needing(main, 50_000)
    main.hold_turns = True
    chat = main.run(
        ServerChanged(KEY, ServerReady(props, shared_pool=False)),
        InputReceived(big.id, "b"),
        InputReceived(mid.id, "m"),
        settle=True,
    )
    assert started(chat) == [big.id, mid.id]  # each slot has its own 64k


def test_a_big_prompt_stops_being_overtaken_once_it_waited_its_patience(main: MainHarness):
    main.server = server(slots=3, context=128_000)
    running, big = session_needing(main, 80_000), session_needing(main, 60_000)
    small, late = session_needing(main, 10_000), session_needing(main, 10_000)
    main.hold_turns = True

    def patience_passes() -> None:
        main.now = 121.0  # admission.patience is 120 s by default

    chat = main.run(
        *open_all(late),
        InputReceived(running.id, "r"),
        InputReceived(big.id, "b"),
        InputReceived(small.id, "s"),  # fits, and overtakes the big one
        patience_passes,
        InputReceived(late.id, "l"),  # fits too, but the big one has waited enough
        ended(running),
        settle=True,
    )
    assert started(chat) == [running.id, small.id, big.id, late.id]
    assert "waiting for room in the pool" in waiting(main, late)


def test_a_prompt_bigger_than_the_pool_fails_without_waiting(main: MainHarness):
    main.server = server(slots=2, context=128_000)
    session = session_needing(main, 130_000)
    main.hold_turns = True
    chat = main.run(*open_all(session), InputReceived(session.id, "too much"), settle=True)
    assert started(chat) == []
    [window] = main.windows(session.id)
    errors = [m.text for m in window.sent if isinstance(m, ChatAppend) and m.role == "error"]
    assert errors == ["prompt_too_big: need=130000, limit=128000"]
    assert InputRestore(text="too much") in window.sent  # it is in no Conversation
    states = [m for m in window.sent if isinstance(m, TurnState)]
    assert states[-1] == TurnState(active=False)  # Enter works again


def context_notices(main: MainHarness, session: Session) -> list[str]:
    [window] = main.windows(session.id)
    return [m.text for m in window.sent if isinstance(m, BoxShow) and m.title == "Context"]


@pytest.mark.parametrize(
    ("slots", "shared", "what"),
    [(1, True, "the Context"), (2, False, "the Context"), (2, True, "the shared pool")],
)
def test_the_context_notice_shows_once_when_it_crosses_80_percent(
    main: MainHarness, slots: int, shared: bool, what: str
):
    main.server_up = False
    props = ServerProps(total_slots=slots, slot_context=10_000, model_path="m.gguf")
    session = main.create_session()
    main.llm = FakeLlm(
        [answer("a"), completed("", "a", tokens=(6_000, 1_000))],  # 70 %
        [answer("b"), completed("", "b", tokens=(7_500, 1_000))],  # 85 %: crossed
        [answer("c"), completed("", "c", tokens=(8_600, 400))],  # 90 %: still above
    )
    main.run(
        ServerChanged(KEY, ServerReady(props, shared_pool=shared)),
        *open_all(session),
        WindowConnected(session.id),
        InputReceived(session.id, "1"),
        InputReceived(session.id, "2"),
        InputReceived(session.id, "3"),
        settle=True,
    )
    assert context_notices(main, session) == [f"{what} is at 85% of 10000 tokens"]


def test_the_context_notice_can_be_turned_off(main: MainHarness):
    main.server = server(context=10_000)
    session = main.create_session(notices={"context": {"enabled": False}})
    main.llm = FakeLlm([answer("a"), completed("", "a", tokens=(8_500, 500))])
    main.run(*open_all(session), InputReceived(session.id, "1"), settle=True)
    assert context_notices(main, session) == []


def test_a_freed_slot_goes_to_the_other_main_that_waited_first(main: MainHarness):
    a, b = main.create_session(), main.create_session()
    main.hold_turns = True
    holder: list[SlotLock] = []

    def other_main_takes_its_turn() -> None:
        lock = SlotLocks(main.slot_locks_dir, KEY).take(1)
        assert lock is not None  # Main left it free for the one that waited first
        other_main_reserves(main, "x", 100)
        holder.append(lock)

    def other_main_ends_its_step() -> None:
        other_main_releases(main, "x")
        holder.pop().release()

    main.now = 10.0
    chat = main.run(
        *open_all(b),
        InputReceived(a.id, "a1"),
        lambda: other_main_waits(main, "x", since=5.0),  # it waits since before b did
        InputReceived(b.id, "b1"),
        ended(a),  # Main does not take the slot again for b
        other_main_takes_its_turn,
        other_main_ends_its_step,
        SlotFreed(KEY),
        settle=True,
    )
    assert started(chat) == [a.id, b.id]
    assert "queued (2nd)" in waiting(main, b)


def test_a_main_whose_prompt_waited_first_keeps_its_turn(main: MainHarness):
    a, b = main.create_session(), main.create_session()
    main.hold_turns = True
    main.now = 10.0
    chat = main.run(
        InputReceived(a.id, "a1"),
        InputReceived(b.id, "b1"),
        lambda: other_main_waits(main, "x", since=50.0),  # it came after b
        ended(a),
        settle=True,
    )
    assert started(chat) == [a.id, b.id]


def test_what_another_main_reserves_counts_for_the_admission(main: MainHarness):
    main.server = server(slots=2, context=128_000)
    session = session_needing(main, 50_000)
    main.hold_turns = True
    chat = main.run(
        *open_all(session),
        lambda: other_main_reserves(main, "x", 100_000),
        InputReceived(session.id, "s"),
        lambda: other_main_releases(main, "x"),
        SlotFreed(KEY),
        settle=True,
    )
    assert started(chat) == [session.id]
    assert waiting(main, session) == [None, "waiting for room in the pool", None]


def test_the_border_says_the_server_is_loading(main: MainHarness):
    main.server_up = False
    session = main.create_session()
    main.run(
        ServerChanged(KEY, ServerLoading()),
        *open_all(session),
        InputReceived(session.id, "hola"),
        settle=True,
    )
    assert waiting(main, session)[-1] == "Server loading"


def test_a_turn_that_ends_gives_back_what_it_reserved(main: MainHarness):
    session = session_needing(main, 5_000)
    main.hold_turns = True
    main.run(
        InputReceived(session.id, "s"),
        lambda: _reserved_now(main, [5_000]),
        TurnEnded(session.id, steps=None),
        settle=True,
    )
    assert WaitList(main.slot_locks_dir, KEY).look().reserved == ()


def _reserved_now(main: MainHarness, tokens: list[int]) -> None:
    assert [r.tokens for r in WaitList(main.slot_locks_dir, KEY).look().reserved] == tokens
