"""What travels on a tool call's channel, a ``socketpair`` with one JSON object per line
(ADR-0002, ADR-0005). Never ``pickle``: a confined process that wrote one would run code in
Main (#99). What comes back from the tool is parsed here, at the boundary.

The warden (``boot.py``) reads and writes these same shapes with the standard library, so a
tool's process does not pay for importing pydantic (#100); the launcher tests run both ends."""

from typing import Annotated, Literal

from pydantic import BaseModel, Field, JsonValue, TypeAdapter, ValidationError

from allin.core.errors import MalformedMessage


class CallTool(BaseModel, frozen=True):
    """Main to the warden: run this function, confined to these folders."""

    type: Literal["call"] = "call"
    package: str
    module: str
    function: str
    arguments: dict[str, JsonValue]
    workspace: str
    read: list[str]
    write: list[str]


class Cancel(BaseModel, frozen=True):
    """Main to the warden: take the call down now, with every process it started."""

    type: Literal["cancel"] = "cancel"


class Returned(BaseModel, frozen=True):
    """The function returned this value."""

    type: Literal["returned"] = "returned"
    value: JsonValue


class Raised(BaseModel, frozen=True):
    """The function raised; ``error`` is its type and message, the tool's output as data."""

    type: Literal["raised"] = "raised"
    error: str


class Died(BaseModel, frozen=True):
    """The function's process ended without a result; negative ``exit_code`` for a signal."""

    type: Literal["died"] = "died"
    exit_code: int


class Unconfined(BaseModel, frozen=True):
    """The process could not confine itself, so the function never ran."""

    type: Literal["unconfined"] = "unconfined"
    call: str
    errno: int
    reason: str


type ToMain = Returned | Raised | Died | Unconfined

_to_main: TypeAdapter[ToMain] = TypeAdapter(Annotated[ToMain, Field(discriminator="type")])


def encode(message: BaseModel) -> bytes:
    return message.model_dump_json().encode() + b"\n"


def parse_to_main(line: bytes) -> ToMain:
    try:
        return _to_main.validate_json(line)
    except ValidationError as error:
        raise MalformedMessage(channel="tool", line=line[:200].decode(errors="replace")) from error
