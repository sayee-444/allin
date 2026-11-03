"""What leaves Main's queue with a Prompt, whatever takes it out (#196).

A Prompt leaves ``TurnQueue`` with its Turn running or waiting in the queue (suspended by a
fall or yielded to compact, ADR-0023, ADR-0017), or before any Turn of it started, and saved in
its Conversation or not. Every way out goes through ``TurnQueue._leave`` with a ``Leaving`` and
its ``Ending``, so each applies the same rule; this module holds what of that rule needs only
the Prompt.
"""

from dataclasses import dataclass
from typing import Literal

from allin.main.conversation import interjected_in
from allin.main.prompts import Prompt

# How it left: its Turn failed, or it could not start; it was cancelled, or taken back; it is
# kept as it was, in the MainState Main stops with or the Profile it leaves takes with it; or
# its Turn, or its Compaction, was saved in its Conversation (#208).
type Ending = Literal["failed", "cancelled", "kept", "saved"]


@dataclass(frozen=True)
class Leaving:
    """A Prompt that leaves the queue: ``ran`` if a Turn of it ran Steps, which are in no
    Conversation; ``joined``, the Textos intercalados that Turn took (ADR-0024)."""

    prompt: Prompt
    joined: tuple[str, ...] = ()
    ran: bool = False

    @classmethod
    def queued(cls, prompt: Prompt) -> "Leaving":
        """One that waited in the queue: suspended or yielded, its Turn ran, and its Steps
        hold the Textos intercalados it took."""
        return cls(prompt, tuple(interjected_in(prompt.steps)), ran=prompt.suspended)

    @property
    def told(self) -> bool:
        """Whether who sent it hears it left: never for a Compaction, which nobody waits on."""
        return self.prompt.kind == "prompt"

    @property
    def back(self) -> list[str]:
        """What goes back to its Session's input, in order: a human's Prompt as it was typed,
        then the Textos intercalados, a human's whatever its Origin (#156, #171, #176); nothing
        of a Compaction (GLOSSARY: Prompt)."""
        if not self.told:
            return []
        typed = [self.prompt.as_typed] if self.prompt.origin.is_human else []
        return [*typed, *self.joined]

    @property
    def unsaved(self) -> bool:
        """Whether what its Turn did is lost: what it opened is cancelled, what it told waits
        again, and the Prompts queued behind it, written after it, fall if it failed (#16)."""
        return self.ran and self.told
