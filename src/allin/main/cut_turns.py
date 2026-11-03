"""A cut Turn: its last Step ended at its Output cap or at the slot's limit (ADR-0004).

llama's Responses API says ``completed`` either way, so Main tells a cut by the tokens. The
cut Turn is saved with what it generated and marked, and its Notice, ``cut_turn``, is built
into Main like a module of ``notices/`` (ADR-0009): it has the same Settings, and a module
declaring ``cut_turn`` replaces it.
"""

from collections.abc import Mapping

from pydantic import JsonValue

from allin.core.cut import CutReason
from allin.core.settings import Sampling
from allin.main.conversation import Conversation, CutNote, Step, Turn
from allin.main.usage import context_tokens
from allin.notices import NoticeEvent, NoticeType

DEFAULT_OUTPUT_CAP = 16_384
# The Notification the LLM gets in the Prompt after a Turn a human interrupted (ADR-0024).
INTERRUPTED_LINE = "The human interrupted your last answer; it is kept as far as it got."
# llama stops one token short of the slot's context: 80 in + 2 991 out of 3 072 (build 10909).
_SLOT_MARGIN = 1


def output_cap(sampling: Sampling, slot_context: int) -> int:
    """The most a Step may generate: the Session's Setting, else 16 384, or a quarter of the
    slot's limit if smaller."""
    if sampling.max_output_tokens is not None:
        return sampling.max_output_tokens
    return max(1, min(DEFAULT_OUTPUT_CAP, slot_context // 4))


def cut_reason(step: Step, cap: int, slot_context: int) -> CutReason | None:
    """Why the Step was cut, by its tokens; ``None`` if it ended on its own or has no usage."""
    if step.usage is None:
        return None
    input_tokens: int = step.usage["input_tokens"]
    output_tokens: int = step.usage["output_tokens"]
    if output_tokens >= cap:
        return "output_cap"
    if input_tokens + output_tokens >= slot_context - _SLOT_MARGIN:
        return "slot_limit"
    return None


def cut_mark(reason: CutReason) -> str:
    """The mark a cut Turn shows in its Chat, live and rebuilt."""
    match reason:
        case "output_cap":
            return "turn cut: it reached its output cap"
        case "slot_limit":
            return "turn cut: it filled the slot's context"
        case "interrupted":
            return "turn cut: interrupted with Shift+Enter"


def cut_note(turn: Turn) -> CutNote | None:
    """Why the Turn was cut, with the tokens that tell it; ``None`` if it was not."""
    if turn.cut is None:
        return None
    match turn.cut:
        case "interrupted":
            tokens = None
        case "output_cap":
            tokens = turn.steps[-1].generated if turn.steps else None
        case "slot_limit":
            tokens = context_tokens(turn.steps)
    return CutNote(reason=turn.cut, tokens=tokens or None)


def last_cut(conversation: Conversation) -> CutNote | None:
    """Why its last Turn was cut, also when a Compaction replaced it; ``None`` if it was not."""
    if conversation.turns:
        return cut_note(conversation.turns[-1])
    return conversation.summarized_cut


def cut_line(note: CutNote) -> str:
    """What the next Prompt tells the LLM first after a cut Turn: it cannot see its own
    tokens (#120)."""
    match note.reason:
        case "interrupted":
            return INTERRUPTED_LINE
        case "output_cap":
            why = "it reached its output cap"
            count = "" if note.tokens is None else f" of {note.tokens} tokens"
        case "slot_limit":
            why = "the Conversation filled its slot's context"
            count = "" if note.tokens is None else f" ({note.tokens} tokens)"
    return f"Your last answer was cut: {why}{count}."


def _check(event: NoticeEvent, params: dict[str, JsonValue]) -> Mapping[str, object] | None:
    del params  # its text and title are all it has
    sid = event.session_id
    match event.cut:
        case None | "interrupted":  # the human who interrupted it needs no Notice
            return None
        case "output_cap":
            why = f"it reached its output cap of {event.output_cap} tokens"
            how = f"raise it with /session set {sid} sampling.max_output_tokens=<n>, or ask it to go on"
        case "slot_limit":
            why = f"the Conversation filled the slot's {event.limit} tokens of context"
            # Asking it to go on does not fit either: the full Conversation is sent again.
            how = (
                f"compact it (/session compact {sid}) or link the Session to a Server with more"
                f" context (/session set {sid} server=<name>)"
            )
    return {"why": why, "how": how}


CUT_TURN = NoticeType(
    name="cut_turn", title="Turn cut", text="{why}; {how}", params={}, check=_check
)
