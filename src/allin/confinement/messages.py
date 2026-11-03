"""What travels on a tool call's channel, a ``socketpair`` with one JSON object per line
(ADR-0002, ADR-0005). Never ``pickle``: a confined process that wrote one would run code in
Main (#99). What comes back from the tool is parsed here, at the boundary.

The warden (``warden.py``) reads and writes these same shapes with the standard library, so a
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
    connect: list[int] | None  # the TCP ports it connects to; None: a shared layer decides
    net_layer: int | None  # the descriptor of net=True's shared layer (``port_layer``)
    ip: bool  # whether it may create IP sockets at all; without it UDP is closed too
    unix: bool  # whether it may create unix sockets: ``display``
    prompt_id: str = ""  # the Prompt whose Turn calls it (``allin.channel.prompt_id``)


class DescribeModule(BaseModel, frozen=True):
    """Main to the warden: import this module under the encierro de descripción, reading only
    ``read``, and answer with what ``describer.describe`` makes of it (ADR-0016)."""

    type: Literal["describe"] = "describe"
    package: str
    module: str
    read: list[str]


class AttachFproc(BaseModel, frozen=True):
    """Main to the warden: start this fproc as a Proceso adherido named ``name``, confined for
    good to these folders, with ``file`` as its shared file (ADR-0012, ADR-0016)."""

    type: Literal["attach"] = "attach"
    package: str
    module: str
    name: str
    file: str | None
    workspace: str
    read: list[str]
    write: list[str]
    connect: list[int] | None
    net_layer: int | None
    ip: bool
    unix: bool


class CallMethod(BaseModel, frozen=True):
    """Main to a Proceso adherido's warden: call this public method of its fproc. ``id`` tells
    its answers apart from those of the calls that run with it; ``seen`` is the revision its
    Session saw last, which an ``@writes`` method checks, and ``None`` for one nobody checks:
    a human's (ADR-0012)."""

    type: Literal["method"] = "method"
    id: int
    method: str
    arguments: dict[str, JsonValue]
    seen: int | None = None
    prompt_id: str = ""  # the Prompt whose Turn calls it; none for a human's call (#116)


class CancelCall(BaseModel, frozen=True):
    """Main to a Proceso adherido's warden: the call ``id`` was cancelled, or ran out of time;
    its fproc leaves it (``Fproc.cancelled``)."""

    type: Literal["cancel_call"] = "cancel_call"
    id: int


class Detach(BaseModel, frozen=True):
    """Main to a Proceso adherido's warden: take it down now, with every process it started."""

    type: Literal["detach"] = "detach"


class Cancel(BaseModel, frozen=True):
    """Main to the warden: take the call down now, with every process it started."""

    type: Literal["cancel"] = "cancel"


COMMAND_MAX = 64 * 1024  # of a Command session's text, in characters


class RunCommand(BaseModel, frozen=True):
    """The function asks Main to run a Command session, ``@name …`` as a Window takes it, and
    waits for its result (ADR-0025): the only way a confined process reaches Main."""

    type: Literal["command"] = "command"
    text: str = Field(max_length=COMMAND_MAX)
    id: int | None = None  # in a Proceso adherido, the call it runs for


class CommandResult(BaseModel, frozen=True):
    """Main to the warden, for the function: how the Command session it asked went."""

    type: Literal["command_result"] = "command_result"
    result: JsonValue
    id: int | None = None  # in a Proceso adherido, the call that asked


class Attached(BaseModel, frozen=True):
    """The fproc's class was made: the Proceso adherido takes calls to its methods."""

    type: Literal["attached"] = "attached"


class Returned(BaseModel, frozen=True):
    """The function returned this value."""

    type: Literal["returned"] = "returned"
    value: JsonValue
    id: int | None = None  # in a Proceso adherido, the call it answers
    revision: int | None = None  # the revision the call saw, if its Session is to note it


class Raised(BaseModel, frozen=True):
    """The function raised; ``error`` is its type and message, the tool's output as data."""

    type: Literal["raised"] = "raised"
    error: str
    id: int | None = None
    revision: int | None = None


class Stale(BaseModel, frozen=True):
    """An ``@writes`` method was not run: its Session had not seen ``revision``, the current
    one (ADR-0012)."""

    type: Literal["stale"] = "stale"
    id: int
    revision: int


PROGRESS_MAX = 2000  # of what a call says of its progress, in characters


class Progress(BaseModel, frozen=True):
    """A method tells how far the call ``id`` went (``Fproc.progress``): its timeout counts
    again from now."""

    type: Literal["progress"] = "progress"
    id: int
    text: str = Field(max_length=PROGRESS_MAX)


class Announce(BaseModel, frozen=True):
    """A method says something happened (``Fproc.announce``), which wakes the Sessions
    subscribed to its process (#116)."""

    type: Literal["announce"] = "announce"
    id: int
    text: str = Field(max_length=PROGRESS_MAX)


class Died(BaseModel, frozen=True):
    """The function's process ended without a result; negative ``exit_code`` for a signal."""

    type: Literal["died"] = "died"
    exit_code: int


class Unconfined(BaseModel, frozen=True):
    """The process could not confine itself, so the function never ran."""

    type: Literal["unconfined"] = "unconfined"
    call: str
    errno: int


type ToMain = (
    Returned | Raised | Died | Unconfined | RunCommand | Attached | Stale | Progress | Announce
)

_to_main: TypeAdapter[ToMain] = TypeAdapter(Annotated[ToMain, Field(discriminator="type")])


def encode(message: BaseModel) -> bytes:
    return message.model_dump_json().encode() + b"\n"


def parse_to_main(line: bytes) -> ToMain:
    try:
        return _to_main.validate_json(line)
    except ValidationError as error:
        raise MalformedMessage(channel="tool", line=line[:200].decode(errors="replace")) from error
