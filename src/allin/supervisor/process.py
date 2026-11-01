"""The Supervisor's work: launch its Server, tell how it goes and launch it again if it falls
(ADR-0008).

It runs as its own process (``python -m allin.supervisor <name>``), in its own session, so it
outlives the Main that woke it. Its Server dies with it (``PR_SET_PDEATHSIG``), so a killed
Supervisor never leaves a Server holding its port and its VRAM.
"""

import ctypes
import json
import os
import re
import signal
import subprocess
import time
import urllib.error
import urllib.request
from collections.abc import Callable
from pathlib import Path
from typing import Literal

from allin.core.errors import ServerSettingsInvalid, SupervisorAlreadyRunning
from allin.core.ids import ServerKey, SupervisorName
from allin.core.slot_locks import SlotLock, SlotLocks
from allin.supervisor.lock import SupervisorLock, lock_path
from allin.supervisor.ports import port_free, port_holder
from allin.supervisor.settings import ServerSettings, load_server_settings
from allin.supervisor.state import (
    FailureKind,
    RestartKind,
    SupervisorState,
    SupervisorStatus,
    read_state,
    write_state,
)

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
# What would make llama truncate the Conversation in silence, from the environment (ADR-0017).
_CONTEXT_SHIFT_ENV = "LLAMA_ARG_CONTEXT_SHIFT"


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
        self._restart: RestartKind | None = None  # set from a signal handler, like the next
        self._good: ServerSettings | None = None  # the last Settings that got ready
        self._failed_pending: ServerSettings | None = None  # pending ones that did not start
        self._fallback_reason: str | None = None  # why it runs on ``_good`` instead of them
        self._held: list[SlotLock] = []  # every slot lock, while it restarts the Server
        self._server_pid: int | None = None
        self._slots = 1  # the Server's, from its Settings or, when they leave it, its /props
        self._stopping = False  # set from a signal handler: a plain flag, no lock
        self._human_sleep = False  # likewise: drain, then sleep until a human wakes it
        self._pid = 0

    def stop(self) -> None:
        """Stop the Server and fall asleep; safe from a signal handler or another thread."""
        self._stopping = True

    def sleep_by_human(self) -> None:
        """Drain the Server as before a restart, then fall asleep marked as put to sleep by a
        human, whom only a human wakes (ADR-0022). Safe from a signal handler."""
        self._human_sleep = True

    def restart(self, now: bool) -> None:
        """Restart the Server with the pending Settings once no Main has a Step on it; with
        ``now``, Mains cut their Steps short (ADR-0008). Safe from a signal handler."""
        self._restart = "now" if now else "drain"

    def run(self, pid: int) -> int:
        """Supervise until stopped or failed; the process's exit code. Raises
        ``SupervisorAlreadyRunning``, having touched nothing, if another one is this one."""
        lock = SupervisorLock(lock_path(self._servers, self._name))
        lock.acquire()
        self._pid = pid
        try:
            return self._supervise()
        finally:
            self._release_slots()
            lock.release()

    def _supervise(self) -> int:
        try:
            settings = self._pending()
        except ServerSettingsInvalid as error:
            return self._fail("settings", str(error), None)
        crashes = 0
        while True:
            if self._stopping or self._human_sleep:  # asked before the model was loaded
                self._write("asleep", settings)
                return 0
            if not port_free(settings.port):
                holder = port_holder(settings.port)
                return self._fail(
                    "port_in_use", f"port {settings.port} is used by {holder}", settings
                )
            server, log_start = self._launch(settings)
            outcome = self._watch(server, settings)
            if outcome == "sleep":
                self._drain(settings)  # a stop meanwhile only ends it sooner
                return self._fall_asleep(server, settings)
            if outcome is None or (outcome == "restart" and not self._drain(settings)):
                return self._fall_asleep(server, settings)  # stopped, maybe while it drained
            if outcome == "restart":
                self._end(server)  # every slot is ours: no Turn of any Main is suspended
                self._restart = None
                settings = self._next(settings)
                crashes = 0
                continue
            exit_code = outcome
            if self._ready_at is None and settings != self._good and self._good is not None:
                # New Settings that do not start: back to the last that did (ADR-0008).
                vram = self._vram_line(log_start)
                self._fell_back(settings, vram or f"exit code {exit_code} before it was ready")
                settings = self._good
                continue
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
            settings = self._next(settings)  # a relaunch after a crash uses the pending ones

    def _fall_asleep(self, server: subprocess.Popen[bytes], settings: ServerSettings) -> int:
        self._end(server)
        self._write("asleep", settings)
        return 0

    def _pending(self) -> ServerSettings:
        return load_server_settings(self._servers / f"{self._name}.toml")

    def _next(self, settings: ServerSettings) -> ServerSettings:
        """The Settings of the next launch: the pending ones, unless they are the ones that
        already failed to start, or cannot be read (then it says why and keeps these)."""
        try:
            pending = self._pending()
        except ServerSettingsInvalid as error:
            self._fallback_reason = str(error)
            return settings
        if pending == self._failed_pending:
            return settings
        return pending

    def _runs_pending(self, settings: ServerSettings) -> bool:
        try:
            return self._pending() == settings
        except ServerSettingsInvalid:
            return False

    def _fell_back(self, failed: ServerSettings, reason: str) -> None:
        self._failed_pending = failed
        self._fallback_reason = reason

    def _drain(self, settings: ServerSettings) -> bool:
        """Take every slot lock of the Server, waiting for each Step on it to end: then no Main
        has one. ``False`` if stopped meanwhile."""
        told: RestartKind | None = self._restart
        self._write("draining", settings, server_pid=self._server_pid)
        locks = SlotLocks(self._servers, ServerKey(self._name))
        while len(self._held) < self._slots:
            if self._stopping:
                return False
            if self._restart != told:  # ``now`` may come while it drains: Mains read it here
                told = self._restart
                self._write("draining", settings, server_pid=self._server_pid)
            lock = locks.take(self._slots)
            if lock is None:
                time.sleep(self._poll)
            else:
                self._held.append(lock)
        return True

    def _release_slots(self) -> None:
        for lock in self._held:
            lock.release()
        self._held = []

    def _launch(self, settings: ServerSettings) -> tuple[subprocess.Popen[bytes], int]:
        self._logs.mkdir(parents=True, exist_ok=True)
        log_path = self._logs / "server.log"
        with log_path.open("ab") as log:
            log_start = log.tell()
            stamp = time.strftime("%Y-%m-%dT%H:%M:%S")
            log.write(f"--- {stamp} {' '.join(settings.arguments())}\n".encode())
            log.flush()
            env = {k: v for k, v in os.environ.items() if k != _CONTEXT_SHIFT_ENV}
            server = subprocess.Popen(
                settings.arguments(),
                cwd=settings.cwd,
                env=env,
                stdin=subprocess.DEVNULL,
                stdout=log,
                stderr=subprocess.STDOUT,
                preexec_fn=_die_with(os.getpid()),  # noqa: PLW1509 -- only this thread forks
            )
        self._ready_at = None
        self._slots = settings.parallel or 1
        self._server_pid = server.pid
        self._write("starting", settings, server_pid=server.pid)
        return server, log_start

    def _watch(
        self, server: subprocess.Popen[bytes], settings: ServerSettings
    ) -> int | Literal["restart", "sleep"] | None:
        """Until the Server exits (its exit code), a restart or a human's sleep is asked for,
        or the Supervisor is stopped (``None``)."""
        while not self._stopping:
            if self._human_sleep:
                return "sleep"
            exit_code = server.poll()
            if exit_code is not None:
                self._release_slots()
                return exit_code
            if self._ready_at is None and _healthy(settings.port):
                self._ready_at = time.monotonic()
                self._ready(settings)
            if self._restart is not None and self._ready_at is not None:
                return "restart"
            time.sleep(self._poll)
        return None

    def _ready(self, settings: ServerSettings) -> None:
        """Its Server answers: these Settings are good, and Mains may take slots again."""
        if self._runs_pending(settings):  # no fallback any more
            self._failed_pending, self._fallback_reason = None, None
            self._restart = None  # one asked while it started: it runs the pending ones already
        self._good = settings
        if settings.parallel is None:  # llama chose how many
            self._slots = _total_slots(settings.port) or self._slots
        self._release_slots()
        self._write("ready", settings, server_pid=self._server_pid)

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
            if self._stopping or self._human_sleep:
                return False
            time.sleep(min(self._poll, seconds))
        return not (self._stopping or self._human_sleep)

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
            cwd=settings.cwd if settings is not None else None,
            port=settings.port if settings is not None else None,
            last_exit_code=last_exit_code,
            failure=failure,
            reason=reason,
            restart=self._restart if status == "draining" else None,
            going_to_sleep=self._human_sleep and status == "draining",
            slept_by_human=self._human_sleep and status == "asleep",
            fallback_reason=self._fallback_reason,
        )
        write_state(self._servers, self._name, state)


def put_to_sleep(servers: Path, name: SupervisorName) -> bool:
    """Mark a Supervisor that does not run as put to sleep by a human, holding its lock while
    it writes, as the Supervisor would; ``False`` if it runs, and is told by a signal instead."""
    lock = SupervisorLock(lock_path(servers, name))
    try:
        lock.acquire()
    except SupervisorAlreadyRunning:
        return False
    try:
        state = read_state(servers, name) or SupervisorState(status="asleep")
        asleep = SupervisorState(
            status="asleep", arguments=state.arguments, port=state.port, slept_by_human=True
        )
        write_state(servers, name, asleep)
    finally:
        lock.release()
    return True


def _total_slots(port: int) -> int | None:
    try:
        with urllib.request.urlopen(
            f"http://127.0.0.1:{port}/props", timeout=_HEALTH_TIMEOUT
        ) as reply:
            slots = json.load(reply).get("total_slots")
    except (urllib.error.URLError, OSError, ValueError, AttributeError):
        return None
    return slots if isinstance(slots, int) and slots >= 1 else None


def _healthy(port: int) -> bool:
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/health", timeout=_HEALTH_TIMEOUT):
            return True
    except (urllib.error.URLError, OSError):  # not listening yet, or 503 while it loads
        return False
