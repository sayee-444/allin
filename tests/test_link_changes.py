"""When a pending Enlace change applies, waits or fails (ADR-0022), and what Main says if it fails."""

from allin.core.ids import ServerKey, SessionId, SupervisorName
from allin.main.events import (
    ServerAsleep,
    ServerDown,
    ServerFailed,
    ServerLoading,
    ServerReady,
    ServerRestarting,
    ServerState,
)
from allin.main.link_changes import LinkChange, failure_text, judge
from allin.main.links import Link
from allin.main.slots import ServerProps

CPU = Link(ServerKey("cpu"), "http://127.0.0.1:9102/v1", SupervisorName("cpu"))
READY = ServerReady(ServerProps(total_slots=1, slot_context=4096, model_path="m.gguf"))
VRAM = ServerFailed("vram", "out of memory", (SupervisorName("gpu"),))


def asked(live: bool = False) -> LinkChange:
    return LinkChange(SessionId("s1"), CPU, since=100.0, live=live)


def test_a_ready_server_applies_the_change_unless_the_session_has_an_active_turn():
    assert judge(asked(), READY, busy=False, now=100.0) == "apply"
    assert judge(asked(), READY, busy=True, now=100.0) == "wait"


def test_nothing_known_yet_waits_without_counting_as_running():
    change = asked()
    assert judge(change, None, busy=False, now=105.0) == "wait"
    assert judge(change, VRAM, busy=False, now=105.0) == "wait"  # still maybe stale


def test_a_server_that_runs_marks_the_change_live_and_waits():
    for state in (ServerLoading(), ServerDown(), ServerRestarting(), READY):
        change = asked()
        judge(change, state, busy=True, now=100.0)
        assert change.live, state


def test_a_failure_told_before_the_wake_counts_only_once_ten_seconds_passed():
    for state in (VRAM, ServerAsleep(), ServerRestarting(sleep=True)):
        assert judge(asked(), state, busy=False, now=109.9) == "wait", state
        assert judge(asked(), state, busy=False, now=110.0) == "fail", state
        assert not asked().live


def test_a_failure_after_the_server_ran_ends_the_change_at_once():
    for state in (VRAM, ServerAsleep(), ServerRestarting(sleep=True)):
        assert judge(asked(live=True), state, busy=False, now=100.0) == "fail", state


def test_a_restart_that_does_not_go_to_sleep_waits():
    assert judge(asked(live=True), ServerRestarting(), busy=False, now=200.0) == "wait"


def test_a_supervisor_a_human_put_to_sleep_fails_at_once():
    asleep = ServerAsleep(slept_by_human=True)
    assert judge(asked(), asleep, busy=False, now=100.0) == "fail"


def told(state: ServerState | None) -> str:
    return failure_text(asked(), state, "Supervisor gpu")


def test_each_failure_says_why_and_that_the_enlace_stays():
    assert told(VRAM) == (
        "Enlace not changed: Supervisor cpu does not fit in VRAM: the GPU is held by Supervisor"
        " gpu; free it with /server sleep gpu, or lower its Settings with /server set; the"
        " Enlace stays on Supervisor gpu"
    )
    assert told(ServerFailed("vram", "out of memory")) == (
        "Enlace not changed: Supervisor cpu does not fit in VRAM and no Supervisor is active,"
        " so something outside AllIn holds the GPU; lower its Settings with /server set; the"
        " Enlace stays on Supervisor gpu"
    )
    assert told(ServerFailed("port_in_use", "port 9102 is used")) == (
        "Enlace not changed: Supervisor cpu failed (port_in_use): port 9102 is used; the Enlace"
        " stays on Supervisor gpu"
    )
    assert told(ServerAsleep(slept_by_human=True)) == (
        "Enlace not changed: Supervisor cpu was put to sleep by a human; wake it with"
        " /server wake cpu; the Enlace stays on Supervisor gpu"
    )
    assert told(ServerAsleep()) == (
        "Enlace not changed: Supervisor cpu fell asleep before it was ready; the Enlace stays on"
        " Supervisor gpu"
    )


def test_several_supervisors_holding_the_gpu_are_each_named():
    holders = (SupervisorName("gpu"), SupervisorName("rag"))
    text = told(ServerFailed("vram", "out of memory", holders))
    assert "held by Supervisor gpu, rag" in text
    assert "/server sleep gpu or /server sleep rag" in text
