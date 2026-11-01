import os
import signal
import socket
import sys
import threading
import time
from collections.abc import Callable, Iterator
from pathlib import Path

import pytest

from allin.core.errors import ServerSettingsInvalid, SupervisorAlreadyRunning
from allin.supervisor.lock import supervisor_active
from allin.supervisor.process import MAX_CRASHES, Supervisor
from allin.supervisor.settings import ServerSettings, load_server_settings
from allin.supervisor.state import SupervisorState, read_state

FAKE = Path(__file__).parent / "fake_llama.py"


def free_port() -> int:
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return probe.getsockname()[1]


def write_settings(data_dir: Path, name: str, port: int, *extra: str, parallel: int = 1) -> None:
    servers = data_dir / "servers"
    servers.mkdir(parents=True, exist_ok=True)
    flags = ", ".join(f'"{flag}"' for flag in extra)
    (servers / f"{name}.toml").write_text(
        f'model = "m.gguf"\ncontext = 8192\nparallel = {parallel}\nport = {port}\n'
        f'command = ["{sys.executable}", "{FAKE}"]\nextra = [{flags}]\n'
    )


def wait_for(check: Callable[[], bool], timeout: float = 10.0) -> None:
    deadline = time.monotonic() + timeout
    while not check():
        if time.monotonic() > deadline:
            raise AssertionError("timed out")
        time.sleep(0.05)


class Running:
    """A Supervisor run on a thread of the test, as its process would run it."""

    def __init__(self, data_dir: Path, name: str = "gpu") -> None:
        self.data_dir = data_dir
        self.name = name
        self.supervisor = Supervisor(name, data_dir, backoff=0.05, poll=0.05, stop_grace=2.0)
        self.exit_code: int | None = None
        self._thread = threading.Thread(target=self._run, daemon=True)

    def _run(self) -> None:
        self.exit_code = self.supervisor.run(os.getpid())

    def start(self) -> "Running":
        self._thread.start()
        return self

    def state(self) -> SupervisorState | None:
        return read_state(self.data_dir / "servers", self.name)

    def status_is(self, status: str) -> Callable[[], bool]:
        return lambda: (state := self.state()) is not None and state.status == status

    def stop(self) -> None:
        self.supervisor.stop()
        self._thread.join(10)

    def ended(self) -> bool:
        return not self._thread.is_alive()


@pytest.fixture
def running(tmp_path: Path) -> Iterator[Callable[..., Running]]:
    started: list[Running] = []

    def start(*extra: str, port: int | None = None, parallel: int = 1) -> Running:
        write_settings(tmp_path, "gpu", port or free_port(), *extra, parallel=parallel)
        supervisor = Running(tmp_path).start()
        started.append(supervisor)
        return supervisor

    yield start
    for supervisor in started:
        supervisor.stop()


def test_two_slots_or_more_get_a_unified_cache_unless_the_settings_say_not():
    two = ServerSettings(model="m.gguf", context=8192, parallel=2, port=9931)
    assert "--kv-unified" in two.arguments()
    assert "--kv-unified" not in two.model_copy(update={"kv_unified": False}).arguments()
    assert "--kv-unified" not in ServerSettings(model="m.gguf", context=8192, port=9931).arguments()


def test_context_shift_is_never_allowed(tmp_path: Path):
    (tmp_path / "x.toml").write_text(
        'model = "m.gguf"\ncontext = 8192\nport = 9931\nextra = ["--context-shift"]\n'
    )
    with pytest.raises(ServerSettingsInvalid) as raised:
        load_server_settings(tmp_path / "x.toml")
    assert raised.value.fields["key"] == "extra"


def test_it_launches_its_server_and_says_when_it_is_ready(running: Callable[..., Running]):
    supervisor = running("--fake-load", "0.3", parallel=2)
    wait_for(supervisor.status_is("ready"))
    state = supervisor.state()
    assert state is not None and state.server_pid is not None and state.pid == os.getpid()
    assert "--kv-unified" in state.arguments
    log = supervisor.data_dir / "logs" / "servers" / "gpu" / "server.log"
    assert "fake llama on" in log.read_text()


def test_a_server_killed_outright_is_launched_again(running: Callable[..., Running]):
    supervisor = running()
    wait_for(supervisor.status_is("ready"))
    first = supervisor.state()
    assert first is not None and first.server_pid is not None
    os.kill(first.server_pid, signal.SIGKILL)
    wait_for(lambda: (s := supervisor.state()) is not None and s.server_pid != first.server_pid)
    wait_for(supervisor.status_is("ready"))


def test_five_crashes_in_a_row_leave_it_failed(running: Callable[..., Running]):
    supervisor = running("--fake-mode", "crash")
    wait_for(supervisor.ended)
    state = supervisor.state()
    assert state is not None and state.status == "failed" and state.failure == "crashes"
    assert state.last_exit_code == 3 and str(MAX_CRASHES) in (state.reason or "")
    assert supervisor.exit_code == 1


def test_a_server_that_does_not_fit_in_vram_fails_at_once_saying_so(
    running: Callable[..., Running],
):
    supervisor = running("--fake-mode", "vram")
    wait_for(supervisor.ended)
    state = supervisor.state()
    assert state is not None and state.status == "failed" and state.failure == "vram"
    assert "out of memory" in (state.reason or "")


def test_a_port_already_taken_fails_naming_who_has_it(running: Callable[..., Running]):
    with socket.socket() as holder:
        holder.bind(("127.0.0.1", 0))
        holder.listen()
        supervisor = running(port=holder.getsockname()[1])
        wait_for(supervisor.ended)
    state = supervisor.state()
    assert state is not None and state.failure == "port_in_use"
    assert f"pid {os.getpid()}" in (state.reason or "")


def test_a_second_supervisor_with_the_same_name_leaves_without_touching_anything(
    running: Callable[..., Running],
):
    supervisor = running()
    wait_for(supervisor.status_is("ready"))
    before = (supervisor.data_dir / "servers" / "gpu.json").read_bytes()
    with pytest.raises(SupervisorAlreadyRunning):
        Supervisor("gpu", supervisor.data_dir).run(os.getpid())
    assert (supervisor.data_dir / "servers" / "gpu.json").read_bytes() == before
    assert supervisor.status_is("ready")()


def test_stopped_it_ends_its_server_and_falls_asleep(running: Callable[..., Running]):
    supervisor = running()
    wait_for(supervisor.status_is("ready"))
    state = supervisor.state()
    assert state is not None and state.server_pid is not None
    assert supervisor_active(supervisor.data_dir / "servers", "gpu")
    supervisor.stop()
    assert supervisor.status_is("asleep")() and supervisor.exit_code == 0
    assert not supervisor_active(supervisor.data_dir / "servers", "gpu")
    with pytest.raises(ProcessLookupError):
        os.kill(state.server_pid, 0)
