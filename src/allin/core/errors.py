"""AllIn error hierarchy.

Errors are raised where they happen and handled only at boundaries (dispatcher, executors,
FIFO readers, UI). Each error carries a stable ``code`` plus structured ``fields`` so the UI can
render it in any language without parsing the message.
"""

from typing import Any, ClassVar


class AllInError(Exception):
    code: ClassVar[str] = "allin_error"

    def __init__(self, **fields: Any) -> None:
        self.fields: dict[str, Any] = fields
        details = ", ".join(f"{key}={value!r}" for key, value in fields.items())
        super().__init__(f"{self.code}: {details}" if details else self.code)


class MalformedMessage(AllInError):
    """A line received through an IPC channel is not a valid message."""

    code: ClassVar[str] = "malformed_message"


class ChannelBroken(AllInError):
    """An end of an IPC channel could not be opened, read or written."""

    code: ClassVar[str] = "channel_broken"


class ScreenTooSmall(AllInError):
    """The terminal has no room left for the Chat or the input."""

    code: ClassVar[str] = "screen_too_small"
