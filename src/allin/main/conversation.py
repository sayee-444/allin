"""A Session's Conversation, stored grouped by Turn and flattened into ``input=[...]``.

Saved as ``conversation.json`` in the Session's folder once per finished Turn, atomically.
"""

from pathlib import Path
from typing import Any

from pydantic import BaseModel, ValidationError

from allin.core.errors import SessionStorageFailed, SessionUnreadable
from allin.core.files import write_atomically

_FILE = "conversation.json"

type Item = dict[str, Any]  # a Responses API input/output item, as JSON


class Step(BaseModel, frozen=True):
    """One ``responses.create`` call: the items it produced and its token usage."""

    output: list[Item]
    usage: dict[str, Any] | None = None


class Turn(BaseModel, frozen=True):
    prompt: str
    steps: list[Step]


class Conversation(BaseModel, frozen=True):
    sysprompt: str  # rendered text, so editing the file later does not change this Session
    turns: list[Turn] = []

    def with_turn(self, turn: Turn) -> "Conversation":
        return self.model_copy(update={"turns": [*self.turns, turn]})

    def request_input(self, prompt: str) -> list[Item]:
        """The ``input`` for the first Step of a Turn that answers ``prompt``."""
        items: list[Item] = [{"role": "developer", "content": self.sysprompt}]
        for turn in self.turns:
            items.append(_user(turn.prompt))
            items += [item for step in turn.steps for item in step.output]
        items.append(_user(prompt))
        return items


def _user(text: str) -> Item:
    return {"role": "user", "content": text}


def load_conversation(folder: Path) -> Conversation | None:
    path = folder / _FILE
    try:
        return Conversation.model_validate_json(path.read_bytes())
    except FileNotFoundError:
        return None
    except (OSError, ValidationError) as error:
        raise SessionUnreadable(session_id=folder.name, reason=str(error)) from error


def save_conversation(folder: Path, conversation: Conversation) -> None:
    try:
        write_atomically(folder / _FILE, conversation.model_dump_json(indent=2))
    except OSError as error:
        raise SessionStorageFailed(root=str(folder), reason=str(error)) from error
