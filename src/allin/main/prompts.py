"""Main's queue of Prompts waiting for a Turn (ADR-0004).

Only the dispatcher touches it, so it needs no locking.
"""

from collections.abc import Container
from dataclasses import dataclass

from allin.core.ids import SessionId


@dataclass(frozen=True)
class Prompt:
    session_id: SessionId
    text: str


class PromptQueue:
    def __init__(self) -> None:
        self._prompts: list[Prompt] = []

    def push(self, prompt: Prompt) -> None:
        self._prompts.append(prompt)

    def push_front(self, prompt: Prompt) -> None:
        """Put a Prompt back first: a Step a Server left half-done goes before the rest."""
        self._prompts.insert(0, prompt)

    def waiting(self, active: Container[str]) -> list[Prompt]:
        """The Prompts whose Session has no active Turn, in queue order."""
        return [prompt for prompt in self._prompts if prompt.session_id not in active]

    def remove(self, prompt: Prompt) -> None:
        """Take out the first Prompt equal to ``prompt``: the one ``waiting`` gave first."""
        self._prompts.remove(prompt)

    def drop(self, session_id: SessionId) -> list[Prompt]:
        """Take a Session's Prompts out of the queue; return them in their order."""
        dropped = [prompt for prompt in self._prompts if prompt.session_id == session_id]
        self._prompts = [prompt for prompt in self._prompts if prompt.session_id != session_id]
        return dropped

    def all(self) -> list[Prompt]:
        """Every queued Prompt, in queue order."""
        return list(self._prompts)

    def has(self, session_id: SessionId) -> bool:
        return any(prompt.session_id == session_id for prompt in self._prompts)

    def session_ids(self) -> list[SessionId]:
        """The Sessions with queued Prompts, each once, in the order of their first one."""
        return list(dict.fromkeys(prompt.session_id for prompt in self._prompts))
