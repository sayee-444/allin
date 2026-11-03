"""An fproc's initiative between calls (#123, ADR-0026).

From a thread of its own, a Proceso adherido asks for Command sessions on its own: Main runs each
as its Command main, with the Origin of whoever attached it and the Proceso adherido after it, a
link that never counts as human. Only those that act on its own Sessions run: ``@new`` from the
Moldes of its package, ``@send``, ``@open`` and ``@close``; only those its Alcance declares, and
that every Session of that chain has in its list. Each Command main then keeps the process to
its Subperfil.
"""

import queue
from collections.abc import Callable

from pydantic import JsonValue

from allin.confinement.launcher import OnCommand
from allin.core.errors import (
    AllInError,
    InitiativeRefused,
    InvalidSessionCommand,
    NoSubprofile,
    SessionCommandNotDeclared,
    SessionCommandNotListed,
)
from allin.core.ids import AttachedName, CommandName
from allin.core.origin import from_process
from allin.main.attached import AttachedProcesses
from allin.main.events import (
    CommandOutcome,
    InitiativeAsked,
    MainEvent,
    Queried,
    SessionCommandResult,
)
from allin.main.prompt_commands import session_command_name
from allin.main.reporting import Reporter
from allin.main.session_commands import RunMain, lacking, new_command
from allin.main.sessions import SessionStore

type Answer = Callable[[SessionCommandResult], bool]

_SID_TEXT = "<sid> <text>"


def _one_sid(name: str) -> Callable[[str], str]:
    def translate(args: str) -> str:
        words = args.split()
        if len(words) != 1 or words[0].startswith("-"):
            msg = f"@{name} <sid>"
            raise ValueError(msg)
        return f"/{name} {words[0]}"

    return translate


def _send(args: str) -> str:
    sid, _, text = args.partition(" ")
    if not sid or not text.strip():
        msg = f"@send {_SID_TEXT}"
        raise ValueError(msg)
    return f"/send {sid} {text}"


# What an fproc runs on its own, and the Command main each becomes.
_INITIATIVE: dict[str, Callable[[str], str]] = {
    "new": new_command,
    "send": _send,
    "open": _one_sid("open"),
    "close": _one_sid("close"),
}


def asker(emit: Callable[[MainEvent], None], name: AttachedName) -> OnCommand:
    """What the Lanzador de tools runs, in a thread of its own, for each Command session the
    Proceso adherido ``name`` asks on its own: Main runs it, and this waits for how it went."""

    def on_initiative(text: str) -> JsonValue:
        answers: queue.Queue[SessionCommandResult] = queue.Queue()

        def answer(result: SessionCommandResult) -> bool:
            answers.put(result)
            return True

        emit(InitiativeAsked(name, text, answer))
        return answers.get().as_json()

    return on_initiative


class FprocInitiative:
    """Runs, in Main's dispatcher, what a Proceso adherido asks on its own."""

    def __init__(
        self,
        attached: AttachedProcesses,
        sessions: SessionStore,
        reporter: Reporter,
        run_main: RunMain,
    ) -> None:
        self._attached = attached
        self._sessions = sessions
        self._reporter = reporter
        self._run_main = run_main

    def run(self, name: AttachedName, text: str, answer: Answer) -> bool:
        """Run ``text``, ``@name …``, for the Proceso adherido ``name``; ``answer`` gets how it
        went. Returns whether Main keeps running."""
        try:
            fproc = self._attached.attached_fproc(name)
            if not self._attached.declares_subprofile(name):
                raise NoSubprofile(name=name)  # it created no Session to act on
            origin = from_process(self._attached.attached_by(name), name)
            command = session_command_name(text) or ""
            if command not in fproc.offered.description.reach.commands:
                raise SessionCommandNotDeclared(command=command or text[:40], tool=name)
            translate = _INITIATIVE.get(command)
            if translate is None:
                raise InitiativeRefused(command=command, allowed=list[JsonValue](_INITIATIVE))
            missing = lacking(self._sessions, origin, CommandName(command))
            if missing:
                raise SessionCommandNotListed(command=command, not_in=list[JsonValue](missing))
            args = text.strip().removeprefix(f"@{command}").strip()
            try:
                main_text = translate(args)
            except ValueError as error:
                raise InvalidSessionCommand(command=command, reason=str(error)) from error
        except AllInError as error:
            shown = f"[{name}] {text.strip()[:60]}: error {error}"
            self._reporter.report(
                None, "initiative", shown, name=name, command=text, result=str(error)
            )
            answer(SessionCommandResult(error=str(error)))
            return True
        self._reporter.log_main(
            "initiative",
            name=name,
            command=text,
            runs=main_text,
            origin=list[JsonValue](origin.labels),
        )

        def done(outcome: CommandOutcome) -> None:
            match outcome:
                case "ok":
                    answer(SessionCommandResult())
                case Queried(state=state):
                    answer(SessionCommandResult(state=state))
                case "rejected":
                    answer(SessionCommandResult(error="rejected"))
                case AllInError() as error:
                    answer(SessionCommandResult(error=str(error)))

        return self._run_main(main_text, origin, done)
