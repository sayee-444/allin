"""Messages exchanged between Main and a Window, one JSON object per line (ADR-0002)."""

from typing import Annotated, Literal

from pydantic import BaseModel, Field, TypeAdapter, ValidationError

from allin.core.errors import MalformedMessage
from allin.core.ids import BoxId
from allin.ui.boxes import BoxKind

# Window -> Main


class WindowReady(BaseModel):
    """The Window is up and listening."""

    type: Literal["window_ready"] = "window_ready"


class UserInput(BaseModel):
    """Text the user submitted with Enter; with Shift+Enter, ``interrupt`` also cuts the
    running Turn, and the text, which may be empty, runs next (ADR-0024)."""

    type: Literal["user_input"] = "user_input"
    text: str
    interrupt: bool = False


class WithdrawQueued(BaseModel):
    """Up on the input's first row while Prompts are queued: the last one comes back."""

    type: Literal["withdraw_queued"] = "withdraw_queued"


class InputDraft(BaseModel):
    """The input's Draft, shown block first (``core.drafts``), sent once the user stops typing
    for a moment, so a restarted Main can give it back."""

    type: Literal["input_draft"] = "input_draft"
    blocks: list[str]


class BoxAnswer(BaseModel):
    """The user answered a box: yes or no to a Confirmation, ``None`` for a Notice closed."""

    type: Literal["box_answer"] = "box_answer"
    box_id: BoxId
    answer: bool | None


type WindowToMain = WindowReady | UserInput | WithdrawQueued | InputDraft | BoxAnswer
WindowMessage = Annotated[WindowToMain, Field(discriminator="type")]

# Main -> Window

type ChatRole = Literal["user", "reasoning", "answer", "info", "error"]


class ChatAppend(BaseModel):
    """Append an entry to the Window's Chat, shown as ``[label] text`` in its role's colour."""

    type: Literal["chat_append"] = "chat_append"
    role: ChatRole
    label: str
    text: str


class ChatDelta(BaseModel):
    """Stream text into the Window's Chat: it extends the last entry if that one has the same
    role and label and is still streaming, else starts a new entry."""

    type: Literal["chat_delta"] = "chat_delta"
    role: ChatRole
    label: str
    text: str


class TurnState(BaseModel):
    """Whether the Session has a Turn running or waiting. ``waiting`` says why its next Step
    has not started, for its border (ADR-0022); ``queued``, the Prompts sent while a Turn ran
    that wait for theirs, shown below the Chat (ADR-0024)."""

    type: Literal["turn_state"] = "turn_state"
    active: bool
    waiting: str | None = None
    queued: list[str] = []


class ContextUsage(BaseModel):
    """How much of its context limit the Session fills and how fast its last Step generated;
    ``None`` when not known yet."""

    type: Literal["context_usage"] = "context_usage"
    context: int | None
    limit: int | None  # the context of each of the Server's slots, as its /props says
    tokens_per_second: float | None


class PoolShared(BaseModel):
    """Whether the Session's limit is a ``--kv-unified`` pool that a Step of another Session
    uses now, so its border says the limit is shared."""

    type: Literal["pool_shared"] = "pool_shared"
    shared: bool


class InputRestore(BaseModel):
    """Show a Prompt that never reached the Conversation, or a Draft block, in the Window's
    input; what was there waits behind it."""

    type: Literal["input_restore"] = "input_restore"
    text: str


class Close(BaseModel):
    """Ask the Window to exit."""

    type: Literal["close"] = "close"


class BoxShow(BaseModel):
    """Show a Notice or a Confirmation over the Window's Chat."""

    type: Literal["box_show"] = "box_show"
    box_id: BoxId
    kind: BoxKind
    title: str
    text: str


class Coverage(BaseModel):
    """Whether Main answers this Session's Confirmations itself (``--devastation`` and its
    Exemption); the Window marks it on its border."""

    type: Literal["coverage"] = "coverage"
    covered: bool


class BoxHide(BaseModel):
    """Drop a box answered elsewhere or no longer valid."""

    type: Literal["box_hide"] = "box_hide"
    box_id: BoxId


class PromptHistory(BaseModel):
    """The Session's earlier Prompts, oldest first, as they were typed: Up brings them back to
    the input (#22)."""

    type: Literal["prompt_history"] = "prompt_history"
    prompts: list[str]


type MainToWindow = (
    ChatAppend
    | ChatDelta
    | TurnState
    | ContextUsage
    | PoolShared
    | InputRestore
    | PromptHistory
    | BoxShow
    | BoxHide
    | Coverage
    | Close
)
MainMessage = Annotated[MainToWindow, Field(discriminator="type")]

_window_adapter: TypeAdapter[WindowToMain] = TypeAdapter(WindowMessage)
_main_adapter: TypeAdapter[MainToWindow] = TypeAdapter(MainMessage)


def encode(message: BaseModel) -> str:
    """Serialize a message as one JSON line; newlines inside strings are escaped by JSON."""
    return message.model_dump_json() + "\n"


def decode_window_message(line: str) -> WindowToMain:
    return _decode(_window_adapter, line)


def decode_main_message(line: str) -> MainToWindow:
    return _decode(_main_adapter, line)


def _decode[T](adapter: TypeAdapter[T], line: str) -> T:
    raw = line.rstrip("\n")
    try:
        return adapter.validate_json(raw)
    except ValidationError as error:
        raise MalformedMessage(line=raw, reason=str(error.errors()[0]["msg"])) from error
