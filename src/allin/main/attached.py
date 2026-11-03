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

import os
import threading
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field, JsonValue, ValidationError

from allin.confinement.launcher import (
    AttachedProcess,
    FprocStart,
    OnCommand,
    ToolLauncher,
    close_left_warden,
)
from allin.confinement.reach import install_root
from allin.core.errors import (
    AllInError,
    AttachedMethodNotFound,
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
    FprocName,
    ProfileName,
    Revision,
    SessionId,
    parse_attached_name,
    parse_session_id,
)
from allin.core.origin import HUMAN, Origin
from allin.decorators import CONFIRM, HUMAN_ONLY, at_once, has_subprofile
from allin.main.boxes import MainBoxes
from allin.main.descriptions import ToolSchema
from allin.main.events import AttachedProcessEnded, CommandOutcome, MainEvent, OnOutcome
from allin.main.profiles import Profile
from allin.main.quarantine import OfferedFunction, Quarantine
from allin.main.reporting import Reporter
from allin.main.secrets import SecretName
from allin.main.sessions import SessionStore
from allin.main.status import AttachedView

ATTACH_USAGE = "/attach [<fproc> [name] [--to=<sid,…>] [--file=<path>]]"
DETACH_USAGE = "/detach <name>"
TO = "--to="  # also @attach's, which gives the methods to its own Session
_FILE = "--file="
_RECORDS = "attached.json"  # in ``state/``: the wardens alive, for the next Main to check
_SHOWN_STDERR = 300  # of what a crashed one wrote last, what its Notice shows


# Why every Proceso adherido is let go at once.
type DetachReason = Literal["main stopped", "profile switched"]


def _ignored(_: CommandOutcome) -> None:
    pass


def _nobody(*_: object) -> None:
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

    fproc: FprocName
    name: AttachedName
    to: tuple[SessionId, ...]  # the Sessions that get its methods (#114)
    file: Path | None
    origin: Origin


class Revisions:
    """The revision of one Proceso adherido that each Session saw last, by the Turn it saw it
    in: a Rewind forgets those seen since (ADR-0012). Safe from any thread: a Turn's thread
    notes what each call saw at once, since its next call, in the same Step or the next, checks
    it; going through Main's dispatcher would let that next call run first."""

    def __init__(self) -> None:
        self._seen: dict[SessionId, list[tuple[int, Revision]]] = {}
        self._lock = threading.Lock()

    def seen(self, session_id: SessionId) -> Revision:
        """The last one the Session saw; ``NEVER_SEEN`` if it saw none."""
        with self._lock:
            seen = self._seen.get(session_id)
            return seen[-1][1] if seen else NEVER_SEEN

    def saw(self, session_id: SessionId, turn: int, revision: Revision | None) -> None:
        """The Session saw ``revision`` in its Turn ``turn``; nothing for ``None``."""
        if revision is not None:
            with self._lock:
                self._seen.setdefault(session_id, []).append((turn, revision))

    def forget(self, session_id: SessionId, turns: int) -> None:
        """The Session went back to its first ``turns`` Turns."""
        with self._lock:
            kept = [(t, r) for t, r in self._seen.get(session_id, []) if t < turns]
            self._seen[session_id] = kept


NEVER_SEEN = Revision(-1)  # one no process has: an ``@writes`` from it is always refused


@dataclass(frozen=True)
class AttachedFproc:
    """A Proceso adherido of the active Profile, attached or crashed, as its methods see it."""

    name: AttachedName
    offered: OfferedFunction  # its fproc, as it was approved when it started
    process: AttachedProcess
    revisions: Revisions  # this process's, seen by each Session

    @property
    def methods(self) -> tuple[ToolSchema, ...]:
        """Its fproc's public methods, as its description read them."""
        return self.offered.description.tools

    def method(self, name: str) -> ToolSchema:
        """Raises ``AttachedMethodNotFound``."""
        for schema in self.methods:
            if schema.name == name:
                return schema
        raise AttachedMethodNotFound(name=self.name, method=name)

    def confirms(self, method: ToolSchema) -> bool:
        """Whether a call to ``method`` asks a Confirmation: ``@confirm``, or its Approval."""
        return method.name in self.offered.confirm or method.marks.get(CONFIRM) is True

    def tool_name(self, method: ToolSchema) -> str:
        """``<name>_<method>``, as ``tools=[...]`` names it: a Session may have two Procesos
        adheridos of one fproc, and a tool's name takes no dot (ADR-0021)."""
        return f"{self.name}_{method.name}"


def human_only(method: ToolSchema) -> bool:
    """Whether ``method`` is marked ``@human``: a human's alone, never a tool."""
    return method.marks.get(HUMAN_ONLY) is True


@dataclass
class _Instance:
    asked: _Asked
    profile: ProfileName
    offered: OfferedFunction  # its fproc as it started: its Alcance and its methods
    process: AttachedProcess
    ended: bool = False  # it crashed, or was detached
    revisions: Revisions = field(default_factory=Revisions)


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
        secrets_of: Callable[[AttachedName], Mapping[SecretName, str]],
        start_timeout: float = 20.0,
        release: Callable[[], float] = lambda: 5.0,
    ) -> None:
        """``launcher`` starts the processes, with a thread as long-lived as Main; without one
        nothing is attached. ``quarantine`` and ``profile`` are the active Profile's;
        ``emit`` takes the event of a process that ended, from any thread; ``secrets_of``
        gives an instance's Secretos, read anew each time it is attached (#124)."""
        self._launcher = launcher
        self._quarantine = quarantine
        self._profile = profile
        self._sessions = sessions
        self._in_profile = in_profile
        self._boxes = boxes
        self._reporter = reporter
        self._emit = emit
        self._data_dir = data_dir
        self._secrets_of = secrets_of
        self._start_timeout = start_timeout
        self._release = release  # Main's Setting, read as each one is attached
        self._given: Callable[[AttachedName, tuple[SessionId, ...]], None] = _nobody
        self._taken: Callable[[AttachedName], None] = _nobody
        self._asker: Callable[[AttachedName], OnCommand | None] = lambda _: None
        self._subprofile: Callable[[AttachedName, FprocName], None] = _nobody
        self._instances: dict[AttachedName, _Instance] = {}

    def listen(
        self,
        given: Callable[[AttachedName, tuple[SessionId, ...]], None],
        taken: Callable[[AttachedName], None],
    ) -> None:
        """``given`` hears the Sessions that get a new one's methods, and ``taken`` that one was
        detached, so no Session keeps them (#114)."""
        self._given = given
        self._taken = taken

    def act_through(
        self,
        asker: Callable[[AttachedName], OnCommand],
        subprofile: Callable[[AttachedName, FprocName], None],
    ) -> None:
        """``asker`` gives what runs the Command sessions an instance asks on its own, between
        calls; ``subprofile`` hears that one whose fproc declares ``@subprofile`` was attached,
        so it has its Subperfil (ADR-0026)."""
        self._asker = asker
        self._subprofile = subprofile

    def declares_subprofile(self, name: AttachedName) -> bool:
        """Whether the Proceso adherido ``name`` runs now and its fproc declares
        ``@subprofile`` (ADR-0026)."""
        instance = self._instances.get(name)
        return instance is not None and has_subprofile(instance.offered.description.marks)

    def attached_by(self, name: AttachedName) -> Origin:
        """Who attached the Proceso adherido ``name``: what it does on its own goes with that
        chain, and never with more (ADR-0026). Raises ``AttachedNotFound``."""
        instance = self._instances.get(_name(name))
        if instance is None:
            raise AttachedNotFound(name=name)
        return instance.asked.origin

    def run(self, *options: str, origin: Origin = HUMAN, answer: OnOutcome = _ignored) -> bool:
        """``/attach``: list them, or attach one; ``answer`` hears how it went, maybe once a
        human answered the Approval of its shared file."""
        if not options:
            self._list()
            answer("ok")
            return True
        asked = self._parse(options, origin)
        offered = self._offered_to(asked)
        self._check_free(asked)
        if asked.file is None or origin.is_human or self._in_workspace(asked.file, origin):
            self._start(asked, offered)
            answer("ok")
            return True

        def on_approval(yes: bool) -> None:
            try:
                if not yes:
                    raise SharedFileNotApproved(path=str(asked.file))
                self._check_free(asked)  # another may have taken it meanwhile
                self._start(asked, self._offered_to(asked))  # it may have changed meanwhile
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
        self._taken(instance.asked.name)
        return True

    def detach_all(self, reason: DetachReason) -> None:
        """Let every one go: Main stops, or leaves their Profile."""
        names = list(self._instances)
        for name, instance in list(self._instances.items()):
            if not instance.ended:
                self._stop(instance)
                shown = f"{name} detached: {reason}"
                self._reporter.report(None, "detached", shown, name=name, reason=reason)
        self._instances.clear()
        self._save()
        for name in names:
            self._taken(name)

    def attached_fproc(self, name: str) -> AttachedFproc:
        """The Proceso adherido ``name``, attached or crashed: a crashed one's calls raise
        ``AttachedEnded``. Raises ``AttachedNotFound``."""
        instance = self._instances.get(_name(name))
        if instance is None:
            raise AttachedNotFound(name=name)
        return AttachedFproc(
            instance.asked.name, instance.offered, instance.process, instance.revisions
        )

    def views(self) -> list[AttachedView]:
        """Consultar's (#118): each Proceso adherido and the methods it offers."""
        return [
            AttachedView(name=f.name, methods=tuple(m.name for m in f.methods))
            for f in self.fprocs()
        ]

    def fprocs(self) -> list[AttachedFproc]:
        """Every Proceso adherido of the active Profile, attached or crashed."""
        return [self.attached_fproc(name) for name in self._instances]

    def ended(self, name: AttachedName, pid: int) -> None:
        """Its process ended: one that was not detached crashed, and is not started again."""
        instance = self._instances.get(name)
        if instance is None or instance.process.pid != pid or instance.ended:
            return  # detached, or one that this name had before
        instance.ended = True
        self._save()
        code = instance.process.wait()
        stderr = instance.process.stderr[-_SHOWN_STDERR:]
        fproc = instance.asked.fproc
        how = _how_it_ended(code)
        hung = instance.process.hung
        if hung is not None:
            how = f"AllIn killed it, {hung}"
        self._reporter.report(
            None,
            "attached_crashed",
            f"{name} crashed: {how}",
            name=name,
            fproc=fproc,
            exit_code=code,
            stderr=stderr,
            killed=hung,
        )
        text = (
            f"{name} ({fproc}) ended: {how}. It does not start again by itself, and"
            f" its methods fail; a human attaches it again with {_again(instance.asked)}."
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
            if offered.description.reach == instance.offered.description.reach:
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
            closed = close_left_warden(record.pid, record.main_pid)
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
            if option.startswith(TO):
                to = self._sessions_named(option.removeprefix(TO))
            elif option.startswith(_FILE):
                file = self._shared_file(option.removeprefix(_FILE), origin)
            elif option.startswith("--"):
                raise InvalidArguments(usage=ATTACH_USAGE)
        if not 1 <= len(words) <= 2:
            raise InvalidArguments(usage=ATTACH_USAGE)
        fproc = FprocName(words[0])
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

    def _offered_to(self, asked: _Asked) -> OfferedFunction:
        """The fproc ``asked`` names, if its Origin may attach it: one that declares Otorgar
        only a human attaches (ADR-0025)."""
        offered = self._offered(asked.fproc)
        if offered.description.reach.grants and not asked.origin.is_human:
            raise HumanOnly(action="/attach", origin=list[JsonValue](asked.origin.labels))
        return offered

    def _offered(self, fproc: FprocName) -> OfferedFunction:
        """The active Profile's approved fproc ``fproc``, with the Alcance it was approved
        with."""
        for offered in self._quarantine().offered():
            if offered.description.kind == "fproc" and offered.description.module == fproc:
                return offered
        raise FprocNotOffered(fproc=fproc)

    def _check_free(self, asked: _Asked) -> None:
        """One that crashed leaves its name free for a human: attaching it again is how it
        comes back, and only a human does it (ADR-0012)."""
        instance = self._instances.get(asked.name)
        if instance is None:
            return
        if not instance.ended:
            raise AttachedNameTaken(name=asked.name)
        if not asked.origin.is_human:
            raise HumanOnly(action="/attach", origin=list[JsonValue](asked.origin.labels))

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
        if has_subprofile(described.marks):
            self._subprofile(asked.name, FprocName(described.module))  # before it may act
        start = FprocStart(
            offered.package,
            FprocName(described.module),
            asked.name,
            workspace,
            described.reach,
            asked.file,
            at_once(described.marks),
            dict(self._secrets_of(asked.name).items()),  # their names as the channel's str
        )
        name = asked.name
        process = self._launcher.attach(
            start,
            self._start_timeout,
            lambda ended: self._emit(AttachedProcessEnded(name, ended.pid)),
            self._release(),
            self._asker(name),
        )
        self._instances[name] = _Instance(asked, profile, offered, process)
        self._save()
        self._given(name, asked.to)
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

    def _stop(self, instance: _Instance) -> None:
        instance.ended = True  # so its end, when Main hears it, is no crash
        instance.process.detach()

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


def _again(asked: _Asked) -> str:
    """The ``/attach`` that brings it back as it was."""
    words = ["/attach", asked.fproc, asked.name]
    if asked.to:
        words.append(f"{TO}{','.join(asked.to)}")
    if asked.file is not None:
        words.append(f"{_FILE}{asked.file}")
    return " ".join(words)


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
