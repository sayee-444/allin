"""Each Session's Enlace (ADR-0022): where its Steps go, and the wait of each Supervisor."""

from pathlib import Path

import pytest

from allin.core.errors import LlmUnavailable
from allin.core.ids import ServerKey, SupervisorName
from allin.ipc.messages import ChatAppend, ContextUsage
from allin.main.conversation import Step
from allin.main.events import (
    InputReceived,
    MainCommandEntered,
    ServerAsleep,
    ServerChanged,
    ServerDown,
    ServerFailed,
    ServerLoading,
    ServerReady,
    Tick,
    TurnEnded,
    WindowConnected,
)
from allin.ui.screen import ShowBox
from tests.test_dispatcher import MainHarness, server

GPU, CPU = ServerKey("gpu"), ServerKey("cpu")


@pytest.fixture
def main(tmp_path: Path) -> MainHarness:
    harness = MainHarness(tmp_path)
    servers = harness.slot_locks_dir
    servers.mkdir(parents=True, exist_ok=True)
    for name, port in (("gpu", 9101), ("cpu", 9102)):
        (servers / f"{name}.toml").write_text(f'model = "m.gguf"\ncontext = 8192\nport = {port}\n')
    harness.hold_turns = True
    return harness


def ready(key: ServerKey, slots: int = 1) -> ServerChanged:
    return ServerChanged(key, ServerReady(server(slots=slots)))


def told(main: MainHarness, session_id: str) -> list[str]:
    return [
        message.text
        for window in main.windows(session_id)
        for message in window.sent
        if isinstance(message, ChatAppend) and message.label == "queue"
    ]


def test_a_prompt_to_a_session_linked_to_an_asleep_supervisor_wakes_it_and_runs_once_ready(
    main: MainHarness,
):
    session = main.create_session(server="gpu")
    main.run(
        MainCommandEntered(f"/open {session.id}"),
        InputReceived(session.id, "hi"),
        ServerChanged(GPU, ServerAsleep()),
        ServerChanged(GPU, ServerLoading()),
        ready(GPU),
        settle=True,
    )
    assert set(main.servers.woken) == {"gpu"}  # ServerLinks wakes it once
    [job] = main.held
    assert job.base_url == "http://127.0.0.1:9101/v1"
    assert any("work for it wakes it" in text for text in told(main, session.id))


def test_main_does_not_wake_a_supervisor_without_work_for_it(main: MainHarness):
    main.create_session(server="gpu")
    main.run(MainCommandEntered("/session"), ServerChanged(GPU, ServerAsleep()), settle=True)
    assert main.servers.woken == []


def test_what_a_human_put_to_sleep_only_a_human_wakes(main: MainHarness):
    session = main.create_session(server="gpu")
    main.run(
        InputReceived(session.id, "hi"),
        ServerChanged(GPU, ServerAsleep(slept_by_human=True)),
        settle=True,
    )
    assert main.servers.woken == ["gpu"]  # the Prompt asked; ServerLinks refuses it on disk
    assert main.held == []


def test_while_one_supervisor_is_not_ready_only_its_sessions_wait(main: MainHarness):
    on_gpu, on_cpu = main.create_session(server="gpu"), main.create_session(server="cpu")
    main.run(
        InputReceived(on_gpu.id, "a"),
        InputReceived(on_cpu.id, "b"),
        ServerChanged(GPU, ServerDown()),
        ready(CPU),
        settle=True,
    )
    assert [job.session_id for job in main.held] == [on_cpu.id]


def test_two_sessions_of_one_main_on_two_supervisors_run_at_once_each_on_its_own(
    main: MainHarness,
):
    on_gpu, on_cpu = main.create_session(server="gpu"), main.create_session(server="cpu")
    main.run(
        InputReceived(on_gpu.id, "a"),
        InputReceived(on_cpu.id, "b"),
        ready(GPU),
        ready(CPU),
        settle=True,
    )
    assert {job.session_id: job.base_url for job in main.held} == {
        on_gpu.id: "http://127.0.0.1:9101/v1",
        on_cpu.id: "http://127.0.0.1:9102/v1",
    }
    assert sorted(main.servers.taken) == ["cpu", "gpu"]  # a slot lock of each one's own


def test_parallel_caps_the_turns_of_the_whole_main_across_its_supervisors(main: MainHarness):
    main.settings = main.settings.model_copy(update={"parallel": 1})
    on_gpu, on_cpu = main.create_session(server="gpu"), main.create_session(server="cpu")
    main.run(
        InputReceived(on_gpu.id, "a"),
        InputReceived(on_cpu.id, "b"),
        ready(GPU),
        ready(CPU),
        settle=True,
    )
    assert [job.session_id for job in main.held] == [on_gpu.id]


def test_a_session_linked_to_a_supervisor_with_no_settings_says_so_and_drops_its_prompt(
    main: MainHarness,
):
    session = main.create_session(server="nowhere")
    chat = main.run(InputReceived(session.id, "hi"), settle=True)
    assert main.held == []
    assert any("server_settings_invalid" in line and "nowhere" in line for line in chat)


def test_new_copies_the_default_enlace_of_the_template(main: MainHarness):
    main.run(MainCommandEntered("/set session.server=cpu"), MainCommandEntered("/new"))
    [(session_id, _)] = main.opened
    assert main.sessions.load(session_id).settings.server == "cpu"


def test_a_server_that_does_not_fit_in_vram_tells_who_holds_the_gpu_and_waits(
    main: MainHarness,
):
    session = main.create_session(server="gpu")
    holder = (SupervisorName("cpu"),)
    main.run(
        InputReceived(session.id, "hi"),
        ServerChanged(GPU, ServerFailed("vram", "cudaMalloc failed: out of memory", holder)),
        settle=True,
    )
    [box] = [update.box for update in main.main_screen if isinstance(update, ShowBox)]
    assert "gpu" in box.title and "cpu" in box.text
    assert main.held == []
    assert main.servers.woken == ["gpu"]  # only for the Prompt: Main puts no one to sleep


def test_new_work_does_not_wake_again_a_supervisor_that_did_not_fit(main: MainHarness):
    session = main.create_session(server="gpu")
    holder = (SupervisorName("cpu"),)
    main.hold_turns = False
    main.run(
        InputReceived(session.id, "one"),
        ServerChanged(GPU, ServerFailed("vram", "out of memory", holder)),
        MainCommandEntered(f"/send {session.id} two"),
        settle=True,
    )
    assert main.servers.woken == ["gpu"]


def test_a_supervisor_that_failed_otherwise_is_a_notice_saying_why(main: MainHarness):
    session = main.create_session(server="gpu")
    reason = "port 9101 is used by pid 7 (llama)"
    main.run(
        InputReceived(session.id, "hi"),
        ServerChanged(GPU, ServerFailed("port_in_use", reason)),
        settle=True,
    )
    [box] = [update.box for update in main.main_screen if isinstance(update, ShowBox)]
    assert "port_in_use" in box.title and box.text == reason


def change(session_id: str, name: str) -> MainCommandEntered:
    return MainCommandEntered(f"/session set {session_id} server={name}")


def test_changing_the_enlace_mid_turn_lets_the_turn_end_on_the_old_one_and_the_next_go_to_the_new(
    main: MainHarness,
):
    session = main.create_session(server="gpu")
    linked: list[str | None] = []

    def look() -> None:
        linked.append(main.sessions.load(session.id).settings.server)

    main.run(
        InputReceived(session.id, "one"),
        ready(GPU),
        change(session.id, "cpu"),
        ready(CPU),
        InputReceived(session.id, "two"),
        look,  # the second waits for the first, and the change with it
        TurnEnded(session.id, steps=[Step(output=[])]),
        look,
        settle=True,
    )
    assert linked == ["gpu", "cpu"]
    assert [job.base_url for job in main.held] == [
        "http://127.0.0.1:9101/v1",
        "http://127.0.0.1:9102/v1",
    ]
    assert main.servers.slept == []  # the old Supervisor goes on


def test_once_the_supervisor_holding_the_gpu_falls_asleep_main_tries_again(main: MainHarness):
    session = main.create_session(server="gpu")
    holder = (SupervisorName("cpu"),)
    main.run(
        InputReceived(session.id, "hi"),
        ServerChanged(GPU, ServerFailed("vram", "out of memory", holder)),
        ServerChanged(GPU, ServerFailed("vram", "out of memory", ())),  # cpu fell asleep
        settle=True,
    )
    assert main.servers.woken == ["gpu", "gpu"]


def test_each_window_shows_the_context_limit_of_its_own_supervisor(main: MainHarness):
    on_gpu, on_cpu = main.create_session(server="gpu"), main.create_session(server="cpu")
    main.run(
        MainCommandEntered(f"/open {on_gpu.id}"),
        MainCommandEntered(f"/open {on_cpu.id}"),
        WindowConnected(on_gpu.id),  # a Window that connects has its Server watched
        WindowConnected(on_cpu.id),
        ServerChanged(GPU, ServerReady(server(context=4096))),
        ServerChanged(CPU, ServerReady(server(context=2048))),
        settle=True,
    )

    def limits(session_id: str) -> list[int | None]:
        [window] = main.windows(session_id)
        return [m.limit for m in window.sent if isinstance(m, ContextUsage)]

    assert limits(on_gpu.id)[-1] == 4096 and 2048 not in limits(on_gpu.id)
    assert limits(on_cpu.id)[-1] == 2048 and 4096 not in limits(on_cpu.id)


def test_changing_the_default_enlace_moves_no_session_and_the_next_new_uses_it(
    main: MainHarness,
):
    session = main.create_session(server="gpu")
    main.run(MainCommandEntered("/set session.server=cpu"), MainCommandEntered("/new"))
    [(new_id, _)] = main.opened
    assert main.sessions.load(session.id).settings.server == "gpu"
    assert main.sessions.load(new_id).settings.server == "cpu"


def test_the_default_enlace_cannot_name_a_supervisor_with_no_settings(main: MainHarness):
    chat = main.run(MainCommandEntered("/set session.server=nowhere"))
    assert any("server_settings_invalid" in line for line in chat)
    assert main.settings.session.server is None


def test_a_supervisor_that_does_not_fit_in_vram_leaves_the_enlace_where_it_was_and_says_why(
    main: MainHarness,
):
    session = main.create_session(server="gpu")
    holder = (SupervisorName("gpu"),)
    chat = main.run(
        change(session.id, "cpu"),
        ServerChanged(CPU, ServerLoading()),
        ServerChanged(CPU, ServerFailed("vram", "cudaMalloc failed: out of memory", holder)),
        settle=True,
    )
    assert main.sessions.load(session.id).settings.server == "gpu"
    [failed] = [line for line in chat if "Enlace not changed" in line]
    assert "does not fit in VRAM" in failed and "/server sleep gpu" in failed
    assert "stays on Supervisor gpu" in failed
    assert main.servers.woken == ["cpu"] and main.servers.slept == []


def test_moving_a_session_to_a_supervisor_with_less_context_than_its_conversation_gives_a_notice(
    main: MainHarness,
):
    session = main.create_session(server="gpu")
    usage = {"input_tokens": 5000, "output_tokens": 1000, "total_tokens": 6000}
    main.run(
        InputReceived(session.id, "long"),
        ServerChanged(GPU, ServerReady(server(context=8192))),
        TurnEnded(session.id, steps=[Step(output=[], usage=usage)]),
        change(session.id, "cpu"),
        ServerChanged(CPU, ServerReady(server(context=4096))),
        settle=True,
    )
    assert main.sessions.load(session.id).settings.server == "cpu"
    [box] = [update.box for update in main.main_screen if isinstance(update, ShowBox)]
    assert "4096" in box.title and "6000" in box.text


def test_a_change_to_a_supervisor_a_human_put_to_sleep_fails_at_once_saying_how_to_wake_it(
    main: MainHarness,
):
    session = main.create_session(server="gpu")
    chat = main.run(
        change(session.id, "cpu"),
        ServerChanged(CPU, ServerAsleep(slept_by_human=True)),
        settle=True,
    )
    assert main.sessions.load(session.id).settings.server == "gpu"
    assert any("/server wake cpu" in line for line in chat)


def test_a_failure_the_supervisor_told_before_it_was_woken_ends_the_change_only_once_stale(
    main: MainHarness,
):
    session = main.create_session(server="gpu")

    def later() -> None:
        main.now += 11

    chat = main.run(
        change(session.id, "cpu"),
        ServerChanged(CPU, ServerFailed("port_in_use", "port 9102 is used")),  # its last run
        Tick(),
        later,
        Tick(),
        settle=True,
    )
    failed = [line for line in chat if "Enlace not changed" in line]
    assert len(failed) == 1 and "port_in_use" in failed[0]


def test_while_its_enlace_changes_a_sessions_prompts_wait_and_then_go_to_the_new_one(
    main: MainHarness,
):
    session = main.create_session(server="gpu")
    main.run(
        ready(GPU),
        change(session.id, "cpu"),
        InputReceived(session.id, "hi"),
        ServerChanged(CPU, ServerLoading()),
        ready(CPU),
        settle=True,
    )
    assert [job.base_url for job in main.held] == ["http://127.0.0.1:9102/v1"]


def test_asking_for_the_current_enlace_drops_a_pending_change(main: MainHarness):
    session = main.create_session(server="gpu")
    chat = main.run(
        change(session.id, "cpu"),
        change(session.id, "gpu"),
        InputReceived(session.id, "hi"),
        ready(CPU),
        ready(GPU),
        settle=True,
    )
    assert main.sessions.load(session.id).settings.server == "gpu"
    assert any("dropped" in line for line in chat)
    assert [job.base_url for job in main.held] == ["http://127.0.0.1:9101/v1"]


def test_a_session_can_move_to_the_server_at_mains_base_url(main: MainHarness):
    session = main.create_session(server="gpu")
    main.run(change(session.id, ""), settle=True)  # the harness's Server at 9931 is ready
    assert main.sessions.load(session.id).settings.server is None


def test_a_turn_suspended_by_a_fall_repeats_its_step_on_the_old_server_before_the_change(
    main: MainHarness,
):
    session = main.create_session(server="gpu")
    main.run(
        InputReceived(session.id, "one"),
        ready(GPU),
        change(session.id, "cpu"),
        ready(CPU),
        TurnEnded(session.id, error=LlmUnavailable(reason="Connection error.")),
        ready(GPU),
        settle=True,
    )
    assert [job.base_url for job in main.held] == [
        "http://127.0.0.1:9101/v1",
        "http://127.0.0.1:9101/v1",  # the same Step again, on the same model
    ]
    assert main.sessions.load(session.id).settings.server == "gpu"


def test_a_change_to_mains_server_while_it_is_down_fails_and_frees_the_sessions_prompts(
    main: MainHarness,
):
    session = main.create_session(server="gpu")
    main.server_up = False
    chat = main.run(
        change(session.id, ""),
        ServerChanged(ServerKey("9931"), ServerDown()),
        InputReceived(session.id, "hi"),
        ready(GPU),
        settle=True,
    )
    assert any("Enlace not changed" in line and "cannot be reached" in line for line in chat)
    assert [job.base_url for job in main.held] == ["http://127.0.0.1:9101/v1"]
