"""Procesos adheridos (#113, ADR-0012, ADR-0016): Main attaches them from an approved fproc,
watches them and lets them go; what each one does is its fproc's.

``/attach <fproc> [name] [--to=<sid,…>] [--file=<path>]`` starts one, confined for good with
the Alcance its fproc was approved with, plus its shared file. It gets a name, by default its
fproc's, and its own Carpeta de trabajo, ``<data dir>/attached/<Profile>/<name>/``, which
outlives it: attached again with the same name, it finds what it kept there. ``/detach <name>``
lets it go. It dies with Main, and Main keeps the pid of each one in ``state/attached.json``:
on start it closes any a dead Main left alive, checking first in ``/proc/<pid>/cmdline`` that
it is one of its wardens, and tells which ones were lost. Main never starts one again by
itself: one that crashes gives a Notice and its methods fail until a human attaches it again.
One whose fproc a human approves with another Alcance starts again with it, since its encierro
was fixed when it started. Switching Profile detaches the outgoing Profile's.

A shared file is chosen before the process starts. A human's is theirs to name; one a Session
asks for from outside its Carpeta de trabajo waits for a human's Approval, which neither ``-y``
nor ``--devastation`` gives (ADR-0013). It is never AllIn's own.
"""

import contextlib
import os
import signal
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from pydantic import BaseModel, Field, JsonValue, ValidationError

from allin.confinement.launcher import AttachedProcess, FprocStart, ToolLauncher
from allin.confinement.reach import install_root
from allin.core.errors import (
    AllInError,
    AttachedEnded,
    AttachedNameInvalid,
    AttachedNameTaken,
    AttachedNotFound,
    FprocNotOffered,
    HumanOnly,
    InvalidArguments,
    OutsideProfile,
    SessionNotFound,
    SharedFileNotApproved,
    SharedFileRefused,
    ToolNotStarted,
)
from allin.core.files import write_atomically
from allin.core.ids import (
    AttachedName,
    ProfileName,
    SessionId,
    parse_attached_name,
    parse_session_id,
)
from allin.core.origin import HUMAN, Origin
from allin.decorators import Reach
from allin.main.boxes import MainBoxes
from allin.main.events import AttachedProcessEnded, CommandOutcome, MainEvent, OnOutcome
from allin.main.profiles import Profile
from allin.main.quarantine import OfferedFunction, Quarantine
from allin.main.reporting import Reporter
from allin.main.sessions import SessionStore

ATTACH_USAGE = "/attach [<fproc> [name] [--to=<sid,…>] [--file=<path>]]"
DETACH_USAGE = "/detach <name>"
_TO = "--to="
_FILE = "--file="
_RECORDS = "attached.json"  # in ``state/``: the wardens alive, for the next Main to check
_WARDEN = b"allin.confinement.warden"  # in the command line of every warden
_ORPHAN_GRACE = 2.0  # how long a warden a dead Main left gets to take itself down
_SHOWN_STDERR = 300  # of what a crashed one wrote last, what its Notice shows


def _ignored(_: CommandOutcome) -> None:
    pass


class _Record(BaseModel, frozen=True):
    name: AttachedName
    profile: ProfileName
    pid: int  # its warden's, the leader of its process group
    main_pid: int  # the Main that started it, which its warden's command line names


class _Records(BaseModel, frozen=True):
    processes: list[_Record] = Field(default_factory=list[_Record])


@dataclass(frozen=True)
class _Asked:
    """What ``/attach`` was asked, kept so that the same Proceso adherido can start again."""

    fproc: str  # its module
    name: AttachedName
    to: tuple[SessionId, ...]  # the Sessions that get its methods (#114)
    file: Path | None
    origin: Origin


@dataclass
class _Instance:
    asked: _Asked
    profile: ProfileName
    reach: Reach  # what it was confined with when it started
    process: AttachedProcess
    ended: bool = False
    detaching: bool = False


class AttachedProcesses:
    """The Procesos adheridos of the active Profile; only Main's dispatcher changes them."""

    def __init__(
        self,
        launcher: ToolLauncher | None,
        quarantine: Callable[[], Quarantine],
        profile: Callable[[], Profile],
        sessions: SessionStore,
        in_profile: Callable[[SessionId], bool],
        boxes: MainBoxes,
        reporter: Reporter,
        emit: Callable[[MainEvent], None],
        data_dir: Path,
        start_timeout: float = 20.0,
    ) -> None:
        """``launcher`` starts the processes, with a thread as long-lived as Main; without one
        nothing is attached. ``quarantine`` and ``profile`` are the active Profile's;
        ``emit`` takes the event of a process that ended, from any thread."""
        self._launcher = launcher
        self._quarantine = quarantine
        self._profile = profile
        self._sessions = sessions
        self._in_profile = in_profile
        self._boxes = boxes
        self._reporter = reporter
        self._emit = emit
        self._data_dir = data_dir
        self._start_timeout = start_timeout
        self._instances: dict[AttachedName, _Instance] = {}

    def run(self, *options: str, origin: Origin = HUMAN, answer: OnOutcome = _ignored) -> bool:
        """``/attach``: list them, or attach one; ``answer`` hears how it went, maybe once a
        human answered the Approval of its shared file."""
        if not options:
            self._list()
            answer("ok")
            return True
        asked = self._parse(options, origin)
        offered = self._offered(asked.fproc)
        if offered.description.reach.grants and not origin.is_human:  # ADR-0025
            raise HumanOnly(action="/attach", origin=list[JsonValue](origin.labels))
        self._check_free(asked.name)
        if asked.file is None or origin.is_human or self._in_workspace(asked.file, origin):
            self._start(asked, offered)
            answer("ok")
            return True

        def on_approval(yes: bool) -> None:
            try:
                if not yes:
                    raise SharedFileNotApproved(path=str(asked.file))
                self._check_free(asked.name)  # another may have taken it meanwhile
                self._start(asked, self._offered(asked.fproc))
            except AllInError as error:
                answer(error)
            else:
                answer("ok")

        sender = origin.sender or str(origin)
        text = (
            f"{sender} attaches {asked.fproc} as {asked.name}, with a shared file from outside"
            f" its folder:\n{asked.file}\nLet it read and write that file?"
        )
        self._boxes.confirm(None, "Approve shared file", text, on_approval)
        return True

    def detach(self, name: str) -> bool:
        """``/detach``: let one go, with every process it started."""
        instance = self._instances.pop(_name(name), None)
        if instance is None:
            raise AttachedNotFound(name=name)
        self._stop(instance)
        self._save()
        self._reporter.report(None, "detached", f"{name} detached", name=name)
        return True

    def detach_all(self, reason: str) -> None:
        """Let every one go: Main stops, or leaves their Profile."""
        for name, instance in list(self._instances.items()):
            if not instance.ended:
                self._stop(instance)
                shown = f"{name} detached: {reason}"
                self._reporter.report(None, "detached", shown, name=name, reason=reason)
        self._instances.clear()
        self._save()

    def process(self, name: str) -> AttachedProcess:
        """The process its methods are called on. Raises ``AttachedNotFound`` and, for one
        that crashed, ``AttachedEnded``."""
        instance = self._instances.get(_name(name))
        if instance is None:
            raise AttachedNotFound(name=name)
        if instance.ended:
            raise AttachedEnded(exit_code=instance.process.wait())
        return instance.process

    def ended(self, name: AttachedName, pid: int) -> None:
        """Its process ended: one that was not detached crashed, and is not started again."""
        instance = self._instances.get(name)
        if instance is None or instance.process.pid != pid or instance.ended:
            return  # detached, or one that this name had before
        instance.ended = True
        self._save()
        if instance.detaching:
            return
        code = instance.process.wait()
        stderr = instance.process.stderr[-_SHOWN_STDERR:]
        fproc = instance.asked.fproc
        how = _how_it_ended(code)
        self._reporter.report(
            None,
            "attached_crashed",
            f"{name} crashed: {how}",
            name=name,
            fproc=fproc,
            exit_code=code,
            stderr=stderr,
        )
        text = (
            f"{name} ({fproc}) ended by itself: {how}. It does not start again by itself, and"
            f" its methods fail; attach it again with /attach {fproc} {name}."
        )
        if stderr.strip():
            text += f"\n\nIts last error output:\n{stderr}"
        self._boxes.notify(None, f"{name} crashed", text, kind=f"attached:{name}")

    def reach_changed(self) -> None:
        """After an Approval: one whose fproc is now approved with another Alcance starts
        again with it, since its encierro was fixed when it started (ADR-0016)."""
        for name, instance in list(self._instances.items()):
            if instance.ended:
                continue
            try:
                offered = self._offered(instance.asked.fproc)
            except FprocNotOffered:
                continue  # its approved code runs on, as it was approved
            if offered.description.reach == instance.reach:
                continue
            self._stop(instance)
            del self._instances[name]
            try:
                self._start(instance.asked, offered, restarted=True)
            except AllInError as error:
                self._save()
                self._reporter.error(error)

    def close_left_alive(self) -> None:
        """On start: close the ones a dead Main left alive and tell which ones it lost, as part
        of what it left half done (ADR-0012)."""
        records = _load(self._records_path).processes
        if not records:
            return
        for record in records:
            closed = _close_orphan(record.pid, record.main_pid)
            self._reporter.log_main(
                "attached_lost",
                name=record.name,
                profile=record.profile,
                pid=record.pid,
                closed=closed,
            )
        names = ", ".join(f"{r.name} ({r.profile})" for r in records)
        text = (
            f"The last Main stopped without detaching: {names}. What they held in memory is"
            " lost; what they kept in their Carpeta de trabajo is not. Attach them again with"
            " /attach."
        )
        self._boxes.notify(None, "Procesos adheridos lost", text, kind="attached_lost")
        self._save()

    def workspace(self, profile: ProfileName, name: AttachedName) -> Path:
        """Its own Carpeta de trabajo, which outlives it (ADR-0012)."""
        return self._data_dir / "attached" / profile / name

    @property
    def _records_path(self) -> Path:
        return self._data_dir / "state" / _RECORDS

    def _parse(self, options: tuple[str, ...], origin: Origin) -> _Asked:
        words = [o for o in options if not o.startswith("--")]
        to: tuple[SessionId, ...] = ()
        file: Path | None = None
        for option in options:
            if option.startswith(_TO):
                to = self._sessions_named(option.removeprefix(_TO))
            elif option.startswith(_FILE):
                file = self._shared_file(option.removeprefix(_FILE), origin)
            elif option.startswith("--"):
                raise InvalidArguments(usage=ATTACH_USAGE)
        if not 1 <= len(words) <= 2:
            raise InvalidArguments(usage=ATTACH_USAGE)
        fproc = words[0]
        name = _name(words[-1])
        if not origin.is_human and any(sid != origin.sender for sid in to):
            # A Session gives its methods to itself; to others only those it creates (#114).
            raise HumanOnly(action="/attach --to", origin=list[JsonValue](origin.labels))
        return _Asked(fproc, name, to, file, origin)

    def _sessions_named(self, text: str) -> tuple[SessionId, ...]:
        named: list[SessionId] = []
        for word in filter(None, text.split(",")):
            session_id = parse_session_id(word)
            if session_id is None or session_id not in self._sessions.ids():
                raise SessionNotFound(session_id=word)
            if not self._in_profile(session_id):
                raise OutsideProfile(session_id=session_id)
            named.append(session_id)
        return tuple(dict.fromkeys(named))

    def _shared_file(self, text: str, origin: Origin) -> Path:
        """The file ``text`` names, resolved: relative to the asking Session's Carpeta de
        trabajo, or to Main's folder for a human's. Never AllIn's own."""
        base = self._sender_workspace(origin) or Path.cwd()
        path = (base / Path(text).expanduser()).resolve()
        if not path.is_file():
            raise SharedFileRefused(path=text, reason="not a file")
        if self._allins_own(path):
            raise SharedFileRefused(path=text, reason="AllIn's own")
        return path

    def _allins_own(self, path: Path) -> bool:
        """Whether ``path`` is AllIn's: its data folder or its installation, but for what is
        inside a Session's Carpeta de trabajo, also with the data folder inside the
        installation, as ``./var`` is (ADR-0016)."""
        workspaces = self._data_dir.resolve() / "workspaces"
        if path.is_relative_to(workspaces) and len(path.relative_to(workspaces).parts) > 1:
            return False
        return path.is_relative_to(self._data_dir.resolve()) or path.is_relative_to(install_root())

    def _sender_workspace(self, origin: Origin) -> Path | None:
        sender = origin.sender
        if origin.is_human or sender is None or SessionId(sender) not in self._sessions.ids():
            return None
        return self._sessions.workspace(SessionId(sender)).resolve()

    def _in_workspace(self, file: Path, origin: Origin) -> bool:
        workspace = self._sender_workspace(origin)
        return workspace is not None and file.is_relative_to(workspace)

    def _offered(self, fproc: str) -> OfferedFunction:
        """The active Profile's approved fproc ``fproc``, with the Alcance it was approved
        with."""
        for offered in self._quarantine().offered():
            if offered.description.kind == "fproc" and offered.description.module == fproc:
                return offered
        raise FprocNotOffered(fproc=fproc)

    def _check_free(self, name: AttachedName) -> None:
        """One that crashed leaves its name free: attaching it again is how it comes back."""
        instance = self._instances.get(name)
        if instance is not None and not instance.ended:
            raise AttachedNameTaken(name=name)

    def _start(self, asked: _Asked, offered: OfferedFunction, *, restarted: bool = False) -> None:
        if self._launcher is None:
            raise ToolNotStarted(cause="no launcher")
        profile = self._profile().name
        workspace = self.workspace(profile, asked.name)
        try:
            workspace.mkdir(parents=True, exist_ok=True)
        except OSError as error:
            raise ToolNotStarted(cause=type(error).__name__) from error
        described = offered.description
        start = FprocStart(
            offered.package, described.module, asked.name, workspace, described.reach, asked.file
        )
        process = self._launcher.attach(start, self._start_timeout)
        self._instances[asked.name] = _Instance(asked, profile, described.reach, process)
        self._save()
        threading.Thread(
            target=self._watch,
            args=(asked.name, process),
            name=f"attached-{asked.name}",
            daemon=True,
        ).start()
        verb = "started again: its fproc's Alcance changed" if restarted else "attached"
        file = "" if asked.file is None else f" with {asked.file}"
        self._reporter.report(
            None,
            "attached",
            f"{asked.name} ({asked.fproc}){file} {verb}",
            name=asked.name,
            fproc=asked.fproc,
            pid=process.pid,
            file=None if asked.file is None else str(asked.file),
            to=list[JsonValue](asked.to),
            origin=list[JsonValue](asked.origin.labels),
            restarted=restarted,
        )

    def _watch(self, name: AttachedName, process: AttachedProcess) -> None:
        """In its own thread: tell Main once the process ended."""
        process.wait()
        self._emit(AttachedProcessEnded(name, process.pid))

    def _stop(self, instance: _Instance) -> None:
        instance.detaching = True
        instance.process.detach()
        instance.ended = True

    def _list(self) -> None:
        if not self._instances:
            self._reporter.show(None, "no Procesos adheridos")
            return
        for name, instance in self._instances.items():
            asked = instance.asked
            state = (
                f"crashed, {_how_it_ended(instance.process.wait())}"
                if instance.ended
                else f"pid {instance.process.pid}"
            )
            file = "" if asked.file is None else f", file {asked.file}"
            to = "" if not asked.to else f", to {','.join(asked.to)}"
            shown = f"{name}: {asked.fproc}, {state}{file}{to}, folder {self.workspace(instance.profile, name)}"
            self._reporter.show(None, shown)

    def _save(self) -> None:
        records = _Records(
            processes=[
                _Record(name=name, profile=i.profile, pid=i.process.pid, main_pid=os.getpid())
                for name, i in self._instances.items()
                if not i.ended
            ]
        )
        try:
            self._records_path.parent.mkdir(parents=True, exist_ok=True)
            write_atomically(self._records_path, records.model_dump_json(indent=2))
        except OSError as error:
            self._reporter.log_main("attached_records_failed", reason=str(error))


def _how_it_ended(code: int | None) -> str:
    if code is None:
        return "for a reason not known"
    return f"killed by signal {-code}" if code < 0 else f"exit code {code}"


def _name(text: str) -> AttachedName:
    name = parse_attached_name(text)
    if name is None:
        raise AttachedNameInvalid(name=text)
    return name


def _load(path: Path) -> _Records:
    try:
        return _Records.model_validate_json(path.read_bytes())
    except (OSError, ValidationError):
        return _Records()  # none, or unreadable: there is nothing to close


def _close_orphan(pid: int, main_pid: int) -> bool:
    """Close the warden a dead Main left alive, with every process of its group; whether
    there was one. Since that Main died, ``pid`` may belong to an unrelated process now: only
    one whose command line is a warden of that Main is touched."""
    try:
        args = Path(f"/proc/{pid}/cmdline").read_bytes().split(b"\0")
    except OSError:  # gone
        return False
    if not any(_WARDEN in arg for arg in args) or str(main_pid).encode() not in args:
        return False
    try:
        os.kill(pid, signal.SIGTERM)  # its warden takes down what it started
    except ProcessLookupError:
        return False
    deadline = time.monotonic() + _ORPHAN_GRACE
    while _alive(pid) and time.monotonic() < deadline:
        time.sleep(0.05)
    with contextlib.suppress(ProcessLookupError, PermissionError):  # what is left of its group
        os.killpg(pid, signal.SIGKILL)
    return True


def _alive(pid: int) -> bool:
    """Whether ``pid`` is a live process; a zombie has already exited."""
    try:
        stat = Path(f"/proc/{pid}/stat").read_text()
    except OSError:
        return False
    return stat.rpartition(")")[2].split()[0] != "Z"
