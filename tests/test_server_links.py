import os
import queue
import signal
from pathlib import Path

from allin.core.ids import ServerKey, SupervisorName
from allin.main.events import MainEvent, ServerAsleep, ServerFailed, ServerLoading
from allin.main.links import LinkResolver
from allin.main.server_links import ServerLinks
from allin.main.server_watch import ServerWatch, supervisor_view
from allin.supervisor.lock import SupervisorLock, lock_path, supervisor_active
from allin.supervisor.state import SupervisorState, read_state, write_state
from tests.test_server_watch import FakeServer, next_state, props
from tests.test_supervisor import free_port, wait_for, write_settings

GPU = SupervisorName("gpu")


def test_a_supervisor_that_never_ran_is_asleep(tmp_path: Path):
    assert supervisor_view(tmp_path, GPU) == ServerAsleep()


def test_one_a_human_put_to_sleep_says_so(tmp_path: Path):
    write_state(tmp_path, GPU, SupervisorState(status="asleep", slept_by_human=True))
    assert supervisor_view(tmp_path, GPU) == ServerAsleep(slept_by_human=True)


def test_a_failed_one_that_did_not_fit_in_vram_names_the_active_supervisors(tmp_path: Path):
    write_state(tmp_path, GPU, SupervisorState(status="failed", failure="vram", reason="oom"))
    (tmp_path / "gpu.toml").write_text("")
    (tmp_path / "cpu.toml").write_text("")
    holder = SupervisorLock(lock_path(tmp_path, SupervisorName("cpu")))
    holder.acquire()
    try:
        assert supervisor_view(tmp_path, GPU) == ServerFailed(
            "vram", "oom", (SupervisorName("cpu"),)
        )
    finally:
        holder.release()
    assert supervisor_view(tmp_path, GPU) == ServerFailed("vram", "oom", ())


def test_an_active_one_whose_server_does_not_answer_yet_is_loading(tmp_path: Path):
    lock = SupervisorLock(lock_path(tmp_path, GPU))
    lock.acquire()
    try:
        write_state(tmp_path, GPU, SupervisorState(status="starting", pid=1))
        assert supervisor_view(tmp_path, GPU) == ServerLoading()
    finally:
        lock.release()


def test_main_wakes_a_supervisor_in_its_own_session_and_the_next_main_finds_it_awake(
    tmp_path: Path,
):
    write_settings(tmp_path, "gpu", free_port())
    servers = tmp_path / "servers"
    inbox: queue.Queue[MainEvent] = queue.Queue()
    first = ServerLinks(tmp_path, inbox)
    assert first.wake(GPU)
    try:
        wait_for(lambda: (s := read_state(servers, GPU)) is not None and s.status == "ready")
        state = read_state(servers, GPU)
        assert state is not None and state.pid is not None
        assert os.getsid(state.pid) != os.getsid(0)  # it outlives this Main
        assert not ServerLinks(tmp_path, inbox).wake(GPU)  # the next Main only uses it
        link = LinkResolver(servers, "http://127.0.0.1:9931/v1").resolve_name(GPU)
        assert link.key == ServerKey("gpu")
    finally:
        state = read_state(servers, GPU)
        if state is not None and state.pid is not None:
            os.kill(state.pid, signal.SIGTERM)
        wait_for(lambda: not supervisor_active(servers, GPU))
    final = read_state(servers, GPU)
    assert final is not None and final.status == "asleep"


def test_what_answers_on_the_port_of_a_supervisor_that_does_not_run_is_not_its_server(
    tmp_path: Path,
):
    with FakeServer(props(1, 4096)) as stranger:
        inbox: queue.Queue[MainEvent] = queue.Queue()
        watch = ServerWatch(ServerKey("gpu"), stranger.base_url, inbox, (tmp_path, GPU), 0.01)
        watch.start()
        try:
            assert next_state(inbox) == ServerAsleep()
        finally:
            watch.stop()


def test_one_that_ended_since_it_was_woken_can_be_woken_again_at_once(tmp_path: Path):
    servers = tmp_path / "servers"
    servers.mkdir()
    links = ServerLinks(tmp_path, queue.Queue[MainEvent]())
    assert links.wake(GPU)  # no gpu.toml: it ends at once, failed
    wait_for(lambda: (s := read_state(servers, GPU)) is not None and s.status == "failed")
    wait_for(lambda: not supervisor_active(servers, GPU))
    assert links.wake(GPU)  # e.g. tried again once the GPU's holder fell asleep
    wait_for(lambda: not supervisor_active(servers, GPU))


def test_one_just_woken_is_not_woken_twice(tmp_path: Path):
    write_settings(tmp_path, "gpu", free_port(), "--fake-load", "5")
    servers = tmp_path / "servers"
    links = ServerLinks(tmp_path, queue.Queue[MainEvent]())
    assert links.wake(GPU)
    try:
        assert not links.wake(GPU)
    finally:
        wait_for(lambda: (s := read_state(servers, GPU)) is not None and s.pid is not None)
        state = read_state(servers, GPU)
        assert state is not None and state.pid is not None
        os.kill(state.pid, signal.SIGTERM)
        wait_for(lambda: not supervisor_active(servers, GPU))
