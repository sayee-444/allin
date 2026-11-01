"""Logs: dated events of Main or of one Session, one JSON object per line, append only.

Always on. Main's Log and each Session's Log are independent files; only the dispatcher writes
them (ADR-0001).
"""

import json
from contextlib import suppress
from datetime import datetime
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, JsonValue, ValidationError

from allin.core.errors import LogUnreadable, LogWriteFailed


class LogEntry(BaseModel, frozen=True):
    """One event; its own fields sit next to ``ts`` and ``event`` in the same JSON object."""

    model_config = ConfigDict(extra="allow")

    ts: datetime
    event: str

    @property
    def fields(self) -> dict[str, Any]:
        return dict(self.model_extra or {})


class Log:
    def __init__(self, path: Path) -> None:
        self.path = path

    def append(self, event: str, **fields: JsonValue) -> None:
        line = {"ts": datetime.now().astimezone().isoformat(), "event": event, **fields}
        try:
            with self.path.open("a", encoding="utf-8") as file:
                file.write(json.dumps(line, ensure_ascii=False) + "\n")
        except OSError as error:
            raise LogWriteFailed(path=str(self.path), reason=str(error)) from error


def read_log(path: Path) -> list[LogEntry]:
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except FileNotFoundError:
        return []
    except (OSError, UnicodeDecodeError) as error:
        raise LogUnreadable(path=str(path), reason=str(error)) from error
    entries: list[LogEntry] = []
    for line in lines:
        with suppress(ValidationError):  # a line cut short by a crash while it was being written
            entries.append(LogEntry.model_validate_json(line))
    return entries
