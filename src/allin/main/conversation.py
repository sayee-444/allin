"""A Session's Conversation, stored grouped by Turn and flattened into ``input=[...]``.

Saved as ``conversation.json`` in the Session's folder once per finished Turn, atomically.
"""

from collections.abc import Sequence
from pathlib import Path
from typing import Any, Literal, Protocol

from pydantic import BaseModel, ValidationError

from allin.core.cut import CutReason
from allin.core.errors import SessionStorageFailed, SessionUnreadable
from allin.core.files import write_atomically
from allin.core.origin import HUMAN, Origin

_FILE = "conversation.json"
_ARCHIVE = "compacted"  # what each Compaction replaced, numbered from 1

type Item = dict[str, Any]  # a Responses API input/output item, as JSON

# What heads a tool's output when it failed instead of giving a value: the LLM reads it (#106).
TOOL_ERROR = "error: "


class Step(BaseModel, frozen=True):
    """One ``responses.create`` call: the items it produced, its token usage and llama.cpp's
    ``timings`` for it (prompt and generation counts and speeds), and what the tools it called
    gave back, as ``function_call_output`` items: data for the next Step (ADR-0005)."""

    output: list[Item]
    usage: dict[str, Any] | None = None
    timings: dict[str, Any] | None = None
    results: list[Item] = []

    @property
    def items(self) -> list[Item]:
        """What the next Step reads of it: what it produced, then its tools' outputs."""
        return [*self.output, *self.results]

    @property
    def calls(self) -> list[Item]:
        """The tools it called, as ``function_call`` items, in order."""
        return [item for item in self.output if item.get("type") == "function_call"]

    @property
    def generated(self) -> int:
        """The tokens it generated, as its usage says; 0 without usage."""
        tokens: object = (self.usage or {}).get("output_tokens")
        return tokens if isinstance(tokens, int) else 0

    def failures(self) -> list[tuple[str, str]]:
        """Each tool it called that gave back an error instead of a value, with that error."""
        names = {_text(call, "call_id"): _text(call, "name") for call in self.calls}
        return [
            (names.get(_text(result, "call_id"), "?"), _text(result, "output"))
            for result in self.results
            if _text(result, "output").startswith(TOOL_ERROR)
        ]


def _text(item: Item, key: str) -> str:
    value: object = item.get(key)
    return value if isinstance(value, str) else ""


class Attachment(BaseModel, frozen=True):
    """A file a Prompt attached (Referencia a archivo): its place in the Session's Carpeta de
    trabajo, relative to it, and how the LLM gets it (``attachments.AttachmentReader``)."""

    path: str
    kind: Literal["image", "text", "binary"]


# What heads the Notifications that open a Prompt: data from Main, never instructions.
NOTIFICATIONS_HEADER = "[LOGS @COMMANDS]"


class Turn(BaseModel, frozen=True):
    prompt: str
    steps: list[Step]
    attachments: list[Attachment] = []
    cut: CutReason | None = None  # a cut Turn keeps what it generated, marked
    notifications: list[str] = []  # what Main told the LLM ahead of its Prompt
    # What a Rewind needs of its Prompt (#58): what the human typed, if its Command prompts
    # changed it, and who sent it.
    typed: str = ""
    origin: Origin = HUMAN
    prompt_id: str = ""  # its Prompt's, which its tools knew (#116)

    @property
    def as_typed(self) -> str:
        return self.typed or self.prompt


class Conversation(BaseModel, frozen=True):
    sysprompt: str  # rendered text, so editing the file later does not change this Session
    # A Molde's initial history: messages that open the Conversation and are no Turns (#112).
    history: list[Item] = []
    # What the last Compaction wrote of the Turns it replaced, which are archived (ADR-0017).
    summary: str | None = None
    summarized: int = 0  # how many first Turns the summary replaced: the number of turns[0]
    turns: list[Turn] = []

    @property
    def next_turn(self) -> int:
        """The number, from 0, of the next Turn among every Turn the Session had."""
        return self.summarized + len(self.turns)

    @property
    def started(self) -> bool:
        """Whether it has had a Turn, literal or summarized."""
        return self.next_turn > 0

    def with_turn(self, turn: Turn) -> "Conversation":
        return self.model_copy(update={"turns": [*self.turns, turn]})

    def request_input(
        self,
        prompt: str,
        attachments: Sequence[Attachment] = (),
        reader: "AttachmentParts | None" = None,
        notifications: Sequence[str] = (),
    ) -> list[Item]:
        """The ``input`` for the first Step of a Turn that answers ``prompt``; ``reader``
        gives what the LLM gets of each attachment, this Prompt's and earlier ones', and
        ``notifications`` go ahead of the Prompt."""
        items: list[Item] = [{"role": "developer", "content": self.sysprompt}, *self.history]
        if self.summary is not None:
            items.append(summary_item(self.summary, self.summarized))
        for turn in self.turns:
            items += turn_items(turn, reader)
        items.append(user_item(with_notifications(prompt, notifications), attachments, reader))
        return items


def summary_header(prompts: int) -> str:
    """What heads a summary, so the LLM reads it as data about the Prompts it replaced."""
    return f"[Conversation summary · Prompts 1\N{EN DASH}{prompts}]"


def summary_item(summary: str, prompts: int) -> Item:
    """A summary as the Conversation holds it: a ``user`` message, never ``developer``, since
    it comes from what the LLM read (ADR-0013)."""
    return user_item(f"{summary_header(prompts)}\n\n{summary}")


class AttachmentParts(Protocol):
    def parts(self, attachments: Sequence[Attachment]) -> list[Item]: ...


def turn_items(turn: Turn, reader: AttachmentParts | None = None) -> list[Item]:
    """A Turn as ``input`` items: its Prompt, then what each of its Steps produced."""
    text = with_notifications(turn.prompt, turn.notifications)
    prompt = user_item(text, turn.attachments, reader)
    return [prompt, *(item for step in turn.steps for item in step.items)]


def with_notifications(prompt: str, notifications: Sequence[str]) -> str:
    """A Prompt's text as the LLM reads it: its Notifications first, under their header, in
    the same ``user`` message (ADR-0013)."""
    if not notifications:
        return prompt
    return "\n".join([NOTIFICATIONS_HEADER, *notifications]) + f"\n\n{prompt}"


def user_item(
    text: str, attachments: Sequence[Attachment] = (), reader: AttachmentParts | None = None
) -> Item:
    """A Prompt as a ``user`` message: its text, then its attachments as ``reader`` gives
    them; without one (a Compaction's summary Step), each is a line naming it."""
    if not attachments:
        return {"role": "user", "content": text}
    parts = reader.parts(attachments) if reader is not None else _named(attachments)
    return {"role": "user", "content": [{"type": "input_text", "text": text}, *parts]}


def _named(attachments: Sequence[Attachment]) -> list[Item]:
    return [{"type": "input_text", "text": f"\n[{a.kind} {a.path}]"} for a in attachments]


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


def archive_conversation(folder: Path, conversation: Conversation) -> str:
    """Keep a Conversation as it was before a Compaction replaced part of it, so nothing is
    lost and a Rewind can bring it back (ADR-0017); its path within ``folder``."""
    archive = folder / _ARCHIVE
    try:
        archive.mkdir(exist_ok=True)
        number = len(_archived(archive)) + 1
        write_atomically(archive / f"{number}.json", conversation.model_dump_json(indent=2))
    except OSError as error:
        raise SessionStorageFailed(root=str(folder), reason=str(error)) from error
    return f"{_ARCHIVE}/{number}.json"


def load_archived(folder: Path) -> list[Conversation]:
    """Every Conversation a Compaction archived, oldest first."""
    try:
        return [
            Conversation.model_validate_json(path.read_bytes())
            for path in _archived(folder / _ARCHIVE)
        ]
    except (OSError, ValidationError) as error:
        raise SessionUnreadable(session_id=folder.name, reason=str(error)) from error


def _archived(archive: Path) -> list[Path]:
    if not archive.is_dir():
        return []
    numbered = [path for path in archive.glob("*.json") if path.stem.isdigit()]
    return sorted(numbered, key=lambda path: int(path.stem))
