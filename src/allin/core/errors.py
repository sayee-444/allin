"""AllIn error hierarchy.

Errors are raised where they happen and handled only at boundaries (dispatcher, executors,
FIFO readers, UI). Each error carries a stable ``code`` plus structured ``fields`` so the UI can
render it in any language without parsing the message.
"""

from typing import ClassVar

from pydantic import JsonValue


class AllInError(Exception):
    code: ClassVar[str] = "allin_error"

    def __init__(self, **fields: JsonValue) -> None:
        self.fields: dict[str, JsonValue] = fields
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


class SyspromptInvalid(AllInError):
    """A Sysprompt file is missing, outside ``sysprompts/`` or not a valid template."""

    code: ClassVar[str] = "sysprompt_invalid"


class LlmRequestFailed(AllInError):
    """A Step's call to the LLM failed or its stream ended without a response."""

    code: ClassVar[str] = "llm_request_failed"


class LlmUnavailable(LlmRequestFailed):
    """The LLM's server cannot serve a Step: it is down, still loading its model (503), or it
    died while a Step streamed. ``reached`` says whether the Step got to the Server before it
    fell, which counts towards failing its Turn (ADR-0023)."""

    code: ClassVar[str] = "llm_unavailable"


class TurnCancelled(AllInError):
    """A Turn was stopped before it finished: its Window closed, or Main is shutting down."""

    code: ClassVar[str] = "turn_cancelled"


class StepStalled(AllInError):
    """A Step made no progress for ``step.stall`` seconds; Main cancelled it (ADR-0023)."""

    code: ClassVar[str] = "step_stalled"


class PromptTooBig(AllInError):
    """A Step needs more tokens (its measured input plus its Output cap) than its Server's slot
    holds even with nothing else in course: it fails without waiting (ADR-0004)."""

    code: ClassVar[str] = "prompt_too_big"


class TurnCrashed(AllInError):
    """A Turn hit an unexpected error; the executor survives it and reports it."""

    code: ClassVar[str] = "turn_crashed"


class UnknownCommand(AllInError):
    """No Command main has this name."""

    code: ClassVar[str] = "unknown_command"


class LogWriteFailed(AllInError):
    """An event could not be appended to a Log."""

    code: ClassVar[str] = "log_write_failed"


class LogUnreadable(AllInError):
    """A Log exists but cannot be read."""

    code: ClassVar[str] = "log_unreadable"


class MainStateUnreadable(AllInError):
    """Main's saved state is there but cannot be read; Main starts without it."""

    code: ClassVar[str] = "main_state_unreadable"


class MainStateStorageFailed(AllInError):
    """Main's state could not be saved."""

    code: ClassVar[str] = "main_state_storage_failed"


class MainAlreadyRunning(AllInError):
    """Another Main with the same id and data dir is running; only one may (ADR-0007)."""

    code: ClassVar[str] = "main_already_running"


class UnknownSetting(AllInError):
    """No Setting has this key."""

    code: ClassVar[str] = "unknown_setting"


class InvalidSetting(AllInError):
    """A Setting was given a value its key does not take."""

    code: ClassVar[str] = "invalid_setting"


class MalformedAssignment(AllInError):
    """Settings are given as ``key=value`` words; ``text`` is not one."""

    code: ClassVar[str] = "malformed_assignment"


class SettingsFileInvalid(AllInError):
    """A Settings file cannot be read or holds a bad key; Main uses the defaults instead."""

    code: ClassVar[str] = "settings_file_invalid"


class SettingsStorageFailed(AllInError):
    """A Settings file could not be written."""

    code: ClassVar[str] = "settings_storage_failed"


class SyspromptLocked(AllInError):
    """A Session's Sysprompt is fixed once its Conversation has a Turn."""

    code: ClassVar[str] = "sysprompt_locked"


class HumanOnly(AllInError):
    """Only a human may do this (``-y``, ``--devastation``, the Exemption: ADR-0013)."""

    code: ClassVar[str] = "human_only"


class NoticeFailed(AllInError):
    """A Notice module failed to load, or its check failed; the other Notices go on."""

    code: ClassVar[str] = "notice_failed"


class ServerSettingsInvalid(AllInError):
    """A Supervisor's ``servers/<name>.toml`` is missing or holds a bad key."""

    code: ClassVar[str] = "server_settings_invalid"


class SupervisorAlreadyRunning(AllInError):
    """Another process already is this Supervisor; only one may be (ADR-0008)."""

    code: ClassVar[str] = "supervisor_already_running"


class SupervisorNotRunning(AllInError):
    """A Supervisor asked to restart is asleep or failed: its next wake uses its pending
    Settings anyway."""

    code: ClassVar[str] = "supervisor_not_running"


class ServerNotAdoptable(AllInError):
    """``/server adopt`` cannot take this ``llama serve`` under a Supervisor, and says why."""

    code: ClassVar[str] = "server_not_adoptable"


class SupervisorExists(AllInError):
    """``/server new`` or ``/server adopt`` named a Supervisor that already exists."""

    code: ClassVar[str] = "supervisor_exists"


class SupervisorNotFound(AllInError):
    """No ``servers/<name>.toml``: no Supervisor has this name."""

    code: ClassVar[str] = "supervisor_not_found"
