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


class SessionNotFound(AllInError):
    """No Session has this id on disk."""

    code: ClassVar[str] = "session_not_found"


class SessionUnreadable(AllInError):
    """A Session's folder exists but its metadata cannot be read."""

    code: ClassVar[str] = "session_unreadable"


class SessionStorageFailed(AllInError):
    """A Session's folder or metadata could not be written."""

    code: ClassVar[str] = "session_storage_failed"


class SessionNotOwned(AllInError):
    """The Session belongs to another Main, which is the only one that may open it."""

    code: ClassVar[str] = "session_not_owned"


class WindowAlreadyOpen(AllInError):
    """A Session has at most one Window."""

    code: ClassVar[str] = "window_already_open"


class InvalidArguments(AllInError):
    """A Command main was called with arguments it does not take; ``usage`` shows the right ones."""

    code: ClassVar[str] = "invalid_arguments"


class WindowLaunchFailed(AllInError):
    """The Window's terminal could not be started."""

    code: ClassVar[str] = "window_launch_failed"
