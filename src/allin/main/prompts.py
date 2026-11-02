"""Main's queue of Prompts waiting for a Turn (ADR-0004).

Only the dispatcher touches it, so it needs no locking.
"""

from collections.abc import Container
from dataclasses import dataclass, field
from typing import Literal

from allin.core.ids import SessionId
from allin.main.conversation import Attachment
from allin.main.prompt_commands import TurnOverrides

# What a queue entry asks: a Turn for its text, a Compaction asked with ``/session compact``
# (its text: the human's instructions) or one Main starts before a Turn that needs it.
type PromptKind = Literal["prompt", "compact", "auto_compact"]


@dataclass(frozen=True)
class Prompt:
    session_id: SessionId
    text: str
    kind: PromptKind = "prompt"
    # A human's Command prompts (#14): what it attached and the parameters its Turn changes;
    # the id names the attachments' folder, so only a Prompt with attachments has one.
    prompt_id: str = ""
    attachments: tuple[Attachment, ...] = ()
    overrides: TurnOverrides = field(default_factory=TurnOverrides)
    typed: str = ""  # what the human typed, when its Command prompts changed it (#22)
    # Sent while its Session was busy: its Chat shows it once its Turn starts (ADR-0024).
    queued: bool = False

    @property
    def as_typed(self) -> str:
        return self.typed or self.text


class PromptQueue:
    def __init__(self) -> None:
        self._prompts: list[Prompt] = []

    def push(self, prompt: Prompt) -> None:
        self._prompts.append(prompt)

    def push_front(self, prompt: Prompt) -> None:
        """Put a Prompt back first: a Step a Server left half-done goes before the rest."""
        self._prompts.insert(0, prompt)

    def insert_before(self, prompt: Prompt, entry: Prompt) -> None:
        """Put ``entry`` just before the first Prompt equal to ``prompt``."""
        self._prompts.insert(self._prompts.index(prompt), entry)

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

    def of(self, session_id: SessionId) -> list[Prompt]:
        """A Session's queued Prompts, in queue order."""
        return [prompt for prompt in self._prompts if prompt.session_id == session_id]

    def jump_last(self, session_id: SessionId) -> None:
        """Put a Session's last queued Prompt before its other Prompts (Shift+Enter)."""
        typed = [p for p in self.of(session_id) if p.kind == "prompt"]
        if len(typed) < 2:
            return
        last = typed[-1]
        self._prompts.remove(last)
        self._prompts.insert(self._prompts.index(typed[0]), last)

    def take_last(self, session_id: SessionId) -> Prompt | None:
        """Take out a Session's last queued Prompt that a human typed, if any (Up)."""
        typed = [p for p in self.of(session_id) if p.kind == "prompt" and p.queued]
        if not typed:
            return None
        self._prompts.remove(typed[-1])
        return typed[-1]

    def session_ids(self) -> list[SessionId]:
        """The Sessions with queued Prompts, each once, in the order of their first one."""
        return list(dict.fromkeys(prompt.session_id for prompt in self._prompts))
