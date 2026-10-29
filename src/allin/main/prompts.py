"""Main's queue of Prompts waiting for a Turn (ADR-0004).

Only the dispatcher touches it, so it needs no locking.
"""

from collections.abc import Container
from dataclasses import dataclass


@dataclass(frozen=True)
class Prompt:
    session_id: str
    text: str


class PromptQueue:
    def __init__(self) -> None:
        self._prompts: list[Prompt] = []

    def push(self, prompt: Prompt) -> None:
        self._prompts.append(prompt)

    def pop_ready(self, active: Container[str]) -> Prompt | None:
        """Take the first Prompt whose Session has no active Turn, so a busy one blocks no one."""
        for index, prompt in enumerate(self._prompts):
            if prompt.session_id not in active:
                return self._prompts.pop(index)
        return None

    def drop(self, session_id: str) -> list[Prompt]:
        """Take a Session's Prompts out of the queue; return them in their order."""
        dropped = [prompt for prompt in self._prompts if prompt.session_id == session_id]
        self._prompts = [prompt for prompt in self._prompts if prompt.session_id != session_id]
        return dropped

    def has(self, session_id: str) -> bool:
        return any(prompt.session_id == session_id for prompt in self._prompts)
