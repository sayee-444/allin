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
from allin.core.errors import NoSubprofile, SessionCommandNotDeclared
from allin.core.ids import AttachedName
from allin.core.origin import Origin, from_process
from allin.main.attached import AttachedProcesses
from allin.main.commands import MainCommands
from allin.main.events import InitiativeAsked, MainEvent, OnOutcome, SessionCommandResult
from allin.main.prompt_commands import session_command_name
from allin.main.session_commands import Answer, SessionCommands


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
        session_commands: SessionCommands,
        commands: MainCommands,
    ) -> None:
        """``session_commands`` runs each as a Session's Command sessions run, and ``commands``
        each Command main as asked by the Iniciativa, so the Prompts it queues wait behind the
        humans' and the Sessions' (ADR-0026)."""
        self._attached = attached
        self._session_commands = session_commands
        self._commands = commands

    def run(self, name: AttachedName, text: str, answer: Answer) -> bool:
        """Run ``text``, ``@name …``, for the Proceso adherido ``name``; ``answer`` gets how it
        went. Returns whether Main keeps running."""

        def checked() -> Origin:
            return self._origin(name, text)

        def run_main(main_text: str, origin: Origin, done: OnOutcome) -> bool:
            return self._commands.run(main_text, origin, done, initiative=True)

        return self._session_commands.run_initiative(name, text, checked, answer, run_main)

    def _origin(self, name: AttachedName, text: str) -> Origin:
        """The Origin of what ``name`` asks on its own: who attached it, then the process.
        Raises if its fproc declares no Subperfil, which leaves it no Session to act on, or
        names no such Command session in its Alcance."""
        fproc = self._attached.attached_fproc(name)
        if not self._attached.declares_subprofile(name):
            raise NoSubprofile(name=name)
        command = session_command_name(text) or ""
        if command not in fproc.offered.description.reach.commands:
            raise SessionCommandNotDeclared(command=command or text[:40], tool=name)
        return from_process(self._attached.attached_by(name), name)
