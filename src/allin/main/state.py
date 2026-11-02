"""MainState: what Main needs to put the user back where they were after a restart.

Saved after every change, so it survives a Main killed outright. Sessions keep their own data
(Conversation, Log, Settings); this only holds what lives in Main's memory.
"""

from dataclasses import replace
from pathlib import Path

from pydantic import BaseModel, Field, ValidationError, field_validator

from allin.core.errors import MainStateStorageFailed, MainStateUnreadable
from allin.core.files import write_atomically
from allin.core.ids import SessionId
from allin.main.prompts import Prompt


class MainState(BaseModel, frozen=True):
    # Open Windows, in the order they opened, with their xterm's pid (ADR-0007).
    windows: dict[SessionId, int] = Field(default_factory=dict[SessionId, int])
    # Each Session's Draft, shown block first (``core.drafts``).
    drafts: dict[SessionId, list[str]] = Field(default_factory=dict[SessionId, list[str]])
    queue: list[Prompt] = Field(default_factory=list[Prompt])  # waiting, in queue order
    running: list[Prompt] = Field(default_factory=list[Prompt])  # their Turns had started

    def renamed(self, old: SessionId, new: SessionId) -> "MainState":
        """The same MainState, with a Session renamed (#59)."""

        def key(session_id: SessionId) -> SessionId:
            return new if session_id == old else session_id

        def prompts(listed: list[Prompt]) -> list[Prompt]:
            return [replace(p, session_id=key(p.session_id)) for p in listed]

        return MainState(
            windows={key(sid): pid for sid, pid in self.windows.items()},
            drafts={key(sid): blocks for sid, blocks in self.drafts.items()},
            queue=prompts(self.queue),
            running=prompts(self.running),
        )

    @field_validator("drafts", mode="before")
    @classmethod
    def _drafts_saved_as_text(cls, drafts: object) -> object:
        """A Main from before Drafts had blocks saved each one as a single text."""
        if not isinstance(drafts, dict):
            return drafts
        return {
            session_id: [draft] if isinstance(draft, str) else draft
            for session_id, draft in drafts.items()  # pyright: ignore[reportUnknownVariableType]
        }


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
