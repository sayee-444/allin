"""``/server …``: show, create, change, put to sleep and wake Supervisors (#26, ADR-0022)."""

import os
import subprocess
import sys
import time
from pathlib import Path

import pytest

from allin.core.ids import ServerKey, SupervisorName
from allin.main.conversation import Step
from allin.main.events import (
    InputReceived,
    MainCommandEntered,
    ServerAsleep,
    ServerChanged,
    ServerRestarting,
    TurnEnded,
)
from allin.supervisor.lock import SupervisorLock, lock_path
from allin.supervisor.ports import port_free
from allin.supervisor.settings import load_server_settings
from allin.supervisor.state import SupervisorState, write_state
from allin.ui.screen import ShowBox
from tests.test_dispatcher import MainHarness
from tests.test_links import ready
from tests.test_supervisor import FAKE, free_port, wait_for

GPU = ServerKey("gpu")
GPU_TOML = 'model = "m.gguf"\ncontext = 8192\nport = 9101\n'


@pytest.fixture
def main(tmp_path: Path) -> MainHarness:
    harness = MainHarness(tmp_path)
    harness.slot_locks_dir.mkdir(parents=True, exist_ok=True)
    (harness.slot_locks_dir / "gpu.toml").write_text(GPU_TOML)
    return harness


def toml(main: MainHarness, name: str = "gpu") -> Path:
    return main.slot_locks_dir / f"{name}.toml"


def human(text: str) -> MainCommandEntered:
    return MainCommandEntered(text)


def from_a_session(text: str) -> MainCommandEntered:
    """A Command main as a Session's ``@server`` will send it (F2)."""
    return MainCommandEntered(text, origin="session")


def test_new_creates_an_asleep_supervisor_with_its_settings(main: MainHarness):
    chat = main.run(human('/server new cpu model=small.gguf port=9102 extra="--jinja"'))
    assert 'model = "small.gguf"' in toml(main, "cpu").read_text()
    assert any("cpu created, asleep" in line for line in chat)
    assert main.servers.woken == []


def test_new_refuses_a_name_that_exists_or_settings_that_are_not_valid(main: MainHarness):
    chat = main.run(human("/server new gpu model=x.gguf port=9"), human("/server new cpu port=9"))
    assert toml(main).read_text() == GPU_TOML
    assert not toml(main, "cpu").exists()
    assert sum("error" in line for line in chat) == 2


def test_set_changes_the_pending_settings_without_restarting(main: MainHarness):
    chat = main.run(human("/server set gpu context=64000 parallel=2"))
    text = toml(main).read_text()
    assert "context = 64000" in text and "parallel = 2" in text
    assert main.servers.restarted == []
    assert any("/server restart gpu applies them" in line for line in chat)


def test_set_without_a_name_changes_the_default_enlaces_supervisor(main: MainHarness):
    main.run(human("/set session.server=gpu"), human("/server set context=4096"))
    assert "context = 4096" in toml(main).read_text()


@pytest.mark.parametrize(
    "assignments",
    ["temperature=0.6", "context=0", "flash_attn=maybe", "extra=--context-shift", "context"],
)
def test_set_rejects_an_unknown_key_or_an_invalid_value_without_touching_the_toml(
    main: MainHarness, assignments: str
):
    before = toml(main).stat().st_mtime_ns
    chat = main.run(human(f"/server set gpu {assignments}"))
    assert toml(main).read_text() == GPU_TOML and toml(main).stat().st_mtime_ns == before
    assert any("error" in line for line in chat)


@pytest.mark.parametrize("assignments", ["model=other.gguf", "port=9200", 'extra="--host 0.0.0.0"'])
def test_a_key_that_names_a_file_or_opens_the_network_is_only_set_by_a_human(
    main: MainHarness, assignments: str
):
    chat = main.run(
        from_a_session(f"/server set gpu {assignments}"),
        from_a_session(f"/server new cpu {assignments} model=m.gguf port=9102"),
    )
    assert toml(main).read_text() == GPU_TOML and not toml(main, "cpu").exists()
    assert sum("human_only" in line for line in chat) == 2


def test_a_session_may_change_the_other_keys(main: MainHarness):
    main.run(from_a_session("/server set gpu context=4096"))
    assert "context = 4096" in toml(main).read_text()


def test_server_without_a_name_lists_every_supervisor_with_its_open_sessions(main: MainHarness):
    toml(main, "cpu").write_text('model = "m.gguf"\nport = 9102\n')
    a, b = main.create_session(server="gpu"), main.create_session(server="gpu")
    main.create_session(server="cpu")  # closed: not counted
    asleep = SupervisorState(status="asleep", slept_by_human=True)
    write_state(main.slot_locks_dir, SupervisorName("cpu"), asleep)
    chat = main.run(human(f"/open {a.id}"), human(f"/open {b.id}"), human("/server"))
    [gpu] = [line for line in chat if line.startswith("gpu")]
    [cpu] = [line for line in chat if line.startswith("cpu")]
    assert "used by 2 open Session(s)" in gpu and "asleep" in gpu
    assert "used by 0 open Session(s)" in cpu and "by a human" in cpu


def test_server_with_a_name_marks_where_the_pending_settings_differ_from_the_running_ones(
    main: MainHarness,
):
    servers = main.slot_locks_dir
    (servers / "gpu.toml").write_text(GPU_TOML + 'cwd = "/srv/models"\nkv_unified = true\n')
    running = ["llama", "serve", "-m", "m.gguf", "-c", "16384", "--kv-unified", "--port", "9101"]
    lock = SupervisorLock(lock_path(servers, SupervisorName("gpu")))
    lock.acquire()  # as its process would
    try:
        state = SupervisorState(
            status="ready", pid=os.getpid(), server_pid=1, arguments=running, cwd="/srv/models"
        )
        write_state(servers, SupervisorName("gpu"), state)
        chat = main.run(human("/server gpu"))
    finally:
        lock.release()
    assert any(line.startswith("Supervisor gpu: ready") for line in chat)
    [context] = [line for line in chat if line.startswith("context=")]
    assert context == "context=8192   * running: 16384"
    [model] = [line for line in chat if line.startswith("model=")]
    [cwd] = [line for line in chat if line.startswith("cwd=")]
    assert "running" not in model and "running" not in cwd
    assert "kv_unified=true" in chat


def test_sleep_asks_a_confirmation_and_only_a_human_asks_it(main: MainHarness):
    main.run(human("/server sleep gpu"), from_a_session("/server sleep gpu"))
    boxes = [u.box for u in main.main_screen if isinstance(u, ShowBox)]
    assert [box.title for box in boxes] == ["/server sleep gpu"]
    assert main.servers.slept == []
    main.run(human("/server -y sleep gpu"))
    assert main.servers.slept == ["gpu"]


def test_a_sleep_lets_the_running_step_end_and_a_later_prompt_waits_for_a_humans_wake(
    main: MainHarness,
):
    main.hold_turns = True
    session = main.create_session(server="gpu")
    seen: list[tuple[str | None, int, list[str]]] = []

    def look() -> None:
        reason = main.held[0].cancellation.reason
        seen.append((reason, len(main.held), list(main.servers.woken_by_human)))

    main.run(
        InputReceived(session.id, "first"),
        ready(GPU),
        human("/server -y sleep gpu"),
        ServerChanged(GPU, ServerRestarting(sleep=True)),
        look,  # its Step goes on while the Supervisor drains
        TurnEnded(session.id, steps=[Step(output=[])]),
        ServerChanged(GPU, ServerAsleep(slept_by_human=True)),
        InputReceived(session.id, "second"),
        look,  # it waits: work never wakes what a human put to sleep
        human("/server wake gpu"),
        ready(GPU),
        settle=True,
    )
    assert main.servers.slept == ["gpu"]
    assert seen == [(None, 1, []), (None, 1, [])]
    assert main.servers.woken_by_human == ["gpu"]
    assert len(main.held) == 2  # the second Prompt ran once a human woke it


def test_wake_starts_it_for_a_human_and_watches_it(main: MainHarness):
    chat = main.run(human("/server wake gpu"))
    assert main.servers.woken_by_human == ["gpu"]
    assert any(link.key == GPU for link in main.servers.watched)
    assert any("waking Supervisor gpu" in line for line in chat)


def test_a_supervisor_that_does_not_exist_is_said_so(main: MainHarness):
    chat = main.run(
        human("/server wake nope"), human("/server nope"), human("/server set nope context=1")
    )
    assert sum("supervisor_not_found" in line for line in chat) == 3


def test_adopt_refuses_when_nothing_listens_on_mains_port(main: MainHarness):
    main.settings = main.settings.model_copy(update={"base_url": "http://127.0.0.1:1/v1"})
    chat = main.run(human("/server -y adopt mine"))
    assert any("server_not_adoptable" in line for line in chat)
    assert not toml(main, "mine").exists()


def test_adopt_is_only_a_humans_and_asks_a_confirmation(main: MainHarness):
    chat = main.run(from_a_session("/server adopt mine"), human("/server adopt mine"))
    assert any("human_only" in line for line in chat)
    boxes = [u.box for u in main.main_screen if isinstance(u, ShowBox)]
    assert [box.title for box in boxes] == ["/server adopt mine"]


def test_adopt_reads_the_line_of_the_server_on_mains_port_ends_it_and_wakes_its_supervisor(
    main: MainHarness, tmp_path: Path
):
    port = free_port()
    line = [sys.executable, str(FAKE), "-m", "m.gguf", "-c", "8192", "-np", "2", "--kv-unified"]
    line += ["--jinja", "--temp", "0.6", "--port", str(port)]
    folder = tmp_path / "models"
    folder.mkdir()
    process = subprocess.Popen(line, cwd=folder, stdout=subprocess.DEVNULL)
    try:
        wait_for(lambda: not port_free(port))
        main.settings = main.settings.model_copy(update={"base_url": f"http://127.0.0.1:{port}/v1"})

        def until_it_ends() -> None:
            """Main's thread waits for it and then posts the relaunch, polling every 0.1 s."""
            process.wait(10)
            wait_for(lambda: port_free(port))
            time.sleep(0.5)

        main.run(human("/server -y adopt mine"), until_it_ends, settle=True)
    finally:
        process.kill()
        process.wait()
    settings = load_server_settings(toml(main, "mine"))
    assert sorted(settings.arguments()) == sorted(line)
    assert settings.cwd == str(folder)
    assert main.servers.woken_by_human == ["mine"]
