"""Which Sessions have the methods of the Procesos adheridos, and the tools those make (#114,
ADR-0021).

A Session gets a Proceso adherido's methods as a block, by its name:
- when it attaches it, or when a human attaches it ``--to`` it;
- when a human gives them with ``/func add <sid> attached:<name>``;
- when it is born with them, with ``/new -am=<name,…>``; a Session passes on only those that
  every Session of its chain has.

A human takes them away from any Session with ``/func remove``, and a Session from itself.
Detaching the process takes them from every Session. A Rewind to before the Prompt in which a
Session got them takes them away too, without detaching the process. One that crashed keeps
them, and its calls fail.

In ``tools=[...]`` each method is ``<name>_<method>``; one marked ``@human`` never goes there.
"""

from collections.abc import Callable
from dataclasses import dataclass

from pydantic import JsonValue

from allin.confinement.launcher import OnCommand, ToolOutcome
from allin.core.errors import AllInError, HumanOnly, InvalidArguments
from allin.core.ids import AttachedName, SessionId, parse_attached_name
from allin.core.origin import Origin
from allin.main.attached import AttachedFproc, AttachedProcesses, human_only
from allin.main.conversation import Item
from allin.main.descriptions import ToolSchema
from allin.main.events import MainEvent, MethodProgressed
from allin.main.func_commands import ATTACHED
from allin.main.notifications import FUNC, Notifications, told_line
from allin.main.reporting import Reporter
from allin.main.sessions import Change, HeldMethods, Session, SessionStore
from allin.main.tool_steps import AttachedMethod, CallFrame, SessionTool


class AttachedMethods:
    """Who has the methods of the active Profile's Procesos adheridos; only Main's dispatcher
    changes it."""

    def __init__(
        self,
        attached: AttachedProcesses,
        sessions: SessionStore,
        in_profile: Callable[[SessionId], bool],
        busy: Callable[[SessionId], bool],
        notifications: Notifications,
        reporter: Reporter,
        emit: Callable[[MainEvent], None],
    ) -> None:
        """``busy``: whether a Session's Turn is under way, so what it gets now belongs to that
        Prompt; ``emit`` takes what a method tells of its progress, from its call's thread."""
        self._attached = attached
        self._sessions = sessions
        self._in_profile = in_profile
        self._busy = busy
        self._notifications = notifications
        self._reporter = reporter
        self._emit = emit

    def given(self, name: AttachedName, to: tuple[SessionId, ...]) -> None:
        """``name`` was attached for ``to``: each of them gets its methods."""
        for session_id in to:
            self._give(self._sessions.load(session_id), name)

    def taken(self, name: AttachedName) -> None:
        """``name`` was detached: no Session of the active Profile keeps its methods."""
        for session_id in self._sessions.ids():
            if self._in_profile(session_id):
                session = self._sessions.load(session_id)
                if name in held_names(session):
                    self._take(session, name)

    def change(self, session: Session, named: list[str], *, given: bool, origin: Origin) -> None:
        """``/func add|remove <sid> attached:<name>…``: a human gives or takes them; a Session
        only gives them up itself (ADR-0021)."""
        if not origin.is_human and (given or session.id != origin.sender):
            raise HumanOnly(action=f"/func {'add' if given else 'remove'}", origin=_labels(origin))
        names = [_name(word.removeprefix(ATTACHED)) for word in named]
        for name in names:
            if given:
                self._attached.attached_fproc(name)  # it must be attached in this Profile
                self._give(session, name)
            elif name in held_names(session):
                self._take(session, name)
            session = self._sessions.load(session.id)
        self._reporter.record(
            session.id,
            "methods_given" if given else "methods_taken",
            f"Procesos adheridos: {', '.join(held_names(session)) or 'none'}",
            names=list[JsonValue](names),
            origin=_labels(origin),
        )

    def born_with(self, text: str, chain: list[Session | None] | None) -> tuple[AttachedName, ...]:
        """``/new -am=<name,…>``: the Procesos adheridos whose methods a new Session gets.
        Created through a ``chain`` of Sessions (``None`` for a human), only those every one
        of them has. Raises ``AttachedNotFound`` for one not attached in this Profile."""
        names = tuple(dict.fromkeys(_name(word) for word in text.split(",") if word))
        for name in names:
            self._attached.attached_fproc(name)
        if chain is None:
            return names
        return tuple(n for n in names if all(s is not None and n in held_names(s) for s in chain))

    def given_at_birth(self, session: Session, names: tuple[AttachedName, ...]) -> None:
        """A new Session gets the methods ``born_with`` chose, untold: it has had no Turn."""
        held = tuple(HeldMethods(name=name, kept_from=0) for name in names)
        if held:
            self._sessions.save_attached_methods(session, held)

    def rewound(self, session: Session, turns: int) -> None:
        """The Session went back to its first ``turns`` Turns: the methods it got in a later
        one, or after them, go away."""
        for held in session.attached_methods:
            if held.kept_from > turns:
                self._take(self._sessions.load(session.id), held.name)
        for attached in self._attached.fprocs():
            attached.revisions.forget(session.id, turns)

    def tools(self, session: Session) -> list[SessionTool]:
        """The methods its LLM gets, as tools, for the Turn about to start: those of each
        Proceso adherido it has that is attached, or crashed, whose calls fail; never one
        marked ``@human``. Each call notes the revision it saw for the Session, and an
        ``@writes`` checks it."""
        conversation = self._sessions.load_conversation(session)
        caller = _Caller(session.id, 0 if conversation is None else conversation.next_turn)
        tools: list[SessionTool] = []
        for name in held_names(session):
            try:
                attached = self._attached.attached_fproc(name)
            except AllInError:
                continue  # detached meanwhile, or another Profile's
            tools.extend(
                self._tool(attached, m, caller) for m in attached.methods if not human_only(m)
            )
        return tools

    def _tool(self, attached: AttachedFproc, method: ToolSchema, caller: "_Caller") -> SessionTool:
        name = attached.tool_name(method)
        schema: Item = {
            "type": "function",
            "name": name,
            "description": method.description,
            "parameters": method.parameters,
        }
        described = attached.offered.description
        called = f"{attached.name}.{method.name}"

        def call(
            arguments: dict[str, JsonValue],
            timeout: float,
            stop: Callable[[], bool],
            on_command: OnCommand,
            frame: CallFrame,
        ) -> ToolOutcome:
            revisions = attached.revisions
            outcome = attached.process.call(
                method.name,
                arguments,
                timeout,
                on_command,
                stop,
                lambda text: self._emit(MethodProgressed(caller.session_id, f"{called}: {text}")),
                revisions.seen(caller.session_id),
                on_announce=frame.on_announce,
                prompt_id=frame.prompt_id,
            )
            revisions.saw(caller.session_id, caller.turn, outcome.revision)
            return outcome

        return SessionTool(
            name,
            attached.offered.package,
            described.module,
            described.reach,
            schema,
            attached.confirms(method),
            AttachedMethod(attached.name, call),
        )

    def _give(self, session: Session, name: AttachedName) -> None:
        if name in held_names(session):
            return
        held = HeldMethods(name=name, kept_from=self._turns(session))
        self._sessions.save_attached_methods(session, (*session.attached_methods, held))
        self._tell(session, name, given=True)

    def _take(self, session: Session, name: AttachedName) -> None:
        kept = tuple(held for held in session.attached_methods if held.name != name)
        self._sessions.save_attached_methods(session, kept)
        self._tell(session, name, given=False)

    def _turns(self, session: Session) -> int:
        """How many Turns a Rewind must keep for what the Session gets now to stay: those done,
        and the one under way."""
        conversation = self._sessions.load_conversation(session)
        done = 0 if conversation is None else conversation.next_turn
        return done + (1 if self._busy(session.id) else 0)

    def _tell(self, session: Session, name: AttachedName, *, given: bool) -> None:
        change = Change(subject=f"{ATTACHED}{name}", given=given)
        self._notifications.leave(session.id, FUNC, told_line(change), change)


def held_names(session: Session) -> tuple[AttachedName, ...]:
    """The Procesos adheridos whose methods the Session has."""
    return tuple(held.name for held in session.attached_methods)


@dataclass(frozen=True)
class _Caller:
    """Whose LLM a method's tool serves, and in which Turn."""

    session_id: SessionId
    turn: int


def _name(text: str) -> AttachedName:
    name = parse_attached_name(text)
    if name is None:
        raise InvalidArguments(usage=f"{ATTACHED}<name>")
    return name


def _labels(origin: Origin) -> list[JsonValue]:
    return list[JsonValue](origin.labels)
