"""What a Command session module in ``<data-dir>/comsession/`` imports to declare one (ADR-0025).

A Command session is what a Window takes as ``@name …``: an input with nothing else, which
starts no Turn and becomes exactly one Command main. Its module is part of Main, like the
Notice modules: it runs inside Main, with no enclosure and no quarantine, so only the human
writes it; every Profile shares it and Main loads it when it starts (ADR-0016). For example::

    from allin.session_commands import SessionCommandCall, session_command

    @session_command("note", usage="@note <text>", summary="send this Session a Prompt")
    def note(call: SessionCommandCall) -> str:
        return f"/send {call.session_id} {call.args}"

The modules at the folder's root are the ones a new Session gets by default; one in a subfolder
only if ``/new`` names it (``-cs=``), by its declared name.
"""

from collections.abc import Callable
from dataclasses import dataclass


@dataclass(frozen=True)
class SessionCommandCall:
    """A Command session as a Window, or a tool of its Session, asked for it."""

    session_id: str  # the Session it was asked in
    args: str  # what follows its name, as written
    server: str | None  # the Supervisor of the Session's Enlace, if it has one (ADR-0022)


# Gives the one Command main a Command session becomes; raises ``ValueError`` with why its
# arguments do not make one.
type Translate = Callable[[SessionCommandCall], str]


@dataclass(frozen=True)
class SessionCommandType:
    name: str
    usage: str
    summary: str
    translate: Translate


# Filled while Main loads the modules; read by ``allin.main.session_commands``.
declared: list[SessionCommandType] = []


def session_command(name: str, *, usage: str, summary: str) -> Callable[[Translate], Translate]:
    """Declare a Command session, ``@name``; ``usage`` is what an error in its arguments
    shows, and ``summary`` what a listing of them says."""

    def register(translate: Translate) -> Translate:
        declared.append(SessionCommandType(name, usage, summary, translate))
        return translate

    return register
