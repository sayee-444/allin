"""Each Session's Enlace (ADR-0022): where its Steps go, and the wait of each Supervisor."""

from pathlib import Path

import pytest

from allin.core.ids import ServerKey, SupervisorName
from allin.ipc.messages import ChatAppend, ContextUsage
from allin.main.events import (
    InputReceived,
    MainCommandEntered,
    ServerAsleep,
    ServerChanged,
    ServerDown,
    ServerFailed,
    ServerLoading,
    ServerReady,
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


def test_a_sessions_enlace_cannot_change_until_it_can_change_atomically(main: MainHarness):
    session = main.create_session(server="gpu")
    chat = main.run(MainCommandEntered(f"/session set {session.id} server=cpu"))
    assert any("invalid_setting" in line for line in chat)
    assert main.sessions.load(session.id).settings.server == "gpu"


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
