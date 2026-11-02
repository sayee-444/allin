"""Rebuilding a Session's Chat from its Conversation and its Log.

The Chat is never stored on its own: the Log says what was shown and in which order (Prompts,
errors, how each Turn ended) and the Conversation holds what finished Turns said; the Turns a
Compaction replaced are in its archives, so the Chat still shows them, with a cut line and the
summary where it happened (ADR-0017). A Session from before it had a Log shows only its
Conversation's Turns.
"""

from collections.abc import Iterable, Sequence

from allin.core.cut import CutReason
from allin.core.settings import SessionSettings
from allin.ipc.messages import ChatAppend
from allin.main.conversation import Conversation, Item, Step, Turn
from allin.main.cut_turns import cut_mark
from allin.main.log import LogEntry
from allin.main.usage import turn_tokens

# Why a Session's queued Prompts left the queue when the Turn before them failed (#16).
FAILED_BEFORE = "the turn before failed"


def returned_prompts(log: Iterable[LogEntry]) -> list[str]:
    """What goes back to a Window's input as it opens: the Prompt of the Session's last failed
    Turn and those dropped with it, in the order they were sent (#15). Nothing once a later
    Prompt was sent, a later Turn ended, or they were given back to an open Window already."""
    texts: list[str] = []
    for entry in log:
        fields = entry.fields
        match entry.event:
            case "turn_failed":
                prompt = fields.get("prompt")
                texts = [prompt] if isinstance(prompt, str) else []
            case "prompts_dropped" if texts and fields.get("reason") == FAILED_BEFORE:
                dropped: list[object] = fields.get("texts", [])
                kinds: list[object] = fields.get("kinds", ["prompt"] * len(dropped))
                texts += [
                    text
                    for text, kind in zip(dropped, kinds, strict=False)
                    if isinstance(text, str) and kind == "prompt"  # never a queued Compaction
                ]
            case "prompt" | "prompt_returned" | "turn_finished" | "turn_cancelled" | "rewound":
                texts = []
            case _:
                pass
    return texts


def prompt_history(log: Iterable[LogEntry]) -> list[str]:
    """The Prompts the Session was sent, oldest first, as they were typed (#22)."""
    history: list[str] = []
    for entry in log:
        if entry.event == "prompt":
            typed = entry.fields.get("typed") or entry.fields.get("text")
            if isinstance(typed, str):
                history.append(typed)
    return history


def rebuild_chat(
    conversation: Conversation,
    log: Iterable[LogEntry],
    settings: SessionSettings,
    archived: Sequence[Conversation] = (),
) -> list[ChatAppend]:
    """``archived``: the Conversations its Compactions archived, oldest first."""
    log = list(log)
    if not log:
        return [
            entry
            for turn in conversation.turns
            for entry in [_prompt(turn.prompt, settings), *_turn_output(turn, settings)]
        ]
    turns = _every_turn([*archived, conversation])
    chat: list[ChatAppend] = []
    shown_at = 0  # where the last Prompt shown starts: the Turn that finishes next is its
    starts: dict[int, int] = {}  # where each Turn the Conversation has starts in the Chat
    for entry in log:
        fields = entry.fields
        match entry.event:
            case "prompt" if not fields.get("queued"):
                shown_at = len(chat)
                chat.append(_prompt(fields["text"], settings))
            case "prompt_started":  # a queued Prompt, shown where its Turn started (ADR-0024)
                shown_at = len(chat)
                chat.append(_prompt(fields["text"], settings))
            case "turn_finished":
                index: int = fields["turn"]
                starts[index] = shown_at
                if index in turns:
                    chat += _turn_output(turns[index], settings)
            case "rewound":  # what it undid leaves the Chat, and what came after it (#58)
                first: int = fields["turn"]
                undone = [start for number, start in starts.items() if number >= first]
                if undone:
                    del chat[min(undone) :]
                starts = {number: start for number, start in starts.items() if number < first}
            case "prompts_dropped":
                chat.append(dropped_entry(len(fields["texts"]), fields["reason"]))
            case "turn_failed" | "turn_cancelled":
                chat += _output(fields["reasoning"], fields["answer"], settings)
                chat.append(error_entry(fields["error"]))
            case "error":
                chat.append(error_entry(fields["error"]))
            case "compacted":
                chat += compacted_entries(fields["summarized"], fields["summary"])
            case "compaction_failed":
                chat.append(compaction_failed_entry(fields["error"]))
            case _:
                pass  # events that never reach the Window's Chat
    return chat


def _every_turn(conversations: Iterable[Conversation]) -> dict[int, Turn]:
    """Each Turn the Session had, by its number; later Conversations hold the same Turns."""
    return {
        conversation.summarized + offset: turn
        for conversation in conversations
        for offset, turn in enumerate(conversation.turns)
    }


def _prompt(text: str, settings: SessionSettings) -> ChatAppend:
    return ChatAppend(role="user", label=settings.username, text=text)


def _turn_output(turn: Turn, settings: SessionSettings) -> list[ChatAppend]:
    chat: list[ChatAppend] = []
    for step in turn.steps:
        for item in step.output:
            match item.get("type"):
                case "reasoning":
                    chat += _output(_text(item, "reasoning_text"), "", settings)
                case "message":
                    chat += _output("", _text(item, "output_text"), settings)
                case _:
                    pass
        chat += _tool_entries(step)
    if (tokens := tokens_entry(turn.steps)) is not None:
        chat.append(tokens)
    if turn.cut is not None:
        chat.append(cut_entry(turn.cut))
    return chat


def _output(reasoning: str, answer: str, settings: SessionSettings) -> list[ChatAppend]:
    """What a Turn streamed, as the Window shows it: reasoning first, then the answer."""
    chat: list[ChatAppend] = []
    if reasoning:
        chat.append(ChatAppend(role="reasoning", label="reasoning", text=reasoning))
    if answer:
        chat.append(ChatAppend(role="answer", label=settings.assistant_name, text=answer))
    return chat


def _text(item: Item, part_type: str) -> str:
    return "".join(
        part.get("text", "") for part in item.get("content", []) if part.get("type") == part_type
    )


def error_entry(text: str) -> ChatAppend:
    return ChatAppend(role="error", label="error", text=text)


def dropped_entry(count: int, reason: str) -> ChatAppend:
    """Queued Prompts of a Session taken out of the queue before any Turn ran them."""
    return ChatAppend(
        role="info", label="dropped", text=f"{count} queued prompt(s) dropped: {reason}"
    )


def tools_entry(names: Sequence[str]) -> ChatAppend:
    """The tools a Step called, shown while they run (#106)."""
    return ChatAppend(role="info", label="tools", text=f"tools called: {', '.join(names)}")


def tool_failed_entry(name: str, error: str) -> ChatAppend:
    """A tool that failed, timed out or was taken down: the LLM reads ``error`` too."""
    return ChatAppend(role="info", label="tools", text=f"{name} failed: {error}")


def _tool_entries(step: Step) -> list[ChatAppend]:
    if not step.calls:
        return []
    shown = [tools_entry([_field(call, "name") for call in step.calls])]
    return shown + [tool_failed_entry(name, error) for name, error in step.failures()]


def _field(item: Item, key: str) -> str:
    value: object = item.get(key)
    return value if isinstance(value, str) else ""


def cut_entry(reason: CutReason) -> ChatAppend:
    """The mark of a cut Turn, after its output and what it cost (ADR-0004)."""
    return ChatAppend(role="info", label="cut", text=cut_mark(reason))


def tokens_entry(steps: list[Step]) -> ChatAppend | None:
    """What a finished Turn cost, shown after its output; ``None`` if no Step has usage."""
    if all(step.usage is None for step in steps):
        return None
    return ChatAppend(role="info", label="tokens", text=f"{turn_tokens(steps)} tokens this turn")


def compacted_entries(summarized: int, summary: str) -> list[ChatAppend]:
    """Where a Compaction cut the Conversation and what the LLM remembers instead."""
    cut = f"── Prompts 1\N{EN DASH}{summarized} summarized and archived; the LLM now reads this summary ──"
    return [
        ChatAppend(role="info", label="compacted", text=cut),
        ChatAppend(role="info", label="summary", text=summary),
    ]


def compaction_failed_entry(error: str) -> ChatAppend:
    return ChatAppend(
        role="info", label="compacted", text=f"compaction failed, nothing changed: {error}"
    )
