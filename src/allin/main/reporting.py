"""What Main tells: its Chat, its Log and each Session's Log.

High-level events go to Main's Log and, when they concern a Session, to its Log too; Main's
Chat shows them prefixed with ``[sid]``. Raw stream events go only to Main's Log, and to Main's
Chat for Sessions under ``/raw on``; streamed text is in neither Log.
"""

import json
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from pydantic import JsonValue

from allin.core.errors import AllInError
from allin.core.ids import SessionId
from allin.main.log import Log
from allin.main.prompt_commands import read_secret
from allin.main.sessions import SessionStore


@dataclass
class _RawFilter:
    """Which Sessions show their raw stream events in Main's Chat (``/raw``)."""

    everyone: bool = False
    shown: set[SessionId] = field(default_factory=set[SessionId])
    hidden: set[SessionId] = field(default_factory=set[SessionId])  # exceptions while ``everyone``

    def switch(self, on: bool, session_id: SessionId | None) -> None:
        if session_id is None:
            self.everyone = on
            self.shown.clear()
            self.hidden.clear()
        elif on:
            self.shown.add(session_id)
            self.hidden.discard(session_id)
        else:
            self.shown.discard(session_id)
            self.hidden.add(session_id)

    def __contains__(self, session_id: SessionId) -> bool:
        if self.everyone:
            return session_id not in self.hidden
        return session_id in self.shown


class Reporter:
    def __init__(
        self, main_chat: Callable[[str], None], main_log: Log, sessions: SessionStore
    ) -> None:
        self._main_chat = main_chat
        self._main_log = main_log
        self._sessions = sessions
        self._raw = _RawFilter()

    def report(self, session_id: str | None, event: str, shown: str, **fields: JsonValue) -> None:
        """Show a high-level event in Main's Chat and keep it in Main's Log. ``session_id`` is
        as given: a Command may name a Session that does not exist."""
        self.log_main(event, **({"sid": session_id} if session_id else {}), **fields)
        self.show(session_id, shown)

    def show(self, session_id: str | None, shown: str) -> None:
        # ``str``: like ``report``, a Command may name a Session that does not exist.
        """Show a line in Main's Chat that is no event, such as what ``/set`` lists."""
        self._main_chat(f"[{session_id}] {shown}" if session_id else shown)

    def record(self, session_id: SessionId, event: str, shown: str, **fields: JsonValue) -> None:
        """A high-level event of a Session: its Log, Main's Log and Main's Chat."""
        self.log_session(session_id, event, **fields)
        self.report(session_id, event, shown, **fields)

    def error(self, error: AllInError, session_id: SessionId | None = None) -> None:
        if session_id is None:
            self.report(None, "error", f"error {error}", error=str(error))
        else:
            self.record(session_id, "error", f"error {error}", error=str(error))

    def raw(self, session_id: SessionId, data: dict[str, Any]) -> None:
        """A raw stream event of a Session's Turn."""
        self.log_main("raw", sid=session_id, data=data)
        if session_id in self._raw:
            self._main_chat(f"[{session_id}] raw {json.dumps(data, ensure_ascii=False)}")

    def show_raw(self, on: bool, session_id: SessionId | None) -> None:
        """Whether Main's Chat shows the raw stream events of a Session, or of all of them."""
        self._raw.switch(on, session_id)

    def log_main(self, event: str, **fields: JsonValue) -> None:
        """Keep an event in Main's Log only."""
        try:
            self._main_log.append(event, **_kept(fields))
        except AllInError as error:  # never logged again, or a broken Log would loop
            self._main_chat(f"error {error}")

    def log_session(self, session_id: SessionId, event: str, **fields: JsonValue) -> None:
        """Keep an event in a Session's Log only."""
        try:
            self._sessions.log(session_id).append(event, **_kept(fields))
        except AllInError as error:
            self.report(session_id, "error", f"error {error}", error=str(error))


# The fields that hold a command's line, as typed or as it runs.
_COMMAND_FIELDS = ("command", "runs")


def _kept(fields: dict[str, JsonValue]) -> dict[str, JsonValue]:
    """What a Log keeps of an event: of a command's line, never a Secreto's value typed in it
    by mistake, whoever wrote it there (ADR-0026, #195)."""
    return {
        key: read_secret(value).kept if key in _COMMAND_FIELDS and isinstance(value, str) else value
        for key, value in fields.items()
    }
