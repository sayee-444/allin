"""Rebuilding a Session's Chat from its Conversation and its Log.

The Chat is never stored on its own: the Log says what was shown and in which order (Prompts,
errors, how each Turn ended) and the Conversation holds what finished Turns said. A Session
from before it had a Log shows only its Conversation's Turns.
"""

from collections.abc import Iterable

from allin.core.settings import SessionSettings
from allin.ipc.messages import ChatAppend
from allin.main.conversation import Conversation, Item, Step, Turn
from allin.main.log import LogEntry
from allin.main.usage import turn_tokens


def rebuild_chat(
    conversation: Conversation, log: Iterable[LogEntry], settings: SessionSettings
) -> list[ChatAppend]:
    log = list(log)
    if not log:
        return [
            entry
            for turn in conversation.turns
            for entry in [_prompt(turn.prompt, settings), *_turn_output(turn, settings)]
        ]
    chat: list[ChatAppend] = []
    for entry in log:
        fields = entry.fields
        match entry.event:
            case "prompt":
                chat.append(_prompt(fields["text"], settings))
            case "turn_finished" if 0 <= fields["turn"] < len(conversation.turns):
                chat += _turn_output(conversation.turns[fields["turn"]], settings)
            case "prompts_dropped":
                chat.append(dropped_entry(len(fields["texts"]), fields["reason"]))
            case "turn_failed" | "turn_cancelled":
                chat += _output(fields["reasoning"], fields["answer"], settings)
                chat.append(error_entry(fields["error"]))
            case "error":
                chat.append(error_entry(fields["error"]))
            case _:
                pass  # events that never reach the Window's Chat
    return chat


def _prompt(text: str, settings: SessionSettings) -> ChatAppend:
    return ChatAppend(role="user", label=settings.username, text=text)


def _turn_output(turn: Turn, settings: SessionSettings) -> list[ChatAppend]:
    chat: list[ChatAppend] = []
    for item in (item for step in turn.steps for item in step.output):
        match item.get("type"):
            case "reasoning":
                chat += _output(_text(item, "reasoning_text"), "", settings)
            case "message":
                chat += _output("", _text(item, "output_text"), settings)
            case _:
                pass
    if (tokens := tokens_entry(turn.steps)) is not None:
        chat.append(tokens)
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


def tokens_entry(steps: list[Step]) -> ChatAppend | None:
    """What a finished Turn cost, shown after its output; ``None`` if no Step has usage."""
    if all(step.usage is None for step in steps):
        return None
    return ChatAppend(role="info", label="tokens", text=f"{turn_tokens(steps)} tokens this turn")
