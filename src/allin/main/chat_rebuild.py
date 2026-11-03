"""Rebuilding a Session's Chat from its Conversation and its Log.

The Chat is never stored on its own: the Log says what was shown and in which order (Prompts,
errors, how each Turn ended) and the Conversation holds what finished Turns said; the Turns a
Compaction replaced are in its archives, so the Chat still shows them, with a cut line and the
summary where it happened (ADR-0017). A Session from before it had a Log shows only its
Conversation's Turns.
"""

from collections.abc import Iterable, Sequence

from pydantic import ValidationError

from allin.core.cut import CutReason
from allin.core.origin import HUMAN, Origin
from allin.core.settings import SessionSettings
from allin.ipc.messages import ChatAppend
from allin.main.conversation import Conversation, Item, Step, Turn
from allin.main.cut_turns import cut_mark
from allin.main.log import LogEntry
from allin.main.usage import turn_tokens

# Why a Session's queued Prompts left the queue when the Turn before them failed (#16).
FAILED_BEFORE = "the turn before failed"


def returned_prompts(log: Iterable[LogEntry]) -> list[str]:
    """What goes back to a Window's input as it opens, in the order it would have with the
    Window open, as it was typed (#166), and only a human's: the others failed towards whoever
    sent them (#171).

    The Prompt of the Session's last failed Turn, its Textos intercalados and the Prompts
    dropped with it, in the order they were sent (#15, #174): nothing once a later Prompt was
    sent or a later Turn ended. Ahead of them, a human's Prompts that did not fit even in the
    empty slot, the last one first (#174): nothing once the human sent a later one. None of
    them once given back to an open Window already."""
    texts: list[str] = []
    unfit: list[str] = []
    failed = False  # the last Turn failed, and nothing since says its Prompts are told
    for entry in log:
        fields = entry.fields
        match entry.event:
            case "turn_failed":
                prompt = fields.get("typed") or fields.get("prompt")
                failed = isinstance(prompt, str)
                human = _by_human(fields.get("origin"))
                texts = [prompt] if isinstance(prompt, str) and human else []
                if failed:  # its Textos intercalados are a human's, whatever its Origin
                    joined: list[object] = fields.get("joined", [])
                    texts += [text for text in joined if isinstance(text, str)]
            case "prompts_dropped" if failed and fields.get("reason") == FAILED_BEFORE:
                dropped: list[object] = fields.get("typed") or fields.get("texts", [])
                kinds: list[object] = fields.get("kinds", ["prompt"] * len(dropped))
                origins: list[object] = fields.get("origins", [None] * len(dropped))
                texts += [
                    text
                    for text, kind, origin in zip(dropped, kinds, origins, strict=False)
                    # never a queued Compaction
                    if isinstance(text, str) and kind == "prompt" and _by_human(origin)
                ]
            case "prompt_too_big":
                typed = fields.get("typed")
                if isinstance(typed, str) and _by_human(fields.get("origin")):
                    unfit.insert(0, typed)
            case "prompt":
                texts, failed = [], False
                if _by_human(fields.get("origin")):  # another's says nothing of the human's
                    unfit = []
            case "prompt_returned":
                texts, unfit, failed = [], [], False
            case "turn_finished" | "turn_cancelled" | "rewound":
                texts, failed = [], False
            case _:
                pass
    return [*texts, *unfit]


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
            for entry in [
                prompt_entry(turn.prompt, turn.origin, settings),
                *_turn_output(turn, settings),
            ]
        ]
    turns = _every_turn([*archived, conversation])
    chat: list[ChatAppend] = []
    shown_at = 0  # where the last Prompt shown starts: the Turn that finishes next is its
    starts: dict[int, int] = {}  # where each Turn the Conversation has starts in the Chat
    # The Origin of each queued Prompt, by its text: a Log from before #154 names it only there.
    waiting: dict[str, Origin] = {}
    for entry in log:
        fields = entry.fields
        match entry.event:
            case "prompt" if not fields.get("queued"):
                shown_at = len(chat)
                origin = _logged_origin(fields) or HUMAN
                chat.append(prompt_entry(fields["text"], origin, settings))
            case "prompt":  # queued: its Chat shows it once its Turn starts
                waiting[fields["text"]] = _logged_origin(fields) or HUMAN
            case "prompt_started":  # a queued Prompt, shown where its Turn started (ADR-0024)
                shown_at = len(chat)
                queued = waiting.pop(fields["text"], HUMAN)
                origin = _logged_origin(fields) or queued
                chat.append(prompt_entry(fields["text"], origin, settings))
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
                chat += [_prompt(text, settings) for text in fields.get("joined", [])]
                chat.append(error_entry(fields["error"]))
            case "error":
                chat.append(error_entry(fields["error"]))
            case "notification":
                chat.append(logs_entry(fields["text"]))
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


def prompt_entry(text: str, origin: Origin, settings: SessionSettings) -> ChatAppend:
    """A Prompt as its Window's Chat shows it, live and rebuilt: with the user's name only if
    a human sent it, else with the last link of its Origin, so no one reads it as theirs
    (#154)."""
    label = settings.username if origin.is_human else str(origin.chain[-1])
    return ChatAppend(role="user", label=label, text=text)


def _prompt(text: str, settings: SessionSettings) -> ChatAppend:
    """What the human wrote during a Turn, whatever that Turn's Origin (ADR-0024)."""
    return prompt_entry(text, HUMAN, settings)


def _logged_origin(fields: dict[str, object]) -> Origin | None:
    """The Origin a Log entry names; ``None`` if it names none it can read (#105, #154)."""
    try:
        return Origin.model_validate(fields.get("origin"))
    except ValidationError:
        return None


def _by_human(labels: object) -> bool:
    """Whether the Origin a Log entry names for a Prompt is a human's; ``None``, logged before
    #171, names none, and its Prompt goes back as it did then."""
    if labels is None:
        return True
    origin = _logged_origin({"origin": labels})
    return origin is not None and origin.is_human


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
        chat += [_prompt(text, settings) for text in step.interjected]  # ADR-0024
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


def logs_entry(text: str) -> ChatAppend:
    """A Notificación as its Window's Chat shows it, live and rebuilt (#109)."""
    return ChatAppend(role="info", label="logs", text=text)


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
