"""Messages exchanged between Main and a Window, one JSON object per line (ADR-0002)."""

from typing import Annotated, Literal

from pydantic import BaseModel, Field, TypeAdapter, ValidationError

from allin.core.errors import MalformedMessage

# Window -> Main


class WindowReady(BaseModel):
    """The Window is up and listening."""

    type: Literal["window_ready"] = "window_ready"


class UserInput(BaseModel):
    """Text the user submitted with Enter."""

    type: Literal["user_input"] = "user_input"
    text: str


class InputDraft(BaseModel):
    """The input's Draft, shown block first (``core.drafts``), sent once the user stops typing
    for a moment, so a restarted Main can give it back."""

    type: Literal["input_draft"] = "input_draft"
    blocks: list[str]


type WindowToMain = WindowReady | UserInput | InputDraft
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
    """Whether the Session has a Turn running or waiting; Enter is blocked while it does."""

    type: Literal["turn_state"] = "turn_state"
    active: bool


class ContextUsage(BaseModel):
    """How much of its context limit the Session fills and how fast its last Step generated;
    ``None`` when not known yet."""

    type: Literal["context_usage"] = "context_usage"
    context: int | None
    limit: int
    tokens_per_second: float | None


class InputRestore(BaseModel):
    """Show a Prompt that never reached the Conversation, or a Draft block, in the Window's
    input; what was there waits behind it."""

    type: Literal["input_restore"] = "input_restore"
    text: str


class Close(BaseModel):
    """Ask the Window to exit."""

    type: Literal["close"] = "close"


type MainToWindow = ChatAppend | ChatDelta | TurnState | ContextUsage | InputRestore | Close
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
