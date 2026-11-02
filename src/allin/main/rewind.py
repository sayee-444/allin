"""Rewind (#58): a Session's Conversation as it was before its last Prompts.

It never goes back past the start, and before a Compaction it takes what was archived and
drops the summary (ADR-0017). Nothing is deleted: the archives stay where they are.
"""

from collections.abc import Sequence
from dataclasses import dataclass

from allin.core.errors import ArchiveMissing
from allin.main.conversation import Conversation, Turn


@dataclass(frozen=True)
class Rewound:
    conversation: Conversation  # what the Session goes on from
    undone: list[Turn]  # the Turns it no longer has, oldest first
    turn: int  # the number of the first one undone: the next Turn takes it


def rewind(conversation: Conversation, archived: Sequence[Conversation], n: int) -> Rewound:
    """The last ``n`` Prompts undone; ``archived``: the Conversations its Compactions
    archived, oldest first."""
    target = max(0, conversation.next_turn - n)
    every = _every_turn([*archived, conversation])
    undone = [every[number] for number in range(target, conversation.next_turn)]
    source = conversation
    if target < conversation.summarized:  # before the cut: the archive holds it literal
        earlier = [kept for kept in archived if kept.summarized <= target]
        if not earlier:
            raise ArchiveMissing(turn=target)
        source = earlier[-1]
    kept = source.model_copy(update={"turns": source.turns[: target - source.summarized]})
    return Rewound(kept, undone, target)


def _every_turn(conversations: Sequence[Conversation]) -> dict[int, Turn]:
    """Each Turn by its number; a later Conversation holds the same Turns, or newer ones."""
    return {
        conversation.summarized + offset: turn
        for conversation in conversations
        for offset, turn in enumerate(conversation.turns)
    }
