"""Restarts, falls and stalled Steps (ADR-0008, ADR-0023), and the limit Notice (ADR-0022)."""

import os
import queue
from collections.abc import Callable
from pathlib import Path

import pytest

from allin.core.errors import TurnCancelled
from allin.core.ids import ServerKey, SupervisorName
from allin.ipc.messages import InputRestore
from allin.main.events import (
    InputReceived,
    MainCommandEntered,
    MainEvent,
    ServerChanged,
    ServerProgress,
    ServerReady,
    ServerRestarting,
    Tick,
    TurnEnded,
    TurnRaw,
)
from allin.main.server_links import ServerLinks
from allin.main.turn_queue import RESTARTING, STALLED
from allin.supervisor.lock import SupervisorLock, lock_path
from allin.supervisor.state import SupervisorState, write_state
from allin.ui.screen import ShowBox
from tests.llm_fakes import FakeLlm, completed
from tests.test_dispatcher import KEY, MainHarness, server, server_ready

GPU = SupervisorName("gpu")


@pytest.fixture
def main(tmp_path: Path) -> MainHarness:
    return MainHarness(tmp_path)


def restarting(now: bool = False) -> ServerChanged:
    return ServerChanged(KEY, ServerRestarting(now=now))


def notices(main: MainHarness) -> list[str]:
    return [u.box.title for u in main.main_screen if isinstance(u, ShowBox)]


def test_a_restart_lets_the_running_step_end_and_holds_new_prompts_until_it_is_back(
    main: MainHarness,
):
    a, b = main.create_session(), main.create_session()
    main.server = server(slots=2)
    main.hold_turns = True
    reasons: list[str | None] = []
    chat = main.run(
        InputReceived(a.id, "a1"),
        restarting(),
        InputReceived(b.id, "b1"),  # waits for the restart
        lambda: reasons.append(main.held[0].cancellation.reason),
        settle=True,
    )
    assert reasons == [None]  # a's Step goes on
    assert [job.session_id for job in main.held] == [a.id]
    assert any("is restarting" in line for line in chat)


def test_once_back_from_a_restart_the_prompts_that_waited_run(main: MainHarness):
    a = main.create_session()
    main.hold_turns = True
    main.run(restarting(), InputReceived(a.id, "a1"), server_ready(main.server), settle=True)
    assert [job.session_id for job in main.held] == [a.id]


def test_restart_now_cuts_the_running_steps_short_and_repeats_them_once_back(
    main: MainHarness,
):
    session = main.create_session()
    main.hold_turns = True
    reasons: list[str | None] = []
    main.run(
        InputReceived(session.id, "hola"),
        restarting(now=True),
        lambda: reasons.append(main.held[0].cancellation.reason),
        TurnEnded(session.id, error=TurnCancelled(reason=RESTARTING)),
        server_ready(main.server),
        settle=True,
    )
    assert reasons == [RESTARTING]
    assert [job.session_id for job in main.held] == [session.id, session.id]  # run again


def test_restarts_never_count_as_falls(main: MainHarness):
    session = main.create_session()
    main.hold_turns = True
    cut = TurnEnded(session.id, error=TurnCancelled(reason=RESTARTING))
    main.run(InputReceived(session.id, "hola"), cut, cut, cut, settle=True)
    assert not any(e.event == "turn_failed" for e in main.session_log(session))
    assert len(main.held) == 4


def test_progress_keeps_a_step_from_stalling(main: MainHarness):
    session = main.create_session()
    main.hold_turns = True
    reasons: list[str | None] = []

    def at(now: float) -> Callable[[], None]:
        return lambda: setattr(main, "now", now)

    main.run(
        InputReceived(session.id, "hola"),
        at(119.0),
        Tick(),
        lambda: reasons.append(main.held[0].cancellation.reason),
        settle=True,
    )
    assert reasons == [None]
    main.held.clear()
    main.run(
        InputReceived(session.id, "again"),
        at(300.0),
        TurnRaw(session.id, {"type": "x"}),  # progress at 300
        at(419.0),
        Tick(),
        lambda: reasons.append(main.held[0].cancellation.reason),
        settle=True,
    )
    assert reasons == [None, None]


def test_a_stalled_step_is_cancelled_its_slot_freed_and_its_turn_failed(main: MainHarness):
    session = main.create_session()
    main.hold_turns = True
    reasons: list[str | None] = []
    main.run(
        MainCommandEntered(f"/open {session.id}"),
        InputReceived(session.id, "hola"),
        lambda: setattr(main, "now", 120.0),
        Tick(),
        lambda: reasons.append(main.held[0].cancellation.reason),
        TurnEnded(session.id, error=TurnCancelled(reason=STALLED)),
        settle=True,
    )
    assert reasons == [STALLED]
    [failed] = [e for e in main.session_log(session) if e.event == "turn_failed"]
    assert "step_stalled" in failed.fields["error"]
    assert any("Step stalled" in title for title in notices(main))
    [window] = main.windows(session.id)
    assert InputRestore(text="hola") in window.sent


def test_a_lowered_slot_limit_names_the_sessions_left_over_it(main: MainHarness):
    big, small = main.create_session(), main.create_session()
    main.llm = FakeLlm(
        [completed("", "x", total_tokens=3000)], [completed("", "y", total_tokens=100)]
    )
    main.server = server(context=4096)
    main.run(
        InputReceived(big.id, "long"),
        InputReceived(small.id, "short"),
        ServerChanged(KEY, ServerReady(server(context=2048))),
        settle=True,
    )
    boxes = [u.box for u in main.main_screen if isinstance(u, ShowBox)]
    [box] = [box for box in boxes if "context limit" in box.title]
    assert big.id in box.text and small.id not in box.text


def test_settings_that_did_not_start_are_told_with_why_once(main: MainHarness):
    fallback = ServerReady(server(), fallback="cudaMalloc failed: out of memory")
    chat = main.run(ServerChanged(KEY, fallback), ServerChanged(KEY, fallback), settle=True)
    told = [line for line in chat if "last Settings that started" in line]
    assert len(told) == 1 and "out of memory" in told[0]


def test_server_restart_asks_the_supervisor(main: MainHarness):
    main.run(
        MainCommandEntered("/server restart gpu --now"), MainCommandEntered("/server restart gpu")
    )
    assert main.servers.restarted == [("gpu", True), ("gpu", False)]


def test_no_slot_is_taken_while_the_supervisor_drains(tmp_path: Path):
    servers = tmp_path / "servers"
    write_state(servers, GPU, SupervisorState(status="draining", pid=os.getpid()))
    links = ServerLinks(tmp_path, queue.Queue[MainEvent]())
    lock = SupervisorLock(lock_path(servers, GPU))
    lock.acquire()
    try:
        assert links.take_slot(ServerKey("gpu"), 1) is None
    finally:
        lock.release()


def test_a_step_still_reading_its_input_is_kept_alive_by_the_servers_progress(
    main: MainHarness,
):
    session = main.create_session()
    main.hold_turns = True
    reasons: list[str | None] = []
    main.run(
        InputReceived(session.id, "a very long input"),
        lambda: setattr(main, "now", 100.0),
        ServerProgress(KEY),  # /slots: it read more
        lambda: setattr(main, "now", 219.0),
        Tick(),
        lambda: reasons.append(main.held[0].cancellation.reason),
        settle=True,
    )
    assert reasons == [None]
