"""MainState: what Main needs to put the user back where they were after a restart.

Saved after every change, so it survives a Main killed outright. Sessions keep their own data
(Conversation, Log, Settings); this only holds what lives in Main's memory.
"""

from pathlib import Path

from pydantic import BaseModel, Field, ValidationError

from allin.core.errors import MainStateStorageFailed, MainStateUnreadable
from allin.core.files import write_atomically
from allin.main.prompts import Prompt


class MainState(BaseModel, frozen=True):
    # Open Windows, in the order they opened, with their xterm's pid (ADR-0007).
    windows: dict[str, int] = Field(default_factory=dict[str, int])
    drafts: dict[str, str] = Field(default_factory=dict[str, str])  # each Session's input
    queue: list[Prompt] = Field(default_factory=list[Prompt])  # waiting, in queue order
    running: list[Prompt] = Field(default_factory=list[Prompt])  # their Turns had started


class MainStateStore:
    def __init__(self, path: Path) -> None:
        self._path = path

    def load(self) -> MainState:
        """An empty MainState for a Main that never saved one."""
        try:
            return MainState.model_validate_json(self._path.read_bytes())
        except FileNotFoundError:
            return MainState()
        except (OSError, ValidationError) as error:
            raise MainStateUnreadable(path=str(self._path), reason=str(error)) from error

    def save(self, state: MainState) -> None:
        try:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            write_atomically(self._path, state.model_dump_json(indent=2))
        except OSError as error:
            raise MainStateStorageFailed(path=str(self._path), reason=str(error)) from error
