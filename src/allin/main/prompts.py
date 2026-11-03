"""Main's queue of Prompts waiting for a Turn (ADR-0004).

Only the dispatcher touches it, so it needs no locking.
"""

from collections.abc import Callable, Container
from dataclasses import dataclass, field, replace
from typing import Literal

from allin.core.ids import NO_PROMPT_ID, Outsider, PromptId, SessionId, SubscriptionId
from allin.core.origin import HUMAN, LinkKind, Origin
from allin.main.conversation import Attachment, Step
from allin.main.prompt_commands import TurnOverrides

# What a queue entry asks: a Turn for its text, a Compaction asked with ``/session compact``
# (its text: the human's instructions) or one Main starts before a Turn that needs it.
type PromptKind = Literal["prompt", "compact", "auto_compact"]


# ``--devastation inside``: only what stays inside a Subperfil with the Exención (ADR-0026).
type AutoConfirm = Literal["-y", "--devastation", "--devastation inside"]


@dataclass(frozen=True)
class Prompt:
    session_id: SessionId
    text: str
    kind: PromptKind = "prompt"
    # Its id, which its Turn and each of its tools know (#116), and which names its
    # attachments' folder; the queue gives one to a Prompt that comes without it.
    prompt_id: PromptId = NO_PROMPT_ID
    attachments: tuple[Attachment, ...] = ()
    overrides: TurnOverrides = field(default_factory=TurnOverrides)
    typed: str = ""  # what the human typed, when its Command prompts changed it (#22)
    # Sent while its Session was busy: its Chat shows it once its Turn starts (ADR-0024).
    queued: bool = False
    origin: Origin = HUMAN  # a Rewind gives back to the input only a human's (#58)
    # Who answers its Turn's Confirmations instead of a human: ``-y``, sent so by a human, for
    # its own and not for what it sets going; or, once its Turn started, whoever answered them
    # then, which a fall does not change (ADR-0013).
    answered_by: AutoConfirm | None = None
    # Its Turn started and a fall suspended it: it goes on from the Steps it had ended
    # (ADR-0023), with what it had when it started (ADR-0013).
    suspended: bool = False
    steps: tuple[Step, ...] = ()
    # How many of ``steps`` read the Conversation of before the Compactación its Turn yielded
    # to: their Context no longer holds (#177).
    context_from: int = 0
    # The Suscripciones waiting for its answer, its sender's ``--reply-to`` (#116).
    owed: tuple[SubscriptionId, ...] = ()
    # A Suscripción's Prompt: those that woke its Session with it, a Continuation (#116).
    woke_by: tuple[SubscriptionId, ...] = ()
    # Typed in its Session's Window while a Turn of the Session was under way, and queued: it
    # may enter that Turn as a Texto intercalado instead of waiting for its own (ADR-0024).
    in_window: bool = False
    # The Externo whose words a Proceso adherido brought: noted for the Log and the memory,
    # never for its LLM, and never any authority (ADR-0026).
    outsider: Outsider | None = None
    # A Proceso adherido brought it on its own, by its Iniciativa, not while it served a call:
    # it waits behind the humans' and the Sessions' (ADR-0026). Its chain cannot tell.
    initiative: bool = False

    @property
    def as_typed(self) -> str:
        return self.typed or self.text

    @property
    def may_join(self) -> bool:
        """Whether, queued, it may enter its Session's running Turn: plain text a human typed in
        its Window, with nothing that is for a whole Turn of its own (Command prompts,
        attachments, ``-y``) and nobody waiting for its own answer."""
        return (
            self.kind == "prompt"
            and self.queued
            and self.in_window
            and self.origin.is_human
            and not self.attachments
            and self.overrides == TurnOverrides()
            and self.answered_by is None
            and not self.owed
            and not self.woke_by
            and not self.suspended
        )


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

    def drop(
        self, session_id: SessionId, keep: Callable[[Prompt], bool] | None = None
    ) -> list[Prompt]:
        """Take a Session's Prompts out of the queue, but those ``keep`` keeps; return them in
        their order."""

        def dropping(prompt: Prompt) -> bool:
            return prompt.session_id == session_id and not (keep is not None and keep(prompt))

        dropped = [prompt for prompt in self._prompts if dropping(prompt)]
        self._prompts = [prompt for prompt in self._prompts if not dropping(prompt)]
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
        typed = [
            p for p in self.of(session_id) if p.kind == "prompt" and p.queued and p.origin.is_human
        ]
        if not typed:
            return None
        self._prompts.remove(typed[-1])
        return typed[-1]

    def mark_typed_in_window(self, session_id: SessionId) -> None:
        """The Session's last Prompt was typed in its Window: marked so if it waits behind a
        Turn, the only time it matters (ADR-0024)."""
        mine = self.of(session_id)
        if mine and mine[-1].queued:
            index = self._prompts.index(mine[-1])
            self._prompts[index] = replace(mine[-1], in_window=True)

    def compacted(self, session_id: SessionId) -> None:
        """A Compactación of the Session saved: the Steps its Turn waiting to go on had ended
        read the Conversation of before (#177)."""
        self._prompts = [
            replace(prompt, context_from=len(prompt.steps))
            if prompt.session_id == session_id
            else prompt
            for prompt in self._prompts
        ]

    def turn_started(self, session_id: SessionId) -> None:
        """A new Turn of the Session started: what it has waiting was typed before it, so it
        waits for its own Turn instead of entering this one."""
        self._prompts = [
            replace(prompt, in_window=False) if prompt.session_id == session_id else prompt
            for prompt in self._prompts
        ]

    def take_joining(self, session_id: SessionId, room: int) -> list[Prompt]:
        """Take out, in order, the Session's queued Prompts that may enter its running Turn
        (``Prompt.may_join``) and fit in ``room`` characters. The first of its Prompts typed in
        its Window that may not, or a Compaction, stops it, so what the human typed keeps its
        order; the Prompts others sent wait for their own Turn and stop nothing."""
        taken: list[Prompt] = []
        for prompt in self.of(session_id):
            if prompt.kind != "prompt" or (prompt.in_window and not prompt.may_join):
                break
            if not prompt.may_join:
                continue
            room -= len(prompt.text)
            if room < 0:
                break
            taken.append(prompt)
        for prompt in taken:
            self._prompts.remove(prompt)
        return taken

    def session_ids(self) -> list[SessionId]:
        """The Sessions with queued Prompts, each once, in the order of their first one."""
        return list(dict.fromkeys(prompt.session_id for prompt in self._prompts))


_SENDERS: dict[LinkKind, str] = {
    "human": "the human",
    "main": "Main",
    "session": "Session",
    "process": "attached process",
    "subscription": "Subscription",
}


def origin_header(origin: Origin) -> str:
    """What heads a Prompt that no human typed, so its LLM reads it as data from whoever sent
    it, never as the human's words: ``[from Session ab12]`` (ADR-0013). An Externo's words say
    only the Proceso adherido that brought them: who wrote them is for the Log and the memory
    (ADR-0026)."""
    last = origin.chain[-1]
    sender = " ".join(filter(None, (_SENDERS[last.kind], last.id)))
    return f"[from {sender}]"
