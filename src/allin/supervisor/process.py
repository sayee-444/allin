"""The Supervisor's work: launch its Server, tell how it goes and launch it again if it falls
(ADR-0008).

It runs as its own process (``python -m allin.supervisor <name>``), in its own session, so it
outlives the Main that woke it. Its Server dies with it (``PR_SET_PDEATHSIG``), so a killed
Supervisor never leaves a Server holding its port and its VRAM.
"""

import ctypes
import os
import re
import signal
import subprocess
import time
import urllib.error
import urllib.request
from collections.abc import Callable
from pathlib import Path

from allin.core.errors import ServerSettingsInvalid
from allin.core.ids import SupervisorName
from allin.supervisor.lock import SupervisorLock, lock_path
from allin.supervisor.ports import port_free, port_holder
from allin.supervisor.settings import ServerSettings, load_server_settings
from allin.supervisor.state import FailureKind, SupervisorState, SupervisorStatus, write_state

MAX_CRASHES = 5  # in a row: then it stays failed
_STABLE = 60.0  # ready this long, a Server's next crash is the first in a row again
_BACKOFF_CAP = 30.0
_HEALTH_TIMEOUT = 2.0
_TAIL_BYTES = 64 * 1024
# What llama.cpp (CUDA, Vulkan, Metal or the host) prints when the model does not fit.
_VRAM = re.compile(
    r"out of memory|failed to allocate|unable to allocate|cudaMalloc failed|"
    r"ErrorOutOfDeviceMemory|not enough memory",
    re.IGNORECASE,
)
_PR_SET_PDEATHSIG = 1


def _die_with(supervisor_pid: int) -> Callable[[], None]:
    """What the Server's process runs before llama: SIGKILL it when the Supervisor ends."""

    def die_with_supervisor() -> None:
        libc = ctypes.CDLL(None, use_errno=True)
        if libc.prctl(_PR_SET_PDEATHSIG, signal.SIGKILL) != 0:
            os._exit(1)  # without it the Server could outlive its Supervisor
        if os.getppid() != supervisor_pid:  # it ended before prctl took
            os._exit(1)

    return die_with_supervisor


class Supervisor:
    def __init__(
        self,
        name: SupervisorName,
        data_dir: Path,
        backoff: float = 1.0,
        poll: float = 0.25,
        stop_grace: float = 10.0,
        stable: float = _STABLE,
    ) -> None:
        self._name = name
        self._servers = data_dir / "servers"
        self._logs = data_dir / "logs" / "servers" / name
        self._backoff = backoff
        self._poll = poll
        self._stop_grace = stop_grace
        self._stable = stable
        self._ready_at: float | None = None  # when its Server got ready, this launch
        self._stopping = False  # set from a signal handler: a plain flag, no lock
        self._pid = 0

    def stop(self) -> None:
        """Stop the Server and fall asleep; safe from a signal handler or another thread."""
        self._stopping = True

    def run(self, pid: int) -> int:
        """Supervise until stopped or failed; the process's exit code. Raises
        ``SupervisorAlreadyRunning``, having touched nothing, if another one is this one."""
        lock = SupervisorLock(lock_path(self._servers, self._name))
        lock.acquire()
        self._pid = pid
        try:
            return self._supervise()
        finally:
            lock.release()

    def _supervise(self) -> int:
        try:
            settings = load_server_settings(self._servers / f"{self._name}.toml")
        except ServerSettingsInvalid as error:
            return self._fail("settings", str(error), None)
        crashes = 0
        while True:
            if self._stopping:  # asked before the model was loaded: not loaded at all
                self._write("asleep", settings)
                return 0
            if not port_free(settings.port):
                holder = port_holder(settings.port)
                return self._fail(
                    "port_in_use", f"port {settings.port} is used by {holder}", settings
                )
            server, log_start = self._launch(settings)
            exit_code = self._watch(server, settings)
            if exit_code is None:  # stopped
                self._end(server)
                self._write("asleep", settings)
                return 0
            if self._ready_at is None and (line := self._vram_line(log_start)) is not None:
                return self._fail("vram", line, settings, exit_code)  # it never fit
            stable = (
                self._ready_at is not None and time.monotonic() - self._ready_at >= self._stable
            )
            crashes = 1 if stable else crashes + 1
            if crashes >= MAX_CRASHES:
                reason = f"{crashes} crashes in a row, last exit code {exit_code}"
                return self._fail("crashes", reason, settings, exit_code)
            self._write("crashed", settings, last_exit_code=exit_code)
            if not self._sleep(min(self._backoff * 2 ** max(crashes - 1, 0), _BACKOFF_CAP)):
                self._write("asleep", settings, last_exit_code=exit_code)
                return 0

    def _launch(self, settings: ServerSettings) -> tuple[subprocess.Popen[bytes], int]:
        self._logs.mkdir(parents=True, exist_ok=True)
        log_path = self._logs / "server.log"
        with log_path.open("ab") as log:
            log_start = log.tell()
            stamp = time.strftime("%Y-%m-%dT%H:%M:%S")
            log.write(f"--- {stamp} {' '.join(settings.arguments())}\n".encode())
            log.flush()
            server = subprocess.Popen(
                settings.arguments(),
                stdin=subprocess.DEVNULL,
                stdout=log,
                stderr=subprocess.STDOUT,
                preexec_fn=_die_with(os.getpid()),  # noqa: PLW1509 -- only this thread forks
            )
        self._ready_at = None
        self._write("starting", settings, server_pid=server.pid)
        return server, log_start

    def _watch(self, server: subprocess.Popen[bytes], settings: ServerSettings) -> int | None:
        """Until the Server exits (its exit code) or the Supervisor is stopped (``None``)."""
        while not self._stopping:
            exit_code = server.poll()
            if exit_code is not None:
                return exit_code
            if self._ready_at is None and _healthy(settings.port):
                self._ready_at = time.monotonic()
                self._write("ready", settings, server_pid=server.pid)
            time.sleep(self._poll)
        return None

    def _end(self, server: subprocess.Popen[bytes]) -> None:
        server.terminate()
        try:
            server.wait(self._stop_grace)
        except subprocess.TimeoutExpired:
            server.kill()
            server.wait()

    def _sleep(self, seconds: float) -> bool:
        """Wait before launching again; ``False`` if stopped meanwhile."""
        deadline = time.monotonic() + seconds
        while time.monotonic() < deadline:
            if self._stopping:
                return False
            time.sleep(min(self._poll, seconds))
        return not self._stopping

    def _vram_line(self, start: int) -> str | None:
        """The line where the Server said it ran out of memory since its last launch (its
        last 64 KiB), if it did."""
        with (self._logs / "server.log").open("rb") as log:
            log.seek(max(start, log.seek(0, 2) - _TAIL_BYTES))
            tail = log.read().decode(errors="replace")
        lines = [line for line in tail.splitlines() if _VRAM.search(line)]
        return lines[-1].strip() if lines else None

    def _fail(
        self,
        failure: FailureKind,
        reason: str,
        settings: ServerSettings | None,
        exit_code: int | None = None,
    ) -> int:
        self._write("failed", settings, failure=failure, reason=reason, last_exit_code=exit_code)
        return 1

    def _write(
        self,
        status: SupervisorStatus,
        settings: ServerSettings | None,
        server_pid: int | None = None,
        last_exit_code: int | None = None,
        failure: FailureKind | None = None,
        reason: str | None = None,
    ) -> None:
        state = SupervisorState(
            status=status,
            pid=self._pid if status != "asleep" and status != "failed" else None,
            server_pid=server_pid,
            arguments=settings.arguments() if settings is not None else [],
            port=settings.port if settings is not None else None,
            last_exit_code=last_exit_code,
            failure=failure,
            reason=reason,
        )
        write_state(self._servers, self._name, state)


def _healthy(port: int) -> bool:
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/health", timeout=_HEALTH_TIMEOUT):
            return True
    except (urllib.error.URLError, OSError):  # not listening yet, or 503 while it loads
        return False
